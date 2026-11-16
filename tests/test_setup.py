"""Тесты strateg.py setup — только --dry-run во временной папке."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import fixtures  # noqa: F401

ROOT = fixtures.ROOT


class SetupDryRunTests(unittest.TestCase):
    """Проверяем план setup и отсутствие любых изменений при --dry-run."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for name in ("requirements.txt", "requirements-media.txt", "requirements-ux.txt"):
            shutil.copyfile(ROOT / name, self.root / name)
        self.env = {
            "STRATEG_AGENT_ROOT": str(self.root),
            "STRATEG_PROJECTS": str(self.root / "projects"),
        }

    def _run(self, *args: str):
        return fixtures.run_script(ROOT / "strateg.py", "setup", *args, env=self.env)

    def test_dry_run_creates_nothing(self) -> None:
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("План setup", result.stdout)
        self.assertIn(".venv", result.stdout)
        self.assertIn("doctor", result.stdout)
        for name in (".venv", ".env", ".claude", "doctor.json"):
            self.assertFalse((self.root / name).exists(), name)

    def test_dry_run_keeps_existing_env_and_settings(self) -> None:
        fixtures.write(self.root / ".env", "DEEPSEEK_API_KEY=secret\n")
        fixtures.write(self.root / ".claude" / "settings.json", "{}\n")
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("не перезаписываю", result.stdout)
        self.assertIn("уже есть", result.stdout)
        self.assertEqual((self.root / ".env").read_text(encoding="utf-8"),
                         "DEEPSEEK_API_KEY=secret\n")
        self.assertEqual((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"), "{}\n")

    def test_dry_run_mentions_skills_and_agents(self) -> None:
        fixtures.write(self.root / "skills" / "one.md", "# навык\n")
        fixtures.write(self.root / "agents" / "two.md", "# агент\n")
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skills/", result.stdout)
        self.assertIn("agents/", result.stdout)
        self.assertIn("пометку", result.stdout)
        self.assertFalse((self.root / ".claude").exists())

    def test_dry_run_with_media_and_ux(self) -> None:
        result = self._run("--dry-run", "--media", "--ux")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("requirements-media.txt", result.stdout)
        self.assertIn("requirements-ux.txt", result.stdout)

    def test_setup_help_lists_flags(self) -> None:
        result = fixtures.run_script(ROOT / "strateg.py", "setup", "--help", env=self.env)
        self.assertEqual(result.returncode, 0)
        for flag in ("--media", "--ux", "--dry-run"):
            self.assertIn(flag, result.stdout)


if __name__ == "__main__":
    unittest.main()
