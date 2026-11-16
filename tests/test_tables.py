"""Тесты tools/extract/tables.py: CSV без pandas, XLSX с pandas и без неё."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import fixtures  # noqa: F401
import tables

CSV = """date,amount,qty,note
2024-01-01,10,1,ок
2024-01-02,20,2,
2024-01-03,30,3,ок
2024-01-04,40,4,ок
2024-01-05,1000,5,ок
2024-01-06,-5,6,ок
"""


class TablesTests(unittest.TestCase):
    """Профиль таблицы: типы, пропуски, даты, выбросы, единицы."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = fixtures.make_bare_project(self.root)
        self.csv = self.root / "data.csv"
        self.csv.write_text(CSV, encoding="utf-8")

    def _run(self, path=None, project=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = tables.main([str(project or self.project), str(path or self.csv)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_csv_profile(self) -> None:
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        profile = (self.project / "analysis" / "data_profile.md").read_text(encoding="utf-8")
        self.assertIn("Строк: 6", profile)
        self.assertIn("| date | дата | 0 |", profile)
        self.assertIn("| amount | число | 0 |", profile)
        self.assertIn("| note | текст | 1 |", profile)
        self.assertIn("2024-01-01 … 2024-01-06", profile)
        self.assertIn("amount: отрицательных значений 1", profile)
        self.assertIn("amount: выбросов за 3 IQR: 1", profile)
        self.assertIn("amount, qty", profile)
        self.assertIn("Профиль сохранён", out)

    def test_units_in_headers_suppress_warning(self) -> None:
        path = self.root / "units.csv"
        path.write_text("date,price_rub,invoice (шт)\n2024-01-01,10,1\n2024-01-02,20,2\n",
                        encoding="utf-8")
        code, _out, err = self._run(path)
        self.assertEqual(code, 0, err)
        profile = (self.project / "analysis" / "units_profile.md").read_text(encoding="utf-8")
        self.assertIn("Числовых столбцов без единиц не найдено.", profile)

    def test_xlsx_without_pandas(self) -> None:
        path = self.root / "table.xlsx"
        path.write_bytes(b"fake xlsx")
        with mock.patch.dict(sys.modules, {"pandas": None}):
            code, _out, err = self._run(path)
        self.assertEqual(code, 3)
        self.assertIn("нужен модуль pandas: python3 strateg.py setup", err)
        self.assertFalse((self.project / "analysis" / "table_profile.md").exists())

    def test_xlsx_with_fake_pandas(self) -> None:
        class Frame:
            columns = ["date", "value_rub"]

            def itertuples(self, index=False, name=None):  # noqa: ANN001, ANN201
                return iter([("2024-01-01", 10.0), ("2024-02-01", 20.0)])

        module = types.ModuleType("pandas")
        module.read_excel = lambda path, sheet_name=0: Frame()
        path = self.root / "table.xlsx"
        path.write_bytes(b"fake xlsx")
        with mock.patch.dict(sys.modules, {"pandas": module}):
            code, _out, err = self._run(path)
        self.assertEqual(code, 0, err)
        profile = (self.project / "analysis" / "table_profile.md").read_text(encoding="utf-8")
        self.assertIn("| date | дата | 0 |", profile)
        self.assertIn("value_rub", profile)
        self.assertIn("2024-01-01 … 2024-02-01", profile)

    def test_xlsx_nan_counts_as_missing(self) -> None:
        class Frame:
            columns = ["value_rub"]

            def itertuples(self, index=False, name=None):  # noqa: ANN001, ANN201
                return iter([(float("nan"),), (10.0,)])

        module = types.ModuleType("pandas")
        module.read_excel = lambda path, sheet_name=0: Frame()
        path = self.root / "nan.xlsx"
        path.write_bytes(b"fake xlsx")
        with mock.patch.dict(sys.modules, {"pandas": module}):
            code, _out, err = self._run(path)
        self.assertEqual(code, 0, err)
        profile = (self.project / "analysis" / "nan_profile.md").read_text(encoding="utf-8")
        self.assertIn("| value_rub | число | 1 |", profile)

    def test_unsupported_format(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("просто текст", encoding="utf-8")
        code, _out, err = self._run(path)
        self.assertEqual(code, 1)
        self.assertIn("неподдерживаемый формат", err)

    def test_empty_csv(self) -> None:
        path = self.root / "empty.csv"
        path.write_text("", encoding="utf-8")
        code, _out, err = self._run(path)
        self.assertEqual(code, 1)
        self.assertIn("файл пуст", err)

    def test_missing_file(self) -> None:
        code, _out, err = self._run(self.root / "nope.csv")
        self.assertEqual(code, 2)
        self.assertIn("файл не найден", err)

    def test_missing_project(self) -> None:
        code, _out, err = self._run(project=self.root / "nope")
        self.assertEqual(code, 2)
        self.assertIn("проект не найден", err)


if __name__ == "__main__":
    unittest.main()
