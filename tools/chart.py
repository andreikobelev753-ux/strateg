#!/usr/bin/env python3
"""Графики «Стратега»: SVG для документов и интерактивный HTML. Без внешних библиотек.

Спецификация — JSON-файл (обычно его пишет скрипт расчёта в analysis/):
  {
    "id": "funnel",                      латиница, цифры, дефис
    "type": "line" | "bar" | "waterfall",
    "title": "Конверсия из пробного в платный",
    "subtitle": "по месяцам 2026",       необязательно
    "unit": "%",                         подпись значений: %, ₽, тыс ₽
    "status": "вывод, расчёт: analysis/funnel.py",   статус, как в claims.md
    "source": "SRC-03, CLM-007",         откуда числа

    line и bar:
    "x": ["мар", "апр", ...],
    "series": [{"name": "...", "values": [...],
                "low": [...], "high": [...],    необязательно: диапазон прогноза
                "dashed": true}],               необязательно: пунктир для прогноза
    "notes": [{"x": "июл", "text": "смена определения"}],   необязательно
    "zero": false                        необязательно для line: не начинать ось с нуля

    waterfall:
    "steps": [{"label": "Цена с НДС", "value": 199, "total": true},
              {"label": "НДС 22%", "value": -35.9}, ...,
              {"label": "Вклад", "total": true}]       итог без value считается сам
  }

Команда:
  python3 tools/chart.py <project_dir> <spec.json>
Результат в output/charts/:
  <id>.svg  — вставлять в markdown: ![Заголовок](charts/<id>.svg);
  <id>.html — интерактивный: подсказки при наведении, легенда-переключатель,
              таблица данных, светлая и тёмная тема;
  <id>.json — копия спецификации для отчёта (tools/report.py).
Коды: 0 — готово, 2 — ошибка в спецификации.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

STATUSES = ("факт", "вывод", "гипотеза", "прогноз", "синтетика", "пробел")
TYPES = ("line", "bar", "waterfall")
WIDTH, HEIGHT = 720.0, 320.0
CHAR_W = 7.0  # средняя ширина кириллического символа при 12px

LIGHT = {
    "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "pos": "#2a78d6", "neg": "#e34948", "tot": "#52514e",
    "c": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
}
DARK = {
    "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#383835", "pos": "#3987e5", "neg": "#e66767", "tot": "#c3c2b7",
    "c": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}


def _vars(palette: Dict[str, Any]) -> str:
    items = [f"--v-{key}:{value}" for key, value in palette.items() if key != "c"]
    items += [f"--v-c{index}:{value}" for index, value in enumerate(palette["c"])]
    return ";".join(items)


_SCOPE = ".viz-root,.viz-card"
SVG_CSS = (
    _SCOPE + "{" + _vars(LIGHT) + "}"
    "html[data-theme=dark] .viz-root,html[data-theme=dark] .viz-card{" + _vars(DARK) + "}"
    "@media (prefers-color-scheme:dark){html:not([data-theme=light]) .viz-root,"
    "html:not([data-theme=light]) .viz-card{" + _vars(DARK) + "}}"
    ".viz-root{font-family:system-ui,-apple-system,'Segoe UI',sans-serif}"
    ".viz-root .bg{fill:var(--v-surface)}"
    ".viz-root .grid{stroke:var(--v-grid);stroke-width:1}"
    ".viz-root .axis{stroke:var(--v-axis);stroke-width:1}"
    ".viz-root .tick{fill:var(--v-muted);font-size:11px;font-variant-numeric:tabular-nums}"
    ".viz-root .lbl{fill:var(--v-ink2);font-size:12px}"
    ".viz-root .val{fill:var(--v-ink);font-size:11px;font-weight:600}"
    ".viz-root .ttl{fill:var(--v-ink);font-size:15px;font-weight:600}"
    ".viz-root .note{fill:var(--v-muted);font-size:11px}"
    ".viz-root .noteline{stroke:var(--v-muted);stroke-width:1;stroke-dasharray:3 3}"
    ".viz-root .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
    ".viz-root .dash{stroke-dasharray:6 4}"
    ".viz-root .band{opacity:.16}"
    ".viz-root .dot{stroke:var(--v-surface);stroke-width:2}"
    ".viz-root .hit{fill:transparent;cursor:crosshair}"
    ".viz-root .conn{stroke:var(--v-axis);stroke-width:1;stroke-dasharray:2 2}"
    ".viz-root .fpos{fill:var(--v-pos)}.viz-root .fneg{fill:var(--v-neg)}.viz-root .ftot{fill:var(--v-tot)}"
    ".viz-root .viz-xh{stroke:var(--v-muted);stroke-width:1}"
    ".viz-root .off{opacity:.07}"
    + "".join(f".viz-root .f{i}{{fill:var(--v-c{i})}}.viz-root .s{i}{{stroke:var(--v-c{i})}}"
              for i in range(8))
    + "@media (prefers-reduced-motion:no-preference){.viz-root .mk{animation:vizin .6s ease-out both}}"
    "@keyframes vizin{from{opacity:0}}"
)

PAGE_CSS = (
    ":root{--p-plane:#f9f9f7;--p-card:#fcfcfb;--p-ink:#0b0b0b;--p-ink2:#52514e;--p-muted:#898781;"
    "--p-line:rgba(11,11,11,.10);--p-accent:#2a78d6;color-scheme:light}"
    "html[data-theme=dark]{--p-plane:#0d0d0d;--p-card:#1a1a19;--p-ink:#f0efec;--p-ink2:#c3c2b7;"
    "--p-line:rgba(255,255,255,.12);--p-accent:#3987e5;color-scheme:dark}"
    "@media (prefers-color-scheme:dark){html:not([data-theme=light]){--p-plane:#0d0d0d;--p-card:#1a1a19;"
    "--p-ink:#f0efec;--p-ink2:#c3c2b7;--p-line:rgba(255,255,255,.12);--p-accent:#3987e5;color-scheme:dark}}"
    "body{margin:0;background:var(--p-plane);color:var(--p-ink);"
    "font:15px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif}"
    ".page{max-width:880px;margin:0 auto;padding:24px 16px 56px}"
    ".viz-card{background:var(--p-card);border:1px solid var(--p-line);border-radius:14px;"
    "padding:16px 16px 10px;margin:18px 0}"
    ".viz-card figcaption b{display:block;font-size:16px;line-height:1.3}"
    ".viz-card figcaption span{display:block;color:var(--p-ink2);font-size:13px}"
    ".viz-card svg{display:block;width:100%;height:auto;margin-top:8px}"
    ".viz-legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}"
    ".viz-leg{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--p-line);"
    "background:transparent;color:var(--p-ink2);border-radius:999px;padding:3px 10px 3px 8px;"
    "font:12px system-ui,-apple-system,sans-serif;cursor:pointer}"
    ".viz-leg.off{opacity:.45;text-decoration:line-through}"
    ".viz-leg .sw{width:10px;height:10px;border-radius:3px}"
    + "".join(f".sw{i}{{background:var(--v-c{i})}}" for i in range(8))
    + ".viz-foot{margin:6px 0 0;color:var(--p-muted);font-size:12px}"
    ".viz-table summary{cursor:pointer;color:var(--p-ink2);font-size:12px;margin-top:6px}"
    ".viz-table table{border-collapse:collapse;font-size:12px;margin:6px 0 4px;"
    "font-variant-numeric:tabular-nums}"
    ".viz-table th,.viz-table td{border-bottom:1px solid var(--p-line);padding:4px 10px;text-align:right}"
    ".viz-table th:first-child,.viz-table td:first-child{text-align:left}"
    ".viz-tip{position:fixed;left:0;top:0;z-index:20;pointer-events:none;opacity:0;transition:opacity .12s;"
    "max-width:300px;background:var(--p-card);color:var(--p-ink);border:1px solid var(--p-line);"
    "border-radius:10px;padding:8px 10px;font:12px/1.45 system-ui,-apple-system,sans-serif;"
    "box-shadow:0 8px 28px rgba(0,0,0,.14)}"
    ".viz-tip b{display:block;margin-bottom:2px}"
    ".viz-theme,.btn-print{border:1px solid var(--p-line);background:var(--p-card);color:var(--p-ink2);"
    "border-radius:999px;padding:4px 12px;font:12px system-ui,-apple-system,sans-serif;cursor:pointer}"
    "@media print{.viz-theme,.btn-print,.viz-tip{display:none}.viz-card{break-inside:avoid}}"
)

VIZ_JS = r"""(function(){
var tip=document.createElement('div');tip.className='viz-tip';document.body.appendChild(tip);
function hide(){tip.style.opacity=0;document.querySelectorAll('.viz-xh').forEach(function(l){l.setAttribute('opacity',0);});}
function place(e){var p=14,x=e.clientX+p,y=e.clientY+p,r=tip.getBoundingClientRect();
if(x+r.width>innerWidth-8)x=e.clientX-r.width-p;if(y+r.height>innerHeight-8)y=e.clientY-r.height-p;
tip.style.left=Math.max(8,x)+'px';tip.style.top=Math.max(8,y)+'px';}
document.querySelectorAll('svg.viz-root').forEach(function(svg){
var card=svg.closest('.viz-card');
function show(e){var t=e.target.closest('[data-tip]');if(!t){hide();return;}
var off={};if(card){card.querySelectorAll('.viz-leg.off').forEach(function(b){off[b.dataset.s]=1;});}
tip.textContent='';t.getAttribute('data-tip').split('|').forEach(function(l,i){var m=l.match(/^s(\d+)~/);
if(m&&off[m[1]])return;var d=document.createElement(i?'div':'b');d.textContent=l.replace(/^s\d+~/,'');tip.appendChild(d);});
tip.style.opacity=1;place(e);var cx=t.getAttribute('data-cx');
if(cx){var xh=svg.querySelector('.viz-xh');if(xh){xh.setAttribute('x1',cx);xh.setAttribute('x2',cx);xh.setAttribute('opacity',1);}}}
svg.addEventListener('mousemove',show);svg.addEventListener('click',show);svg.addEventListener('mouseleave',hide);});
document.querySelectorAll('.viz-leg').forEach(function(b){b.addEventListener('click',function(){
var hidden=b.classList.toggle('off');b.setAttribute('aria-pressed',hidden?'false':'true');
b.closest('.viz-card').querySelectorAll('.ser-'+b.dataset.s).forEach(function(g){g.classList.toggle('off',hidden);});});});
var root=document.documentElement;
try{var saved=localStorage.getItem('strateg-theme');if(saved)root.dataset.theme=saved;}catch(e){}
document.querySelectorAll('.viz-theme').forEach(function(btn){btn.addEventListener('click',function(){
var dark=root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
root.dataset.theme=dark?'light':'dark';try{localStorage.setItem('strateg-theme',root.dataset.theme);}catch(e){}});});
document.querySelectorAll('.stl .chip').forEach(function(c){c.addEventListener('click',function(){
var b=document.body;if(b.dataset.hl===c.dataset.st){delete b.dataset.hl;}else{b.dataset.hl=c.dataset.st;}});});
})();"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fmt(value: Any, unit: str = "", signed: bool = False) -> str:
    """Число по-русски: узкий пробел в тысячах, запятая в дробях, настоящий минус."""
    if not _num(value):
        return "—"
    number = round(float(value), 1)
    if abs(number - round(number)) < 1e-9:
        text = f"{int(round(number)):,}"
    else:
        text = f"{number:,.1f}"
    text = text.replace(",", " ").replace(".", ",")
    if text.startswith("-"):
        text = "−" + text[1:]
    elif signed and number > 0:
        text = "+" + text
    if unit:
        text += unit if unit == "%" else " " + unit
    return text


def nice_ticks(low: float, high: float, count: int = 5) -> List[float]:
    """Круглые деления оси, покрывающие [low, high]."""
    if high - low < 1e-9:
        high = low + 1.0
    span = high - low
    magnitude = 10 ** math.floor(math.log10(span / count))
    step = magnitude * 10
    for factor in (1, 2, 2.5, 5, 10):
        if span / (factor * magnitude) <= count:
            step = factor * magnitude
            break
    start = math.floor(low / step + 1e-9) * step
    end = math.ceil(high / step - 1e-9) * step
    total = int(round((end - start) / step))
    return [round(start + index * step, 10) for index in range(total + 1)]


def wrap(label: str, max_chars: int) -> List[str]:
    """Подпись в одну-две строки."""
    lines: List[str] = []
    current = ""
    for word in str(label).split():
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    if len(lines) == 2 and len(lines[1]) > max_chars:
        lines[1] = lines[1][: max(1, max_chars - 1)] + "…"
    return lines or [""]


def validate(spec: Any) -> List[str]:
    """Список ошибок спецификации; пустой — всё в порядке."""
    if not isinstance(spec, dict):
        return ["спецификация должна быть объектом JSON"]
    errors: List[str] = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", str(spec.get("id", ""))):
        errors.append("id: строчная латиница, цифры, дефис")
    kind = spec.get("type")
    if kind not in TYPES:
        errors.append("type: одно из " + ", ".join(TYPES))
    if not str(spec.get("title", "")).strip():
        errors.append("title: нужен заголовок")
    status = str(spec.get("status", "")).strip().lower()
    source = str(spec.get("source", "")).strip()
    if not status.startswith(STATUSES):
        errors.append("status: начинается со статуса — " + ", ".join(STATUSES))
    if not source:
        errors.append("source: откуда числа — SRC, CLM или файл расчёта")
    if status.startswith("факт"):
        if "расч" in status:
            errors.append("status: расчёт — это вывод: «вывод, расчёт: analysis/<файл>»")
        if "SRC-" not in source.upper():
            errors.append("status «факт» требует SRC в source")
    if kind in ("line", "bar"):
        labels = spec.get("x")
        series = spec.get("series")
        if not isinstance(labels, list) or not labels:
            errors.append("x: непустой список подписей")
            labels = []
        if not isinstance(series, list) or not series:
            errors.append("series: хотя бы один ряд")
        elif len(series) > 8:
            errors.append("series: не больше 8 рядов — остальное сверните в «прочее» или разбейте на графики")
        else:
            for index, item in enumerate(series):
                if not isinstance(item, dict):
                    errors.append(f"series[{index}]: объект с name и values")
                    continue
                for key in ("values", "low", "high"):
                    values = item.get(key)
                    if values is None and key != "values":
                        continue
                    if (not isinstance(values, list) or len(values) != len(labels)
                            or any(value is not None and not _num(value) for value in values)):
                        errors.append(f"series[{index}].{key}: числа или null, столько же, сколько подписей x")
    if kind == "waterfall":
        steps = spec.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
            errors.append("steps: хотя бы два шага")
        else:
            for index, step in enumerate(steps):
                if not isinstance(step, dict) or not str(step.get("label", "")).strip():
                    errors.append(f"steps[{index}]: нужна подпись label")
                elif not step.get("total") and not _num(step.get("value")):
                    errors.append(f"steps[{index}].value: число")
            first = steps[0]
            if isinstance(first, dict) and not (first.get("total") and _num(first.get("value"))):
                errors.append("steps[0]: первый шаг — исходная величина: total: true и value")
    return errors


class Frame:
    """Поля и шкала значений графика."""

    def __init__(self, values: Sequence[Any], include_zero: bool, unit: str,
                 right_pad: float, top: float, bottom: float) -> None:
        finite = [float(value) for value in values if _num(value)]
        low = min(finite) if finite else 0.0
        high = max(finite) if finite else 1.0
        if include_zero:
            low, high = min(low, 0.0), max(high, 0.0)
        self.ticks = nice_ticks(low, high)
        self.lo, self.hi = self.ticks[0], self.ticks[-1]
        self.unit = unit
        widest = max(len(self.tick_label(tick)) for tick in self.ticks)
        self.left = 14 + widest * CHAR_W
        self.top = top
        self.bottom = HEIGHT - bottom
        self.right = WIDTH - right_pad

    def tick_label(self, value: float) -> str:
        return fmt(value, "%" if self.unit == "%" else "")

    def y(self, value: float) -> float:
        return self.top + (self.hi - value) / (self.hi - self.lo) * (self.bottom - self.top)

    def axes(self) -> List[str]:
        parts = []
        for tick in self.ticks:
            y = self.y(tick)
            parts.append(f'<line class="grid" x1="{self.left:.1f}" x2="{self.right:.1f}" '
                         f'y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(f'<text class="tick" x="{self.left - 8:.1f}" y="{y + 4:.1f}" '
                         f'text-anchor="end">{_esc(self.tick_label(tick))}</text>')
        base = self.y(0.0) if self.lo <= 0.0 <= self.hi else self.bottom
        parts.append(f'<line class="axis" x1="{self.left:.1f}" x2="{self.right:.1f}" '
                     f'y1="{base:.1f}" y2="{base:.1f}"/>')
        return parts


def _tip(lines: Sequence[str]) -> str:
    """Атрибут data-tip и вложенный <title> (подсказка и без скрипта)."""
    clean = [str(line).replace("|", "/") for line in lines]
    plain = "\n".join(re.sub(r"^s\d+~", "", line) for line in clean)
    return f' data-tip="{_esc("|".join(clean))}"><title>{_esc(plain)}</title>'


def _x_labels(frame: Frame, centers: Sequence[float], labels: Sequence[Any], band: float) -> List[str]:
    max_chars = max(4, int(band / CHAR_W))
    average = sum(len(str(label)) for label in labels) / max(len(labels), 1)
    every = max(1, math.ceil(len(labels) * (min(average, max_chars) * CHAR_W + 10)
                             / max(frame.right - frame.left, 1)))
    parts = []
    for index, (center, label) in enumerate(zip(centers, labels)):
        if index % every and index != len(labels) - 1:
            continue
        lines = wrap(str(label), max_chars if every == 1 else 40)
        spans = "".join(f'<tspan x="{center:.1f}" dy="{0 if number == 0 else 14}">{_esc(line)}</tspan>'
                        for number, line in enumerate(lines))
        parts.append(f'<text class="lbl" x="{center:.1f}" y="{frame.bottom + 18:.1f}" '
                     f'text-anchor="middle">{spans}</text>')
    return parts


def _notes(spec: Dict[str, Any], frame: Frame, centers: Sequence[float], labels: Sequence[Any]) -> List[str]:
    index = {str(label): number for number, label in enumerate(labels)}
    parts = []
    for note in spec.get("notes") or []:
        if not isinstance(note, dict) or str(note.get("x")) not in index:
            continue
        center = centers[index[str(note.get("x"))]]
        anchor = "start" if center < (frame.left + frame.right) / 2 else "end"
        shift = 5 if anchor == "start" else -5
        parts.append(f'<line class="noteline" x1="{center:.1f}" x2="{center:.1f}" '
                     f'y1="{frame.top - 4:.1f}" y2="{frame.bottom:.1f}"/>')
        parts.append(f'<text class="note" x="{center + shift:.1f}" y="{frame.top - 8:.1f}" '
                     f'text-anchor="{anchor}">{_esc(note.get("text", ""))}</text>')
    return parts


def _runs(points: Sequence[Tuple[float, Optional[float]]]) -> List[List[Tuple[float, float]]]:
    runs: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    for x, y in points:
        if y is None:
            if current:
                runs.append(current)
            current = []
        else:
            current.append((x, y))
    if current:
        runs.append(current)
    return runs


def render_line(spec: Dict[str, Any]) -> List[str]:
    labels, series, unit = spec["x"], spec["series"], spec.get("unit", "")
    values: List[Any] = []
    for item in series:
        for key in ("values", "low", "high"):
            values.extend(item.get(key) or [])
    direct = 2 <= len(series) <= 4
    right_pad = 20.0
    label_chars = 0
    if direct:
        label_chars = min(26, max(len(str(item.get("name", ""))) for item in series))
        right_pad = 22 + label_chars * CHAR_W
    frame = Frame(values, bool(spec.get("zero", True)), unit, right_pad,
                  30.0 if spec.get("notes") else 12.0, 42.0)
    count = len(labels)
    width = frame.right - frame.left
    pad = min(28.0, width / (2 * count))
    span = width - 2 * pad
    centers = [frame.left + pad + (span * index / (count - 1) if count > 1 else span / 2)
               for index in range(count)]
    step = span / (count - 1) if count > 1 else width
    parts = frame.axes() + _notes(spec, frame, centers, labels)
    ends = []
    for number, item in enumerate(series):
        color = number % 8
        group = [f'<g class="ser-{number} mk">']
        low, high = item.get("low"), item.get("high")
        if isinstance(low, list) and isinstance(high, list):
            run: List[Tuple[float, float, float]] = []
            for index in range(count + 1):
                ok = index < count and _num(low[index]) and _num(high[index])
                if ok:
                    run.append((centers[index], float(low[index]), float(high[index])))
                    continue
                if len(run) >= 2:
                    upper = " ".join(f"{x:.1f},{frame.y(h):.1f}" for x, _, h in run)
                    lower = " ".join(f"{x:.1f},{frame.y(l):.1f}" for x, l, _ in reversed(run))
                    group.append(f'<polygon class="band f{color}" points="{upper} {lower}"/>')
                run = []
        points = [(centers[index], frame.y(float(value)) if _num(value) else None)
                  for index, value in enumerate(item["values"])]
        dash = " dash" if item.get("dashed") else ""
        for run_points in _runs(points):
            path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in run_points)
            group.append(f'<path class="line s{color}{dash}" d="{path}"/>')
        for index, (x, y) in enumerate(points):
            if y is not None and (count <= 24 or index in (0, count - 1)):
                group.append(f'<circle class="dot f{color}" cx="{x:.1f}" cy="{y:.1f}" r="4"/>')
        group.append("</g>")
        parts += group
        last = max((index for index, (_, y) in enumerate(points) if y is not None), default=None)
        if last is not None:
            ends.append([points[last][1], number, str(item.get("name", ""))])
    if direct:
        ends.sort()
        for index in range(1, len(ends)):
            if ends[index][0] - ends[index - 1][0] < 14:
                ends[index][0] = ends[index - 1][0] + 14
        for y, number, name in ends:
            short = name if len(name) <= label_chars else name[: label_chars - 1].rstrip() + "…"
            parts.append(f'<text class="lbl ser-{number}" x="{frame.right + 10:.1f}" y="{y + 4:.1f}">'
                         f'{_esc(short)}</text>')
    parts += _x_labels(frame, centers, labels, step)
    parts.append(f'<line class="viz-xh" x1="0" x2="0" y1="{frame.top:.1f}" y2="{frame.bottom:.1f}" opacity="0"/>')
    for index in range(count):
        lines = [str(labels[index])]
        for number, item in enumerate(series):
            text = f"s{number}~{item.get('name', '')}: {fmt(item['values'][index], unit)}"
            low, high = item.get("low"), item.get("high")
            if isinstance(low, list) and isinstance(high, list) and _num(low[index]) and _num(high[index]):
                text += f" (от {fmt(low[index], unit)} до {fmt(high[index], unit)})"
            lines.append(text)
        parts.append(f'<rect class="hit" x="{centers[index] - step / 2:.1f}" y="{frame.top:.1f}" '
                     f'width="{step:.1f}" height="{frame.bottom - frame.top:.1f}" '
                     f'data-cx="{centers[index]:.1f}"{_tip(lines)}</rect>')
    return parts


def bar_path(x: float, width: float, base: float, end: float, radius: float = 4.0) -> str:
    """Столбец со скруглением 4px только на конце значения, у базовой линии — прямой угол."""
    height = abs(base - end)
    if height < 0.5:
        return f"M{x:.1f},{base - 0.5:.1f}h{width:.1f}v1h{-width:.1f}Z"
    radius = min(radius, width / 2, height)
    sign = 1 if end < base else -1
    corner = end + sign * radius
    return (f"M{x:.1f},{base:.1f}V{corner:.1f}Q{x:.1f},{end:.1f} {x + radius:.1f},{end:.1f}"
            f"H{x + width - radius:.1f}Q{x + width:.1f},{end:.1f} {x + width:.1f},{corner:.1f}"
            f"V{base:.1f}Z")


def render_bar(spec: Dict[str, Any]) -> List[str]:
    labels, series, unit = spec["x"], spec["series"], spec.get("unit", "")
    values = [value for item in series for value in item["values"]]
    frame = Frame(values, True, unit, 20.0, 22.0, 42.0)
    count, groups = len(labels), len(series)
    band = (frame.right - frame.left) / count
    group_width = band * (0.62 if groups == 1 else 0.78)
    bar_width = max(2.0, (group_width - 2 * (groups - 1)) / groups)
    base = frame.y(0.0)
    centers = [frame.left + band * (index + 0.5) for index in range(count)]
    labels_on_bars = groups == 1 and count <= 12
    parts = frame.axes()
    marks: Dict[int, List[str]] = {number: [f'<g class="ser-{number} mk">'] for number in range(groups)}
    hits = []
    for index, label in enumerate(labels):
        left = centers[index] - group_width / 2
        for number, item in enumerate(series):
            value = item["values"][index]
            if not _num(value):
                continue
            x = left + number * (bar_width + 2)
            end = frame.y(float(value))
            marks[number].append(f'<path class="f{number % 8}" d="{bar_path(x, bar_width, base, end)}"/>')
            if labels_on_bars:
                text_y = end - 6 if value >= 0 else end + 14
                marks[number].append(f'<text class="val" x="{x + bar_width / 2:.1f}" y="{text_y:.1f}" '
                                     f'text-anchor="middle">{_esc(fmt(value, unit))}</text>')
            tip = [str(label), f"s{number}~{item.get('name', '')}: {fmt(value, unit)}"]
            hits.append(f'<rect class="hit" x="{x - 1:.1f}" y="{frame.top:.1f}" width="{bar_width + 2:.1f}" '
                        f'height="{frame.bottom - frame.top:.1f}"{_tip(tip)}</rect>')
    for number in range(groups):
        parts += marks[number] + ["</g>"]
    return parts + _x_labels(frame, centers, labels, band) + hits


def waterfall_bars(steps: Sequence[Dict[str, Any]]) -> List[Tuple[str, float, float, str]]:
    """(подпись, начало, конец, вид) для каждого шага; вид — tot, pos или neg."""
    bars = []
    running = 0.0
    for step in steps:
        label = str(step.get("label", ""))
        if step.get("total"):
            value = step.get("value")
            running = float(value) if _num(value) else running
            bars.append((label, 0.0, running, "tot"))
        else:
            start = running
            running = start + float(step["value"])
            bars.append((label, start, running, "pos" if running >= start else "neg"))
    return bars


def render_waterfall(spec: Dict[str, Any]) -> List[str]:
    unit = spec.get("unit", "")
    bars = waterfall_bars(spec["steps"])
    frame = Frame([bar[1] for bar in bars] + [bar[2] for bar in bars], True, unit, 20.0, 22.0, 46.0)
    count = len(bars)
    band = (frame.right - frame.left) / count
    width = band * 0.62
    centers = [frame.left + band * (index + 0.5) for index in range(count)]
    parts = frame.axes() + ['<g class="mk">']
    hits = []
    for index, (label, start, end, kind) in enumerate(bars):
        x = centers[index] - width / 2
        top, bottom = frame.y(max(start, end)), frame.y(min(start, end))
        parts.append(f'<rect class="f{kind}" x="{x:.1f}" y="{top:.1f}" width="{width:.1f}" '
                     f'height="{max(bottom - top, 1.0):.1f}" rx="3"/>')
        if kind == "tot":
            text = fmt(end, unit)
            tip = [label, f"итог: {text}"]
            above = end >= 0
        else:
            text = fmt(end - start, unit, signed=True)
            tip = [label, f"изменение: {text}", f"после шага: {fmt(end, unit)}"]
            above = end >= start
        text_y = top - 6 if above else bottom + 14
        if text_y > frame.bottom - 4:  # у самой оси подпись налезла бы на подписи шагов
            text_y = top - 6
        parts.append(f'<text class="val" x="{centers[index]:.1f}" y="{text_y:.1f}" '
                     f'text-anchor="middle">{_esc(text)}</text>')
        if index + 1 < count:
            level = frame.y(end)
            parts.append(f'<line class="conn" x1="{x + width:.1f}" x2="{centers[index + 1] - width / 2:.1f}" '
                         f'y1="{level:.1f}" y2="{level:.1f}"/>')
        hits.append(f'<rect class="hit" x="{centers[index] - band / 2:.1f}" y="{frame.top:.1f}" '
                    f'width="{band:.1f}" height="{frame.bottom - frame.top:.1f}"{_tip(tip)}</rect>')
    parts.append("</g>")
    return parts + _x_labels(frame, centers, [bar[0] for bar in bars], band) + hits


def render_plot(spec: Dict[str, Any]) -> List[str]:
    if spec["type"] == "line":
        return render_line(spec)
    if spec["type"] == "bar":
        return render_bar(spec)
    return render_waterfall(spec)


def footnote(spec: Dict[str, Any]) -> str:
    return f"Статус: {spec.get('status', '')} · Источник: {spec.get('source', '')}"


def standalone_svg(spec: Dict[str, Any]) -> str:
    """Самостоятельный SVG: заголовок, легенда, график, статус и источник."""
    series = spec.get("series") or []
    legend = spec["type"] != "waterfall" and len(series) >= 2
    head = 50.0 + (24.0 if legend else 0.0)
    total = HEIGHT + head + 28.0
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" class="viz-root" viewBox="0 0 {WIDTH:.0f} {total:.0f}" '
             f'width="{WIDTH:.0f}" height="{total:.0f}" role="img" aria-label="{_esc(spec["title"])}">',
             f"<style>{SVG_CSS}</style>",
             '<rect class="bg" width="100%" height="100%" rx="12"/>',
             f'<text class="ttl" x="16" y="26">{_esc(spec["title"])}</text>']
    if spec.get("subtitle"):
        parts.append(f'<text class="note" style="font-size:12px" x="16" y="44">{_esc(spec["subtitle"])}</text>')
    if legend:
        x = 16.0
        for number, item in enumerate(series):
            name = str(item.get("name", ""))
            parts.append(f'<rect class="f{number % 8}" x="{x:.1f}" y="{head - 21:.1f}" width="10" height="10" rx="3"/>')
            parts.append(f'<text class="lbl" x="{x + 14:.1f}" y="{head - 12:.1f}">{_esc(name)}</text>')
            x += 32 + len(name) * CHAR_W
    parts.append(f'<g transform="translate(0,{head:.0f})">')
    parts += render_plot(spec)
    parts += ["</g>", f'<text class="note" x="16" y="{total - 10:.0f}">{_esc(footnote(spec))}</text>', "</svg>"]
    return "\n".join(parts) + "\n"


def inline_svg(spec: Dict[str, Any]) -> str:
    return (f'<svg class="viz-root" viewBox="0 0 {WIDTH:.0f} {HEIGHT:.0f}" role="img" '
            f'aria-label="{_esc(spec["title"])}">' + "".join(render_plot(spec)) + "</svg>")


def table_html(spec: Dict[str, Any]) -> str:
    """Таблица данных под графиком — для доступности и проверки чисел."""
    unit = spec.get("unit", "")
    if spec["type"] == "waterfall":
        head = ["Шаг", "Изменение", "После шага"]
        rows = [[label, "—" if kind == "tot" else fmt(end - start, unit, signed=True), fmt(end, unit)]
                for label, start, end, kind in waterfall_bars(spec["steps"])]
    else:
        head = [""] + [str(item.get("name", "")) for item in spec["series"]]
        rows = []
        for index, label in enumerate(spec["x"]):
            row = [str(label)]
            for item in spec["series"]:
                cell = fmt(item["values"][index], unit)
                low, high = item.get("low"), item.get("high")
                if isinstance(low, list) and isinstance(high, list) and _num(low[index]) and _num(high[index]):
                    cell += f" ({fmt(low[index], unit)} … {fmt(high[index], unit)})"
                row.append(cell)
            rows.append(row)
    thead = "".join(f"<th>{_esc(cell)}</th>" for cell in head)
    tbody = "".join("<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return (f'<details class="viz-table"><summary>Таблица данных</summary><table><thead><tr>{thead}</tr>'
            f"</thead><tbody>{tbody}</tbody></table></details>")


def card_html(spec: Dict[str, Any]) -> str:
    """Карточка для HTML: заголовок, легенда-переключатель, график, статус, таблица."""
    series = spec.get("series") or []
    legend = ""
    if spec["type"] != "waterfall" and len(series) >= 2:
        legend = '<div class="viz-legend">' + "".join(
            f'<button class="viz-leg" type="button" data-s="{number}" aria-pressed="true">'
            f'<i class="sw sw{number % 8}"></i>{_esc(item.get("name", ""))}</button>'
            for number, item in enumerate(series)) + "</div>"
    subtitle = f'<span>{_esc(spec["subtitle"])}</span>' if spec.get("subtitle") else ""
    return (f'<figure class="viz-card" id="chart-{_esc(spec["id"])}"><figcaption><b>{_esc(spec["title"])}</b>'
            f"{subtitle}</figcaption>{legend}{inline_svg(spec)}"
            f'<p class="viz-foot">{_esc(footnote(spec))}</p>{table_html(spec)}</figure>')


def page_html(spec: Dict[str, Any]) -> str:
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{_esc(spec['title'])}</title><style>{SVG_CSS}{PAGE_CSS}</style></head>"
            '<body><main class="page"><div style="text-align:right">'
            '<button class="viz-theme" type="button">◐ Тема</button></div>'
            f"{card_html(spec)}</main><script>{VIZ_JS}</script></body></html>\n")


def build(project: Path, spec_path: Path) -> int:
    """Строит SVG, HTML и копию спецификации; код 2 при ошибках."""
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ошибка: не читается спецификация {spec_path}: {exc}", file=sys.stderr)
        return 2
    errors = validate(spec)
    if errors:
        print("Спецификация графика с ошибками:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2
    target = project / "output" / "charts"
    target.mkdir(parents=True, exist_ok=True)
    chart_id = spec["id"]
    (target / f"{chart_id}.svg").write_text(standalone_svg(spec), encoding="utf-8")
    (target / f"{chart_id}.html").write_text(page_html(spec), encoding="utf-8")
    (target / f"{chart_id}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    try:
        import journal  # noqa: WPS433 - тот же каталог tools/
        journal.append_event(project, "done", "chart", f"chart:{chart_id}", tool="chart.py",
                             note=str(spec["title"])[:80])
    except Exception:  # noqa: BLE001 - журнал не должен ломать график
        pass
    print(f"График: {target / (chart_id + '.html')}")
    print(f"Для документа: ![{spec['title']}](charts/{chart_id}.svg)")
    print("Показать пользователю: отправьте .html файлом (если среда умеет) или откройте его "
          "(macOS: open, Windows: start, Linux: xdg-open).")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="chart.py", description="График по JSON-спецификации.")
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("spec", help="JSON-спецификация графика")
    args = parser.parse_args(argv)
    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"ошибка: проект не найден: {project}", file=sys.stderr)
        return 2
    spec_path = Path(args.spec)
    if not spec_path.is_file() and (project / args.spec).is_file():
        spec_path = project / args.spec
    return build(project, spec_path)


if __name__ == "__main__":
    sys.exit(main())
