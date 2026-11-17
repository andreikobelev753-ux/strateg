#!/usr/bin/env python3
"""Диспетчер проекта «Стратег»: окружение, проекты, проверки, журнал.

Команды:
  doctor              — окружение: Python, .venv, модули, ffmpeg/gitleaks, ключи .env;
  new <name>          — создать проект из projects/_template (или с нуля);
  status [<name>]     — состояние проекта, прерванные шаги, просрочки, устаревшие источники;
  status --brief [<name>] — короткая сводка (до 15 строк) для старта сессии;
  check <name>        — реестры + материалы + секреты;
  snapshot <name> <stage>, restore <name> <snapshot>, impact <name> <id>, log <name> ...
                      — вызовы tools/journal.py, tools/snapshot.py, tools/registry.py;
  setup [--media] [--ux] [--dry-run] — подготовить окружение;
  cost <name>         — отчёт о стоимости: транскрипты Claude Code + журнал (tools/cost.py);
  chart <name> <spec.json> — график: SVG для документов и интерактивный HTML (tools/chart.py);
  report <name>       — отчёт одним HTML-файлом: одностраничник с графиками (tools/report.py);
  extract-скрипты     — tools/extract/{docs,web,tables,media}.py.

Примеры:
  python3 strateg.py doctor
  python3 strateg.py new demo
  python3 strateg.py status --brief
  python3 strateg.py check demo
  python3 strateg.py snapshot demo S1
  python3 strateg.py log demo start S1 "extract:materials/a.pdf" --tool markitdown
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE = Path(__file__).resolve().parent
TOOLS = BASE / "tools"
AGENT_ROOT = Path(os.environ.get("STRATEG_AGENT_ROOT", str(BASE)))
PROJECTS = Path(os.environ.get("STRATEG_PROJECTS", str(AGENT_ROOT / "projects")))

COPY_MARKER = "<!-- копия, правьте оригинал в skills/ или agents/ -->\n"
SETTINGS_CONTENT = (
    '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": '
    '"cd \\"$CLAUDE_PROJECT_DIR\\" && python3 strateg.py status --brief"}]}]}}'
    + "\n"
)
SETUP_REQUIREMENTS = ["requirements.txt"]

MODULES = ["yaml", "markitdown", "trafilatura", "pandas", "faster_whisper",
           "scenedetect", "playwright"]
TOOLS_IN_PATH = ["ffmpeg", "gitleaks"]

sys.path.insert(0, str(TOOLS))
try:
    import registry  # type: ignore
    import scan  # type: ignore
    import journal  # type: ignore
except ImportError:  # pragma: no cover - на случай запуска из другого каталога
    registry = scan = journal = None  # type: ignore
try:
    import cost  # type: ignore
except ImportError:  # pragma: no cover
    cost = None  # type: ignore


def _now() -> str:
    """Текущее локальное время в ISO-формате."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _run(tool: str, args: List[str]) -> int:
    """Запускает скрипт из tools/ с теми же аргументами."""
    script = TOOLS / tool
    return subprocess.run([sys.executable, str(script)] + args, check=False).returncode


def _project_path(name: str) -> Path:
    """Безопасно собирает путь проекта по имени."""
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise ValueError(f"недопустимое имя проекта: {name!r}")
    if name == "_template":
        raise ValueError("имя _template зарезервировано")
    return PROJECTS / name


def _require_project(name: str) -> Path:
    path = _project_path(name)
    if not path.is_dir():
        raise FileNotFoundError(f"проект не найден: {path}")
    return path


def _module_available(name: str) -> bool:
    """Проверяет, импортируется ли модуль (без импорта)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError, AttributeError):
        return False


def _env_keys(path: Path) -> List[str]:
    """Имена ключей из .env (значения не читаются в вывод)."""
    keys: List[str] = []
    if not path.exists():
        return keys
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return keys
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.append(key)
    return keys


def _instruction_files() -> Dict[str, str]:
    """Ищет AGENTS.md, CLAUDE.md, .cursor/ в корне агента и текущем каталоге."""
    found: Dict[str, str] = {}
    roots = [AGENT_ROOT]
    cwd = Path.cwd()
    if cwd != AGENT_ROOT:
        roots.append(cwd)
    for name in ("AGENTS.md", "CLAUDE.md"):
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                found[name] = str(candidate)
                break
    for root in roots:
        candidate = root / ".cursor"
        if candidate.is_dir():
            found[".cursor/"] = str(candidate)
            break
    return found


def _print_doctor(modules: Dict[str, bool], binaries: Dict[str, Optional[str]],
                  keys: List[str], instructions: Dict[str, str]) -> None:
    version = platform.python_version()
    ok_python = sys.version_info >= (3, 9)
    print("Окружение «Стратег»")
    print(f"  Python: {version} ({'ок' if ok_python else 'нужен 3.9+'})")
    venv = AGENT_ROOT / ".venv"
    print(f"  .venv: {'есть' if venv.is_dir() else 'нет'} ({venv})")
    print("  Модули:")
    for name in MODULES:
        print(f"    {name}: {'есть' if modules[name] else 'нет'}")
    print("  Программы в PATH:")
    for name in TOOLS_IN_PATH:
        print(f"    {name}: {binaries[name] or 'нет'}")
    print(f"  .env: {('ключи: ' + ', '.join(keys)) if keys else 'нет или пуст'}")
    print("  Инструкции:")
    if instructions:
        for name, path in instructions.items():
            print(f"    {name}: {path}")
    else:
        print("    AGENTS.md, CLAUDE.md, .cursor/ — не найдены")
    available = sum(1 for value in modules.values() if value)
    print(f"  Итог: Python {'ок' if ok_python else 'старый'}, модулей {available}/{len(MODULES)}, "
          f"ffmpeg {'есть' if binaries['ffmpeg'] else 'нет'}, "
          f"gitleaks {'есть' if binaries['gitleaks'] else 'нет'}, ключей .env: {len(keys)}")


def cmd_doctor() -> int:
    """Собирает сведения об окружении и пишет doctor.json."""
    modules = {name: _module_available(name) for name in MODULES}
    binaries = {name: shutil.which(name) for name in TOOLS_IN_PATH}
    keys = _env_keys(AGENT_ROOT / ".env")
    instructions = _instruction_files()
    _print_doctor(modules, binaries, keys, instructions)
    payload = {
        "ts": _now(),
        "agent_root": str(AGENT_ROOT),
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 9),
        "venv": (AGENT_ROOT / ".venv").is_dir(),
        "modules": modules,
        "tools": binaries,
        "env_keys": keys,
        "instruction_files": instructions,
    }
    target = AGENT_ROOT / "doctor.json"
    try:
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  Записано: {target}")
    except OSError as exc:
        print(f"  Не удалось записать doctor.json: {exc}", file=sys.stderr)
        return 1
    return 0


def _state_template(name: str, today: str) -> str:
    """Стандартный state.md нового проекта (разделы из шаблона задачи 2)."""
    return (
        f"# Проект: {name}\n\n## Этап\nS0 — бриф\n\n## Сделано\n—\n\n## Сейчас\n—\n\n"
        f"## Дальше\nзагрузить материалы в materials/ и сформулировать запрос\n\n"
        f"## Ждём от пользователя\n—\n\n## Допущения\n—\n\n## Последний снимок\n—\n\n"
        f"## Обновлено\n{today}\n"
    )


def _write_project_files(path: Path, name: str) -> None:
    """Создаёт базовые файлы проекта (используется при отсутствии шаблона)."""
    for subdir in ("materials", "extracted", "analysis", "state", "state/tasks",
                   "drafts", "output"):
        (path / subdir).mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    payload = {
        "name": name,
        "created": today,
        "request": "",
        "question": "",
        "goal": "",
        "success_metric": "",
        "recipient": "",
        "constraints": "",
        "mode": "",
        "sources": [],
        "modules": [],
    }
    (path / "project.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (path / "state" / "state.md").write_text(_state_template(name, today), encoding="utf-8")
    for filename, title in (
        ("sources.md", "Источники"),
        ("claims.md", "Утверждения"),
        ("decisions.md", "Решения"),
    ):
        (path / "state" / filename).write_text(f"# {title}\n", encoding="utf-8")
    (path / "state" / "journal.jsonl").write_text("", encoding="utf-8")


def _make_writable(root: Path) -> None:
    """Даёт владельцу право записи: шаблон мог прийти из архива с правами «только чтение»."""
    for path in [root, *root.rglob("*")]:
        try:
            extra = stat.S_IWUSR | (stat.S_IXUSR if path.is_dir() else 0)
            path.chmod(path.stat().st_mode | extra)
        except OSError:
            pass


def cmd_new(name: str) -> int:
    """Создаёт новый проект из шаблона или с нуля."""
    try:
        target = _project_path(name)
    except ValueError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    if target.exists():
        print(f"ошибка: проект уже существует: {target}", file=sys.stderr)
        return 1
    template = PROJECTS / "_template"
    try:
        if template.is_dir():
            # copyfile не переносит права файлов шаблона; каталоги правит _make_writable
            shutil.copytree(template, target, copy_function=shutil.copyfile)
            _make_writable(target)
            print(f"Проект создан из шаблона: {target}")
        else:
            _write_project_files(target, name)
            print(f"Шаблон не найден, проект создан с нуля: {target}")
        today = date.today().isoformat()
        project_json = target / "project.json"
        payload: Dict[str, Any] = {
            "name": name,
            "created": today,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "request": "",
            "question": "",
            "goal": "",
            "success_metric": "",
            "recipient": "",
            "constraints": "",
            "mode": "",
            "sources": [],
            "modules": [],
        }
        if project_json.exists():
            try:
                loaded = json.loads(project_json.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        payload["name"] = name
        if not payload.get("created"):
            payload["created"] = today
        project_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        state_md = target / "state" / "state.md"
        if not state_md.exists():
            state_md.write_text(_state_template(name, today), encoding="utf-8")
        else:
            text = state_md.read_text(encoding="utf-8", errors="replace")
            text = text.replace("example", name)
            text = text.replace("2026-01-01", today)
            if not re.search(r"(?im)^\s*(?:#{1,6}\s*)?(?:этап|stage)\b", text):
                text = _state_template(name, today)
            state_md.write_text(text, encoding="utf-8")
        for filename, title in (
            ("sources.md", "Источники"),
            ("claims.md", "Утверждения"),
            ("decisions.md", "Решения"),
        ):
            file_path = target / "state" / filename
            if not file_path.exists():
                file_path.write_text(f"# {title}\n", encoding="utf-8")
        journal_file = target / "state" / "journal.jsonl"
        journal_file.touch(exist_ok=True)
        for subdir in ("materials", "extracted", "analysis", "state/tasks", "drafts", "output"):
            (target / subdir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ошибка при создании проекта: {exc}", file=sys.stderr)
        return 1
    print(f"Дальше: положите материалы в {target / 'materials'} и запустите check.")
    return 0


def _section_value(text: str, names: Set[str]) -> str:
    """Значение из раздела state.md: «## Этап» → следующая непустая строка."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
        if not header or header.group(1).strip().lower() not in names:
            continue
        for follow in lines[index + 1:]:
            value = follow.strip()
            if not value:
                continue
            if re.match(r"^\s*#{1,6}\s", follow):
                break
            return value
    return ""


def _state_fields(text: str) -> Tuple[str, str]:
    """Достаёт «Этап» и «Дальше» из state.md (разделы или строки «Ключ: значение»)."""
    stage = _section_value(text, {"этап", "stage"})
    next_step = _section_value(text, {"дальше", "next"})
    if stage and next_step:
        return stage, next_step
    for line in text.splitlines():
        match = re.match(r"\s*([^:\n]+):\s*(.*)", line)
        if not match:
            continue
        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        if key in ("этап", "stage") and not stage:
            stage = value
        if key in ("дальше", "next") and not next_step:
            next_step = value
    return stage, next_step


def _warn_stale_state(name: str, stage: str) -> None:
    """Предупреждает, если state.md не обновлён до снимка: иначе откат вернёт старый «Этап»."""
    try:
        text = (PROJECTS / name / "state" / "state.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    current, _next = _state_fields(text)
    first = current.split()[0] if current.split() else ""
    if first and first.upper() != stage.upper():
        print(f"Внимание: в state.md этап «{first}», а снимок — «{stage}». "
              f"Обнови state.md до снимка (AGENTS.md, раздел 4), иначе откат вернёт старый этап.",
              file=sys.stderr)


def _overdue_commitments(project: Path) -> List[Tuple[str, Dict[str, Any], int]]:
    """Просроченные обязательства: [(id решения, обязательство, дней просрочки)]."""
    if registry is None:
        return []
    data = registry.load(project)
    today = date.today()
    result: List[Tuple[str, Dict[str, Any], int]] = []
    for decision in data["DEC"]:
        for commitment in decision["commitments"]:
            if commitment["done"]:
                continue
            try:
                when = date.fromisoformat(commitment["date"])
            except (TypeError, ValueError):
                continue
            if when < today:
                result.append((decision["id"], commitment, (today - when).days))
    return result


def cmd_status(name: Optional[str], brief: bool, days: int = 180) -> int:
    """Показывает состояние проекта или короткую сводку."""
    if brief and not name:
        return _status_brief_all()
    if not name:
        print("ошибка: укажите имя проекта или используйте status --brief", file=sys.stderr)
        return 2
    try:
        project = _require_project(name)
    except (ValueError, FileNotFoundError) as exc:
        if brief:
            print(f"Проект недоступен: {exc}")
            return 0
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    if brief:
        return _status_brief(project, name)
    print(f"Проект: {name}")
    print(f"Путь: {project}")
    print("--- state.md ---")
    state_md = project / "state" / "state.md"
    print(state_md.read_text(encoding="utf-8", errors="replace").strip() if state_md.exists() else "нет state.md")
    print("--- Прерванные шаги ---")
    if journal is not None:
        pending = journal.pending_steps(project)
        if pending:
            for event in pending:
                print(f"  {event.get('key')} (этап {event.get('stage')}, {event.get('ts')})")
        else:
            print("  нет")
    print("--- Просроченные обязательства ---")
    overdue = _overdue_commitments(project)
    if overdue:
        for decision_id, commitment, days_late in overdue:
            print(f"  {decision_id}: {commitment['text']} · {commitment['responsible']} · "
                  f"{commitment['date']} ({days_late} дн. просрочки)")
    else:
        print("  нет")
    print(f"--- Устаревшие источники (старше {days} дней) ---")
    if registry is not None:
        stale = registry.stale_data(project, days)
        if stale["sources"]:
            for item in stale["sources"]:
                record = item["record"]
                print(f"  {record['id']} — {item['date']} ({item['days']} дн.)")
        else:
            print("  нет")
    return 0


def _mode_budget(project: Path) -> Tuple[str, int, float]:
    """Режим проекта, бюджет токенов и порог предупреждения из config/modes.json."""
    try:
        mode = json.loads((project / "project.json").read_text(encoding="utf-8")).get("mode", "")
        settings = json.loads((AGENT_ROOT / "config" / "modes.json").read_text(encoding="utf-8")).get(mode, {})
        return str(mode), int(settings.get("token_budget", 0)), float(settings.get("warn_at", 0.8))
    except (OSError, ValueError, AttributeError, TypeError):
        return "", 0, 0.8


def _elapsed(seconds: int) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"


def _progress_line(project: Path) -> str:
    """Строка прогресса: активное время и «бюджет ≈ X%».

    Расход головной модели и субагентов — из транскриптов Claude Code (tools/cost.py),
    вспомогательных моделей — из журнала. Правило AGENTS.md: на warn_at (80%)
    предупредить, на 100% остановиться и спросить.
    """
    helper = 0
    journal_stamps: List[datetime] = []
    if journal is not None:
        data = journal.summary(project)
        helper = data["totals"]["tokens_in"] + data["totals"]["tokens_out"]
        journal_stamps = [stamp for item in data["stages"] for stamp in (item["first"], item["last"])
                          if stamp is not None]
    usage = None
    if cost is not None:
        try:
            usage = cost.head_usage(project, AGENT_ROOT)
        except Exception:  # noqa: BLE001 - сводка не должна падать
            usage = None
    if usage is None and not journal_stamps:
        return "Бюджет: оценка недоступна — транскрипт Claude Code не найден, журнал пуст"
    _mode, budget, warn_at = _mode_budget(project)
    if usage is not None:
        elapsed = f"активно {_elapsed(usage['active_seconds'])}"
        spent = usage["budget_tokens"] + helper
        share = spent / budget if budget else None
        money = f" · ≈ ${usage['cost']:.2f} по ценам API"
        if budget and share is not None:
            line = f"Идёт: {elapsed} · бюджет ≈ {share:.0%} ({spent:,} из {budget:,} ток.){money}".replace(",", " ")
            if share >= 1:
                line += "\n⛔ Бюджет режима исчерпан: остановитесь и спросите пользователя, продолжать ли."
            elif share >= warn_at:
                line += f"\n⚠ Израсходовано {share:.0%} бюджета режима: предупредите пользователя."
            return line
        return f"Идёт: {elapsed} · расход {spent:,} ток.{money} · режим не выбран — бюджета нет".replace(",", " ")
    seconds = int((max(journal_stamps) - min(journal_stamps)).total_seconds())
    return (f"Идёт: {_elapsed(seconds)} с первого шага · вспомогательные модели: {helper:,} ток."
            .replace(",", " ") + " · бюджет: оценка недоступна (нет транскрипта Claude Code)")


STAGE_ORDER = ["S0", "S1", "S2", "S3.0", "S3.1", "S3.2", "S4", "S5", "S6", "S7", "S8", "S9"]


def _stage_bar(stage: str) -> str:
    """Шкала этапов «▰▰▰▱… 4 из 12» по строке этапа из state.md; пусто, если этап не понятен."""
    match = re.search(r"(\d+)\s*из\s*12", stage or "")
    number = int(match.group(1)) if match else 0
    if not match:
        for code in re.findall(r"S\d(?:\.\d)?", stage or ""):
            if code in STAGE_ORDER:
                number = max(number, STAGE_ORDER.index(code) + 1)
            elif code == "S3":
                number = max(number, 4)
    number = max(0, min(12, number))
    return "▰" * number + "▱" * (12 - number) + f" {number} из 12" if number else ""


def _status_brief(project: Path, name: str) -> int:
    """Короткая сводка по одному проекту (до 15 строк)."""
    try:
        state_md = project / "state" / "state.md"
        text = state_md.read_text(encoding="utf-8", errors="replace") if state_md.exists() else ""
        stage, next_step = _state_fields(text)
        pending = journal.pending_steps(project) if journal is not None else []
        lines = [
            f"Проект: {name} · этап: {stage or '—'}",
            f"Дальше: {next_step or '—'}",
        ]
        bar = _stage_bar(stage)
        if bar:
            lines.insert(1, f"Прогресс: {bar}")
        if pending:
            keys = ", ".join(str(event.get("key")) for event in pending[:3])
            suffix = f" (+{len(pending) - 3})" if len(pending) > 3 else ""
            lines.append(f"Прерванные шаги: {len(pending)} — {keys}{suffix}")
        else:
            lines.append("Прерванные шаги: нет")
        progress = _progress_line(project)
        if progress:
            lines.append(progress)
        overdue = _overdue_commitments(project)
        if overdue:
            lines.append(f"Просроченных обязательств: {len(overdue)}")
        print("\n".join(lines[:15]))
    except Exception as exc:  # noqa: BLE001 - сводка не должна падать
        print(f"Сводка недоступна: {exc}")
    return 0


def _newest_project() -> Optional[Tuple[Path, str]]:
    """Проект с самым свежим state.md (кроме _template)."""
    if not PROJECTS.is_dir():
        return None
    best: Optional[Tuple[float, Path, str]] = None
    for entry in PROJECTS.iterdir():
        if not entry.is_dir() or entry.name == "_template":
            continue
        state_md = entry / "state" / "state.md"
        if not state_md.exists():
            continue
        mtime = state_md.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, entry, entry.name)
    if best is None:
        return None
    return best[1], best[2]


def _status_brief_all() -> int:
    """Сводка по самому свежему проекту; всегда код 0."""
    try:
        newest = _newest_project()
        if newest is None:
            print("Проектов пока нет. Напишите в чате: новый проект")
            return 0
        return _status_brief(newest[0], newest[1])
    except Exception as exc:  # noqa: BLE001
        print(f"Сводка недоступна: {exc}")
        return 0


def cmd_check(name: str) -> int:
    """Реестры + материалы + секреты; код 1, если есть проблемы или секреты."""
    try:
        project = _require_project(name)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"Проверка проекта: {name}")
    print("--- Реестры ---")
    problems = registry.check_project(project) if registry is not None else ["registry недоступен"]
    if problems:
        for problem in problems:
            print(f"  {problem}")
        print(f"  Проблем: {len(problems)}")
    else:
        print("  Проблем не найдено.")
    print("--- Материалы (эвристика) ---")
    materials = scan.scan_materials(project) if scan is not None else []
    copies = [item for item in materials if item.get("copy")]
    materials = [item for item in materials if not item.get("copy")]
    if materials:
        for item in materials:
            print(f"  {item['file']}:{item['line']}: [{item['label']}] {item['snippet']}")
        print(f"  Подозрительных мест: {len(materials)}")
        if copies:
            files = sorted({item["file"] for item in copies})
            print(f"  Те же места в копиях: {len(copies)} ({', '.join(files)}) — отдельно не считаем")
    else:
        print("  Не найдено.")
    print("--- Секреты ---")
    try:
        secrets = scan.scan_secrets(project) if scan is not None else []
    except (OSError, ValueError) as exc:
        print(f"  Ошибка поиска: {exc}")
        secrets = []
    if secrets:
        for item in secrets:
            print(f"  {item['file']}:{item['line']}: [{item['label']}] {item['masked']}")
        print(f"  Найдено: {len(secrets)}")
    else:
        print("  Не найдено.")
    print("--- Персональные данные (телефоны, почта) ---")
    pii = scan.scan_pii(project) if scan is not None else []
    if pii:
        for item in pii[:20]:
            print(f"  {item['file']}:{item['line']}: [{item['label']}] {item['masked']}")
        print(f"  Найдено: {len(pii)} — обезличьте (правило 9). Имена людей проверьте сами: скрипт их не видит.")
    else:
        print("  Не найдено. Имена людей проверьте сами: скрипт их не видит.")
    print("--- Пометки [факт] в документах output/ ---")
    tags = scan.scan_status_tags(project) if scan is not None else []
    if tags:
        for item in tags[:20]:
            print(f"  {item['file']}:{item['line']}: {item['snippet']} — {item['label']}")
        print(f"  Найдено: {len(tags)}")
    else:
        print("  Ошибок нет.")
    failed = bool(problems) or bool(secrets) or bool(pii) or bool(tags)
    print(f"Итог: реестры — {'проблемы' if problems else 'ок'}, "
          f"материалы — {len(materials)} предупрежд., "
          f"секреты — {'найдены' if secrets else 'нет'}, "
          f"персональные данные — {len(pii) or 'нет'}, "
          f"пометки [факт] — {'ошибки: ' + str(len(tags)) if tags else 'ок'}.")
    return 1 if failed else 0


def _dispatch(name: str, tool: str, command: str, args: List[str]) -> int:
    """Вызывает подкоманду скрипта tools/ для проекта name."""
    try:
        project = _require_project(name)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    return _run(tool, [command, str(project)] + args)


def _dispatch_plain(name: str, tool: str, args: List[str]) -> int:
    """Вызывает скрипт tools/ с папкой проекта первым аргументом (без подкоманды)."""
    try:
        project = _require_project(name)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    return _run(tool, [str(project)] + args)


def _copy_tree_with_marker(source: Path, destination: Path) -> int:
    """Копирует дерево и добавляет пометку в начало каждого .md."""
    copied = 0
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
        if target.suffix.lower() == ".md":
            text = target.read_text(encoding="utf-8", errors="replace")
            marked = _with_marker(text)
            if marked != text:
                target.write_text(marked, encoding="utf-8")
    return copied


def _with_marker(text: str) -> str:
    """Добавляет пометку о копии, не ломая YAML-заголовок навыка или роли.

    Claude Code читает заголовок между строками «---» только в самом начале файла,
    поэтому пометка ставится сразу после заголовка, а не перед ним.
    """
    if COPY_MARKER in text:
        return text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            cut = text.find("\n", end + 4)
            cut = len(text) if cut == -1 else cut + 1
            return text[:cut] + COPY_MARKER + text[cut:]
    return COPY_MARKER + text


def _venv_python(venv: Path) -> Path:
    """Путь к python внутри .venv."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def cmd_setup(media: bool, ux: bool, dry_run: bool) -> int:
    """Готовит окружение: .venv, .env, .claude, зависимости, doctor."""
    root = AGENT_ROOT
    venv = root / ".venv"
    python_path = _venv_python(venv)
    requirements = list(SETUP_REQUIREMENTS)
    if media:
        requirements.append("requirements-media.txt")
    if ux:
        requirements.append("requirements-ux.txt")
    env_path = root / ".env"
    example = root / ".env.example"
    settings = root / ".claude" / "settings.json"
    skill_sources = [
        ("skills", root / "skills", root / ".claude" / "skills"),
        ("agents", root / "agents", root / ".claude" / "agents"),
    ]

    if dry_run:
        print("План setup (--dry-run), ничего не создаю и не устанавливаю:")
        print(f"  .venv: {'уже есть' if venv.exists() else 'создал бы ' + str(venv)}")
        print(f"  зависимости: установил бы {', '.join(requirements)}")
        if env_path.exists():
            print("  .env: уже есть — не перезаписываю")
        elif example.exists():
            print("  .env: создал бы из .env.example")
        else:
            print("  .env: .env.example не найден — не создавал бы")
        for label, source, destination in skill_sources:
            if source.is_dir():
                print(f"  {label}/: скопировал бы в {destination} (в .md добавил бы пометку)")
        print(f"  .claude/settings.json: {'уже есть' if settings.exists() else 'создал бы'}")
        print("  doctor: запустил бы в конце")
        return 0

    if not venv.exists():
        print(f"Создаю .venv: {venv}")
        try:
            result = subprocess.run([sys.executable, "-m", "venv", str(venv)], check=False)
        except OSError as exc:
            print(f"ошибка: не удалось запустить venv: {exc}", file=sys.stderr)
            return 1
        if result.returncode != 0:
            print(f"ошибка: python -m venv завершился с кодом {result.returncode}", file=sys.stderr)
            return 1
    else:
        print(f".venv уже есть: {venv}")

    existing = [str(root / name) for name in requirements if (root / name).exists()]
    missing = [name for name in requirements if not (root / name).exists()]
    if missing:
        print("предупреждение: нет файлов " + ", ".join(missing))
    if existing:
        command = [str(python_path), "-m", "pip", "install"]
        for path in existing:
            command.extend(["-r", path])
        print("Устанавливаю зависимости: " + ", ".join(existing))
        try:
            result = subprocess.run(command, check=False)
        except OSError as exc:
            print(f"ошибка: не удалось запустить pip: {exc}", file=sys.stderr)
            return 1
        if result.returncode != 0:
            print(f"ошибка: pip install завершился с кодом {result.returncode}", file=sys.stderr)
            return 1
    else:
        print("Файлы requirements не найдены — зависимости не ставлю.")

    if env_path.exists():
        print(".env уже есть — не перезаписываю.")
    elif example.exists():
        shutil.copyfile(example, env_path)
        print(f".env создан из .env.example: {env_path}")
    else:
        print("предупреждение: .env.example не найден — .env не создан.")

    if media and not shutil.which("ffmpeg"):
        print("ffmpeg не найден. Аудио расшифруется и без него; для кадров из видео он нужен.")
        print("  Спросите пользователя и поставьте: macOS — brew install ffmpeg; "
              "Windows — winget install Gyan.FFmpeg; Linux — sudo apt install ffmpeg")

    cmd_sync()
    # doctor запускаем Python из .venv: модули стоят там, а не в системном Python
    if python_path.exists():
        print("Запускаю doctor в .venv.")
        return subprocess.run([str(python_path), str(Path(__file__).resolve()), "doctor"],
                              check=False).returncode
    print("Запускаю doctor.")
    return cmd_doctor()


def cmd_sync() -> int:
    """Копирует skills/ и agents/ в .claude/ и создаёт хук, если его нет. Без сети и pip."""
    root = AGENT_ROOT
    settings = root / ".claude" / "settings.json"
    for label, source, destination in (
        ("skills", root / "skills", root / ".claude" / "skills"),
        ("agents", root / "agents", root / ".claude" / "agents"),
    ):
        if not source.is_dir():
            continue
        count = _copy_tree_with_marker(source, destination)
        print(f"{label}/ → {destination}: скопировано файлов {count}")
    if settings.exists():
        print(".claude/settings.json уже есть — не трогаю.")
    else:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(SETTINGS_CONTENT, encoding="utf-8")
        print(f"Создан {settings}")
    return 0


def _reexec_in_venv() -> None:
    """Если есть .venv, перезапускает команду её Python: так видны модули, поставленные через setup."""
    if os.environ.get("STRATEG_NO_VENV") or (len(sys.argv) > 1 and sys.argv[1] == "setup"):
        return
    venv_python = _venv_python(AGENT_ROOT / ".venv")
    if not venv_python.exists():
        return
    try:
        if Path(sys.executable).resolve() == venv_python.resolve():
            return
    except OSError:
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve())] + sys.argv[1:])


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер команд диспетчера."""
    parser = argparse.ArgumentParser(
        prog="strateg.py",
        description="«Стратег»: проекты, статус, проверки, журнал и снимки.",
        epilog="Извлечение документов/страниц/таблиц/медиа — tools/extract/*.py.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="проверить окружение и записать doctor.json")

    p_new = sub.add_parser("new", help="создать проект")
    p_new.add_argument("name", help="имя проекта")

    p_status = sub.add_parser("status", help="состояние проекта или короткая сводка")
    p_status.add_argument("name", nargs="?", help="имя проекта (можно не указывать с --brief)")
    p_status.add_argument("--brief", action="store_true", help="короткая сводка до 15 строк")
    p_status.add_argument("--days", type=int, default=180, help="порог устаревания источников")

    p_snap = sub.add_parser("snapshot", help="создать снимок")
    p_snap.add_argument("name", help="имя проекта")
    p_snap.add_argument("stage", help="этап")

    p_restore = sub.add_parser("restore", help="восстановить из снимка")
    p_restore.add_argument("name", help="имя проекта")
    p_restore.add_argument("snapshot", help="имя снимка")

    p_check = sub.add_parser("check", help="проверить реестры, материалы и секреты")
    p_check.add_argument("name", help="имя проекта")

    p_impact = sub.add_parser("impact", help="цепочка влияния")
    p_impact.add_argument("name", help="имя проекта")
    p_impact.add_argument("target", help="SRC-xx или CLM-xx")

    p_log = sub.add_parser("log", help="записать событие журнала")
    p_log.add_argument("name", help="имя проекта")
    p_log.add_argument("rest", nargs=argparse.REMAINDER, help="аргументы journal.py")

    p_setup = sub.add_parser("setup", help="подготовить окружение (.venv, .env, .claude)")
    p_setup.add_argument("--media", action="store_true", help="добавить requirements-media.txt")
    p_setup.add_argument("--ux", action="store_true", help="добавить requirements-ux.txt")
    p_setup.add_argument("--dry-run", action="store_true",
                        help="только показать план, ничего не создавать и не устанавливать")
    sub.add_parser("sync", help="обновить копии навыков и ролей в .claude/ и хук (без сети)")
    p_extract = sub.add_parser("extract", help="разобрать материал: docs, web, tables или media")
    p_extract.add_argument("name", help="имя проекта")
    p_extract.add_argument("kind", choices=["docs", "web", "tables", "media"], help="тип материала")
    p_extract.add_argument("rest", nargs=argparse.REMAINDER, help="файл или URL и прочие аргументы")
    p_cost = sub.add_parser("cost", help="отчёт о стоимости проекта")
    p_cost.add_argument("name", help="имя проекта")
    p_cost.add_argument("--head-in", type=int, default=None, help="входные токены головной модели из счётчика среды")
    p_cost.add_argument("--head-out", type=int, default=None, help="выходные токены головной модели")
    p_cost.add_argument("--head-model", default="claude-opus-5-5", help="модель для цены")
    p_chart = sub.add_parser("chart", help="график по JSON-спецификации: SVG и интерактивный HTML")
    p_chart.add_argument("name", help="имя проекта")
    p_chart.add_argument("spec", help="файл спецификации, например analysis/chart-funnel.json")
    p_report = sub.add_parser("report", help="отчёт одним HTML-файлом с графиками")
    p_report.add_argument("name", help="имя проекта")
    p_report.add_argument("--source", default="output/one-pager.md", help="документ внутри проекта")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа диспетчера."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "doctor":
            return cmd_doctor()
        if args.cmd == "new":
            return cmd_new(args.name)
        if args.cmd == "status":
            return cmd_status(args.name, args.brief, args.days)
        if args.cmd == "check":
            return cmd_check(args.name)
        if args.cmd == "snapshot":
            _warn_stale_state(args.name, args.stage)
            return _dispatch(args.name, "snapshot.py", "snapshot", [args.stage])
        if args.cmd == "restore":
            return _dispatch(args.name, "snapshot.py", "restore", [args.snapshot])
        if args.cmd == "impact":
            return _dispatch(args.name, "registry.py", "impact", [args.target])
        if args.cmd == "log":
            rest = list(args.rest or [])
            if not rest:
                print("ошибка: для log нужна подкоманда журнала", file=sys.stderr)
                return 2
            try:
                project = _require_project(args.name)
            except (ValueError, FileNotFoundError) as exc:
                print(f"ошибка: {exc}", file=sys.stderr)
                return 1
            return _run("journal.py", [rest[0], str(project)] + rest[1:])
        if args.cmd == "setup":
            return cmd_setup(args.media, args.ux, args.dry_run)
        if args.cmd == "sync":
            return cmd_sync()
        if args.cmd == "extract":
            try:
                project = _require_project(args.name)
            except (ValueError, FileNotFoundError) as exc:
                print(f"ошибка: {exc}", file=sys.stderr)
                return 1
            return _run(f"extract/{args.kind}.py", [str(project)] + list(args.rest))
        if args.cmd == "cost":
            try:
                project = _require_project(args.name)
            except (ValueError, FileNotFoundError) as exc:
                print(f"ошибка: {exc}", file=sys.stderr)
                return 1
            extra: List[str] = []
            if args.head_in is not None and args.head_out is not None:
                extra = ["--head-in", str(args.head_in), "--head-out", str(args.head_out),
                         "--head-model", args.head_model]
            return _run("cost.py", [str(project)] + extra)
        if args.cmd == "chart":
            return _dispatch_plain(args.name, "chart.py", [args.spec])
        if args.cmd == "report":
            return _dispatch_plain(args.name, "report.py", ["--source", args.source])
    except OSError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    _reexec_in_venv()
    sys.exit(main())
