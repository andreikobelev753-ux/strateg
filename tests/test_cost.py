"""Тесты tools/cost.py: отчёт по этапам/моделям, output/cost.md, диспетчер."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import os
import unittest
from pathlib import Path
from unittest import mock

import fixtures  # noqa: F401
import cost

EVENTS = [
    {"ts": "2026-11-05T10:00:00+03:00", "stage": "S1", "model": "m1",
     "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001},
    {"ts": "2026-11-05T10:30:00+03:00", "stage": "S1", "model": "m1",
     "tokens_in": 200, "tokens_out": 100, "cost_usd": 0.002},
    {"ts": "2026-11-05T11:00:00+03:00", "stage": "S2", "model": "m2",
     "tokens_in": 50, "tokens_out": 10, "cost_usd": 0.0005},
    {"ts": "2026-11-05T11:05:00+03:00", "stage": "S2", "model": None,
     "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
]


class CostTests(unittest.TestCase):
    """Отчёт о стоимости и копия в output/cost.md."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = fixtures.make_bare_project(self.root)
        # транскрипта Claude Code нет — отчёт должен честно сказать «оценка недоступна»
        patcher = mock.patch.dict(os.environ, {"STRATEG_TRANSCRIPTS": str(self.root / "no-transcripts")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_events(self, events) -> None:
        path = self.project / "state" / "journal.jsonl"
        path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )

    def _run(self, project=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cost.main([str(project or self.project)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_report_by_stage_and_model(self) -> None:
        self._write_events(EVENTS)
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        report = (self.project / "output" / "cost.md").read_text(encoding="utf-8")
        self.assertIn("## По этапам", report)
        self.assertIn("| S1 | 2 | 300 | 150 | 0.0030 | 0:30:00 |", report)
        self.assertIn("## По моделям", report)
        self.assertIn("| m1 | 2 | 300 | 150 | 0.0030 | 0:30:00 |", report)
        self.assertIn("| — | 1 | 0 | 0 | 0.0000 | 0:00:00 |", report)
        self.assertIn("## Итого", report)
        self.assertIn("- Событий журнала: 4", report)
        self.assertIn("- Токены вспомогательных моделей: вход 350, выход 160", report)
        self.assertIn("- Стоимость вспомогательных моделей: $0.0035", report)
        self.assertIn("- Всего: оценка недоступна", report)
        self.assertNotIn("Стоимость: $0.0000", report)
        self.assertIn(cost.HEAD_MODEL_LINE, report)
        self.assertIn(cost.HEAD_MODEL_LINE, out)

    def test_empty_journal(self) -> None:
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        report = (self.project / "output" / "cost.md").read_text(encoding="utf-8")
        self.assertIn("- Событий журнала: 0", report)
        self.assertIn(cost.HEAD_MODEL_LINE, report)
        self.assertIn("Стоимость", out)

    def test_missing_project(self) -> None:
        code, _out, err = self._run(project=self.root / "nope")
        self.assertEqual(code, 1)
        self.assertIn("проект не найден", err)

    def test_dispatcher_cost(self) -> None:
        projects = self.root / "projects"
        project = fixtures.make_bare_project(projects, "demo")
        self._write_events_for(project, EVENTS)
        result = fixtures.run_script(
            fixtures.ROOT / "strateg.py", "cost", "demo",
            env={"STRATEG_PROJECTS": str(projects), "STRATEG_AGENT_ROOT": str(self.root),
                 "STRATEG_TRANSCRIPTS": str(self.root / "no-transcripts")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((project / "output" / "cost.md").is_file())
        self.assertIn(cost.HEAD_MODEL_LINE, result.stdout)

    def _write_events_for(self, project: Path, events) -> None:
        path = project / "state" / "journal.jsonl"
        path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )


DATA = Path(__file__).resolve().parent / "data"


class TranscriptTests(unittest.TestCase):
    """№3+№7 ревью: расход головной модели из транскриптов Claude Code."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.transcripts = self.root / "transcripts"
        session = self.transcripts / "session-1"
        (session / "subagents").mkdir(parents=True)
        (self.transcripts / "session-1.jsonl").write_bytes((DATA / "claude_transcript.jsonl").read_bytes())
        (session / "subagents" / "agent-critic.jsonl").write_bytes(
            (DATA / "claude_subagents" / "agent-critic.jsonl").read_bytes())
        self.project = fixtures.make_bare_project(self.root / "projects", "demo")
        (self.project / "project.json").write_text(json.dumps(
            {"name": "demo", "mode": "express", "created": "2026-11-05",
             "created_at": "2026-11-05T09:30:00+00:00"}), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"STRATEG_TRANSCRIPTS": str(self.transcripts)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parser_dedups_and_filters_by_project_window(self) -> None:
        data = cost.read_transcripts(self.transcripts, cost.project_start(self.project))
        self.assertIsNotNone(data)
        opus = data["models"]["claude-opus-5-5"]
        # msg_A записан двумя строками — считается один раз; msg_before — до начала проекта
        self.assertEqual(opus["messages"], 2)
        self.assertEqual((opus["input"], opus["output"], opus["cache_read"]), (15, 150, 4000))
        self.assertEqual((opus["cache_write_1h"], opus["cache_write_5m"]), (2000, 0))
        self.assertEqual(data["models"]["claude-sonnet-5-5"]["output"], 200)  # субагент
        self.assertNotIn("<synthetic>", data["models"])
        # активное время: 30 + 270 + 1 (служебная запись) + 60 с; пауза 35 минут не считается
        self.assertEqual(data["active_seconds"], 361)

    def test_cost_and_budget_tokens(self) -> None:
        usage = cost.head_usage(self.project)
        self.assertAlmostEqual(usage["models"]["claude-opus-5-5"]["cost"], 0.01986, places=6)
        self.assertAlmostEqual(usage["models"]["claude-sonnet-5-5"]["cost"], 0.0022, places=6)
        self.assertAlmostEqual(usage["cost"], 0.02206, places=6)
        self.assertEqual(usage["budget_tokens"], 15 + 150 + 2000 + 300)

    def test_price_prefix_match(self) -> None:
        prices = {"claude-opus-5-5": {"input": 4.0, "output": 20.0}}
        self.assertAlmostEqual(cost.usage_cost("claude-opus-5-5-20261101", {"input": 1_000_000}, prices), 4.0)
        self.assertIsNone(cost.usage_cost("unknown-model", {"input": 1}, prices))

    def test_report_uses_transcript(self) -> None:
        report = cost.build_report(self.project, "demo")
        self.assertIn("транскрипты Claude Code", report)
        self.assertIn("| claude-opus-5-5 | 2 | 15 | 150 | 4000 | 2000 | 0.02 |", report)
        self.assertIn("- Всего: $0.02 по ценам API", report)
        self.assertIn("- Активное время: 0:06:01", report)
        self.assertNotIn(cost.HEAD_MODEL_LINE, report)

    def test_missing_transcript_is_not_zero(self) -> None:
        with mock.patch.dict(os.environ, {"STRATEG_TRANSCRIPTS": str(self.root / "nope")}):
            self.assertIsNone(cost.head_usage(self.project))
            report = cost.build_report(self.project, "demo")
        self.assertIn("Оценка недоступна", report)
        self.assertIn("Это не $0", report)

    def test_active_seconds(self) -> None:
        from datetime import datetime, timedelta
        base = datetime(2026, 11, 5, 10, 0)
        stamps = [base, base + timedelta(minutes=9), base + timedelta(minutes=30)]
        self.assertEqual(cost.active_seconds(stamps), 9 * 60)

    def test_status_brief_shows_budget_share(self) -> None:
        result = fixtures.run_script(
            fixtures.ROOT / "strateg.py", "status", "--brief", "demo",
            env={"STRATEG_PROJECTS": str(self.root / "projects"),
                 "STRATEG_TRANSCRIPTS": str(self.transcripts)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("бюджет ≈ 1%", result.stdout)
        self.assertNotIn("по счётчику среды", result.stdout)

    def test_status_brief_warns_at_80_and_stops_at_100(self) -> None:
        import strateg
        with mock.patch.object(strateg, "_mode_budget", return_value=("express", 3000, 0.8)):
            self.assertIn("⚠ Израсходовано 82%", strateg._progress_line(self.project))
        with mock.patch.object(strateg, "_mode_budget", return_value=("express", 2000, 0.8)):
            self.assertIn("⛔ Бюджет режима исчерпан", strateg._progress_line(self.project))

    def test_status_brief_without_transcript(self) -> None:
        import strateg
        with mock.patch.dict(os.environ, {"STRATEG_TRANSCRIPTS": str(self.root / "nope")}):
            line = strateg._progress_line(self.project)
        self.assertIn("оценка недоступна", line)


if __name__ == "__main__":
    unittest.main()
