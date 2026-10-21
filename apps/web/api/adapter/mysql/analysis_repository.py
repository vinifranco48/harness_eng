"""Parameterized SQL persistence for analyses, independent of chat history."""

from datetime import datetime, timezone
from typing import Any

import pymysql

from ...domain.analysis import Analysis, AnalysisStorageError
from .client import MySQLClient, MySQLError


def _utc(value: datetime) -> datetime:
    # MariaDB DATETIME has no timezone; this adapter writes and reads UTC.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _analysis(row: dict[str, Any]) -> Analysis:
    return Analysis(
        id=row["id"], title=row["title"],
        created_at=_utc(row["created_at"]), updated_at=_utc(row["updated_at"]),
    )


class MySQLAnalysisRepository:
    def __init__(self, client: MySQLClient) -> None:
        self.client = client

    def insert(self, analysis: Analysis) -> None:
        try:
            with self.client.connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO analyses (id, title, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                    (analysis.id, analysis.title,
                     _utc(analysis.created_at).replace(tzinfo=None),
                     _utc(analysis.updated_at).replace(tzinfo=None)),
                )
        except (MySQLError, pymysql.MySQLError, OSError):
            raise AnalysisStorageError("Não foi possível salvar a análise.") from None

    def get(self, analysis_id: str) -> Analysis | None:
        try:
            with self.client.connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, title, created_at, updated_at FROM analyses WHERE id = %s",
                    (analysis_id,),
                )
                row = cursor.fetchone()
                return _analysis(row) if row is not None else None
        except (MySQLError, pymysql.MySQLError, OSError):
            raise AnalysisStorageError("Não foi possível consultar a análise.") from None

    def list(self, *, limit: int, offset: int) -> list[Analysis]:
        try:
            with self.client.connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, title, created_at, updated_at FROM analyses "
                    "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                return [_analysis(row) for row in cursor.fetchall()]
        except (MySQLError, pymysql.MySQLError, OSError):
            raise AnalysisStorageError("Não foi possível listar as análises.") from None
