"""Validate tool arguments and delegate analysis creation to its use case."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...domain.analysis import AnalysisStorageError, AnalysisValidationError
from ...services.analysis import AnalysisService


class AnalysisCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)


class AnalysisCreateTool:
    def __init__(self, analysis_service: AnalysisService):
        self.analysis_service = analysis_service

    def execute(self, arguments: dict) -> dict:
        try:
            args = AnalysisCreateArgs.model_validate(arguments)
            analysis = self.analysis_service.create(title=args.title)
        except (ValidationError, AnalysisValidationError):
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_ARGUMENTS",
                    "message": "Título inválido para a análise.",
                },
            }
        except AnalysisStorageError:
            return {
                "ok": False,
                "error": {
                    "code": "STORAGE_ERROR",
                    "message": "Não foi possível salvar a análise. Tente novamente.",
                },
            }

        return {
            "ok": True,
            "analysis_id": analysis.id,
            "title": analysis.title,
        }
