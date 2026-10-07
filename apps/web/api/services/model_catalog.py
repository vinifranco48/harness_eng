"""Discover model choices from the authenticated native harness catalogs."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ..domain.auth import Provider
from ..domain.errors import HarnessError
from .auth import AuthManager
from .protocol import JsonProcess, claude_args, claude_initialize, codex_args, codex_initialize


class ModelCatalog:
    def __init__(self, auth: AuthManager, cwd: Path):
        self.auth = auth
        self.cwd = cwd
        self._cache: dict = {}
        self._locks = {provider: threading.Lock() for provider in Provider}

    def list(self, provider: Provider, force: bool = False) -> dict:
        with self._locks[provider]:
            cached = self._cache.get(provider)
            if cached and not force and time.monotonic() - cached[0] < 120:
                return cached[1]
            status = self.auth.status(provider)
            payload = {"provider": provider.value, "connected": status.connected, "models": [], "error": None}
            if not status.connected:
                payload["error"] = "Conecte sua conta para escolher um modelo."
                return payload
            try:
                models = self._claude() if provider == Provider.CLAUDE else self._codex()
                if not models:
                    raise HarnessError("O provedor não retornou modelos. Atualize o CLI e tente novamente.")
                payload["models"] = models
                self._cache[provider] = (time.monotonic(), payload)
            except (HarnessError, OSError, ValueError) as exc:
                payload["error"] = str(exc) if isinstance(exc, HarnessError) else "Não foi possível consultar os modelos. Tente atualizar a lista."
            return payload

    def choice(self, provider: Provider, model: str, effort: str | None = None) -> dict:
        catalog = self.list(provider)
        if catalog["error"]:
            raise HarnessError(catalog["error"])
        choice = next((item for item in catalog["models"] if item["id"] == model), None)
        if choice is None:
            raise HarnessError("Escolha um modelo da lista disponível.")
        if effort is not None and effort not in choice["efforts"]:
            raise HarnessError("Escolha um nível de raciocínio disponível para este modelo.")
        return choice

    def _claude(self) -> list[dict]:
        with JsonProcess(self.auth, Provider.CLAUDE, claude_args(), self.cwd) as process:
            info = claude_initialize(process)
            result = []
            for item in info.get("models", []):
                model = item.get("value") or item.get("model") or item.get("id")
                if not isinstance(model, str):
                    continue
                # The initialization response contains the CLI's account-aware aliases.
                result.append({"id": model, "name": item.get("displayName", model),
                               "description": item.get("description", ""),
                               "default": len(result) == 0, "efforts": [], "default_effort": None})
            return result

    def _codex(self) -> list[dict]:
        with JsonProcess(self.auth, Provider.CODEX, codex_args(), self.cwd) as process:
            codex_initialize(process)
            result = []
            cursor = None
            for request_id in range(2, 12):
                params = {"limit": 100, "includeHidden": False}
                if cursor:
                    params["cursor"] = cursor
                info = process.rpc(request_id, "model/list", params)
                for item in info.get("data", []):
                    model = item.get("model") or item.get("id")
                    if not isinstance(model, str) or item.get("hidden"):
                        continue
                    result.append({"id": model, "name": item.get("displayName", model),
                                   "description": item.get("description", ""), "default": item.get("isDefault", False),
                                   "efforts": [entry["reasoningEffort"] for entry in item.get("supportedReasoningEfforts", [])],
                                   "default_effort": item.get("defaultReasoningEffort")})
                cursor = info.get("nextCursor")
                if not cursor:
                    break
            return result
