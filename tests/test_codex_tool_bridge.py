import json
import unittest

from apps.web.api.adapter.codex.tools import CodexToolBridge
from apps.web.api.tools.executor import ToolExecutor


_UNSET = object()


class FakeTool:
    def __init__(self, result=_UNSET, error=None):
        self.calls = []
        self.result = (
            {
                "ok": True,
                "analysis_id": "analysis-123",
                "title": "Receita Comercial 🌱",
            }
            if result is _UNSET else result
        )
        self.error = error

    def execute(self, arguments: dict) -> dict:
        self.calls.append(arguments)
        if self.error is not None:
            raise self.error
        return self.result


class CodexToolBridgeTests(unittest.TestCase):

    def setUp(self):
        self.tool = FakeTool()
        self.bridge = CodexToolBridge(ToolExecutor({"analysis_create": self.tool}))

    def make_request(self, request_id=60):
        return {
            "id": request_id,
            "method": "item/tool/call",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "call-1",
                "tool": "analysis_create",
                "arguments": {"title": "Receita Comercial 🌱"},
            },
        }

    def decode_response(self, response, request_id, *, success):
        self.assertEqual(set(response), {"id", "result"})
        self.assertEqual(response["id"], request_id)
        self.assertIs(type(response["id"]), type(request_id))
        result = response["result"]
        self.assertEqual(set(result), {"contentItems", "success"})
        self.assertIs(result["success"], success)
        self.assertEqual(len(result["contentItems"]), 1)
        item = result["contentItems"][0]
        self.assertEqual(set(item), {"type", "text"})
        self.assertEqual(item["type"], "inputText")
        self.assertIsInstance(item["text"], str)
        return json.loads(item["text"])

    def assert_failure(self, response, request_id, code):
        payload = self.decode_response(response, request_id, success=False)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], code)
        self.assertIsInstance(payload["error"]["message"], str)
        self.assertTrue(payload["error"]["message"].strip())
        return payload

    def test_valid_tool_call(self):
        request = self.make_request()
        arguments = request["params"]["arguments"]

        response = self.bridge.handle(request)

        payload = self.decode_response(response, 60, success=True)
        self.assertEqual(payload, self.tool.result)
        self.assertEqual(self.tool.calls, [arguments])
        self.assertIs(self.tool.calls[0], arguments)
        text = response["result"]["contentItems"][0]["text"]
        self.assertIn("Receita Comercial 🌱", text)
        self.assertNotIn("\\u", text)

    def test_zero_and_string_request_ids_are_preserved(self):
        for request_id in (0, "request-60"):
            with self.subTest(request_id=request_id):
                response = self.bridge.handle(self.make_request(request_id))

                self.assertEqual(
                    self.decode_response(response, request_id, success=True),
                    self.tool.result,
                )

    def test_unknown_tool(self):
        request = self.make_request()
        request["params"]["tool"] = "unknown_tool"

        response = self.bridge.handle(request)

        payload = self.assert_failure(response, 60, "TOOL_NOT_FOUND")
        self.assertEqual(payload, {
            "ok": False,
            "error": {
                "code": "TOOL_NOT_FOUND",
                "message": "Ferramenta não registrada: unknown_tool",
            },
        })
        self.assertEqual(self.tool.calls, [])

    def test_handler_failure_payload_is_preserved(self):
        self.tool.result = {
            "ok": False,
            "error": {"code": "INVALID_TITLE", "message": "Título recusado."},
        }

        response = self.bridge.handle(self.make_request())

        payload = self.assert_failure(response, 60, "INVALID_TITLE")
        self.assertEqual(payload, self.tool.result)
        self.assertEqual(len(self.tool.calls), 1)

    def test_missing_or_invalid_params_fail_without_executing_tool(self):
        for params in (_UNSET, None, [], "invalid", 0, True):
            with self.subTest(params=params):
                request = self.make_request()
                if params is _UNSET:
                    del request["params"]
                else:
                    request["params"] = params

                response = self.bridge.handle(request)

                self.assert_failure(response, 60, "INVALID_ARGUMENTS")
                self.assertEqual(self.tool.calls, [])

    def test_missing_or_invalid_tool_names_fail_without_executing_tool(self):
        for name in (_UNSET, None, "", "   ", 7, False, [], {}):
            with self.subTest(tool=name):
                request = self.make_request()
                if name is _UNSET:
                    del request["params"]["tool"]
                else:
                    request["params"]["tool"] = name

                response = self.bridge.handle(request)

                self.assert_failure(response, 60, "INVALID_ARGUMENTS")
                self.assertEqual(self.tool.calls, [])

    def test_invalid_arguments(self):
        for arguments in (_UNSET, None, [], "invalid", 7, False):
            with self.subTest(arguments=arguments):
                request = self.make_request()
                if arguments is _UNSET:
                    del request["params"]["arguments"]
                else:
                    request["params"]["arguments"] = arguments

                response = self.bridge.handle(request)

                self.assert_failure(response, 60, "INVALID_ARGUMENTS")
                self.assertEqual(self.tool.calls, [])

    def test_failed_tool_call_preserves_zero_id(self):
        for params, code in (
            ({"tool": "unknown_tool", "arguments": {}}, "TOOL_NOT_FOUND"),
            ({"tool": "analysis_create", "arguments": []}, "INVALID_ARGUMENTS"),
        ):
            with self.subTest(params=params):
                request = self.make_request(request_id=0)
                request["params"].update(params)

                response = self.bridge.handle(request)

                self.assert_failure(response, 0, code)
                self.assertEqual(self.tool.calls, [])

    def test_other_envelopes_and_methods_raise_without_executing_tool(self):
        for message in (
            None,
            [],
            "invalid",
            {},
            {"method": "item/tool/call", "params": {}},
            {"id": 60, "result": {}},
            {"id": 60, "method": "turn/start", "params": {}},
            {"id": 60, "method": "item/tool/call", "result": None},
            {"id": 60, "method": "item/tool/call", "error": {}},
        ):
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    self.bridge.handle(message)
                self.assertEqual(self.tool.calls, [])

    def test_invalid_request_id_types_raise_without_executing_tool(self):
        for request_id in (True, False, None, [], {}, 60.0):
            with self.subTest(request_id=request_id):
                with self.assertRaises(ValueError):
                    self.bridge.handle(self.make_request(request_id))
                self.assertEqual(self.tool.calls, [])

    def test_unexpected_tool_errors_use_fixed_message_without_details(self):
        public_messages = []
        for error in (
            RuntimeError("secret database password"),
            ValueError("private query and credentials"),
        ):
            with self.subTest(error=type(error).__name__):
                tool = FakeTool(error=error)
                bridge = CodexToolBridge(ToolExecutor({"analysis_create": tool}))

                response = bridge.handle(self.make_request())

                payload = self.assert_failure(response, 60, "TOOL_EXECUTION_FAILED")
                public_messages.append(payload["error"]["message"])
                self.assertNotIn(str(error), json.dumps(response))
                self.assertEqual(len(tool.calls), 1)
        self.assertEqual(public_messages[0], public_messages[1])

    def test_invalid_tool_results_return_serializable_failure(self):
        for result in (
            None,
            [],
            "invalid",
            {},
            {"ok": None},
            {"ok": 0},
            {"ok": 1},
            {"ok": "true"},
            {"ok": True, "value": object()},
            {"ok": True, "value": float("nan")},
            {"ok": False, "value": float("inf")},
        ):
            with self.subTest(result=result):
                tool = FakeTool(result=result)
                bridge = CodexToolBridge(ToolExecutor({"analysis_create": tool}))

                response = bridge.handle(self.make_request())

                self.assert_failure(response, 60, "INVALID_TOOL_RESULT")
                json.dumps(response, allow_nan=False)
                self.assertEqual(len(tool.calls), 1)
