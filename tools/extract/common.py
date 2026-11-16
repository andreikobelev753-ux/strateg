#!/usr/bin/env python3
"""Общие помощники извлечения: номера источников, записи sources.md, журнал.

Модуль служебный: используется tools/extract/*.py, отдельного CLI нет.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable, List, Optional

EXTRACT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = EXTRACT_DIR.parent
for _path in (str(TOOLS_DIR), str(EXTRACT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import journal  # noqa: E402
import registry  # noqa: E402
import scan  # noqa: E402


def next_source_id(project_dir: Any) -> str:
    """Следующий свободный номер источника SRC-xx."""
    return registry.next_id(project_dir, "SRC")


def scan_line_numbers(text: str) -> List[int]:
    """Уникальные номера строк с подозрительными командами ИИ."""
    return sorted({int(item["line"]) for item in scan.scan_text(text)})


def notes_for(lines: Iterable[int]) -> str:
    """Значение поля «Пометки»: номера строк или прочерк."""
    numbers = sorted({int(item) for item in lines})
    if not numbers:
        return "—"
    return "подозрительные строки: " + ", ".join(str(number) for number in numbers)


def _one_line(value: str) -> str:
    """Схлопывает переводы строк, чтобы запись реестра осталась одной строкой."""
    return " ".join(str(value or "").split())


def append_source(project_dir: Any, source_id: str, what: str, where: str,
                  type_: str, copy_path: str, notes: str) -> Path:
    """Дописывает запись источника в state/sources.md."""
    path = Path(project_dir) / "state" / "sources.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    block = (
        f"\n## {source_id}\n"
        f"- Что: {_one_line(what)}\n"
        f"- Где: {_one_line(where)}\n"
        f"- Тип: {_one_line(type_)}\n"
        f"- Дата обращения: {date.today().isoformat()}\n"
        f"- Копия: {_one_line(copy_path)}\n"
        f"- Можно использовать: да\n"
        f"- Пометки: {_one_line(notes)}\n"
    )
    path.write_text(existing + block, encoding="utf-8")
    return path


def step_key(prefix: str, project_dir: Any, path: Any) -> str:
    """Ключ шага для журнала: «prefix:путь от папки проекта», например extract:materials/memo.md.

    Так ключ совпадает с тем, что агент проверяет через journal.py is-done (AGENTS.md, раздел 4).
    Файл вне проекта — по имени файла.
    """
    file_path = Path(path)
    try:
        relative = file_path.resolve().relative_to(Path(project_dir).resolve())
        return f"{prefix}:{relative.as_posix()}"
    except ValueError:
        return f"{prefix}:{file_path.name}"


def start(project_dir: Any, stage: str, key: str, tool: str,
          note: str = "") -> None:
    """Событие start в журнале."""
    journal.append_event(project_dir, "start", stage, key, tool=tool, note=note)


def done(project_dir: Any, stage: str, key: str, tool: str,
         note: str = "") -> None:
    """Событие done в журнале."""
    journal.append_event(project_dir, "done", stage, key, tool=tool, note=note)


def error(project_dir: Any, stage: str, key: str, tool: str,
          note: str = "") -> None:
    """Событие error в журнале."""
    journal.append_event(project_dir, "error", stage, key, tool=tool, note=note)


def ensure_project(project_dir: Any) -> Optional[Path]:
    """Проверяет, что проект существует; иначе None."""
    project = Path(project_dir)
    return project if project.is_dir() else None


def resolve_input(project, value):
    """Путь к материалу: как указан, а если такого файла нет — относительно папки проекта."""
    path = Path(value)
    if path.exists() or path.is_absolute():
        return path
    candidate = Path(project) / value
    return candidate if candidate.exists() else path
