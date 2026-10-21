from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

from apps.web.api.domain.analysis import (
    Analysis,
    AnalysisNotFound,
    AnalysisStorageError,
    AnalysisValidationError,
)
from apps.web.api.services.analysis import AnalysisService


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Analysis] = {}
        self.calls: list[tuple] = []
        self.error: AnalysisStorageError | None = None

    def insert(self, analysis: Analysis) -> None:
        self.calls.append(("insert", analysis))
        if self.error:
            raise self.error
        self.rows[analysis.id] = analysis

    def get(self, analysis_id: str) -> Analysis | None:
        self.calls.append(("get", analysis_id))
        if self.error:
            raise self.error
        return self.rows.get(analysis_id)

    def list(self, *, limit: int, offset: int) -> list[Analysis]:
        self.calls.append(("list", limit, offset))
        if self.error:
            raise self.error
        return list(self.rows.values())[offset:offset + limit]


class AnalysisDomainTests(unittest.TestCase):
    def test_create_trims_unicode_whitespace_and_preserves_the_title(self) -> None:
        analysis = Analysis.create("\u2003\t  Análise de produção 🌱 — São João  \n\u00a0")
        self.assertEqual(analysis.title, "Análise de produção 🌱 — São João")

    def test_create_preserves_internal_whitespace(self) -> None:
        self.assertEqual(Analysis.create("  Safra  2026  ").title, "Safra  2026")

    def test_create_generates_a_canonical_uuid4(self) -> None:
        analysis = Analysis.create("Safra")
        identifier = UUID(analysis.id)
        self.assertEqual(identifier.version, 4)
        self.assertEqual(str(identifier), analysis.id)
        self.assertNotEqual(analysis.id, Analysis.create("Safra").id)

    def test_create_uses_one_aware_utc_timestamp_for_both_fields(self) -> None:
        analysis = Analysis.create("Safra")
        self.assertIs(analysis.created_at.tzinfo, UTC)
        self.assertIs(analysis.updated_at.tzinfo, UTC)
        self.assertEqual(analysis.created_at, analysis.updated_at)

    def test_title_limits_are_applied_after_trimming(self) -> None:
        for title in ("a", "á" * 120, "🌱" * 120):
            with self.subTest(title_length=len(title)):
                self.assertEqual(Analysis.create(f"  {title}  ").title, title)

    def test_empty_and_overlong_titles_are_rejected(self) -> None:
        for title in ("", " \t\r\n\u2003\u00a0", "a" * 121, "á" * 121, "🌱" * 121):
            with self.subTest(title_length=len(title)):
                with self.assertRaises(AnalysisValidationError):
                    Analysis.create(title)

    def test_non_text_titles_raise_a_domain_error(self) -> None:
        for title in (None, True, 7, [], {}, b"Safra"):
            with self.subTest(title=title):
                with self.assertRaises(AnalysisValidationError):
                    Analysis.create(title)

    def test_lone_high_and_low_surrogates_are_rejected_as_invalid_unicode(self) -> None:
        for escape in (r'"\ud800"', r'"\udbff"', r'"\udc00"', r'"\udfff"', r'"Safra \ud800"', r'"Safra \udfff"'):
            with self.subTest(escape=escape):
                with self.assertRaises(AnalysisValidationError):
                    Analysis.create(json.loads(escape))

    def test_valid_json_surrogate_pair_remains_a_supported_unicode_title(self) -> None:
        title = json.loads(r'"Safra \ud83c\udf31"')
        self.assertEqual(Analysis.create(title).title, "Safra 🌱")

    def test_public_contains_only_analysis_fields_and_utc_iso_timestamps(self) -> None:
        instant = datetime(2026, 10, 16, 12, 34, 56, 123456, tzinfo=UTC)
        identifier = str(uuid4())
        analysis = Analysis(identifier, "Safra", instant, instant)
        self.assertEqual(analysis.public(), {
            "id": identifier,
            "title": "Safra",
            "created_at": "2026-10-16T12:34:56.123456Z",
            "updated_at": "2026-10-16T12:34:56.123456Z",
        })

    def test_public_normalizes_aware_timestamps_to_utc(self) -> None:
        instant = datetime(2026, 10, 16, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
        analysis = Analysis(str(uuid4()), "Safra", instant, instant)
        self.assertEqual(analysis.public()["created_at"], "2026-10-16T12:00:00Z")
        self.assertEqual(analysis.public()["updated_at"], "2026-10-16T12:00:00Z")

    def test_public_does_not_assume_a_local_timezone_for_naive_dates(self) -> None:
        naive = datetime(2026, 10, 16, 12, 0)
        aware = naive.replace(tzinfo=UTC)
        for created_at, updated_at in ((naive, aware), (aware, naive)):
            with self.subTest(created_at=created_at, updated_at=updated_at):
                with self.assertRaises(AnalysisValidationError):
                    Analysis(str(uuid4()), "Safra", created_at, updated_at).public()


class AnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeAnalysisRepository()
        self.service = AnalysisService(self.repository)

    def test_create_persists_and_returns_an_analysis_without_chat_context(self) -> None:
        analysis = self.service.create("  Indicadores da safra  ")
        self.assertEqual(analysis.title, "Indicadores da safra")
        self.assertIs(self.repository.rows[analysis.id], analysis)
        self.assertEqual(self.repository.calls, [("insert", analysis)])
        self.assertNotIn("chat_id", analysis.public())

    def test_invalid_title_never_reaches_the_repository(self) -> None:
        with self.assertRaises(AnalysisValidationError):
            self.service.create("  ")
        self.assertEqual(self.repository.calls, [])

    def test_invalid_unicode_title_never_reaches_the_repository(self) -> None:
        with self.assertRaises(AnalysisValidationError):
            self.service.create(json.loads(r'"Safra \ud800"'))
        self.assertEqual(self.repository.calls, [])

    def test_get_returns_the_stored_analysis(self) -> None:
        analysis = self.service.create("Safra")
        self.repository.calls.clear()
        self.assertIs(self.service.get(analysis.id), analysis)
        self.assertEqual(self.repository.calls, [("get", analysis.id)])

    def test_get_normalizes_valid_uuid_spellings(self) -> None:
        analysis = self.service.create("Safra")
        identifier = UUID(analysis.id)
        for supplied in (analysis.id.upper(), identifier.hex, f"{{{analysis.id}}}", f"  {analysis.id}  ", identifier.urn):
            with self.subTest(supplied=supplied):
                self.repository.calls.clear()
                self.assertIs(self.service.get(supplied), analysis)
                self.assertEqual(self.repository.calls, [("get", analysis.id)])

    def test_get_raises_not_found_for_an_absent_valid_uuid(self) -> None:
        identifier = str(uuid4())
        with self.assertRaises(AnalysisNotFound):
            self.service.get(identifier)
        self.assertEqual(self.repository.calls, [("get", identifier)])

    def test_invalid_identifiers_never_reach_the_repository(self) -> None:
        for identifier in ("", "  ", "not-a-uuid", "123", None, True, 7, [], b"abc", uuid4()):
            with self.subTest(identifier=identifier):
                with self.assertRaises(AnalysisValidationError):
                    self.service.get(identifier)
        self.assertEqual(self.repository.calls, [])

    def test_list_uses_default_pagination(self) -> None:
        self.assertEqual(self.service.list(), [])
        self.assertEqual(self.repository.calls, [("list", 20, 0)])

    def test_list_returns_the_repository_page(self) -> None:
        analyses = [self.service.create(f"Safra {index}") for index in range(4)]
        self.repository.calls.clear()
        self.assertEqual(self.service.list(limit=2, offset=1), analyses[1:3])
        self.assertEqual(self.repository.calls, [("list", 2, 1)])

    def test_list_accepts_pagination_boundaries(self) -> None:
        for limit in (1, 100):
            for offset in (0, 2_147_483_647):
                with self.subTest(limit=limit, offset=offset):
                    self.repository.calls.clear()
                    self.assertEqual(self.service.list(limit=limit, offset=offset), [])
                    self.assertEqual(self.repository.calls, [("list", limit, offset)])

    def test_list_rejects_invalid_limits_before_reading_storage(self) -> None:
        for limit in (0, -1, 101, True, False, 1.0, "20", None):
            with self.subTest(limit=limit):
                with self.assertRaises(AnalysisValidationError):
                    self.service.list(limit=limit)
        self.assertEqual(self.repository.calls, [])

    def test_list_rejects_invalid_offsets_before_reading_storage(self) -> None:
        for offset in (-1, 2_147_483_648, True, False, 0.0, "0", None):
            with self.subTest(offset=offset):
                with self.assertRaises(AnalysisValidationError):
                    self.service.list(offset=offset)
        self.assertEqual(self.repository.calls, [])

    def test_storage_errors_propagate_from_each_use_case(self) -> None:
        error = AnalysisStorageError("Armazenamento indisponível.")
        self.repository.error = error
        operations = (
            lambda: self.service.create("Safra"),
            lambda: self.service.get(str(uuid4())),
            self.service.list,
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(AnalysisStorageError) as raised:
                    operation()
                self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
