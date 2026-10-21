# Copyright (2026) Databricks, Inc.
# Licensed under the Apache License, Version 2.0.
# Authentication flow adapted from omnigent/onboarding/harness_install.py.
# Modified for a standalone Python package, strict status parsing and Windows.
"""Delegate login/logout to the vendor CLIs and verify their stored login."""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Mapping

from ..domain.auth import AuthResult, AuthState, AuthStatus, Provider
from ..domain.commands import CliCommand
from ..domain.errors import CliResolutionError
from .commands import resolve_cli


# Native saved logins are checked independently of ambient API keys/gateways.
# Only the child environment changes; the parent environment is untouched.
_AUTH_OVERRIDES = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL",
})

# Infrastructure credentials belong to the backend, not to provider processes.
_BACKEND_SECRETS = frozenset({
    "DATABASE_URL", "DREMIO_ACCESS_TOKEN", "MYSQL_PWD",
})


def _positive_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("O timeout deve ser um número positivo e finito.")
    return value


class AuthManager:
    """Reusable auth API; no inference, token copying or credential-file reads.

    By default uses the CLI's existing home. ``auth_home`` opts into separate
    ``claude`` and ``codex`` subdirectories, using the CLIs' native home vars.
    Status is a local credential check, not a model access/entitlement probe.
    """

    def __init__(
        self,
        *,
        auth_home: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        status_timeout: float = 15,
    ) -> None:
        self.auth_home = Path(auth_home).expanduser().absolute() if auth_home else None
        self._env = dict(os.environ if env is None else env)
        self.status_timeout = _positive_timeout(status_timeout)

    def environment(self, provider: Provider | str) -> dict[str, str]:
        """Return a fresh child environment to launch the harness with this login."""
        provider = Provider(provider)
        env = {
            k: v for k, v in self._env.items()
            if k.upper() not in _AUTH_OVERRIDES | _BACKEND_SECRETS
            and not k.upper().startswith(("MYSQL_", "HARNESS_MYSQL_"))
        }
        if self.auth_home:
            home_var = "CLAUDE_CONFIG_DIR" if provider == Provider.CLAUDE else "CODEX_HOME"
            env[home_var] = str(self.auth_home / provider.value)
        return env

    def status(self, provider: Provider | str) -> AuthStatus:
        provider = Provider(provider)
        try:
            command = resolve_cli(provider.value, self.environment(provider))
        except CliResolutionError as exc:
            return AuthStatus(provider, AuthState.ERROR, str(exc))
        if command is None:
            return AuthStatus(provider, AuthState.MISSING_CLI, f"CLI {provider.value} não encontrado.")
        return self._status(provider, command)

    def _status(self, provider: Provider, command: CliCommand) -> AuthStatus:
        args = ("auth", "status", "--json") if provider == Provider.CLAUDE else ("login", "status")
        try:
            result = subprocess.run(
                [*command.argv, *args],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False,
                timeout=self.status_timeout, env=self.environment(provider),
            )
        except subprocess.TimeoutExpired:
            return AuthStatus(provider, AuthState.TIMEOUT, "A consulta de autenticação expirou.", command.path)
        except OSError:
            return AuthStatus(provider, AuthState.ERROR, "Não foi possível executar o CLI.", command.path)
        if provider == Provider.CLAUDE:
            try:
                payload = json.loads(result.stdout)
            except (ValueError, TypeError):
                payload = None
            if not isinstance(payload, dict) or type(payload.get("loggedIn")) is not bool:
                return AuthStatus(provider, AuthState.ERROR, "O Claude retornou um status JSON inválido.", command.path)
            if result.returncode not in {0, 1} or (payload["loggedIn"] and result.returncode != 0):
                return AuthStatus(provider, AuthState.ERROR, "O CLI falhou ao consultar a autenticação.", command.path)
            connected = payload["loggedIn"]
            method = payload.get("authMethod")
            method = method if isinstance(method, str) and method in {"claude.ai", "oauth", "api_key", "apiKey"} else "cli"
        else:
            output = f"{result.stdout}\n{result.stderr}".lower()
            if result.returncode == 0:
                connected = True
            elif result.returncode == 1 and "not logged in" in output:
                connected = False
            else:
                return AuthStatus(provider, AuthState.ERROR, "O CLI falhou ao consultar a autenticação.", command.path)
            method = "chatgpt" if "chatgpt" in output else "api_key" if "api key" in output else "cli"
        return AuthStatus(
            provider, AuthState.CONNECTED if connected else AuthState.DISCONNECTED,
            "Login salvo no CLI." if connected else "O CLI está desconectado.",
            command.path, method if connected else None,
        )

    def login(
        self, provider: Provider | str, *, device_auth: bool = False,
        force: bool = False, timeout: float = 600,
    ) -> AuthResult:
        provider = Provider(provider)
        timeout = _positive_timeout(timeout)
        if device_auth and provider != Provider.CODEX:
            raise ValueError("Login por código de dispositivo está disponível apenas para Codex.")
        if self.auth_home:
            (self.auth_home / provider.value).mkdir(parents=True, exist_ok=True)
        initial = self.status(provider)
        if initial.state in {AuthState.MISSING_CLI, AuthState.ERROR, AuthState.TIMEOUT}:
            return AuthResult("login", False, initial, initial.message)
        if initial.connected and not force:
            return AuthResult("login", True, initial, "O CLI já está autenticado.")
        args = ("auth", "login", "--claudeai") if provider == Provider.CLAUDE else ("login",)
        if device_auth:
            args = (*args, "--device-auth")
        return self._action(provider, "login", args, timeout)

    def logout(self, provider: Provider | str, *, timeout: float = 60) -> AuthResult:
        provider = Provider(provider)
        timeout = _positive_timeout(timeout)
        initial = self.status(provider)
        if initial.state == AuthState.MISSING_CLI:
            return AuthResult("logout", False, initial, initial.message)
        if initial.state == AuthState.DISCONNECTED:
            return AuthResult("logout", True, initial, "O CLI já está desconectado.")
        args = ("auth", "logout") if provider == Provider.CLAUDE else ("logout",)
        return self._action(provider, "logout", args, timeout)

    def _action(self, provider: Provider, action: str, args: tuple[str, ...], timeout: float) -> AuthResult:
        try:
            command = resolve_cli(provider.value, self.environment(provider))
        except CliResolutionError as exc:
            status = AuthStatus(provider, AuthState.ERROR, str(exc))
            return AuthResult(action, False, status, status.message)
        if command is None:
            status = AuthStatus(provider, AuthState.MISSING_CLI, "O CLI não está disponível.")
            return AuthResult(action, False, status, status.message)
        # Inherit the terminal: native browser URLs, prompts and device codes
        # go directly to the user rather than being captured or persisted.
        try:
            result = subprocess.run(
                [*command.argv, *args], check=False,
                timeout=timeout, env=self.environment(provider),
            )
        except subprocess.TimeoutExpired:
            status = self._status(provider, command)
            return AuthResult(action, False, status, "O tempo para concluir a operação expirou.")
        except OSError:
            status = AuthStatus(provider, AuthState.ERROR, "Não foi possível executar o CLI.", command.path)
            return AuthResult(action, False, status, status.message)
        status = self._status(provider, command)
        expected = AuthState.CONNECTED if action == "login" else AuthState.DISCONNECTED
        if result.returncode == 0:
            for _ in range(2):
                if status.state == expected:
                    break
                time.sleep(0.25)
                status = self._status(provider, command)
        success = result.returncode == 0 and status.state == expected
        message = "Autenticação confirmada." if action == "login" else "Logout confirmado."
        if not success:
            message = f"O CLI terminou com código {result.returncode}; não foi possível confirmar {action}."
        return AuthResult(action, success, status, message, result.returncode)
