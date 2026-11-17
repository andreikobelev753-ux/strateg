#!/usr/bin/env python3
"""Документ → markdown через markitdown.

Поддерживаются документы, презентации, PDF, Word и Excel (конвертацию делает
markitdown). Результат — extracted/SRC-xx.md, запись в state/sources.md с типом
«материал пользователя» и событие в журнале. Перед записью текст проверяется
искателем команд из tools/scan.py, номера строк попадают в «Пометки».

Простые текстовые файлы (.md, .txt) читаются напрямую, без markitdown.
Для остальных форматов без markitdown — код 3 и подсказка про setup.
.docx при любой ошибке markitdown (или без него) читается встроенным разборщиком.

Пример:
  python3 tools/extract/docs.py projects/demo materials/report.pdf
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any, List, Optional

EXTRACT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = EXTRACT_DIR.parent
for _path in (str(TOOLS_DIR), str(EXTRACT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import common  # noqa: E402

TOOL = "extract/docs.py"
MISSING_MESSAGE = "нужен модуль markitdown: python3 strateg.py setup"


PLAIN_TEXT = {".md", ".markdown", ".txt", ".text"}


def read_plain_text(path: Path) -> str:
    """Читает простой текст: UTF-8, затем cp1251 (частая кодировка русских файлов)."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def convert_document(path: Path) -> str:
    """Конвертирует документ в markdown; бросает ImportError без markitdown."""
    if path.suffix.lower() in PLAIN_TEXT:
        return read_plain_text(path)
    markitdown = importlib.import_module("markitdown")
    if hasattr(markitdown, "MarkItDown"):
        converter = markitdown.MarkItDown()
        result = converter.convert(str(path))
        for attribute in ("text_content", "text", "markdown"):
            value = getattr(result, attribute, None)
            if isinstance(value, str):
                return value
        if isinstance(result, str):
            return result
    if hasattr(markitdown, "convert"):
        value = markitdown.convert(str(path))
        if isinstance(value, str):
            return value
        for attribute in ("text_content", "text", "markdown"):
            text = getattr(value, attribute, None)
            if isinstance(text, str):
                return text
    raise RuntimeError("markitdown не вернул текст документа")


def _builtin_docx_text(paragraphs: List[Any]) -> str:
    """Текст .docx встроенным разборщиком (абзацы и таблицы из word/document.xml)."""
    if not paragraphs:
        raise RuntimeError("в .docx не найден текст")
    return "\n\n".join(line for line, _hidden in paragraphs)


def build_parser() -> argparse.ArgumentParser:
    """Парсер команды извлечения документа."""
    parser = argparse.ArgumentParser(
        prog="docs.py",
        description="Документ/PDF/Word/Excel → extracted/SRC-xx.md через markitdown.",
    )
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("file", help="исходный документ")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа: конвертирует документ и регистрирует источник."""
    args = build_parser().parse_args(argv)
    project = Path(args.project_dir)
    source_file = common.resolve_input(project, args.file)
    if not project.is_dir():
        print(f"ошибка: проект не найден: {project}", file=sys.stderr)
        return 2
    if not source_file.is_file():
        print(f"ошибка: файл не найден: {source_file}", file=sys.stderr)
        return 2

    is_docx = source_file.suffix.lower() == ".docx"
    builtin_docx = False
    if source_file.suffix.lower() not in PLAIN_TEXT:
        try:
            markitdown = importlib.import_module("markitdown")
            del markitdown
        except ImportError:
            if not is_docx:
                print(MISSING_MESSAGE, file=sys.stderr)
                return 3
            builtin_docx = True  # .docx разбираем и без markitdown

    source_id = common.next_source_id(project)
    key = common.step_key("extract", project, source_file)
    common.start(project, "extract", key, TOOL, note=str(source_file))
    paragraphs = common.scan.docx_paragraphs(source_file) if is_docx else []
    fallback_reason = ""
    try:
        if builtin_docx:
            text = _builtin_docx_text(paragraphs)
        else:
            try:
                text = convert_document(source_file)
            except Exception as exc:  # noqa: BLE001 - markitdown падает по-разному
                if not is_docx:
                    raise
                # Любая ошибка markitdown на .docx (нет дополнения [docx],
                # MissingDependencyException, битый стиль) — встроенный разборщик.
                fallback_reason = f"{type(exc).__name__}: {str(exc)[:200]}"
                try:
                    text = _builtin_docx_text(paragraphs)
                except Exception as builtin_exc:  # noqa: BLE001
                    raise RuntimeError(f"markitdown: {exc}; встроенный разборщик: "
                                       f"{builtin_exc}") from exc
                builtin_docx = True
    except Exception as exc:  # noqa: BLE001 - конвертер может падать по-разному
        common.error(project, "extract", key, TOOL, note=str(exc)[:300])
        print(f"ошибка конвертации: {exc}", file=sys.stderr)
        return 1
    text, masked = common.scan.mask_pii(text)
    if not text.endswith("\n"):
        text += "\n"

    lines = common.scan_line_numbers(text)
    target = project / "extracted" / f"{source_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    notes = [common.notes_for(lines)] if lines else []
    hidden = [line for line, is_hidden in paragraphs if is_hidden]
    if hidden:
        notes.append("скрытый текст в docx (белый, мелкий или невидимый): "
                     + "; ".join("«" + line[:60] + "»" for line in hidden[:3]))
    if masked:
        notes.append(f"скрыто телефонов и адресов почты: {masked}")
    common.append_source(
        project,
        source_id,
        what=f"документ {source_file.name}",
        where=str(args.file),
        type_="материал пользователя",
        copy_path=f"extracted/{source_id}.md",
        notes="; ".join(notes) or "—",
    )
    common.done(project, "extract", key, TOOL, note=source_id)
    print(f"Сохранено: {target}")
    print(f"Источник: {source_id}")
    if builtin_docx:
        print("Разобрано встроенным разборщиком .docx (без markitdown): только текст абзацев и таблиц.")
        if fallback_reason:
            print(f"Причина: markitdown не справился ({fallback_reason}).")
    if hidden:
        print(f"Внимание: скрытый текст в документе — {len(hidden)} абзац(ев). Это может быть подложенная команда.")
    if masked:
        print(f"Скрыто телефонов и адресов почты: {masked}. Имена людей обезличьте сами (правило 9).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
