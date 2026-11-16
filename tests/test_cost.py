"""Тесты tools/cost.py: отчёт по этапам/моделям, output/cost.md, диспетчер."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

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
        self.assertIn("- Событий: 4", report)
        self.assertIn("- Токены: вход 350, выход 160", report)
        self.assertIn("- Стоимость: $0.0035", report)
        self.assertIn(cost.HEAD_MODEL_LINE, report)
        self.assertIn(cost.HEAD_MODEL_LINE, out)

    def test_empty_journal(self) -> None:
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        report = (self.project / "output" / "cost.md").read_text(encoding="utf-8")
        self.assertIn("- Событий: 0", report)
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
            env={"STRATEG_PROJECTS": str(projects), "STRATEG_AGENT_ROOT": str(self.root)},
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


if __name__ == "__main__":
    unittest.main()
