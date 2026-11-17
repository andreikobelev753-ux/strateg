#!/usr/bin/env python3
"""Общие помощники извлечения: номера источников, записи sources.md, журнал.

Модуль служебный: используется tools/extract/*.py, отдельного CLI нет.
"""

from __future__ import annotations

import hashlib
import json
import sys
from urllib.parse import urlparse
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


CONFIG_DIR = TOOLS_DIR.parent / "config"


def _load_lists(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, (dict, list)) else {}


def _matches(url: str, host: str, pattern: str) -> bool:
    """Шаблон: домен (с поддоменами), *.домен или начало адреса с http(s)://."""
    pattern = str(pattern).strip().lower()
    if not pattern:
        return False
    if pattern.startswith(("http://", "https://")):
        return url.lower().startswith(pattern)
    pattern = pattern.lstrip("*.").rstrip("/")
    return host == pattern or host.endswith("." + pattern)


def source_policy(project_dir: Any, url: str) -> str:
    """Проверяет адрес по спискам config/sources.json и project.json («sources»).

    Возвращает причину запрета или пустую строку. deny сильнее allow; пустой allow —
    любые открытые источники. В project.json «sources» — {"allow": [...], "deny": [...]}
    или просто список разрешённых сайтов.
    """
    host = (urlparse(url).hostname or "").lower()
    allow: List[str] = []
    deny: List[str] = []
    base = _load_lists(CONFIG_DIR / "sources.json")
    if isinstance(base, dict):
        allow += list(base.get("allow") or [])
        deny += list(base.get("deny") or [])
    project_json = _load_lists(Path(project_dir) / "project.json")
    own = project_json.get("sources") if isinstance(project_json, dict) else None
    if isinstance(own, list):
        allow += [item for item in own if isinstance(item, str)]
    elif isinstance(own, dict):
        allow += list(own.get("allow") or [])
        deny += list(own.get("deny") or [])
    for pattern in deny:
        if _matches(url, host, pattern):
            return f"адрес в списке запрещённых ({pattern})"
    if allow and not any(_matches(url, host, pattern) for pattern in allow):
        return "адреса нет в списке разрешённых: " + ", ".join(map(str, allow[:5]))
    return ""


def project_relative(project_dir: Any, path: Any) -> str:
    """«Где» для реестра: путь от папки проекта (materials/…), если файл внутри проекта,
    иначе путь как есть. Один формат для всех extract-скриптов."""
    try:
        return Path(path).resolve().relative_to(Path(project_dir).resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def file_digest(path: Any) -> str:
    """Короткий отпечаток содержимого файла: sha256:<16 hex>."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()[:16]


def find_existing_source(project_dir: Any, where: str = "", digest: str = "") -> dict:
    """Ищет уже заведённый SRC для того же материала.

    Совпадение — тот же отпечаток содержимого (файл мог переехать) или тот же «Где»
    у записи без отпечатка (старые записи, адреса страниц). Тот же путь с другим
    отпечатком — новая версия файла: возвращается {"previous": SRC} без "id".
    """
    found: dict = {}
    for record in registry.parse_file(Path(project_dir) / "state" / "sources.md"):
        if record["kind"] != "SRC":
            continue
        fields = record["fields"]
        record_digest = fields.get("Хеш", "").strip()
        same_place = bool(where) and fields.get("Где", "").strip() == where
        if digest and record_digest == digest:
            return {"id": record["id"], "by": "отпечаток содержимого"}
        if same_place and not record_digest:
            return {"id": record["id"], "by": "путь"}
        if same_place and digest and record_digest != digest:
            found = {"previous": record["id"]}
    return found


def append_source(project_dir: Any, source_id: str, what: str, where: str,
                  type_: str, copy_path: str, notes: str, digest: str = "") -> Path:
    """Дописывает запись источника в state/sources.md (с отпечатком файла, если он есть)."""
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
        + (f"- Хеш: {digest}\n" if digest else "")
        + f"- Пометки: {_one_line(notes)}\n"
    )
    path.write_text(existing + block, encoding="utf-8")
    return path


def report_existing(existing: dict) -> None:
    """Сообщение о повторном extract: номер уже выдан, повторно не разбираем."""
    print(f"Уже в реестре: {existing['id']} (совпадение: {existing['by']}). Повторно не разбираю.")
    print(f"Источник: {existing['id']}")
    print("Нужна новая версия источника (файл или страница изменились) — добавьте --again.")


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
