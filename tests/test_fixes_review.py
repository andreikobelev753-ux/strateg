"""Тесты на исправления по ревью (ветка fixes-review). Номера — из ревью."""

from __future__ import annotations

import unittest
from pathlib import Path

import fixtures  # noqa: F401  (настраивает sys.path)

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class ReviewItem05Options(unittest.TestCase):
    """№5: «закрыть направление» = закрыть сам продукт; варианты не близнецы."""

    def test_options_skill_defines_closing_the_product(self) -> None:
        text = _read("skills/options/SKILL.md")
        self.assertIn("сам продукт или линию бизнеса", text)
        self.assertIn("что теряем", text)
        self.assertIn("высвобождаем", text)
        self.assertIn("не должны отличаться одной настройкой", text)

    def test_critic_checklist(self) -> None:
        for rel in ("agents/critic.md", ".claude/agents/critic.md"):
            text = _read(rel)
            self.assertIn("Закрытие продукта рассмотрено всерьёз", text, rel)
            self.assertIn("Варианты не близнецы", text, rel)


class ReviewItem17Permissions(unittest.TestCase):
    """№17: разрешения первого шага и папка inbox/."""

    def test_settings_allow(self) -> None:
        import json
        allow = json.loads(_read(".claude/settings.json"))["permissions"]["allow"]
        for rule in ("Bash(cp inbox/:*)", "Bash(ls:*)", "Bash(grep:*)", "WebSearch", "WebFetch",
                     "Bash(python3 strateg.py:*)"):
            self.assertIn(rule, allow)

    def test_inbox_documented_and_ignored(self) -> None:
        self.assertTrue((ROOT / "inbox" / ".gitkeep").is_file())
        lines = _read(".gitignore").splitlines()
        self.assertIn("inbox/*", lines)
        self.assertIn("!inbox/.gitkeep", lines)
        self.assertIn("inbox/", _read("README.md"))
        self.assertIn("cp inbox/", _read("skills/intake/SKILL.md"))


if __name__ == "__main__":
    unittest.main()
