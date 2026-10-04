from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.ubs_portfolio import (
    PortfolioType,
    build_correlation_pairs,
    curve_increment_correlation,
    evaluate_portfolio,
    execution_units_from_step,
    improve_with_local_search,
    margin_profile_label,
    normalize_margin_profile,
    optimize_portfolio,
    portfolio_margin_summary,
)
from tests.ubs_portfolio_fixtures import make_strategy


class UBSPortfolioRepairTests(unittest.TestCase):
    def test_correlation_pairs_detect_similar_curves(self) -> None:
        sets = [
            make_strategy("a", "US30", [0, 10, 5, 20, 15, 30]),
            make_strategy("b", "DE40", [0, 20, 10, 40, 30, 60]),
            make_strategy("c", "EURUSD", [0, -5, 5, -2, 8, 1]),
        ]
        pairs = build_correlation_pairs(sets)
        pair_by_ids = {frozenset((pair.set_id_a, pair.set_id_b)): pair for pair in pairs}
        self.assertGreater(pair_by_ids[frozenset(("a", "b"))].pearson_corr, 0.99)

    def test_optimizer_rejects_new_strategy_above_correlation_limit(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("a", "US30", [0, 10, 5, 20, 15, 30]),
                make_strategy("b", "DE40", [0, 20, 10, 40, 30, 60]),
            ],
            capital=1000,
            valley_dd_pct=50,
            point_dd_pct=50,
            top_k_per_symbol=2,
            max_sets_per_symbol=2,
            max_total_units=4,
            max_pair_corr=0.5,
            max_downside_corr=0.5,
            max_dd_overlap=1.0,
        )
        self.assertEqual(result.active_strategies, 1)
        self.assertGreater(result.correlation_rejections, 0)

    def test_curve_increment_correlation_for_saved_portfolio_curves(self) -> None:
        self.assertGreater(
            curve_increment_correlation([0, 10, 5, 20], [0, 20, 10, 40]),
            0.99,
        )

    def test_optimizer_rejects_portfolio_too_correlated_with_saved_curve(self) -> None:
        result = optimize_portfolio(
            [make_strategy("a", "US30", [0, 10, 5, 20, 15, 30])],
            capital=1000,
            valley_dd_pct=50,
            point_dd_pct=50,
            max_total_units=4,
            existing_portfolio_curves=[[0, 20, 10, 40, 30, 60]],
            max_portfolio_corr=0.5,
        )
        self.assertEqual(result.total_units, 0)
        self.assertGreater(result.correlation_rejections, 0)

    def test_time_axis_preserves_duplicate_timestamp_drawdown(self) -> None:
        strategy = make_strategy("dup", "JP225CASH", [0, 100, 50, 120])
        timestamp = datetime(2025, 1, 1, 9, 0)
        strategy.curve_points_2020_2026_001 = [
            (timestamp, 100),
            (timestamp, 50),
            (timestamp, 120),
        ]

        evaluation = evaluate_portfolio(
            [strategy],
            {"dup": 1},
            target_valley_dd=1000,
            target_point_dd=1000,
        )

        self.assertEqual(evaluation.equity_curve_2020_2026, [0.0, 100.0, 50.0, 120.0])
        self.assertEqual(evaluation.valley_dd, 50)
        self.assertEqual(evaluation.point_dd, 50)

    def test_local_search_respects_max_sets_per_symbol(self) -> None:
        sets = [
            make_strategy("eur_a", "EURUSD", [0, 60, 50, 100]),
            make_strategy("eur_b", "EURUSD", [0, 80, 70, 180]),
        ]
        allocations = {"eur_a": 2, "eur_b": 0}
        current = evaluate_portfolio(sets, allocations, target_valley_dd=1000, target_point_dd=1000)

        improved_allocations, _improved, _local_log = improve_with_local_search(
            sets,
            allocations,
            current,
            current.target_valley_dd,
            current.target_point_dd,
            max_sets_per_symbol=1,
        )

        self.assertEqual(improved_allocations["eur_b"], 0)

    def test_decision_log_exists_when_allocations_exist(self) -> None:
        result = optimize_portfolio(
            [make_strategy("s1", "EURUSD", [0, 100, 90, 180])],
            capital=1000,
            valley_dd_pct=10,
            point_dd_pct=5,
            max_total_units=3,
        )
        self.assertGreater(result.total_units, 0)
        self.assertGreater(len(result.decision_log), 0)
        evaluation = evaluate_portfolio(
            [make_strategy("s1", "EURUSD", [0, 100, 90, 180])],
            {"s1": result.total_units},
            result.target_valley_dd,
            result.target_point_dd,
        )
        self.assertEqual(evaluation.total_units, result.total_units)

    def test_export_step_units_match_displayed_lot(self) -> None:
        capital = 5000
        examples = [
            (5, 1000, 10.0),
            (15, 333, 3.33),
            (24, 208, 2.08),
            (35, 142, 1.42),
        ]
        for step, expected_units, expected_lot in examples:
            with self.subTest(step=step):
                units = execution_units_from_step(capital, step)
                self.assertEqual(units, expected_units)
                self.assertEqual(round(units * 0.01, 2), expected_lot)

    def test_optimizer_rounds_final_units_to_integer_step_export(self) -> None:
        result = optimize_portfolio(
            [make_strategy("s1", "EURUSD", [0, 100, 99.5, 180])],
            capital=5000,
            valley_dd_pct=7,
            point_dd_pct=4,
            max_total_units=1114,
        )
        allocation = result.allocations[0]
        self.assertEqual(allocation.units, execution_units_from_step(5000, allocation.lot_size_step))
        self.assertEqual(allocation.lot, allocation.units * 0.01)

    def test_roboforex_margin_guard_limits_stock_lot_by_balance(self) -> None:
        result = optimize_portfolio(
            [make_strategy("meta", "META", [0, 50, 49.9, 100], price=700.0)],
            capital=5000,
            valley_dd_pct=50,
            point_dd_pct=50,
            max_total_units=1000,
            margin_balance=5000,
            max_margin_pct=100,
            stock_leverage=20,
            default_leverage=500,
            stock_contract_size=100,
            default_contract_size=1,
        )

        allocation = result.allocations[0]
        self.assertLessEqual(allocation.units, 142)
        self.assertLess(allocation.lot, 10.0)
        self.assertLessEqual(float(result.margin_summary["total"]), 5000.0)
        self.assertEqual(allocation.margin_leverage, 20.0)
        self.assertEqual(allocation.margin_contract_size, 100.0)

    def test_ttp_margin_profile_uses_asset_specific_leverage(self) -> None:
        strategies = [
            make_strategy("eurusd", "EURUSD", [0, 10], price=1.1),
            make_strategy("us30", "US30", [0, 10], price=40000.0),
            make_strategy("xauusd", "XAUUSD", [0, 10], price=2400.0),
            make_strategy("wti", "WTI", [0, 10], price=80.0),
            make_strategy("meta", "META", [0, 10], price=700.0),
        ]
        summary = portfolio_margin_summary(
            strategies,
            {strategy.set_id: 1 for strategy in strategies},
            balance=5000,
            max_margin_pct=100,
            margin_profile="ttp",
            stock_leverage=20,
            default_leverage=500,
            stock_contract_size=100,
            default_contract_size=1,
        )
        by_set = summary["by_set"]

        self.assertEqual(by_set["eurusd"]["leverage"], 50.0)
        self.assertEqual(by_set["us30"]["leverage"], 15.0)
        self.assertEqual(by_set["xauusd"]["leverage"], 10.0)
        self.assertEqual(by_set["wti"]["leverage"], 10.0)
        self.assertEqual(by_set["meta"]["leverage"], 2.0)
        self.assertEqual(by_set["meta"]["contract_size"], 100.0)

    def test_standard_broker_margin_profiles_keep_broker_identity(self) -> None:
        self.assertEqual(normalize_margin_profile("ROBOFOREX"), "roboforex")
        self.assertEqual(normalize_margin_profile("AXI"), "axi")
        self.assertEqual(normalize_margin_profile("ICTrading"), "ictrading")
        self.assertEqual(margin_profile_label("AXI"), "AXI")
        self.assertEqual(margin_profile_label("ICTRADING"), "ICTrading")

        summary = portfolio_margin_summary(
            [make_strategy("meta", "META", [0, 10], price=700.0)],
            {"meta": 1},
            balance=5000,
            max_margin_pct=100,
            margin_profile="AXI",
            stock_leverage=20,
            default_leverage=500,
            stock_contract_size=100,
            default_contract_size=1,
        )

        self.assertEqual(summary["profile"], "axi")
        self.assertEqual(summary["profile_label"], "AXI")
        self.assertEqual(summary["by_set"]["meta"]["leverage"], 20.0)

    def test_normal_deep_refinement_expands_pool_and_adds_valid_candidate(self) -> None:
        anchor = make_strategy("anchor", "EURUSD", [0, 100, 99, 120], trades=1)
        improver = make_strategy("improver", "GBPUSD", [0, 80, 79, 90], trades=1)
        base = optimize_portfolio(
            [anchor, improver],
            capital=10000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=1,
            max_total_candidates=1,
            max_units_per_set=1,
            max_total_units=2,
            max_sets_per_symbol=1,
            run_local_search=False,
            use_deep_refinement=False,
        )
        refined = optimize_portfolio(
            [anchor, improver],
            capital=10000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=1,
            max_total_candidates=1,
            max_units_per_set=1,
            max_total_units=2,
            max_sets_per_symbol=1,
            run_local_search=False,
            use_deep_refinement=True,
        )

        self.assertEqual({item.set_id for item in base.allocations}, {"anchor"})
        self.assertEqual({item.set_id for item in refined.allocations}, {"anchor", "improver"})
        self.assertGreater(refined.total_net_profit, base.total_net_profit)
        self.assertTrue(any("Optimizacion profunda aplicada" in warning for warning in refined.warnings))

    def test_portfolio_repair_retains_required_sets(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("remaining", "EURUSD", [0, 20, 19, 40]),
                make_strategy("replacement", "GBPUSD", [0, 80, 79, 160]),
                make_strategy("best", "XAUUSD", [0, 100, 99, 200]),
            ],
            capital=1000,
            valley_dd_pct=50,
            point_dd_pct=50,
            max_total_units=3,
            max_sets_per_symbol=1,
            run_local_search=True,
            required_set_ids=["remaining"],
            minimum_active_strategies=2,
            max_units_per_group_pct=1.0,
            max_sets_per_group=10,
        )
        allocations = {allocation.set_id: allocation.units for allocation in result.allocations}
        self.assertGreaterEqual(allocations["remaining"], 1)
        self.assertEqual(result.active_strategies, 2)

    def test_portfolio_repair_fills_missing_strategy_slots_before_extra_units(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("a", "EURUSD", [0, 100, 99, 200]),
                make_strategy("b", "GBPUSD", [0, 80, 79, 160]),
                make_strategy("c", "XAUUSD", [0, 60, 59, 120]),
            ],
            capital=1000,
            valley_dd_pct=50,
            point_dd_pct=50,
            max_total_units=3,
            run_local_search=False,
            required_set_ids=["a", "b"],
            minimum_active_strategies=3,
            max_units_per_group_pct=1.0,
            max_sets_per_group=10,
        )
        self.assertEqual(result.active_strategies, 3)
        self.assertEqual({allocation.set_id for allocation in result.allocations}, {"a", "b", "c"})

    def test_portfolio_repair_preserves_existing_units_and_only_sizes_replacement(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("a", "EURUSD", [0, 100, 99, 200]),
                make_strategy("b", "GBPUSD", [0, 80, 79, 160]),
                make_strategy("c", "XAUUSD", [0, 60, 59, 120]),
                make_strategy("d", "US30", [0, 50, 49, 100]),
            ],
            capital=1000,
            valley_dd_pct=50,
            point_dd_pct=50,
            max_total_units=12,
            run_local_search=True,
            required_set_ids=["a", "b"],
            required_initial_allocations={"a": 4, "b": 3},
            preserve_required_allocations=True,
            minimum_active_strategies=3,
            maximum_active_strategies=3,
            max_units_per_group_pct=1.0,
            max_sets_per_group=10,
        )
        allocations = {allocation.set_id: allocation.units for allocation in result.allocations}
        self.assertEqual(allocations["a"], 4)
        self.assertEqual(allocations["b"], 3)
        self.assertEqual(result.active_strategies, 3)
        self.assertEqual(len(set(allocations) - {"a", "b"}), 1)

    def test_portfolio_repair_reduces_only_units_required_to_restore_dd(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("a", "EURUSD", [0, 100, 20]),
                make_strategy("b", "GBPUSD", [0, 10, 20]),
                make_strategy("c", "XAUUSD", [0, 10, 1]),
            ],
            capital=1000,
            valley_dd_pct=14.9,
            point_dd_pct=14.9,
            max_total_units=4,
            run_local_search=True,
            required_set_ids=["a", "b"],
            required_initial_allocations={"a": 2, "b": 1},
            preserve_required_allocations=True,
            minimum_active_strategies=3,
            maximum_active_strategies=3,
            max_units_per_group_pct=1.0,
            max_sets_per_group=10,
        )
        allocations = {allocation.set_id: allocation.units for allocation in result.allocations}
        self.assertEqual(allocations["a"], 1)
        self.assertEqual(allocations["b"], 1)
        self.assertGreaterEqual(allocations["c"], 1)
        self.assertTrue(any("changed only 1 existing unit" in warning for warning in result.warnings))

    def test_portfolio_repair_can_add_replacement_progressively_before_reducing_existing_units(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("a", "EURUSD", [0, 100, 20]),
                make_strategy("b", "GBPUSD", [0, 10, 20]),
                make_strategy("c", "XAUUSD", [0, 0, 5]),
            ],
            capital=1000,
            valley_dd_pct=14,
            point_dd_pct=14,
            max_total_units=5,
            run_local_search=True,
            required_set_ids=["a", "b"],
            required_initial_allocations={"a": 2, "b": 1},
            preserve_required_allocations=True,
            minimum_active_strategies=3,
            maximum_active_strategies=3,
            max_units_per_group_pct=1.0,
            max_sets_per_group=10,
        )
        allocations = {allocation.set_id: allocation.units for allocation in result.allocations}
        self.assertEqual(allocations, {"a": 2, "b": 1, "c": 2})
        self.assertTrue(any("units were preserved" in warning for warning in result.warnings))

    def test_portfolio_repair_stops_reducing_existing_units_once_complete_and_valid(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("a", "EURUSD", [0, 100, 20]),
                make_strategy("b", "GBPUSD", [0, 10, 20]),
                make_strategy("c", "XAUUSD", [0, 10, 1]),
            ],
            capital=1000,
            valley_dd_pct=23,
            point_dd_pct=23,
            max_total_units=20,
            run_local_search=True,
            required_set_ids=["a", "b"],
            required_initial_allocations={"a": 4, "b": 1},
            preserve_required_allocations=True,
            minimum_active_strategies=3,
            maximum_active_strategies=3,
            max_units_per_group_pct=1.0,
            max_sets_per_group=10,
        )
        allocations = {allocation.set_id: allocation.units for allocation in result.allocations}
        self.assertEqual(allocations["a"], 2)
        self.assertEqual(allocations["b"], 1)
        self.assertGreaterEqual(allocations["c"], 1)
        reductions = [
            decision for decision in result.decision_log
            if decision.action == "reduce_unit_for_repair"
        ]
        self.assertEqual(len(reductions), 2)


if __name__ == "__main__":
    unittest.main()
