"""Structural contract shared by application tool handlers."""

from typing import Protocol


class Tool(Protocol):
    def execute(self, arguments: dict) -> dict: ...
