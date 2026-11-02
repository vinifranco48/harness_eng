import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.web.api.domain.analysis import AnalysisStorageError, AnalysisValidationError
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

    def test_invalid_arguments_never_reach_the_service(self):
        service = FakeAnalysisService()
        tool = AnalysisCreateTool(service)

        for arguments in (
            {}, {"title": "a" * 121}, {"title": 123},
            {"title": "Receita", "admin": True},
        ):
            with self.subTest(arguments=arguments):
                result = tool.execute(arguments)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(service.titles, [])

    def test_domain_validation_errors_become_invalid_arguments(self):
        service = FakeAnalysisService()
        tool = AnalysisCreateTool(service)
        with patch.object(service, "create", side_effect=AnalysisValidationError("private detail")):
            result = tool.execute({"title": "Receita"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")
        self.assertNotIn("private detail", str(result))

    def test_storage_errors_return_a_safe_result(self):
        service = FakeAnalysisService()
        tool = AnalysisCreateTool(service)
        with patch.object(service, "create", side_effect=AnalysisStorageError("private-password; INSERT INTO analyses")):
            result = tool.execute({"title": "Receita"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STORAGE_ERROR")
        self.assertTrue(result["error"]["message"])
        self.assertNotIn("analysis_id", result)
        self.assertNotIn("private-password", str(result))
        self.assertNotIn("INSERT", str(result))
