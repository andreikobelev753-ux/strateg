"""Тесты журнала событий."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import fixtures  # noqa: F401 - настраивает sys.path
import journal


class JournalTests(unittest.TestCase):
    """Проверки записи и чтения журнала."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = fixtures.make_project(self.root)

    def test_start_then_done(self) -> None:
        self.assertFalse(journal.is_done(self.project, "extract:a.pdf"))
        journal.append_event(self.project, "start", "S1", "extract:a.pdf")
        self.assertFalse(journal.is_done(self.project, "extract:a.pdf"))
        journal.append_event(self.project, "done", "S1", "extract:a.pdf")
        self.assertTrue(journal.is_done(self.project, "extract:a.pdf"))

    def test_is_done_cli_exit_codes(self) -> None:
        result = fixtures.run_tool("journal.py", "is-done", str(self.project), "extract:a.pdf")
        self.assertEqual(result.returncode, 1)
        journal.append_event(self.project, "done", "S1", "extract:a.pdf")
        result = fixtures.run_tool("journal.py", "is-done", str(self.project), "extract:a.pdf")
        self.assertEqual(result.returncode, 0)

    def test_pending_after_crash(self) -> None:
        journal.append_event(self.project, "start", "S1", "a")
        journal.append_event(self.project, "start", "S1", "b")
        journal.append_event(self.project, "done", "S1", "b")
        journal.append_event(self.project, "retry", "S1", "c")
        keys = {event["key"] for event in journal.pending_steps(self.project)}
        self.assertEqual(keys, {"a", "c"})

    def test_last_status_wins(self) -> None:
        journal.append_event(self.project, "start", "S1", "a")
        journal.append_event(self.project, "done", "S1", "a")
        journal.append_event(self.project, "retry", "S1", "a")
        self.assertIn("a", {event["key"] for event in journal.pending_steps(self.project)})
        journal.append_event(self.project, "done", "S1", "a")
        self.assertNotIn("a", {event["key"] for event in journal.pending_steps(self.project)})

    def test_event_format(self) -> None:
        journal.append_event(
            self.project, "done", "S2.2", "extract:materials/report.pdf",
            tool="markitdown", model="gpt-oss:20b", tokens_in=10, tokens_out=5,
            cost_usd=0.001, note="проверка", chat="main",
        )
        line = (self.project / "state" / "journal.jsonl").read_text(encoding="utf-8").strip()
        event = json.loads(line)
        expected = {"ts", "chat", "stage", "key", "status", "tool", "model",
                    "tokens_in", "tokens_out", "cost_usd", "note"}
        self.assertEqual(set(event), expected)
        self.assertEqual(event["status"], "done")
        self.assertEqual(event["key"], "extract:materials/report.pdf")
        self.assertEqual(event["tokens_in"], 10)
        self.assertAlmostEqual(event["cost_usd"], 0.001)
        datetime.fromisoformat(event["ts"])

    def test_journal_only_appends(self) -> None:
        journal.append_event(self.project, "start", "S1", "a")
        first = (self.project / "state" / "journal.jsonl").read_text(encoding="utf-8")
        journal.append_event(self.project, "done", "S1", "a")
        second = (self.project / "state" / "journal.jsonl").read_text(encoding="utf-8")
        self.assertTrue(second.startswith(first))
        self.assertEqual(len(second.strip().splitlines()), 2)

    def test_summary_totals(self) -> None:
        journal.append_event(self.project, "start", "S1", "a", tokens_in=100, cost_usd=0.001)
        journal.append_event(self.project, "done", "S1", "a", tokens_out=50, cost_usd=0.002)
        journal.append_event(self.project, "start", "S2", "b", tokens_in=10)
        data = journal.summary(self.project)
        self.assertEqual(data["totals"]["events"], 3)
        self.assertEqual(data["totals"]["tokens_in"], 110)
        self.assertEqual(data["totals"]["tokens_out"], 50)
        self.assertAlmostEqual(data["totals"]["cost_usd"], 0.003)
        self.assertEqual([item["stage"] for item in data["stages"]], ["S1", "S2"])

    def test_summary_cli_output(self) -> None:
        journal.append_event(self.project, "start", "S1", "a", tokens_in=100)
        journal.append_event(self.project, "done", "S1", "a", cost_usd=0.5)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            journal._print_summary(self.project)
        output = buffer.getvalue()
        self.assertIn("S1", output)
        self.assertIn("Итого", output)
        self.assertIn("0.5000", output)

    def test_unknown_status(self) -> None:
        with self.assertRaises(ValueError):
            journal.append_event(self.project, "finished", "S1", "a")

    def test_empty_journal_summary(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            journal._print_summary(self.project)
        self.assertIn("Журнал пуст", buffer.getvalue())

    def test_pending_cli(self) -> None:
        journal.append_event(self.project, "start", "S1", "a")
        result = fixtures.run_tool("journal.py", "pending", str(self.project))
        self.assertEqual(result.returncode, 0)
        self.assertIn("a", result.stdout)


if __name__ == "__main__":
    unittest.main()
