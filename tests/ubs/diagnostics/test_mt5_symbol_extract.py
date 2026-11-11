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

    def test_group_symbols_classifies_axi_suffixed_forex_as_forex(self) -> None:
        groups = group_symbols_for_universe(
            [
                ExtractedSymbol("USDJPY.sa"),
                ExtractedSymbol("EURUSD.sa"),
                ExtractedSymbol("AUDJPY.sa"),
                ExtractedSymbol("GBPUSD.sa"),
                ExtractedSymbol("GBPJPY.sa"),
            ]
        )

        self.assertEqual(
            groups["Forex"],
            ["AUDJPY.sa", "EURUSD.sa", "GBPJPY.sa", "GBPUSD.sa", "USDJPY.sa"],
        )
        self.assertNotIn("Other", groups)
        self.assertNotIn("Bonds", groups)

    def test_group_symbols_prefers_axi_mt5_paths_over_name_hints(self) -> None:
        groups = group_symbols_for_universe(
            [
                ExtractedSymbol("Siemens+", "SHARES_COMMFREE\\EU_SHARES_COMMFREE\\Siemens+"),
                ExtractedSymbol("InvescoDBPrecious+", "SHARES_COMMFREE\\US_SHARES_COMMFREE\\US_ETF\\InvescoDBPrecious+"),
                ExtractedSymbol("BitwiseCrypto+", "SHARES_COMMFREE\\US_SHARES_COMMFREE\\US_ETF\\BitwiseCrypto+"),
                ExtractedSymbol("NATGAS.fs", "FUTURES\\FUT_COMMODITY2\\NATGAS.fs"),
                ExtractedSymbol("COPPER.fs", "FUTURES\\FUT_COMMODITY2\\COPPER.fs"),
                ExtractedSymbol("NAS100.fs", "FUTURES\\FUT_INDICES2\\NAS100.fs"),
            ]
        )

        self.assertEqual(
            groups["Stocks"],
            ["BitwiseCrypto+", "InvescoDBPrecious+", "Siemens+"],
        )
        self.assertEqual(groups["Energies"], ["NATGAS.fs"])
        self.assertEqual(groups["Commodities"], ["COPPER.fs"])
        self.assertEqual(groups["Indices"], ["NAS100.fs"])

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

    def test_write_asset_universe_can_reclassify_existing_symbols_from_mt5_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "axi_assets.ini"
            path.write_text(
                "[Metals]\n"
                "symbols=Siemens+,XAUUSD.sa\n\n"
                "[Other]\n"
                "symbols=EURUSD.sa\n",
                encoding="utf-8",
            )

            result = write_asset_universe_from_symbols(
                path,
                [
                    ExtractedSymbol("Siemens+", "SHARES_COMMFREE\\EU_SHARES_COMMFREE\\Siemens+"),
                    ExtractedSymbol("XAUUSD.sa", "AXISELECT_STANDARD_METALS\\STD_METALS1\\XAUUSD.sa"),
                    ExtractedSymbol("EURUSD.sa", "AXISELECT_STANDARD_FX\\STD_FX1\\EURUSD.sa"),
                ],
                backup=False,
                preserve_existing_groups=False,
            )

            text = path.read_text(encoding="utf-8")
            self.assertEqual(result.counts["Stocks"], 1)
            self.assertEqual(result.counts["Metals"], 1)
            self.assertEqual(result.counts["Forex"], 1)
            self.assertIn("[Stocks]", text)
            self.assertIn("symbols=Siemens+", text)
            self.assertIn("[Forex]", text)
            self.assertIn("symbols=EURUSD.sa", text)
            self.assertNotIn("[Other]", text)


if __name__ == "__main__":
    unittest.main()
