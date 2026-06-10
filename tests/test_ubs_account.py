import unittest
from pathlib import Path

from ubs.account import account_memory_path, account_output_dir, account_seed_dir, normalize_account_type


class UBSAccountTests(unittest.TestCase):
    def test_normalize_account_type_defaults_to_ecn(self) -> None:
        self.assertEqual(normalize_account_type("pro"), "PRO")
        self.assertEqual(normalize_account_type("ECN"), "ECN")
        self.assertEqual(normalize_account_type(""), "ECN")
        self.assertEqual(normalize_account_type("demo"), "ECN")

    def test_account_paths_are_scoped_per_account(self) -> None:
        base = Path("project")

        self.assertEqual(account_memory_path(base, "PRO"), base / "outputs" / "ubs_memory_PRO.sqlite")
        self.assertEqual(account_output_dir(base, "ECN"), base / "outputs" / "ubs_agent" / "ECN")
        self.assertEqual(account_seed_dir(base, "PRO"), base / "sets" / "ubs_ready" / "PRO")


if __name__ == "__main__":
    unittest.main()
