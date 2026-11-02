import unittest

from apps.web.api.domain.analysis import Analysis
from apps.web.api.services.analysis import AnalysisService
from apps.web.api.tools.definitions import ANALYSIS_CREATE_TOOL_NAME
from apps.web.api.tools.registry import create_tool_executor, list_tool_definitions


class InMemoryAnalysisRepository:

    def __init__(self):
        self.analyses: dict[str, Analysis] = {}

    def insert(self, analysis: Analysis) -> None:
        self.analyses[analysis.id] = analysis

    def get(self, analysis_id: str) -> Analysis | None:
        return self.analyses.get(analysis_id)

    def list(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[Analysis]:
        return list(self.analyses.values())[offset:offset + limit]


class AnalysisToolIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.repository = InMemoryAnalysisRepository()
        self.service = AnalysisService(self.repository)
        self.executor = create_tool_executor(self.service)

    def test_create_analysis_persists_and_can_be_retrieved(self):
        result = self.executor.execute(ANALYSIS_CREATE_TOOL_NAME, {
            "title": "  Receita Comercial  ",
        })

        self.assertTrue(result["ok"])
        analysis = self.service.get(result["analysis_id"])
        self.assertIsInstance(analysis, Analysis)
        self.assertEqual(analysis.title, "Receita Comercial")
        self.assertEqual(result["title"], analysis.title)
        self.assertIs(self.repository.get(analysis.id), analysis)
        self.assertEqual(self.service.list(), [analysis])

    def test_invalid_title_does_not_persist_analysis(self):
        result = self.executor.execute(ANALYSIS_CREATE_TOOL_NAME, {"title": "   "})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.service.list(), [])

    def test_list_respects_limit_and_offset(self):
        analyses = []
        for title in ("Receita", "Despesas", "Lucro", "Margem"):
            result = self.executor.execute(ANALYSIS_CREATE_TOOL_NAME, {"title": title})
            self.assertTrue(result["ok"])
            analyses.append(self.service.get(result["analysis_id"]))

        for limit, offset, expected in (
            (2, 0, analyses[:2]),
            (2, 1, analyses[1:3]),
            (2, 3, analyses[3:]),
            (2, 4, []),
            (2, 5, []),
        ):
            with self.subTest(limit=limit, offset=offset):
                self.assertEqual(
                    self.service.list(limit=limit, offset=offset), expected,
                )

    def test_get_unknown_analysis_returns_none(self):
        self.executor.execute(ANALYSIS_CREATE_TOOL_NAME, {"title": "Receita Comercial"})

        self.assertIsNone(
            self.repository.get("00000000-0000-0000-0000-000000000000"),
        )

    def test_invalid_unicode_does_not_persist_analysis(self):
        for title in ("\ud800", "Safra \udfff"):
            with self.subTest(title=repr(title)):
                result = self.executor.execute(ANALYSIS_CREATE_TOOL_NAME, {"title": title})
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.service.list(), [])

    def test_advertised_tool_uses_the_registered_name(self):
        definitions = list_tool_definitions(self.service)
        self.assertEqual(len(definitions), 1)

        result = self.executor.execute(definitions[0]["name"], {"title": "Safra"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.service.get(result["analysis_id"]).title, "Safra")

    def test_unconfigured_storage_does_not_register_or_advertise_tools(self):
        self.assertEqual(list_tool_definitions(None), [])

        result = create_tool_executor(None).execute(ANALYSIS_CREATE_TOOL_NAME, {"title": "Safra"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "TOOL_NOT_FOUND")
        self.assertEqual(self.repository.analyses, {})
