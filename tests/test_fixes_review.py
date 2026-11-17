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


if __name__ == "__main__":
    unittest.main()
