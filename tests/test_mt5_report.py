from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.excel import _to_float as excel_to_float
from portfolio_manager.mt5_report import RawDeal, _build_trades, _to_float as mt5_to_float
from ubs.score import _extract_drawdown, _to_float as score_to_float


class MT5ReportParserTests(unittest.TestCase):
    def test_out_of_order_html_deals_do_not_cross_two_closures(self) -> None:
        def deal(ticket: str, moment: datetime, trade_type: str, direction: str, price: float) -> RawDeal:
            return RawDeal(
                timestamp=moment, ticket=ticket, symbol="XAUUSD", trade_type=trade_type,
                direction=direction, volume=0.03, price=price, order=ticket,
                commission=0.0, swap=0.0, profit=0.0, balance=5000.0, comment="",
            )

        early_open = deal("6", datetime(2026, 9, 1, 11, 9, 50), "sell", "in", 4399.18)
        early_close = deal("7", datetime(2026, 9, 1, 11, 12, 58), "buy", "out", 4399.21)
        late_open = deal("8", datetime(2026, 9, 1, 16, 12, 4), "sell", "in", 4327.25)
        late_close = deal("9", datetime(2026, 9, 1, 16, 13, 0), "buy", "out", 4331.56)

        trades = _build_trades([late_open, early_open, early_close, late_close])

        self.assertEqual([(trade.ticket, trade.open_time, trade.close_time) for trade in trades], [
            ("6", early_open.timestamp, early_close.timestamp),
            ("8", late_open.timestamp, late_close.timestamp),
        ])
        self.assertTrue(all(trade.close_time >= trade.open_time for trade in trades))

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

    def test_build_trades_preserves_partial_close_profit(self) -> None:
        opened = RawDeal(
            timestamp=datetime(2020, 1, 1, 10, 0),
            ticket="1",
            symbol="META",
            trade_type="buy",
            direction="in",
            volume=1.0,
            price=100.0,
            order="1",
            commission=-1.0,
            swap=0.0,
            profit=0.0,
            balance=999.0,
            comment="",
        )
        first_close = RawDeal(
            timestamp=datetime(2020, 1, 1, 11, 0),
            ticket="2",
            symbol="META",
            trade_type="sell",
            direction="out",
            volume=0.5,
            price=101.0,
            order="2",
            commission=-0.5,
            swap=0.0,
            profit=10.0,
            balance=1008.5,
            comment="partial",
        )
        second_close = RawDeal(
            timestamp=datetime(2020, 1, 1, 12, 0),
            ticket="3",
            symbol="META",
            trade_type="sell",
            direction="out",
            volume=0.5,
            price=102.0,
            order="3",
            commission=-0.5,
            swap=0.0,
            profit=20.0,
            balance=1028.0,
            comment="final",
        )

        trades = _build_trades([opened, first_close, second_close])

        self.assertEqual(len(trades), 2)
        self.assertAlmostEqual(sum(trade.profit_loss for trade in trades), 28.0)
        self.assertAlmostEqual(trades[0].profit_loss, 9.0)
        self.assertAlmostEqual(trades[1].profit_loss, 19.0)

    def test_number_parsing_handles_mixed_thousand_and_decimal_separators(self) -> None:
        for parser in (mt5_to_float, score_to_float, excel_to_float):
            with self.subTest(parser=parser.__module__):
                self.assertEqual(parser("1,234.56"), 1234.56)
                self.assertEqual(parser("1.234,56"), 1234.56)
                self.assertEqual(parser("1 234,56"), 1234.56)
                self.assertEqual(parser("12.345,67 (8.90%)"), 12345.67)

    def test_drawdown_parser_handles_thousand_separators(self) -> None:
        self.assertEqual(_extract_drawdown("1.234,56 (8,90%)"), (1234.56, 8.9))


if __name__ == "__main__":
    unittest.main()
