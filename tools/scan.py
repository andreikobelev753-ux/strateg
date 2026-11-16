#!/usr/bin/env python3
"""Поиск подозрительного в материалах и секретов в файлах.

Команды:
  materials <project_dir> — эвристика по materials/ и extracted/: обращения к ИИ,
                            «игнорируй инструкции», «считай фактом», «не показывай
                            пользователю», «отправь на адрес», «system prompt» и т.п.
  secrets <path>          — ключи и пароли (sk-…, AWS, GitHub, приватные ключи,
                            присваивания api_key/token/password). Если в PATH есть
                            gitleaks, вызывается он, иначе свои шаблоны. Значения
                            в выводе маскируются; код 1, если что-то найдено.

Примеры:
  python3 tools/scan.py materials projects/demo
  python3 tools/scan.py secrets projects/demo
  python3 tools/scan.py secrets path/to/file.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".html", ".htm", ".xml", ".log", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".css", ".sql", ".env",
}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}
SKIP_FILE_NAMES = {".env.example"}
SKIP_SUFFIXES = (".example", ".sample", ".template")
MAX_BYTES = 5 * 1024 * 1024

MATERIAL_PATTERNS: List[Tuple[str, str]] = [
    ("игнорируй инструкции",
     r"игнорир\w*\s+(?:все\s+)?(?:предыдущ\w+\s+)?(?:инструкц\w+|указани\w+|правил\w+)"),
    ("игнорируй инструкции",
     r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous\s+)?(?:instructions?|prompts?|rules?)"),
    ("игнорируй всё выше",
     r"(?:ignore|disregard)\s+(?:everything\s+)?above"),
    ("считай фактом",
     r"(?:считай\w*|рассматривай\w*|принимай\w*)\s+[^\n]{0,60}?факт\w*"),
    ("считай фактом",
     r"(?:consider|treat)\s+(?:it\s+|this\s+)?as\s+(?:a\s+)?fact"),
    ("не показывай пользователю",
     r"не\s+(?:показывай|сообщай|рассказывай|упоминай)\w*\s+(?:это\s+)?(?:пользовател\w+|никому|в\s+чате)"),
    ("не показывай пользователю",
     r"(?:do\s+not|don['’]t)\s+(?:show|tell|reveal|mention)\s+(?:this\s+)?(?:to\s+)?(?:the\s+)?user"),
    ("отправь на адрес",
     r"(?:отправ\w+|перешл\w+|выгруз\w+|загрузи\w*)\s+[^\n]{0,40}?"
     r"(?:на\s+адрес|по\s+адресу|на\s+сайт|в\s+(?:облако|чат|телеграм|почту))"),
    ("отправь на адрес",
     r"(?:send|forward|upload|exfiltrate|e-?mail)\s+(?:this\s+|it\s+|the\s+\w+\s+)?(?:to|at)\s+\S+"),
    ("отправь на адрес",
     r"(?:отправ\w+|перешл\w+|выгруз\w+|загрузи\w*|передай\w*|слей\w*)\s+[^\n]{0,80}?"
     r"(?:https?://|www\.|[\w.+-]+@[\w-]+\.[\w.]+)"),
    ("отправь на адрес",
     r"(?:send|post|upload|forward|transmit)\s+[^\n]{0,80}?(?:https?://|www\.|[\w.+-]+@[\w-]+\.[\w.]+)"),
    ("навязывает вывод",
     r"(?:рекомендуй|порекомендуй|выбери|выбирай|предлагай\s+только)\s+[^\n]{0,20}?вариант\w*"),
    ("навязывает вывод",
     r"без\s+(?:всякого\s+|какого-либо\s+)?сравнени\w+"),
    ("навязывает вывод",
     r"(?:recommend|choose|pick|select)\s+(?:only\s+)?(?:option|variant)\b|without\s+(?:any\s+)?comparison"),
    ("скрой от пользователя",
     r"скр(?:ой|ойте|ыть)\w*\s+[^\n]{0,40}?(?:от\s+пользовател\w+|риск\w*|раздел\w*)"),
    ("скрой от пользователя",
     r"hide\s+[^\n]{0,40}?(?:from\s+the\s+user|risks?)"),
    ("обращение к модели",
     r"(?:^|[\s(«\"'])(?:ассистент|ии-ассистент|ии-агент|нейросеть|assistant|ai\s+agent)\s*[,:!]"),
    ("system prompt",
     r"system\s*prompt|системн\w+\s+промпт|\bsystem\s*:\s*"),
    ("смена роли",
     r"(?:act\s+as|pretend\s+to\s+be|you\s+are\s+now)\b"),
    ("смена роли",
     r"(?:притворись|представь\s*,?\s*что\s+ты|теперь\s+ты)\b"),
    ("новые инструкции",
     r"(?:new|updated|real)\s+instructions?|нов\w+\s+инструкц\w+"),
    ("jailbreak",
     r"jailbreak|developer\s+mode|\bDAN\b"),
    ("обращение к модели",
     r"(?:dear|дорог\w+|уважаем\w+)\s+(?:ai\s+|ии\s+)?(?:assistant|ассистент|модель|нейросет\w+)"),
    # Название модели — только как обращение («Claude, …»), простое упоминание на сайте не в счёт.
    ("обращение к модели",
     r"\b(?:chatgpt|gpt-4|gpt4|gpt-3|claude|llama|deepseek)\s*[,:!]"),
    ("обращение к модели",
     r"(?:примечани\w*|заметк\w*|указани\w*|инструкци\w*|сообщени\w*|note|instructions?)\s+"
     r"(?:для|to|for)\s+(?:the\s+)?(?:ассистент\w*|ии|нейросет\w*|модел\w*|агент\w*|бот\w*|"
     r"assistant|ai|model|agent|llm)"),
    ("обращение к модели",
     r"для\s+(?:автоматическ\w+|машинн\w+)\s+(?:обработк\w+|анализ\w*|разбор\w*)"),
    ("считай фактом",
     r"(?:считать|считай\w*|принять|принимай\w*)\s+[^\n]{0,60}?"
     r"(?:сопоставим\w*|достоверн\w*|подтвержд\w*|верн\w*|проверенн\w*)"),
    ("считай фактом",
     r"(?:verified|confirmed|official|proven)\s+(?:facts?|data|figures|numbers|results?)"),
    ("отключает проверку",
     r"(?:можно|нужно|следует|стоит)\s+не\s+(?:прорабатыв\w*|рассматрив\w*|проверя\w*|"
     r"пересчитыв\w*|упомина\w*|сравнива\w*|учитыва\w*)"),
    ("отключает проверку",
     r"(?:пересч[её]т\w*|проверк\w*|сравнени\w*)\s+(?:не\s+нуж\w*|не\s+требу\w*|излиш\w*)"),
    ("навязывает вывод",
     r"достаточно\s+(?:обосновать|показать|описать)\s+[^\n]{0,30}?(?:вариант|предложени|решени)"),
    ("скрой от пользователя",
     r"(?:do\s+not|don['’]t|never)\s+(?:mention|discuss|disclose|reveal|show)\b"),
]
MATERIAL_REGEXPS = [(label, re.compile(pattern, re.IGNORECASE | re.UNICODE))
                    for label, pattern in MATERIAL_PATTERNS]

# Служебные шапки копий в extracted/ пишет сам агент — их не проверяем.
OWN_HEADER = re.compile(r"^\s*(?:Источник|Дата обращения|Копия|Извлечено|Метод|Инструмент)\s*:", re.IGNORECASE)

PHONE_RE = re.compile(r"(?<![\d+])(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PII_DIRS = ("extracted", "state", "output", "analysis")
STATUS_TAG_RE = re.compile(r"\[(факт)\b([^\[\]]{0,240})\]", re.IGNORECASE)
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SECRET_PATTERNS: List[Tuple[str, str, int]] = [
    ("OpenAI-ключ", r"\bsk-[A-Za-z0-9_\-]{16,}", 0),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b", 0),
    ("GitHub token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b", 0),
    ("GitHub token", r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", 0),
    ("Приватный ключ", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----", 0),
    ("JWT", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}", 0),
    ("Пароль/токен", r"(?i)(?<![-.#\w])(api[_-]?key|apikey|secret[_-]?key|access[_-]?token|"
                     r"auth[_-]?token|token|password|passwd|pwd)\b\s*[:=]\s*"
                     r"[\"']?([^\s\"',;]{6,})", 2),
]
SECRET_REGEXPS = [(label, re.compile(pattern), group) for label, pattern, group in SECRET_PATTERNS]

PLACEHOLDER_VALUES = {
    "changeme", "change_me", "password", "secret", "your_api_key", "your_token",
    "your_password", "example", "xxxxxxxx", "todo", "none", "null", "true", "false",
}

MATERIAL_MASKS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
]


def _mask_snippet(text: str) -> str:
    """Прячет секретоподобные строки во фрагментах материалов."""
    for pattern in MATERIAL_MASKS:
        text = pattern.sub(lambda match: _mask(match.group(0)), text)
    return text


def _read_text(path: Path) -> Optional[str]:
    """Читает текстовый файл; бинарные и слишком большие пропускает."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def _iter_files(root: Path) -> Iterable[Path]:
    """Обходит файлы, пропуская служебные каталоги и примеры."""
    if root.is_file():
        yield root
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name in SKIP_FILE_NAMES or name.endswith(SKIP_SUFFIXES):
                continue
            yield Path(current) / name


def _is_text_candidate(path: Path) -> bool:
    if path.name in SKIP_FILE_NAMES or path.name.endswith(SKIP_SUFFIXES):
        return False
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.suffix == ""


def scan_text(text: str) -> List[Dict[str, Any]]:
    """Ищет признаки команд ИИ в произвольном тексте; возвращает строки."""
    findings: List[Dict[str, Any]] = []
    for lineno, line in enumerate((text or "").splitlines(), 1):
        if OWN_HEADER.match(line):
            continue
        for label, regexp in MATERIAL_REGEXPS:
            if regexp.search(line):
                findings.append(
                    {
                        "line": lineno,
                        "label": label,
                        "snippet": _mask_snippet(line.strip())[:160],
                    }
                )
                break
    return findings


def scan_materials(project_dir: Any) -> List[Dict[str, Any]]:
    """Ищет признаки команд для ИИ в materials/ и extracted/."""
    project = Path(project_dir)
    findings: List[Dict[str, Any]] = []
    seen: set = set()
    for subdir in ("materials", "extracted"):
        root = project / subdir
        if not root.exists():
            continue
        for path in sorted(_iter_files(root)):
            try:
                relative = path.relative_to(project)
            except ValueError:
                relative = path
            if path.suffix.lower() == ".docx":
                for number, (line, hidden) in enumerate(docx_paragraphs(path), 1):
                    if hidden:
                        findings.append({"file": str(relative), "line": number, "copy": False,
                                         "label": "скрытый текст в docx",
                                         "snippet": _mask_snippet(line)[:160]})
                continue
            if not _is_text_candidate(path):
                continue
            text = _read_text(path)
            if text is None:
                continue
            for item in scan_text(text):
                findings.append(
                    {
                        "file": str(relative),
                        "line": item["line"],
                        "label": item["label"],
                        "snippet": _mask_snippet(mask_pii(item["snippet"])[0]),
                        # копия в extracted/ того же места, что уже найдено в materials/
                        "copy": subdir == "extracted" and (item["label"], item["snippet"]) in seen,
                    }
                )
                if subdir == "materials":
                    seen.add((item["label"], item["snippet"]))
    return findings


def _run_hidden(properties: Any) -> bool:
    """Свойства фрагмента Word прячут текст: белый цвет, размер до 4 pt, «скрытый»."""
    if properties is None:
        return False
    if properties.find(WORD_NS + "vanish") is not None:
        return True
    size = properties.find(WORD_NS + "sz")
    if size is not None:
        try:
            if int(size.get(WORD_NS + "val", "99")) <= 8:  # в половинах пункта: 8 = 4 pt
                return True
        except ValueError:
            pass
    color = properties.find(WORD_NS + "color")
    value = (color.get(WORD_NS + "val", "") if color is not None else "").lower()
    if re.fullmatch(r"[0-9a-f]{6}", value):
        return all(int(value[index:index + 2], 16) >= 0xF0 for index in (0, 2, 4))
    return False


def docx_paragraphs(path: Any) -> List[Tuple[str, bool]]:
    """Абзацы .docx без внешних модулей: (текст, есть ли в абзаце скрытый текст)."""
    import xml.etree.ElementTree as ElementTree
    import zipfile

    try:
        with zipfile.ZipFile(str(path)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return []
    result: List[Tuple[str, bool]] = []
    for paragraph in root.iter(WORD_NS + "p"):
        pieces: List[str] = []
        hidden = False
        for run in paragraph.iter(WORD_NS + "r"):
            text = "".join(node.text or "" for node in run.iter(WORD_NS + "t"))
            if text:
                pieces.append(text)
                hidden = hidden or _run_hidden(run.find(WORD_NS + "rPr"))
        line = "".join(pieces).strip()
        if line:
            result.append((line, hidden))
    return result


def mask_pii(text: str) -> Tuple[str, int]:
    """Заменяет телефоны и почту на пометки; возвращает текст и число замен.

    Имена так не найти — их агент обезличивает сам (правило 9 в AGENTS.md).
    """
    text, phones = PHONE_RE.subn("[телефон скрыт]", text or "")
    text, emails = EMAIL_RE.subn("[почта скрыта]", text)
    return text, phones + emails


def scan_pii(project_dir: Any) -> List[Dict[str, Any]]:
    """Телефоны и почта в extracted/, state/, output/, analysis/ (в materials/ оригиналы можно)."""
    project = Path(project_dir)
    findings: List[Dict[str, Any]] = []
    for subdir in PII_DIRS:
        root = project / subdir
        if not root.exists():
            continue
        for path in sorted(_iter_files(root)):
            if not _is_text_candidate(path) or path.suffix.lower() in (".html", ".htm"):
                continue
            text = _read_text(path)
            if text is None:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for label, regexp in (("телефон", PHONE_RE), ("почта", EMAIL_RE)):
                    for match in regexp.finditer(line):
                        findings.append({"file": str(path.relative_to(project)), "line": lineno,
                                         "label": label, "masked": _mask(match.group(0), 2)})
    return findings


def scan_status_tags(project_dir: Any) -> List[Dict[str, Any]]:
    """Пометки [факт …] в output/*.md: расчёт — это вывод, а у факта должен быть SRC."""
    project = Path(project_dir)
    findings: List[Dict[str, Any]] = []
    for path in sorted((project / "output").glob("*.md")):
        text = _read_text(path) or ""
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in STATUS_TAG_RE.finditer(line):
                inner = match.group(2)
                if re.search(r"расч[её]т|посчитан|оценк", inner, re.IGNORECASE):
                    problem = "расчёт помечен как факт — пометьте [вывод, расчёт: analysis/…]"
                elif "SRC-" not in inner.upper():
                    problem = "факт без источника SRC — добавьте SRC и место или смените статус"
                else:
                    continue
                findings.append({"file": str(path.relative_to(project)), "line": lineno,
                                 "label": problem, "snippet": match.group(0)[:120]})
    return findings


def _mask(value: str, keep: int = 3) -> str:
    """Маскирует значение, оставляя по keep символов с краёв."""
    value = (value or "").strip()
    if len(value) <= 2 * keep:
        return "*" * max(len(value), 1)
    middle = "*" * min(max(len(value) - 2 * keep, 4), 10)
    return f"{value[:keep]}{middle}{value[-keep:]}"


def _skip_secret_value(value: str) -> bool:
    value = value.strip()
    if not value:
        return True
    if value[0] in "$%<{":
        return True
    if value.lower() in PLACEHOLDER_VALUES:
        return True
    if value.lower().startswith(("your_", "your-", "xxx")):
        return True
    # Обычное короткое слово строчными буквами — это проза или CSS («token: before»), а не ключ.
    word = value.strip("`'\"«»()[]{}.,;:")
    if word.isalpha() and word.islower() and len(word) <= 10:
        return True
    return False


def _own_secret_findings(path: Path) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for file_path in sorted(_iter_files(path)):
        text = _read_text(file_path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, regexp, group in SECRET_REGEXPS:
                for match in regexp.finditer(line):
                    value = match.group(group)
                    if group and _skip_secret_value(value):
                        continue
                    findings.append(
                        {
                            "file": str(file_path),
                            "line": lineno,
                            "label": label,
                            "masked": _mask(value) if group else label,
                        }
                    )
    return findings


def _gitleaks_findings(path: Path) -> Optional[List[Dict[str, Any]]]:
    """Пробует gitleaks; None, если он недоступен или не сработал."""
    executable = shutil.which("gitleaks")
    if not executable or os.environ.get("STRATEG_NO_GITLEAKS"):
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        report = Path(tmpdir) / "report.json"
        command = [
            executable, "detect", "--no-git", "--source", str(path),
            "--report-format", "json", "--report-path", str(report),
            "--exit-code", "0",
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            return None
    if not isinstance(payload, list):
        return None
    findings: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        secret = item.get("Secret") or item.get("Match") or ""
        findings.append(
            {
                "file": item.get("File", "?"),
                "line": item.get("StartLine", "?"),
                "label": f"gitleaks:{item.get('RuleID', 'secret')}",
                "masked": _mask(secret) if secret else "***",
            }
        )
    return findings


def scan_secrets(path: Any) -> List[Dict[str, Any]]:
    """Ищет секреты в файле или каталоге; значения маскируются."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"путь не найден: {target}")
    external = _gitleaks_findings(target)
    if external is not None:
        return external
    return _own_secret_findings(target)


def _print_materials(project_dir: Any) -> int:
    findings = scan_materials(project_dir)
    if not findings:
        print("Подозрительных мест не найдено.")
        return 0
    print("Подозрительные места в materials/ и extracted/:")
    for item in findings:
        print(f"  {item['file']}:{item['line']}: [{item['label']}] {item['snippet']}")
    print(f"Всего: {len(findings)}.")
    return 0


def _print_secrets(path: Any) -> int:
    findings = scan_secrets(path)
    if not findings:
        print("Секретов не найдено.")
        return 0
    print("Найдены секреты:")
    for item in findings:
        print(f"  {item['file']}:{item['line']}: [{item['label']}] {item['masked']}")
    print(f"Всего: {len(findings)}. Значения замаскированы.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер команд сканера."""
    parser = argparse.ArgumentParser(
        prog="scan.py",
        description="Поиск подозрительных команд в материалах и секретов в файлах.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mat = sub.add_parser("materials", help="эвристика по materials/ и extracted/")
    p_mat.add_argument("project_dir", help="папка проекта")

    p_sec = sub.add_parser("secrets", help="поиск ключей и паролей")
    p_sec.add_argument("path", help="файл или каталог")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа CLI сканера."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "materials":
            return _print_materials(args.project_dir)
        if args.cmd == "secrets":
            return _print_secrets(args.path)
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
