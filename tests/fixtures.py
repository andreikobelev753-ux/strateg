"""Общие фикстуры для тестов «Стратега»."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
EXTRACT = TOOLS / "extract"
TESTS = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(EXTRACT) not in sys.path:
    sys.path.insert(0, str(EXTRACT))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

VALID_SOURCES = """# Источники

## SRC-01
- Что: Годовой отчёт компании за 2025 год
- Где: https://example.com/report.pdf
- Тип: открытый источник
- Дата обращения: 2026-11-01
- Копия: extracted/SRC-01.md
- Можно использовать: да
- Пометки: —

## SRC-02
- Что: Интервью с руководителем продукта
- Где: материалы пользователя
- Тип: материал пользователя
- Дата обращения: 2026-10-20
- Копия: extracted/SRC-02.md
- Можно использовать: да
- Пометки: —
"""

VALID_CLAIMS = """# Утверждения

## CLM-01
- Утверждение: Выручка выросла на 12% за год
- Статус: факт
- Источник: SRC-01, с. 12
- Опирается на: —
- Дата: 2026-11-02
- Состояние: действует

## CLM-02
- Утверждение: Рост даёт возможность увеличить бюджет найма
- Статус: вывод агента
- Источник: —
- Опирается на: CLM-01
- Дата: 2026-11-03
- Состояние: действует

## CLM-03
- Утверждение: Гипотеза о ёмкости рынка
- Статус: гипотеза
- Источник: —
- Опирается на: CLM-02
- Дата: 2026-11-03
- Состояние: действует
"""

VALID_DECISIONS = """# Решения

## DEC-01
- Решение: Увеличить бюджет найма на 20%
- Почему: CLM-01, CLM-02
- Отвергнуто: Сохранить бюджет без изменений
- Кто и когда: руководитель продукта, 2026-11-05
- Обязательства:
  - [ ] запустить тест онбординга · ответственный · 2026-12-01 · D7 новых
  - [x] выполненное обязательство · ответственный · 2026-11-25 · метрика
- Пересмотреть, если: 2027-02-01; D30 ниже 22%
- Состояние: действует
"""


def write(path: Path, text: str) -> Path:
    """Пишет текст, создавая родительские каталоги."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_project(root: Path, name: str = "demo") -> Path:
    """Создаёт проект с корректными реестрами."""
    project = root / name
    for subdir in ("materials", "extracted", "analysis", "state/tasks", "drafts", "output"):
        (project / subdir).mkdir(parents=True, exist_ok=True)
    write(project / "project.yaml", f"name: {name}\ntitle: {name}\nstage: S1\n")
    write(project / "state" / "state.md",
          f"# Проект: {name}\n\nЭтап: S1\nДальше: собрать данные\n")
    write(project / "state" / "sources.md", VALID_SOURCES)
    write(project / "state" / "claims.md", VALID_CLAIMS)
    write(project / "state" / "decisions.md", VALID_DECISIONS)
    (project / "state" / "journal.jsonl").write_text("", encoding="utf-8")
    return project


def make_bare_project(root: Path, name: str = "project") -> Path:
    """Создаёт пустой проект: каталоги и реестры без записей."""
    project = root / name
    for subdir in ("materials", "extracted", "analysis", "state/tasks", "drafts", "output"):
        (project / subdir).mkdir(parents=True, exist_ok=True)
    write(project / "state" / "sources.md", "# Источники\n")
    write(project / "state" / "claims.md", "# Утверждения\n")
    write(project / "state" / "decisions.md", "# Решения\n")
    (project / "state" / "journal.jsonl").write_text("", encoding="utf-8")
    return project


def run_script(script: Path, *args: str, env: dict = None, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    """Запускает скрипт в отдельном процессе."""
    environment = os.environ.copy()
    environment.setdefault("STRATEG_NO_GITLEAKS", "1")
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=environment,
    )


def run_tool(name: str, *args: str, env: dict = None) -> subprocess.CompletedProcess:
    """Запускает tools/<name>."""
    return run_script(TOOLS / name, *args, env=env)
