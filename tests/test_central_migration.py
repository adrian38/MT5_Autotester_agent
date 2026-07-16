from __future__ import annotations

import unittest

from tools.migrate_sqlite_to_postgres import canonical_seed_path


class CentralMigrationTests(unittest.TestCase):
    def test_seed_path_is_portable_across_legacy_and_current_layouts(self) -> None:
        expected = r"sets\ubs_ready\ROBOFOREX\ECN\AUDJPY\D1\seed.set"
        self.assertEqual(
            canonical_seed_path(
                r"C:\old\sets\ubs_ready\ECN\AUDJPY\D1\seed.set",
                "ROBOFOREX",
                "ECN",
            ),
            expected,
        )
        self.assertEqual(
            canonical_seed_path(
                r"G:\repo\sets\ubs_ready\ROBOFOREX\ECN\AUDJPY\D1\seed.set",
                "ROBOFOREX",
                "ECN",
            ),
            expected,
        )

    def test_seed_path_supports_unc_sources(self) -> None:
        self.assertEqual(
            canonical_seed_path(
                r"\\node\share\sets\ubs_ready\ICTRADING\STANDARD\XAUUSD\H1\seed.set",
                "ICTRADING",
                "STANDARD",
            ),
            r"sets\ubs_ready\ICTRADING\STANDARD\XAUUSD\H1\seed.set",
        )

    def test_non_seed_path_is_preserved(self) -> None:
        value = r"C:\external\seed.set"
        self.assertEqual(canonical_seed_path(value, "AXI", "STANDARD"), value)


if __name__ == "__main__":
    unittest.main()
