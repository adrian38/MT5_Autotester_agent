import tempfile
import unittest
import configparser
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

    def test_recursive_set_loading_skips_run_auxiliary_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = run_tests.Path(temp_dir)
            wanted = run_dir / "gen_001" / "XAUUSD" / "H1" / "candidate.set"
            skipped_paths = [
                run_dir / "accepted_gen_001" / "score_10__candidate.set",
                run_dir / "retry_mismatch" / "run_1_all" / "candidate.set",
                run_dir / "robustness" / "run_1_pending" / "candidate.set",
                run_dir / "final_tick" / "run_1" / "real_tick_sets" / "candidate.set",
            ]
            for path in [wanted, *skipped_paths]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("set", encoding="utf-8")

            loaded = run_tests.load_set_files(run_dir, None, recursive=True)

            self.assertEqual(loaded, [wanted])

    def test_create_ini_can_override_tester_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            configs_dir = root / "configs"
            reports_dir = root / "reports"
            configs_dir.mkdir()
            reports_dir.mkdir()
            template = configparser.ConfigParser(interpolation=None)
            template.optionxform = str
            template.read_dict({"Tester": {"Expert": "", "Symbol": "XAUUSD", "Period": "H1", "Model": "1"}})

            with patch.object(run_tests, "CONFIG_DIR", configs_dir), patch.object(run_tests, "REPORT_DIR", reports_dir):
                ini_path, _report_path = run_tests.create_ini(
                    "Ultimate Breakout System_4.3.ex5",
                    1,
                    template,
                    tester_model="4",
                )

            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read(ini_path, encoding="utf-8")
            self.assertEqual(parser["Tester"]["Model"], "4")


if __name__ == "__main__":
    unittest.main()
