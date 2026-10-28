from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AnalysisCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)

class AnalysisCreateTool:
    def __init__(self, analysis_service):
        self.analysis_service = analysis_service

    def execute(self, arguments: dict) -> dict:
        try:
            args = AnalysisCreateArgs.model_validate(arguments)
        except ValidationError:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_ARGUMENTS",
                    "message": "Título inválido para a análise.",
                },
            }

        analysis = self.analysis_service.create(
            title=args.title
        )

        return {
            "ok": True,
            "analysis_id": analysis.id,
            "title": analysis.title,
        }
