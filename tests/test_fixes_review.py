"""Тесты на исправления по ревью (ветка fixes-review). Номера — из ревью."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class ReviewItem11ExtractDedup(unittest.TestCase):
    """№11: повторный extract возвращает уже выданный SRC (путь и отпечаток файла)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = fixtures.make_bare_project(Path(self._tmp.name))
        self.memo = self.project / "materials" / "memo.md"
        self.memo.write_text("Записка: 1 700 подключений.\n", encoding="utf-8")

    def _call(self, module, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = module.main([str(self.project), *map(str, args)])
        return code, out.getvalue()

    def _ids(self):
        import registry
        return [r["id"] for r in registry.parse_file(self.project / "state" / "sources.md")]

    def test_docs_same_file_twice(self) -> None:
        import docs
        self.assertEqual(self._call(docs, "materials/memo.md")[0], 0)
        code, out = self._call(docs, self.memo)  # тот же файл, абсолютный путь
        self.assertEqual(code, 0)
        self.assertIn("Уже в реестре: SRC-01", out)
        self.assertEqual(self._ids(), ["SRC-01"])
        sources = (self.project / "state" / "sources.md").read_text(encoding="utf-8")
        self.assertIn("- Где: materials/memo.md", sources)
        self.assertIn("- Хеш: sha256:", sources)

    def test_docs_same_content_other_path(self) -> None:
        import docs
        self._call(docs, self.memo)
        copy = self.project / "materials" / "memo-copy.md"
        copy.write_bytes(self.memo.read_bytes())
        self.assertIn("SRC-01", self._call(docs, copy)[1])
        self.assertEqual(self._ids(), ["SRC-01"])

    def test_docs_changed_file_gets_new_version(self) -> None:
        import docs
        self._call(docs, self.memo)
        self.memo.write_text("Записка, версия 2: 810 платящих.\n", encoding="utf-8")
        self._call(docs, self.memo)
        self.assertEqual(self._ids(), ["SRC-01", "SRC-02"])
        sources = (self.project / "state" / "sources.md").read_text(encoding="utf-8")
        self.assertIn("новая версия файла, прежняя — SRC-01", sources)

    def test_docs_again_flag(self) -> None:
        import docs
        self._call(docs, self.memo)
        self._call(docs, self.memo, "--again")
        self.assertEqual(self._ids(), ["SRC-01", "SRC-02"])

    def test_web_saved_page_twice(self) -> None:
        import web
        page = self.project / "materials" / "page.html"
        page.write_text("<html><title>T</title><body>Текст</body></html>", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"trafilatura": None}):
            self._call(web, page)
            code, out = self._call(web, page)
        self.assertEqual(code, 0)
        self.assertIn("Уже в реестре: SRC-01", out)
        self.assertEqual(self._ids(), ["SRC-01"])

    def test_tables_twice(self) -> None:
        import tables
        csv = self.project / "materials" / "t.csv"
        csv.write_text("месяц,платящих\n2026-09,810\n", encoding="utf-8")
        self._call(tables, csv)
        code, out = self._call(tables, csv)
        self.assertEqual(code, 0)
        self.assertIn("SRC-01", out)
        self.assertEqual(self._ids(), ["SRC-01"])


class ReviewItem12Baseline(unittest.TestCase):
    """№12: база сравнения без затронутых когорт; критик проверяет расчёты."""

    def test_skill_and_critic(self) -> None:
        self.assertIn("не включает затронутые когорты", _read("skills/data-analysis/SKILL.md"))
        for rel in ("agents/critic.md", ".claude/agents/critic.md"):
            text = _read(rel)
            self.assertIn("Проверка расчётов", text, rel)
            self.assertIn("база сравнения", text, rel)


class ReviewItem13OnePager(unittest.TestCase):
    """№13+№14: одностраничник до 250 слов и 2 графиков, номера в сносках; чат до 15 строк."""

    def test_rules_in_texts(self) -> None:
        self.assertIn("до 250 слов", _read("templates/one-pager.md"))
        self.assertIn("[^1]:", _read("templates/one-pager.md"))
        self.assertIn("до 15 строк", _read("AGENTS.md"))
        self.assertNotIn("3–8 строк", _read("AGENTS.md"))

    def test_one_pager_problems(self) -> None:
        import report
        good = "# Т\n\n**Ответ.** Чинить Android [вывод][^1]\n\n![a](charts/a.svg)\n\n[^1]: CLM-012, SRC-03\n"
        self.assertEqual(report.one_pager_problems(good), [])
        long_text = "# Т\n\n" + " ".join(["слово"] * 260) + "\n"
        self.assertTrue(any("слов, нужно до 250" in p for p in report.one_pager_problems(long_text)))
        charts = "\n".join(f"![{i}](charts/{i}.svg)" for i in range(3))
        self.assertTrue(any("графиков 3" in p for p in report.one_pager_problems(charts)))
        inline_ids = "Платят 810 (CLM-064) [факт, SRC-03]"
        self.assertTrue(any("CLM-064" in p for p in report.one_pager_problems(inline_ids)))

    def test_footnotes_render(self) -> None:
        import report
        html = report.md_to_html("Текст[^1]\n\n[^1]: CLM-012", {}, set())
        self.assertIn('<sup class="fn"><a href="#fn-1">1</a></sup>', html)
        self.assertIn('id="fn-1"', html)
        self.assertNotIn("[^1]", html)


class ReviewItem09Routing(unittest.TestCase):
    """№9: респондент на Sonnet, исследователь и критик — на головной модели."""

    def _front(self, rel: str) -> str:
        text = _read(rel)
        return text.split("---", 2)[1]

    def test_models(self) -> None:
        for base in ("agents", ".claude/agents"):
            self.assertIn("\nmodel: sonnet\n", self._front(f"{base}/persona.md"))
            self.assertNotIn("model:", self._front(f"{base}/researcher.md"))
            self.assertNotIn("model:", self._front(f"{base}/critic.md"))


if __name__ == "__main__":
    unittest.main()
