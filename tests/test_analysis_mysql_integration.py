"""Opt-in integration checks against the configured MariaDB application schema.

Run after the administrative migrations with HARNESS_MYSQL_INTEGRATION=1.
Every created row is tracked by UUID and removed, including on assertion failure.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx

from apps.web.api.adapter.mysql import MySQLClient, MySQLConfig
from apps.web.api.adapter.mysql.analysis_repository import MySQLAnalysisRepository
from apps.web.api.domain.analysis import Analysis, AnalysisStorageError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_CODE = """
import sys
from pathlib import Path
from unittest.mock import patch
import uvicorn
from apps.web.api.adapter.mysql import MySQLClient, MySQLConfig
from apps.web.api.main import create_app
class Auth:
    auth_home = None
client = MySQLClient(MySQLConfig.from_env({}, config_file=Path(sys.argv[1])))
with patch('apps.web.api.main.MySQLConfig.is_configured', return_value=True), patch('apps.web.api.main.MySQLClient.from_env', return_value=client):
    app = create_app(Auth(), data_dir=Path(sys.argv[3]))
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='error')
"""


@unittest.skipUnless(os.environ.get("HARNESS_MYSQL_INTEGRATION") == "1", "MariaDB integration requires explicit opt-in")
class AnalysisMySQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config_path = Path(os.environ.get("HARNESS_MYSQL_CONFIG", str(PROJECT_ROOT / ".harness-eng/mysql.json")))
        self.config = MySQLConfig.from_env({}, config_file=self.config_path)
        self.client = MySQLClient(self.config)
        self.repository = MySQLAnalysisRepository(self.client)
        self.ids = []
        self.http_titles = []
        self.addCleanup(self.cleanup_rows)

    def cleanup_rows(self):
        if self.ids or self.http_titles:
            with self.client.connection() as connection, connection.cursor() as cursor:
                for analysis_id in self.ids:
                    cursor.execute("DELETE FROM analyses WHERE id = %s", (analysis_id,))
                # Reconcile a persisted POST even if its HTTP response failed.
                # Titles contain a UUID unique to this test run, never a prefix.
                for title in self.http_titles:
                    cursor.execute("DELETE FROM analyses WHERE title = %s", (title,))

    def save(self, title):
        analysis = Analysis.create(title)
        self.ids.append(analysis.id)
        self.repository.insert(analysis)
        return analysis

    def test_round_trip_across_independent_connections_and_unicode(self):
        analysis = self.save("Integração: Vendas por fazenda 🌱")
        reopened = MySQLAnalysisRepository(MySQLClient(self.config))
        self.assertEqual(reopened.get(analysis.id), analysis)
        self.assertIn(analysis, reopened.list(limit=100, offset=0))

    def test_sql_like_title_is_data_and_duplicate_insert_does_not_overwrite(self):
        analysis = self.save("Teste SQL: '); DROP TABLE analyses; --")
        replacement = Analysis(analysis.id, "Outra análise", analysis.created_at, analysis.updated_at)
        with self.assertRaises(AnalysisStorageError):
            self.repository.insert(replacement)
        self.assertEqual(self.repository.get(analysis.id), analysis)

    def test_failed_transaction_rolls_back_its_insert(self):
        analysis = Analysis.create("Integração: rollback")
        self.ids.append(analysis.id)
        with self.assertRaisesRegex(RuntimeError, "rollback expected"):
            with self.client.connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO analyses (id, title, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                    (analysis.id, analysis.title, analysis.created_at.replace(tzinfo=None),
                     analysis.updated_at.replace(tzinfo=None)),
                )
                raise RuntimeError("rollback expected")
        self.assertIsNone(self.repository.get(analysis.id))

    @contextmanager
    def server(self, data_dir, port):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PROJECT_ROOT)
        process = subprocess.Popen(
            [sys.executable, "-c", SERVER_CODE, str(self.config_path.resolve()), str(port), str(data_dir)],
            cwd=PROJECT_ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as browser:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("Integration HTTP server exited before startup.")
                    try:
                        if browser.get("/api/bootstrap").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.05)
                else:
                    self.fail("Integration HTTP server did not start within 15 seconds.")
                yield browser
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def test_http_creation_survives_a_real_backend_process_restart(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            title = f"Teste HTTP {uuid4()}: Vendas por fazenda 🌱"
            self.http_titles.append(title)
            with self.server(data_dir, port) as browser:
                token = browser.get("/api/bootstrap").json()["csrf_token"]
                response = browser.post(
                    "/api/analyses", json={"title": f"  {title}  "},
                    headers={"X-Harness-CSRF": token, "Origin": f"http://127.0.0.1:{port}"},
                )
                self.assertEqual(response.status_code, 201, response.text)
                original = response.json()["analysis"]
                self.ids.append(original["id"])
                self.assertEqual(original["title"], title)
            with self.server(data_dir, port) as browser:
                response = browser.get(f"/api/analyses/{original['id']}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["analysis"], original)
                page = browser.get("/api/analyses", params={"limit": 100}).json()
                self.assertIn(original, page["analyses"])
                # The analysis was created without any conversation.
                token = browser.get("/api/bootstrap").json()["csrf_token"]
                chats = browser.get("/api/chats", headers={"X-Harness-CSRF": token}).json()
                self.assertEqual(chats["chats"], [])


if __name__ == "__main__":
    unittest.main()
