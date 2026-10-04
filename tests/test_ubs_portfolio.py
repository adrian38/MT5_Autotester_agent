from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from portfolio_manager.ubs_portfolio import (
    PortfolioType,
    bootstrap_valley_drawdown,
    calc_point_dd,
    calc_valley_dd,
    filter_eligible_sets,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    merge_accumulated_curves,
    optimize_portfolio,
    portfolio_group_key,
    recent_positive_month_count,
    score_set_for_portfolio,
    select_top_k_per_symbol,
    set_file_has_enabled_grid,
    slice_strategy_set_to_month,
    slice_strategy_sets_to_month,
    summarize_robust_rows,
    validate_strict_monthly_portfolio,
)
from tests.ubs_portfolio_fixtures import make_strategy


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

    def test_robust_set_loader_reports_missing_report_examples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            robust_report = root / "robust.htm"
            robust_report.write_text("report", encoding="utf-8")
            missing_report = root / "missing_base.htm"

            loaded, warnings = load_robust_sets_from_rows(
                [
                    {
                        "set_path": str(root / "candidate.set"),
                        "candidate_id": 1,
                        "is_report_path": str(missing_report),
                        "oos_report_path": str(robust_report),
                    }
                ],
                [],
            )

            self.assertEqual(loaded, [])
            detail = " ".join(warnings)
            self.assertIn("candidate.set", detail)
            self.assertIn("base=missing_base.htm", detail)

    def test_robust_set_loader_reports_parse_error_examples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_report = root / "base.htm"
            robust_report = root / "robust.htm"
            base_report.write_text("report", encoding="utf-8")
            robust_report.write_text("report", encoding="utf-8")

            def fail_parse(_path: Path):
                raise ValueError("periodos solapados")

            loaded, warnings = load_robust_sets_from_rows(
                [
                    {
                        "set_path": str(root / "candidate.set"),
                        "candidate_id": 1,
                        "is_report_path": str(base_report),
                        "oos_report_path": str(robust_report),
                    }
                ],
                [],
                parse=fail_parse,
            )

            self.assertEqual(loaded, [])
            detail = " ".join(warnings)
            self.assertIn("candidate.set", detail)
            self.assertIn("ValueError: periodos solapados", detail)

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

    def test_availability_displays_broker_target_symbol_not_alias(self) -> None:
        availability = summarize_robust_rows(
            [
                {
                    "set_path": "C:/sets/nas100.set",
                    "symbol": "USTEC",
                    "target_symbol": "NAS100.fs",
                }
            ],
            [],
        )

        self.assertEqual(availability.by_symbol, {"NAS100.fs": 1})
        self.assertNotIn(".USTECHCASH", availability.by_symbol)

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
        self.assertLessEqual(result.group_summary["Indices"]["unit_pct"], 55.1)

    def test_conservative_two_group_cap_uses_feasible_diversification_floor(self) -> None:
        result = optimize_portfolio(
            [
                make_strategy("eur", "EURUSD", [0, 90, 85, 180]),
                make_strategy("gbp", "GBPUSD", [0, 85, 80, 170]),
                make_strategy("gold", "XAUUSD", [0, 80, 75, 160]),
                make_strategy("silver", "XAGUSD", [0, 75, 70, 150]),
            ],
            capital=10_000,
            valley_dd_pct=50,
            point_dd_pct=50,
            portfolio_type=PortfolioType.CONSERVATIVE,
            top_k_per_symbol=10,
            max_total_candidates=None,
            max_total_units=40,
            max_sets_per_symbol=1,
            minimum_active_strategies=4,
            maximum_active_strategies=4,
            run_local_search=True,
        )

        self.assertEqual(result.active_strategies, 4)
        self.assertGreater(result.total_units, result.active_strategies)
        self.assertLessEqual(
            max(float(row["unit_pct"]) for row in result.group_summary.values()),
            50.1,
        )
        self.assertTrue(
            any("40.0% -> 50.0%" in warning for warning in result.warnings)
        )

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
        self.assertIn("Indices", selected_groups)
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
