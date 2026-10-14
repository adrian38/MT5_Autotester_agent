from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.manual_portfolio import create_manual_portfolio


class ManualPortfolioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = self.root / "memory.sqlite"
        self.runtime = self.root / "runtime"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def uploaded(name: str, text: str) -> dict[str, str]:
        return {
            "name": f"cartera/{name}",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }

    def test_uploaded_sets_become_an_auditable_saved_portfolio(self) -> None:
        payload = {
            "name": "Cartera AXI externa",
            "portfolio_type": "balanced",
            "files": [
                self.uploaded("eurusd.set", "ForceSymbol=EURUSD||0||0||0||N\nTimeFrame=60||0||0||0||N\nStartLots=0.03||0||0||0||N\n"),
                self.uploaded("xauusd.set", "ForceSymbol=XAUUSD||0||0||0||N\nTimeFrame=H1||0||0||0||N\nStartLots=0.01||0||0||0||N\n"),
            ],
        }

        result = create_manual_portfolio(self.memory, self.runtime, payload)

        self.assertEqual(result["strategies"], 2)
        sets = Path(result["sets_folder"])
        self.assertEqual(sorted(path.name for path in sets.iterdir()), ["eurusd.set", "xauusd.set"])
        conn = sqlite3.connect(self.memory)
        try:
            conn.row_factory = sqlite3.Row
            portfolio = conn.execute("select * from portfolios where id=?", (result["portfolio_id"],)).fetchone()
            members = conn.execute(
                "select symbol,timeframe,lot,set_path from portfolio_allocations where portfolio_id=? order by symbol",
                (result["portfolio_id"],),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(portfolio["name"], "Cartera AXI externa")
        self.assertEqual(portfolio["portfolio_type"], "balanced")
        self.assertEqual([(row["symbol"], row["timeframe"], row["lot"]) for row in members], [
            ("EURUSD", "H1", 0.03), ("XAUUSD", "H1", 0.01),
        ])
        self.assertTrue(all(Path(row["set_path"]).is_file() for row in members))

    def test_a_set_without_auditable_identity_is_rejected_and_cleaned(self) -> None:
        payload = {
            "name": "Incompleto", "portfolio_type": "balanced",
            "files": [self.uploaded("bad.set", "StartLots=0.01||0||0||0||N\n")],
        }
        with self.assertRaisesRegex(ValueError, "ForceSymbol y TimeFrame"):
            create_manual_portfolio(self.memory, self.runtime, payload)
        self.assertFalse(any((self.runtime / "manual_portfolios").glob("*/sets/*.set")))


if __name__ == "__main__":
    unittest.main()
