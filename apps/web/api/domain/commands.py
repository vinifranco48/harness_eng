"""Resolved native CLI command."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CliCommand:
    argv: tuple[str, ...]
    path: str


