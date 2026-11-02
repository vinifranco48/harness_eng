import unittest

from apps.web.api.tools.executor import ToolExecutor


class FakeTool:
    def __init__(self):
        self.calls = []

    def execute(self, arguments: dict) -> dict:
        self.calls.append(arguments)
        return {"ok": True, "message": "Executado"}


class ToolExecutorTests(unittest.TestCase):

    def test_registered_tool(self):
        tool = FakeTool()
        executor = ToolExecutor({"fake": tool})
        arguments = {"title": "Receita Comercial"}

        result = executor.execute("fake", arguments)

        self.assertEqual(result, {"ok": True, "message": "Executado"})
        self.assertEqual(tool.calls, [arguments])
        self.assertIs(tool.calls[0], arguments)

    def test_unknown_tool(self):
        tool = FakeTool()
        executor = ToolExecutor({"fake": tool})

        result = executor.execute("unknown", {"title": "Receita Comercial"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "TOOL_NOT_FOUND")
        self.assertEqual(tool.calls, [])
