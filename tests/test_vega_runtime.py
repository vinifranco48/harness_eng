"""Validate computed marks using the shipped Vega runtime, offline and in Belém."""

import csv
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from apps.web.api.adapter.sqlite.analytics_repository import AnalyticsRepository, SQLiteAnalysisRepository
from apps.web.api.services.analysis import AnalysisService
from apps.web.api.services.analytics import AnalyticsService


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js é necessário para validar o runtime Vega local.")
class VegaRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        database = self.directory / "analytics.sqlite3"
        self.analysis = AnalysisService(SQLiteAnalysisRepository(database))
        self.analytics = AnalyticsService(self.analysis, AnalyticsRepository(database))
        self.analysis_id = self.analysis.create("Validação Vega").id

    def run_vega(self, charts, *, totals=None):
        input_path = self.directory / "charts.json"
        input_path.write_text(json.dumps(charts, ensure_ascii=False), encoding="utf-8")
        command = [NODE, str(ROOT / "scripts/validate_vega.mjs"), str(input_path)]
        if totals:
            command.append(str(totals))
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "TZ": "America/Belem"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def create_chart(self, dataset_id, mark, x, aggregate="sum", color=None, y="receita"):
        return self.analytics.create_chart(self.analysis_id, {
            "dataset_id": dataset_id, "title": f"{mark}: {aggregate} {y} / {x}",
            "mark": mark, "x": x, "y": y, "aggregate": aggregate, "color": color,
        })

    @staticmethod
    def expected(rows, x, aggregate, color=None, y="receita"):
        groups = defaultdict(list)
        for row in rows:
            if row[x] in (None, "") or row[y] in (None, ""):
                continue
            key = (row[x], row[color]) if color else (row[x],)
            groups[key].append(Decimal(str(row[y])))
        result = []
        for key, values in groups.items():
            value = {"sum": lambda: sum(values), "mean": lambda: sum(values) / len(values),
                     "count": lambda: len(values), "min": lambda: min(values), "max": lambda: max(values)}[aggregate]()
            result.append({"key": list(key), "value": float(value)})
        return result

    def test_all_marks_and_aggregates_match_independent_csv_values(self):
        fixture = ROOT / "apps/web/api/data/vendas_demo.csv"
        totals_path = ROOT / "apps/web/api/data/vendas_demo_totals.json"
        with fixture.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        totals = json.loads(totals_path.read_text(), parse_float=Decimal)
        self.assertEqual(sum(Decimal(row["receita"]) for row in rows), totals["receita"])
        self.assertEqual(len(rows), totals["row_count"])
        dataset = self.analytics.import_demo()
        charts = []
        for mark, x in (("bar", "regiao"), ("point", "produto"), ("line", "mes"), ("area", "mes")):
            for aggregate in ("sum", "mean", "count", "min", "max"):
                chart = self.create_chart(dataset["id"], mark, x, aggregate)
                chart["validation"] = {"expected": self.expected(rows, x, aggregate)}
                charts.append(chart)
        report = self.run_vega(charts, totals=totals_path)
        self.assertEqual(report["timezone"], "America/Belem")
        self.assertEqual(report["count"], 20)
        self.assertEqual({chart["mark"] for chart in report["charts"]}, {"bar", "line", "area", "point"})
        self.assertTrue(all(chart["checkedValues"] and chart["renderedSVG"] for chart in report["charts"]))
        self.assertEqual(sum(chart["utc"] for chart in report["charts"]), 10)

    def test_color_groups_and_stacked_area_preserve_each_series(self):
        fixture = ROOT / "apps/web/api/data/vendas_demo.csv"
        with fixture.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        dataset = self.analytics.import_demo()
        charts = []
        for mark in ("bar", "line", "area", "point"):
            chart = self.create_chart(dataset["id"], mark, "mes", color="regiao", y="custo")
            chart["validation"] = {"expected": self.expected(rows, "mes", "sum", color="regiao", y="custo")}
            charts.append(chart)
        report = self.run_vega(charts)
        self.assertTrue(all(chart["groups"] == 48 for chart in report["charts"]))

    def test_zero_missing_and_negative_values_do_not_change_means_or_counts(self):
        content = b"regiao,receita\nNorte,0\nNorte,10\nNorte,\nSul,-5\nSul,15\n,99\n"
        dataset = self.analytics.import_dataset(content, "ausencias.csv")
        rows = list(csv.DictReader(content.decode().splitlines()))
        charts = []
        for aggregate in ("sum", "mean", "count", "min", "max"):
            chart = self.create_chart(dataset["id"], "bar", "regiao", aggregate)
            chart["validation"] = {"expected": self.expected(rows, "regiao", aggregate)}
            charts.append(chart)
        report = self.run_vega(charts)
        self.assertEqual(report["count"], 5)
        self.assertTrue(all(chart["groups"] == 2 for chart in report["charts"]))

    def test_calendar_dates_keep_their_day_and_month_in_belem(self):
        dataset = self.analytics.import_dataset(
            b"mes,receita\n2025-01-01,10\n2025-02-01,20\n", "calendario.csv")
        chart = self.create_chart(dataset["id"], "line", "mes")
        chart["validation"] = {"expected": [
            {"key": ["2025-01-01T00:00:00Z"], "value": 10},
            {"key": ["2025-02-01T00:00:00Z"], "value": 20},
        ]}
        report = self.run_vega([chart])
        self.assertTrue(report["charts"][0]["utc"])
        self.assertEqual(report["charts"][0]["groups"], 2)


if __name__ == "__main__":
    unittest.main()
