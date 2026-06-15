from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.mt5_report import RawDeal, _build_trades


class MT5ReportParserTests(unittest.TestCase):
    def test_build_trades_includes_entry_and_exit_commission(self) -> None:
        opened = RawDeal(
            timestamp=datetime(2020, 1, 1, 10, 0),
            ticket="1",
            symbol="META",
            trade_type="buy",
            direction="in",
            volume=0.01,
            price=100.0,
            order="1",
            commission=-0.04,
            swap=0.0,
            profit=0.0,
            balance=999.96,
            comment="",
        )
        closed = RawDeal(
            timestamp=datetime(2020, 1, 1, 11, 0),
            ticket="2",
            symbol="META",
            trade_type="sell",
            direction="out",
            volume=0.01,
            price=101.0,
            order="2",
            commission=-0.04,
            swap=-0.01,
            profit=1.00,
            balance=1000.91,
            comment="tp",
        )

        trades = _build_trades([opened, closed])

        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0].profit_loss, 0.91)


if __name__ == "__main__":
    unittest.main()
