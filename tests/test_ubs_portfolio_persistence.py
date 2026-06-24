from __future__ import annotations

import json
import sqlite3
import unittest

from portfolio_manager.ubs_portfolio import PortfolioType, optimize_portfolio
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin
from tests.test_ubs_portfolio import make_strategy


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

    def test_portfolio_version_snapshot_restores_allocations_and_metrics(self) -> None:
        self.conn.execute(
            """
            insert into portfolios (
                id, created_at, portfolio_type, total_units, total_lot,
                active_strategies, target_strategies
            ) values (8, '2026-06-24', 'aggressive', 4, 0.04, 1, 2)
            """
        )
        self.conn.execute(
            """
            insert into portfolio_allocations (
                portfolio_id, set_id, candidate_id, symbol, units, lot,
                net_profit_contribution, standalone_valley_dd, standalone_point_dd, set_path
            ) values (8, 'old.set', 'ECN:1', 'EURUSD', 4, 0.04, 100, 10, 5, 'old.set')
            """
        )
        version_no = self.logic._save_portfolio_version(self.conn, 8, "before test")
        self.assertEqual(version_no, 1)
        self.conn.execute("update portfolios set total_units=9, total_lot=0.09 where id=8")
        self.conn.execute("update portfolio_allocations set units=9, lot=0.09 where portfolio_id=8")
        version = self.conn.execute(
            "select snapshot_json from portfolio_versions where portfolio_id=8 and version_no=1"
        ).fetchone()
        self.logic._restore_portfolio_version_payload(self.conn, 8, bytes(version["snapshot_json"]))
        portfolio = self.conn.execute("select total_units,total_lot from portfolios where id=8").fetchone()
        allocation = self.conn.execute(
            "select units,lot from portfolio_allocations where portfolio_id=8"
        ).fetchone()
        self.assertEqual(tuple(portfolio), (4, 0.04))
        self.assertEqual(tuple(allocation), (4, 0.04))

    def test_three_comparable_proposals_use_distinct_risk_profiles(self) -> None:
        inputs = {
            "capital": 1000.0,
            "valley_dd_pct": 20.0,
            "point_dd_pct": 15.0,
            "portfolio_type": "aggressive",
            "top_k_per_symbol": 3,
            "max_total_candidates": 10,
            "min_trades_2020_2026": 100,
            "max_units_per_set": None,
            "max_total_units": 8,
            "max_units_per_symbol": None,
            "max_sets_per_symbol": 1,
            "run_local_search": True,
            "use_correlation": False,
            "require_3_positive_months_6m": False,
            "dd_reserve_pct": 10.0,
            "search_restarts": 1,
            "max_pair_corr": None,
            "max_downside_corr": None,
            "max_dd_overlap": None,
            "max_portfolio_corr": None,
        }
        proposals = self.logic._optimize_ubs_portfolio_proposals(
            [
                make_strategy("a", "EURUSD", [0, 60, 50, 100]),
                make_strategy("b", "GBPUSD", [0, 45, 43, 130]),
                make_strategy("c", "XAUUSD", [0, 20, 19, 60]),
            ],
            inputs,
            PortfolioType.AGGRESSIVE,
            [],
        )
        self.assertEqual([item["key"] for item in proposals], ["profit", "balanced", "margin"])
        self.assertEqual([item["reserve_pct"] for item in proposals], [10.0, 15.0, 25.0])
        targets = [item["result"].target_valley_dd for item in proposals]
        self.assertGreater(targets[0], targets[1])
        self.assertGreater(targets[1], targets[2])
        for proposal in proposals:
            stress = proposal["result"].stress_bootstrap
            self.assertIsNotNone(stress)
            self.assertEqual(stress.simulations, 1000)

    def test_saved_portfolio_persists_bootstrap_analysis_for_audit(self) -> None:
        inputs = {
            "capital": 1000.0,
            "valley_dd_pct": 20.0,
            "point_dd_pct": 20.0,
            "portfolio_type": "balanced",
        }
        result = optimize_portfolio(
            [make_strategy("audit.set", "EURUSD", [0, 20, 10, 35, 15, 45])],
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=20,
            max_total_units=2,
            bootstrap_simulations=40,
        )

        portfolio_id = self.logic._insert_portfolio(self.conn, inputs, result)
        row = self.conn.execute(
            "select metrics_json from portfolios where id=?",
            (portfolio_id,),
        ).fetchone()
        metrics = json.loads(row["metrics_json"])
        stress = metrics["stress_bootstrap"]
        self.assertEqual(stress["method"], "circular_moving_block")
        self.assertEqual(stress["simulations"], 40)
        self.assertEqual(stress["seed"], 20260624)
        self.assertIn("valley_dd_p95", stress)
        self.assertIn("probability_exceed_nominal_pct", stress)
        self.assertIn("probability_exceed_effective_pct", stress)


if __name__ == "__main__":
    unittest.main()
