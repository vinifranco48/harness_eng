import unittest
from types import SimpleNamespace

from apps.web.api.tools.handlers.analyses import AnalysisCreateTool


class FakeAnalysisService:

    def __init__(self):
        self.titles = []

    def create(self, title: str):
        self.titles.append(title)

        return SimpleNamespace(
            id="analysis-123",
            title=title,
        )


class AnalysisCreateToolTests(unittest.TestCase):

    def test_create_analysis(self):
        service = FakeAnalysisService()
        tool = AnalysisCreateTool(service)

        result = tool.execute({
            "title": "Receita Comercial"
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["analysis_id"], "analysis-123")
        self.assertEqual(service.titles, ["Receita Comercial"])

    def test_invalid_title(self):
        service = FakeAnalysisService()
        tool = AnalysisCreateTool(service)

        result = tool.execute({
            "title": "   "
        })

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "INVALID_ARGUMENTS",
        )
        self.assertEqual(service.titles, [])
