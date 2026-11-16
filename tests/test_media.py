"""Тесты tools/extract/media.py: границы форматов и отсутствие инструментов."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fixtures  # noqa: F401
import media


class MediaTests(unittest.TestCase):
    """Границы из config/formats.json и коды 3/5 без реальных ffmpeg."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = fixtures.make_bare_project(self.root)

    def _run(self, path, project=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = media.main([str(project or self.project), str(path)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_formats_config(self) -> None:
        formats = media.load_formats()
        self.assertEqual(formats["video_max_minutes"], 30)
        self.assertEqual(formats["audio_max_minutes"], 90)
        self.assertEqual(formats["audio_languages"], ["ru", "en"])
        self.assertIn("_note", formats)

    def test_check_limit(self) -> None:
        formats = media.load_formats()
        media.check_limit(30, "video", formats)
        media.check_limit(90, "audio", formats)
        with self.assertRaises(media.LimitExceeded) as video:
            media.check_limit(45, "video", formats)
        self.assertIn("30", str(video.exception))
        with self.assertRaises(media.LimitExceeded) as audio:
            media.check_limit(91, "audio", formats)
        self.assertIn("90", str(audio.exception))

    def test_detect_kind(self) -> None:
        self.assertEqual(media.detect_kind(Path("a.mp4")), "video")
        self.assertEqual(media.detect_kind(Path("a.mp3")), "audio")
        self.assertIsNone(media.detect_kind(Path("a.txt")))

    def test_video_without_tools(self) -> None:
        video = self.root / "movie.mp4"
        video.write_bytes(b"fake")
        with mock.patch.object(media.shutil, "which", return_value=None):
            code, _out, err = self._run(video)
        self.assertEqual(code, 3)
        self.assertIn("python3 strateg.py setup --media", err)
        self.assertFalse((self.project / "extracted" / "movie_frames").exists())

    def test_audio_without_tools(self) -> None:
        audio = self.root / "voice.mp3"
        audio.write_bytes(b"fake")
        with mock.patch.object(media.shutil, "which", return_value=None):
            code, _out, err = self._run(audio)
        self.assertEqual(code, 3)
        self.assertIn("python3 strateg.py setup --media", err)

    def test_unknown_extension(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("text", encoding="utf-8")
        code, _out, err = self._run(path)
        self.assertEqual(code, 2)
        self.assertIn("неизвестный формат", err)

    def test_missing_file(self) -> None:
        code, _out, err = self._run(self.root / "nope.mp4")
        self.assertEqual(code, 2)
        self.assertIn("файл не найден", err)

    def test_missing_project(self) -> None:
        video = self.root / "movie.mp4"
        video.write_bytes(b"fake")
        code, _out, err = self._run(video, project=self.root / "nope")
        self.assertEqual(code, 2)
        self.assertIn("проект не найден", err)


if __name__ == "__main__":
    unittest.main()
