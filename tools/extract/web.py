#!/usr/bin/env python3
"""Страница → текст и копия.

Источник — URL или сохранённый HTML-файл. Текст извлекается trafilatura, если
модуль установлен; иначе — стандартным html.parser (заголовок и текст без
script/style). Сохраняются extracted/SRC-xx.md (первая строка — «Источник: …,
дата обращения: …») и копия extracted/SRC-xx.html, запись в state/sources.md
(тип «открытый источник») и события в журнале. Перед записью текст проверяется
искателем команд из tools/scan.py.

Пример:
  python3 tools/extract/web.py projects/demo materials/page.html
  python3 tools/extract/web.py projects/demo https://example.com/page
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, List, Optional, Tuple

EXTRACT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = EXTRACT_DIR.parent
for _path in (str(TOOLS_DIR), str(EXTRACT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import common  # noqa: E402

TOOL = "extract/web.py"
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "table", "ul", "ol",
    "blockquote", "pre", "hr",
}


class _PageParser(HTMLParser):
    """Простой разбор: заголовок и текст без script/style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_script = 0
        self.in_style = 0
        self.in_title = False
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - сигнатура stdlib
        tag = tag.lower()
        if tag == "script":
            self.in_script += 1
        elif tag == "style":
            self.in_style += 1
        elif tag == "title":
            self.in_title = True
        elif tag in BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script":
            self.in_script = max(0, self.in_script - 1)
        elif tag == "style":
            self.in_style = max(0, self.in_style - 1)
        elif tag == "title":
            self.in_title = False
        elif tag in BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_script or self.in_style:
            return
        if self.in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


def _normalize(text: str) -> str:
    """Убирает лишние пробелы, сохраняя абзацы."""
    lines = []
    for raw in text.replace("\r", "\n").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return "\n\n".join(lines)


def parse_html(html: str) -> Tuple[str, str]:
    """Возвращает (заголовок, текст) стандартным парсером."""
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()
    text = _normalize("".join(parser.text_parts))
    return title, text


def trafilatura_text(html: str) -> Optional[str]:
    """Текст через trafilatura; None, если модуль не установлен или не справился."""
    try:
        trafilatura = importlib.import_module("trafilatura")
    except ImportError:
        return None
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
    except Exception:  # noqa: BLE001 - разные версии trafilatura
        return None
    if isinstance(text, str) and text.strip():
        return _normalize(text)
    return None


def load_source(source: str, policy_project: Optional[Path] = None) -> Tuple[bytes, str, str]:
    """Возвращает (сырые байты, расшифрованный HTML, адрес)."""
    if source.lower().startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "strateg-night/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            final = response.geturl() or source
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        if final != source and policy_project is not None:
            blocked = common.source_policy(policy_project, final)
            if blocked:  # перенаправление на запрещённый сайт
                raise OSError(f"перенаправление на {final}: {blocked}")
        return raw, raw.decode(charset, errors="replace"), source
    path = Path(source)
    raw = path.read_bytes()
    return raw, raw.decode("utf-8", errors="replace"), source


def _project_relative(project: Path, address: str) -> str:
    """Путь сохранённой страницы от папки проекта, если она внутри проекта."""
    try:
        return Path(address).resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return address


def build_markdown(address: str, title: str, text: str) -> str:
    """Собирает markdown: первая строка — источник и дата обращения."""
    header = f"Источник: {address}, дата обращения: {date.today().isoformat()}"
    parts = [header, ""]
    if title:
        parts.extend([f"# {title}", ""])
    if text:
        parts.append(text)
    return "\n".join(parts).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Парсер команды извлечения страницы."""
    parser = argparse.ArgumentParser(
        prog="web.py",
        description="URL или сохранённый HTML → extracted/SRC-xx.md и копия SRC-xx.html.",
    )
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("source", help="URL или путь к сохранённому HTML")
    parser.add_argument("--again", action="store_true",
                        help="завести новый SRC, даже если эта страница уже в реестре")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа: извлекает текст страницы и регистрирует источник."""
    args = build_parser().parse_args(argv)
    project = Path(args.project_dir)
    if not project.is_dir():
        print(f"ошибка: проект не найден: {project}", file=sys.stderr)
        return 2
    if not args.source.lower().startswith(("http://", "https://")) and not Path(args.source).is_file() \
            and (Path(args.project_dir) / args.source).is_file():
        args.source = str(Path(args.project_dir) / args.source)
    if not args.source.lower().startswith(("http://", "https://")) and not Path(args.source).is_file():
        print(f"ошибка: файл не найден: {args.source}", file=sys.stderr)
        return 2

    is_url = args.source.lower().startswith(("http://", "https://"))
    if is_url:
        blocked = common.source_policy(project, args.source)
        if blocked:
            print(f"отказ: {blocked}. Списки — config/sources.json и «sources» в project.json; "
                  "поменять их может только пользователь.", file=sys.stderr)
            return 4
    where = args.source if is_url else _project_relative(project, args.source)
    digest = "" if is_url else common.file_digest(args.source)
    existing = common.find_existing_source(project, where, digest)
    if existing.get("id") and not args.again:
        common.report_existing(existing)
        return 0
    key = f"extract:{args.source}" if is_url else common.step_key("extract", project, Path(args.source))
    common.start(project, "extract", key, TOOL, note=args.source)
    try:
        raw, html, address = load_source(args.source, project)
    except (OSError, urllib.error.URLError) as exc:
        common.error(project, "extract", key, TOOL, note=f"ошибка загрузки: {exc}"[:300])
        print(f"ошибка загрузки: {exc}", file=sys.stderr)
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            print("Сайт подписан сертификатом, которому Python не доверяет (у российских сайтов это часто "
                  "сертификат Минцифры). Загрузку в обход проверки не делаем.", file=sys.stderr)
        print("Обходной путь: попросите пользователя открыть страницу в браузере, сохранить её "
              "(Cmd+S или Ctrl+S, формат HTML) в папку inbox/ и выполните: "
              "python3 strateg.py extract <имя> web inbox/<файл>.html", file=sys.stderr)
        return 1

    title, fallback_text = parse_html(html)
    text = trafilatura_text(html) or fallback_text
    text, _masked = common.scan.mask_pii(text)
    markdown = build_markdown(address, title, text)

    source_id = common.next_source_id(project)

    md_path = project / "extracted" / f"{source_id}.md"
    html_path = project / "extracted" / f"{source_id}.html"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_bytes(raw)

    lines = common.scan_line_numbers(markdown)
    common.append_source(
        project,
        source_id,
        what=title or f"Страница {address}",
        where=address if is_url else _project_relative(project, address),
        # страница из materials/ — материал пользователя (например, его посадочная страница);
        # URL или сохранённая копия чужой страницы вне materials/ — открытый источник
        type_=("материал пользователя"
               if not is_url and _project_relative(project, address).startswith("materials/")
               else "открытый источник"),
        copy_path=f"extracted/{source_id}.md",
        notes=common.notes_for(lines) + (f"; новая версия, прежняя — {existing['previous']}"
                                          if existing.get("previous") else ""),
        digest=digest,
    )
    common.done(project, "extract", key, TOOL, note=source_id)
    print(f"Сохранено: {md_path}")
    print(f"Копия HTML: {html_path}")
    print(f"Источник: {source_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
