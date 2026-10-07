"""Conversation run state independent of its provider transport."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol


class ProcessHandle(Protocol):
    def close(self) -> None: ...


@dataclass
class ChatRun:
    id: str
    chat_id: str
    message_id: str
    provider: str
    model: str
    state: str = "running"
    content: str = ""
    error: str | None = None
    usage: dict | None = None
    revision: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    process: ProcessHandle | None = None
    thread: threading.Thread | None = None
    persisted_at: float = 0

    def public(self) -> dict:
        return {key: getattr(self, key) for key in (
            "id", "chat_id", "message_id", "provider", "model", "state", "content", "error", "usage", "revision"
        )}


