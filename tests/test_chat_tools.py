import json
import sqlite3
import tempfile
import unittest
from collections import deque
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from apps.web.api.adapter.codex.tools import CodexToolBridge, CodexToolSession
from apps.web.api.domain.auth import Provider
from apps.web.api.domain.chat import ChatRun
from apps.web.api.services.chat import ChatManager
from apps.web.api.services.chat_store import ChatStore
from apps.web.api.services.protocol import CHAT_INSTRUCTIONS, JsonProcess
from apps.web.api.tools.executor import ToolExecutor


DEFINITION = {
    "name": "analysis_create", "description": "Cria uma análise.",
    "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
}


class FakeTool:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True, "analysis_id": "analysis-test", "analysis_url": "/analyses/analysis-test"}

    def execute(self, arguments):
        self.calls.append(deepcopy(arguments))
        return deepcopy(self.result)


def tool_call(request_id=0, call_id="call-1", **params):
    return {"id": request_id, "method": "item/tool/call", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "callId": call_id,
        "tool": "analysis_create", "arguments": {"title": "Receita"}, **params,
    }}


def tool_payload(response):
    return json.loads(response["result"]["contentItems"][0]["text"])


class ScriptedProcess(JsonProcess):
    """Use the real RPC dispatcher with scripted JSON, never a subprocess."""

    def __init__(self, incoming):
        self.incoming = deque(incoming)
        self.deferred = deque()
        self.sent = []
        self.tool_handler = None
        self.was_closed = False

    def send(self, message):
        self.sent.append(deepcopy(message))

    def read(self, deadline, cancel=None, *, queued=True):
        if queued and self.deferred:
            return self.deferred.popleft()
        return self.incoming.popleft()

    def close(self):
        self.was_closed = True


class CodexToolSessionTests(unittest.TestCase):
    def setUp(self):
        self.tool = FakeTool()
        self.session = CodexToolSession(CodexToolBridge(ToolExecutor({"analysis_create": self.tool})), "thread-1", {"analysis_create"})

    def test_duplicate_call_reuses_result_and_echoes_new_request_id(self):
        first = self.session.handle(tool_call())
        repeated = self.session.handle(tool_call(request_id="retry"))
        self.assertEqual(first["id"], 0)
        self.assertEqual(repeated["id"], "retry")
        self.assertEqual(first["result"], repeated["result"])
        self.assertEqual(self.tool.calls, [{"title": "Receita"}])

    def test_duplicate_with_changed_arguments_does_not_execute_again(self):
        self.session.handle(tool_call())
        response = self.session.handle(tool_call(arguments={"title": "Outra"}))
        self.assertFalse(response["result"]["success"])
        self.assertEqual(tool_payload(response)["error"]["code"], "DUPLICATE_CALL_CONFLICT")
        self.assertEqual(len(self.tool.calls), 1)

    def test_wrong_thread_or_turn_cannot_execute(self):
        self.session.turn_id = "turn-1"
        for params in ({"threadId": "other"}, {"threadId": None}, {"turnId": "other"}):
            with self.subTest(params=params):
                response = self.session.handle(tool_call(**params))
                self.assertEqual(tool_payload(response)["error"]["code"], "INVALID_TOOL_CONTEXT")
        self.assertEqual(self.tool.calls, [])

    def test_malformed_context_arguments_and_names_do_not_execute(self):
        for params in ({"callId": None}, {"callId": ""}, {"callId": "x" * 513}, {"turnId": []}, {"tool": {}}, {"arguments": []}):
            with self.subTest(params=params):
                response = self.session.handle(tool_call(**params))
                self.assertEqual(tool_payload(response)["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.tool.calls, [])

    def test_unadvertised_tool_and_namespace_are_denied(self):
        for params in ({"tool": "shell"}, {"namespace": "external"}):
            with self.subTest(params=params):
                response = self.session.handle(tool_call(**params))
                self.assertEqual(tool_payload(response)["error"]["code"], "TOOL_NOT_FOUND")
        self.assertEqual(self.tool.calls, [])

    def test_invalid_rpc_id_cannot_execute(self):
        response = self.session.handle(tool_call(request_id=False))
        self.assertEqual(response["error"]["code"], -32600)
        self.assertIsNone(response["id"])
        self.assertEqual(self.tool.calls, [])

    def test_cache_limit_does_not_evict_previous_calls(self):
        self.session.MAX_CALLS = 1
        self.session.handle(tool_call())
        response = self.session.handle(tool_call(call_id="call-2"))
        self.assertEqual(tool_payload(response)["error"]["code"], "TOOL_CALL_LIMIT")
        self.session.handle(tool_call(request_id=9))
        self.assertEqual(len(self.tool.calls), 1)


class ChatToolIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.tool = FakeTool()
        self.manager = ChatManager(Mock(), self.root, tool_executor=ToolExecutor({"analysis_create": self.tool}), tool_definitions=[DEFINITION])
        self.addCleanup(self.manager.close)
        self.chat = self.manager.store.create(Provider.CODEX, "test-model", "low")
        self.message_id = self.manager.store.begin(self.chat["id"], "Crie a análise", "test-model", "low")
        self.run = ChatRun("run-1", self.chat["id"], self.message_id, "codex", "test-model")

    def messages(self, *, before_turn=(), after_turn=()):
        return [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            *before_turn,
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            *after_turn,
            {"method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "itemId": "answer", "delta": "Resultado disponível."}},
            {"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}}},
        ]

    def run_script(self, messages, chat=None):
        process = ScriptedProcess(messages)
        with patch.object(self.manager, "_process", return_value=process):
            self.manager._codex(self.run, chat or self.chat, "Crie a análise", "low")
        return process

    def test_tools_execute_during_rpc_and_stream_without_confusing_ids(self):
        early = tool_call(request_id=3, arguments={"title": "Durante RPC"})
        late = tool_call(request_id=0, call_id="call-2", arguments={"title": "Durante stream"})
        repeated = {**early, "id": 4}
        process = self.run_script(self.messages(before_turn=[early], after_turn=[repeated, late]))
        self.assertEqual(self.tool.calls, [{"title": "Durante RPC"}, {"title": "Durante stream"}])
        responses = [message for message in process.sent if "result" in message]
        self.assertEqual([message["id"] for message in responses], [3, 4, 0])
        self.assertTrue(all(message["result"]["success"] for message in responses))
        self.assertEqual(self.run.content, "Resultado disponível.")
        self.assertTrue(process.was_closed)

    def test_tools_are_advertised_with_installed_schema_and_opt_in(self):
        process = self.run_script(self.messages())
        initialize = next(message for message in process.sent if message.get("method") == "initialize")
        start = next(message for message in process.sent if message.get("method") == "thread/start")
        self.assertEqual(initialize["params"]["capabilities"], {"experimentalApi": True})
        self.assertEqual(start["params"]["dynamicTools"], [{**DEFINITION, "type": "function"}])
        self.assertEqual(start["params"]["sandbox"], "read-only")
        self.assertEqual(start["params"]["approvalPolicy"], "never")
        self.assertIn("ok=true", start["params"]["baseInstructions"])
        self.assertNotIn("type", DEFINITION)

    def test_unknown_and_native_requests_stay_blocked(self):
        calls = [tool_call(threadId="other-thread"),
                 {"id": 8, "method": "item/commandExecution/requestApproval", "params": {}},
                 {"id": 9, "method": "tool/requestUserInput", "params": {}},
                 {**tool_call(request_id=10), "result": {}}]
        process = self.run_script(self.messages(after_turn=calls))
        self.assertEqual(self.tool.calls, [])
        by_id = {item["id"]: item for item in process.sent if "method" not in item}
        self.assertEqual(tool_payload(by_id[0])["error"]["code"], "INVALID_TOOL_CONTEXT")
        self.assertEqual(by_id[8]["result"], {"decision": "decline"})
        self.assertEqual(by_id[9]["error"]["code"], -32601)
        self.assertNotIn(10, by_id)

    def test_failure_is_sent_to_codex_with_original_payload(self):
        self.tool.result = {"ok": False, "error": {"code": "STORAGE_ERROR", "message": "Não foi possível salvar."}}
        process = self.run_script(self.messages(after_turn=[tool_call()]))
        response = next(item for item in process.sent if "result" in item)
        self.assertFalse(response["result"]["success"])
        self.assertEqual(tool_payload(response), self.tool.result)

    def test_notifications_received_inside_rpc_are_preserved(self):
        notification = {"method": "item/agentMessage/delta", "params": {"threadId": "thread-1", "itemId": "intro", "delta": "Preparando."}}
        self.run_script(self.messages(before_turn=[notification, tool_call()]))
        self.assertEqual(self.run.content, "Preparando.\n\nResultado disponível.")

    def test_compatible_thread_resumes_without_dynamic_tools_param(self):
        chat = {**self.chat, "native_id": "thread-1", "native_tools_signature": self.manager.tool_signature}
        process = self.run_script(self.messages(), chat)
        resume = next(item for item in process.sent if item.get("method") == "thread/resume")
        self.assertNotIn("dynamicTools", resume["params"])
        self.assertFalse(any(item.get("method") == "thread/start" for item in process.sent))
        self.assertEqual(self.manager.store.get(self.chat["id"])["native_tools_signature"], self.manager.tool_signature)
        self.assertNotIn("native_tools_signature", self.manager.public_chat(self.chat["id"]))

    def test_legacy_thread_starts_with_tools_and_recent_visible_context(self):
        chat = {**self.chat, "native_id": "legacy-thread", "messages": [
            {"role": "user", "content": "Minha região é Norte.", "state": "completed"},
            {"role": "assistant", "content": "Vou usar Norte.", "state": "completed"},
        ]}
        process = self.run_script(self.messages(), chat)
        self.assertTrue(any(item.get("method") == "thread/start" for item in process.sent))
        turn = next(item for item in process.sent if item.get("method") == "turn/start")
        self.assertIn("Minha região é Norte.", turn["params"]["input"][0]["text"])
        self.assertTrue(turn["params"]["input"][0]["text"].endswith("Crie a análise"))

    def test_plain_chat_keeps_native_thread_and_existing_instructions(self):
        self.manager.tool_definitions = []
        self.manager.tool_signature = None
        process = self.run_script(self.messages(), {**self.chat, "native_id": "thread-1"})
        initialize = next(item for item in process.sent if item.get("method") == "initialize")
        resume = next(item for item in process.sent if item.get("method") == "thread/resume")
        self.assertNotIn("capabilities", initialize["params"])
        self.assertEqual(resume["params"]["baseInstructions"], CHAT_INSTRUCTIONS)

    def test_migration_context_is_bounded_and_ignores_incomplete_messages(self):
        context = self.manager._context_for_new_thread({"messages": [
            {"role": "assistant", "state": "running", "content": "INCOMPLETE"},
            {"role": "user", "state": "completed", "content": "Receita\n" * 100_000},
        ]}, "Agora crie um gráfico")
        self.assertLess(len(context), 32_000)
        self.assertNotIn("INCOMPLETE", context)
        self.assertTrue(context.endswith("Agora crie um gráfico"))


class ChatStoreToolsMigrationTests(unittest.TestCase):
    def test_legacy_database_migrates_without_losing_chat_and_persists_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE chats (id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, effort TEXT, title TEXT NOT NULL, native_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
                db.execute("INSERT INTO chats VALUES ('legacy', 'codex', 'model', NULL, 'Conversa anterior', 'native', 1, 1)")
            store = ChatStore(path)
            self.assertEqual(store.get("legacy")["title"], "Conversa anterior")
            self.assertIsNone(store.get("legacy")["native_tools_signature"])
            store.native("legacy", "new-native", tools_signature="abc123")
            store.close()
            reopened = ChatStore(path)
            try:
                self.assertEqual(reopened.get("legacy")["native_tools_signature"], "abc123")
                self.assertEqual(reopened.get("legacy")["native_id"], "new-native")
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
