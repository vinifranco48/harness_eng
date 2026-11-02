from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from openpyxl import Workbook

from apps.web.api.domain.auth import AuthState, AuthStatus, Provider
from apps.web.api.main import create_app


class FakeAuth:
    auth_home = None

    def status(self, provider):
        return AuthStatus(Provider(provider), AuthState.DISCONNECTED, "Status de teste.")

    def environment(self, provider):
        return {"PATH": os.defpath, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows")}


class AnalyticsApiTests(unittest.TestCase):
    """Exercise real HTTP parsing, composition and SQLite persistence offline."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name)
        self.client = None
        self.addCleanup(self.close_client)
        self.open_client()

    def open_client(self):
        with patch("apps.web.api.main.MySQLClient.from_env") as mysql_factory:
            self.app = create_app(FakeAuth(), data_dir=self.data_dir, analysis_storage="sqlite")
        mysql_factory.assert_not_called()
        self.client = TestClient(self.app)
        self.client.__enter__()
        token = self.client.get("/api/bootstrap").json()["csrf_token"]
        self.headers = {"X-Harness-CSRF": token, "Origin": "http://testserver"}

    def close_client(self):
        if self.client is not None:
            self.client.__exit__(None, None, None)
            self.client = None

    def upload(self, content, filename="vendas.csv", name=None):
        response = self.client.post(
            "/api/datasets", files={"file": (filename, content)},
            data={} if name is None else {"name": name}, headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["dataset"]

    def analysis(self, title="Receita comercial"):
        response = self.client.post("/api/analyses", json={"title": title}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["analysis"]

    def chart_body(self, dataset_id, **changes):
        return {"dataset_id": dataset_id, "title": "Receita por região", "mark": "bar",
                "x": "regiao", "y": "receita", "aggregate": "sum", **changes}

    def create_chart(self, analysis_id, body):
        response = self.client.post(f"/api/analyses/{analysis_id}/charts", json=body, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["chart"]

    def demo(self):
        response = self.client.post("/api/datasets/demo", headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["dataset"]

    def test_csv_upload_returns_typed_profile_and_bounded_preview(self):
        dataset = self.upload(
            b"mes;regiao;receita\n2025-01-01;Norte;12,50\n2025-02-01;Sul;0\n",
            name="  Receita mensal  ",
        )
        self.assertEqual(dataset["name"], "Receita mensal")
        self.assertEqual(dataset["row_count"], 2)
        self.assertEqual(str(UUID(dataset["id"])), dataset["id"])
        self.assertEqual(dataset["columns"], [
            {"name": "mes", "type": "temporal"},
            {"name": "regiao", "type": "nominal"},
            {"name": "receita", "type": "quantitative"},
        ])
        self.assertEqual(dataset["preview"], [
            {"mes": "2025-01-01", "regiao": "Norte", "receita": 12.5},
            {"mes": "2025-02-01", "regiao": "Sul", "receita": 0},
        ])
        self.assertNotIn("rows", dataset)
        response = self.client.get(f"/api/datasets/{dataset['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["dataset"], dataset)

    def test_xlsx_upload_preserves_native_dates_numbers_and_missing_values(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Dados"
        sheet.append(["mes", "regiao", "receita"])
        sheet.append([datetime(2025, 1, 1), "Norte", 10.25])
        sheet.append([datetime(2025, 2, 1), "Sul", 0])
        sheet.append([datetime(2025, 3, 1), "Norte", None])
        workbook.create_sheet("Sobre").append(["Não importar esta aba."])
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()

        dataset = self.upload(content.getvalue(), "receita.xlsx")

        self.assertEqual(dataset["row_count"], 3)
        self.assertEqual(dataset["columns"][0]["type"], "temporal")
        self.assertEqual(dataset["columns"][2]["type"], "quantitative")
        self.assertEqual(datetime.fromisoformat(dataset["preview"][0]["mes"]), datetime(2025, 1, 1))
        self.assertEqual([row["receita"] for row in dataset["preview"]], [10.25, 0, None])

    def test_demo_import_is_repeatable_and_chart_uses_all_144_rows(self):
        dataset = self.demo()
        repeated = self.demo()
        self.assertEqual(repeated["id"], dataset["id"])
        self.assertEqual(dataset["row_count"], 144)
        self.assertEqual(len(dataset["preview"]), 20)
        self.assertEqual(len(self.client.get("/api/datasets").json()["datasets"]), 1)
        analysis = self.analysis()
        chart = self.create_chart(analysis["id"], self.chart_body(dataset["id"]))
        self.assertEqual(chart["analysis_id"], analysis["id"])
        self.assertEqual(chart["dataset_id"], dataset["id"])
        self.assertEqual(chart["spec"]["encoding"]["y"]["aggregate"], "sum")
        values = chart["spec"]["data"]["values"]
        self.assertEqual(len(values), 144)
        expected_path = Path(__file__).resolve().parents[1] / "apps/web/api/data/vendas_demo_totals.json"
        expected = json.loads(expected_path.read_text(), parse_float=Decimal)
        self.assertEqual(sum(Decimal(str(row["receita"])) for row in values), expected["receita"])
        for region, totals in expected["by_region"].items():
            with self.subTest(region=region):
                total = sum(Decimal(str(row["receita"])) for row in values if row["regiao"] == region)
                self.assertEqual(total, totals["receita"])

    def test_xlsx_formula_without_a_calculated_value_is_not_imported_as_zero(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["regiao", "receita"])
        sheet.append(["Norte", "=10+20"])
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()

        response = self.client.post(
            "/api/datasets", files={"file": ("formula.xlsx", content.getvalue())}, headers=self.headers,
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/datasets").json()["datasets"], [])

    def test_analysis_dataset_and_chart_survive_application_restart(self):
        dataset = self.upload(b"regiao,receita\nNorte,20\nSul,30\n")
        analysis = self.analysis()
        chart = self.create_chart(analysis["id"], self.chart_body(dataset["id"]))

        self.close_client()
        self.open_client()

        self.assertEqual(self.client.get(f"/api/analyses/{analysis['id']}").json()["analysis"], analysis)
        self.assertEqual(self.client.get(f"/api/datasets/{dataset['id']}").json()["dataset"], dataset)
        self.assertEqual(self.client.get(f"/api/charts/{chart['id']}").json()["chart"], chart)
        self.assertEqual(self.client.get(f"/api/analyses/{analysis['id']}/charts").json()["charts"], [chart])

    def test_chart_preserves_real_zero_and_excludes_missing_axis_values(self):
        dataset = self.upload(b"regiao,receita\nNorte,0\nSul,\n,12\nSudeste,30\n")
        analysis = self.analysis()
        chart = self.create_chart(analysis["id"], self.chart_body(dataset["id"]))
        self.assertEqual(chart["spec"]["data"]["values"], [
            {"regiao": "Norte", "receita": 0}, {"regiao": "Sudeste", "receita": 30},
        ])

    def test_invalid_headers_and_corrupt_files_do_not_create_datasets(self):
        for filename, content in (
            ("vendas.csv", b"regiao,regiao\nNorte,Sul\n"),
            ("vendas.csv", b"regiao,\nNorte,10\n"),
            ("vendas.csv", b"regiao,receita\nNorte,10,extra\n"),
            ("vendas.csv", b""),
            ("vendas.csv", b"regiao,receita\nNorte,NaN\n"),
            ("vendas.xlsx", b"not a workbook"),
            ("vendas.exe", b"regiao,receita\nNorte,10\n"),
        ):
            with self.subTest(filename=filename, content=content):
                response = self.client.post("/api/datasets", files={"file": (filename, content)}, headers=self.headers)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/datasets").json()["datasets"], [])

    def test_upload_over_five_mebibytes_is_rejected_without_persistence(self):
        response = self.client.post(
            "/api/datasets", files={"file": ("large.csv", b"x" * (5 * 1024 * 1024 + 1))}, headers=self.headers,
        )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.client.get("/api/datasets").json()["datasets"], [])

    def test_dataset_and_chart_mutations_require_csrf_and_same_origin(self):
        dataset = self.demo()
        analysis = self.analysis()
        for headers in ({}, {**self.headers, "Origin": "https://example.com"}):
            for endpoint in ("/api/datasets", "/api/datasets/demo", f"/api/analyses/{analysis['id']}/charts"):
                with self.subTest(headers=headers, endpoint=endpoint):
                    options = {"files": {"file": ("vendas.csv", b"regiao,receita\nNorte,10\n")}} if endpoint == "/api/datasets" else {"json": self.chart_body(dataset["id"])}
                    response = self.client.post(endpoint, headers=headers, **options)
                    self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get(f"/api/analyses/{analysis['id']}/charts").json()["charts"], [])
        self.assertEqual(len(self.client.get("/api/datasets").json()["datasets"]), 1)

    def test_invalid_chart_fields_and_external_specs_are_rejected(self):
        dataset = self.demo()
        analysis = self.analysis()
        for changes in (
            {"x": "coluna_inexistente"}, {"y": "produto"}, {"mark": "script"},
            {"aggregate": "execute"}, {"mark": "line", "x": "regiao"},
            {"spec": {"data": {"url": "https://example.com/private.csv"}}},
        ):
            with self.subTest(changes=changes):
                response = self.client.post(
                    f"/api/analyses/{analysis['id']}/charts",
                    json=self.chart_body(dataset["id"], **changes), headers=self.headers,
                )
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(f"/api/analyses/{analysis['id']}/charts").json()["charts"], [])

    def test_unknown_references_return_not_found_without_creating_charts(self):
        dataset = self.demo()
        analysis = self.analysis()
        for analysis_id, dataset_id in ((str(uuid4()), dataset["id"]), (analysis["id"], str(uuid4()))):
            response = self.client.post(
                f"/api/analyses/{analysis_id}/charts", json=self.chart_body(dataset_id), headers=self.headers,
            )
            self.assertEqual(response.status_code, 404, response.text)
        for endpoint in (f"/api/datasets/{uuid4()}", f"/api/charts/{uuid4()}", f"/api/analyses/{uuid4()}/charts"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.get(f"/api/analyses/{analysis['id']}/charts").json()["charts"], [])

    def test_dataset_pagination_and_invalid_identifiers(self):
        first = self.upload(b"regiao,receita\nNorte,10\n")
        second = self.upload(b"regiao,receita\nSul,20\n")
        response = self.client.get("/api/datasets?limit=1&offset=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["datasets"], [first])
        self.assertNotEqual(first["id"], second["id"])
        for endpoint in ("/api/datasets?limit=0", "/api/datasets?offset=-1", "/api/datasets/invalid", "/api/charts/invalid"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 422)

    def test_analytics_page_and_chart_scripts_are_served_locally(self):
        page = self.client.get("/analyses")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("/static/analyses.js", page.text)
        self.assertEqual(self.client.get("/static/analyses.js").status_code, 200)
        self.assertEqual(self.client.get("/static/analyses.css").status_code, 200)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
