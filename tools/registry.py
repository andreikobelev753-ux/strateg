#!/usr/bin/env python3
"""Реестры проекта «Стратег»: разбор и проверка sources.md, claims.md, decisions.md.

Команды:
  check    — проверить номера, ссылки, статусы и состояния; код 1, если есть проблемы;
  impact   — цепочка «источник → утверждения → решения»;
  next-id  — следующий свободный номер SRC/CLM/DEC;
  stale    — устаревшие источники и решения с наступившей датой пересмотра.

Примеры:
  python3 tools/registry.py check   projects/demo
  python3 tools/registry.py impact  projects/demo SRC-03
  python3 tools/registry.py impact  projects/demo CLM-012
  python3 tools/registry.py next-id projects/demo CLM
  python3 tools/registry.py stale   projects/demo --days 180
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CLAIM_STATUSES = ["факт", "вывод агента", "гипотеза", "прогноз", "синтетика", "пробел"]
CLAIM_STATES = ["действует", "исправлено", "отозвано"]
DECISION_STATES = ["действует", "пересмотрено"]
KIND_NAMES = {"SRC": "источник", "CLM": "утверждение", "DEC": "решение"}

REQUIRED_FIELDS = {
    "SRC": ["Что", "Где", "Тип", "Дата обращения", "Копия", "Можно использовать"],
    "CLM": ["Утверждение", "Статус", "Источник", "Опирается на", "Дата", "Состояние"],
    "DEC": ["Решение", "Почему", "Отвергнуто", "Кто и когда", "Обязательства",
            "Пересмотреть, если", "Состояние"],
}

RECORD_RE = re.compile(r"^##\s+(SRC|CLM|DEC)-(\d+)\s*$")
FIELD_RE = re.compile(r"^-\s+(?!\[)([^:]+):\s*(.*)$")
COMMIT_RE = re.compile(r"^\s+-\s*\[( |x|X)\]\s*(.+)$")
REF_RE = re.compile(r"\b((?:SRC|CLM|DEC)-\d+)\b")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
STATE_RE = re.compile(r"^(исправлено|отозвано|пересмотрено)\s+(\d{4}-\d{2}-\d{2}):\s*(\S.*)$")


def _parse_date(value: str) -> Optional[date]:
    """Разбирает дату ГГГГ-ММ-ДД, возвращает None при ошибке."""
    try:
        return date.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None


def parse_file(path: Any) -> List[Dict[str, Any]]:
    """Разбирает один файл реестра и возвращает записи."""
    file_path = Path(path)
    records: List[Dict[str, Any]] = []
    if not file_path.exists():
        return records
    current: Optional[Dict[str, Any]] = None
    in_commitments = False
    with file_path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            header = RECORD_RE.match(line.strip())
            if header:
                current = {
                    "id": f"{header.group(1)}-{header.group(2)}",
                    "kind": header.group(1),
                    "num": int(header.group(2)),
                    "fields": {},
                    "commitments": [],
                    "line": lineno,
                }
                records.append(current)
                in_commitments = False
                continue
            if current is None:
                continue
            if in_commitments:
                commit = COMMIT_RE.match(line)
                if commit:
                    current["commitments"].append(
                        _parse_commitment(commit.group(1), commit.group(2), lineno)
                    )
                    continue
                in_commitments = False
            field = FIELD_RE.match(line)
            if field:
                key = field.group(1).strip()
                value = field.group(2).strip()
                current["fields"][key] = value
                if key == "Обязательства" and not value:
                    in_commitments = True
    return records


def _parse_commitment(flag: str, text: str, lineno: int) -> Dict[str, Any]:
    """Разбирает строку обязательства «- [ ] текст · ответственный · дата · метрика»."""
    parts = [part.strip() for part in text.split("·")]
    return {
        "done": flag.strip().lower() == "x",
        "text": parts[0] if parts else "",
        "responsible": parts[1] if len(parts) > 1 else "",
        "date": parts[2] if len(parts) > 2 else "",
        "metric": parts[3] if len(parts) > 3 else "",
        "line": lineno,
        "raw": text.strip(),
    }


def load(project_dir: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Загружает все три реестра проекта."""
    state = Path(project_dir) / "state"
    return {
        "SRC": parse_file(state / "sources.md"),
        "CLM": parse_file(state / "claims.md"),
        "DEC": parse_file(state / "decisions.md"),
    }


def _ids(records: List[Dict[str, Any]]) -> List[str]:
    return [record["id"] for record in records]


def _refs(value: str) -> List[str]:
    return REF_RE.findall(value or "")


def _check_required(record: Dict[str, Any], problems: List[str]) -> None:
    kind = record["kind"]
    for field in REQUIRED_FIELDS[kind]:
        if field not in record["fields"]:
            problems.append(f"{record['id']}: нет поля «{field}»")


def _check_state(record: Dict[str, Any], problems: List[str]) -> None:
    kind = record["kind"]
    value = record["fields"].get("Состояние", "").strip()
    if not value:
        return
    allowed = CLAIM_STATES if kind == "CLM" else DECISION_STATES
    if value == "действует":
        return
    match = STATE_RE.match(value)
    if not match:
        expected = "действует / исправлено ГГГГ-ММ-ДД: причина" if kind == "CLM" else "действует / пересмотрено ГГГГ-ММ-ДД: причина"
        problems.append(f"{record['id']}: неверное состояние «{value}» (ожидается: {expected})")
        return
    word, date_text, reason = match.group(1), match.group(2), match.group(3).strip()
    if word not in allowed:
        problems.append(f"{record['id']}: состояние «{word}» недопустимо для {KIND_NAMES[kind]}")
        return
    if _parse_date(date_text) is None:
        problems.append(f"{record['id']}: в состоянии неверная дата «{date_text}»")
    if not reason:
        problems.append(f"{record['id']}: в состоянии нет причины")


def _check_date_field(record: Dict[str, Any], field: str, problems: List[str]) -> None:
    value = record["fields"].get(field, "").strip()
    if not value:
        return
    if _parse_date(value) is None:
        problems.append(f"{record['id']}: неверная дата в «{field}»: «{value}»")


def _check_refs(record: Dict[str, Any], field: str, expected: str,
                known: Dict[str, str], problems: List[str]) -> List[str]:
    """Проверяет ссылки в поле; возвращает список id."""
    value = record["fields"].get(field, "")
    refs = _refs(value)
    for ref in refs:
        if ref not in known:
            problems.append(f"{record['id']}: ссылка на несуществующую запись {ref} (поле «{field}»)")
        elif not ref.startswith(expected + "-"):
            problems.append(f"{record['id']}: в поле «{field}» ожидается {expected}-ссылка, найдена {ref}")
    return refs


def check_project(project_dir: Any) -> List[str]:
    """Проверяет реестры проекта, возвращает список проблем."""
    data = load(project_dir)
    problems: List[str] = []

    for kind in ("SRC", "CLM", "DEC"):
        records = data[kind]
        counts: Dict[str, int] = {}
        for record in records:
            counts[record["id"]] = counts.get(record["id"], 0) + 1
        for record_id, count in counts.items():
            if count > 1:
                problems.append(f"{record_id}: номер повторяется ({count} записи)")

    known: Dict[str, str] = {}
    for kind in ("SRC", "CLM", "DEC"):
        for record in data[kind]:
            known.setdefault(record["id"], kind)

    for record in data["SRC"]:
        _check_required(record, problems)
        _check_date_field(record, "Дата обращения", problems)

    for record in data["CLM"]:
        _check_required(record, problems)
        status = record["fields"].get("Статус", "").strip()
        if status and status not in CLAIM_STATUSES:
            problems.append(f"{record['id']}: недопустимый статус «{status}»")
        _check_state(record, problems)
        _check_date_field(record, "Дата", problems)
        source_refs = _check_refs(record, "Источник", "SRC", known, problems)
        basis_refs = _check_refs(record, "Опирается на", "CLM", known, problems)
        if status == "факт" and not source_refs:
            problems.append(f"{record['id']}: у «факт» должен быть Источник с существующим SRC")
        if status == "вывод агента" and not basis_refs:
            problems.append(
                f"{record['id']}: у «вывод агента» в «Опирается на» должна быть хотя бы одна ссылка на CLM"
            )

    for record in data["DEC"]:
        _check_required(record, problems)
        _check_state(record, problems)
        _check_refs(record, "Почему", "CLM", known, problems)
        why_refs = _refs(record["fields"].get("Почему", ""))
        if not why_refs:
            problems.append(f"{record['id']}: в «Почему» нет ссылок на CLM")
        when = record["fields"].get("Кто и когда", "")
        dates = DATE_RE.findall(when)
        if not dates:
            problems.append(f"{record['id']}: в «Кто и когда» нет даты")
        else:
            for item in dates:
                if _parse_date(item) is None:
                    problems.append(f"{record['id']}: неверная дата в «Кто и когда»: «{item}»")
        review = record["fields"].get("Пересмотреть, если", "")
        review_dates = DATE_RE.findall(review)
        if not review_dates:
            problems.append(f"{record['id']}: в «Пересмотреть, если» нет даты")
        else:
            for item in review_dates:
                if _parse_date(item) is None:
                    problems.append(f"{record['id']}: неверная дата пересмотра: «{item}»")
        for commitment in record["commitments"]:
            if not commitment["date"]:
                problems.append(
                    f"{record['id']}: у обязательства нет даты (строка {commitment['line']})"
                )
            elif _parse_date(commitment["date"]) is None:
                problems.append(
                    f"{record['id']}: неверная дата обязательства «{commitment['date']}»"
                )
    return problems


def _find(records: List[Dict[str, Any]], record_id: str) -> Optional[Dict[str, Any]]:
    for record in records:
        if record["id"] == record_id:
            return record
    return None


def impact_data(project_dir: Any, target: str) -> Dict[str, Any]:
    """Строит цепочку влияния источника или утверждения."""
    target = (target or "").strip().upper()
    match = re.fullmatch(r"(SRC|CLM|DEC)-(\d+)", target)
    if not match:
        raise ValueError(f"неверный идентификатор: {target}")
    data = load(project_dir)
    records = {kind: _find(data[kind], target) for kind in ("SRC", "CLM", "DEC")}
    record = records[match.group(1)]
    if record is None:
        raise KeyError(target)
    if match.group(1) == "DEC":
        raise ValueError("impact поддерживает SRC и CLM")

    claims = data["CLM"]
    decisions = data["DEC"]

    def dep_refs(claim: Dict[str, Any]) -> List[str]:
        return _refs(claim["fields"].get("Опирается на", ""))

    selected: List[Dict[str, Any]] = []
    if match.group(1) == "SRC":
        selected = [c for c in claims if target in _refs(c["fields"].get("Источник", ""))]
    elif match.group(1) == "CLM":
        selected = [c for c in claims if target in dep_refs(c)]

    selected_ids = {c["id"] for c in selected}
    changed = True
    while changed:
        changed = False
        for claim in claims:
            if claim["id"] in selected_ids:
                continue
            if any(ref in selected_ids for ref in dep_refs(claim)):
                selected.append(claim)
                selected_ids.add(claim["id"])
                changed = True

    # решение затронуто, если опирается на любое затронутое утверждение,
    # в том числе на само утверждение, с которого начали
    decision_basis = set(selected_ids)
    if match.group(1) == "CLM":
        decision_basis.add(target)
    used_decisions = [
        decision for decision in decisions
        if any(ref in decision_basis for ref in _refs(decision["fields"].get("Почему", "")))
    ]

    selected.sort(key=lambda item: item["num"])
    used_decisions.sort(key=lambda item: item["num"])
    return {
        "target": record,
        "claims": selected,
        "decisions": used_decisions,
        "kind": match.group(1),
    }


def _mark(record: Dict[str, Any]) -> str:
    state = record["fields"].get("Состояние", "").strip()
    if state.startswith("отозвано"):
        return f" [ОТОЗВАНО: {state[len('отозвано'):].strip()}]"
    if state and state != "действует":
        return f" [{state}]"
    return ""


def _short(value: str, limit: int = 160) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _print_impact(project_dir: Any, target: str) -> int:
    try:
        data = impact_data(project_dir, target)
    except (ValueError, KeyError):
        print(f"Запись {target} не найдена", file=sys.stderr)
        return 1
    record = data["target"]
    print(f"Цель: {record['id']} ({KIND_NAMES[data['kind']]}){_mark(record)}")
    title = record["fields"].get("Что") or record["fields"].get("Утверждение") or record["fields"].get("Решение", "")
    if title:
        print(f"  {_short(title)}")
    print("Утверждения:")
    if data["claims"]:
        for claim in data["claims"]:
            status = claim["fields"].get("Статус", "")
            print(f"  {claim['id']} ({status}){_mark(claim)} — {_short(claim['fields'].get('Утверждение', ''))}")
    else:
        print("  нет")
    print("Решения:")
    if data["decisions"]:
        for decision in data["decisions"]:
            print(f"  {decision['id']}{_mark(decision)} — {_short(decision['fields'].get('Решение', ''))}")
    else:
        print("  нет")
    return 0


def next_id(project_dir: Any, prefix: str) -> str:
    """Следующий свободный номер для SRC, CLM или DEC."""
    prefix = (prefix or "").strip().upper()
    if prefix not in ("SRC", "CLM", "DEC"):
        raise ValueError(f"неизвестный префикс: {prefix}")
    data = load(project_dir)
    best = max([record["num"] for record in data[prefix]] or [0])
    # Номера не переиспользуются и после отката: учитываем записи из снимков в drafts/,
    # в том числе из ветки, от которой откатились (папки before-restore).
    file_name = {"SRC": "sources.md", "CLM": "claims.md", "DEC": "decisions.md"}[prefix]
    drafts = Path(project_dir) / "drafts"
    if drafts.is_dir():
        for registry_file in drafts.glob(f"*/state/{file_name}"):
            for record in parse_file(registry_file):
                if record["kind"] == prefix:
                    best = max(best, record["num"])
    number = best + 1
    width = 3 if prefix == "CLM" else 2
    width = max(width, len(str(number)))
    return f"{prefix}-{number:0{width}d}"


def stale_data(project_dir: Any, days: int) -> Dict[str, Any]:
    """Источники старше days дней и решения с наступившей датой пересмотра."""
    data = load(project_dir)
    today = date.today()
    sources = []
    for record in data["SRC"]:
        raw = record["fields"].get("Дата обращения", "")
        parsed = _parse_date(raw)
        if parsed is None:
            continue
        age = (today - parsed).days
        if age > days:
            sources.append({"record": record, "date": raw, "days": age})
    decisions = []
    for record in data["DEC"]:
        raw = record["fields"].get("Пересмотреть, если", "")
        dates = DATE_RE.findall(raw)
        if not dates:
            continue
        parsed = _parse_date(dates[0])
        if parsed is None:
            continue
        if parsed <= today:
            decisions.append({"record": record, "date": dates[0], "overdue": (today - parsed).days})
    return {"sources": sources, "decisions": decisions, "days": days}


def _print_stale(project_dir: Any, days: int) -> None:
    data = stale_data(project_dir, days)
    print(f"Устаревшие источники (старше {days} дней):")
    if data["sources"]:
        for item in data["sources"]:
            record = item["record"]
            print(
                f"  {record['id']} — {_short(record['fields'].get('Что', ''))} "
                f"(дата обращения {item['date']}, {item['days']} дн.)"
            )
    else:
        print("  нет")
    print("Решения с наступившей датой пересмотра:")
    if data["decisions"]:
        for item in data["decisions"]:
            record = item["record"]
            print(
                f"  {record['id']} — {_short(record['fields'].get('Решение', ''))} "
                f"(дата пересмотра {item['date']}, просрочено {item['overdue']} дн.)"
            )
    else:
        print("  нет")


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер команд реестров."""
    parser = argparse.ArgumentParser(
        prog="registry.py",
        description="Разбор и проверка реестров проекта: sources.md, claims.md, decisions.md.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="проверить реестры")
    p_check.add_argument("project_dir", help="папка проекта")

    p_impact = sub.add_parser("impact", help="цепочка влияния SRC/CLM")
    p_impact.add_argument("project_dir", help="папка проекта")
    p_impact.add_argument("target", help="SRC-xx или CLM-xx")

    p_next = sub.add_parser("next-id", help="следующий свободный номер")
    p_next.add_argument("project_dir", help="папка проекта")
    p_next.add_argument("prefix", choices=["SRC", "CLM", "DEC", "src", "clm", "dec"],
                        help="префикс записи")

    p_stale = sub.add_parser("stale", help="устаревшие источники и даты пересмотра")
    p_stale.add_argument("project_dir", help="папка проекта")
    p_stale.add_argument("--days", type=int, required=True, help="порог устаревания, дней")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа CLI реестров."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "check":
            problems = check_project(args.project_dir)
            if problems:
                print("Проблемы в реестрах:")
                for problem in problems:
                    print(f"  {problem}")
                print(f"Всего проблем: {len(problems)}")
                return 1
            print("Проблем не найдено.")
            return 0
        if args.cmd == "impact":
            return _print_impact(args.project_dir, args.target)
        if args.cmd == "next-id":
            print(next_id(args.project_dir, args.prefix))
            return 0
        if args.cmd == "stale":
            _print_stale(args.project_dir, args.days)
            return 0
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
