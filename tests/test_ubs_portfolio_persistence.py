from __future__ import annotations

import json
import sqlite3
import unittest

from portfolio_manager.ubs_portfolio import PortfolioType
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin


class _PortfolioLogic(UBSPortfolioLogicMixin):
    pass


class UBSPortfolioPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logic = _PortfolioLogic()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.logic._ensure_portfolio_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_quarantine_is_a_hard_candidate_gate(self) -> None:
        self.conn.executescript(
            """
            create table candidates (
                id integer primary key, set_path text, symbol text, target_symbol text,
                period text, family text, report_path text, status text
            );
            create table candidate_robustness (
                candidate_id integer primary key, report_path text, status text
            );
            create table candidate_final_tick (
                candidate_id integer primary key, status text
            );
            create table candidate_final_tick_6m (
                candidate_id integer primary key, status text, ohlc_report_path text,
                real_tick_report_path text, from_date text, to_date text
            );
            insert into candidates values
                (7, 'C:/sets/strategy.set', 'EURUSD', 'EURUSD', 'H1', 'test', 'base.htm', 'accepted');
            insert into candidate_robustness values (7, 'robust.htm', 'accepted');
            insert into candidate_final_tick values (7, 'accepted');
            insert into candidate_final_tick_6m values
                (7, 'accepted', 'ohlc.htm', 'tick.htm', '2026.01.01', '2026.06.30');
            """
        )
        self.assertEqual(len(self.logic._final_tick_passed_candidates(self.conn, "ECN")), 1)

        self.conn.execute(
            """
            insert into portfolio_quarantine (
                account_type, candidate_id, set_path, symbol, timeframe,
                reason, source_portfolio_id, quarantined_at
            ) values ('ECN', 7, 'C:/sets/strategy.set', 'EURUSD', 'H1', 'manual', 3, '2026-06-24T12:00:00')
            """
        )
        self.assertEqual(self.logic._final_tick_passed_candidates(self.conn, "ECN"), [])

    def test_used_set_query_can_exclude_portfolio_being_repaired(self) -> None:
        for portfolio_id, set_path in ((1, "C:/sets/current.set"), (2, "C:/sets/other.set")):
            self.conn.execute(
                """
                insert into portfolios (id, created_at, portfolio_type)
                values (?, '2026-06-24T12:00:00', 'balanced')
                """,
                (portfolio_id,),
            )
            self.conn.execute(
                """
                insert into portfolio_allocations (
                    portfolio_id, set_id, candidate_id, symbol, units, lot,
                    net_profit_contribution, standalone_valley_dd, standalone_point_dd, set_path
                ) values (?, ?, ?, 'EURUSD', 1, 0.01, 10, 1, 1, ?)
                """,
                (portfolio_id, set_path, str(portfolio_id), set_path),
            )
        used = self.logic._used_set_paths(
            self.conn,
            PortfolioType.BALANCED,
            exclude_portfolio_id=1,
        )
        self.assertEqual(used, ["C:/sets/other.set"])

    def test_aggressive_is_blocked_only_by_other_aggressive_portfolios(self) -> None:
        for portfolio_id, portfolio_type, set_path in (
            (1, "aggressive", "C:/sets/current.set"),
            (2, "balanced", "C:/sets/balanced.set"),
            (3, "aggressive", "C:/sets/aggressive.set"),
        ):
            self.conn.execute(
                "insert into portfolios (id, created_at, portfolio_type) values (?, '2026-06-24', ?)",
                (portfolio_id, portfolio_type),
            )
            self.conn.execute(
                """
                insert into portfolio_allocations (
                    portfolio_id, set_id, candidate_id, symbol, units, lot,
                    net_profit_contribution, standalone_valley_dd, standalone_point_dd, set_path
                ) values (?, ?, ?, 'EURUSD', 1, 0.01, 10, 1, 1, ?)
                """,
                (portfolio_id, set_path, str(portfolio_id), set_path),
            )
        used = self.logic._used_set_paths(
            self.conn,
            PortfolioType.AGGRESSIVE,
            exclude_portfolio_id=1,
        )
        self.assertEqual(used, ["C:/sets/aggressive.set"])

    def test_aggressive_correlation_uses_only_other_aggressive_curves(self) -> None:
        for portfolio_id, portfolio_type, curve in (
            (1, "balanced", [0, 10, 20]),
            (2, "aggressive", [0, 30, 60]),
        ):
            self.conn.execute(
                """
                insert into portfolios (id, created_at, portfolio_type, metrics_json)
                values (?, '2026-06-24', ?, ?)
                """,
                (
                    portfolio_id,
                    portfolio_type,
                    json.dumps({"equity_curve_2020_2026": curve}),
                ),
            )
        curves = self.logic._saved_portfolio_curves(self.conn, PortfolioType.AGGRESSIVE)
        self.assertEqual(curves, [[0.0, 30.0, 60.0]])


if __name__ == "__main__":
    unittest.main()
