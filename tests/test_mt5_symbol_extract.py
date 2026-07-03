from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ubs.mt5_symbol_extract import (
    ExtractedSymbol,
    group_symbols_for_universe,
    write_asset_universe_from_symbols,
)


class MT5SymbolExtractTests(unittest.TestCase):
    def test_group_symbols_keeps_common_asset_types_apart(self) -> None:
        groups = group_symbols_for_universe(
            [
                ExtractedSymbol("EURUSD", "Forex\\Majors"),
                ExtractedSymbol("XAUUSD", "Metals"),
                ExtractedSymbol("US500", "Indices"),
                ExtractedSymbol("ETHUSD", "Crypto"),
                ExtractedSymbol("SSSS.NAS", "Stocks\\NASDAQ"),
            ]
        )

        self.assertEqual(groups["Forex"], ["EURUSD"])
        self.assertEqual(groups["Metals"], ["XAUUSD"])
        self.assertEqual(groups["Indices"], ["US500"])
        self.assertEqual(groups["Crypto"], ["ETHUSD"])
        self.assertEqual(groups["Stocks"], ["SSSS.NAS"])

    def test_write_asset_universe_syncs_removed_and_added_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ictrading_assets.ini"
            path.write_text(
                "[Forex]\n"
                "symbols=EURUSD,USDJPY\n\n"
                "[Indices]\n"
                "symbols=US500\n\n"
                "[CommonAliases]\n"
                "SPX500=US500\n",
                encoding="utf-8",
            )

            result = write_asset_universe_from_symbols(
                path,
                [
                    ExtractedSymbol("EURUSD", "Forex"),
                    ExtractedSymbol("US500", "Indices"),
                    ExtractedSymbol("XAUUSD", "Metals"),
                ],
            )

            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())
            self.assertEqual(result.counts["Forex"], 1)
            self.assertEqual(result.counts["Indices"], 1)
            self.assertEqual(result.counts["Metals"], 1)
            self.assertEqual(result.added_symbols, ("XAUUSD",))
            self.assertEqual(result.removed_symbols, ("USDJPY",))
            text = path.read_text(encoding="utf-8")
            self.assertIn("symbols=EURUSD", text)
            self.assertIn("symbols=US500", text)
            self.assertIn("symbols=XAUUSD", text)
            self.assertNotIn("USDJPY", text)
            self.assertIn("SPX500=US500", text)


if __name__ == "__main__":
    unittest.main()
