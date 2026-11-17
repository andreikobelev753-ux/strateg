"""Тесты конфигов, файлов окружения и шаблона проекта."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import fixtures  # noqa: F401
import strateg

ROOT = fixtures.ROOT
CONFIG = ROOT / "config"
TEMPLATE = ROOT / "projects" / "_template"
STATE_HEADERS = ["Этап", "Сделано", "Сейчас", "Дальше", "Ждём от пользователя",
                 "Допущения", "Последний снимок", "Обновлено"]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ConfigTests(unittest.TestCase):
    """config/*.json читаются без сторонних библиотек и соответствуют заданию."""

    def test_routing(self) -> None:
        routing = load_json(CONFIG / "routing.json")
        deepseek = routing["providers"]["deepseek"]
        self.assertEqual(deepseek["base_url"], "https://api.deepseek.com")
        self.assertEqual(deepseek["key_env"], "DEEPSEEK_API_KEY")
        tasks = routing["tasks"]
        self.assertEqual(
            set(tasks),
            {"extract_facts", "tag_claims", "synthetic_persona", "classify_request",
             "research", "options", "strategy", "critic"},
        )
        for name in ("extract_facts", "tag_claims", "synthetic_persona"):
            self.assertEqual(tasks[name]["executor"], "helper")
            self.assertEqual(tasks[name]["provider"], "deepseek")
            self.assertTrue(tasks[name]["model"])
            self.assertNotEqual(tasks[name]["model"], "ЗАПОЛНИТЬ")
            self.assertEqual(tasks[name]["fallback"], "head")
        for name in ("classify_request", "research", "options", "strategy", "critic"):
            self.assertEqual(tasks[name]["executor"], "head")

    def test_prices(self) -> None:
        prices = load_json(CONFIG / "prices.json")
        self.assertIn("_note", prices)
        self.assertTrue(prices["models"])
        for name, entry in prices["models"].items():
            self.assertIsInstance(entry["input"], (int, float), name)
            self.assertIsInstance(entry["output"], (int, float), name)
            self.assertTrue(entry["source"].startswith("https://"), name)
        routing = load_json(CONFIG / "routing.json")
        for task in routing["tasks"].values():
            if task.get("executor") == "helper":
                self.assertIn(task["model"], prices["models"])

    def test_modes(self) -> None:
        modes = load_json(CONFIG / "modes.json")
        expected = {
            "express": (4, 3, False),
            "standard": (5, 5, False),
            "deep": (6, 7, True),
        }
        for name, (options, questions, wait) in expected.items():
            mode = modes[name]
            self.assertIsInstance(mode["token_budget"], int)
            self.assertGreater(mode["token_budget"], 0)
            self.assertEqual(mode["options_count"], options)
            self.assertEqual(mode["research_questions"], questions)
            self.assertEqual(mode["wait_plan_confirmation"], wait)
            self.assertEqual(mode["warn_at"], 0.8)
        self.assertIn("_note", modes)

    def test_sources(self) -> None:
        sources = load_json(CONFIG / "sources.json")
        self.assertEqual(sources["allow"], [])
        self.assertEqual(sources["deny"], [])
        self.assertIn("_note", sources)

    def test_formats(self) -> None:
        formats = load_json(CONFIG / "formats.json")
        self.assertEqual(formats["video_max_minutes"], 30)
        self.assertEqual(formats["audio_max_minutes"], 90)
        self.assertEqual(formats["audio_languages"], ["ru", "en"])
        self.assertIn("_note", formats)


class EnvFilesTests(unittest.TestCase):
    """.env.example, .gitignore и requirements."""

    def test_env_example(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_API_KEY=", text)
        for line in text.splitlines():
            if not line.startswith("#") and "=" in line:
                self.assertEqual(line.split("=", 1)[1], "", line)  # без значений
        self.assertIn("DeepSeek", text)
        # №20 ревью: неподключённая модель Jev и её ключ убраны
        self.assertNotIn("TYPESAFE", text)
        self.assertNotIn("Jev", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_gitignore(self) -> None:
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for pattern in (".env", ".venv/", "__pycache__/", "doctor.json", "*.part",
                        "projects/*", "!projects/demo/", "!projects/_template/"):
            self.assertIn(pattern, lines)

    def test_requirements_files(self) -> None:
        files = {
            "requirements.txt": {"markitdown", "trafilatura", "pandas"},
            "requirements-media.txt": {"faster-whisper", "av", "scenedetect"},
            "requirements-ux.txt": {"playwright"},
        }
        for name, expected in files.items():
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines[0].startswith("#"), name)
            packages = {re.split(r"[\[<>=!~\s]", line.strip())[0]
                        for line in lines if line.strip() and not line.startswith("#")}
            self.assertEqual(packages, expected)


class TemplateTests(unittest.TestCase):
    """projects/_template — заготовка нового проекта."""

    def test_project_json(self) -> None:
        payload = load_json(TEMPLATE / "project.json")
        expected = {"name", "created", "request", "question", "goal", "success_metric",
                    "user_role", "recipient", "constraints", "mode", "sources", "modules"}
        self.assertEqual(set(payload), expected)
        for key in ("name", "created", "request", "question", "goal", "success_metric",
                    "user_role", "recipient", "constraints", "mode"):
            self.assertEqual(payload[key], "", key)
        self.assertEqual(payload["sources"], [])
        self.assertEqual(payload["modules"], [])

    def test_empty_dirs_have_gitkeep(self) -> None:
        for name in ("materials", "extracted", "analysis", "drafts", "output", "state/tasks"):
            self.assertTrue((TEMPLATE / name / ".gitkeep").is_file(), name)

    def test_state_md_headers(self) -> None:
        text = (TEMPLATE / "state" / "state.md").read_text(encoding="utf-8")
        for header in STATE_HEADERS:
            self.assertRegex(text, rf"(?m)^##\s*{re.escape(header)}\s*$")

    def test_state_files(self) -> None:
        self.assertEqual((TEMPLATE / "state" / "journal.jsonl").read_text(encoding="utf-8"), "")
        import registry
        for name in ("sources.md", "claims.md", "decisions.md"):
            text = (TEMPLATE / "state" / name).read_text(encoding="utf-8").strip()
            self.assertTrue(text.splitlines()[0].startswith("# "), name)
            # шапка с форматом не должна разбираться как запись реестра
            self.assertEqual(registry.parse_file(TEMPLATE / "state" / name), [], name)
        for name in ("sources.md", "claims.md"):
            text = (TEMPLATE / "state" / name).read_text(encoding="utf-8")
            self.assertIn("Формат записи", text, name)
        self.assertEqual(registry.check_project(TEMPLATE), [])

    def test_state_template_parses(self) -> None:
        text = strateg._state_template("demo", "2026-11-05")
        for header in STATE_HEADERS:
            self.assertRegex(text, rf"(?m)^##\s*{re.escape(header)}\s*$")
        stage, next_step = strateg._state_fields(text)
        self.assertEqual(stage, "S0 — бриф")
        self.assertEqual(next_step, "загрузить материалы в materials/ и сформулировать запрос")

    def test_state_fields_support_old_format(self) -> None:
        stage, next_step = strateg._state_fields("# Проект\n\nЭтап: S2\nДальше: шаг S2\n")
        self.assertEqual(stage, "S2")
        self.assertEqual(next_step, "шаг S2")

    def test_settings_content_is_exact_json(self) -> None:
        expected = (
            '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": '
            '"cd \\"$CLAUDE_PROJECT_DIR\\" && python3 strateg.py status --brief"}]}]}}'
        )
        self.assertEqual(strateg.SETTINGS_CONTENT.strip(), expected)
        payload = json.loads(strateg.SETTINGS_CONTENT)
        hook = payload["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"],
                         'cd "$CLAUDE_PROJECT_DIR" && python3 strateg.py status --brief')

    def test_copy_marker(self) -> None:
        self.assertEqual(strateg.COPY_MARKER,
                         "<!-- копия, правьте оригинал в skills/ или agents/ -->\n")


if __name__ == "__main__":
    unittest.main()
