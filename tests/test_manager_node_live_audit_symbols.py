from __future__ import annotations

import unittest

from manager_node_runtime.live_audit_symbols import normalize_live_audit_set_symbols


class LiveAuditSymbolNormalizationTests(unittest.TestCase):
    def test_axi_us50_removes_only_standard_suffix_from_symbol_parameters(self) -> None:
        source = (
            "ForceSymbol=ETHUSD.sa||ETHUSD.sa||0||ETHUSD.sa||N\r\n"
            "Symbol=US500.SA\r\n"
            "OtherSymbol=BTCUSD.sa\r\n"
            "ForceSymbol=NAS100.fs\r\n"
            "Symbol=Tesla+\r\n"
        )

        normalized = normalize_live_audit_set_symbols(source, "Axi-US50-Live")

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
            normalize_live_audit_set_symbols(source, "Axi-US51-Live"),
            source,
        )


if __name__ == "__main__":
    unittest.main()
