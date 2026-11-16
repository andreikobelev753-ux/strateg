#!/usr/bin/env python3
"""Видео/аудио → ключевые кадры или расшифровка.

Видео: ключевые кадры через ffmpeg из PATH (фильтр смены сцен
``select='gt(scene,0.3)'``) в extracted/<имя>_frames/.
Аудио: текст через faster_whisper в extracted/<имя>_transcript.md.
Длительность берётся из ffprobe и сверяется с границами config/formats.json.

Коды выхода:
  0 — успех;
  2 — неверные аргументы;
  3 — нет инструмента (ffmpeg/ffprobe/faster_whisper) — подсказка про setup --media;
  5 — файл длиннее границы из config/formats.json.

Пример:
  python3 tools/extract/media.py projects/demo materials/lecture.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import scan  # noqa: E402  - маскировка телефонов и почты в расшифровке


def resolve_input(project, value):
    """Путь к материалу: как указан, а если такого файла нет — относительно папки проекта."""
    path = Path(value)
    if path.exists() or path.is_absolute():
        return path
    candidate = Path(project) / value
    return candidate if candidate.exists() else path

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
SETUP_HINT = "python3 strateg.py setup --media"


class MissingTool(Exception):
    """Нет внешнего инструмента или модуля (код 3)."""


class LimitExceeded(Exception):
    """Файл длиннее границы (код 5)."""


def agent_root() -> Path:
    """Корень агента (config/formats.json лежит там)."""
    default = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("STRATEG_AGENT_ROOT", str(default)))


def load_formats() -> Dict[str, Any]:
    """Читает config/formats.json."""
    path = agent_root() / "config" / "formats.json"
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: ожидался объект JSON")
    return payload


def detect_kind(path: Path) -> Optional[str]:
    """Определяет вид медиа по расширению."""
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return None


def limit_minutes(kind: str, formats: Dict[str, Any]) -> int:
    """Граница длительности для вида медиа."""
    key = "video_max_minutes" if kind == "video" else "audio_max_minutes"
    value = formats.get(key, 30 if kind == "video" else 90)
    return int(value)


def check_limit(minutes: float, kind: str, formats: Dict[str, Any]) -> None:
    """Бросает LimitExceeded, если файл длиннее границы."""
    limit = limit_minutes(kind, formats)
    if minutes > limit:
        title = "видео" if kind == "video" else "аудио"
        raise LimitExceeded(
            f"{title}: файл длиннее границы {limit} мин ({minutes:.1f} мин), "
            f"граница из config/formats.json"
        )


def _ffprobe_minutes(path: Path) -> float:
    """Длительность файла в минутах через ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise MissingTool(f"нужен ffprobe: {SETUP_HINT}")
    command = [ffprobe, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MissingTool(f"ffprobe не запустился: {exc}") from exc
    if result.returncode != 0:
        raise MissingTool(f"ffprobe не смог прочитать файл: {result.stderr.strip()[:200]}")
    try:
        seconds = float(result.stdout.strip())
    except ValueError as exc:
        raise MissingTool("ffprobe не вернул длительность") from exc
    return seconds / 60.0


def extract_frames(project: Path, source: Path) -> List[Path]:
    """Извлекает ключевые кадры видео через ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MissingTool(f"нужен ffmpeg: {SETUP_HINT}")
    target = project / "extracted" / f"{source.stem}_frames"
    target.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vf", "select='gt(scene,0.3)'", "-vsync", "vfr", "-q:v", "2",
        str(target / "%04d.jpg"),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MissingTool(f"ffmpeg не запустился: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg завершился с кодом {result.returncode}: {result.stderr.strip()[:200]}")
    return sorted(target.glob("*.jpg"))


def _duration_minutes(path: Path, kind: str) -> float:
    """Длительность: ffprobe, а для аудио без ffmpeg — через PyAV (ставится с faster-whisper)."""
    try:
        return _ffprobe_minutes(path)
    except MissingTool:
        if kind == "video":
            raise
    try:
        import av  # type: ignore
        with av.open(str(path)) as container:
            return (container.duration or 0) / 1_000_000 / 60.0
    except Exception as exc:  # noqa: BLE001 - без длительности лимит не проверить
        raise MissingTool(f"не удалось узнать длительность ({exc}): {SETUP_HINT}") from exc


def _stamp(seconds: float) -> str:
    seconds = int(seconds or 0)
    return f"[{seconds // 60:02d}:{seconds % 60:02d}]"


def transcribe_audio(project: Path, source: Path) -> Path:
    """Расшифровывает аудио через faster_whisper: строка на фразу с тайм-кодом [мм:сс]."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise MissingTool(f"нужен модуль faster_whisper: {SETUP_HINT}") from exc
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(source), language=None)
    parts = [f"{_stamp(getattr(segment, 'start', 0))} {segment.text.strip()}"
             for segment in segments if getattr(segment, "text", "").strip()]
    text, masked = scan.mask_pii("\n\n".join(parts))
    target = project / "extracted" / f"{source.stem}_transcript.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    note = (f"\n\nСкрыто телефонов и адресов почты: {masked}. Имена людей обезличьте вручную (правило 9)."
            if masked else "\n\nИмена людей в расшифровке обезличьте вручную (правило 9).")
    target.write_text(f"# Расшифровка: {source.name}\n\n{text}{note}\n", encoding="utf-8")
    return target


def build_parser() -> argparse.ArgumentParser:
    """Парсер команды извлечения из медиа."""
    parser = argparse.ArgumentParser(
        prog="media.py",
        description="Видео → ключевые кадры (ffmpeg), аудио → текст (faster_whisper).",
    )
    parser.add_argument("project_dir", help="папка проекта")
    parser.add_argument("file", help="видео или аудио")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа: извлекает кадры или расшифровку."""
    args = build_parser().parse_args(argv)
    project = Path(args.project_dir)
    source = resolve_input(project, args.file)
    if not project.is_dir():
        print(f"ошибка: проект не найден: {project}", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"ошибка: файл не найден: {source}", file=sys.stderr)
        return 2
    kind = detect_kind(source)
    if kind is None:
        print(f"ошибка: неизвестный формат файла: {source.suffix}", file=sys.stderr)
        return 2
    try:
        formats = load_formats()
    except (OSError, ValueError) as exc:
        print(f"ошибка: не читается config/formats.json: {exc}", file=sys.stderr)
        return 1

    try:
        minutes = _duration_minutes(source, kind)
        check_limit(minutes, kind, formats)
        if kind == "video":
            frames = extract_frames(project, source)
            print(f"Ключевых кадров: {len(frames)}")
            print(f"Папка: {project / 'extracted' / (source.stem + '_frames')}")
        else:
            target = transcribe_audio(project, source)
            print(f"Расшифровка сохранена: {target}")
    except MissingTool as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except LimitExceeded as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except RuntimeError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
