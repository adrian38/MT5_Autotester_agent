import argparse
import json
import random
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs.memory import AgentMemory
from ubs_agent import (
    TargetDiversityLimiter,
    choose_diverse_target,
    choose_target_period,
    choose_target_symbol,
    related_timeframes,
    score_config_for_period,
    target_timeframe_universe,
    min_trades_for_period,
    probability_argument,
    restore_run_unseeded_probabilities,
    restored_discovery_exploitable_ratio,
    restored_discovery_current_target_probability,
    restored_discovery_current_timeframe_probability,
    restored_discovery_universe_feedback_probability,
    unseeded_asset_force_probability,
    unseeded_timeframe_force_probability,
)
from tests.ubs_agent_files_fixtures import score


class UBSAgentDiscoveryTests(unittest.TestCase):
    def test_unseeded_force_probabilities_drop_after_generation_one(self) -> None:
        self.assertEqual(unseeded_asset_force_probability(1, 10), 0.12)
        self.assertEqual(unseeded_asset_force_probability(2, 10), 0.08)
        self.assertEqual(unseeded_asset_force_probability(3, 10), 0.05)
        self.assertEqual(unseeded_asset_force_probability(1, 0), 0.0)
        self.assertEqual(unseeded_timeframe_force_probability(1, 2), 0.20)
        self.assertEqual(unseeded_timeframe_force_probability(3, 2), 0.08)

    def test_unseeded_force_probabilities_accept_run_specific_schedule(self) -> None:
        self.assertEqual(unseeded_asset_force_probability(1, 10, {1: 0.3, 2: 0.2}, 0.1), 0.3)
        self.assertEqual(unseeded_asset_force_probability(4, 10, {1: 0.3, 2: 0.2}, 0.1), 0.1)
        self.assertEqual(probability_argument("0.25"), 0.25)
        with self.assertRaises(argparse.ArgumentTypeError):
            probability_argument("1.1")

    def test_unseeded_asset_selection_uses_group_lifecycle_feedback(self) -> None:
        stocks = tuple(f"STOCK_{index}" for index in range(100))
        metals = tuple(f"METAL_{index}" for index in range(4))
        universe = (*stocks, *metals)
        groups = {
            **{symbol: "Stocks" for symbol in stocks},
            **{symbol: "Metals" for symbol in metals},
        }
        rng = random.Random(20260812)

        selected = [
            choose_target_symbol(
                Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1"),
                {},
                rng,
                universe,
                {},
                force_unseeded_universe=True,
                unseeded_universe_symbols=universe,
                force_unseeded_probability=1.0,
                group_by_symbol=groups,
                asset_group_feedback={"Stocks": -24.0, "Metals": 24.0},
            )
            for _ in range(400)
        ]

        metal_count = sum(target.startswith("METAL_") for target, _policy in selected)
        self.assertGreater(metal_count, 320)
        self.assertTrue(all(policy == "asset_unseeded_group_feedback" for _target, policy in selected))

    def test_asset_feedback_with_groups_separates_lifecycle_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root, root / "output", 1, 1, 2, True, False)
                seed = Seed(root / "seed.set", "XAUUSD", "H1", "family", "1")
                for name, symbol, value, status in (
                    ("metal.set", "XAUUSD", 120.0, "accepted"),
                    ("stock.set", "AAPL", -80.0, "rejected"),
                    ("obsolete.set", "OLD+", 5000.0, "accepted"),
                ):
                    variant = Variant(root / name, seed, symbol, "H1", (), (), "test")
                    memory.record_variant(run_id, 1, variant)
                    memory.record_score(
                        variant.path,
                        score(value, symbol=symbol),
                        status,
                        root / f"{name}.htm",
                    )

                assets, groups = memory.asset_feedback_with_groups(
                    {},
                    {"XAUUSD": "Metals", "AAPL": "Stocks"},
                )

                self.assertIn("XAUUSD", assets)
                self.assertIn("AAPL", assets)
                self.assertNotIn("OLD+", assets)
                self.assertGreater(groups["Metals"], groups["Stocks"])
                filtered_signals = memory.asset_feedback_signals(
                    {},
                    allowed_symbols={"XAUUSD", "AAPL"},
                )
                self.assertEqual(set(filtered_signals), {"XAUUSD", "AAPL"})
            finally:
                memory.close()

    def test_resume_restores_persisted_unseeded_schedule(self) -> None:
        args = SimpleNamespace(
            asset_unseeded_prob_gen1=0.12,
            asset_unseeded_prob_gen2=0.08,
            asset_unseeded_prob_late=0.05,
            timeframe_unseeded_prob_gen1=0.20,
            timeframe_unseeded_prob_gen2=0.12,
            timeframe_unseeded_prob_late=0.08,
        )
        restore_run_unseeded_probabilities(
            args,
            json.dumps(
                {
                    "generation": {
                        "asset_unseeded_force_probability": {
                            "generation_1": 0.35,
                            "generation_2": 0.25,
                            "late": 0.15,
                        },
                        "timeframe_unseeded_force_probability": {
                            "generation_1": 0.18,
                            "generation_2": 0.11,
                            "late": 0.07,
                        },
                    }
                }
            ),
        )

        self.assertEqual(args.asset_unseeded_prob_gen1, 0.35)
        self.assertEqual(args.asset_unseeded_prob_gen2, 0.25)
        self.assertEqual(args.asset_unseeded_prob_late, 0.15)
        self.assertEqual(args.timeframe_unseeded_prob_gen1, 0.18)
        self.assertEqual(args.timeframe_unseeded_prob_gen2, 0.11)
        self.assertEqual(args.timeframe_unseeded_prob_late, 0.07)

    def test_resume_restores_persisted_discovery_source_ratio(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "seed_selection_diversity_caps": {
                        "discovery_exploitable_seed_min_ratio": 0.74,
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_exploitable_ratio(config), 0.74)
        self.assertEqual(restored_discovery_exploitable_ratio("{}"), 0.60)

    def test_resume_restores_persisted_discovery_feedback_probability(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "target_policy": {
                        "discovery_adaptive_policy": {
                            "universe_feedback": {"probability": 0.79}
                        }
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_universe_feedback_probability(config), 0.79)
        self.assertEqual(restored_discovery_universe_feedback_probability("{}"), 0.55)

    def test_resume_restores_persisted_discovery_current_target_probability(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "target_policy": {
                        "discovery_adaptive_policy": {
                            "current_target": {"probability": 0.82}
                        }
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_current_target_probability(config), 0.82)
        self.assertEqual(restored_discovery_current_target_probability("{}"), 0.70)

    def test_resume_restores_persisted_discovery_current_timeframe_probability(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "target_policy": {
                        "discovery_adaptive_policy": {
                            "current_timeframe": {"probability": 0.73}
                        }
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_current_timeframe_probability(config), 0.73)
        self.assertEqual(restored_discovery_current_timeframe_probability("{}"), 0.60)

    def test_current_timeframe_probability_controls_discovery_branch(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            current_timeframe_probability=1.0,
        )
        _changed_target, changed_policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            current_timeframe_probability=0.0,
        )

        self.assertEqual(target, "H1")
        self.assertEqual(policy, "tf_exploit")
        self.assertNotEqual(changed_policy, "tf_exploit")

    def test_current_target_probability_controls_discovery_exploit_branch(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XAUUSD", "EURUSD"),
            {},
            current_target_probability=1.0,
        )
        _cross_target, cross_policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XAUUSD", "EURUSD"),
            {},
            current_target_probability=0.0,
        )

        self.assertEqual(target, "XAUUSD")
        self.assertEqual(policy, "exploit")
        self.assertNotEqual(cross_policy, "exploit")

    def test_target_timeframe_universe_keeps_long_timeframes_experimental(self) -> None:
        normal = target_timeframe_universe(False)
        experimental = target_timeframe_universe(True)

        self.assertIn("M1", normal)
        self.assertIn("M5", normal)
        self.assertNotIn("W1", normal)
        self.assertNotIn("MN", normal)
        self.assertEqual(experimental[-2:], ("W1", "MN"))

    def test_related_timeframes_filter_experimental_long_timeframes(self) -> None:
        self.assertEqual(related_timeframes("D1", target_timeframe_universe(False)), ("H4", "D1"))
        self.assertEqual(related_timeframes("D1", target_timeframe_universe(True)), ("H4", "D1", "W1", "MN"))

    def test_choose_target_period_does_not_emit_long_timeframes_by_default(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "D1", "family", "1")

        for idx in range(40):
            target, _policy = choose_target_period(
                seed,
                {},
                random.Random(idx),
                timeframe_universe=target_timeframe_universe(False),
            )
            self.assertNotIn(target, {"W1", "MN"})

    def test_choose_target_period_can_force_experimental_long_timeframes(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "D1", "family", "1")

        target, policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            timeframe_universe=target_timeframe_universe(True),
            force_unseeded_timeframes=True,
            unseeded_timeframes=("W1", "MN"),
            force_unseeded_probability=1.0,
        )

        self.assertIn(target, {"W1", "MN"})
        self.assertEqual(policy, "tf_unseeded_force")

    def test_long_timeframe_min_trades_only_overrides_w1_mn(self) -> None:
        config = ScoreConfig(min_trades=48)

        self.assertEqual(score_config_for_period(config, "H4", min_trades_w1=12, min_trades_mn=4).min_trades, 48)
        self.assertEqual(score_config_for_period(config, "W1", min_trades_w1=12, min_trades_mn=4).min_trades, 12)
        self.assertEqual(score_config_for_period(config, "MN", min_trades_w1=12, min_trades_mn=4).min_trades, 4)

    def test_final_tick_min_trades_can_be_lower_for_long_timeframes(self) -> None:
        self.assertEqual(min_trades_for_period("H1", 5, 2, 1), 5)
        self.assertEqual(min_trades_for_period("W1", 5, 2, 1), 2)
        self.assertEqual(min_trades_for_period("MN", 5, 2, 1), 1)

    def test_zero_unseeded_probability_disables_forced_symbol_branch(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        for idx in range(20):
            _target, policy = choose_target_symbol(
                seed,
                {},
                random.Random(idx),
                ("EURUSD", "GBPUSD"),
                {},
                force_unseeded_universe=True,
                unseeded_universe_symbols=("EURUSD", "GBPUSD"),
                force_unseeded_probability=0.0,
            )
            self.assertNotEqual(policy, "asset_unseeded_force")

    def test_zero_unseeded_probability_disables_forced_timeframe_branch(self) -> None:
        seed = Seed(Path("seed.set"), "MYSTERY", "H1", "family", "1")

        for idx in range(20):
            _target, policy = choose_target_period(
                seed,
                {},
                random.Random(idx),
                force_unseeded_timeframes=True,
                unseeded_timeframes=("D1",),
                force_unseeded_probability=0.0,
            )
            self.assertNotEqual(policy, "tf_unseeded_force")

    def test_production_symbol_selection_does_not_random_walk_universe(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        allowed = {"XAUUSD", "XAGUSD", "XAUEUR"}

        observed = {
            choose_target_symbol(
                seed,
                {},
                random.Random(idx),
                ("AAPL.NAS", "TSLA.NAS", "XAGUSD", "XAUEUR"),
                {},
                production_mode=True,
            )[0]
            for idx in range(50)
        }

        self.assertTrue(observed)
        self.assertTrue(observed <= allowed)

    def test_discovery_current_target_probability_does_not_change_production(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XAUUSD", "EURUSD"),
            {},
            production_mode=True,
            current_target_probability=0.0,
        )

        self.assertEqual(target, "XAUUSD")
        self.assertEqual(policy, "production_exploit")

    def test_discovery_current_timeframe_probability_does_not_change_production(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            production_mode=True,
            current_timeframe_probability=0.0,
        )

        self.assertEqual(target, "H1")
        self.assertEqual(policy, "tf_production_exploit")

    def test_production_symbol_selection_uses_positive_evidence_fallback(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_symbol(
            seed,
            {"EURUSD": 5.0, "AAPL.NAS": -10.0},
            random.Random(1),
            ("EURUSD", "AAPL.NAS"),
            {},
            disabled_symbols={"XAUUSD"},
            production_mode=True,
        )

        self.assertEqual(target, "EURUSD")
        self.assertEqual(policy, "production_asset_feedback")

    def test_production_symbol_selection_blocks_cross_group_feedback_when_group_known(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target = choose_target_symbol(
            seed,
            {"EURUSD": 50.0},
            random.Random(1),
            ("EURUSD",),
            {},
            disabled_symbols={"XAUUSD"},
            production_mode=True,
            group_by_symbol={"XAUUSD": "Metals", "EURUSD": "Forex"},
        )

        self.assertIsNone(target)

    def test_production_symbol_selection_uses_same_group_when_current_disabled(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target = choose_target_symbol(
            seed,
            {"EURUSD": 50.0},
            random.Random(1),
            ("XAGUSD", "EURUSD"),
            {},
            disabled_symbols={"XAUUSD"},
            production_mode=True,
            group_by_symbol={"XAUUSD": "Metals", "XAGUSD": "Metals", "EURUSD": "Forex"},
        )

        self.assertIsNotNone(target)
        self.assertEqual(target[0], "XAGUSD")
        self.assertNotEqual(target[0], "EURUSD")

    def test_production_timeframe_selection_avoids_unexplored_timeframes(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        observed = {
            choose_target_period(
                seed,
                {},
                random.Random(idx),
                timeframe_universe=("M1", "M5", "M15", "M30", "H1", "H4", "D1"),
                production_mode=True,
            )[0]
            for idx in range(50)
        }

        self.assertEqual(observed, {"H1"})

    def test_production_diverse_target_fallback_stays_near_seed(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        limiter = TargetDiversityLimiter(4)
        limiter.record("XAUUSD", "H1")
        limiter.record("XAUUSD", "H4")

        target_symbol, target_period, policy = choose_diverse_target(
            seed,
            {},
            {},
            random.Random(2),
            limiter,
            ("AAPL.NAS", "TSLA.NAS", "XAGUSD", "XAUEUR"),
            {},
            production_mode=True,
        )

        self.assertIn(target_symbol, {"XAGUSD", "XAUEUR"})
        self.assertEqual(target_period, "H1")
        self.assertNotIn("asset_universe_explore", policy)

    def test_production_diverse_target_returns_none_instead_of_overflow(self) -> None:
        seed = Seed(Path("seed.set"), "ONLY", "H1", "family", "1")
        limiter = TargetDiversityLimiter(1)
        limiter.record("ONLY", "H1")

        target = choose_diverse_target(
            seed,
            {},
            {},
            random.Random(2),
            limiter,
            ("ONLY",),
            {},
            timeframe_universe=("H1",),
            production_mode=True,
        )

        self.assertIsNone(target)
