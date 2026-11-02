"""Bounded dataset discovery and analysis lookup tools."""

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...domain.analysis import AnalysisNotFound, AnalysisStorageError, AnalysisValidationError
from ...services.analytics import AnalyticsService
from .analyses import AnalysisCreateTool


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetGetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    dataset_id: str = Field(min_length=1, max_length=36)


class AnalysisGetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    analysis_id: str = Field(min_length=1, max_length=36)


class ListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2147483647)


def tool_result(operation: Callable[[], dict]) -> dict:
    try:
        return {"ok": True, **operation()}
    except ValidationError:
        return {"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message": "Confira os argumentos da ferramenta."}}
    except AnalysisValidationError as error:
        return {"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message": str(error)}}
    except AnalysisNotFound as error:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": str(error)}}
    except AnalysisStorageError:
        return {"ok": False, "error": {"code": "STORAGE_ERROR", "message": "Não foi possível acessar os dados da análise."}}


def chart_summary(chart: dict) -> dict:
    return {key: value for key, value in chart.items() if key != "spec"}


class AnalyticsAnalysisCreateTool(AnalysisCreateTool):
    def execute(self, arguments: dict) -> dict:
        result = super().execute(arguments)
        if result["ok"]:
            result["analysis_url"] = f"/analyses/{result['analysis_id']}"
        return result


class DatasetListTool:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def execute(self, arguments: dict) -> dict:
        def operation():
            args = ListArgs.model_validate(arguments)
            datasets = self.analytics.list_datasets(**args.model_dump())
            return {"datasets": [{key: value for key, value in dataset.items() if key != "preview"} for dataset in datasets]}
        return tool_result(operation)


class DatasetGetTool:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def execute(self, arguments: dict) -> dict:
        def operation():
            args = DatasetGetArgs.model_validate(arguments)
            return {"dataset": self.analytics.get_dataset(args.dataset_id)}
        return tool_result(operation)


class DatasetImportDemoTool:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def execute(self, arguments: dict) -> dict:
        def operation():
            EmptyArgs.model_validate(arguments)
            return {"dataset": self.analytics.import_demo()}
        return tool_result(operation)


class AnalysisListTool:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def execute(self, arguments: dict) -> dict:
        def operation():
            args = ListArgs.model_validate(arguments)
            return {"analyses": [
                {**analysis.public(), "analysis_url": f"/analyses/{analysis.id}"}
                for analysis in self.analytics.analysis_service.list(**args.model_dump())
            ]}
        return tool_result(operation)


class AnalysisGetTool:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def execute(self, arguments: dict) -> dict:
        def operation():
            args = AnalysisGetArgs.model_validate(arguments)
            analysis = self.analytics.get_analysis(args.analysis_id)
            analysis["charts"] = [chart_summary(chart) for chart in analysis["charts"]]
            return {"analysis": analysis, "analysis_url": f"/analyses/{analysis['id']}"}
        return tool_result(operation)
