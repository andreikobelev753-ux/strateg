#!/usr/bin/env python3
"""Отчёт проекта одним HTML-файлом: одностраничник с интерактивными графиками.

Берёт output/one-pager.md (или файл из --source) и собирает output/report.html:
- ссылки на графики ![…](charts/<id>.svg) становятся интерактивными карточками
  (подсказки, легенда-переключатель, таблица данных);
- графики из output/charts/, на которые нет ссылок, идут отдельным разделом;
- пометки [факт, …], [вывод], [гипотеза], [прогноз], [синтетика], [пробел] —
  цветные метки; щелчок по метке в шапке подсвечивает все такие места;
- сверху — шкала этапов «N из 12», кнопки темы и печати.
Без внешних библиотек; файл открывается в любом браузере и печатается в PDF.

Команда:
  python3 tools/report.py <project_dir> [--source output/one-pager.md]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import chart  # noqa: E402

STAGES = ["S0", "S1", "S2", "S3.0", "S3.1", "S3.2", "S4", "S5", "S6", "S7", "S8", "S9"]
STAGE_NAMES = ["бриф", "постановка", "материалы", "план исследования", "картина ситуации", "варианты",
               "рекомендация", "наброски", "стратегии", "доработка", "сопровождение", "пересмотр"]
STATUS_CLASSES = {"факт": ("st-fact", 0), "вывод": ("st-concl", 6), "гипотеза": ("st-hyp", 3),
                  "прогноз": ("st-fc", 2), "синтетика": ("st-syn", 4), "пробел": ("st-gap", 7)}
STATUS_RE = re.compile(r"\[(факт|вывод|гипотеза|прогноз|синтетика|пробел)([^\[\]]{0,240})\]", re.IGNORECASE)

REPORT_CSS = (
    "sup.fn{font-size:11px;margin-left:1px}sup.fn a{text-decoration:none}"
    ".footnotes{margin-top:18px;border-top:1px solid var(--p-muted);padding-top:6px;font-size:13px;color:var(--p-muted)}"
    ".footnotes h3{font-size:13px;margin:4px 0}.fn-def{margin:2px 0}.more{font-size:13px;color:var(--p-muted)}"
    ".top .tools{display:flex;gap:8px;justify-content:flex-end}"
    ".kicker{margin:10px 0 6px;color:var(--p-muted);font-size:13px}"
    ".prog{display:flex;align-items:center;gap:12px;margin:6px 0 10px}"
    ".prog .bar{flex:1;display:grid;grid-template-columns:repeat(12,1fr);gap:3px}"
    ".prog i{height:8px;border-radius:4px;background:var(--p-line)}"
    ".prog i.on{background:var(--p-accent)}"
    ".prog span{font-size:13px;color:var(--p-ink2);white-space:nowrap}"
    ".stl{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:12px;color:var(--p-muted);"
    "padding-bottom:14px;border-bottom:1px solid var(--p-line)}"
    ".stl .chip{cursor:pointer}"
    ".chip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--p-line);border-radius:999px;"
    "padding:0 8px;font-size:12px;line-height:20px;color:var(--p-ink2);background:var(--p-card);"
    "white-space:nowrap;vertical-align:1px;transition:opacity .15s}"
    ".chip i{width:8px;height:8px;border-radius:50%;flex:none}"
    + "".join(f".chip.{cls} i{{background:var(--v-c{slot})}}" for cls, slot in STATUS_CLASSES.values())
    + "body[data-hl] .doc .chip{opacity:.25}"
    + "".join(f'body[data-hl="{cls}"] .doc .chip.{cls}{{opacity:1;border-color:var(--v-c{slot})}}'
              for cls, slot in STATUS_CLASSES.values())
    + ".doc h1{font-size:28px;line-height:1.2;margin:22px 0 12px}"
    ".doc h2{font-size:20px;margin:30px 0 8px}.doc h3{font-size:16px;margin:22px 0 6px}"
    ".doc p,.doc li{max-width:72ch}.doc a{color:var(--p-accent)}"
    ".doc code{font-size:13px;background:var(--p-card);border:1px solid var(--p-line);border-radius:6px;"
    "padding:0 4px}"
    ".doc blockquote{margin:12px 0;padding:4px 14px;border-left:3px solid var(--p-accent);color:var(--p-ink2)}"
    ".doc hr{border:0;border-top:1px solid var(--p-line);margin:24px 0}"
    ".doc img{max-width:100%;border-radius:12px}"
    ".tbl{overflow-x:auto;margin:12px 0}"
    ".tbl table{border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums;min-width:60%}"
    ".tbl th,.tbl td{border-bottom:1px solid var(--p-line);padding:6px 10px;text-align:left;vertical-align:top}"
    ".tbl th{color:var(--p-ink2);font-weight:600}"
    ".foot{margin-top:36px;padding-top:12px;border-top:1px solid var(--p-line);font-size:13px;"
    "color:var(--p-muted)}.foot a{color:var(--p-ink2)}"
)


def stage_index(text: str) -> int:
    """Номер этапа 1–12 по строке «N из 12» или коду S0…S9; 0 — неизвестно."""
    match = re.search(r"(\d+)\s*из\s*12", text or "")
    if match:
        return max(0, min(12, int(match.group(1))))
    best = 0
    for code in re.findall(r"S\d(?:\.\d)?", text or ""):
        if code in STAGES:
            best = max(best, STAGES.index(code) + 1)
        elif code == "S3":
            best = max(best, 4)
    return best


def read_stage(project: Path) -> int:
    state = project / "state" / "state.md"
    if not state.exists():
        return 0
    text = state.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(?im)^#{1,6}\s*этап\s*\n+\s*(.+)$", text)
    return stage_index(match.group(1) if match else text[:400])


FOOTNOTE_REF_RE = re.compile(r"\[\^(\w{1,12})\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^(\w{1,12})\]:\s*(.*)$")
ONE_PAGER_WORDS = 250
ONE_PAGER_CHARTS = 2


def one_pager_problems(text: str) -> List[str]:
    """Проверка одностраничника: до 250 слов, до 2 графиков, номера CLM/SRC — только в сносках."""
    body: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if FOOTNOTE_DEF_RE.match(stripped) or stripped.startswith("Подробно:"):
            continue
        body.append(stripped)
    joined = "\n".join(body)
    charts = re.findall(r"!\[[^\]]*\]\([^)]+\)", joined)
    plain = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", joined)
    plain = FOOTNOTE_REF_RE.sub(" ", plain)
    plain = STATUS_RE.sub(" ", plain)
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-–,.][0-9A-Za-zА-Яа-яЁё]+)*", plain)
    problems: List[str] = []
    if len(words) > ONE_PAGER_WORDS:
        problems.append(f"одностраничник: {len(words)} слов, нужно до {ONE_PAGER_WORDS} — сократите")
    if len(charts) > ONE_PAGER_CHARTS:
        problems.append(f"одностраничник: графиков {len(charts)}, нужно до {ONE_PAGER_CHARTS}")
    ids = sorted(set(re.findall(r"\b(?:CLM|SRC|DEC)-\d+\b", "\n".join(body))))
    if ids:
        shown = ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")
        problems.append(f"одностраничник: номера в тексте ({shown}) — перенесите в сноски [^1]")
    return problems


def _link(label: str, url: str) -> str:
    if re.match(r"^(https?://|[\w./#%-]+$)", url) and not url.lower().startswith("javascript"):
        return f'<a href="{url}">{label}</a>'
    return label


def _chip(match: "re.Match[str]") -> str:
    kind = match.group(1).lower()
    cls = STATUS_CLASSES.get(kind, ("st-concl", 6))[0]
    return f'<span class="chip {cls}"><i></i>{kind}{match.group(2)}</span>'


def inline(text: str) -> str:
    """Строчная разметка: код, ссылки, жирный, курсив, метки статусов."""
    codes: List[str] = []

    def keep(match: "re.Match[str]") -> str:
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: _link(m.group(1), m.group(2)), text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<em>\1</em>", text)
    text = STATUS_RE.sub(_chip, text)
    text = FOOTNOTE_REF_RE.sub(lambda m: f'<sup class="fn"><a href="#fn-{m.group(1)}">{m.group(1)}</a></sup>', text)
    return re.sub("\x00(\\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", text)


def _cells(row: str) -> List[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def table_html(rows: List[str]) -> str:
    head: Optional[List[str]] = None
    body = rows
    if len(rows) >= 2 and re.fullmatch(r"\|?[\s:|-]+\|?", rows[1]) and "-" in rows[1]:
        head, body = _cells(rows[0]), rows[2:]
    parts = ['<div class="tbl"><table>']
    if head:
        parts.append("<thead><tr>" + "".join(f"<th>{inline(cell)}</th>" for cell in head) + "</tr></thead>")
    parts.append("<tbody>" + "".join("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in _cells(row))
                                     + "</tr>" for row in body) + "</tbody></table></div>")
    return "".join(parts)


def list_html(block: List[str]) -> str:
    items: List[List[Any]] = []
    for line in block:
        match = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if match:
            items.append([len(match.group(1).replace("\t", "    ")), match.group(2)[0].isdigit(), match.group(3)])
        elif items:
            items[-1][2] += " " + line.strip()
    parts: List[str] = []
    stack: List[List[Any]] = []
    for indent, ordered, text in items:
        while stack and indent < stack[-1][0]:
            parts.append(f"</li></{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            tag = "ol" if ordered else "ul"
            parts.append(f"<{tag}>")
            stack.append([indent, tag])
        else:
            parts.append("</li>")
        parts.append(f"<li>{inline(text)}")
    while stack:
        parts.append(f"</li></{stack.pop()[1]}>")
    return "".join(parts)


def chart_id(src: str) -> Optional[str]:
    match = re.search(r"charts/([a-z0-9][a-z0-9-]*)\.(?:svg|html|json)$", src)
    return match.group(1) if match else None


def md_to_html(text: str, charts: Dict[str, Dict[str, Any]], used: Set[str]) -> str:
    """Markdown → HTML: заголовки, абзацы, списки, таблицы, цитаты, линии, графики."""
    lines = text.splitlines()
    out: List[str] = []
    para: List[str] = []
    notes: List[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            flush()
            index += 1
            continue
        footnote = FOOTNOTE_DEF_RE.match(stripped)
        if footnote:
            flush()
            notes.append(f'<p class="fn-def" id="fn-{footnote.group(1)}"><sup>{footnote.group(1)}</sup> '
                         f"{inline(footnote.group(2))}</p>")
            index += 1
            continue
        image = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)\)", stripped)
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if image:
            flush()
            found = chart_id(image.group(2))
            if found and found in charts:
                out.append(chart.card_html(charts[found]))
                used.add(found)
            else:
                out.append("<p>" + inline(stripped) + "</p>")
        elif heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            out.append("<hr>")
        elif stripped.startswith("|"):
            flush()
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip())
                index += 1
            out.append(table_html(rows))
            continue
        elif stripped.startswith(">"):
            flush()
            quote = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            out.append("<blockquote>" + inline(" ".join(quote)) + "</blockquote>")
            continue
        elif re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush()
            block = []
            while index < len(lines) and (re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[index])
                                          or (lines[index].startswith("  ") and lines[index].strip())):
                block.append(lines[index])
                index += 1
            out.append(list_html(block))
            continue
        else:
            para.append(stripped)
        index += 1
    flush()
    if notes:
        out.append('<section class="footnotes"><h3>Основания</h3>' + "".join(notes) + "</section>")
    return "\n".join(out)


def load_charts(project: Path) -> Dict[str, Dict[str, Any]]:
    charts: Dict[str, Dict[str, Any]] = {}
    for path in sorted((project / "output" / "charts").glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not chart.validate(spec):
            charts[spec["id"]] = spec
    return charts


def build_report(project: Path, source: Path) -> str:
    markdown = source.read_text(encoding="utf-8", errors="replace")
    charts = load_charts(project)
    used: Set[str] = set()
    body = md_to_html(markdown, charts, used)
    rest = [chart_key for chart_key in sorted(charts) if chart_key not in used]
    if rest and source.name == "one-pager.md":
        # одностраничник остаётся одной страницей: остальные графики — ссылками
        body += ('\n<p class="more">Другие графики: ' + " · ".join(
            f'<a href="charts/{html.escape(key)}.html">{html.escape(key)}</a>' for key in rest) + "</p>")
    elif rest:
        body += "\n<h2>Графики</h2>\n" + "\n".join(chart.card_html(charts[key]) for key in rest)
    title_match = re.search(r"(?m)^#\s+(.+)$", markdown)
    title = re.sub(r"[*`\[\]]", "", title_match.group(1)).strip() if title_match else project.name
    stage = read_stage(project)
    cells = "".join(f'<i class="{"on" if number < stage else ""}" title="{number + 1}. {STAGE_NAMES[number]}"></i>'
                    for number in range(12))
    label = f"Этап {stage} из 12 · {STAGE_NAMES[stage - 1]}" if stage else "Этап не указан"
    chips = "".join(f'<span class="chip {cls}" data-st="{cls}"><i></i>{kind}</span>'
                    for kind, (cls, _slot) in STATUS_CLASSES.items())
    docs = sorted(path.name for path in (project / "output").glob("*.md") if path.resolve() != source.resolve())
    links = " · ".join(f'<a href="{html.escape(name)}">{html.escape(name)}</a>' for name in docs) or "—"
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{chart.SVG_CSS}{chart.PAGE_CSS}{REPORT_CSS}</style></head><body><main class=\"page\">"
        '<header class="top"><div class="tools"><button class="viz-theme" type="button">◐ Тема</button>'
        '<button class="btn-print" type="button" onclick="window.print()">Печать</button></div>'
        f'<p class="kicker">«Стратег» · проект {html.escape(project.name)} · отчёт от {date.today().isoformat()}</p>'
        f'<div class="prog" aria-label="{label}"><div class="bar">{cells}</div><span>{label}</span></div>'
        f'<div class="stl"><span>Пометки (нажмите, чтобы подсветить):</span>{chips}</div></header>'
        f'<article class="doc">{body}</article>'
        f'<footer class="foot">Подробно: {links}</footer>'
        f"</main><script>{chart.VIZ_JS}</script></body></html>\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="report.py", description="Отчёт проекта одним HTML-файлом.")
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("--source", default="output/one-pager.md", help="markdown-документ внутри проекта")
    args = parser.parse_args(argv)
    project = Path(args.project_dir)
    source = project / args.source
    if not source.is_file():
        fallback = project / "output" / "situation.md"
        if args.source == "output/one-pager.md" and fallback.is_file():
            source = fallback
        else:
            print(f"ошибка: нет документа {source}", file=sys.stderr)
            return 2
    target = project / "output" / "report.html"
    target.write_text(build_report(project, source), encoding="utf-8")
    print(f"Отчёт: {target}")
    if source.name == "one-pager.md":
        for problem in one_pager_problems(source.read_text(encoding="utf-8", errors="replace")):
            print(f"Внимание: {problem}")
    print("Показать пользователю: отправьте файл (если среда умеет) или откройте его "
          "(macOS: open, Windows: start, Linux: xdg-open). Печать в PDF — кнопкой «Печать».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
