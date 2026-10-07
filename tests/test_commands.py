from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.web.api.domain.errors import CliResolutionError
from apps.web.api.services.commands import resolve_cli


class CommandTests(unittest.TestCase):
    def test_windows_claude_npm_shim_resolves_native_executable_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli with spaces ") as root:
            folder = Path(root)
            shim = folder / "claude.cmd"
            shim.touch()
            native = folder / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            with patch("apps.web.api.services.commands.shutil.which", return_value=str(shim)):
                command = resolve_cli("claude", {"PATH": root})
            self.assertEqual(command.argv, (str(native),))

    def test_windows_codex_optional_package_resolves_native_executable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            shim = folder / "codex.cmd"
            shim.touch()
            native = folder / "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            with patch("apps.web.api.services.commands.os.name", "nt"), patch("apps.web.api.services.commands.platform.machine", return_value="AMD64"), patch("apps.web.api.services.commands.shutil.which", return_value=str(shim)):
                command = resolve_cli("codex", {"PATH": root})
            self.assertEqual(command.argv, (str(native),))

    def test_broken_explicit_override_does_not_fall_back_to_another_cli(self) -> None:
        with patch("apps.web.api.services.commands.shutil.which", return_value=None):
            with self.assertRaises(CliResolutionError):
                resolve_cli("codex", {"HARNESS_ENG_CODEX_BIN": "missing-vendor-cli", "PATH": "tools"})

    def test_unknown_batch_wrapper_is_not_executed_by_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            shim = Path(root) / "custom.cmd"
            shim.touch()
            with patch("apps.web.api.services.commands.shutil.which", return_value=str(shim)):
                with self.assertRaises(CliResolutionError):
                    resolve_cli("claude", {"PATH": root})

    def test_javascript_entrypoint_uses_node_with_separate_arguments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="path with spaces ") as root:
            entrypoint = Path(root) / "codex.js"
            entrypoint.touch()
            with patch("apps.web.api.services.commands.shutil.which", side_effect=[str(entrypoint), "node.exe"]):
                command = resolve_cli("codex", {"PATH": root})
            self.assertEqual(command.argv, ("node.exe", str(entrypoint)))


if __name__ == "__main__":
    unittest.main()
