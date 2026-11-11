import tempfile
import unittest
from pathlib import Path

from ui.multiterminal_logic import build_tester_cleanup_plan, execute_tester_cleanup


class MultiterminalTesterCleanupTests(unittest.TestCase):
    def test_cleanup_removes_runner_artifacts_and_preserves_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "TerminalData"
            tester_dir = data_dir / "Tester"
            cache_dir = tester_dir / "cache" / "nested"
            logs_dir = tester_dir / "logs"
            profiles_dir = data_dir / "MQL5" / "Profiles" / "Tester"
            for folder in (cache_dir, logs_dir, profiles_dir):
                folder.mkdir(parents=True)

            root_set = tester_dir / "generated.set"
            root_report = tester_dir / "generated.htm"
            root_other = tester_dir / "keep.dat"
            cache_file = cache_dir / "cache.bin"
            log_file = logs_dir / "20260620.log"
            matching_profile_set = profiles_dir / "generated.set"
            manual_profile_set = profiles_dir / "manual.set"
            for path in (
                root_set,
                root_report,
                root_other,
                cache_file,
                log_file,
                matching_profile_set,
                manual_profile_set,
            ):
                path.write_bytes(b"1234")

            plan = build_tester_cleanup_plan([data_dir, data_dir])

            self.assertEqual(len(plan.data_dirs), 1)
            self.assertEqual(len(plan.files), 5)
            self.assertEqual(plan.total_bytes, 20)

            deleted, freed, failures = execute_tester_cleanup(plan)

            self.assertEqual((deleted, freed, failures), (5, 20, []))
            self.assertFalse(root_set.exists())
            self.assertFalse(root_report.exists())
            self.assertFalse(cache_file.exists())
            self.assertFalse(log_file.exists())
            self.assertFalse(matching_profile_set.exists())
            self.assertTrue(root_other.exists())
            self.assertTrue(manual_profile_set.exists())

    def test_cleanup_ignores_profiles_sets_without_tester_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "TerminalData"
            profiles_dir = data_dir / "MQL5" / "Profiles" / "Tester"
            profiles_dir.mkdir(parents=True)
            manual_set = profiles_dir / "manual.set"
            manual_set.write_text("manual", encoding="utf-8")

            plan = build_tester_cleanup_plan([data_dir])

            self.assertFalse(plan.files)
            self.assertTrue(manual_set.exists())


if __name__ == "__main__":
    unittest.main()
