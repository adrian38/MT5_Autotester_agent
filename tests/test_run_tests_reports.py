import tempfile
import unittest
from unittest.mock import patch

import run_tests


class ListLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)


class CopyReportsToProjectTests(unittest.TestCase):
    def test_removes_copied_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            reports_dir = root / "reports"
            terminal_dir = root / "terminal"
            reports_dir.mkdir()
            terminal_dir.mkdir()
            source = terminal_dir / "sample.htm"
            source.write_text("report", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                copied = run_tests.copy_reports_to_project([source], logger)

            destination = reports_dir / source.name
            self.assertEqual(copied, [destination])
            self.assertEqual(destination.read_text(encoding="utf-8"), "report")
            self.assertFalse(source.exists())
            self.assertTrue(any("Reporte origen eliminado" in message for message in logger.messages))

    def test_removes_local_project_report_when_it_was_not_copied_now(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = run_tests.Path(temp_dir) / "reports"
            reports_dir.mkdir()
            source = reports_dir / "sample.htm"
            source.write_text("report", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                copied = run_tests.copy_reports_to_project([source], logger)

            self.assertEqual(copied, [])
            self.assertFalse(source.exists())
            self.assertTrue(any("Reporte local previo eliminado" in message for message in logger.messages))

    def test_keeps_destination_when_external_report_overwrites_local_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            reports_dir = root / "reports"
            terminal_dir = root / "terminal"
            reports_dir.mkdir()
            terminal_dir.mkdir()
            local_source = reports_dir / "sample.htm"
            external_source = terminal_dir / "sample.htm"
            local_source.write_text("old", encoding="utf-8")
            external_source.write_text("new", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                copied = run_tests.copy_reports_to_project([local_source, external_source], logger)

            self.assertEqual(copied, [local_source])
            self.assertTrue(local_source.exists())
            self.assertEqual(local_source.read_text(encoding="utf-8"), "new")
            self.assertFalse(external_source.exists())


if __name__ == "__main__":
    unittest.main()
