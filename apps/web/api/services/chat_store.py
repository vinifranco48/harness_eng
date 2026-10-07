"""SQLite persistence for conversation history."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..domain.auth import Provider


def _now() -> float:
    return time.time()


class ChatStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                effort TEXT, title TEXT NOT NULL, native_id TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, state TEXT NOT NULL, error TEXT,
                model TEXT NOT NULL, created_at REAL NOT NULL, usage TEXT
            );
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, created_at);
        """)
        # Native runs cannot survive a web-server restart.
        self.db.execute("UPDATE messages SET state='interrupted', error='A resposta foi interrompida ao reiniciar o servidor.' WHERE state='running'")
        self.db.commit()

    def list(self) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT id,provider,model,effort,title,created_at,updated_at FROM chats ORDER BY updated_at DESC LIMIT 200"
            )]

    def get(self, chat_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            if row is None:
                raise KeyError(chat_id)
            chat = dict(row)
            chat["messages"] = [dict(item) for item in self.db.execute(
                "SELECT * FROM messages WHERE chat_id=? ORDER BY created_at,rowid", (chat_id,)
            )]
            for message in chat["messages"]:
                message.pop("chat_id", None)
                message["usage"] = json.loads(message["usage"]) if message["usage"] else None
            return chat

    def create(self, provider: Provider, model: str, effort: str | None) -> dict:
        chat_id = str(uuid.uuid4())
        now = _now()
        with self.lock:
            self.db.execute("INSERT INTO chats VALUES (?,?,?,?,?,?,?,?)",
                            (chat_id, provider.value, model, effort, "Nova conversa", None, now, now))
            self.db.commit()
        return self.get(chat_id)

    def begin(self, chat_id: str, text: str, model: str, effort: str | None) -> str:
        assistant_id = str(uuid.uuid4())
        with self.lock:
            chat = self.get(chat_id)
            title = chat["title"]
            if not chat["messages"]:
                title = " ".join(text.split())[:70]
            now = _now()
            self.db.execute("UPDATE chats SET model=?,effort=?,title=?,updated_at=? WHERE id=?",
                            (model, effort, title, now, chat_id))
            for message_id, role, content, state in (
                (str(uuid.uuid4()), "user", text, "completed"),
                (assistant_id, "assistant", "", "running"),
            ):
                self.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
                                (message_id, chat_id, role, content, state, None, model, now, None))
            self.db.commit()
        return assistant_id

    def native(self, chat_id: str, native_id: str):
        with self.lock:
            self.db.execute("UPDATE chats SET native_id=? WHERE id=?", (native_id, chat_id))
            self.db.commit()

    def update(self, message_id: str, content: str, state: str, error: str | None, model: str, usage: dict | None):
        with self.lock:
            self.db.execute("UPDATE messages SET content=?,state=?,error=?,model=?,usage=? WHERE id=?",
                            (content, state, error, model, json.dumps(usage) if usage else None, message_id))
            self.db.execute("UPDATE chats SET updated_at=? WHERE id=(SELECT chat_id FROM messages WHERE id=?)", (_now(), message_id))
            self.db.commit()

    def delete(self, chat_id: str):
        with self.lock:
            self.get(chat_id)
            self.db.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            self.db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()


