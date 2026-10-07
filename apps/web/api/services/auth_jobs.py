"""Background native login jobs with a browser-friendly, token-free view."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from ..domain.auth import AuthState, AuthStatus, Provider
from ..domain.errors import CliResolutionError, JobConflict, JobInputError
from ..domain.jobs import AuthJob, public_status
from .auth import AuthManager
from .commands import resolve_cli

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_URL = re.compile(r"https://[^\s<>\"\x1b\x07]+")
_DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b")
_ALLOWED_URLS = {
    Provider.CLAUDE: {("claude.ai", "/oauth/authorize"), ("claude.com", "/cai/oauth/authorize"), ("console.anthropic.com", "/oauth/authorize")},
    Provider.CODEX: {("auth.openai.com", "/oauth/authorize"), ("auth.openai.com", "/codex/device")},
}


class AuthJobs:
    def __init__(self, auth: AuthManager, *, timeout: float = 600) -> None:
        self.auth = auth
        self.timeout = timeout
        self._jobs: dict[str, AuthJob] = {}
        self._active: dict[Provider, str] = {}
        self._lock = threading.RLock()
        self._closed = False

    def start(self, provider: Provider, action: str, *, mode: str = "browser", force: bool = False) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise JobConflict("O servidor está encerrando.")
            if provider in self._active:
                raise JobConflict("Já existe uma operação em andamento para essa conta.")
            # Bound memory use, retaining recent terminal results for polling.
            for key in list(self._jobs):
                if len(self._jobs) < 32:
                    break
                if self._jobs[key].state != "running" and key not in self._active.values():
                    del self._jobs[key]
            job = AuthJob(uuid4().hex, provider, action, mode, force)
            self._jobs[job.id] = job
            self._active[provider] = job.id
            threading.Thread(target=self._work, args=(job,), daemon=True, name=f"auth-{provider.value}").start()
            return job.public()

    def get(self, job_id: str) -> dict[str, object]:
        with self._lock:
            return self._jobs[job_id].public()

    def active(self) -> list[dict[str, object]]:
        with self._lock:
            return [self._jobs[job_id].public() for job_id in self._active.values()]

    def cancel(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs[job_id]
            if job.state != "running":
                return job.public()
            job.cancelled.set()
            job.state, job.message = "cancelled", "Operação cancelada."
            process = job.process
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        with self._lock:
            if self._active.get(job.provider) == job.id:
                del self._active[job.provider]
        return self.get(job_id)

    def send_code(self, job_id: str, code: str) -> dict[str, object]:
        # A single line is forwarded to the native login, never to a shell.
        code = code.strip()
        if not code or len(code) > 2048 or any(ord(char) < 32 or ord(char) == 127 for char in code):
            raise JobInputError("Cole um código de confirmação válido, em uma única linha.")
        with self._lock:
            job = self._jobs[job_id]
            if job.state != "running" or not job.needs_code or job.process is None or job.process.stdin is None:
                raise JobInputError("Esta operação não está aguardando um código.")
            try:
                job.process.stdin.write(code + "\n")
                job.process.stdin.flush()
            except (OSError, ValueError):
                raise JobInputError("O login já terminou. Inicie uma nova conexão.") from None
            job.needs_code = False
            job.message = "Confirmando sua conta…"
            return job.public()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            ids = list(self._active.values())
        for job_id in ids:
            self.cancel(job_id)

    def _finish(self, job: AuthJob, state: str, message: str, result: AuthStatus | None = None) -> None:
        with self._lock:
            if not job.cancelled.is_set():
                job.state, job.message, job.result = state, message, result
            job.auth_url = job.user_code = None
            job.needs_code = False
            if self._active.get(job.provider) == job.id:
                del self._active[job.provider]

    def _rpc_send(self, job: AuthJob, message: dict[str, object]) -> None:
        with self._lock:
            if job.process is not None and job.process.stdin is not None and not job.cancelled.is_set():
                job.process.stdin.write(json.dumps(message) + "\n")
                job.process.stdin.flush()

    def _rpc_output(self, job: AuthJob, text: str) -> bool:
        if job.provider != Provider.CODEX or job.mode != "browser" or job.action != "login":
            return False
        try:
            message = json.loads(text)
        except ValueError:
            return False
        if not isinstance(message, dict):
            return True
        if message.get("id") in {1, 2} and "error" in message:
            job.rpc_done.set()
        elif message.get("id") == 1 and "result" in message:
            self._rpc_send(job, {"method": "initialized"})
            self._rpc_send(job, {"id": 2, "method": "account/login/start", "params": {"type": "chatgpt"}})
        elif message.get("id") == 2:
            result = message.get("result")
            if isinstance(result, dict) and isinstance(result.get("authUrl"), str):
                self._parse_signin_url(job, result["authUrl"])
            else:
                job.rpc_done.set()
        elif message.get("method") == "account/login/completed":
            params = message.get("params")
            job.rpc_success = isinstance(params, dict) and params.get("success") is True
            job.rpc_done.set()
        return True

    def _parse_signin_url(self, job: AuthJob, text: str) -> None:
        for candidate in _URL.findall(text):
            candidate = candidate.rstrip(".,;)")
            try:
                url = urlsplit(candidate)
                if url.port is not None or url.username is not None:
                    continue
                if (url.hostname, url.path) not in _ALLOWED_URLS[job.provider]:
                    continue
                if {"access_token", "refresh_token", "id_token", "api_key"} & parse_qs(url.query).keys():
                    continue
            except ValueError:
                continue
            job.auth_url = candidate
            job.needs_code = job.provider == Provider.CLAUDE
            job.message = "Conclua o login na página do provedor."

    def _output(self, job: AuthJob, text: str) -> None:
        text = _ANSI.sub("", text)
        with self._lock:
            if job.state != "running":
                return
            if self._rpc_output(job, text):
                return
            self._parse_signin_url(job, text)
            if job.provider == Provider.CODEX and job.mode == "device":
                match = _DEVICE_CODE.search(text)
                if match:
                    job.user_code = match.group()
            if "paste code" in text.lower() and job.provider == Provider.CLAUDE:
                job.needs_code = True

    def _read_output(self, job: AuthJob, stream: object) -> None:
        pending = ""
        try:
            while char := stream.read(1):
                pending += char
                if char in "\n\r" or len(pending) >= 8192:
                    self._output(job, pending)
                    pending = ""
            if pending:
                self._output(job, pending)
        except (OSError, ValueError):
            pass

    def _work(self, job: AuthJob) -> None:
        process = None
        readers: list[threading.Thread] = []
        try:
            if self.auth.auth_home:
                (self.auth.auth_home / job.provider.value).mkdir(parents=True, exist_ok=True)
            initial = self.auth.status(job.provider)
            if job.cancelled.is_set():
                return
            if initial.state in {AuthState.MISSING_CLI, AuthState.ERROR, AuthState.TIMEOUT}:
                self._finish(job, "failed", initial.message, initial)
                return
            expected = AuthState.CONNECTED if job.action == "login" else AuthState.DISCONNECTED
            if initial.state == expected and not job.force:
                self._finish(job, "succeeded", "Conta conectada." if job.action == "login" else "Conta desconectada.", initial)
                return
            command = resolve_cli(job.provider.value, self.auth.environment(job.provider))
            if command is None:
                self._finish(job, "failed", "Instale o aplicativo do provedor no servidor e tente novamente.")
                return
            if job.action == "logout":
                args = ("auth", "logout") if job.provider == Provider.CLAUDE else ("logout",)
            elif job.provider == Provider.CLAUDE:
                args = ("auth", "login", "--claudeai")
            else:
                args = ("login", "--device-auth") if job.mode == "device" else ("app-server",)
            with self._lock:
                if job.cancelled.is_set():
                    return
                process = subprocess.Popen(
                    [*command.argv, *args], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", bufsize=1,
                    env=self.auth.environment(job.provider),
                )
                job.process = process
                job.message = "Preparando o login…" if job.action == "login" else "Desconectando sua conta…"
            for stream in (process.stdout, process.stderr):
                reader = threading.Thread(target=self._read_output, args=(job, stream), daemon=True)
                reader.start()
                readers.append(reader)
            try:
                if job.provider == Provider.CODEX and job.mode == "browser" and job.action == "login":
                    self._rpc_send(job, {
                        "id": 1, "method": "initialize",
                        "params": {"clientInfo": {"name": "harness_eng", "title": "Harness Eng", "version": "0.1.0"}},
                    })
                    deadline = time.monotonic() + self.timeout
                    while not job.rpc_done.wait(0.2):
                        if job.cancelled.is_set() or process.poll() is not None:
                            break
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(command.argv, self.timeout)
                    exit_code = 0 if job.rpc_success else 1
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                else:
                    exit_code = process.wait(timeout=self.timeout if job.action == "login" else 60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                self._finish(job, "expired", "O login expirou. Inicie uma nova conexão.")
                return
            if job.cancelled.is_set():
                return
            status = self.auth.status(job.provider)
            for _ in range(2):
                if exit_code != 0 or status.state == expected:
                    break
                time.sleep(0.25)
                status = self.auth.status(job.provider)
            if exit_code == 0 and status.state == expected:
                self._finish(job, "succeeded", "Conta conectada." if job.action == "login" else "Conta desconectada.", status)
            else:
                message = "Não foi possível confirmar a conexão. Tente novamente."
                if job.provider == Provider.CODEX and job.mode == "device":
                    message = "O login por código não foi concluído. Tente a opção de login pelo navegador."
                self._finish(job, "failed", message, status)
        except (OSError, CliResolutionError):
            self._finish(job, "failed", "Não foi possível iniciar o aplicativo do provedor. Verifique a instalação.")
        except Exception:
            # Do not leak vendor exceptions, which may contain credentials.
            self._finish(job, "failed", "A operação falhou. Inicie uma nova conexão.")
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                for reader in readers:
                    reader.join(timeout=1)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            with self._lock:
                if self._active.get(job.provider) == job.id:
                    del self._active[job.provider]
