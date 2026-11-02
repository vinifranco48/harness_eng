import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from apps.web.api.adapter.sqlite.analytics_repository import AnalyticsRepository, SQLiteAnalysisRepository
from apps.web.api.domain.analysis import AnalysisNotFound, AnalysisValidationError
from apps.web.api.services.analysis import AnalysisService
from apps.web.api.services.analytics import MAX_FILE_BYTES, AnalyticsService
from apps.web.api.tools.registry import create_tool_executor, list_tool_definitions


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "analytics.sqlite3"
        self.analyses = AnalysisService(SQLiteAnalysisRepository(self.path))
        self.repository = AnalyticsRepository(self.path)
        self.analytics = AnalyticsService(self.analyses, self.repository)
        self.executor = create_tool_executor(self.analyses, analytics_service=self.analytics)

    def source(self):
        return self.analytics.import_dataset(
            b"mes,regiao,receita\n2025-01-01,Norte,100.50\n2025-02-01,Norte,0\n2025-01-01,Sul,250.25\n",
            "vendas.csv",
        )

    def chart(self, **changes):
        source = self.source()
        analysis = self.analyses.create("Receitas")
        arguments = {"dataset_id": source["id"], "title": "Receita por região", "x": "regiao", "y": "receita"}
        arguments.update(changes)
        return self.analytics.create_chart(analysis.id, arguments)

    def test_import_infers_types_and_preserves_zero(self):
        dataset = self.source()
        self.assertEqual(dataset["row_count"], 3)
        self.assertEqual(dataset["columns"], [
            {"name": "mes", "type": "temporal"},
            {"name": "regiao", "type": "nominal"},
            {"name": "receita", "type": "quantitative"},
        ])
        self.assertEqual(dataset["preview"][1]["receita"], 0)
        self.assertNotIn("rows", dataset)

    def test_csv_bom_semicolon_and_decimal_comma_are_supported(self):
        content = "\ufeffregiao;receita\nNorte;12,50\nSul;-2,25\n".encode("utf-8")
        dataset = self.analytics.import_dataset(content, "dados.CSV")
        self.assertEqual([row["receita"] for row in dataset["preview"]], [12.5, -2.25])

    def test_numeric_identifiers_with_leading_zero_remain_text(self):
        dataset = self.analytics.import_dataset(b"codigo,total\n00123,12\n00456,15\n", "dados.csv")
        self.assertEqual(dataset["columns"][0]["type"], "nominal")
        self.assertEqual(dataset["preview"][0]["codigo"], "00123")

    def test_dates_and_unzoned_timestamps_have_consistent_utc_semantics(self):
        dataset = self.analytics.import_dataset(
            b"mes,receita\n2025-01-01,1\n2025-01-01T00:00:00,2\n2025-01-01T15:30:00,3\n2025-01-01T12:30:00-03:00,4\n",
            "horarios.csv",
        )
        self.assertEqual([row["mes"] for row in dataset["preview"]], [
            "2025-01-01", "2025-01-01", "2025-01-01T15:30:00Z", "2025-01-01T15:30:00Z",
        ])

    def test_invalid_import_does_not_write_any_data(self):
        for content, filename in (
            (b"", "empty.csv"), (b"x,x\n1,2\n", "duplicate.csv"),
            (b" ,y\n1,2\n", "blank.csv"), (b"x,y\n1\n", "short.csv"),
            (b"x,y\n1,2,3\n", "long.csv"), (b"x,y\n", "empty-data.csv"),
            (b"x,y\nNorte,NaN\n", "nan.csv"), (b"x,y\nNorte,Infinity\n", "infinite.csv"),
            (b"a.b,total\nNorte,1\n", "path.csv"), (b"__proto__,total\nNorte,1\n", "prototype.csv"),
            (b"\xff", "encoding.csv"), (b"garbage", "data.xls"), (b"garbage", "data.xlsx"),
            (b"x,y\nNorte,1000000000000001\n", "too-large-number.csv"),
        ):
            with self.subTest(filename=filename):
                with self.assertRaises(AnalysisValidationError):
                    self.analytics.import_dataset(content, filename)
        self.assertEqual(self.analytics.list_datasets(), [])

    def test_file_row_column_and_cell_limits_are_enforced(self):
        payloads = (
            b"a" * (MAX_FILE_BYTES + 1),
            ("x,y\n" + "a,1\n" * 5001).encode(),
            (",".join(f"c{i}" for i in range(51)) + "\n" + ",".join("1" for _ in range(51))).encode(),
            ("x,y\n" + "a" * 2001 + ",1\n").encode(),
        )
        for content in payloads:
            with self.subTest(size=len(content)):
                with self.assertRaises(AnalysisValidationError):
                    self.analytics.import_dataset(content, "dados.csv")
        self.assertEqual(self.analytics.list_datasets(), [])

    def test_import_is_idempotent_even_when_requests_overlap(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            datasets = list(pool.map(lambda _: self.source(), range(8)))
        self.assertEqual(len({item["id"] for item in datasets}), 1)
        self.assertEqual(len(self.analytics.list_datasets()), 1)

    def test_demo_matches_independent_source_controls_and_is_repeatable(self):
        dataset = self.analytics.import_demo()
        self.assertEqual(dataset["id"], self.analytics.import_demo()["id"])
        rows = self.repository.get_dataset(dataset["id"])["rows"]
        self.assertEqual(len(rows), 144)
        self.assertEqual(len({row["mes"] for row in rows}), 12)
        self.assertEqual(sum(Decimal(str(row["receita"])) for row in rows), Decimal("5174501.20"))
        self.assertEqual(sum(Decimal(str(row["custo"])) for row in rows), Decimal("2868766.21"))
        self.assertEqual(sum(row["unidades"] for row in rows), 13452)
        self.assertEqual(len(dataset["preview"]), 20)

    def test_saved_chart_contains_only_local_data_and_expected_aggregate(self):
        chart = self.chart()
        spec = chart["spec"]
        self.assertEqual(spec["$schema"], "https://vega.github.io/schema/vega-lite/v6.json")
        self.assertEqual(spec["encoding"]["y"]["aggregate"], "sum")
        self.assertEqual(spec["encoding"]["y"]["field"], "receita")
        self.assertEqual(spec["encoding"]["y"]["title"], "Soma de receita")
        self.assertEqual(len(spec["data"]["values"]), 3)
        self.assertNotIn("url", spec["data"])
        self.assertNotIn("transform", spec)
        self.assertNotIn("expr", json.dumps(spec))
        expected = {"Norte": Decimal("100.50"), "Sul": Decimal("250.25")}
        for region, total in expected.items():
            actual = sum(Decimal(str(row["receita"])) for row in spec["data"]["values"] if row["regiao"] == region)
            self.assertEqual(actual, total)

    def test_missing_values_are_excluded_and_zero_is_a_real_observation(self):
        dataset = self.analytics.import_dataset(b"regiao,receita\nNorte,0\nNorte,\n,40\nSul,50\n", "faltantes.csv")
        analysis = self.analyses.create("Ausências")
        chart = self.analytics.create_chart(analysis.id, {
            "dataset_id": dataset["id"], "title": "Valores", "x": "regiao", "y": "receita", "aggregate": "count",
        })
        self.assertEqual(chart["spec"]["data"]["values"], [{"regiao": "Norte", "receita": 0}, {"regiao": "Sul", "receita": 50}])

    def test_line_and_area_require_ordered_x_and_color_categories(self):
        for mark in ("line", "area"):
            with self.subTest(mark=mark):
                chart = self.chart(mark=mark, x="mes", color="regiao")
                self.assertEqual(chart["spec"]["encoding"]["x"]["type"], "temporal")
                self.assertEqual(chart["spec"]["encoding"]["x"]["scale"], {"type": "utc"})
                self.assertNotIn("timeUnit", chart["spec"]["encoding"]["x"])
                self.assertNotIn("tooltip", chart["spec"]["encoding"])
                self.assertIs(chart["spec"]["mark"]["tooltip"], True)
                self.assertEqual(chart["spec"]["encoding"]["color"]["field"], "regiao")
                with self.assertRaises(AnalysisValidationError):
                    self.chart(mark=mark)

    def test_invalid_chart_arguments_and_references_do_not_save_charts(self):
        dataset = self.source()
        analysis = self.analyses.create("Validação")
        base = {"dataset_id": dataset["id"], "title": "Vendas", "x": "regiao", "y": "receita"}
        invalid = (
            {"x": "inexistente"}, {"y": "regiao"}, {"color": "receita"},
            {"color": "regiao"}, {"mark": "image"}, {"aggregate": "eval"},
            {"url": "https://example.com/private.csv"}, {"spec": {"data": {"url": "https://example.com"}}},
            {"title": "\ud800"}, {"dataset_id": "invalid"}, {"title": ""},
        )
        for changes in invalid:
            with self.subTest(changes=repr(changes)):
                with self.assertRaises((AnalysisValidationError, ValidationError)):
                    self.analytics.create_chart(analysis.id, {**base, **changes})
        with self.assertRaises(AnalysisNotFound):
            self.analytics.create_chart(str(uuid4()), base)
        with self.assertRaises(AnalysisNotFound):
            self.analytics.create_chart(analysis.id, {**base, "dataset_id": str(uuid4())})
        self.assertEqual(self.analytics.list_charts(analysis.id), [])

    def test_analyses_data_and_chart_snapshots_survive_new_repository_instances(self):
        chart = self.chart()
        reloaded_analyses = AnalysisService(SQLiteAnalysisRepository(self.path))
        reloaded = AnalyticsService(reloaded_analyses, AnalyticsRepository(self.path))
        self.assertEqual(reloaded.get_chart(chart["id"]), chart)
        self.assertEqual(reloaded.get_analysis(chart["analysis_id"])["charts"], [chart])
        self.assertEqual(reloaded.get_dataset(chart["dataset_id"])["row_count"], 3)
        chart["spec"]["data"]["values"].clear()
        self.assertEqual(len(reloaded.get_chart(chart["id"])["spec"]["data"]["values"]), 3)

    def test_registered_tool_flow_creates_an_analysis_and_chart(self):
        names = {item["name"] for item in list_tool_definitions(self.analyses, analytics_service=self.analytics)}
        self.assertEqual(names, {"analysis_create", "analysis_list", "analysis_get", "dataset_import_demo", "dataset_list", "dataset_get", "chart_create"})
        imported = self.executor.execute("dataset_import_demo", {})
        self.assertTrue(imported["ok"])
        created = self.executor.execute("analysis_create", {"title": "Receita comercial"})
        self.assertTrue(created["ok"])
        result = self.executor.execute("chart_create", {
            "analysis_id": created["analysis_id"], "dataset_id": imported["dataset"]["id"],
            "title": "Receita por região", "x": "regiao", "y": "receita",
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["analysis_url"], created["analysis_url"])
        self.assertNotIn("spec", result["chart"])
        detail = self.executor.execute("analysis_get", {"analysis_id": created["analysis_id"]})
        self.assertEqual(detail["analysis"]["charts"][0]["id"], result["chart_id"])
        self.assertNotIn("spec", detail["analysis"]["charts"][0])
        self.assertEqual(len(self.analytics.get_chart(result["chart_id"])["spec"]["data"]["values"]), 144)

    def test_tools_reject_invalid_arguments_and_unknown_ids(self):
        for name, arguments in (
            ("dataset_import_demo", {"url": "https://example.com"}),
            ("dataset_list", {"limit": True}), ("dataset_get", {"dataset_id": "bad"}),
            ("analysis_list", {"offset": -1}), ("chart_create", {"title": "Missing inputs"}),
        ):
            with self.subTest(name=name):
                result = self.executor.execute(name, arguments)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")
        result = self.executor.execute("dataset_get", {"dataset_id": str(uuid4())})
        self.assertEqual(result["error"]["code"], "NOT_FOUND")
        self.assertEqual(self.analytics.list_datasets(), [])

    def test_storage_error_is_sanitized_by_tool(self):
        with patch("apps.web.api.adapter.sqlite.analytics_repository.sqlite3.connect", side_effect=sqlite3.OperationalError("private path and password")):
            result = self.executor.execute("dataset_list", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STORAGE_ERROR")
        self.assertNotIn("private", json.dumps(result))

    def test_dataset_list_is_paginated_and_tool_catalog_omits_rows(self):
        for number in range(3):
            self.analytics.import_dataset(f"item,value\na,{number}\n".encode(), f"{number}.csv")
        full = self.analytics.list_datasets()
        self.assertEqual(self.analytics.list_datasets(limit=1, offset=1), full[1:2])
        result = self.executor.execute("dataset_list", {"limit": 1, "offset": 1})
        self.assertTrue(result["ok"])
        self.assertEqual(result["datasets"][0]["id"], full[1]["id"])
        self.assertNotIn("preview", result["datasets"][0])


if __name__ == "__main__":
    unittest.main()
