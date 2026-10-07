from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.web.api.domain.auth import AuthState, Provider
from apps.web.api.services.auth import AuthManager
from apps.web.api.domain.commands import CliCommand


def response(code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, stderr)


CLAUDE_ON = response(stdout='{"loggedIn": true, "authMethod": "claude.ai"}')
CLAUDE_OFF = response(stdout='{"loggedIn": false}')
CODEX_ON = response(stderr="Logged in using ChatGPT")
CODEX_OFF = response(1, stderr="Not logged in")


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolve = patch("apps.web.api.services.auth.resolve_cli", return_value=CliCommand(("vendor-cli",), "vendor-cli")).start()
        self.run = patch("apps.web.api.services.auth.subprocess.run").start()
        self.sleep = patch("apps.web.api.services.auth.time.sleep").start()
        self.addCleanup(patch.stopall)
        self.auth = AuthManager(env={"PATH": "tools", "ANTHROPIC_API_KEY": "private-key", "CODEX_ACCESS_TOKEN": "private-token"})

    def test_claude_requires_a_real_boolean_even_when_exit_code_is_zero(self) -> None:
        for payload in ('{}', '{"loggedIn": "false"}', '{"loggedIn": 1}', '[]', 'not json'):
            with self.subTest(payload=payload):
                self.run.return_value = response(stdout=payload)
                self.assertEqual(self.auth.status("claude").state, AuthState.ERROR)

    def test_claude_exit_zero_logged_out_is_disconnected(self) -> None:
        self.run.return_value = CLAUDE_OFF
        self.assertEqual(self.auth.status("claude").state, AuthState.DISCONNECTED)

    def test_claude_nonzero_cannot_claim_connected(self) -> None:
        self.run.return_value = response(1, stdout='{"loggedIn": true}')
        self.assertEqual(self.auth.status("claude").state, AuthState.ERROR)

    def test_status_never_exports_raw_vendor_metadata(self) -> None:
        self.run.return_value = response(stdout=json.dumps({
            "loggedIn": True, "authMethod": ["unexpected"],
            "email": "private@example.com", "accessToken": "secret-token",
        }))
        rendered = json.dumps(self.auth.status("claude").to_dict())
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("private@example.com", rendered)
        self.assertIn('"auth_method": "cli"', rendered)

    def test_codex_accepts_stderr_status_and_distinguishes_config_errors(self) -> None:
        self.run.return_value = CODEX_ON
        self.assertTrue(self.auth.status("codex").connected)
        self.run.return_value = CODEX_OFF
        self.assertEqual(self.auth.status("codex").state, AuthState.DISCONNECTED)
        self.run.return_value = response(1, stderr="Error loading config.toml")
        self.assertEqual(self.auth.status("codex").state, AuthState.ERROR)

    def test_only_child_environment_is_cleaned(self) -> None:
        self.run.return_value = CLAUDE_ON
        self.auth.status("claude")
        child = self.run.call_args.kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", child)
        self.assertNotIn("CODEX_ACCESS_TOKEN", child)
        self.assertEqual(child["PATH"], "tools")
        self.assertEqual(self.auth._env["ANTHROPIC_API_KEY"], "private-key")

    def test_public_environment_is_an_independent_copy_for_the_harness(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            auth = AuthManager(auth_home=root, env={"PATH": "tools", "CLAUDE_CODE_OAUTH_TOKEN": "private"})
            environment = auth.environment(Provider.CLAUDE)
            self.assertEqual(environment["CLAUDE_CONFIG_DIR"], str(Path(root).absolute() / "claude"))
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", environment)
            environment["PATH"] = "modified"
            self.assertEqual(auth.environment(Provider.CLAUDE)["PATH"], "tools")

    def test_existing_login_does_not_start_another_login(self) -> None:
        self.run.return_value = CODEX_ON
        self.assertTrue(self.auth.login("codex").success)
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.run.call_args.args[0][-2:], ["login", "status"])

    def test_login_is_confirmed_after_native_flow(self) -> None:
        self.run.side_effect = [CODEX_OFF, response(), CODEX_ON]
        result = self.auth.login("codex", device_auth=True)
        self.assertTrue(result.success)
        invocation = self.run.call_args_list[1]
        self.assertEqual(invocation.args[0], ["vendor-cli", "login", "--device-auth"])
        self.assertNotIn("capture_output", invocation.kwargs)
        self.assertNotIn("shell", invocation.kwargs)

    def test_login_exit_zero_without_saved_auth_is_failure(self) -> None:
        self.run.side_effect = [CLAUDE_OFF, response(), CLAUDE_OFF, CLAUDE_OFF, CLAUDE_OFF]
        self.assertFalse(self.auth.login("claude").success)

    def test_failed_login_does_not_succeed_using_old_credentials(self) -> None:
        self.run.side_effect = [CODEX_ON, response(1), CODEX_ON]
        self.assertFalse(self.auth.login("codex", force=True).success)

    def test_logout_must_confirm_disconnected_not_status_error(self) -> None:
        broken = response(1, stderr="CLI internal error")
        self.run.side_effect = [CODEX_ON, response(), broken, broken, broken]
        result = self.auth.logout("codex")
        self.assertFalse(result.success)
        self.assertEqual(result.status.state, AuthState.ERROR)

    def test_logout_is_confirmed_by_native_status(self) -> None:
        self.run.side_effect = [CLAUDE_ON, response(), CLAUDE_OFF]
        self.assertTrue(self.auth.logout("claude").success)
        self.assertEqual(self.run.call_args_list[1].args[0], ["vendor-cli", "auth", "logout"])

    def test_logout_is_idempotent_when_disconnected(self) -> None:
        self.run.return_value = CODEX_OFF
        self.assertTrue(self.auth.logout("codex").success)
        self.assertEqual(self.run.call_count, 1)

    def test_status_timeout_is_not_disconnected(self) -> None:
        self.run.side_effect = subprocess.TimeoutExpired("cli", 15)
        self.assertEqual(self.auth.status("claude").state, AuthState.TIMEOUT)

    def test_login_timeout_cannot_be_reported_as_success(self) -> None:
        self.run.side_effect = [CODEX_OFF, subprocess.TimeoutExpired("cli", 600), CODEX_ON]
        self.assertFalse(self.auth.login("codex").success)

    def test_missing_cli_never_starts_login(self) -> None:
        self.resolve.return_value = None
        result = self.auth.login("claude")
        self.assertFalse(result.success)
        self.assertEqual(result.status.state, AuthState.MISSING_CLI)
        self.run.assert_not_called()

    def test_isolated_home_is_forwarded_on_all_operations(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            auth = AuthManager(auth_home=root, env={"PATH": "tools"})
            self.run.side_effect = [CODEX_OFF, response(), CODEX_ON]
            self.assertTrue(auth.login("codex").success)
            expected = str(Path(root).absolute() / "codex")
            self.assertTrue(Path(expected).is_dir())
            for invocation in self.run.call_args_list:
                self.assertEqual(invocation.kwargs["env"]["CODEX_HOME"], expected)
            self.assertFalse((Path(root) / "claude").exists())

    def test_invalid_timeout_and_claude_device_code_are_rejected(self) -> None:
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.auth.login(Provider.CODEX, timeout=value)
        with self.assertRaises(ValueError):
            self.auth.login(Provider.CLAUDE, device_auth=True)
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
