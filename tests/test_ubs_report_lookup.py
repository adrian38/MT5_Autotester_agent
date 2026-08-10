"""Indice de nombres de `reports/` usado por find_report_for_set."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ubs_agent
from ubs_agent import find_report_for_set, find_watchdog_snapshot_for_set


class ReportLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.reports = self.base / "reports"
        self.reports.mkdir()
        patcher = mock.patch.object(ubs_agent, "BASE_DIR", self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        ubs_agent._REPORTS_NAME_INDEX["signature"] = None

    def _touch(self, name: str) -> Path:
        path = self.reports / name
        path.write_text("x", encoding="utf-8")
        return path

    def test_exact_report_is_found(self) -> None:
        expected = self._touch("seed_0001_XAUUSD_H1_demo.htm")
        self._touch("seed_0001_XAUUSD_H1_demo.png")
        self._touch("seed_0001_XAUUSD_H1_demo-hst.png")

        found = find_report_for_set(Path("any/seed_0001_XAUUSD_H1_demo.set"))

        self.assertEqual(found, expected)

    def test_images_and_logs_are_never_returned_as_reports(self) -> None:
        self._touch("only_images.png")
        self._touch("only_images.mt5log.txt")

        self.assertIsNone(find_report_for_set(Path("any/only_images.set")))

    def test_stale_reports_are_rejected_by_min_mtime(self) -> None:
        self._touch("seed.htm")

        self.assertIsNone(find_report_for_set(Path("a/seed.set"), min_mtime=time.time() + 60))
        self.assertIsNotNone(find_report_for_set(Path("a/seed.set"), min_mtime=0))

    def test_a_stem_with_dots_only_matches_its_own_report(self) -> None:
        """Los stems llevan puntos (TSLA.NAS) y no deben capturar vecinos."""
        expected = self._touch("seed_0001_TSLA.NAS_D1_demo.htm")
        self._touch("seed_0001_TSLA.NAS_D1_demo_extra.htm")

        found = find_report_for_set(Path("a/seed_0001_TSLA.NAS_D1_demo.set"))

        self.assertEqual(found, expected)

    def test_glob_metacharacters_in_the_stem_are_literal(self) -> None:
        expected = self._touch("odd[1].htm")
        self._touch("odd1.htm")

        self.assertEqual(find_report_for_set(Path("a/odd[1].set")), expected)

    def test_index_picks_up_reports_created_after_the_first_lookup(self) -> None:
        """El indice se reconstruye al cambiar el mtime del directorio."""
        probe = Path("a/late_seed.set")
        self.assertIsNone(find_report_for_set(probe))

        expected = self._touch("late_seed.htm")

        self.assertEqual(find_report_for_set(probe), expected)

    def test_watchdog_snapshot_uses_the_newest_attempt(self) -> None:
        first = self._touch("seed.watchdog_attempt_1.mt5log.txt")
        second = self._touch("seed.watchdog_attempt_2.mt5log.txt")
        self._touch("seed.mt5log.txt")
        self._touch("other.watchdog_attempt_9.mt5log.txt")
        import os

        os.utime(first, (1_000_000, 1_000_000))
        os.utime(second, (2_000_000, 2_000_000))

        self.assertEqual(find_watchdog_snapshot_for_set(Path("a/seed.set")), second)

    def test_missing_reports_directory_is_not_an_error(self) -> None:
        for entry in self.reports.iterdir():
            entry.unlink()
        self.reports.rmdir()
        ubs_agent._REPORTS_NAME_INDEX["signature"] = None

        self.assertIsNone(find_report_for_set(Path("a/seed.set")))
        self.assertIsNone(find_watchdog_snapshot_for_set(Path("a/seed.set")))


if __name__ == "__main__":
    unittest.main()
