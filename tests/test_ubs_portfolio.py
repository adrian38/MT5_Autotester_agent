from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    PortfolioType,
    RobustStrategySet,
    build_portfolio_greedy,
    build_correlation_pairs,
    bootstrap_valley_drawdown,
    calc_point_dd,
    calc_valley_dd,
    curve_increment_correlation,
    evaluate_portfolio,
    execution_units_from_step,
    filter_eligible_sets,
    filter_rows_grid_off,
    improve_with_local_search,
    merge_accumulated_curves,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
    portfolio_margin_summary,
    portfolio_group_key,
    recent_positive_month_count,
    score_set_for_portfolio,
    select_top_k_per_symbol,
    set_file_has_enabled_grid,
    slice_strategy_set_to_month,
    slice_strategy_sets_to_month,
    validate_strict_monthly_portfolio,
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
    price: float | None = None,
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
        closed_trades=[
            ClosedTrade(
                open_time=datetime(2026, 1, 1),
                close_time=datetime(2026, 1, 2),
                symbol=symbol,
                volume=0.01,
                profit=net,
                open_price=price,
                close_price=price,
            )
        ]
        if price is not None
        else [],
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
    def test_month_slice_uses_only_target_month_trade_increments_across_years(self) -> None:
        strategy = make_strategy("seasonal", "EURUSD", [0, 10, 5, 25, 15, 30], trades=5)
        strategy.curve_points_2020_2026_001 = [
            (datetime(2020, 1, 10), 10.0),
            (datetime(2020, 2, 10), 5.0),
            (datetime(2021, 1, 10), 25.0),
            (datetime(2022, 1, 10), 15.0),
            (datetime(2022, 3, 10), 30.0),
        ]

        monthly = slice_strategy_set_to_month(strategy, 1)

        self.assertEqual(monthly.curve_2020_2026_001, [0.0, 10.0, 30.0, 20.0])
        self.assertEqual(monthly.net_profit_2020_2026_001, 20.0)
        self.assertEqual(monthly.trades_2020_2026, 3)
        self.assertEqual(monthly.month_years, (2020, 2021, 2022))
        self.assertEqual(monthly.positive_month_years, (2020, 2021))
        self.assertEqual(monthly.target_month, 1)

    def test_month_slice_reports_candidates_without_timestamped_curves(self) -> None:
        sliced, warnings = slice_strategy_sets_to_month(
            [make_strategy("missing-dates", "EURUSD", [0, 10])],
            1,
        )
        self.assertEqual(sliced, [])
        self.assertTrue(any("curva historica con fechas" in warning for warning in warnings))

    def test_grid_off_filter_excludes_only_explicit_enable_grid_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            grid_on = root / "grid_on.set"
            grid_off = root / "grid_off.set"
            missing = root / "missing_key.set"
            grid_on.write_text("EnableGrid=true||false||0||true||N\n", encoding="utf-8")
            grid_off.write_text("EnableGrid=false||false||0||true||N\n", encoding="utf-8")
            missing.write_text("OtherParam=true\n", encoding="utf-8")

            self.assertTrue(set_file_has_enabled_grid(grid_on))
            self.assertFalse(set_file_has_enabled_grid(grid_off))
            self.assertFalse(set_file_has_enabled_grid(missing))

            rows = [
                {"set_path": str(grid_on), "candidate_id": 1},
                {"set_path": str(grid_off), "candidate_id": 2},
                {"set_path": str(missing), "candidate_id": 3},
                {"set_path": str(root / "does_not_exist.set"), "candidate_id": 4},
            ]
            filtered, warnings = filter_rows_grid_off(rows)

            self.assertEqual([row["candidate_id"] for row in filtered], [2, 3, 4])
            self.assertTrue(any("EnableGrid=true" in warning for warning in warnings))

    def test_strict_monthly_validation_requires_target_month_best_in_last_five_years(self) -> None:
        strategy = make_strategy("seasonal", "EURUSD", [0, 10], trades=10)
        accumulated = 0.0
        points = []
        for year in range(2021, 2026):
            accumulated += 10.0
            points.append((datetime(year, 7, 10), accumulated))
            accumulated += 8.0
            points.append((datetime(year, 8, 10), accumulated))
        strategy.curve_points_2020_2026_001 = points

        validation = validate_strict_monthly_portfolio(
            [strategy],
            {"seasonal": 1},
            target_month=7,
            target_valley_dd=100,
            target_point_dd=100,
            lookback_years=5,
        )

        self.assertTrue(validation["passed"])
        self.assertEqual(validation["best_month"], 7)
        self.assertEqual(len(validation["yearly"]), 5)

        accumulated = 0.0
        points = []
        for year in range(2021, 2026):
            accumulated += 10.0
            points.append((datetime(year, 7, 10), accumulated))
            accumulated += 20.0
            points.append((datetime(year, 8, 10), accumulated))
        strategy.curve_points_2020_2026_001 = points
        validation = validate_strict_monthly_portfolio(
            [strategy],
            {"seasonal": 1},
            target_month=7,
            target_valley_dd=100,
            target_point_dd=100,
            lookback_years=5,
        )

        self.assertFalse(validation["passed"])
        self.assertEqual(validation["best_month"], 8)
        self.assertTrue(any("no es el mejor" in reason for reason in validation["reasons"]))

    def test_strict_monthly_validation_rejects_yearly_dd_break(self) -> None:
        strategy = make_strategy("seasonal", "EURUSD", [0, 10], trades=10)
        accumulated = 0.0
        points = []
        for year in range(2021, 2026):
            accumulated += 20.0
            points.append((datetime(year, 7, 5), accumulated))
            accumulated -= 15.0
            points.append((datetime(year, 7, 10), accumulated))
            accumulated += 20.0
            points.append((datetime(year, 7, 20), accumulated))
        strategy.curve_points_2020_2026_001 = points

        validation = validate_strict_monthly_portfolio(
            [strategy],
            {"seasonal": 1},
            target_month=7,
            target_valley_dd=10,
            target_point_dd=20,
            lookback_years=5,
        )

        self.assertFalse(validation["passed"])
        self.assertTrue(any("DD valle" in reason for reason in validation["reasons"]))

    def test_strict_monthly_validation_rejects_any_month_dd_break(self) -> None:
        strategy = make_strategy("seasonal", "EURUSD", [0, 10], trades=10)
        accumulated = 0.0
        points = []
        for year in range(2021, 2026):
            accumulated += 100.0
            points.append((datetime(year, 7, 5), accumulated))
            accumulated += 10.0
            points.append((datetime(year, 8, 5), accumulated))
            accumulated -= 20.0
            points.append((datetime(year, 8, 10), accumulated))
            accumulated += 20.0
            points.append((datetime(year, 8, 20), accumulated))
        strategy.curve_points_2020_2026_001 = points

        validation = validate_strict_monthly_portfolio(
            [strategy],
            {"seasonal": 1},
            target_month=7,
            target_valley_dd=15,
            target_point_dd=15,
            lookback_years=5,
        )

        self.assertFalse(validation["passed"])
        self.assertEqual(validation["best_month"], 7)
        self.assertFalse(validation["monthly_dd"]["08"]["passed_dd"])
        self.assertTrue(any("mes 08" in reason for reason in validation["reasons"]))

    def test_block_bootstrap_is_deterministic_and_reports_audit_parameters(self) -> None:
        curve = [0, 12, 5, -4, 9, 3, 18, 7, 4, 22]
        first = bootstrap_valley_drawdown(
            curve,
            nominal_valley_dd_limit=14,
            effective_valley_dd_limit=10,
            simulations=250,
            block_size=3,
            seed=77,
        )
        second = bootstrap_valley_drawdown(
            curve,
            nominal_valley_dd_limit=14,
            effective_valley_dd_limit=10,
            simulations=250,
            block_size=3,
            seed=77,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.method, "circular_moving_block")
        self.assertEqual(first.simulations, 250)
        self.assertEqual(first.observations, len(curve) - 1)
        self.assertEqual(first.block_size, 3)
        self.assertGreaterEqual(first.valley_dd_p95, first.valley_dd_p50)
        self.assertGreaterEqual(first.probability_exceed_effective_pct, first.probability_exceed_nominal_pct)
        self.assertEqual(first.alert, first.valley_dd_p95 > 10)

    def test_optimizer_attaches_one_thousand_simulation_stress_analysis(self) -> None:
        result = optimize_portfolio(
            [make_strategy("stress", "EURUSD", [0, 20, 10, 30, 5, 40])],
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=20,
            max_total_units=2,
        )

        self.assertIsNotNone(result.stress_bootstrap)
        self.assertEqual(result.stress_bootstrap.simulations, 1000)
        self.assertEqual(result.stress_bootstrap.nominal_valley_dd_limit, 200)
        self.assertEqual(result.stress_bootstrap.effective_valley_dd_limit, result.target_valley_dd)

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
            max_units_per_group_pct=1.0,
        )
        self.assertLessEqual(result.actual_valley_dd, result.target_valley_dd)
        self.assertLessEqual(result.actual_point_dd, result.target_point_dd)

    def test_monthly_optimizer_can_ignore_point_dd_when_daily_dd_is_the_control(self) -> None:
        result = optimize_portfolio(
            [make_strategy("s1", "EURUSD", [0, 100, 0, 200])],
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=5,
            max_total_units=1,
            max_units_per_group_pct=1.0,
            enforce_point_dd=False,
        )

        self.assertFalse(result.enforce_point_dd)
        self.assertEqual(result.active_strategies, 1)
        self.assertLessEqual(result.actual_valley_dd, result.target_valley_dd)
        self.assertGreater(result.actual_point_dd, result.target_point_dd)

    def test_optimizer_applies_configured_dd_reserve(self) -> None:
        result = optimize_portfolio(
            [make_strategy("s1", "EURUSD", [0, 100, 80, 160])],
            capital=1000,
            valley_dd_pct=10,
            point_dd_pct=5,
            dd_reserve_pct=10,
            max_total_units=5,
        )
        self.assertEqual(result.target_valley_dd, 90.0)
        self.assertEqual(result.target_point_dd, 45.0)
        self.assertTrue(any("DD reserve 10.0%" in warning for warning in result.warnings))

    def test_multi_start_search_is_deterministic_and_never_worsens_greedy_result(self) -> None:
        sets = [
            make_strategy("a", "EURUSD", [0, 60, 50, 100]),
            make_strategy("b", "GBPUSD", [0, 45, 43, 130]),
            make_strategy("c", "XAUUSD", [0, 20, 19, 60]),
            make_strategy("d", "US30", [0, 30, 25, 75]),
        ]
        baseline = optimize_portfolio(
            sets,
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=20,
            max_total_units=8,
            max_sets_per_symbol=1,
            search_restarts=0,
        )
        first = optimize_portfolio(
            sets,
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=20,
            max_total_units=8,
            max_sets_per_symbol=1,
            search_restarts=3,
        )
        second = optimize_portfolio(
            sets,
            capital=1000,
            valley_dd_pct=20,
            point_dd_pct=20,
            max_total_units=8,
            max_sets_per_symbol=1,
            search_restarts=3,
        )
        self.assertGreaterEqual(first.total_net_profit, baseline.total_net_profit)
        self.assertEqual(first.total_net_profit, second.total_net_profit)
        self.assertEqual(
            [(item.set_id, item.units) for item in first.allocations],
            [(item.set_id, item.units) for item in second.allocations],
        )
        self.assertTrue(any("Multi-start search evaluated" in warning for warning in first.warnings))

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

    def test_recent_positive_month_count_uses_configured_end_month(self) -> None:
        monthly = {
            2026: {
                1: 10.0,
                2: -3.0,
                3: 0.0,
                4: 12.0,
                6: 5.0,
            }
        }
        self.assertEqual(recent_positive_month_count(monthly, "2026.06.30", window_months=6), 3)

    def test_recent_positive_month_count_treats_missing_months_as_not_positive(self) -> None:
        monthly = {2026: {1: 10.0, 4: 12.0}}
        self.assertEqual(recent_positive_month_count(monthly, "2026.06.30", window_months=6), 2)

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

    def test_top_k_per_symbol_groups_symbol_aliases(self) -> None:
        sets = [
            make_strategy("ustec", "USTEC", [0, 30, 25, 70]),
            make_strategy("cash", ".USTECHCASH", [0, 60, 55, 120]),
            make_strategy("us100", "US100", [0, 45, 40, 90]),
        ]
        selected = select_top_k_per_symbol(sets, top_k_per_symbol=2, max_total_candidates=None)
        self.assertEqual(len(selected), 2)
        self.assertNotIn("ustec", {item.set_id for item in selected})

    def test_optimizer_treats_symbol_aliases_as_same_symbol_limit(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("ustec", "USTEC", [0, 100, 95, 180]),
                make_strategy("cash", ".USTECHCASH", [0, 90, 85, 170]),
                make_strategy("us100", "US100", [0, 80, 75, 160]),
            ],
            capital=1000,
            valley_dd_pct=50,
            point_dd_pct=50,
            top_k_per_symbol=3,
            max_sets_per_symbol=1,
            max_total_units=6,
            run_local_search=True,
        )
        self.assertEqual(result.active_strategies, 1)

    def test_balanced_limits_units_by_asset_group(self) -> None:
        sets = [
            make_strategy("nas", ".USTECHCASH", [0, 120, 119, 240]),
            make_strategy("dow", ".US30CASH", [0, 115, 114, 230]),
            make_strategy("dax", ".DE40CASH", [0, 110, 109, 220]),
            make_strategy("silver", "XAGUSD", [0, 80, 79, 160]),
            make_strategy("apple", "AAPL", [0, 70, 69, 140]),
            make_strategy("eur", "EURUSD", [0, 65, 64, 130]),
        ]
        result = optimize_portfolio(
            sets,
            capital=10000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.BALANCED,
            top_k_per_symbol=10,
            max_total_candidates=None,
            max_total_units=100,
            max_sets_per_symbol=1,
            run_local_search=True,
        )
        self.assertLessEqual(result.group_summary["IndicesEnergies"]["unit_pct"], 55.1)

    def test_candidate_cap_reserves_available_asset_groups(self) -> None:
        sets = [
            make_strategy(f"idx{i}", ".USTECHCASH", [0, 120 + i, 119 + i, 240 + i * 2])
            for i in range(10)
        ]
        sets.extend(
            [
                make_strategy("silver", "XAGUSD", [0, 80, 79, 160]),
                make_strategy("apple", "AAPL", [0, 70, 69, 140]),
                make_strategy("eur", "EURUSD", [0, 65, 64, 130]),
            ]
        )
        selected = select_top_k_per_symbol(
            sets,
            top_k_per_symbol=10,
            max_total_candidates=5,
        )
        selected_groups = {portfolio_group_key(item.symbol) for item in selected}
        self.assertIn("IndicesEnergies", selected_groups)
        self.assertIn("Metals", selected_groups)
        self.assertIn("Stocks", selected_groups)
        self.assertIn("Forex", selected_groups)

    def test_candidate_cap_reserves_symbols_inside_same_asset_group(self) -> None:
        sets = []
        for symbol_index, symbol in enumerate(("EURUSD", "USDJPY", "GBPUSD")):
            for variant in range(3):
                net = 200 - symbol_index * 20 - variant * 2
                sets.append(make_strategy(f"{symbol}-{variant}", symbol, [0, net, net - 1, net * 2]))

        selected = select_top_k_per_symbol(
            sets,
            top_k_per_symbol=3,
            max_total_candidates=3,
        )

        self.assertEqual(
            {portfolio_group_key(item.symbol) for item in selected},
            {"Forex"},
        )
        self.assertEqual(
            {item.symbol for item in selected},
            {"EURUSD", "USDJPY", "GBPUSD"},
        )

    def test_strict_monthly_optimizer_can_combine_non_individually_best_sets(self) -> None:
        july = 7
        a = make_strategy("a", "EURUSD", [0, 1], trades=1)
        b = make_strategy("b", "USDJPY", [0, 1], trades=1)
        total_a = 0.0
        total_b = 0.0
        a_points = []
        b_points = []
        for year in range(2021, 2026):
            total_a += 20.0
            a_points.append((datetime(year, 7, 10), total_a))
            total_a += 30.0
            a_points.append((datetime(year, 8, 10), total_a))

            total_b += 1.0
            b_points.append((datetime(year, 7, 11), total_b))
            total_b -= 50.0
            b_points.append((datetime(year, 8, 11), total_b))
            total_b += 2.0
            b_points.append((datetime(year, 9, 11), total_b))
        a.curve_points_2020_2026_001 = a_points
        b.curve_points_2020_2026_001 = b_points
        monthly, _warnings = slice_strategy_sets_to_month([a, b], july)

        self.assertFalse(
            validate_strict_monthly_portfolio(
                [a],
                {"a": 1},
                target_month=july,
                target_valley_dd=1_000,
                target_point_dd=1_000,
            )["passed"]
        )
        self.assertFalse(
            validate_strict_monthly_portfolio(
                [b],
                {"b": 1},
                target_month=july,
                target_valley_dd=1_000,
                target_point_dd=1_000,
            )["passed"]
        )

        result = optimize_strict_monthly_portfolio(
            monthly,
            [a, b],
            target_month=july,
            capital=10_000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=3,
            max_total_candidates=2,
            max_units_per_set=1,
            max_total_units=2,
            max_sets_per_symbol=1,
            run_local_search=False,
        )

        self.assertEqual({item.set_id for item in result.allocations}, {"a", "b"})
        self.assertTrue(result.seasonal_validation["passed"])
        self.assertEqual(result.seasonal_validation["best_month"], july)

    def test_strict_monthly_deep_refinement_adds_valid_improver(self) -> None:
        july = 7
        anchor = make_strategy("anchor", "EURUSD", [0, 1], trades=1)
        improver = make_strategy("improver", "USDJPY", [0, 1], trades=1)
        anchor_total = 0.0
        improver_total = 0.0
        anchor_points = []
        improver_points = []
        for year in range(2021, 2026):
            anchor_total += 100.0
            anchor_points.append((datetime(year, 7, 10), anchor_total))
            improver_total += 10.0
            improver_points.append((datetime(year, 7, 11), improver_total))
            improver_total += 20.0
            improver_points.append((datetime(year, 8, 11), improver_total))
        anchor.curve_points_2020_2026_001 = anchor_points
        improver.curve_points_2020_2026_001 = improver_points
        monthly, _warnings = slice_strategy_sets_to_month([anchor, improver], july)

        self.assertTrue(
            validate_strict_monthly_portfolio(
                [anchor],
                {"anchor": 1},
                target_month=july,
                target_valley_dd=1_000,
                target_point_dd=1_000,
            )["passed"]
        )
        self.assertFalse(
            validate_strict_monthly_portfolio(
                [improver],
                {"improver": 1},
                target_month=july,
                target_valley_dd=1_000,
                target_point_dd=1_000,
            )["passed"]
        )

        result = optimize_strict_monthly_portfolio(
            monthly,
            [anchor, improver],
            target_month=july,
            capital=10_000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=3,
            max_total_candidates=2,
            max_units_per_set=1,
            max_total_units=2,
            max_sets_per_symbol=1,
            run_local_search=False,
        )

        self.assertEqual({item.set_id for item in result.allocations}, {"anchor", "improver"})
        self.assertEqual(result.active_strategies, 2)
        self.assertTrue(result.seasonal_validation["passed"])
        self.assertTrue(any("Optimizacion profunda aplicada" in warning for warning in result.warnings))

    def test_strict_monthly_optimizer_can_run_without_deep_refinement(self) -> None:
        july = 7
        strategy = make_strategy("anchor", "EURUSD", [0, 1], trades=1)
        total = 0.0
        points = []
        for year in range(2021, 2026):
            total += 100.0
            points.append((datetime(year, 7, 10), total))
            total += 10.0
            points.append((datetime(year, 8, 10), total))
        strategy.curve_points_2020_2026_001 = points
        monthly, _warnings = slice_strategy_sets_to_month([strategy], july)

        result = optimize_strict_monthly_portfolio(
            monthly,
            [strategy],
            target_month=july,
            capital=10_000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=3,
            max_total_candidates=1,
            max_units_per_set=1,
            max_total_units=1,
            max_sets_per_symbol=1,
            run_local_search=False,
            use_deep_refinement=False,
        )

        self.assertTrue(result.seasonal_validation["passed"])
        self.assertTrue(any("sin optimizacion profunda" in warning for warning in result.warnings))
        self.assertFalse(any("Optimizacion profunda aplicada" in warning for warning in result.warnings))

    def test_balanced_limits_active_sets_by_asset_group(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("nas", ".USTECHCASH", [0, 120, 119, 240]),
                make_strategy("dow", ".US30CASH", [0, 115, 114, 230]),
                make_strategy("dax", ".DE40CASH", [0, 110, 109, 220]),
                make_strategy("spx", ".US500CASH", [0, 105, 104, 210]),
                make_strategy("silver", "XAGUSD", [0, 80, 79, 160]),
            ],
            capital=10000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.BALANCED,
            top_k_per_symbol=10,
            max_total_candidates=None,
            max_total_units=20,
            max_sets_per_symbol=1,
        )
        index_sets = [
            allocation
            for allocation in result.allocations
            if portfolio_group_key(allocation.symbol) == "IndicesEnergies"
        ]
        self.assertLessEqual(len(index_sets), 3)

    def test_monthly_daily_dd_limit_blocks_closed_plus_floating_risk(self) -> None:
        risky = make_strategy("risky", "EURUSD", [0, 100, 250], trades=120)
        risky.report_2020_2024.closed_trades = [
            ClosedTrade(
                open_time=datetime(2026, 7, 1, 10),
                close_time=datetime(2026, 7, 1, 12),
                symbol="EURUSD",
                volume=0.01,
                profit=-100.0,
            ),
            ClosedTrade(
                open_time=datetime(2026, 7, 2, 10),
                close_time=datetime(2026, 7, 2, 12),
                symbol="EURUSD",
                volume=0.01,
                profit=350.0,
            ),
        ]
        risky.target_month = 7
        safe = make_strategy("safe", "GBPUSD", [0, 40, 80], trades=120)
        safe.report_2020_2024.closed_trades = [
            ClosedTrade(
                open_time=datetime(2026, 7, 3, 10),
                close_time=datetime(2026, 7, 3, 12),
                symbol="GBPUSD",
                volume=0.01,
                profit=80.0,
            )
        ]
        safe.target_month = 7

        result = optimize_portfolio(
            [risky, safe],
            capital=5000,
            valley_dd_pct=20,
            point_dd_pct=20,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=5,
            max_total_candidates=None,
            max_total_units=1,
            max_daily_dd=150,
        )

        self.assertEqual([allocation.set_id for allocation in result.allocations], ["safe"])
        self.assertLessEqual(result.max_daily_dd, 150)
        self.assertEqual(result.target_daily_dd, 150)

    def test_monthly_daily_dd_full_history_blocks_non_target_month_risk(self) -> None:
        risky = make_strategy("risky", "XAUUSD", [0, 200, 400], trades=120)
        risky.target_month = 7
        risky.report_2020_2024.closed_trades = [
            ClosedTrade(
                open_time=datetime(2026, 4, 20, 10),
                close_time=datetime(2026, 4, 20, 12),
                symbol="XAUUSD",
                volume=0.01,
                profit=-160.0,
            ),
            ClosedTrade(
                open_time=datetime(2026, 7, 10, 10),
                close_time=datetime(2026, 7, 10, 12),
                symbol="XAUUSD",
                volume=0.01,
                profit=560.0,
            ),
        ]
        safe = make_strategy("safe", "EURUSD", [0, 50, 100], trades=120)
        safe.target_month = 7
        safe.report_2020_2024.closed_trades = [
            ClosedTrade(
                open_time=datetime(2026, 7, 11, 10),
                close_time=datetime(2026, 7, 11, 12),
                symbol="EURUSD",
                volume=0.01,
                profit=100.0,
            )
        ]

        month_only = optimize_portfolio(
            [risky, safe],
            capital=5000,
            valley_dd_pct=20,
            point_dd_pct=20,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=5,
            max_total_candidates=None,
            max_total_units=1,
            max_daily_dd=150,
            daily_dd_full_history=False,
        )
        full_history = optimize_portfolio(
            [risky, safe],
            capital=5000,
            valley_dd_pct=20,
            point_dd_pct=20,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=5,
            max_total_candidates=None,
            max_total_units=1,
            max_daily_dd=150,
            daily_dd_full_history=True,
        )

        self.assertEqual([allocation.set_id for allocation in month_only.allocations], ["risky"])
        self.assertFalse(month_only.daily_dd_full_history)
        self.assertEqual([allocation.set_id for allocation in full_history.allocations], ["safe"])
        self.assertTrue(full_history.daily_dd_full_history)
        self.assertLessEqual(full_history.max_daily_dd, 150)

    def test_balanced_warns_when_only_one_asset_group_is_eligible(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("nas", ".USTECHCASH", [0, 120, 119, 240]),
                make_strategy("dow", ".US30CASH", [0, 115, 114, 230]),
            ],
            capital=10000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.BALANCED,
            max_total_units=20,
        )
        self.assertTrue(any("Solo un grupo" in warning for warning in result.warnings))

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
