#!/usr/bin/env python3
"""Отчёт о стоимости по state/journal.jsonl.

Считает по этапам и по моделям: число событий, токены, стоимость и время
(от первого до последнего события группы), плюс итог. Печатает отчёт в консоль
и пишет копию в output/cost.md.

Пример:
  python3 tools/cost.py projects/demo
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import journal  # noqa: E402

HEAD_MODEL_LINE = (
    "Токены головной модели в журнал не попадают: их считает среда. Чтобы увидеть "
    "стоимость, передайте их из счётчика среды: strateg.py cost <имя> "
    "--head-in N --head-out M [--head-model claude-opus-5-5]."
)
CONFIG_DIR = TOOLS_DIR.parent / "config"


def _load_json(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def head_section(head_in: Optional[int], head_out: Optional[int], head_model: str,
                 duration: str) -> List[str]:
    """Раздел о головной модели: эквивалент по ценам API и сравнение с тарифами."""
    lines = ["## Головная модель", ""]
    if head_in is None or head_out is None:
        lines.append(HEAD_MODEL_LINE)
        return lines
    prices = _load_json("prices.json").get("models", {})
    price = prices.get(head_model) if isinstance(prices, dict) else None
    lines.append(f"- Модель: {head_model}; токены: вход {head_in}, выход {head_out}; время проекта: {duration}")
    if not isinstance(price, dict):
        lines.append(f"- Цена модели {head_model} не задана в config/prices.json")
        return lines
    api_cost = head_in / 1_000_000 * float(price.get("input", 0)) + head_out / 1_000_000 * float(price.get("output", 0))
    lines.append(f"- По ценам API: ${api_cost:.2f} (без скидки за кэш, поэтому это верхняя оценка)")
    plans = _load_json("plans.json")
    if plans.get("plans"):
        lines.extend(["", "| Тариф | Цена в месяц, $ | Прогон по ценам API, % месячной цены |", "|---|---:|---:|"])
        for plan in plans["plans"]:
            monthly = float(plan.get("monthly_usd") or 0)
            share = (api_cost / monthly * 100) if monthly else 0.0
            lines.append(f"| {plan.get('name')} | {monthly:.0f} | {share:.1f} |")
        lines.extend(["", "Лимиты подписок публикуются не в токенах, поэтому это сравнение с ценой тарифа, а не доля лимита. "
                      f"Тарифы: {plans.get('source', '')}, проверено {plans.get('checked', '—')}."])
    return lines


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _duration(first: Optional[datetime], last: Optional[datetime]) -> str:
    if first is None or last is None:
        return "—"
    total = max(int((last - first).total_seconds()), 0)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _empty_bucket() -> Dict[str, Any]:
    return {"events": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
            "first": None, "last": None}


def _add(bucket: Dict[str, Any], event: Dict[str, Any]) -> None:
    bucket["events"] += 1
    bucket["tokens_in"] += int(event.get("tokens_in") or 0)
    bucket["tokens_out"] += int(event.get("tokens_out") or 0)
    bucket["cost"] += float(event.get("cost_usd") or 0.0)
    stamp = _parse_ts(event.get("ts"))
    if stamp is not None:
        if bucket["first"] is None or stamp < bucket["first"]:
            bucket["first"] = stamp
        if bucket["last"] is None or stamp > bucket["last"]:
            bucket["last"] = stamp


def collect(project_dir: Any) -> Dict[str, Any]:
    """Группирует события журнала по этапам и моделям."""
    stages: Dict[str, Dict[str, Any]] = {}
    models: Dict[str, Dict[str, Any]] = {}
    totals = _empty_bucket()
    events = journal.read_events(project_dir)
    for event in events:
        stage = str(event.get("stage") or "—")
        model = str(event.get("model") or "—")
        _add(stages.setdefault(stage, _empty_bucket()), event)
        _add(models.setdefault(model, _empty_bucket()), event)
        _add(totals, event)
    return {"stages": stages, "models": models, "totals": totals,
            "events": len(events)}


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|")


def _table(groups: Dict[str, Dict[str, Any]], title: str) -> List[str]:
    lines = [f"## {title}", "",
             "| Название | Событий | Токены in | Токены out | Стоимость, $ | Время |",
             "|---|---:|---:|---:|---:|---|"]
    if not groups:
        lines.append("| — | 0 | 0 | 0 | 0.0000 | — |")
        return lines
    for name in sorted(groups):
        bucket = groups[name]
        lines.append(
            f"| {_cell(name)} | {bucket['events']} | {bucket['tokens_in']} | "
            f"{bucket['tokens_out']} | {bucket['cost']:.4f} | "
            f"{_duration(bucket['first'], bucket['last'])} |"
        )
    return lines


def build_report(project_dir: Any, project_name: str, head_in: Optional[int] = None,
                 head_out: Optional[int] = None, head_model: str = "claude-opus-5-5") -> str:
    """Собирает markdown-отчёт о стоимости."""
    data = collect(project_dir)
    totals = data["totals"]
    lines = [f"# Стоимость проекта {project_name}", ""]
    lines.extend(_table(data["stages"], "По этапам"))
    lines.append("")
    lines.extend(_table(data["models"], "По моделям"))
    lines.extend([
        "",
        "## Итого",
        "",
        f"- Событий: {data['events']}",
        f"- Токены: вход {totals['tokens_in']}, выход {totals['tokens_out']}",
        f"- Стоимость: ${totals['cost']:.4f}",
        f"- Время: {_duration(totals['first'], totals['last'])}",
        "",
    ])
    lines.extend(head_section(head_in, head_out, head_model, _duration(totals["first"], totals["last"])))
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Парсер команды отчёта о стоимости."""
    parser = argparse.ArgumentParser(
        prog="cost.py",
        description="Отчёт о стоимости по state/journal.jsonl (этапы, модели, итог).",
    )
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("--head-in", type=int, default=None, help="входные токены головной модели из счётчика среды")
    parser.add_argument("--head-out", type=int, default=None, help="выходные токены головной модели")
    parser.add_argument("--head-model", default="claude-opus-5-5", help="модель для цены (ключ из config/prices.json)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа: печатает отчёт и пишет output/cost.md."""
    args = build_parser().parse_args(argv)
    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"ошибка: проект не найден: {project}", file=sys.stderr)
        return 1
    report = build_report(project, project.name, args.head_in, args.head_out, args.head_model)
    target = project / "output" / "cost.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    print(report)
    print(f"Отчёт сохранён: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
