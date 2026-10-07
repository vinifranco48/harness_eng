"""Authentication job state and its browser-safe representation."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .auth import AuthStatus, Provider

@dataclass
class AuthJob:
    id: str
    provider: Provider
    action: str
    mode: str
    force: bool
    state: str = "running"
    message: str = "Verificando sua conexão…"
    auth_url: str | None = None
    user_code: str | None = None
    needs_code: bool = False
    result: AuthStatus | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    process: subprocess.Popen | None = field(default=None, repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    rpc_done: threading.Event = field(default_factory=threading.Event, repr=False)
    rpc_success: bool = False

    def public(self) -> dict[str, object]:
        # Raw process output, tokens and submitted authorization codes never
        # enter the public view or a log. Only the provider's sign-in URL/code.
        return {
            "id": self.id, "provider": self.provider.value, "action": self.action,
            "mode": self.mode, "state": self.state, "message": self.message,
            "auth_url": self.auth_url if self.state == "running" else None,
            "user_code": self.user_code if self.state == "running" else None,
            "needs_code": self.needs_code and self.state == "running",
            "started_at": self.started_at,
            "result": public_status(self.result) if self.result else None,
        }


def public_status(status: AuthStatus) -> dict[str, object]:
    return {
        "provider": status.provider.value, "state": status.state.value,
        "connected": status.connected, "auth_method": status.auth_method,
        "message": status.message,
    }


