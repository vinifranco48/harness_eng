"""Coordinate native provider responses and persistent conversations."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from ..domain.auth import Provider
from ..domain.chat import ChatRun
from ..domain.errors import ChatConflict, HarnessError
from .auth import AuthManager
from .chat_store import ChatStore
from .model_catalog import ModelCatalog
from .protocol import (
    CHAT_INSTRUCTIONS, JsonProcess, claude_args, claude_initialize,
    codex_args, codex_initialize,
)


def _error(provider: str, detail: object) -> str:
    """Translate provider failures without forwarding raw transport output."""
    text = str(detail).lower()
    name = "Claude" if provider == "claude" else "Codex"
    if any(term in text for term in ("usage_limit", "usagelimit", "rate_limit", "rate limit", "limit reached", "out of extra usage", "hit your limit")):
        return f"O limite de uso do {name} foi atingido. Aguarde a renovação do limite ou confira seu plano."
    if any(term in text for term in ("unauthorized", "authentication", "not logged", "401", "oauth")):
        return f"O {name} precisa de um novo login. Abra Contas e reconecte."
    if any(term in text for term in ("model_not_found", "unsupported model", "model is not", "model does not", "not available")):
        return "Este modelo não está disponível para sua conta. Escolha outro modelo."
    if "context" in text and any(term in text for term in ("exceeded", "too long", "limit")):
        return "Esta conversa atingiu o limite de contexto. Inicie uma nova conversa."
    return f"O {name} não conseguiu responder. Tente novamente ou escolha outro modelo."


class ChatManager:
    def __init__(self, auth: AuthManager, data_dir: Path):
        self.auth = auth
        self.cwd = data_dir / "chat-workspace"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.store = ChatStore(data_dir / "chats.sqlite3")
        self.catalog = ModelCatalog(auth, self.cwd)
        self.lock = threading.RLock()
        self.runs: dict[str, ChatRun] = {}
        self.active: dict[str, str] = {}
        self.closed = False

    def public_chat(self, chat_id: str) -> dict:
        with self.lock:
            chat = self.store.get(chat_id)
            chat.pop("native_id", None)
            run_id = self.active.get(chat_id)
            run = self.runs.get(run_id)
            if run:
                for message in chat["messages"]:
                    if message["id"] == run.message_id:
                        message.update(content=run.content, state=run.state, error=run.error, model=run.model, usage=run.usage)
            chat["active_run"] = run.public() if run else None
            return chat

    def create(self, provider: Provider, model: str, effort: str | None):
        self.catalog.choice(provider, model, effort)
        return self.public_chat(self.store.create(provider, model, effort)["id"])

    def send(self, chat_id: str, text: str, model: str, effort: str | None) -> dict:
        text = text.strip()
        if not text:
            raise HarnessError("Digite uma mensagem para enviar.")
        chat = self.store.get(chat_id)
        provider = Provider(chat["provider"])
        self.catalog.choice(provider, model, effort)
        if not self.auth.status(provider).connected:
            raise HarnessError("Conecte sua conta antes de enviar uma mensagem.")
        with self.lock:
            if self.closed:
                raise ChatConflict("O servidor está encerrando. Recarregue a página.")
            if chat_id in self.active:
                raise ChatConflict("Aguarde a resposta ou clique em Parar antes de enviar outra mensagem.")
            if len(self.active) >= 4:
                raise ChatConflict("Há quatro respostas em andamento. Aguarde uma delas terminar.")
            for key, old in list(self.runs.items()):
                if len(self.runs) < 64:
                    break
                if old.state != "running":
                    del self.runs[key]
            assistant_id = self.store.begin(chat_id, text, model, effort)
            run = ChatRun(str(uuid.uuid4()), chat_id, assistant_id, provider.value, model)
            self.runs[run.id] = run
            self.active[chat_id] = run.id
            run.thread = threading.Thread(target=self._work, args=(run, chat, text, effort), daemon=True)
            run.thread.start()
            return run.public()

    def get_run(self, run_id: str) -> dict:
        with self.lock:
            return self.runs[run_id].public()

    def _update(self, run: ChatRun, *, content: str | None = None, model: str | None = None, usage: dict | None = None):
        with self.lock:
            if content is not None:
                if len(content) > 2_000_000:
                    raise HarnessError("A resposta ultrapassou o tamanho máximo desta interface.")
                run.content = content
            if model:
                run.model = model
            if usage:
                run.usage = usage
            run.revision += 1
            if time.monotonic() - run.persisted_at > 0.5:
                self._persist(run)

    def _persist(self, run: ChatRun):
        self.store.update(run.message_id, run.content, run.state, run.error, run.model, run.usage)
        run.persisted_at = time.monotonic()

    def _process(self, run: ChatRun, args: list[str]) -> JsonProcess:
        process = JsonProcess(self.auth, Provider(run.provider), args, self.cwd)
        with self.lock:
            run.process = process
            if run.cancel.is_set():
                process.close()
                raise HarnessError("Resposta interrompida.")
        return process

    def _work(self, run: ChatRun, chat: dict, text: str, effort: str | None):
        state = "completed"
        error = None
        try:
            if run.provider == "claude":
                self._claude(run, chat, text)
            else:
                self._codex(run, chat, text, effort)
            if not run.content:
                raise HarnessError("O provedor concluiu a solicitação sem uma resposta de texto. Tente novamente.")
        except (HarnessError, OSError, ValueError, KeyError, TypeError) as exc:
            state = "failed"
            error = str(exc) if isinstance(exc, HarnessError) else "Não foi possível concluir a resposta. Tente novamente."
        except Exception:
            state = "failed"
            error = "Ocorreu um erro ao receber a resposta. Tente novamente."
        finally:
            if run.process:
                run.process.close()
            with self.lock:
                if run.cancel.is_set():
                    state, error = "interrupted", "Resposta interrompida."
                run.state, run.error = state, error
                run.revision += 1
                self._persist(run)
                self.active.pop(run.chat_id, None)

    def _claude(self, run: ChatRun, chat: dict, text: str):
        args = [*claude_args(), "--model", run.model]
        if chat["native_id"]:
            args += ["--resume", chat["native_id"]]
        else:
            args += ["--session-id", str(uuid.uuid4())]
        with self._process(run, args) as process:
            claude_initialize(process)
            process.send({"type": "user", "message": {"role": "user", "content": text}, "session_id": "default"})
            deadline = time.monotonic() + 600
            messages: dict[str, str] = {}
            current_id = "answer"
            while True:
                message = process.read(deadline, run.cancel)
                kind = message.get("type")
                if kind == "system" and message.get("subtype") == "init":
                    native_id = message.get("session_id")
                    if isinstance(native_id, str):
                        self.store.native(run.chat_id, native_id)
                    self._update(run, model=message.get("model"))
                elif kind == "stream_event":
                    event = message.get("event", {})
                    if event.get("type") == "message_start":
                        current_id = event.get("message", {}).get("id", current_id)
                        messages.setdefault(current_id, "")
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        messages[current_id] = messages.get(current_id, "") + delta.get("text", "")
                        self._update(run, content="\n\n".join(value for value in messages.values() if value))
                elif kind == "assistant":
                    body = message.get("message", {})
                    content = "".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")
                    if content:
                        # Claude may emit each completed text block separately.
                        key = body.get("id", current_id)
                        existing = messages.get(key, "")
                        messages[key] = existing if existing.startswith(content) else content if content.startswith(existing) else existing + content
                        self._update(run, content="\n\n".join(value for value in messages.values() if value), model=body.get("model"))
                elif kind == "result":
                    native_id = message.get("session_id")
                    if isinstance(native_id, str):
                        self.store.native(run.chat_id, native_id)
                    if message.get("is_error"):
                        raise HarnessError(_error("claude", message.get("errors") or message.get("result") or message.get("subtype")))
                    result = message.get("result")
                    if isinstance(result, str) and result:
                        self._update(run, content=result)
                    usage = message.get("usage", {})
                    self._update(run, usage={"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)})
                    return
                elif kind == "control_request":
                    process.send({"type": "control_response", "response": {"subtype": "error", "request_id": message.get("request_id"), "error": "Ferramentas indisponíveis no chat."}})

    def _codex(self, run: ChatRun, chat: dict, text: str, effort: str | None):
        with self._process(run, codex_args()) as process:
            codex_initialize(process)
            params = {"model": run.model, "cwd": str(self.cwd), "sandbox": "read-only",
                      "approvalPolicy": "never", "baseInstructions": CHAT_INSTRUCTIONS}
            if chat["native_id"]:
                params["threadId"] = chat["native_id"]
                response = process.rpc(2, "thread/resume", params)
            else:
                response = process.rpc(2, "thread/start", params)
            thread_id = response["thread"]["id"]
            self.store.native(run.chat_id, thread_id)
            turn_params = {"threadId": thread_id, "model": run.model, "input": [{"type": "text", "text": text}]}
            if effort:
                turn_params["effort"] = effort
            process.rpc(3, "turn/start", turn_params)
            deadline = time.monotonic() + 600
            messages: dict[str, str] = {}
            provider_error = None
            while True:
                message = process.read(deadline, run.cancel)
                process.deny_request(message)
                params = message.get("params", {})
                if params.get("threadId") not in {None, thread_id}:
                    continue
                method = message.get("method")
                if method == "item/agentMessage/delta":
                    key = params.get("itemId", "answer")
                    messages[key] = messages.get(key, "") + params.get("delta", "")
                    self._update(run, content="\n\n".join(value for value in messages.values() if value))
                elif method == "item/completed":
                    item = params.get("item", {})
                    if item.get("type") == "agentMessage":
                        messages[item.get("id", "answer")] = item.get("text", "")
                        self._update(run, content="\n\n".join(value for value in messages.values() if value))
                elif method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage", {}).get("last", {})
                    self._update(run, usage={"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)})
                elif method == "error":
                    provider_error = params.get("error")
                elif method == "turn/completed":
                    turn = params.get("turn", {})
                    if turn.get("status") != "completed":
                        raise HarnessError(_error("codex", turn.get("error") or provider_error or turn.get("status")))
                    return

    def cancel_run(self, run_id: str) -> dict:
        with self.lock:
            run = self.runs[run_id]
            if run.state == "running":
                run.cancel.set()
                process = run.process
            else:
                process = None
        if process:
            process.close()
        return self.get_run(run_id)

    def delete(self, chat_id: str):
        with self.lock:
            if chat_id in self.active:
                raise ChatConflict("Pare a resposta antes de excluir a conversa.")
            self.store.delete(chat_id)

    def close(self):
        with self.lock:
            self.closed = True
            runs = list(self.runs.values())
        for run in runs:
            if run.state == "running":
                self.cancel_run(run.id)
        for run in runs:
            if run.thread and run.thread.is_alive():
                run.thread.join(timeout=35)
        self.store.close()
