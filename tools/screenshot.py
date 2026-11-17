#!/usr/bin/env python3
"""Снимок страницы для разбора интерфейса (навык ux-review): URL → PNG и текст в materials/.

Сохраняет materials/screens/<имя>.png (вся страница) и <имя>.txt (адрес, дата,
видимый текст), заводит источник SRC. Нужен модуль «интерфейсы»:
python3 strateg.py setup --ux (ставит playwright и браузер chromium).

Пример:
  python3 tools/screenshot.py projects/demo https://example.com --name landing [--mobile]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent / "extract"))
import common  # noqa: E402

MISSING = ("нужен модуль playwright: python3 strateg.py setup --ux "
           "(или: pip install playwright && python3 -m playwright install chromium). "
           "Без него попросите пользователя прислать скриншот и положить его в inbox/.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="screenshot.py", description="Снимок страницы: PNG + текст.")
    parser.add_argument("project_dir")
    parser.add_argument("url")
    parser.add_argument("--name", default="", help="имя файла без расширения")
    parser.add_argument("--mobile", action="store_true", help="ширина 390 px вместо 1440")
    args = parser.parse_args(argv)
    project = Path(args.project_dir)
    if not project.is_dir() or not args.url.startswith(("http://", "https://")):
        print("ошибка: нужен существующий проект и адрес http(s)://", file=sys.stderr)
        return 2
    blocked = common.source_policy(project, args.url)
    if blocked:
        print(f"отказ: {blocked}. Списки — config/sources.json и «sources» в project.json.", file=sys.stderr)
        return 4
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(MISSING, file=sys.stderr)
        return 3
    name = args.name or re.sub(r"[^\w-]+", "-", args.url.split("://", 1)[1]).strip("-")[:60]
    folder = project / "materials" / "screens"
    folder.mkdir(parents=True, exist_ok=True)
    png, txt = folder / f"{name}.png", folder / f"{name}.txt"
    key = f"screenshot:{args.url}"
    common.start(project, "extract", key, "screenshot.py", note=args.url)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 390 if args.mobile else 1440, "height": 900})
            page.goto(args.url, wait_until="networkidle", timeout=45000)
            page.screenshot(path=str(png), full_page=True)
            text = page.inner_text("body")
            browser.close()
    except Exception as exc:  # noqa: BLE001 - нет браузера, таймаут, сеть
        common.error(project, "extract", key, "screenshot.py", note=str(exc)[:300])
        print(f"ошибка снимка: {exc}", file=sys.stderr)
        if "Executable doesn't exist" in str(exc):
            print("Браузер не установлен: python3 -m playwright install chromium", file=sys.stderr)
        return 1
    text, _masked = common.scan.mask_pii(text)
    txt.write_text(f"Источник: {args.url}, дата обращения: {date.today().isoformat()}\n\n{text}\n", encoding="utf-8")
    source_id = common.next_source_id(project)
    common.append_source(project, source_id, what=f"снимок страницы {args.url}", where=args.url,
                         type_="открытый источник", copy_path=common.project_relative(project, txt),
                         notes=f"скриншот: {common.project_relative(project, png)}; "
                               + common.notes_for(common.scan_line_numbers(text)))
    common.done(project, "extract", key, "screenshot.py", note=source_id)
    print(f"Скриншот: {png}\nТекст: {txt}\nИсточник: {source_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
