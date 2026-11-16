#!/usr/bin/env python3
"""CSV/XLSX → профиль таблицы в analysis/<имя>_profile.md.

Профиль: столбцы и типы, число строк, пропуски, столбцы-даты и их диапазон
(период данных), подозрительные значения (отрицательные при положительных
остальных, выбросы за 3 межквартильных размаха) и предупреждение об отсутствии
единиц измерения в названиях числовых столбцов.

CSV читается модулем csv (без pandas). XLSX требует pandas; если его нет —
код 3 и подсказка про setup.

Пример:
  python3 tools/extract/tables.py projects/demo materials/sales.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def resolve_input(project, value):
    """Путь к материалу: как указан, а если такого файла нет — относительно папки проекта."""
    path = Path(value)
    if path.exists() or path.is_absolute():
        return path
    candidate = Path(project) / value
    return candidate if candidate.exists() else path

DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y-%m", "%m.%Y", "%Y-%m-%dT%H:%M:%S",
                "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S")
UNIT_RE = re.compile(
    r"(?:\([^)]*\)|\[[^\]]*\]|%|‰|₽|\$|€|£|руб|долл|евро|тыс|млн|млрд|шт|кг|"
    r"мг|тонн|км|см|мм|час|мин|сек|дн|нед|мес|год|чел|раз|балл|°|"
    r"(?<![a-z])(?:usd|eur|rub|kg|mm|cm|km|min|sec|ms|gb|mb|kb)(?![a-z])|"
    r"гб|мб|кб)",
    re.IGNORECASE,
)


class MissingModule(Exception):
    """Нет необязательного модуля."""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _to_number(value: Any) -> Optional[float]:
    """Пытается превратить значение в число."""
    text = _clean(value).replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        if text.count(",") == 1 and "." not in text:
            try:
                return float(text.replace(",", "."))
            except ValueError:
                return None
    return None


def _to_date(value: Any) -> Optional[date]:
    """Пытается разобрать дату в основных форматах."""
    text = _clean(value)
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def load_csv(path: Path) -> Tuple[List[str], List[List[str]]]:
    """Читает CSV/TSV стандартным модулем csv."""
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        rows = [row for row in reader]
    if not rows:
        raise ValueError("файл пуст")
    header = [str(item).strip() for item in rows[0]]
    body = [row for row in rows[1:] if any(_clean(cell) for cell in row)]
    for row in body:
        if len(row) < len(header):
            row.extend([""] * (len(header) - len(row)))
    return header, [row[:len(header)] for row in body]


def load_xlsx(path: Path) -> Tuple[List[str], List[List[Any]]]:
    """Читает первый лист XLSX через pandas (необязательный модуль)."""
    try:
        pandas = importlib.import_module("pandas")
    except ImportError as exc:
        raise MissingModule("нужен модуль pandas: python3 strateg.py setup") from exc
    try:
        frame = pandas.read_excel(str(path), sheet_name=0)
    except ImportError as exc:
        raise MissingModule("нужен модуль pandas: python3 strateg.py setup") from exc
    except Exception as exc:  # noqa: BLE001 - разные движки Excel
        raise ValueError(f"не удалось прочитать XLSX: {exc}") from exc
    columns = [str(item).strip() for item in frame.columns]
    rows = [list(row) for row in frame.itertuples(index=False, name=None)]
    return columns, rows


def load_table(path: Path) -> Tuple[List[str], List[List[Any]]]:
    """Загружает таблицу по расширению."""
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        return load_csv(path)
    if suffix in (".xlsx", ".xls"):
        return load_xlsx(path)
    raise ValueError(f"неподдерживаемый формат: {suffix or 'без расширения'}")


def column_type(values: Sequence[Any]) -> str:
    """Определяет тип столбца: число, дата или текст."""
    filled = [value for value in values if _clean(value)]
    if not filled:
        return "текст"
    if all(_to_number(value) is not None for value in filled):
        return "число"
    if all(_to_date(value) is not None for value in filled):
        return "дата"
    return "текст"


def percentile(values: Sequence[float], fraction: float) -> float:
    """Линейная интерполяция перцентиля (0..1)."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def has_unit(name: str) -> bool:
    """Есть ли в названии столбца признак единицы измерения."""
    return bool(UNIT_RE.search(name))


def build_profile(name: str, columns: List[str], rows: List[List[Any]]) -> str:
    """Собирает markdown-профиль таблицы."""
    profile: List[str] = [f"# Профиль таблицы: {name}", "",
                          f"Строк: {len(rows)}", f"Столбцов: {len(columns)}", ""]
    profile.extend(["## Столбцы и типы", "", "| Столбец | Тип | Пропуски |",
                    "|---|---|---|"])
    column_values: Dict[str, List[Any]] = {}
    types: Dict[str, str] = {}
    for index, column in enumerate(columns):
        values = [row[index] if index < len(row) else "" for row in rows]
        column_values[column] = values
        kind = column_type(values)
        types[column] = kind
        missing = sum(1 for value in values if not _clean(value))
        profile.append(f"| {column} | {kind} | {missing} |")
    profile.append("")

    profile.extend(["## Даты и период", ""])
    date_columns = [column for column in columns if types[column] == "дата"]
    if date_columns:
        for column in date_columns:
            dates = [value for value in (_to_date(item) for item in column_values[column]) if value]
            if dates:
                profile.append(f"- {column}: {min(dates).isoformat()} … {max(dates).isoformat()}")
    else:
        profile.append("Столбцов, похожих на даты, не найдено.")
    profile.append("")

    profile.extend(["## Подозрительные значения", ""])
    suspicious: List[str] = []
    for column in columns:
        if types[column] != "число":
            continue
        numbers = [value for value in (_to_number(item) for item in column_values[column]) if value is not None]
        if not numbers:
            continue
        negatives = [value for value in numbers if value < 0]
        positives = [value for value in numbers if value > 0]
        if negatives and positives:
            examples = ", ".join(str(value) for value in negatives[:3])
            suspicious.append(
                f"- {column}: отрицательных значений {len(negatives)} при положительных "
                f"остальных (например: {examples})"
            )
        if len(numbers) >= 4:
            q1 = percentile(numbers, 0.25)
            q3 = percentile(numbers, 0.75)
            iqr = q3 - q1
            if iqr > 0:
                low, high = q1 - 3 * iqr, q3 + 3 * iqr
                outliers = [value for value in numbers if value < low or value > high]
                if outliers:
                    examples = ", ".join(str(value) for value in outliers[:3])
                    suspicious.append(
                        f"- {column}: выбросов за 3 IQR: {len(outliers)} (например: {examples})"
                    )
    if suspicious:
        profile.extend(suspicious)
    else:
        profile.append("Не найдено.")
    profile.append("")

    profile.extend(["## Единицы измерения", ""])
    numeric_without_units = [column for column in columns
                             if types[column] == "число" and not has_unit(column)]
    if numeric_without_units:
        profile.append(
            "- В названиях числовых столбцов не видно единиц измерения: "
            + ", ".join(numeric_without_units)
        )
    else:
        profile.append("Числовых столбцов без единиц не найдено.")
    profile.append("")
    return "\n".join(profile)


def build_parser() -> argparse.ArgumentParser:
    """Парсер команды профилирования таблицы."""
    parser = argparse.ArgumentParser(
        prog="tables.py",
        description="CSV/XLSX → analysis/<имя>_profile.md (типы, пропуски, даты, выбросы).",
    )
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("file", help="CSV/TSV или XLSX")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа: строит профиль таблицы."""
    args = build_parser().parse_args(argv)
    project = Path(args.project_dir)
    source = resolve_input(project, args.file)
    if not project.is_dir():
        print(f"ошибка: проект не найден: {project}", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"ошибка: файл не найден: {source}", file=sys.stderr)
        return 2
    try:
        key = "profile:" + source.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        key = f"profile:{source.name}"
    _journal(project, "start", key)
    try:
        columns, rows = load_table(source)
    except MissingModule as exc:
        print(str(exc), file=sys.stderr)
        _journal(project, "error", key, str(exc)[:200])
        return 3
    except (OSError, ValueError) as exc:
        print(f"ошибка чтения таблицы: {exc}", file=sys.stderr)
        _journal(project, "error", key, str(exc)[:200])
        return 1

    markdown = build_profile(source.name, columns, rows)
    target = project / "analysis" / f"{source.stem}_profile.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    print(f"Профиль сохранён: {target}")
    source_id = _register_source(project, source, f"analysis/{target.name}", len(rows), len(columns))
    if source_id:
        print(f"Источник: {source_id}")
    _journal(project, "done", key, f"analysis/{target.name}" + (f"; {source_id}" if source_id else ""))
    return 0


def _register_source(project: Path, source: Path, profile: str, n_rows: int, n_cols: int) -> str:
    """Заводит SRC на таблицу, если её ещё нет в реестре; возвращает номер или пустую строку."""
    try:
        extract_dir = Path(__file__).resolve().parent
        for path in (str(extract_dir.parent), str(extract_dir)):
            if path not in sys.path:
                sys.path.insert(0, path)
        import common  # noqa: WPS433
        try:
            where = source.resolve().relative_to(project.resolve()).as_posix()
        except ValueError:
            where = str(source)
        sources = project / "state" / "sources.md"
        text = sources.read_text(encoding="utf-8", errors="replace") if sources.exists() else ""
        if f"- Где: {where}\n" in text:
            return ""
        source_id = common.next_source_id(project)
        common.append_source(project, source_id, what=f"таблица {source.name}: {n_rows} строк, {n_cols} столбцов",
                             where=where, type_="материал пользователя", copy_path=profile, notes="—")
        return source_id
    except Exception:  # noqa: BLE001 — профиль важнее записи в реестр
        return ""


def _journal(project: Path, status: str, key: str, note: str = "") -> None:
    """Пишет событие в журнал проекта, если журнал доступен; ошибки журнала не мешают профилю."""
    try:
        tools_dir = Path(__file__).resolve().parents[1]
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        import journal  # noqa: WPS433
        journal.append_event(project, status, "extract", key, tool="extract/tables.py", note=note)
    except Exception:  # noqa: BLE001 — журнал вспомогательный
        pass


if __name__ == "__main__":
    sys.exit(main())
