"""Translate chart tool arguments into the analytical use case."""

from pydantic import Field

from ...services.analytics import AnalyticsService, ChartCreateArgs
from .datasets import chart_summary, tool_result


class ChartToolArgs(ChartCreateArgs):
    analysis_id: str = Field(min_length=1, max_length=36)


class ChartCreateTool:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics

    def execute(self, arguments: dict) -> dict:
        def operation():
            args = ChartToolArgs.model_validate(arguments)
            chart = self.analytics.create_chart(args.analysis_id, args.model_dump(exclude={"analysis_id"}))
            return {
                "chart": chart_summary(chart), "chart_id": chart["id"],
                "analysis_url": f"/analyses/{chart['analysis_id']}",
            }
        return tool_result(operation)
