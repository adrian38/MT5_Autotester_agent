from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.restore_postgres_to_sqlite import rewrite_json_document, rewrite_project_path


class RestorePostgresToSqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = Path(r"C:\clone\MT5_Autotester_agent")

    def test_rewrites_absolute_and_portable_project_paths(self) -> None:
        self.assertEqual(
            rewrite_project_path(
                r"G:\TRADING\MT5_Autotester_agent\outputs\ubs_agent\candidate.set",
                self.target,
            ),
            str(self.target / "outputs" / "ubs_agent" / "candidate.set"),
        )
        self.assertEqual(
            rewrite_project_path(r"sets\ubs_ready\ROBOFOREX\ECN\seed.set", self.target),
            str(self.target / "sets" / "ubs_ready" / "ROBOFOREX" / "ECN" / "seed.set"),
        )

    def test_does_not_rewrite_external_terminal_paths(self) -> None:
        terminal = r"C:\Program Files\RoboForex MT5 Terminal\terminal64.exe"
        self.assertEqual(rewrite_project_path(terminal, self.target), terminal)

    def test_rewrites_paths_nested_in_json(self) -> None:
        restored = json.loads(
            rewrite_json_document(
                json.dumps(
                    {
                        "set": r"G:\TRADING\MT5_Autotester_agent\sets\seed.set",
                        "items": [r"G:\TRADING\MT5_Autotester_agent\reports\missing.htm"],
                    }
                ),
                self.target,
            )
        )
        self.assertEqual(restored["set"], str(self.target / "sets" / "seed.set"))
        self.assertEqual(restored["items"], [str(self.target / "reports" / "missing.htm")])


if __name__ == "__main__":
    unittest.main()
