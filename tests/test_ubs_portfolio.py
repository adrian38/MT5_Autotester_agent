from __future__ import annotations

from datetime import datetime
import unittest

from portfolio_manager.ubs_portfolio import (
    PeriodReport,
    PortfolioType,
    RobustStrategySet,
    build_portfolio_greedy,
    build_correlation_pairs,
    calc_point_dd,
    calc_valley_dd,
    curve_increment_correlation,
    evaluate_portfolio,
    execution_units_from_step,
    filter_eligible_sets,
    improve_with_local_search,
    merge_accumulated_curves,
    optimize_portfolio,
    score_set_for_portfolio,
    select_top_k_per_symbol,
)


def make_strategy(
    set_id: str,
    symbol: str,
    curve: list[float],
    *,
    candidate_id: str | None = None,
    status: str = "accepted",
    already_used: bool = False,
    trades: int = 120,
    profit_factor: float = 1.5,
) -> RobustStrategySet:
    valley = calc_valley_dd(curve)
    point = calc_point_dd(curve)
    net = curve[-1]
    period = PeriodReport(
        period_name="dummy",
        start_year=2020,
        end_year=2026,
        symbol=symbol,
        timeframe="H1",
        pnl_curve_001=curve,
        net_profit_001=net,
        valley_dd_001=valley,
        point_dd_001=point,
        profit_factor=profit_factor,
        return_dd_ratio=net / max(valley, 1),
        trades=trades,
        gross_profit=max(net, 0),
        gross_loss=-max(valley, 1),
    )
    return RobustStrategySet(
        set_id=set_id,
        candidate_id=candidate_id or set_id,
        symbol=symbol,
        timeframe="H1",
        strategy_family="test",
        robustness_status=status,
        already_used=already_used,
        report_2020_2024=period,
        report_2025_2026=period,
        curve_2020_2026_001=curve,
        net_profit_2020_2026_001=net,
        valley_dd_2020_2026_001=valley,
        point_dd_2020_2026_001=point,
        profit_factor_2020_2026=profit_factor,
        return_dd_2020_2026=net / max(valley, 1),
        trades_2020_2026=trades,
        set_path=set_id,
    )


class UBSPortfolioOptimizerTests(unittest.TestCase):
    def test_merge_accumulated_curves(self) -> None:
        self.assertEqual(
            merge_accumulated_curves([0, 100, 80, 150], [0, 30, 10, 70]),
            [0, 100, 80, 150, 180, 160, 220],
        )

    def test_drawdown_calculations(self) -> None:
        curve = [0, 100, 80, 120, 50]
        self.assertEqual(calc_valley_dd(curve), 70)
        self.assertEqual(calc_point_dd(curve), 70)

    def test_optimizer_never_exceeds_dd_constraints(self) -> None:
        sets = [
            make_strategy("s1", "EURUSD", [0, 100, 80, 160]),
            make_strategy("s2", "GBPUSD", [0, 40, 35, 90]),
            make_strategy("s3", "XAUUSD", [0, 70, 55, 120]),
        ]
        result = optimize_portfolio(
            sets,
            capital=1000,
            valley_dd_pct=10,
            point_dd_pct=5,
            max_total_units=20,
        )
        self.assertLessEqual(result.actual_valley_dd, result.target_valley_dd)
        self.assertLessEqual(result.actual_point_dd, result.target_point_dd)

    def test_zero_units_are_allowed_for_selected_candidates(self) -> None:
        sets = [
            make_strategy("strong", "EURUSD", [0, 100, 90, 180]),
            make_strategy("weaker", "EURUSD", [0, 12, 8, 20]),
        ]
        result = optimize_portfolio(
            sets,
            capital=1000,
            valley_dd_pct=10,
            point_dd_pct=5,
            top_k_per_symbol=2,
            max_sets_per_symbol=1,
            max_total_units=5,
        )
        reasons = {item.set_id: item.reason for item in result.unused_sets}
        self.assertEqual(reasons.get("weaker"), "received_zero_units")

    def test_already_used_sets_are_filtered(self) -> None:
        used = make_strategy("used", "EURUSD", [0, 50, 40, 90], already_used=True)
        fresh = make_strategy("fresh", "GBPUSD", [0, 40, 30, 80])
        eligible = filter_eligible_sets([used, fresh], min_trades_2020_2026=100)
        self.assertEqual([item.set_id for item in eligible], ["fresh"])

    def test_top_k_per_symbol(self) -> None:
        sets = [
            make_strategy(f"eur{i}", "EURUSD", [0, 10 + i * 5, 8 + i * 5, 20 + i * 10])
            for i in range(5)
        ]
        selected = select_top_k_per_symbol(sets, top_k_per_symbol=3, max_total_candidates=None)
        self.assertEqual(len(selected), 3)
        self.assertEqual({item.symbol for item in selected}, {"EURUSD"})
        self.assertEqual(
            [item.set_id for item in selected],
            [item.set_id for item in sorted(selected, key=score_set_for_portfolio, reverse=True)],
        )

    def test_local_search_does_not_reduce_profit(self) -> None:
        sets = [
            make_strategy("s1", "EURUSD", [0, 60, 50, 100]),
            make_strategy("s2", "GBPUSD", [0, 45, 43, 130]),
            make_strategy("s3", "XAUUSD", [0, 20, 19, 60]),
        ]
        allocations, current, _log, _reason, _corr_rejections = build_portfolio_greedy(
            sets,
            capital=1000,
            valley_dd_pct=10,
            point_dd_pct=5,
            portfolio_type=PortfolioType.BALANCED,
            max_total_units=12,
            max_sets_per_symbol=1,
        )
        before = current.total_net_profit
        _allocations, improved, _local_log = improve_with_local_search(
            sets,
            allocations,
            current,
            current.target_valley_dd,
            current.target_point_dd,
        )
        self.assertGreaterEqual(improved.total_net_profit, before)

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


if __name__ == "__main__":
    unittest.main()
