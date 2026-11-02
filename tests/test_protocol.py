import unittest
from collections import deque
from unittest.mock import Mock, call

from apps.web.api.domain.errors import HarnessError
from apps.web.api.services.protocol import JsonProcess, classify_message


class ClassifyMessageTests(unittest.TestCase):

    def test_method_with_id_is_request_including_zero(self):
        for request_id in (0, 1, "tool-call-1"):
            with self.subTest(request_id=request_id):
                message = {
                    "id": request_id,
                    "method": "item/tool/call",
                    "params": {"name": "analysis_create"},
                }

                self.assertEqual(classify_message(message), "request")

    def test_method_without_id_is_notification_with_or_without_params(self):
        for message in (
            {"method": "initialized"},
            {"method": "item/completed", "params": {"itemId": "item-1"}},
            {"jsonrpc": "2.0", "method": "turn/completed", "params": {}},
        ):
            with self.subTest(message=message):
                self.assertEqual(classify_message(message), "notification")

    def test_result_response_accepts_falsey_payloads(self):
        for result in (None, False, 0, "", [], {}):
            with self.subTest(result=result):
                self.assertEqual(
                    classify_message({"id": 0, "result": result}),
                    "response",
                )

    def test_error_with_zero_id_is_response(self):
        message = {
            "id": 0,
            "error": {"code": -32601, "message": "Method not found"},
        }

        self.assertEqual(classify_message(message), "response")

    def test_request_with_result_is_invalid(self):
        message = {
            "id": 1,
            "method": "item/tool/call",
            "result": {},
        }

        self.assertEqual(classify_message(message), "invalid")

    def test_response_with_result_and_error_is_invalid(self):
        message = {
            "id": 1,
            "result": {},
            "error": {"code": -32603},
        }

        self.assertEqual(classify_message(message), "invalid")

    def test_method_combined_with_response_fields_is_invalid(self):
        for identifiers in ({}, {"id": 0}):
            for payload in (
                {"result": None},
                {"error": {"code": -32601}},
                {"result": {}, "error": {}},
            ):
                message = {"method": "item/tool/call", **identifiers, **payload}
                with self.subTest(message=message):
                    self.assertEqual(classify_message(message), "invalid")

    def test_result_and_error_together_are_invalid(self):
        for identifiers in ({}, {"id": 0}):
            message = {**identifiers, "result": None, "error": None}
            with self.subTest(message=message):
                self.assertEqual(classify_message(message), "invalid")

    def test_incomplete_envelopes_are_invalid(self):
        for message in (
            {},
            {"id": 0},
            {"params": {}},
            {"result": None},
            {"error": {"code": -32601}},
            {"jsonrpc": "2.0"},
        ):
            with self.subTest(message=message):
                self.assertEqual(classify_message(message), "invalid")


class JsonProcessRpcTests(unittest.TestCase):

    def make_process(self, messages):
        process = JsonProcess.__new__(JsonProcess)
        process.send = Mock()
        process.read = Mock(side_effect=messages)
        process.deferred = deque()
        return process

    def test_server_request_with_matching_id_is_denied_until_response(self):
        result = {"status": "ok"}
        process = self.make_process([
            {"id": 7, "method": "item/tool/call", "params": {}},
            {"id": 7, "result": result},
        ])

        actual = process.rpc(7, "turn/start", {"threadId": "thread-1"})

        self.assertEqual(actual, result)
        self.assertEqual(process.read.call_count, 2)
        self.assertEqual(process.send.call_args_list, [
            call({
                "id": 7,
                "method": "turn/start",
                "params": {"threadId": "thread-1"},
            }),
            call({
                "id": 7,
                "error": {"code": -32601, "message": "Unavailable in chat"},
            }),
        ])

    def test_item_and_turn_notifications_are_deferred(self):
        notifications = [
            {"method": "item/completed", "params": {"itemId": "item-1"}},
            {"method": "turn/completed", "params": {"turnId": "turn-1"}},
        ]
        process = self.make_process([
            *notifications,
            {"id": 7, "result": {"status": "ok"}},
        ])

        result = process.rpc(7, "turn/start", {})

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(list(process.deferred), notifications)
        self.assertEqual(process.read.call_count, 3)
        for read_call in process.read.call_args_list:
            self.assertEqual(read_call.kwargs, {"queued": False})
        process.send.assert_called_once_with({
            "id": 7, "method": "turn/start", "params": {},
        })

    def test_invalid_matching_ids_are_not_accepted_as_responses(self):
        for invalid in (
            {"id": 7},
            {"id": 7, "result": {}, "error": {"code": -32603}},
            {"id": 7, "method": "item/tool/call", "result": {}},
        ):
            with self.subTest(message=invalid):
                process = self.make_process([
                    invalid,
                    {"id": 7, "result": {"status": "ok"}},
                ])

                result = process.rpc(7, "turn/start", {})

                self.assertEqual(result, {"status": "ok"})
                self.assertEqual(process.read.call_count, 2)

    def test_response_for_other_id_is_ignored(self):
        for unrelated in (
            {"id": 8, "result": {"status": "unrelated"}},
            {"id": 8, "error": {"code": -32603}},
        ):
            with self.subTest(message=unrelated):
                process = self.make_process([
                    unrelated,
                    {"id": 7, "result": {"status": "ok"}},
                ])

                result = process.rpc(7, "turn/start", {})

                self.assertEqual(result, {"status": "ok"})
                self.assertEqual(process.read.call_count, 2)
                process.send.assert_called_once_with({
                    "id": 7, "method": "turn/start", "params": {},
                })

    def test_matching_error_response_raises_harness_error(self):
        process = self.make_process([
            {"id": 7, "error": {"code": -32603, "message": "Failed"}},
        ])

        with self.assertRaises(HarnessError):
            process.rpc(7, "turn/start", {})

        self.assertEqual(process.read.call_count, 1)
        process.send.assert_called_once_with({
            "id": 7, "method": "turn/start", "params": {},
        })

    def test_response_with_zero_id_is_accepted(self):
        process = self.make_process([
            {"id": 0, "result": {"status": "ok"}},
        ])

        result = process.rpc(0, "initialize", {})

        self.assertEqual(result, {"status": "ok"})
        process.send.assert_called_once_with({
            "id": 0, "method": "initialize", "params": {},
        })

    def test_null_result_is_preserved(self):
        process = self.make_process([{"id": 7, "result": None}])

        result = process.rpc(7, "turn/start", {})

        self.assertIsNone(result)

    def test_boolean_id_does_not_match_integer_zero(self):
        process = self.make_process([
            {"id": False, "result": {"wrong": True}},
            {"id": 0, "result": {"ok": True}},
        ])

        self.assertEqual(process.rpc(0, "initialize", {}), {"ok": True})
        self.assertEqual(process.read.call_count, 2)

    def test_malformed_request_method_is_denied_without_interrupting_rpc(self):
        process = self.make_process([
            {"id": 2, "method": []},
            {"id": 7, "result": {"ok": True}},
        ])

        self.assertEqual(process.rpc(7, "initialize", {}), {"ok": True})
        self.assertEqual(process.send.call_args_list[-1].args[0]["error"]["code"], -32600)
