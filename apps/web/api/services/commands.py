"""Resolve native CLIs, including Windows npm shims, without a shell."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Mapping

from ..domain.commands import CliCommand
from ..domain.errors import CliResolutionError


def _npm_executable(shim: Path, provider: str) -> Path | None:
    modules = shim.parent / "node_modules"
    if provider == "claude":
        candidate = modules / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        return candidate if candidate.is_file() else None
    if os.name != "nt":
        return None
    architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    target = "aarch64-pc-windows-msvc" if architecture == "arm64" else "x86_64-pc-windows-msvc"
    package = modules / "@openai" / "codex"
    optional = f"codex-win32-{architecture}"
    roots = (
        package / "node_modules" / "@openai" / optional,
        modules / "@openai" / optional,
        package,
    )
    for root in roots:
        candidate = root / "vendor" / target / "bin" / "codex.exe"
        if candidate.is_file():
            return candidate
    return None


def resolve_cli(provider: str, env: Mapping[str, str]) -> CliCommand | None:
    override_name = f"HARNESS_ENG_{provider.upper()}_BIN"
    override = env.get(override_name, "").strip()
    search_path = env.get("PATH", os.defpath)
    located = shutil.which(override or provider, path=search_path)
    if override and located is None:
        candidate = Path(override).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            located = str(candidate)
        else:
            raise CliResolutionError(f"{override_name} não aponta para um executável válido.")
    if located is None:
        directories = [Path.home() / ".local" / "bin"]
        if os.name == "nt" and env.get("APPDATA"):
            directories.append(Path(env["APPDATA"]) / "npm")
        suffixes = (".exe", ".cmd", "") if os.name == "nt" else ("",)
        for directory in directories:
            for suffix in suffixes:
                candidate = directory / f"{provider}{suffix}"
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    located = str(candidate)
                    break
            if located is not None:
                break
    if located is None:
        return None
    path = Path(located).absolute()
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        native = _npm_executable(path, provider)
        if native is None:
            raise CliResolutionError(
                f"O wrapper de {provider} não contém o executável nativo. "
                f"Configure {override_name} com o caminho do .exe."
            )
        path = native
    if path.suffix.lower() == ".js":
        node = shutil.which("node", path=search_path)
        if node is None:
            raise CliResolutionError("Node.js não foi encontrado para executar o CLI .js.")
        return CliCommand((node, str(path)), str(path))
    return CliCommand((str(path),), str(path))
