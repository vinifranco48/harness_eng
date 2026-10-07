# Copyright (2026) Databricks, Inc.
# Licensed under the Apache License, Version 2.0.
# Authentication types adapted from omnigent/onboarding/harness_install.py.
"""Providers and public authentication results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

class Provider(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class AuthState(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    MISSING_CLI = "missing_cli"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class AuthStatus:
    provider: Provider
    state: AuthState
    message: str
    cli_path: str | None = None
    auth_method: str | None = None

    @property
    def connected(self) -> bool:
        return self.state == AuthState.CONNECTED

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "connected": self.connected}


@dataclass(frozen=True)
class AuthResult:
    action: str
    success: bool
    status: AuthStatus
    message: str
    exit_code: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "success": self.success,
            "status": self.status.to_dict(),
            "message": self.message,
            "exit_code": self.exit_code,
        }


