from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest

from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PortfolioType,
    build_portfolio_greedy,
    improve_with_local_search,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
    portfolio_group_key,
    slice_strategy_sets_to_month,
    validate_strict_monthly_portfolio,
)
from tests.ubs_portfolio_fixtures import make_strategy
from ubs.universe import load_asset_universe


class UBSPortfolioStrictMonthlyTests(unittest.TestCase):
    @staticmethod
    def _complementary_july_pair():
        """Dos curvas que solo pasan julio combinadas: cada una cae en otro mes."""
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
        return a, b

    def test_strict_monthly_optimizer_can_combine_non_individually_best_sets(self) -> None:
        july = 7
        a, b = self._complementary_july_pair()
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
            if portfolio_group_key(allocation.symbol) == "Indices"
        ]
        self.assertLessEqual(len(index_sets), 3)

    def test_ictrading_universe_groups_are_available_to_portfolio(self) -> None:
        groups, _aliases = load_asset_universe(
            Path("assets/ictrading_assets.ini"),
            include_disabled=True,
        )
        expected_groups = {
            "Forex", "Metals", "Indices", "Energies",
            "Crypto", "Stocks", "Bonds", "Commodities",
        }

        self.assertEqual(set(groups), expected_groups)
        for source_group, symbols in groups.items():
            expected_group = "Softs" if source_group == "Commodities" else source_group
            with self.subTest(group=source_group):
                self.assertTrue(symbols)
                self.assertTrue(
                    all(
                        portfolio_group_key(
                            symbol,
                            universe_files=[Path("assets/ictrading_assets.ini")],
                        ) == expected_group
                        for symbol in symbols
                    )
                )

    def test_axi_universe_groups_are_available_to_portfolio(self) -> None:
        groups, _aliases = load_asset_universe(
            Path("assets/axi_assets.ini"),
            include_disabled=True,
        )
        expected_groups = {
            "Forex", "Metals", "Indices", "Energies",
            "Crypto", "Stocks", "Commodities",
        }

        self.assertEqual(set(groups), expected_groups)
        for source_group, symbols in groups.items():
            expected_group = "Softs" if source_group == "Commodities" else source_group
            with self.subTest(group=source_group):
                self.assertTrue(symbols)
                self.assertTrue(
                    all(
                        portfolio_group_key(
                            symbol,
                            universe_files=[Path("assets/axi_assets.ini")],
                        ) == expected_group
                        for symbol in symbols
                    )
                )

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

    @staticmethod
    def _daily_dd_pair():
        """Una estrategia con perdida fuera del mes objetivo y otra limpia dentro."""
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
        return risky, safe

    @staticmethod
    def _optimize_with_daily_dd(sets, *, full_history: bool):
        return optimize_portfolio(
            sets,
            capital=5000,
            valley_dd_pct=20,
            point_dd_pct=20,
            portfolio_type=PortfolioType.AGGRESSIVE,
            min_trades_2020_2026=1,
            top_k_per_symbol=5,
            max_total_candidates=None,
            max_total_units=1,
            max_daily_dd=150,
            daily_dd_full_history=full_history,
        )

    def test_monthly_daily_dd_full_history_blocks_non_target_month_risk(self) -> None:
        risky, safe = self._daily_dd_pair()
        month_only = self._optimize_with_daily_dd([risky, safe], full_history=False)
        full_history = self._optimize_with_daily_dd([risky, safe], full_history=True)

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
