"""An analysis exists independently of conversations and provider accounts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


class AnalysisValidationError(ValueError):
    """An analysis input violates its domain constraints."""


class AnalysisNotFound(LookupError):
    """The requested analysis does not exist."""


class AnalysisStorageError(RuntimeError):
    """An analysis could not be read from or saved to its repository."""


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AnalysisValidationError("As datas da análise devem informar o fuso horário.")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Analysis:
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(cls, title: str) -> Analysis:
        if not isinstance(title, str):
            raise AnalysisValidationError("O título da análise deve ser um texto.")
        title = title.strip()
        if not 1 <= len(title) <= 120:
            raise AnalysisValidationError("O título da análise deve conter entre 1 e 120 caracteres.")
        try:
            title.encode("utf-8")
        except UnicodeEncodeError:
            raise AnalysisValidationError("O título da análise deve conter texto Unicode válido.") from None
        now = datetime.now(UTC)
        return cls(id=str(uuid4()), title=title, created_at=now, updated_at=now)

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": _utc_iso(self.created_at),
            "updated_at": _utc_iso(self.updated_at),
        }
