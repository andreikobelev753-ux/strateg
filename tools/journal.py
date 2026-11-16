#!/usr/bin/env python3
"""Журнал событий проекта «Стратег».

Журнал — файл ``state/journal.jsonl``: одна JSON-запись на строку, файл только
дополняется. Статусы: start, done, error, retry, fallback, skip, pause, restore.

Примеры:
  python3 tools/journal.py start   projects/demo S1.2 "extract:materials/report.pdf" --tool markitdown
  python3 tools/journal.py done    projects/demo S1.2 "extract:materials/report.pdf" --tokens-in 1200 --tokens-out 340 --cost-usd 0.0021
  python3 tools/journal.py is-done projects/demo "extract:materials/report.pdf"
  python3 tools/journal.py pending projects/demo
  python3 tools/journal.py summary projects/demo
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATUSES = ["start", "done", "error", "retry", "fallback", "skip", "pause", "restore"]
CLI_STATUSES = [s for s in STATUSES if s != "restore"]
INTERRUPTED = {"start", "retry"}


def now_iso() -> str:
    """Текущее локальное время в ISO-формате с часовым поясом."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def journal_path(project_dir: Any) -> Path:
    """Путь к журналу проекта."""
    return Path(project_dir) / "state" / "journal.jsonl"


def append_event(
    project_dir: Any,
    status: str,
    stage: str,
    key: str,
    *,
    tool: Optional[str] = None,
    model: Optional[str] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    note: str = "",
    chat: str = "main",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Дописывает событие в журнал и возвращает запись.

    extra — дополнительные поля события, например rollback_to у restore.
    """
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус: {status}")
    record = {
        "ts": now_iso(),
        "chat": chat,
        "stage": stage,
        "key": key,
        "status": status,
        "tool": tool,
        "model": model,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_usd": float(cost_usd or 0.0),
        "note": note,
    }
    for field, value in (extra or {}).items():
        if field not in record:
            record[field] = value
    path = journal_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


def read_events(project_dir: Any) -> List[Dict[str, Any]]:
    """Читает журнал; битые строки пропускает с предупреждением."""
    path = journal_path(project_dir)
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"журнал: строка {lineno} не JSON, пропущена", file=sys.stderr)
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def line_count(project_dir: Any = None, path: Any = None) -> int:
    """Число строк в журнале. Снимок запоминает его как точку, к которой откатываться."""
    file_path = Path(path) if path is not None else journal_path(project_dir)
    if not file_path.exists():
        return 0
    with file_path.open("rb") as fh:
        return sum(1 for _ in fh)


def _read_numbered(project_dir: Any) -> List[Tuple[int, Dict[str, Any]]]:
    """События журнала вместе с номерами строк в файле."""
    path = journal_path(project_dir)
    if not path.exists():
        return []
    result: List[Tuple[int, Dict[str, Any]]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                result.append((lineno, event))
    return result


def effective_events(project_dir: Any) -> List[Dict[str, Any]]:
    """События, которые действуют после всех откатов.

    Журнал только дополняется и никогда не откатывается: так в нём остаются все
    действия и все расходы. Событие restore с полем rollback_to=N значит
    «действующее состояние — такое, каким оно было после строки N журнала».
    Шаги, сделанные между снимком и откатом, остаются в журнале, но больше не
    считаются выполненными. Откаты могут быть вложенными: каждое событие ссылается
    на предыдущее действующее событие, получается дерево, а действующая ветка —
    путь от последнего события к корню.
    """
    nodes: List[Tuple[Dict[str, Any], int]] = []  # (событие, индекс родителя или -1)
    lines: List[int] = []   # номера строк обработанных событий, по возрастанию
    heads: List[int] = []   # индекс действующей вершины после этой строки
    head = -1
    for lineno, event in _read_numbered(project_dir):
        parent = head
        rollback = event.get("rollback_to") if event.get("status") == "restore" else None
        if isinstance(rollback, int) and not isinstance(rollback, bool) and 0 <= rollback < lineno:
            position = bisect.bisect_right(lines, rollback) - 1
            parent = heads[position] if position >= 0 else -1
        nodes.append((event, parent))
        head = len(nodes) - 1
        lines.append(lineno)
        heads.append(head)
    chain: List[Dict[str, Any]] = []
    index = head
    while index != -1:
        event, parent = nodes[index]
        chain.append(event)
        index = parent
    chain.reverse()
    return chain


def last_events(project_dir: Any) -> "Dict[str, Dict[str, Any]]":
    """Последнее действующее событие по каждому ключу (с учётом откатов)."""
    result: Dict[str, Dict[str, Any]] = {}
    for event in effective_events(project_dir):
        key = event.get("key")
        if key is None:
            continue
        result[key] = event
    return result


def is_done(project_dir: Any, key: str) -> bool:
    """Выполнен ли шаг: последний действующий статус ключа — done (с учётом откатов)."""
    events = [e for e in effective_events(project_dir) if e.get("key") == key]
    if not events:
        return False
    return events[-1].get("status") == "done"


def pending_steps(project_dir: Any) -> List[Dict[str, Any]]:
    """Ключи, у которых последний статус start или retry."""
    pending = [e for e in last_events(project_dir).values() if e.get("status") in INTERRUPTED]
    pending.sort(key=lambda e: str(e.get("ts", "")))
    return pending


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def summary(project_dir: Any) -> Dict[str, Any]:
    """Сводка по этапам: интервал, токены, стоимость."""
    events = read_events(project_dir)
    stages: Dict[str, Dict[str, Any]] = {}
    for event in events:
        stage = str(event.get("stage") or "—")
        item = stages.setdefault(
            stage,
            {"stage": stage, "events": 0, "first": None, "last": None,
             "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
        )
        item["events"] += 1
        ts = _parse_ts(event.get("ts"))
        if ts is not None:
            if item["first"] is None or ts < item["first"]:
                item["first"] = ts
            if item["last"] is None or ts > item["last"]:
                item["last"] = ts
        item["tokens_in"] += int(event.get("tokens_in") or 0)
        item["tokens_out"] += int(event.get("tokens_out") or 0)
        item["cost_usd"] += float(event.get("cost_usd") or 0.0)

    ordered = sorted(
        stages.values(),
        key=lambda item: item["first"] or datetime.max.astimezone(),
    )
    totals = {
        "events": len(events),
        "tokens_in": sum(item["tokens_in"] for item in ordered),
        "tokens_out": sum(item["tokens_out"] for item in ordered),
        "cost_usd": sum(item["cost_usd"] for item in ordered),
    }
    return {"stages": ordered, "totals": totals}


def _print_summary(project_dir: Any) -> None:
    data = summary(project_dir)
    if not data["stages"]:
        print("Журнал пуст.")
        return
    print("По этапам:")
    for item in data["stages"]:
        first, last = item["first"], item["last"]
        if first is not None and last is not None:
            interval = f"{first.strftime('%H:%M:%S')}–{last.strftime('%H:%M:%S')} ({_format_duration((last - first).total_seconds())})"
        else:
            interval = "время не определено"
        print(
            f"  {item['stage']}: событий {item['events']}, {interval}, "
            f"токены {item['tokens_in']}/{item['tokens_out']}, "
            f"стоимость ${item['cost_usd']:.4f}"
        )
    totals = data["totals"]
    print(
        f"Итого: событий {totals['events']}, этапов {len(data['stages'])}, "
        f"токены {totals['tokens_in']}/{totals['tokens_out']}, "
        f"стоимость ${totals['cost_usd']:.4f}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер команд журнала."""
    parser = argparse.ArgumentParser(
        prog="journal.py",
        description="Журнал событий проекта (state/journal.jsonl, только дополнение).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Статусы: start, done, error, retry, fallback, skip, pause, restore.\n"
            "is-done возвращает 0, если последний действующий статус шага — done, иначе 1.\n"
            "Откат (restore с rollback_to) делает недействительными шаги после снимка,\n"
            "но не удаляет их из журнала: расходы на них остаются в summary."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for status in CLI_STATUSES:
        p = sub.add_parser(status, help=f"добавить событие {status}")
        p.add_argument("project_dir", help="папка проекта")
        p.add_argument("stage", help="этап, например S2.2")
        p.add_argument("key", help="ключ шага, например extract:materials/report.pdf")
        p.add_argument("--tool", default=None, help="инструмент")
        p.add_argument("--model", default=None, help="модель")
        p.add_argument("--tokens-in", type=int, default=0, help="входные токены")
        p.add_argument("--tokens-out", type=int, default=0, help="выходные токены")
        p.add_argument("--cost-usd", type=float, default=0.0, help="стоимость, USD")
        p.add_argument("--note", default="", help="примечание")
        p.add_argument("--chat", default="main", help="имя чата (по умолчанию main)")

    p_done = sub.add_parser("is-done", help="проверить, выполнен ли шаг (код 0/1)")
    p_done.add_argument("project_dir", help="папка проекта")
    p_done.add_argument("key", help="ключ шага")

    p_pending = sub.add_parser("pending", help="показать прерванные шаги")
    p_pending.add_argument("project_dir", help="папка проекта")

    p_sum = sub.add_parser("summary", help="сводка по этапам")
    p_sum.add_argument("project_dir", help="папка проекта")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа CLI журнала."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd in CLI_STATUSES:
            record = append_event(
                args.project_dir,
                args.cmd,
                args.stage,
                args.key,
                tool=args.tool,
                model=args.model,
                tokens_in=args.tokens_in,
                tokens_out=args.tokens_out,
                cost_usd=args.cost_usd,
                note=args.note,
                chat=args.chat,
            )
            print(f"записано: {record['status']} {record['key']}")
            return 0
        if args.cmd == "is-done":
            return 0 if is_done(args.project_dir, args.key) else 1
        if args.cmd == "pending":
            items = pending_steps(args.project_dir)
            if not items:
                print("Прерванных шагов нет.")
                return 0
            print("Прерванные шаги:")
            for event in items:
                print(f"  {event.get('key')}  (этап {event.get('stage')}, {event.get('ts')})")
            return 0
        if args.cmd == "summary":
            _print_summary(args.project_dir)
            return 0
    except OSError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
