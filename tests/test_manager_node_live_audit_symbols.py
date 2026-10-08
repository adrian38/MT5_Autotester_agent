from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from manager_node_runtime.live_audit_symbols import (
    audit_symbol_key,
    broker_symbol_spellings,
    normalize_live_audit_set_symbols,
)


AXI_UNIVERSE = (
    "[Forex]\n"
    "symbols=EURUSD.sa,USDJPY.sa\n"
    "\n"
    "[Metals]\n"
    "symbols=XAUUSD.sa\n"
    "\n"
    "[Crypto]\n"
    "symbols=BTCUSD.sa,ETHUSD.sa\n"
    "\n"
    "[Indices]\n"
    "symbols=NAS100.fs,US500.sa\n"
    "\n"
    "[Stocks]\n"
    "symbols=Tesla+,Apple+\n"
)


class LiveAuditSymbolKeyTests(unittest.TestCase):
    def test_account_suffix_does_not_change_the_key(self) -> None:
        self.assertEqual(audit_symbol_key("XAUUSD.sa"), audit_symbol_key("XAUUSD"))
        self.assertEqual(audit_symbol_key("NAS100.fs"), audit_symbol_key("NAS100"))
        self.assertEqual(audit_symbol_key("Tesla+"), audit_symbol_key("TESLA+"))

    def test_market_suffixes_stay_part_of_the_key(self) -> None:
        self.assertNotEqual(audit_symbol_key("SIL.NYSE"), audit_symbol_key("SIL.US-24"))
        self.assertNotEqual(audit_symbol_key("SIL.NYSE"), audit_symbol_key("SIL"))

    def test_empty_symbol_has_no_key(self) -> None:
        self.assertEqual(audit_symbol_key(None), "")


class BrokerSymbolSpellingTests(unittest.TestCase):
    def _universe(self, directory: str, text: str = AXI_UNIVERSE) -> Path:
        project = Path(directory)
        (project / "assets").mkdir()
        (project / "assets" / "axi_assets.ini").write_text(text, encoding="utf-8")
        return project

    def test_each_symbol_is_indexed_by_its_suffix_free_key(self) -> None:
        with TemporaryDirectory() as directory:
            spellings = broker_symbol_spellings(self._universe(directory), "AXI")

        self.assertEqual(spellings[audit_symbol_key("XAUUSD")], "XAUUSD.sa")
        self.assertEqual(spellings[audit_symbol_key("NAS100")], "NAS100.fs")
        self.assertEqual(spellings[audit_symbol_key("tesla+")], "Tesla+")

    def test_ambiguous_keys_are_dropped_instead_of_guessed(self) -> None:
        text = AXI_UNIVERSE + "\n[Energies]\nsymbols=USOIL.sa,USOIL.fs\n"
        with TemporaryDirectory() as directory:
            spellings = broker_symbol_spellings(self._universe(directory, text), "AXI")

        self.assertNotIn(audit_symbol_key("USOIL"), spellings)
        self.assertIn(audit_symbol_key("XAUUSD"), spellings)

    def test_missing_universe_returns_no_spellings(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertEqual(broker_symbol_spellings(Path(directory), "AXI"), {})


class LiveAuditSymbolNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        project = Path(self._directory.name)
        (project / "assets").mkdir()
        (project / "assets" / "axi_assets.ini").write_text(AXI_UNIVERSE, encoding="utf-8")
        self.spellings = broker_symbol_spellings(project, "AXI")

    def test_axi_us50_removes_only_standard_suffix_from_symbol_parameters(self) -> None:
        source = (
            "ForceSymbol=ETHUSD.sa||ETHUSD.sa||0||ETHUSD.sa||N\r\n"
            "Symbol=US500.SA\r\n"
            "OtherSymbol=BTCUSD.sa\r\n"
            "ForceSymbol=NAS100.fs\r\n"
            "Symbol=Tesla+\r\n"
        )

        normalized = normalize_live_audit_set_symbols(source, "Axi-US50-Live", self.spellings)

        self.assertEqual(
            normalized,
            (
                "ForceSymbol=ETHUSD||ETHUSD.sa||0||ETHUSD.sa||N\r\n"
                "Symbol=US500\r\n"
                "OtherSymbol=BTCUSD.sa\r\n"
                "ForceSymbol=NAS100.fs\r\n"
                "Symbol=Tesla+\r\n"
            ),
        )

    def test_other_axi_servers_keep_standard_suffix(self) -> None:
        source = "ForceSymbol=ETHUSD.sa\n"

        self.assertEqual(
            normalize_live_audit_set_symbols(source, "Axi-US51-Live", self.spellings),
            source,
        )

    def test_suffixed_server_adapts_a_set_written_without_suffix(self) -> None:
        source = (
            "ForceSymbol=USDJPY||USDJPY||0||USDJPY||N\r\n"
            "Symbol=NAS100\r\n"
            "OtherSymbol=BTCUSD\r\n"
            "ForceSymbol=APPLE+\r\n"
        )

        normalized = normalize_live_audit_set_symbols(source, "Axi-US51-Live", self.spellings)

        self.assertEqual(
            normalized,
            (
                "ForceSymbol=USDJPY.sa||USDJPY||0||USDJPY||N\r\n"
                "Symbol=NAS100.fs\r\n"
                "OtherSymbol=BTCUSD\r\n"
                "ForceSymbol=Apple+\r\n"
            ),
        )

    def test_symbol_outside_the_broker_universe_is_left_untouched(self) -> None:
        source = "ForceSymbol=USDMXN\n"

        self.assertEqual(
            normalize_live_audit_set_symbols(source, "Axi-US51-Live", self.spellings),
            source,
        )

    def test_without_universe_the_set_is_left_untouched(self) -> None:
        source = "ForceSymbol=USDJPY\n"

        self.assertEqual(normalize_live_audit_set_symbols(source, "Axi-US51-Live"), source)


if __name__ == "__main__":
    unittest.main()
