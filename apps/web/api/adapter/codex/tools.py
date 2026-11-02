"""Translate dynamic tool calls without managing a Codex process."""

import hashlib
import json
from copy import deepcopy

from ...services.protocol import classify_message
from ...tools.executor import ToolExecutor


class CodexToolBridge:
    def __init__(self, executor: ToolExecutor):
        self._executor = executor

    def handle(self, message: dict) -> dict:
        if (
            not isinstance(message, dict)
            or classify_message(message) != "request"
            or message.get("method") != "item/tool/call"
        ):
            raise ValueError("Mensagem não é uma chamada de tool")

        # The response echoes the JSON-RPC id, independently of params.callId.
        request_id = message["id"]
        if type(request_id) not in (int, str):
            raise ValueError("Identificador da requisição inválido")

        params = message.get("params")
        if not isinstance(params, dict):
            return self._failure(request_id, "INVALID_ARGUMENTS", "Informe parâmetros válidos para a ferramenta.")
        tool_name = params.get("tool")
        arguments = params.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip() or not isinstance(arguments, dict):
            return self._failure(request_id, "INVALID_ARGUMENTS", "Informe uma ferramenta e argumentos válidos.")

        try:
            result = self._executor.execute(tool_name, arguments)
        except Exception:
            return self._failure(request_id, "TOOL_EXECUTION_FAILED", "Não foi possível executar a ferramenta.")

        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return self._failure(request_id, "INVALID_TOOL_RESULT", "A ferramenta retornou um resultado inválido.")
        try:
            return self._response(request_id, result)
        except (TypeError, ValueError):
            return self._failure(request_id, "INVALID_TOOL_RESULT", "A ferramenta retornou um resultado inválido.")

    @staticmethod
    def _response(request_id: int | str, result: dict) -> dict:
        return {
            "id": request_id,
            "result": {
                "contentItems": [{
                    "type": "inputText",
                    "text": json.dumps(result, ensure_ascii=False, allow_nan=False),
                }],
                "success": result["ok"],
            },
        }

    @classmethod
    def _failure(cls, request_id: int | str, code: str, message: str) -> dict:
        return cls._response(request_id, {
            "ok": False,
            "error": {"code": code, "message": message},
        })


class CodexToolSession:
    """Scope app-tool execution and duplicate detection to one active turn."""

    MAX_CALLS = 256

    def __init__(self, bridge: CodexToolBridge, thread_id: str, tool_names: set[str]):
        self.bridge = bridge
        self.thread_id = thread_id
        self.tool_names = frozenset(tool_names)
        self.turn_id: str | None = None
        self._calls: dict[str, tuple[str, dict]] = {}

    def handle(self, message: dict) -> dict:
        request_id = message.get("id")
        if type(request_id) not in (int, str):
            return {"id": None, "error": {"code": -32600, "message": "Invalid request id"}}
        params = message.get("params")
        if not isinstance(params, dict):
            return self.bridge._failure(request_id, "INVALID_ARGUMENTS", "Informe parâmetros válidos para a ferramenta.")
        if params.get("threadId") != self.thread_id:
            return self.bridge._failure(request_id, "INVALID_TOOL_CONTEXT", "A chamada não pertence a esta conversa.")
        turn_id, call_id = params.get("turnId"), params.get("callId")
        if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 512 or not isinstance(call_id, str) or not 1 <= len(call_id) <= 512:
            return self.bridge._failure(request_id, "INVALID_ARGUMENTS", "Informe os identificadores da chamada e da interação.")
        if self.turn_id is not None and self.turn_id != turn_id:
            return self.bridge._failure(request_id, "INVALID_TOOL_CONTEXT", "A chamada não pertence à interação atual.")
        tool_name = params.get("tool")
        if not isinstance(tool_name, str):
            return self.bridge._failure(request_id, "INVALID_ARGUMENTS", "Informe o nome da ferramenta.")
        if params.get("namespace") not in (None, "") or tool_name not in self.tool_names:
            return self.bridge._failure(request_id, "TOOL_NOT_FOUND", "Esta ferramenta não está disponível nesta conversa.")
        try:
            fingerprint = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            return self.bridge._failure(request_id, "INVALID_ARGUMENTS", "Informe argumentos JSON válidos.")
        self.turn_id = turn_id
        previous = self._calls.get(call_id)
        if previous is not None:
            if previous[0] != fingerprint:
                return self.bridge._failure(request_id, "DUPLICATE_CALL_CONFLICT", "Esta chamada já foi recebida com outros argumentos.")
            return {"id": request_id, "result": deepcopy(previous[1])}
        if len(self._calls) >= self.MAX_CALLS:
            return self.bridge._failure(request_id, "TOOL_CALL_LIMIT", "O limite de chamadas desta interação foi atingido.")
        response = self.bridge.handle(message)
        self._calls[call_id] = (fingerprint, deepcopy(response["result"]))
        return response
