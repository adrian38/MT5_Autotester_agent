import random
import unittest
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.universe import (
    augment_aliases_with_symbol_map,
)
from ubs_agent import (
    DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
    PRODUCTION_SEED_SYMBOL_CAP_RATIO,
    PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
    TargetDiversityLimiter,
    apply_reserved_timeframe,
    discovery_ranked_seed_selection,
    discovery_seed_pool,
    _relative_delta_pct,
    production_viable_source_seeds,
    production_seed_pool,
    ranked_seed_selection,
    reserved_timeframe_plan,
    select_next_seed_survivors,
    select_survivors,
)
from tests.ubs_agent_files_fixtures import score


class UBSAgentSelectionTests(unittest.TestCase):
    def test_target_diversity_limiter_caps_pair_and_symbol(self) -> None:
        limiter = TargetDiversityLimiter(10)

        for _ in range(3):
            self.assertTrue(limiter.allows("META", "H4"))
            limiter.record("META", "H4")

        self.assertFalse(limiter.allows("META", "H4"))
        self.assertTrue(limiter.allows("META", "H1"))
        limiter.record("META", "H1")
        limiter.record("META", "D1")
        self.assertFalse(limiter.allows("META", "M30"))
        self.assertTrue(limiter.allows("AMZN", "H4"))

    def test_target_diversity_limiter_caps_universe_group(self) -> None:
        limiter = TargetDiversityLimiter(
            10,
            group_by_symbol={
                "XAUUSD": "Metals",
                "XAGUSD": "Metals",
                "XAUEUR": "Metals",
                "META": "Stocks",
            },
        )

        for symbol in ("XAUUSD", "XAGUSD", "XAUEUR", "XAUUSD"):
            self.assertTrue(limiter.allows(symbol, "H4"))
            limiter.record(symbol, "H4")

        self.assertFalse(limiter.allows("XAUUSD", "H1"))
        self.assertFalse(limiter.allows("XAGUSD", "H1"))
        self.assertTrue(limiter.allows("META", "H4"))

    def test_symbol_map_aliases_share_feedback_identity_and_group_cap(self) -> None:
        aliases = augment_aliases_with_symbol_map(
            {"GOLD": "XAUUSD"},
            {"XAUUSD": "XAUUSD.sa", "ORPHAN": "MISSING.sa"},
            ("XAUUSD.sa", "XAGUSD.sa"),
        )

        self.assertEqual(aliases["XAUUSD"], "XAUUSD.sa")
        self.assertEqual(aliases["GOLD"], "XAUUSD.sa")
        self.assertNotIn("ORPHAN", aliases)

        limiter = TargetDiversityLimiter(
            5,
            aliases,
            group_by_symbol={"XAUUSD.SA": "Metals", "XAGUSD.SA": "Metals"},
            group_cap_ratios={"Metals": 0.2},
        )
        self.assertTrue(limiter.allows("XAUUSD", "H1"))
        limiter.record("XAUUSD", "H1")
        self.assertFalse(limiter.allows("GOLD", "H4"))
        self.assertFalse(limiter.allows("XAGUSD.sa", "M30"))

    def test_target_diversity_limiter_uses_group_specific_caps(self) -> None:
        limiter = TargetDiversityLimiter(
            10,
            group_by_symbol={
                "EURUSD": "Forex",
                "GBPUSD": "Forex",
                "USDJPY": "Forex",
                "AUDUSD": "Forex",
                "USDCAD": "Forex",
                "USDCHF": "Forex",
                "NZDUSD": "Forex",
                "XAUUSD": "Metals",
            },
        )

        for symbol in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"):
            self.assertTrue(limiter.allows(symbol, "H1"))
            limiter.record(symbol, "H1")

        self.assertFalse(limiter.allows("NZDUSD", "H4"))
        self.assertTrue(limiter.allows("XAUUSD", "H4"))

    def test_ranked_seed_selection_keeps_diverse_alternatives(self) -> None:
        seeds = [
            *(Seed(Path(f"xau_{idx}.set"), "XAUUSD", "H4", "family", "1") for idx in range(12)),
            *(Seed(Path(f"meta_{idx}.set"), "META", "H1", "family", "1") for idx in range(4)),
            *(Seed(Path(f"eur_{idx}.set"), "EURUSD", "D1", "family", "1") for idx in range(4)),
            *(Seed(Path(f"btc_{idx}.set"), "BTCUSD", "M30", "family", "1") for idx in range(4)),
        ]

        selected = ranked_seed_selection(
            seeds,
            10,
            {"XAUUSD": 100.0, "META": 20.0, "EURUSD": 15.0, "BTCUSD": 10.0},
            {"H4": 10.0, "H1": 5.0, "D1": 4.0, "M30": 3.0},
            random.Random(7),
            {},
            {"XAUUSD": "Metals", "META": "Stocks", "EURUSD": "Forex", "BTCUSD": "Crypto"},
        )

        pairs = {(seed.symbol, seed.period) for _score, seed, _asset, _tf, _div in selected}
        self.assertIn(("XAUUSD", "H4"), pairs)
        self.assertGreater(len(pairs), 1)
        self.assertLess(sum(1 for _score, seed, _asset, _tf, _div in selected if seed.symbol == "XAUUSD"), 10)

    def test_ranked_seed_selection_symbol_reserve_keeps_discovery_broad(self) -> None:
        seeds = [
            *(Seed(Path(f"xau_{idx}.set"), "XAUUSD", "H4", "family", "1") for idx in range(20)),
            Seed(Path("xti.set"), "XTIUSD", "D1", "family", "1"),
            Seed(Path("eur.set"), "EURUSD", "H1", "family", "1"),
            Seed(Path("btc.set"), "BTCUSD", "M15", "family", "1"),
            Seed(Path("us30.set"), "US30", "M30", "family", "1"),
        ]

        selected = ranked_seed_selection(
            seeds,
            10,
            {"XAUUSD": 100.0, "XTIUSD": 10.0, "EURUSD": 9.0, "BTCUSD": 8.0, "US30": 7.0},
            {"H4": 10.0, "D1": 1.0, "H1": 1.0, "M15": 1.0, "M30": 1.0},
            random.Random(11),
            {},
            {"XAUUSD": "Metals", "XTIUSD": "Energies", "EURUSD": "Forex", "BTCUSD": "Crypto", "US30": "Indices"},
            {},
            0.40,
        )

        selected_symbols = {seed.symbol for _score, seed, _asset, _tf, _div in selected}
        self.assertGreaterEqual(len(selected_symbols), 4)
        self.assertIn("XTIUSD", selected_symbols)

    def test_discovery_seed_selection_budgets_exploitation_and_cross_asset_search(self) -> None:
        live_symbols = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "NZDUSD")
        exploitable = [
            Seed(Path(f"live_{idx}.set"), symbol, "H1", "family", "1")
            for idx, symbol in enumerate(live_symbols)
        ]
        cross_asset = [
            Seed(Path(f"legacy_{idx}.set"), f"LEGACY{idx}", "H1", "family", "1")
            for idx in range(6)
        ]
        feedback = {
            **{seed.symbol: 1.0 for seed in exploitable},
            **{seed.symbol: 100.0 for seed in cross_asset},
        }

        selected = discovery_ranked_seed_selection(
            exploitable + cross_asset,
            5,
            feedback,
            {},
            random.Random(17),
            tuple(seed.symbol for seed in exploitable),
        )
        selected_symbols = [seed.symbol for _score, seed, _asset, _tf, _div in selected]

        self.assertEqual(len(selected_symbols), 5)
        self.assertEqual(sum(symbol in live_symbols for symbol in selected_symbols), 3)
        self.assertEqual(sum(symbol.startswith("LEGACY") for symbol in selected_symbols), 2)

    def test_ranked_seed_selection_applies_final_fitness_softly(self) -> None:
        ordinary = Seed(Path("ordinary.set"), "XAUUSD", "H4", "family", "1")
        compatible = Seed(Path("compatible.set"), "XAUUSD", "H4", "family", "1")

        observed = ranked_seed_selection(
            [ordinary, compatible],
            2,
            {},
            {},
            random.Random(4),
            {},
            {},
            {str(ordinary.path): -15.0, str(compatible.path): 15.0},
        )
        neutral = ranked_seed_selection(
            [ordinary, compatible],
            2,
            {},
            {},
            random.Random(4),
            {},
            {},
            {},
        )

        self.assertNotEqual(observed, neutral)
        self.assertEqual(observed[0][1], compatible)

    def test_next_seed_survivors_are_diversified_without_changing_accepted_copy_pool(self) -> None:
        dominant = [
            (
                Variant(Path(f"xau_{idx}.set"), Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"), "XAUUSD", "H4", (), (), "", (), ()),
                score(100.0 - idx),
            )
            for idx in range(12)
        ]
        alternatives = [
            (
                Variant(Path("meta.set"), Seed(Path("seed.set"), "META", "H1", "family", "1"), "META", "H1", (), (), "", (), ()),
                score(40.0, trades=100),
            ),
            (
                Variant(Path("eur.set"), Seed(Path("seed.set"), "EURUSD", "D1", "family", "1"), "EURUSD", "D1", (), (), "", (), ()),
                score(39.0, trades=100),
            ),
        ]

        selected = select_next_seed_survivors(
            dominant + alternatives,
            20.0,
            8,
            {},
            {"XAUUSD": "Metals", "META": "Stocks", "EURUSD": "Forex"},
        )

        self.assertEqual(len(selected), 8)
        self.assertTrue(any(variant.target_symbol != "XAUUSD" for variant, _result in selected))

    def test_production_survivors_do_not_backfill_rejected_scores(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1")
        rejected = [
            (
                Variant(Path("high.set"), seed, "XAUUSD", "H4", (), (), "", (), ()),
                score(100.0, accepted=False),
            ),
            (
                Variant(Path("low.set"), seed, "XAUUSD", "H1", (), (), "", (), ()),
                score(50.0, accepted=False),
            ),
        ]

        self.assertEqual(
            select_survivors(rejected, 50.0, allow_rejected_fallback=False),
            [],
        )
        self.assertEqual(
            select_next_seed_survivors(rejected, 50.0, 10, allow_rejected_fallback=False),
            [],
        )
        self.assertEqual(len(select_survivors(rejected, 50.0)), 1)

    def test_discovery_target_symbol_cap_is_stricter_than_default(self) -> None:
        limiter = TargetDiversityLimiter(
            20,
            symbol_cap_ratio=DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
        )

        self.assertTrue(limiter.allows("XAUUSD", "H1"))
        limiter.record("XAUUSD", "H1")
        self.assertTrue(limiter.allows("XAUUSD", "H4"))
        limiter.record("XAUUSD", "H4")
        self.assertFalse(limiter.allows("XAUUSD", "D1"))

    def test_production_target_symbol_cap_is_tighter_than_legacy_default(self) -> None:
        limiter = TargetDiversityLimiter(
            20,
            symbol_cap_ratio=PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
        )

        for period in ("M1", "M5", "M15", "M30", "H1", "H4"):
            self.assertTrue(limiter.allows("XAUUSD", period))
            limiter.record("XAUUSD", period)

        self.assertFalse(limiter.allows("XAUUSD", "D1"))

    def test_production_seed_selection_uses_strict_symbol_cap_without_overflow(self) -> None:
        symbols = (("XAUUSD", "M1"), ("BTCUSD", "M5"), ("EURUSD", "M15"), ("US30", "M30"))
        seeds = [
            Seed(Path(f"{symbol}_{idx}.set"), symbol, period, "family", "1")
            for symbol, period in symbols
            for idx in range(10)
        ]

        selected = ranked_seed_selection(
            seeds,
            12,
            {"XAUUSD": 100.0, "BTCUSD": 90.0, "EURUSD": 80.0, "US30": 70.0},
            {},
            random.Random(5),
            {},
            {},
            {},
            0.0,
            symbol_cap_ratio=PRODUCTION_SEED_SYMBOL_CAP_RATIO,
            allow_overflow=False,
        )

        counts: dict[str, int] = {}
        for _score, seed, _asset, _tf, _div in selected:
            counts[seed.symbol] = counts.get(seed.symbol, 0) + 1

        self.assertEqual(len(selected), 12)
        self.assertLessEqual(max(counts.values()), 3)

    def test_production_viable_source_seeds_filters_dead_cross_group_sources(self) -> None:
        dead = Seed(Path("xau.set"), "XAUUSD", "H1", "family", "1")
        viable = Seed(Path("eur.set"), "EURUSD", "H1", "family", "1")

        selected = production_viable_source_seeds(
            [dead, viable],
            ("EURUSD",),
            {},
            disabled_symbols={"XAUUSD"},
            group_by_symbol={"XAUUSD": "Metals", "EURUSD": "Forex"},
        )

        self.assertEqual(selected, [viable])

    def test_discovery_seed_pool_reinjects_source_seeds_without_duplicates(self) -> None:
        survivor = Seed(Path("survivor.set"), "EURUSD", "H1", "family", "1")
        source_a = Seed(Path("source_a.set"), "XTIUSD", "D1", "family", "1")
        source_b = Seed(Path("survivor.set"), "EURUSD", "H1", "family", "1")

        pool = discovery_seed_pool([survivor], [source_a, source_b])

        self.assertEqual([seed.path.name for seed in pool], ["survivor.set", "source_a.set"])
        self.assertIn("XTIUSD", {seed.symbol for seed in pool})

    def test_production_seed_pool_backfills_only_when_survivors_are_sparse(self) -> None:
        survivors = [Seed(Path(f"survivor_{idx}.set"), "EURUSD", "H1", "family", "1") for idx in range(18)]
        sources = [Seed(Path(f"source_{idx}.set"), "XAUUSD", "H1", "family", "1") for idx in range(30)]

        self.assertEqual(production_seed_pool(survivors, sources, 30), survivors)

        sparse = survivors[:3]
        pool = production_seed_pool(sparse, sources, 30)

        self.assertEqual(pool[:3], sparse)
        self.assertEqual(len(pool), 30)
        self.assertEqual(len({str(seed.path).lower() for seed in pool}), 30)

    def test_next_seed_survivors_apply_final_fitness_softly(self) -> None:
        higher_score = Variant(
            Path("higher.set"),
            Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"),
            "XAUUSD", "H4", (), (), "", (), (),
        )
        lower_score = Variant(
            Path("lower.set"),
            Seed(Path("seed.set"), "EURUSD", "H1", "family", "1"),
            "EURUSD", "H1", (), (), "", (), (),
        )

        selected = select_next_seed_survivors(
            [(higher_score, score(100.0)), (lower_score, score(50.0))],
            20.0,
            1,
            {},
            {},
            {str(higher_score.path): -15.0, str(lower_score.path): 15.0},
        )

        self.assertEqual(selected[0][0], higher_score)

    def test_next_seed_survivors_allow_fitness_to_nudge_close_scores(self) -> None:
        slightly_higher_score = Variant(
            Path("slightly_higher.set"),
            Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"),
            "XAUUSD", "H4", (), (), "", (), (),
        )
        slightly_lower_score = Variant(
            Path("slightly_lower.set"),
            Seed(Path("seed.set"), "EURUSD", "H1", "family", "1"),
            "EURUSD", "H1", (), (), "", (), (),
        )

        selected = select_next_seed_survivors(
            [(slightly_higher_score, score(52.0)), (slightly_lower_score, score(50.0))],
            20.0,
            1,
            {},
            {},
            {str(slightly_higher_score.path): -15.0, str(slightly_lower_score.path): 15.0},
        )

        self.assertEqual(selected[0][0], slightly_lower_score)

    def test_relative_delta_is_symmetric(self) -> None:
        forward = _relative_delta_pct(100.0, 135.0)
        reverse = _relative_delta_pct(135.0, 100.0)

        self.assertAlmostEqual(forward, reverse)
        self.assertAlmostEqual(forward, 35.0 / 135.0 * 100.0)

    def test_relative_delta_uses_explicit_symmetric_threshold_semantics(self) -> None:
        self.assertAlmostEqual(_relative_delta_pct(100.0, 150.0), 100.0 / 3.0)
        self.assertLessEqual(_relative_delta_pct(100.0, 150.0), 35.0)
        self.assertGreater(_relative_delta_pct(100.0, 160.0), 35.0)

    def test_reserved_timeframe_plan_targets_missing_allowed_timeframes(self) -> None:
        selected = [Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1")]

        plan = reserved_timeframe_plan(selected, ("M1", "M5", "H4", "D1"), 10)

        self.assertEqual(plan, ["M1", "M5", "D1"])

    def test_reserved_timeframe_plan_applies_minimum_intraday_quotas(self) -> None:
        selected = [
            Seed(Path("h1.set"), "META", "H1", "family", "1"),
            Seed(Path("h4.set"), "XAGUSD", "H4", "family", "1"),
            Seed(Path("d1.set"), "USDJPY", "D1", "family", "1"),
        ]

        plan = reserved_timeframe_plan(selected, ("M1", "M5", "M15", "M30", "H1", "H4", "D1"), 300)

        self.assertEqual(plan.count("M1"), 6)
        self.assertEqual(plan.count("M5"), 6)
        self.assertEqual(plan.count("M15"), 9)
        self.assertEqual(plan.count("M30"), 15)

    def test_apply_reserved_timeframe_overrides_target_when_allowed(self) -> None:
        reserved = ["M1"]
        limiter = TargetDiversityLimiter(10)

        symbol, period, policy = apply_reserved_timeframe(
            reserved_timeframes=reserved,
            target_symbol="XAUUSD",
            target_period="H4",
            policy="exploit",
            seed=Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"),
            target_limiter=limiter,
            universe_symbols=("META", "EURUSD"),
            disabled_symbols=set(),
            aliases={},
            rng=random.Random(1),
        )

        self.assertEqual(symbol, "XAUUSD")
        self.assertEqual(period, "M1")
        self.assertEqual(policy, "exploit+tf_reserved")
        self.assertEqual(reserved, [])
