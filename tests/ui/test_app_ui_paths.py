import tempfile
import unittest
from pathlib import Path

from app_ui import resolve_existing_local_file


class AppUiPathTests(unittest.TestCase):
    def test_resolves_legacy_report_path_to_current_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "MT5_Autotester_agent_AXI"
            report = base_dir / "reports" / "tick_001.htm"
            report.parent.mkdir(parents=True)
            report.write_text("ok", encoding="utf-8")
            legacy_path = Path(temp_dir) / "MT5_Autotester_agent" / "reports" / report.name

            resolved = resolve_existing_local_file(legacy_path, base_dir)

            self.assertEqual(resolved, report)

    def test_keeps_existing_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = Path(temp_dir) / "reports" / "report.htm"
            existing.parent.mkdir()
            existing.write_text("ok", encoding="utf-8")

            resolved = resolve_existing_local_file(existing, Path(temp_dir) / "other")

            self.assertEqual(resolved, existing)
