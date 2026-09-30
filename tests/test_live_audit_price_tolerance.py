from __future__ import annotations

from datetime import datetime, timezone
import unittest

from manager_node_runtime.live_audit import LiveAuditController
from tests.test_manager_node_live_audit import request


class LiveAuditPriceToleranceTests(unittest.TestCase):
    def test_price_tolerance_is_adapted_to_each_validated_instrument_family(self) -> None:
        now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        cases = (
            ("US30", 53462.0, 53472.5, .01, 10.5, "adaptive_indices"),
            ("DE40", 20000.0, 20010.5, .1, 10.5, "adaptive_indices"),
            ("USTECH", 25000.0, 25010.5, .01, 10.5, "adaptive_indices"),
            ("NAS100.fs", 29570.0, 29575.0, .01, 5.0, "adaptive_nasdaq"),
            ("BTCUSD", 77010.0, 77020.0, .01, 10.0, "adaptive_crypto_btc"),
            ("USDJPY", 159.650, 159.700, .001, .05, "adaptive_jpy_fx"),
            ("XAUUSD", 4807.16, 4809.21, .01, 2.05, "adaptive_gold"),
            ("XAGUSD", 67.454, 67.474, .001, .02, "adaptive_silver"),
            ("EURUSD", 1.1621, 1.1626, .00001, .0005, "adaptive_fx"),
        )
        for symbol, real_price, tester_price, point, absolute_limit, rule in cases:
            with self.subTest(symbol=symbol):
                real = [{
                    "strategy": "real", "symbol": symbol, "side": "buy", "open_time": now,
                    "close_time": now, "open_price": real_price, "volume": .1, "profit": 1.0,
                }]
                tester = [{
                    "strategy": "tester", "symbol": symbol, "side": "buy", "open_time": now,
                    "close_time": now, "open_price": tester_price, "volume": .1, "profit": 1.0,
                }]
                result = LiveAuditController._compare(
                    real, tester, {symbol: point}, request(), {"tester": 1},
                )
                row = result["comparison_detail"]["operation_comparisons"][0]
                self.assertEqual(row["status"], "matched")
                self.assertAlmostEqual(row["limits"]["open_price_absolute"], absolute_limit)
                self.assertEqual(row["limits"]["open_price_rule"], rule)

        real = [{
            "strategy": "real", "symbol": "US30", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 53462.0, "volume": .1, "profit": 1.0,
        }]
        tester = [{
            "strategy": "tester", "symbol": "US30", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 53472.51, "volume": .1, "profit": 1.0,
        }]
        outside = LiveAuditController._compare(
            real, tester, {"US30": .01}, request(), {"tester": 1},
        )
        self.assertEqual(
            outside["comparison_detail"]["operation_comparisons"][0]["reasons"],
            ["open_price"],
        )


if __name__ == "__main__":
    unittest.main()
