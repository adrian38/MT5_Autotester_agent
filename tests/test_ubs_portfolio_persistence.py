from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from portfolio_manager.ubs_portfolio import PortfolioType, optimize_portfolio
from ui.ubs_portfolio_logic import PORTFOLIO_TYPE_BATCH_SPECS, UBSPortfolioLogicMixin
from tests.test_ubs_portfolio import make_strategy


class _PortfolioLogic(UBSPortfolioLogicMixin):
    pass


class _MonthlyProposalApplyLogic(UBSPortfolioLogicMixin):
    def _accept_generated_ubs_monthly_portfolio_proposal(self, proposal):
        self.accepted_monthly_proposal = proposal

    def _save_pending_ubs_monthly_portfolio(self):
        self.saved_monthly_proposal = True


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _DummyConn:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _BatchSaveLogic(UBSPortfolioLogicMixin):
    def __init__(self):
        self.inserted = []
        self.bundle_inserted = []
        self.selected_id = None
        self.save_enabled = True
        self.notifications = []
        self.ubs_portfolio_status = _Var("")

    def _ubs_portfolio_conn(self):
        return _DummyConn()

    def _insert_portfolio(self, conn, inputs, result, *, commit=True):
        portfolio_id = len(self.inserted) + 1
        self.inserted.append((portfolio_id, inputs, result, commit))
        return portfolio_id

    def _insert_portfolio_bundle(self, conn, proposals, selected_result, *, commit=True):
        portfolio_id = len(self.bundle_inserted) + 1
        self.bundle_inserted.append((portfolio_id, proposals, selected_result, commit))
        return portfolio_id

    def _set_ubs_portfolio_save_enabled(self, enabled):
        self.save_enabled = enabled

    def _refresh_ubs_portfolios(self, select_id=None):
        self.selected_id = select_id

    def _notify_ubs_portfolio_event(self, message):
        self.notifications.append(message)


class _Result:
    def __init__(self, net):
        self.allocations = [object()]
        self.total_net_profit = net
        self.total_lot = 0.01
        self.active_strategies = 1


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
        self.conn.execute("delete from candidate_final_tick where candidate_id=7")
        self.assertEqual(
            len(self.logic._final_tick_passed_candidates(self.conn, "ECN")),
            1,
            "The probe stage is not a portfolio gate once Final Tick 6M passed",
        )

        self.conn.execute(
            """
            insert into portfolio_quarantine (
                account_type, candidate_id, set_path, symbol, timeframe,
                reason, source_portfolio_id, quarantined_at
            ) values ('ECN', 7, 'C:/sets/strategy.set', 'EURUSD', 'H1', 'manual', 3, '2026-06-24T12:00:00')
            """
        )
        self.assertEqual(self.logic._final_tick_passed_candidates(self.conn, "ECN"), [])
        self.assertEqual(
            len(
                self.logic._final_tick_passed_candidates(
                    self.conn,
                    "ECN",
                    include_quarantined=True,
                )
            ),
            1,
        )

    def test_monthly_portfolios_do_not_lock_full_history_sets(self) -> None:
        for portfolio_id, scope, month, set_path in (
            (1, "full_history", None, "C:/sets/full.set"),
            (2, "monthly", 1, "C:/sets/monthly.set"),
        ):
            self.conn.execute(
                """
                insert into portfolios (
                    id, created_at, portfolio_type, portfolio_scope, target_month
                ) values (?, '2026-06-24', 'balanced', ?, ?)
                """,
                (portfolio_id, scope, month),
            )
            self.conn.execute(
                """
                insert into portfolio_allocations (
                    portfolio_id, set_id, candidate_id, symbol, units, lot,
                    net_profit_contribution, standalone_valley_dd,
                    standalone_point_dd, set_path
                ) values (?, ?, '1', 'EURUSD', 1, 0.01, 10, 1, 1, ?)
                """,
                (portfolio_id, set_path, set_path),
            )

        self.assertEqual(
            self.logic._used_set_paths(self.conn, PortfolioType.BALANCED),
            ["C:/sets/full.set"],
        )
        self.assertEqual(
            [row["id"] for row in self.logic._list_portfolios(self.conn)],
            [1],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.logic._list_portfolios(
                    self.conn,
                    portfolio_scope="monthly",
                )
            ],
            [2],
        )

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

    def test_normal_batch_specs_create_aggressive_balanced_conservative_inputs(self) -> None:
        inputs = {
            "capital": 1000.0,
            "valley_dd_pct": 20.0,
            "point_dd_pct": 20.0,
            "portfolio_type": "balanced",
            "top_k_per_symbol": 3,
            "max_total_candidates": 10,
            "min_trades_2020_2026": 0,
            "max_units_per_set": None,
            "max_total_units": 8,
            "max_units_per_symbol": None,
            "max_sets_per_symbol": 1,
            "run_local_search": True,
            "use_correlation": False,
            "require_3_positive_months_6m": False,
            "dd_reserve_pct": 10.0,
            "search_restarts": 0,
            "max_pair_corr": None,
            "max_downside_corr": None,
            "max_dd_overlap": None,
            "max_portfolio_corr": None,
            "enforce_point_dd": False,
        }
        specs = tuple(
            (
                key,
                label,
                portfolio_type,
                self.logic._portfolio_type_reserve_pct(10.0, portfolio_type),
            )
            for key, label, portfolio_type in PORTFOLIO_TYPE_BATCH_SPECS
        )

        proposals = self.logic._optimize_ubs_portfolio_proposals(
            [
                make_strategy("a", "EURUSD", [0, 60, 50, 100]),
                make_strategy("b", "GBPUSD", [0, 45, 43, 130]),
                make_strategy("c", "XAUUSD", [0, 20, 19, 60]),
            ],
            inputs,
            PortfolioType.BALANCED,
            [],
            specs=specs,
        )

        self.assertEqual([item["key"] for item in proposals], ["aggressive", "balanced", "conservative"])
        self.assertEqual([item["inputs"]["portfolio_type"] for item in proposals], ["aggressive", "balanced", "conservative"])
        self.assertEqual([item["reserve_pct"] for item in proposals], [10.0, 15.0, 25.0])
        self.assertTrue(all(not item["result"].enforce_point_dd for item in proposals))

    def test_locked_normal_variants_share_sets_and_persist_as_one_bundle(self) -> None:
        inputs = {
            "capital": 1000.0,
            "valley_dd_pct": 60.0,
            "point_dd_pct": 60.0,
            "portfolio_type": "balanced",
            "top_k_per_symbol": 3,
            "max_total_candidates": 10,
            "min_trades_2020_2026": 0,
            "max_units_per_set": None,
            "max_total_units": 6,
            "max_units_per_symbol": None,
            "max_sets_per_symbol": 1,
            "run_local_search": True,
            "deep_optimization": True,
            "use_correlation": False,
            "require_3_positive_months_6m": False,
            "dd_reserve_pct": 10.0,
            "search_restarts": 0,
            "max_pair_corr": None,
            "max_downside_corr": None,
            "max_dd_overlap": None,
            "max_portfolio_corr": None,
            "enforce_point_dd": False,
            "daily_dd_full_history": False,
            "validate_margin": False,
            "validate_roboforex_margin": False,
            "validate_ttp_margin": False,
            "max_margin_pct": None,
            "margin_profile": "ictrading",
        }
        with patch("ui.ubs_portfolio_logic.optimize_portfolio", wraps=optimize_portfolio) as optimize_mock:
            proposals = self.logic._optimize_locked_ubs_portfolio_variants(
                [
                    make_strategy("a.set", "EURUSD", [0, 60, 50, 100]),
                    make_strategy("b.set", "GBPUSD", [0, 45, 43, 130]),
                    make_strategy("c.set", "XAUUSD", [0, 20, 19, 60]),
                ],
                inputs,
                PortfolioType.BALANCED,
                {
                    PortfolioType.AGGRESSIVE: [],
                    PortfolioType.BALANCED: [],
                    PortfolioType.CONSERVATIVE: [],
                },
            )

        self.assertEqual([item["key"] for item in proposals], ["aggressive", "balanced", "conservative"])
        set_ids_by_variant = [
            {allocation.set_id for allocation in item["result"].allocations}
            for item in proposals
        ]
        self.assertTrue(set_ids_by_variant[0])
        self.assertEqual(set_ids_by_variant[0], set_ids_by_variant[1])
        self.assertEqual(set_ids_by_variant[0], set_ids_by_variant[2])
        conservative_result = proposals[2]["result"]
        self.assertGreater(
            conservative_result.total_units,
            conservative_result.active_strategies,
        )
        variant_calls = [
            call
            for call in optimize_mock.call_args_list
            if call.kwargs.get("max_total_candidates") is None
        ]
        self.assertEqual(len(variant_calls), len(PORTFOLIO_TYPE_BATCH_SPECS))
        for call in variant_calls:
            self.assertNotIn("required_set_ids", call.kwargs)
            self.assertEqual(
                call.kwargs["minimum_active_strategies"],
                len(set_ids_by_variant[0]),
            )
            self.assertEqual(
                call.kwargs["maximum_active_strategies"],
                len(set_ids_by_variant[0]),
            )
        base_calls = [
            call
            for call in optimize_mock.call_args_list
            if call.kwargs.get("max_total_candidates") is not None
        ]
        self.assertEqual(len(base_calls), 1)
        self.assertEqual(base_calls[0].kwargs["dd_reserve_pct"], 25.0)

        portfolio_id = self.logic._insert_portfolio_bundle(
            self.conn,
            proposals,
            proposals[1]["result"],
        )
        portfolio_count = self.conn.execute("select count(*) from portfolios").fetchone()[0]
        self.assertEqual(portfolio_count, 1)
        row = self.conn.execute(
            "select portfolio_type, metrics_json from portfolios where id=?",
            (portfolio_id,),
        ).fetchone()
        self.assertEqual(row["portfolio_type"], "bundle")
        metrics = json.loads(row["metrics_json"])
        self.assertTrue(metrics["portfolio_bundle"])
        self.assertEqual(metrics["variant_order"], ["aggressive", "balanced", "conservative"])
        variant_rows = self.conn.execute(
            """
            select variant_key, count(*) as rows_count
            from portfolio_allocations
            where portfolio_id=?
            group by variant_key
            order by variant_key
            """,
            (portfolio_id,),
        ).fetchall()
        self.assertEqual({row["variant_key"] for row in variant_rows}, {"aggressive", "balanced", "conservative"})
        saved_sets = {
            row["variant_key"]: {
                item["set_id"]
                for item in self.conn.execute(
                    "select set_id from portfolio_allocations where portfolio_id=? and variant_key=?",
                    (portfolio_id, row["variant_key"]),
                )
            }
            for row in variant_rows
        }
        self.assertEqual(saved_sets["aggressive"], saved_sets["balanced"])
        self.assertEqual(saved_sets["aggressive"], saved_sets["conservative"])

    def test_normal_portfolio_inputs_ignore_point_dd_limit(self) -> None:
        logic = _PortfolioLogic()
        logic.ubs_portfolio_capital = _Var("1000")
        logic.ubs_portfolio_valley_pct = _Var("12")
        logic.ubs_portfolio_point_pct = _Var("")
        logic.ubs_portfolio_type = _Var("Balanced")
        logic.ubs_portfolio_top_k = _Var("3")
        logic.ubs_portfolio_max_candidates = _Var("10")
        logic.ubs_portfolio_min_trades = _Var("0")
        logic.ubs_portfolio_max_units_per_set = _Var("")
        logic.ubs_portfolio_max_total_units = _Var("")
        logic.ubs_portfolio_max_units_per_symbol = _Var("")
        logic.ubs_portfolio_max_sets_per_symbol = _Var("1")
        logic.ubs_portfolio_run_local_search = _Var(True)
        logic.ubs_portfolio_deep_optimization = _Var(True)
        logic.ubs_portfolio_use_correlation = _Var(False)
        logic.ubs_portfolio_require_3_positive_months_6m = _Var(False)
        logic.ubs_portfolio_dd_reserve_pct = _Var("0")
        logic.ubs_portfolio_search_restarts = _Var("0")
        logic.ubs_portfolio_max_pair_corr = _Var("")
        logic.ubs_portfolio_max_downside_corr = _Var("")
        logic.ubs_portfolio_max_dd_overlap = _Var("")
        logic.ubs_portfolio_max_portfolio_corr = _Var("")

        values = logic._read_ubs_portfolio_inputs()

        self.assertEqual(values["point_dd_pct"], 12.0)
        self.assertFalse(values["enforce_point_dd"])
        self.assertEqual(
            values["allowed_asset_groups"],
            ["Bonds", "Crypto", "Energies", "Forex", "Indices", "Metals", "Softs", "Stocks"],
        )

    def test_normal_portfolio_inputs_read_margin_profile_and_group_filters(self) -> None:
        logic = _PortfolioLogic()
        logic.ubs_broker = _Var("AXI")
        logic.ubs_portfolio_capital = _Var("1000")
        logic.ubs_portfolio_valley_pct = _Var("12")
        logic.ubs_portfolio_point_pct = _Var("")
        logic.ubs_portfolio_type = _Var("Moderado")
        logic.ubs_portfolio_top_k = _Var("3")
        logic.ubs_portfolio_max_candidates = _Var("10")
        logic.ubs_portfolio_min_trades = _Var("0")
        logic.ubs_portfolio_max_units_per_set = _Var("")
        logic.ubs_portfolio_max_total_units = _Var("")
        logic.ubs_portfolio_max_units_per_symbol = _Var("")
        logic.ubs_portfolio_max_sets_per_symbol = _Var("1")
        logic.ubs_portfolio_run_local_search = _Var(True)
        logic.ubs_portfolio_deep_optimization = _Var(True)
        logic.ubs_portfolio_use_correlation = _Var(False)
        logic.ubs_portfolio_require_3_positive_months_6m = _Var(False)
        logic.ubs_portfolio_dd_reserve_pct = _Var("0")
        logic.ubs_portfolio_search_restarts = _Var("0")
        logic.ubs_portfolio_max_pair_corr = _Var("")
        logic.ubs_portfolio_max_downside_corr = _Var("")
        logic.ubs_portfolio_max_dd_overlap = _Var("")
        logic.ubs_portfolio_max_portfolio_corr = _Var("")
        logic.ubs_portfolio_margin_profile = _Var("ICTRADING")
        logic.ubs_portfolio_max_margin_pct = _Var("80")
        logic.ubs_portfolio_allow_forex = _Var(True)
        logic.ubs_portfolio_allow_metals = _Var(True)
        logic.ubs_portfolio_allow_indices = _Var(False)
        logic.ubs_portfolio_allow_energies = _Var(False)
        logic.ubs_portfolio_allow_crypto = _Var(False)
        logic.ubs_portfolio_allow_stocks = _Var(False)
        logic.ubs_portfolio_allow_bonds = _Var(False)
        logic.ubs_portfolio_allow_softs = _Var(False)

        values = logic._read_ubs_portfolio_inputs()

        self.assertEqual(values["portfolio_type"], "balanced")
        self.assertTrue(values["deep_optimization"])
        self.assertEqual(values["margin_profile"], "ictrading")
        self.assertTrue(values["validate_margin"])
        self.assertTrue(values["validate_roboforex_margin"])
        self.assertFalse(values["validate_ttp_margin"])
        self.assertEqual(values["max_margin_pct"], 80.0)
        self.assertEqual(values["allowed_asset_groups"], ["Forex", "Metals"])

    def test_saving_generated_portfolio_persists_one_bundle_with_all_pending_proposals(self) -> None:
        logic = _BatchSaveLogic()
        proposals = [
            {
                "label": "Agresivo",
                "inputs": {"optimization_profile": "aggressive", "portfolio_type": "aggressive"},
                "result": _Result(100.0),
            },
            {
                "label": "Moderado",
                "inputs": {"optimization_profile": "balanced", "portfolio_type": "balanced"},
                "result": _Result(80.0),
            },
            {
                "label": "Conservador",
                "inputs": {"optimization_profile": "conservative", "portfolio_type": "conservative"},
                "result": _Result(60.0),
            },
        ]
        logic.ubs_portfolio_pending_result = proposals[1]["result"]
        logic.ubs_portfolio_pending_inputs = proposals[1]["inputs"]
        logic.ubs_portfolio_pending_proposals = proposals

        logic._save_pending_ubs_portfolio()

        self.assertEqual(logic.inserted, [])
        self.assertEqual(len(logic.bundle_inserted), 1)
        portfolio_id, saved_proposals, selected_result, commit = logic.bundle_inserted[0]
        self.assertEqual(portfolio_id, 1)
        self.assertEqual([item["inputs"]["portfolio_type"] for item in saved_proposals], ["aggressive", "balanced", "conservative"])
        self.assertIs(selected_result, proposals[1]["result"])
        self.assertFalse(commit)
        self.assertEqual(logic.selected_id, 1)
        self.assertFalse(logic.save_enabled)
        self.assertEqual(logic.ubs_portfolio_pending_proposals, [])
        self.assertIn("A/M/C", logic.ubs_portfolio_status.get())

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

    def test_monthly_portfolio_persists_scope_and_target_month(self) -> None:
        inputs = {
            "capital": 1000.0,
            "valley_dd_pct": 20.0,
            "point_dd_pct": 20.0,
            "portfolio_type": "balanced",
            "portfolio_scope": "monthly",
            "target_month": 8,
            "target_month_label": "08 - Agosto",
        }
        result = optimize_portfolio(
            [make_strategy("august.set", "EURUSD", [0, 20, 10, 35])],
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=20,
            max_total_units=1,
            bootstrap_simulations=20,
        )

        portfolio_id = self.logic._insert_portfolio(self.conn, inputs, result)
        row = self.conn.execute(
            "select portfolio_scope,target_month,metrics_json from portfolios where id=?",
            (portfolio_id,),
        ).fetchone()
        self.assertEqual(row["portfolio_scope"], "monthly")
        self.assertEqual(row["target_month"], 8)
        self.assertEqual(json.loads(row["metrics_json"])["inputs"]["target_month"], 8)

    def test_generated_monthly_proposal_is_saved_directly_from_preview(self) -> None:
        logic = _MonthlyProposalApplyLogic()
        proposal = {"result": object(), "inputs": {"portfolio_scope": "monthly", "target_month": 7}}
        logic.ubs_portfolio_proposals = {"profit": proposal}
        logic.ubs_portfolio_selected_proposal_key = "profit"
        logic.ubs_portfolio_proposals_id = 0
        logic.ubs_portfolio_proposals_mode = "generate_monthly"

        logic._apply_selected_ubs_portfolio_proposal()

        self.assertIs(logic.accepted_monthly_proposal, proposal)
        self.assertTrue(logic.saved_monthly_proposal)
        self.assertEqual(logic.ubs_portfolio_proposals, {})


if __name__ == "__main__":
    unittest.main()
