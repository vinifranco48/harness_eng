from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.web.api.domain.auth import AuthState, AuthStatus, Provider
from apps.web.api.domain.commands import CliCommand
from apps.web.api.domain.errors import JobConflict, JobInputError
from apps.web.api.domain.jobs import AuthJob
from apps.web.api.main import create_app
from apps.web.api.services.auth_jobs import AuthJobs


class FakeAuth:
    auth_home = None

    def __init__(self, marker: Path | None = None):
        self.marker = marker

    def status(self, provider):
        state = AuthState.CONNECTED if self.marker and self.marker.exists() else AuthState.DISCONNECTED
        return AuthStatus(Provider(provider), state, "Status de teste.")

    def environment(self, provider):
        return {"PATH": os.defpath, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows")}


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(FakeAuth()))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        token = self.client.get("/api/bootstrap").json()["csrf_token"]
        self.headers = {"X-Harness-CSRF": token, "Origin": "http://testserver"}

    def test_web_page_and_assets_are_served(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Suas contas.", page.text)
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)

    def test_status_only_returns_public_metadata(self):
        status = self.client.get("/api/status").json()
        self.assertEqual(len(status["providers"]), 2)
        self.assertNotIn("cli_path", status["providers"][0])
        self.assertNotIn("csrf_token", status)

    def test_mutations_require_token_and_same_origin(self):
        self.assertEqual(self.client.post("/api/auth/claude/logout").status_code, 403)
        headers = {**self.headers, "Origin": "https://example.com"}
        self.assertEqual(self.client.post("/api/auth/claude/logout", headers=headers).status_code, 403)

    def test_invalid_provider_and_device_mode_are_rejected(self):
        self.assertEqual(self.client.post("/api/auth/other/login", json={}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/api/auth/claude/login", json={"mode": "device"}, headers=self.headers).status_code, 400)

    def test_disconnected_logout_finishes_without_process(self):
        result = self.client.post("/api/auth/codex/logout", headers=self.headers)
        self.assertEqual(result.status_code, 202)
        job_id = result.json()["job"]["id"]
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()["job"]
            if job["state"] != "running":
                break
            time.sleep(.02)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(job["result"]["state"], "disconnected")

    def test_unknown_job_and_invalid_code_do_not_mutate_accounts(self):
        self.assertEqual(self.client.get("/api/jobs/unknown").status_code, 404)
        self.assertEqual(self.client.post("/api/jobs/unknown/code", json={"code": "example"}, headers=self.headers).status_code, 404)

    def test_untrusted_hosts_are_rejected(self):
        self.assertEqual(self.client.get("/", headers={"Host": "example.com"}).status_code, 400)


class WebJobTests(unittest.TestCase):
    def test_only_authorization_urls_are_exposed(self):
        jobs = AuthJobs(FakeAuth())
        job = AuthJob("example", Provider.CLAUDE, "login", "browser", False)
        for value in (
            "https://example.com/oauth/authorize?state=test",
            "https://claude.ai/oauth/authorize?access_token=secret",
            "https://claude.ai:invalid/oauth/authorize",
            "accessToken=secret; stderr contains private credentials",
        ):
            jobs._output(job, value)
        self.assertIsNone(job.public()["auth_url"])
        jobs._output(job, "https://claude.ai/oauth/authorize?state=test\n")
        self.assertEqual(job.public()["auth_url"], "https://claude.ai/oauth/authorize?state=test")
        self.assertTrue(job.public()["needs_code"])
        jobs._finish(job, "succeeded", "Conectado")
        self.assertIsNone(job.public()["auth_url"])

    def test_device_code_is_captured_without_exporting_raw_output(self):
        jobs = AuthJobs(FakeAuth())
        job = AuthJob("example", Provider.CODEX, "login", "device", False)
        jobs._output(job, "Visit https://auth.openai.com/codex/device\nABCD-EFGH\nsecret-token")
        self.assertEqual(job.public()["user_code"], "ABCD-EFGH")
        self.assertNotIn("secret-token", str(job.public()))

    def test_multiline_code_is_rejected(self):
        with self.assertRaises(JobInputError):
            AuthJobs(FakeAuth()).send_code("unknown", "one\ntwo")

    def _wait(self, jobs, job_id, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = jobs.get(job_id)
            if predicate(job):
                return job
            time.sleep(.02)
        self.fail("Job did not reach expected state")

    def test_claude_login_accepts_code_in_the_web_job(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "connected"
            script = Path(root) / "fake_cli.py"
            script.write_text(
                "import sys\nfrom pathlib import Path\n"
                "print('https://claude.ai/oauth/authorize?state=test', flush=True)\n"
                "print('Paste code here if prompted', flush=True)\n"
                "code = sys.stdin.readline().strip()\n"
                f"Path({str(marker)!r}).touch() if code == 'test-code' else None\n"
                "sys.exit(0 if code == 'test-code' else 1)\n", encoding="utf-8",
            )
            jobs = AuthJobs(FakeAuth(marker), timeout=5)
            command = CliCommand((sys.executable, str(script)), str(script))
            with patch("apps.web.api.services.auth_jobs.resolve_cli", return_value=command):
                try:
                    job_id = jobs.start(Provider.CLAUDE, "login")["id"]
                    job = self._wait(jobs, job_id, lambda job: job["needs_code"])
                    self.assertTrue(job["auth_url"])
                    with self.assertRaises(JobConflict):
                        jobs.start(Provider.CLAUDE, "login")
                    snapshot = jobs.send_code(job_id, "test-code")
                    self.assertNotIn("test-code", str(snapshot))
                    job = self._wait(jobs, job_id, lambda job: job["state"] != "running")
                    self.assertEqual(job["state"], "succeeded")
                    self.assertIsNone(job["auth_url"])
                finally:
                    jobs.close()

    def test_codex_web_uses_structured_app_server_auth(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "connected"
            script = Path(root) / "fake_codex.py"
            script.write_text(
                "import json, sys\nfrom pathlib import Path\n"
                "first = json.loads(sys.stdin.readline())\n"
                "assert first['method'] == 'initialize'\n"
                "print(json.dumps({'id': 1, 'result': {}}), flush=True)\n"
                "assert json.loads(sys.stdin.readline())['method'] == 'initialized'\n"
                "request = json.loads(sys.stdin.readline())\n"
                "assert request['method'] == 'account/login/start'\n"
                "print(json.dumps({'id': 2, 'result': {'authUrl': 'https://auth.openai.com/oauth/authorize?state=test'}}), flush=True)\n"
                f"Path({str(marker)!r}).touch()\n"
                "print(json.dumps({'method': 'account/login/completed', 'params': {'success': True}}), flush=True)\n"
                "sys.stdin.read()\n", encoding="utf-8",
            )
            jobs = AuthJobs(FakeAuth(marker), timeout=5)
            command = CliCommand((sys.executable, str(script)), str(script))
            with patch("apps.web.api.services.auth_jobs.resolve_cli", return_value=command):
                try:
                    job_id = jobs.start(Provider.CODEX, "login")["id"]
                    job = self._wait(jobs, job_id, lambda job: job["state"] != "running")
                    self.assertEqual(job["state"], "succeeded")
                finally:
                    jobs.close()

    def test_cancellation_stops_pending_login_process(self):
        with tempfile.TemporaryDirectory() as root:
            script = Path(root) / "pending.py"
            script.write_text("import time\nprint('https://claude.ai/oauth/authorize?state=test', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
            jobs = AuthJobs(FakeAuth(), timeout=5)
            command = CliCommand((sys.executable, str(script)), str(script))
            with patch("apps.web.api.services.auth_jobs.resolve_cli", return_value=command):
                try:
                    job_id = jobs.start(Provider.CLAUDE, "login")["id"]
                    self._wait(jobs, job_id, lambda job: job["auth_url"])
                    process = jobs._jobs[job_id].process
                    self.assertEqual(jobs.cancel(job_id)["state"], "cancelled")
                    self.assertIsNotNone(process.poll())
                    self.assertEqual(jobs.active(), [])
                finally:
                    jobs.close()


if __name__ == "__main__":
    unittest.main()
