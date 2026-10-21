from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pymysql

from apps.web.api.adapter.mysql.analysis_repository import MySQLAnalysisRepository
from apps.web.api.adapter.mysql.client import MySQLConnectionError
from apps.web.api.domain.analysis import Analysis, AnalysisStorageError


class AnalysisRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.connection = self.client.connection.return_value.__enter__.return_value
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.repository = MySQLAnalysisRepository(self.client)

    def row(self, analysis):
        return {**analysis.public(), "created_at": analysis.created_at.replace(tzinfo=None),
                "updated_at": analysis.updated_at.replace(tzinfo=None)}

    def test_insert_uses_parameters_and_explicit_utc(self):
        analysis = Analysis.create("Vendas'); DROP TABLE analyses; --")
        self.repository.insert(analysis)
        statement, parameters = self.cursor.execute.call_args.args
        self.assertIn("INSERT INTO analyses", statement)
        self.assertNotIn(analysis.title, statement)
        self.assertEqual(parameters[:2], (analysis.id, analysis.title))
        self.assertEqual(parameters[2:], (analysis.created_at.replace(tzinfo=None),
                                         analysis.updated_at.replace(tzinfo=None)))
        self.assertNotIn("UPDATE", statement)
        self.assertNotIn("REPLACE", statement)

    def test_get_rehydrates_utc_without_changing_id_or_dates(self):
        analysis = Analysis.create("Safra 🌱")
        self.cursor.fetchone.return_value = self.row(analysis)
        self.assertEqual(self.repository.get(analysis.id), analysis)
        self.assertEqual(self.cursor.execute.call_args.args[1], (analysis.id,))

    def test_get_missing_returns_none(self):
        self.cursor.fetchone.return_value = None
        self.assertIsNone(self.repository.get("missing"))

    def test_aware_dates_are_written_in_utc(self):
        local = datetime(2026, 10, 16, 9, 30, tzinfo=timezone(timedelta(hours=-3)))
        analysis = Analysis("00000000-0000-4000-8000-000000000000", "Vendas", local, local)
        self.repository.insert(analysis)
        parameters = self.cursor.execute.call_args.args[1]
        self.assertEqual(parameters[2], datetime(2026, 10, 16, 12, 30))

    def test_list_is_ordered_and_pagination_is_parameterized(self):
        analysis = Analysis.create("Receita")
        self.cursor.fetchall.return_value = [self.row(analysis)]
        self.assertEqual(self.repository.list(limit=10, offset=20), [analysis])
        statement, parameters = self.cursor.execute.call_args.args
        self.assertIn("ORDER BY updated_at DESC, id DESC", statement)
        self.assertEqual(parameters, (10, 20))

    def test_connection_and_sql_errors_are_controlled(self):
        for error in (MySQLConnectionError("private-host"),
                      pymysql.OperationalError(1146, "private schema/password"),
                      OSError("private path")):
            with self.subTest(error=type(error).__name__):
                self.client.connection.return_value.__enter__.side_effect = error
                for operation in (lambda: self.repository.insert(Analysis.create("X")),
                                  lambda: self.repository.get("id"),
                                  lambda: self.repository.list(limit=20, offset=0)):
                    with self.assertRaises(AnalysisStorageError) as failure:
                        operation()
                    self.assertNotIn("private", str(failure.exception))

    def test_duplicate_id_never_overwrites_existing_analysis(self):
        self.cursor.execute.side_effect = pymysql.IntegrityError(1062, "duplicate")
        with self.assertRaises(AnalysisStorageError):
            self.repository.insert(Analysis.create("Vendas"))
        self.assertEqual(self.cursor.execute.call_count, 1)

    def test_execute_failures_are_sanitized_for_reads(self):
        self.cursor.execute.side_effect = pymysql.OperationalError(1146, "private schema")
        for operation in (lambda: self.repository.get("id"),
                          lambda: self.repository.list(limit=20, offset=0)):
            with self.assertRaises(AnalysisStorageError) as failure:
                operation()
            self.assertNotIn("private", str(failure.exception))

    def test_commit_failure_is_sanitized_for_each_operation(self):
        self.cursor.fetchone.return_value = None
        self.cursor.fetchall.return_value = []
        self.client.connection.return_value.__exit__.side_effect = pymysql.OperationalError(2006, "private host")
        for operation in (lambda: self.repository.insert(Analysis.create("Vendas")),
                          lambda: self.repository.get("id"),
                          lambda: self.repository.list(limit=20, offset=0)):
            with self.assertRaises(AnalysisStorageError) as failure:
                operation()
            self.assertNotIn("private", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
