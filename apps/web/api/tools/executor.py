"""Dispatch tool calls to explicitly registered application handlers."""

from .contracts import Tool


class ToolExecutor:
    def __init__(self, tools: dict[str, Tool]):
        self._tools = dict(tools)

    def execute(self, name: str, arguments: dict) -> dict:
        tool = self._tools.get(name)

        if tool is None:
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_NOT_FOUND",
                    "message": f"Ferramenta não registrada: {name}",
                },
            }

        return tool.execute(arguments)
