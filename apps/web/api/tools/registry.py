"""Compose analysis tools using the same service as the HTTP application."""

from ..services.analysis import AnalysisService
from ..services.analytics import AnalyticsService
from .definitions import analysis_create_definition, analytics_definitions
from .executor import ToolExecutor
from .handlers.analyses import AnalysisCreateTool
from .handlers.charts import ChartCreateTool
from .handlers.datasets import (
    AnalysisGetTool, AnalysisListTool, AnalyticsAnalysisCreateTool,
    DatasetGetTool, DatasetImportDemoTool, DatasetListTool,
)


def create_tool_executor(analysis_service: AnalysisService | None, *, analytics_service: AnalyticsService | None = None) -> ToolExecutor:
    if analysis_service is None:
        return ToolExecutor({})
    name = analysis_create_definition()["name"]
    if analytics_service is None:
        return ToolExecutor({name: AnalysisCreateTool(analysis_service)})
    return ToolExecutor({
        name: AnalyticsAnalysisCreateTool(analysis_service),
        "dataset_list": DatasetListTool(analytics_service),
        "dataset_get": DatasetGetTool(analytics_service),
        "dataset_import_demo": DatasetImportDemoTool(analytics_service),
        "analysis_list": AnalysisListTool(analytics_service),
        "analysis_get": AnalysisGetTool(analytics_service),
        "chart_create": ChartCreateTool(analytics_service),
    })


def list_tool_definitions(analysis_service: AnalysisService | None, *, analytics_service: AnalyticsService | None = None) -> list[dict]:
    if analysis_service is None:
        return []
    return [analysis_create_definition(), *(analytics_definitions() if analytics_service is not None else [])]
