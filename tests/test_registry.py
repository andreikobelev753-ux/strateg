"""Тесты реестров: check, impact, next-id, stale."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import fixtures  # noqa: F401
import registry


class RegistryTests(unittest.TestCase):
    """Проверки разбора и валидации реестров."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = fixtures.make_project(self.root)
        self.state = self.project / "state"

    def _write(self, name: str, text: str) -> None:
        fixtures.write(self.state / name, text)

    def _claims(self, **replacements: str) -> None:
        text = fixtures.VALID_CLAIMS
        for old, new in replacements.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        self._write("claims.md", text)

    def test_valid_registry_has_no_problems(self) -> None:
        self.assertEqual(registry.check_project(self.project), [])
        result = fixtures.run_tool("registry.py", "check", str(self.project))
        self.assertEqual(result.returncode, 0)
        self.assertIn("Проблем не найдено", result.stdout)

    def test_duplicate_ids(self) -> None:
        sources = fixtures.VALID_SOURCES + "\n## SRC-01\n- Что: дубль\n"
        self._write("sources.md", sources)
        problems = registry.check_project(self.project)
        self.assertTrue(any("повтор" in problem for problem in problems), problems)

    def test_broken_source_link(self) -> None:
        self._claims(**{"Источник: SRC-01, с. 12": "Источник: SRC-99, с. 1"})
        problems = registry.check_project(self.project)
        self.assertTrue(any("SRC-99" in problem for problem in problems), problems)

    def test_fact_without_source(self) -> None:
        self._claims(**{"Источник: SRC-01, с. 12": "Источник: —"})
        problems = registry.check_project(self.project)
        self.assertTrue(any("факт" in problem for problem in problems), problems)

    def test_agent_conclusion_without_depends(self) -> None:
        self._claims(**{"- Опирается на: CLM-01\n": ""})
        problems = registry.check_project(self.project)
        self.assertTrue(any("Опирается на" in problem for problem in problems), problems)

    def test_invalid_claim_status(self) -> None:
        self._claims(**{"Статус: факт": "Статус: истина"})
        problems = registry.check_project(self.project)
        self.assertTrue(any("недопустимый статус" in problem for problem in problems), problems)

    def test_revoked_without_reason_is_problem(self) -> None:
        self._claims(**{"Состояние: действует": "Состояние: отозвано 2026-11-10"})
        problems = registry.check_project(self.project)
        self.assertTrue(any("неверное состояние" in problem for problem in problems), problems)

    def test_revoked_with_reason_is_ok(self) -> None:
        self._claims(**{"Состояние: действует": "Состояние: отозвано 2026-11-10: ошибка в данных"})
        self.assertEqual(registry.check_project(self.project), [])

    def test_missing_required_field(self) -> None:
        self._claims(**{"- Утверждение: Выручка выросла на 12% за год\n": ""})
        problems = registry.check_project(self.project)
        self.assertTrue(any("нет поля «Утверждение»" in problem for problem in problems), problems)

    def test_decision_why_broken(self) -> None:
        decisions = fixtures.VALID_DECISIONS.replace("Почему: CLM-01, CLM-02", "Почему: CLM-99")
        self._write("decisions.md", decisions)
        problems = registry.check_project(self.project)
        self.assertTrue(any("CLM-99" in problem for problem in problems), problems)

    def test_decision_without_why_refs(self) -> None:
        decisions = fixtures.VALID_DECISIONS.replace("Почему: CLM-01, CLM-02", "Почему: —")
        self._write("decisions.md", decisions)
        problems = registry.check_project(self.project)
        self.assertTrue(any("нет ссылок" in problem for problem in problems), problems)

    def test_impact_from_source(self) -> None:
        data = registry.impact_data(self.project, "SRC-01")
        self.assertEqual([claim["id"] for claim in data["claims"]],
                         ["CLM-01", "CLM-02", "CLM-03"])
        self.assertEqual([decision["id"] for decision in data["decisions"]], ["DEC-01"])

    def test_impact_from_claim(self) -> None:
        data = registry.impact_data(self.project, "CLM-01")
        self.assertEqual([claim["id"] for claim in data["claims"]], ["CLM-02", "CLM-03"])
        self.assertEqual([decision["id"] for decision in data["decisions"]], ["DEC-01"])

    def test_impact_marks_revoked(self) -> None:
        self._claims(**{"Состояние: действует": "Состояние: отозвано 2026-11-10: ошибка"})
        result = fixtures.run_tool("registry.py", "impact", str(self.project), "SRC-01")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ОТОЗВАНО", result.stdout)

    def test_impact_unknown_record(self) -> None:
        result = fixtures.run_tool("registry.py", "impact", str(self.project), "SRC-77")
        self.assertEqual(result.returncode, 1)

    def test_next_ids(self) -> None:
        self.assertEqual(registry.next_id(self.project, "SRC"), "SRC-03")
        self.assertEqual(registry.next_id(self.project, "CLM"), "CLM-004")
        self.assertEqual(registry.next_id(self.project, "DEC"), "DEC-02")

    def test_next_ids_empty_registries(self) -> None:
        self._write("sources.md", "# Источники\n")
        self._write("claims.md", "# Утверждения\n")
        self._write("decisions.md", "# Решения\n")
        self.assertEqual(registry.next_id(self.project, "SRC"), "SRC-01")
        self.assertEqual(registry.next_id(self.project, "CLM"), "CLM-001")
        self.assertEqual(registry.next_id(self.project, "DEC"), "DEC-01")

    def test_stale_sources_and_reviews(self) -> None:
        old = (date.today() - timedelta(days=400)).isoformat()
        sources = fixtures.VALID_SOURCES.replace("Дата обращения: 2026-11-01", f"Дата обращения: {old}")
        decisions = fixtures.VALID_DECISIONS.replace("Пересмотреть, если: 2027-02-01",
                                                     f"Пересмотреть, если: {old}")
        self._write("sources.md", sources)
        self._write("decisions.md", decisions)
        data = registry.stale_data(self.project, 180)
        self.assertEqual([item["record"]["id"] for item in data["sources"]], ["SRC-01"])
        self.assertEqual([item["record"]["id"] for item in data["decisions"]], ["DEC-01"])

    def test_stale_ignores_fresh(self) -> None:
        future = (date.today() + timedelta(days=30)).isoformat()
        decisions = fixtures.VALID_DECISIONS.replace("Пересмотреть, если: 2027-02-01",
                                                     f"Пересмотреть, если: {future}")
        self._write("decisions.md", decisions)
        data = registry.stale_data(self.project, 180)
        self.assertEqual(data["decisions"], [])

    def test_check_cli_reports_problems(self) -> None:
        self._claims(**{"Источник: SRC-01, с. 12": "Источник: SRC-99, с. 1"})
        result = fixtures.run_tool("registry.py", "check", str(self.project))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Проблемы в реестрах", result.stdout)


if __name__ == "__main__":
    unittest.main()
