"""Analysis use cases, independent of HTTP, SQL and chat state."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ..domain.analysis import Analysis, AnalysisNotFound, AnalysisValidationError


class AnalysisRepository(Protocol):
    def insert(self, analysis: Analysis) -> None: ...

    def get(self, analysis_id: str) -> Analysis | None: ...

    def list(self, *, limit: int, offset: int) -> list[Analysis]: ...


class AnalysisService:
    def __init__(self, repository: AnalysisRepository) -> None:
        self.repository = repository

    def create(self, title: str) -> Analysis:
        analysis = Analysis.create(title)
        self.repository.insert(analysis)
        return analysis

    def get(self, analysis_id: str) -> Analysis:
        if not isinstance(analysis_id, str):
            raise AnalysisValidationError("Informe um identificador UUID válido para a análise.")
        try:
            normalized_id = str(UUID(analysis_id.strip()))
        except ValueError:
            raise AnalysisValidationError("Informe um identificador UUID válido para a análise.") from None
        analysis = self.repository.get(normalized_id)
        if analysis is None:
            raise AnalysisNotFound("Esta análise não foi encontrada.")
        return analysis

    def list(self, *, limit: int = 20, offset: int = 0) -> list[Analysis]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise AnalysisValidationError("O limite deve ser um inteiro entre 1 e 100.")
        if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 2_147_483_647:
            raise AnalysisValidationError("O offset deve ser um inteiro entre 0 e 2147483647.")
        return self.repository.list(limit=limit, offset=offset)
