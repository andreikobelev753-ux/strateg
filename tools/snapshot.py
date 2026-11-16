#!/usr/bin/env python3
"""Снимки состояния проекта «Стратег».

Снимок — папка ``drafts/NN_<stage>_<YYYYmmdd-HHMMSS>/`` с копиями ``state/``
и ``output/``. Восстановление сначала переносит текущие ``state/`` и ``output/``
в ``drafts/NN_before-restore_<время>/``, затем копирует их из снимка. Ничего
не удаляется. Журнал при этом не откатывается: в нём остаётся вся история и все
расходы, а событие restore с полем rollback_to отмечает, какие шаги после снимка
больше не считаются выполненными.

Примеры:
  python3 tools/snapshot.py snapshot projects/demo S3.1
  python3 tools/snapshot.py list projects/demo
  python3 tools/snapshot.py restore projects/demo 03_S3.1_20261105-010203
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # поддержка запуска и как скрипта, и как пакетного модуля
    from . import journal
except ImportError:  # pragma: no cover - зависит от способа запуска
    import journal  # type: ignore

NUMBER_RE = re.compile(r"^(\d+)_")


def _stamp() -> str:
    """Метка времени для имени снимка."""
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _next_number(drafts: Path) -> int:
    """Следующий свободный номер снимка (с ведущими нулями при печати)."""
    best = 0
    if drafts.exists():
        for item in drafts.iterdir():
            match = NUMBER_RE.match(item.name)
            if match:
                best = max(best, int(match.group(1)))
    return best + 1


def _unique_dir(drafts: Path, name: str) -> Path:
    """Возвращает свободный путь в drafts для имени name."""
    candidate = drafts / name
    counter = 2
    while candidate.exists():
        candidate = drafts / f"{name}-{counter}"
        counter += 1
    return candidate


def _snapshot_stage(name: str) -> str:
    """Достаёт имя этапа из имени снимка NN_<stage>_<time>."""
    parts = name.split("_")
    if len(parts) >= 3:
        return parts[1]
    return "restore"


def create_snapshot(project_dir: Any, stage: str) -> Path:
    """Создаёт снимок state/ и output/ и возвращает путь к нему."""
    project = Path(project_dir)
    drafts = project / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    number = _next_number(drafts)
    target = _unique_dir(drafts, f"{number:02d}_{stage}_{_stamp()}")
    target.mkdir(parents=True)
    for name in ("state", "output"):
        source = project / name
        destination = target / name
        if source.exists():
            shutil.copytree(source, destination)
        else:
            destination.mkdir(parents=True)
    print(f"Снимок создан: {target}")
    return target


def list_snapshots(project_dir: Any) -> List[Dict[str, Any]]:
    """Список снимков проекта, отсортированный по номеру."""
    drafts = Path(project_dir) / "drafts"
    items: List[Dict[str, Any]] = []
    if not drafts.exists():
        return items
    for entry in drafts.iterdir():
        if not entry.is_dir():
            continue
        match = NUMBER_RE.match(entry.name)
        if not match:
            continue
        state_files = sum(1 for p in (entry / "state").rglob("*") if p.is_file()) if (entry / "state").exists() else 0
        output_files = sum(1 for p in (entry / "output").rglob("*") if p.is_file()) if (entry / "output").exists() else 0
        items.append(
            {
                "num": int(match.group(1)),
                "name": entry.name,
                "path": entry,
                "state_files": state_files,
                "output_files": output_files,
                "mtime": datetime.fromtimestamp(entry.stat().st_mtime).astimezone(),
            }
        )
    items.sort(key=lambda item: (item["num"], item["name"]))
    return items


def _print_snapshots(project_dir: Any) -> None:
    items = list_snapshots(project_dir)
    if not items:
        print("Снимков нет.")
        return
    print(f"Снимки в {Path(project_dir) / 'drafts'}:")
    for item in items:
        print(
            f"  {item['num']:02d}  {item['name']}  "
            f"state: {item['state_files']} ф., output: {item['output_files']} ф., "
            f"{item['mtime'].strftime('%Y-%m-%d %H:%M:%S')}"
        )


def restore(project_dir: Any, snapshot_name: str) -> Tuple[Path, Path]:
    """Восстанавливает state/ и output/ из снимка, сохраняя текущее в before-restore."""
    project = Path(project_dir)
    drafts = project / "drafts"
    if not re.fullmatch(r"[^/\\]+", snapshot_name or ""):
        raise ValueError("имя снимка не должно содержать разделителей пути")
    source = drafts / snapshot_name
    if not source.is_dir():
        raise FileNotFoundError(f"снимок не найден: {source}")

    number = _next_number(drafts)
    before = _unique_dir(drafts, f"{number:02d}_before-restore_{_stamp()}")
    before.mkdir(parents=True)

    for name in ("state", "output"):
        current = project / name
        if current.exists():
            shutil.move(str(current), str(before / name))

    for name in ("state", "output"):
        from_snapshot = source / name
        destination = project / name
        if from_snapshot.exists():
            shutil.copytree(from_snapshot, destination)
        else:
            destination.mkdir(parents=True)

    # Журнал только дополняется и при откате не возвращается к копии из снимка:
    # иначе пропали бы шаги и расходы, сделанные после снимка. Возвращаем на место
    # полный журнал, а точку отката пишем в событие restore (rollback_to).
    snapshot_journal = source / "state" / "journal.jsonl"
    rollback_to = journal.line_count(path=snapshot_journal)
    full_journal = before / "state" / "journal.jsonl"
    if full_journal.exists():
        shutil.copy2(full_journal, journal.journal_path(project))

    stage = _snapshot_stage(snapshot_name)
    journal.append_event(
        project,
        "restore",
        stage,
        f"snapshot:{snapshot_name}",
        tool="snapshot.py",
        note=f"restore from {snapshot_name}; before: {before.name}",
        extra={"rollback_to": rollback_to},
    )
    print(f"Восстановлено из {snapshot_name}; текущее сохранено в {before}")
    return source, before


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер команд снимков."""
    parser = argparse.ArgumentParser(
        prog="snapshot.py",
        description="Снимки state/ и output/ проекта: создать, показать, восстановить.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="создать снимок")
    p_snap.add_argument("project_dir", help="папка проекта")
    p_snap.add_argument("stage", help="этап, например S3.1")

    p_list = sub.add_parser("list", help="список снимков")
    p_list.add_argument("project_dir", help="папка проекта")

    p_restore = sub.add_parser("restore", help="восстановить из снимка")
    p_restore.add_argument("project_dir", help="папка проекта")
    p_restore.add_argument("snapshot_name", help="имя снимка в drafts/")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа CLI снимков."""
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "snapshot":
            create_snapshot(args.project_dir, args.stage)
            return 0
        if args.cmd == "list":
            _print_snapshots(args.project_dir)
            return 0
        if args.cmd == "restore":
            restore(args.project_dir, args.snapshot_name)
            return 0
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
