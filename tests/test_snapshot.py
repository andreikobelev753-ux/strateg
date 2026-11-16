"""Тесты снимков и восстановления."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fixtures  # noqa: F401
import journal
import snapshot


class SnapshotTests(unittest.TestCase):
    """Снимки не теряют данные, восстановление сохраняет текущее."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = fixtures.make_project(self.root)
        fixtures.write(self.project / "state" / "state.md", "# Проект\n\nЭтап: S1\nДальше: снимок\n")
        fixtures.write(self.project / "output" / "strategy.md", "версия 1\n")
        printer = mock.patch("builtins.print")
        printer.start()
        self.addCleanup(printer.stop)

    def _snapshot_dirs(self):
        return sorted(p for p in (self.project / "drafts").iterdir() if p.is_dir())

    def test_snapshot_copies_state_and_output(self) -> None:
        target = snapshot.create_snapshot(self.project, "S1")
        self.assertTrue(target.is_dir())
        self.assertRegex(target.name, r"^01_S1_\d{8}-\d{6}$")
        self.assertEqual((target / "state" / "state.md").read_text(encoding="utf-8"),
                         (self.project / "state" / "state.md").read_text(encoding="utf-8"))
        self.assertEqual((target / "output" / "strategy.md").read_text(encoding="utf-8"), "версия 1\n")

    def test_snapshot_numbering_increments(self) -> None:
        first = snapshot.create_snapshot(self.project, "S1")
        second = snapshot.create_snapshot(self.project, "S2")
        self.assertTrue(first.name.startswith("01_"))
        self.assertTrue(second.name.startswith("02_"))

    def test_restore_returns_snapshot_state(self) -> None:
        frozen = (self.project / "state" / "state.md").read_text(encoding="utf-8")
        target = snapshot.create_snapshot(self.project, "S1")
        fixtures.write(self.project / "state" / "state.md", "изменено\n")
        snapshot.restore(self.project, target.name)
        self.assertEqual((self.project / "state" / "state.md").read_text(encoding="utf-8"), frozen)

    def test_restore_moves_current_to_before_restore(self) -> None:
        target = snapshot.create_snapshot(self.project, "S1")
        fixtures.write(self.project / "state" / "state.md", "изменено\n")
        fixtures.write(self.project / "output" / "strategy.md", "версия 2\n")
        snapshot.restore(self.project, target.name)
        before = [p for p in self._snapshot_dirs() if "before-restore" in p.name]
        self.assertEqual(len(before), 1)
        self.assertEqual((before[0] / "state" / "state.md").read_text(encoding="utf-8"), "изменено\n")
        self.assertEqual((before[0] / "output" / "strategy.md").read_text(encoding="utf-8"), "версия 2\n")

    def test_restore_writes_journal_event(self) -> None:
        target = snapshot.create_snapshot(self.project, "S1")
        snapshot.restore(self.project, target.name)
        statuses = [event.get("status") for event in journal.read_events(self.project)]
        self.assertIn("restore", statuses)

    def test_restore_unknown_snapshot(self) -> None:
        with self.assertRaises(FileNotFoundError):
            snapshot.restore(self.project, "99_нет_20260101-000000")

    def test_restore_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            snapshot.restore(self.project, "../evil")

    def test_list_cli(self) -> None:
        snapshot.create_snapshot(self.project, "S1")
        result = fixtures.run_tool("snapshot.py", "list", str(self.project))
        self.assertEqual(result.returncode, 0)
        self.assertIn("01_", result.stdout)
        self.assertIn("state:", result.stdout)

    def test_snapshot_cli(self) -> None:
        result = fixtures.run_tool("snapshot.py", "snapshot", str(self.project), "S3.1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Снимок создан", result.stdout)


if __name__ == "__main__":
    unittest.main()
