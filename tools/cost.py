#!/usr/bin/env python3
"""Отчёт о стоимости: головная модель из транскриптов Claude Code + журнал.

Головная модель (и субагенты) — из транскриптов Claude Code
`~/.claude/projects/<папка агента>/*.jsonl` (и `*/subagents/*.jsonl`): поле
`message.usage` — input, output, cache_read, cache_creation. Берутся только
записи за время проекта (от project.json «created_at» или первого события
журнала до сейчас). Одно сообщение модели пишется в транскрипт несколькими
строками с одним `message.id` — считается один раз. Цены — config/prices.json.

Время — сумма активных интервалов: промежутки между соседними записями длиннее
10 минут (перерывы, ожидание ответа человека, простой лимита) не считаются.

Вспомогательные модели (DeepSeek и др.) — по state/journal.jsonl: по этапам и
по моделям. Если транскрипт недоступен, отчёт честно пишет «оценка недоступна»,
а не $0. Печатает отчёт в консоль и пишет копию в output/cost.md.

Папку транскриптов можно задать явно: STRATEG_TRANSCRIPTS=<папка>.

Пример:
  python3 tools/cost.py projects/demo
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import journal  # noqa: E402

HEAD_MODEL_LINE = (
    "Оценка недоступна: транскрипт Claude Code за время проекта не найден "
    "(другая среда или папка агента открыта не как проект). Это не $0. Если "
    "среда показывает счётчик токенов, передайте его: strateg.py cost <имя> "
    "--head-in N --head-out M [--head-model claude-opus-5-5]."
)
CONFIG_DIR = TOOLS_DIR.parent / "config"
AGENT_ROOT_DEFAULT = TOOLS_DIR.parent
ACTIVE_GAP_SECONDS = 600  # паузы длиннее 10 минут не считаем рабочим временем
USAGE_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
# Если в prices.json нет цен кэша — стандартные множители Anthropic к цене входа.
CACHE_MULTIPLIERS = {"cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0}


def _load_json(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def transcripts_dir(agent_root: Any = None) -> Path:
    """Папка транскриптов Claude Code для папки агента.

    Claude Code кладёт транскрипты в ~/.claude/projects/<путь, где всё, кроме
    латиницы и цифр, заменено на «-»>. STRATEG_TRANSCRIPTS задаёт папку явно.
    """
    explicit = os.environ.get("STRATEG_TRANSCRIPTS")
    if explicit:
        return Path(explicit)
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config) if config else Path.home() / ".claude"
    root = agent_root or os.environ.get("STRATEG_AGENT_ROOT") or AGENT_ROOT_DEFAULT
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path(root).resolve()))


def active_seconds(stamps: Iterable[datetime], gap: int = ACTIVE_GAP_SECONDS) -> int:
    """Сумма промежутков между соседними отметками, не длиннее gap секунд."""
    ordered = sorted(stamps)
    total = 0.0
    for previous, current in zip(ordered, ordered[1:]):
        delta = (current - previous).total_seconds()
        if 0 < delta <= gap:
            total += delta
    return int(total)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.astimezone()


def project_start(project_dir: Any) -> Optional[datetime]:
    """Начало проекта: created_at из project.json, иначе первое событие журнала,
    иначе начало дня created."""
    project = Path(project_dir)
    payload: Dict[str, Any] = {}
    try:
        loaded = json.loads((project / "project.json").read_text(encoding="utf-8"))
        payload = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        pass
    candidates: List[datetime] = []
    created_at = _aware(_parse_ts(payload.get("created_at")))
    if created_at is not None:
        return created_at
    for event in journal.read_events(project):
        stamp = _aware(_parse_ts(event.get("ts")))
        if stamp is not None:
            candidates.append(stamp)
    created = str(payload.get("created") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", created):
        day = _aware(datetime.fromisoformat(created))
        if day is not None:
            candidates.append(day)
    return min(candidates) if candidates else None


def _empty_usage() -> Dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def read_transcripts(directory: Path, start: Optional[datetime] = None,
                     end: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Расход по транскриптам Claude Code за [start, end]; None — транскриптов нет."""
    directory = Path(directory)
    if not directory.is_dir():
        return None
    files = sorted(directory.rglob("*.jsonl"))
    if not files:
        return None
    start, end = _aware(start), _aware(end)
    messages: Dict[str, Dict[str, Any]] = {}
    stamps: List[datetime] = []
    used_files = 0
    for path in files:
        try:
            if start is not None and path.stat().st_mtime < start.timestamp():
                continue  # файл закончился до начала проекта
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        used = False
        with handle:
            for line in handle:
                if '"assistant"' not in line and '"user"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "user"):
                    continue
                stamp = _aware(_parse_ts(entry.get("timestamp")))
                if stamp is None or (start and stamp < start) or (end and stamp > end):
                    continue
                stamps.append(stamp)
                used = True
                message = entry.get("message")
                if entry.get("type") != "assistant" or not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                model = str(message.get("model") or "—")
                if not isinstance(usage, dict) or model == "<synthetic>":
                    continue
                key = str(message.get("id") or entry.get("requestId") or entry.get("uuid") or len(messages))
                item = messages.setdefault(key, {"model": model, **_empty_usage()})
                cache_write = int(usage.get("cache_creation_input_tokens") or 0)
                details = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
                write_1h = int(details.get("ephemeral_1h_input_tokens") or 0)
                write_5m = int(details.get("ephemeral_5m_input_tokens") or 0)
                if write_1h + write_5m != cache_write:
                    write_5m, write_1h = cache_write - write_1h, write_1h
                values = {
                    "input": int(usage.get("input_tokens") or 0),
                    "output": int(usage.get("output_tokens") or 0),
                    "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                    "cache_write_5m": max(write_5m, 0),
                    "cache_write_1h": write_1h,
                }
                # строки одного сообщения повторяют usage; берём максимум, а не сумму
                for field, value in values.items():
                    item[field] = max(item[field], value)
        used_files += int(used)
    if not stamps:
        return None
    models: Dict[str, Dict[str, int]] = {}
    for item in messages.values():
        bucket = models.setdefault(item["model"], {**_empty_usage(), "messages": 0})
        bucket["messages"] += 1
        for field in USAGE_FIELDS:
            bucket[field] += item[field]
    return {"dir": str(directory), "files": used_files, "messages": len(messages),
            "models": models, "first": min(stamps), "last": max(stamps),
            "active_seconds": active_seconds(stamps)}


def _price_for(model: str, prices: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Цена модели: точное совпадение или самый длинный ключ-префикс (claude-opus-5-5-2026…)."""
    if isinstance(prices.get(model), dict):
        return prices[model]
    matches = [key for key in prices if model.startswith(key) and isinstance(prices[key], dict)]
    return prices[max(matches, key=len)] if matches else None


def usage_cost(model: str, usage: Dict[str, int], prices: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Стоимость по ценам API в долларах; None — цены модели нет в prices.json."""
    if prices is None:
        prices = _load_json("prices.json").get("models", {})
    price = _price_for(model, prices if isinstance(prices, dict) else {})
    if price is None:
        return None
    base_in = float(price.get("input", 0))
    rates = {"input": base_in, "output": float(price.get("output", 0))}
    for field, multiplier in CACHE_MULTIPLIERS.items():
        rates[field] = float(price.get(field, base_in * multiplier))
    return sum(usage.get(field, 0) * rates[field] for field in USAGE_FIELDS) / 1_000_000


def budget_tokens(usage: Dict[str, int]) -> int:
    """Токены в счёт бюджета режима: вход, выход и запись кэша; чтение кэша не считаем
    (оно в 10 раз дешевле и повторяет один и тот же контекст)."""
    return (usage.get("input", 0) + usage.get("output", 0)
            + usage.get("cache_write_5m", 0) + usage.get("cache_write_1h", 0))


def head_usage(project_dir: Any, agent_root: Any = None,
               now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Расход головной модели и субагентов за время проекта; None — оценка недоступна."""
    start = project_start(project_dir)
    if start is None:
        return None
    data = read_transcripts(transcripts_dir(agent_root), start, now)
    if data is None:
        return None
    prices = _load_json("prices.json").get("models", {})
    total_cost = 0.0
    unpriced: List[str] = []
    tokens = 0
    for model, usage in data["models"].items():
        value = usage_cost(model, usage, prices)
        usage["cost"] = value  # type: ignore[assignment]
        if value is None:
            unpriced.append(model)
        else:
            total_cost += value
        tokens += budget_tokens(usage)
    data.update({"cost": total_cost, "unpriced": unpriced, "budget_tokens": tokens,
                 "start": start})
    return data


def _plans_lines(api_cost: float) -> List[str]:
    plans = _load_json("plans.json")
    if not plans.get("plans"):
        return []
    lines = ["", "| Тариф | Цена в месяц, $ | Прогон по ценам API, % месячной цены |", "|---|---:|---:|"]
    for plan in plans["plans"]:
        monthly = float(plan.get("monthly_usd") or 0)
        share = (api_cost / monthly * 100) if monthly else 0.0
        lines.append(f"| {plan.get('name')} | {monthly:.0f} | {share:.1f} |")
    lines.extend(["", "Лимиты подписок публикуются не в токенах, поэтому это сравнение с ценой тарифа, а не доля лимита. "
                  f"Тарифы: {plans.get('source', '')}, проверено {plans.get('checked', '—')}."])
    return lines


def head_section(head_in: Optional[int], head_out: Optional[int], head_model: str,
                 duration: str, usage: Optional[Dict[str, Any]] = None) -> List[str]:
    """Раздел о головной модели: из транскрипта, из ручного счётчика или «оценка недоступна»."""
    lines = ["## Головная модель и субагенты", ""]
    if head_in is not None and head_out is not None:
        manual = {"input": head_in, "output": head_out}
        api_cost = usage_cost(head_model, manual)
        lines.append(f"- Источник: счётчик среды (передан вручную). Модель: {head_model}; "
                     f"токены: вход {head_in}, выход {head_out}; активное время: {duration}")
        if api_cost is None:
            lines.append(f"- Цена модели {head_model} не задана в config/prices.json")
            return lines
        lines.append(f"- По ценам API: ${api_cost:.2f} (без скидки за кэш, поэтому это верхняя оценка)")
        lines.extend(_plans_lines(api_cost))
        return lines
    if usage is None:
        lines.append(HEAD_MODEL_LINE)
        return lines
    lines.extend([
        f"- Источник: транскрипты Claude Code, {usage['dir']} — файлов {usage['files']}, "
        f"сообщений модели {usage['messages']}",
        f"- Окно: {usage['start']:%Y-%m-%d %H:%M} — {usage['last']:%Y-%m-%d %H:%M}; "
        f"активное время {_format_seconds(usage['active_seconds'])} (паузы длиннее 10 минут не считаются)",
        "",
        "| Модель | Сообщений | Вход | Выход | Чтение кэша | Запись кэша | Стоимость, $ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for model in sorted(usage["models"]):
        item = usage["models"][model]
        value = item.get("cost")
        cost_cell = f"{value:.2f}" if isinstance(value, float) else "нет цены"
        lines.append(f"| {_cell(model)} | {item['messages']} | {item['input']} | {item['output']} | "
                     f"{item['cache_read']} | {item['cache_write_5m'] + item['cache_write_1h']} | {cost_cell} |")
    lines.extend(["", f"- По ценам API: ${usage['cost']:.2f} (с учётом цен кэша из config/prices.json)"])
    if usage["unpriced"]:
        lines.append("- Без цены в config/prices.json (в сумму не вошли): " + ", ".join(usage["unpriced"]))
    lines.append(f"- В счёт бюджета режима: {usage['budget_tokens']:,} ток. (вход + выход + запись кэша)".replace(",", " "))
    lines.extend(_plans_lines(usage["cost"]))
    return lines


def _format_seconds(total: int) -> str:
    hours, rest = divmod(max(int(total), 0), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


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
            "first": None, "last": None, "stamps": []}


def _add(bucket: Dict[str, Any], event: Dict[str, Any]) -> None:
    bucket["events"] += 1
    bucket["tokens_in"] += int(event.get("tokens_in") or 0)
    bucket["tokens_out"] += int(event.get("tokens_out") or 0)
    bucket["cost"] += float(event.get("cost_usd") or 0.0)
    stamp = _aware(_parse_ts(event.get("ts")))
    if stamp is not None:
        bucket["stamps"].append(stamp)
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


def _table(groups: Dict[str, Dict[str, Any]], title: str, level: str = "##") -> List[str]:
    lines = [f"{level} {title}", "",
             "| Название | Событий | Токены in | Токены out | Стоимость, $ | Интервал |",
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
    usage = None if head_in is not None and head_out is not None else head_usage(project_dir)
    if usage is not None:
        active = _format_seconds(usage["active_seconds"])
        active_note = "по транскрипту, паузы длиннее 10 минут не считаются"
    else:
        active = _format_seconds(active_seconds(totals["stamps"]))
        active_note = "по журналу — грубо: журнал не видит работу между шагами"
    lines = [f"# Стоимость проекта {project_name}", ""]
    lines.extend(head_section(head_in, head_out, head_model, active, usage))
    lines.extend(["", "## Вспомогательные модели (журнал)", ""])
    lines.extend(_table(data["stages"], "По этапам", level="###"))
    lines.append("")
    lines.extend(_table(data["models"], "По моделям", level="###"))
    if usage is not None:
        overall = f"${usage['cost'] + totals['cost']:.2f} по ценам API (головная модель и субагенты + вспомогательные)"
    elif head_in is not None and head_out is not None:
        manual_cost = usage_cost(head_model, {"input": head_in, "output": head_out})
        overall = (f"${manual_cost + totals['cost']:.2f} по ценам API (по ручному счётчику)"
                   if manual_cost is not None else "оценка недоступна: нет цены головной модели")
    else:
        overall = (f"оценка недоступна — известны только вспомогательные модели: "
                   f"${totals['cost']:.4f}")
    lines.extend([
        "",
        "## Итого",
        "",
        f"- Всего: {overall}",
        f"- Событий журнала: {data['events']}",
        f"- Токены вспомогательных моделей: вход {totals['tokens_in']}, выход {totals['tokens_out']}",
        f"- Стоимость вспомогательных моделей: ${totals['cost']:.4f}",
        f"- Активное время: {active} ({active_note})",
        "",
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Парсер команды отчёта о стоимости."""
    parser = argparse.ArgumentParser(
        prog="cost.py",
        description="Отчёт о стоимости по state/journal.jsonl (этапы, модели, итог).",
    )
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("--head-in", type=int, default=None,
                        help="входные токены головной модели вручную (если транскрипта Claude Code нет)")
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
