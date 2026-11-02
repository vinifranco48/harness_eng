"""Bounded, cancellable JSON-line transport for the installed native harnesses."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Literal

from ..domain.auth import Provider
from ..domain.errors import HarnessError
from .auth import AuthManager
from .commands import resolve_cli


MessageKind = Literal[
    "request",
    "response",
    "notification",
    "invalid",
]


class JsonProcess:
    def __init__(self, auth: AuthManager, provider: Provider, args: list[str], cwd: Path, *, tool_handler: Callable[[dict], dict] | None = None):
        command = resolve_cli(provider.value, auth.environment(provider))
        if command is None:
            raise HarnessError(f"Instale o CLI {provider.value} para continuar.")
        environment = auth.environment(provider)
        # A CLI launched from a Claude terminal is a separate web conversation.
        environment.pop("CLAUDECODE", None)
        environment.pop("CLAUDE_CODE_ENTRYPOINT", None)
        self.process = subprocess.Popen(
            [*command.argv, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            bufsize=1, cwd=cwd, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        self.messages: queue.Queue = queue.Queue(maxsize=1024)
        self.deferred = deque()
        self.closed = threading.Event()
        self.write_lock = threading.Lock()
        self.tool_handler = tool_handler
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(4 * 1024 * 1024)
                if not line:
                    break
                if not line.endswith("\n"):
                    break
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    while not self.closed.is_set():
                        try:
                            self.messages.put(value, timeout=0.2)
                            break
                        except queue.Full:
                            pass
        finally:
            # Never block cleanup on a full queue.
            while not self.closed.is_set():
                try:
                    self.messages.put(None, timeout=0.2)
                    break
                except queue.Full:
                    pass

    def send(self, message: dict):
        with self.write_lock:
            if self.closed.is_set() or self.process.poll() is not None:
                raise HarnessError("A conexão com o provedor foi encerrada.")
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise HarnessError("Não foi possível enviar a mensagem ao provedor.") from None

    def read(self, deadline: float, cancel: threading.Event | None = None, *, queued: bool = True):
        if queued and self.deferred:
            return self.deferred.popleft()
        while time.monotonic() < deadline:
            if self.closed.is_set() or (cancel and cancel.is_set()):
                raise HarnessError("Resposta interrompida.")
            try:
                value = self.messages.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if value is None:
                raise HarnessError("O provedor encerrou a conexão. Confira o login e tente novamente.")
            return value
        raise HarnessError("O provedor demorou demais. Tente novamente.")

    def rpc(self, request_id: int, method: str, params: dict, timeout: float = 30):
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.read(deadline, queued=False)
            if classify_message(message) == "response" and type(message.get("id")) is type(request_id) and message.get("id") == request_id:
                if "error" in message:
                    raise HarnessError("O Codex não conseguiu concluir a solicitação. Confira o modelo e o login.")
                return message.get("result", {})
            self.handle_request(message)
            if classify_message(message) == "notification" and isinstance(message.get("method"), str) and message["method"].startswith(("item/", "turn/", "error")):
                self.deferred.append(message)

    def handle_request(self, message: dict) -> bool:
        """Dispatch only registered app-tool calls; every other request is denied."""
        if classify_message(message) != "request":
            return False
        handler = getattr(self, "tool_handler", None)
        if message.get("method") == "item/tool/call" and handler is not None:
            self.send(handler(message))
        else:
            self.deny_request(message)
        return True

    def deny_request(self, message: dict):
        """Native command, file and input requests remain unavailable in chat."""
        if classify_message(message) == "request":
            method = message["method"]
            if not isinstance(method, str):
                self.send({"id": message["id"], "error": {"code": -32600, "message": "Invalid request method"}})
            elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                self.send({"id": message["id"], "result": {"decision": "decline"}})
            else:
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Unavailable in chat"}})

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


CHAT_INSTRUCTIONS = (
    "Você é um assistente de conversa no Harness Eng. Responda em português, "
    "a menos que o usuário peça outro idioma. Use o contexto da conversa. "
    "Esta interface é para conversar: não execute comandos, não leia arquivos "
    "locais, não altere o computador e não use ferramentas. "
    "Forneça explicações, sugestões e código diretamente na resposta."
)

ANALYSIS_INSTRUCTIONS = (
    "Você é um assistente de análise de dados no Harness Eng. Responda em português, "
    "a menos que o usuário peça outro idioma. Use o contexto da conversa. "
    "Use somente as ferramentas da aplicação anunciadas nesta conversa para consultar "
    "datasets e criar análises e gráficos. Não execute comandos, não leia arquivos locais, "
    "não navegue na internet e não altere o computador. "
    "Consulte os datasets disponíveis e seus campos antes de criar gráficos; não invente "
    "valores, nomes de campos ou identificadores. A planilha demonstrativa contém dados "
    "fictícios e pode ser importada pela ferramenta dataset_import_demo. "
    "Crie a análise antes dos gráficos e reutilize seu analysis_id. "
    "Só confirme uma criação quando a ferramenta retornar ok=true. Em falhas, explique "
    "o erro e não afirme que o recurso foi salvo. Ao concluir, inclua um link Markdown "
    "para analysis_url retornada pela ferramenta, para abrir os gráficos na aplicação."
)


def claude_args() -> list[str]:
    return [
        "--print", "--verbose", "--input-format", "stream-json",
        "--output-format", "stream-json", "--include-partial-messages",
        "--tools", "", "--permission-mode", "dontAsk", "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--system-prompt", CHAT_INSTRUCTIONS,
    ]


def claude_initialize(process: JsonProcess, timeout: float = 30) -> dict:
    process.send({"type": "control_request", "request_id": "harness-init", "request": {"subtype": "initialize"}})
    deadline = time.monotonic() + timeout
    while True:
        message = process.read(deadline)
        response = message.get("response", {})
        if message.get("type") == "control_response" and response.get("request_id") == "harness-init":
            if response.get("subtype") == "error":
                raise HarnessError("O Claude não conseguiu iniciar a conversa.")
            return response.get("response", {})


def codex_args() -> list[str]:
    return [
        "app-server", "--listen", "stdio://", "-c", 'approval_policy="never"',
        "-c", 'sandbox_mode="read-only"', "-c", 'web_search="disabled"',
        "-c", "features.shell_tool=false", "-c", "features.apply_patch_freeform=false",
    ]


def codex_initialize(process: JsonProcess, *, experimental: bool = False):
    params = {"clientInfo": {"name": "harness_eng", "title": "Harness Eng", "version": "0.1.0"}}
    if experimental:
        params["capabilities"] = {"experimentalApi": True}
    process.rpc(1, "initialize", params)
    process.send({"method": "initialized", "params": {}})


def classify_message(message: dict) -> MessageKind:
    """Classify envelope fields and reject conflicting request/response shapes."""
    has_result = "result" in message
    has_error = "error" in message

    if "method" in message:
        if has_result or has_error:
            return "invalid"
        return "request" if "id" in message else "notification"
    if "id" in message and has_result != has_error:
        return "response"
    return "invalid"
