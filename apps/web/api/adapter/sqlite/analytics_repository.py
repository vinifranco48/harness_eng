"""Persist local analyses, imported data and immutable chart specifications."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ...domain.analysis import Analysis, AnalysisStorageError


class AnalyticsRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS analyses (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS datasets (
                        id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL, filename TEXT NOT NULL,
                        columns_json TEXT NOT NULL, rows_json TEXT NOT NULL,
                        row_count INTEGER NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS charts (
                        id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL,
                        dataset_id TEXT NOT NULL REFERENCES datasets(id),
                        title TEXT NOT NULL, spec_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS charts_analysis
                        ON charts (analysis_id, created_at, id);
                """)
        except OSError:
            raise AnalysisStorageError("Não foi possível preparar o armazenamento local.") from None

    @contextmanager
    def connection(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        except sqlite3.Error:
            raise AnalysisStorageError("Não foi possível acessar os dados da análise.") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _dataset(row) -> dict:
        return {
            "id": row["id"], "name": row["name"], "filename": row["filename"],
            "columns": json.loads(row["columns_json"]), "rows": json.loads(row["rows_json"]),
            "row_count": row["row_count"], "created_at": row["created_at"],
        }

    def insert_dataset(self, dataset: dict, digest: str) -> dict:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(digest) DO NOTHING",
                (dataset["id"], digest, dataset["name"], dataset["filename"],
                 json.dumps(dataset["columns"], ensure_ascii=False),
                 json.dumps(dataset["rows"], ensure_ascii=False, allow_nan=False),
                 dataset["row_count"], dataset["created_at"]),
            )
            row = connection.execute("SELECT * FROM datasets WHERE digest = ?", (digest,)).fetchone()
            return self._dataset(row)

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
            return self._dataset(row) if row else None

    def list_datasets(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM datasets ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            return [self._dataset(row) for row in rows]

    @staticmethod
    def _chart(row) -> dict:
        return {
            "id": row["id"], "analysis_id": row["analysis_id"], "dataset_id": row["dataset_id"],
            "title": row["title"], "spec": json.loads(row["spec_json"]), "created_at": row["created_at"],
        }

    def insert_chart(self, chart: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO charts VALUES (?, ?, ?, ?, ?, ?)",
                (chart["id"], chart["analysis_id"], chart["dataset_id"], chart["title"],
                 json.dumps(chart["spec"], ensure_ascii=False, allow_nan=False), chart["created_at"]),
            )

    def get_chart(self, chart_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM charts WHERE id = ?", (chart_id,)).fetchone()
            return self._chart(row) if row else None

    def list_charts(self, analysis_id: str) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM charts WHERE analysis_id = ? ORDER BY created_at, id", (analysis_id,)
            ).fetchall()
            return [self._chart(row) for row in rows]


class SQLiteAnalysisRepository(AnalyticsRepository):
    @staticmethod
    def _analysis(row) -> Analysis:
        return Analysis(
            id=row["id"], title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def insert(self, analysis: Analysis) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO analyses VALUES (?, ?, ?, ?)",
                (analysis.id, analysis.title, analysis.created_at.isoformat(), analysis.updated_at.isoformat()),
            )

    def get(self, analysis_id: str) -> Analysis | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            return self._analysis(row) if row else None

    def list(self, *, limit: int, offset: int) -> list[Analysis]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM analyses ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            return [self._analysis(row) for row in rows]
