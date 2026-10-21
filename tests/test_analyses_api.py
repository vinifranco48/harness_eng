from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from apps.web.api.domain.analysis import Analysis, AnalysisStorageError
from apps.web.api.domain.auth import AuthState, AuthStatus, Provider
from apps.web.api.main import create_app
from apps.web.api.services.analysis import AnalysisService


class FakeAuth:
    auth_home = None

    def status(self, provider):
        return AuthStatus(Provider(provider), AuthState.DISCONNECTED, "Status de teste.")

    def environment(self, provider):
        return {"PATH": os.defpath, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows")}


class MemoryAnalysisRepository:
    def __init__(self):
        self.rows: dict[str, Analysis] = {}
        self.calls: list[tuple] = []
        self.failure: str | None = None

    def _fail(self, operation: str):
        if self.failure == operation:
            raise AnalysisStorageError("private-mysql-password=needle; SELECT * FROM analyses")

    def insert(self, analysis: Analysis) -> None:
        self.calls.append(("insert", analysis.id))
        self._fail("insert")
        self.rows[analysis.id] = analysis

    def get(self, analysis_id: str) -> Analysis | None:
        self.calls.append(("get", analysis_id))
        self._fail("get")
        return self.rows.get(analysis_id)

    def list(self, *, limit: int, offset: int) -> list[Analysis]:
        self.calls.append(("list", limit, offset))
        self._fail("list")
        return list(reversed(list(self.rows.values())))[offset:offset + limit]


class AnalysesApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch("apps.web.api.main.MySQLConfig.is_configured", return_value=False), patch(
            "apps.web.api.main.MySQLClient.from_env"
        ) as mysql_factory:
            self.app = create_app(FakeAuth(), data_dir=Path(directory.name))
        mysql_factory.assert_not_called()
        self.assertIsNone(self.app.state.analysis)
        self.repository = MemoryAnalysisRepository()
        self.app.state.analysis = AnalysisService(self.repository)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        token = self.client.get("/api/bootstrap").json()["csrf_token"]
        self.headers = {"X-Harness-CSRF": token, "Origin": "http://testserver"}

    def create_analysis(self, title="Análise de vendas"):
        response = self.client.post("/api/analyses", json={"title": title}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["analysis"]

    def assert_public_analysis(self, analysis, title):
        self.assertEqual(set(analysis), {"id", "title", "created_at", "updated_at"})
        self.assertEqual(str(UUID(analysis["id"])), analysis["id"])
        self.assertEqual(analysis["title"], title)
        timestamps = []
        for key in ("created_at", "updated_at"):
            self.assertIsInstance(analysis[key], str)
            self.assertTrue(analysis[key].endswith("Z"), analysis[key])
            timestamp = datetime.fromisoformat(analysis[key].replace("Z", "+00:00"))
            self.assertEqual(timestamp.utcoffset(), timedelta(0))
            timestamps.append(timestamp)
        self.assertLessEqual(timestamps[0], timestamps[1])

    def test_create_returns_trimmed_title_uuid_and_utc_timestamps(self):
        response = self.client.post(
            "/api/analyses", json={"title": "  Análise de vendas  "}, headers=self.headers
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(set(response.json()), {"analysis"})
        analysis = response.json()["analysis"]
        self.assert_public_analysis(analysis, "Análise de vendas")
        self.assertEqual(self.repository.calls, [("insert", analysis["id"])])
        self.assertEqual(self.repository.rows[analysis["id"]].title, "Análise de vendas")

    def test_invalid_unicode_is_rejected_before_storage(self):
        for escaped in (r"\ud800", r"\udfff"):
            with self.subTest(escaped=escaped):
                response = self.client.post(
                    "/api/analyses", content='{"title":"' + escaped + '"}',
                    headers={**self.headers, "Content-Type": "application/json"},
                )
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_title_boundaries_are_valid_after_trimming(self):
        for title in ("x", "á" * 120):
            with self.subTest(length=len(title)):
                analysis = self.create_analysis(f" \t{title}\n ")
                self.assert_public_analysis(analysis, title)

    def test_create_assigns_different_ids_even_for_the_same_title(self):
        first = self.create_analysis("Vendas")
        second = self.create_analysis("Vendas")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.repository.rows), 2)

    def test_invalid_titles_and_payload_fields_are_rejected_without_storage(self):
        payloads = (
            {},
            {"title": ""},
            {"title": " \t\r\n "},
            {"title": "a" * 121},
            {"title": None},
            {"title": 123},
            {"title": True},
            {"title": []},
            {"title": {}},
            {"title": "Vendas", "id": str(uuid4())},
            {"title": "Vendas", "unknown": "extra"},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/analyses", json=payload, headers=self.headers)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_missing_or_malformed_request_body_is_rejected(self):
        for body in ("", "{", "[]", '"Vendas"'):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/analyses", content=body,
                    headers={**self.headers, "Content-Type": "application/json"},
                )
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_create_requires_csrf_and_rejects_foreign_origins(self):
        rejected_headers = (
            {},
            {"Origin": "http://testserver"},
            {**self.headers, "X-Harness-CSRF": "incorrect"},
            {**self.headers, "Origin": "https://example.com"},
            {**self.headers, "Origin": "http://testserver.example.com"},
            {**self.headers, "Origin": "null"},
        )
        for headers in rejected_headers:
            with self.subTest(headers=headers):
                response = self.client.post("/api/analyses", json={"title": "Vendas"}, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_get_returns_the_created_analysis_without_csrf(self):
        analysis = self.create_analysis()
        response = self.client.get(f"/api/analyses/{analysis['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"analysis": analysis})
        self.assertEqual(self.repository.calls[-1], ("get", analysis["id"]))

    def test_unknown_analysis_returns_not_found(self):
        response = self.client.get(f"/api/analyses/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(set(response.json()), {"detail"})

    def test_malformed_analysis_id_is_rejected_before_storage(self):
        for analysis_id in ("unknown", "123", "not-a-valid-uuid"):
            with self.subTest(analysis_id=analysis_id):
                response = self.client.get(f"/api/analyses/{analysis_id}")
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_empty_list_has_default_pagination_without_csrf(self):
        response = self.client.get("/api/analyses")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"analyses": [], "limit": 20, "offset": 0})
        self.assertEqual(self.repository.calls, [("list", 20, 0)])

    def test_list_passes_pagination_to_storage_and_returns_public_analyses(self):
        analyses = [self.create_analysis(f"Análise {number}") for number in range(4)]
        response = self.client.get("/api/analyses?limit=2&offset=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(), {"analyses": list(reversed(analyses))[1:3], "limit": 2, "offset": 1}
        )
        self.assertEqual(self.repository.calls[-1], ("list", 2, 1))
        for analysis in response.json()["analyses"]:
            self.assert_public_analysis(analysis, analysis["title"])

    def test_offset_past_the_last_analysis_returns_empty_page(self):
        self.create_analysis()
        response = self.client.get("/api/analyses?limit=1&offset=5")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"analyses": [], "limit": 1, "offset": 5})

    def test_invalid_pagination_is_rejected_before_storage(self):
        queries = (
            "limit=0", "limit=-1", "limit=101", "limit=1.5", "limit=abc", "limit=",
            "offset=-1", "offset=2147483648", "offset=1.5", "offset=abc", "offset=",
        )
        for query in queries:
            with self.subTest(query=query):
                response = self.client.get(f"/api/analyses?{query}")
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_analyses_are_unavailable_without_configured_storage(self):
        self.app.state.analysis = None
        requests = (
            ("GET", "/api/analyses", None),
            ("GET", f"/api/analyses/{uuid4()}", None),
            ("POST", "/api/analyses", {"title": "Vendas"}),
        )
        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, json=body, headers=self.headers)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(set(response.json()), {"detail"})
        self.assertEqual(self.repository.calls, [])

    def test_storage_failures_are_unavailable_and_do_not_expose_details(self):
        analysis = self.create_analysis()
        requests = (
            ("insert", "POST", "/api/analyses", {"title": "Vendas"}),
            ("get", "GET", f"/api/analyses/{analysis['id']}", None),
            ("list", "GET", "/api/analyses", None),
        )
        for operation, method, path, body in requests:
            with self.subTest(operation=operation):
                self.repository.failure = operation
                response = self.client.request(method, path, json=body, headers=self.headers)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(set(response.json()), {"detail"})
                self.assertIsInstance(response.json()["detail"], str)
                self.assertNotIn("private-mysql-password", response.text)
                self.assertNotIn("needle", response.text)
                self.assertNotIn("SELECT", response.text)
        self.assertEqual(len(self.repository.rows), 1)

    def test_read_only_routes_still_reject_untrusted_hosts(self):
        for path in ("/api/analyses", f"/api/analyses/{uuid4()}"):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Host": "example.com"})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.repository.calls, [])

    def test_read_only_routes_accept_localhost_without_csrf(self):
        for host in ("localhost", "127.0.0.1", "testserver"):
            with self.subTest(host=host):
                response = self.client.get("/api/analyses", headers={"Host": host})
                self.assertEqual(response.status_code, 200, response.text)


class AnalysisCompositionTests(unittest.TestCase):
    def test_configured_startup_never_connects_or_runs_ddl(self):
        mysql = MagicMock()
        with tempfile.TemporaryDirectory() as root:
            with patch("apps.web.api.main.MySQLConfig.is_configured", return_value=True), patch(
                "apps.web.api.main.MySQLClient.from_env", return_value=mysql
            ) as factory:
                app = create_app(FakeAuth(), data_dir=Path(root))
            factory.assert_called_once_with(config_file=Path(root) / "mysql.json")
            self.assertIsInstance(app.state.analysis, AnalysisService)
            with TestClient(app) as browser:
                self.assertEqual(browser.get("/api/bootstrap").status_code, 200)
            mysql.connection.assert_not_called()
            mysql.check_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
