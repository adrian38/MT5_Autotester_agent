import random
import tempfile
import unittest
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig, ScoreResult
from ubs.account import account_disabled_symbols_path
from ubs.universe import load_disabled_symbols, load_seed_enabled_disabled_symbols, save_disabled_symbols, seed_symbol_disabled
from ubs_agent import (
    TargetDiversityLimiter,
    choose_diverse_target,
    choose_target_period,
    choose_target_symbol,
    copy_accepted,
    create_variant,
    final_tick_similarity,
    recreate_work_dir,
    related_timeframes,
    robust_status_pending_for_retry,
    score_config_for_period,
    target_timeframe_universe,
    min_trades_for_period,
    unseeded_asset_force_probability,
    unseeded_timeframe_force_probability,
    validate_seed_backtest_set,
    variant_as_next_seed,
    write_set_force_symbol,
)
from run_tests import parse_symbol_map


def score(
    value: float,
    *,
    net_profit: float = 100.0,
    profit_factor: float = 2.0,
    drawdown_pct: float = 1.0,
    trades: int = 100,
    history_quality: float | None = 100.0,
) -> ScoreResult:
    return ScoreResult(
        report_path="report.htm",
        name="report",
        symbol="XAUUSD",
        timeframe="H1",
        score=value,
        accepted=True,
        net_profit=net_profit,
        raw_net_profit=net_profit,
        normalized_net_profit=net_profit,
        net_profit_factor=1.0,
        net_profit_basis="test",
        normalization_group="test",
        history_quality=history_quality,
        profit_factor=profit_factor,
        recovery_factor=2.0,
        drawdown=10.0,
        drawdown_pct=drawdown_pct,
        trades=trades,
        positive_month_ratio=1.0,
        max_month_concentration=0.1,
        avg_trade=1.0,
        sqn=1.0,
        reasons=(),
    )


class UBSSetsFileTests(unittest.TestCase):
    def test_crudeoil_seed_is_disabled_when_wti_is_disabled(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertTrue(seed_symbol_disabled(seed, {"WTI"}, symbol_map))

    def test_seed_enabled_symbol_allows_disabled_seed(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertFalse(seed_symbol_disabled(seed, {"WTI"}, symbol_map, {"WTI"}))

    def test_disabled_symbols_json_preserves_seed_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ubs_disabled_symbols.json"

            save_disabled_symbols(path, {"WTI", "XAUUSD"}, {"WTI"})

            self.assertEqual(load_disabled_symbols(path), {"WTI", "XAUUSD"})
            self.assertEqual(load_seed_enabled_disabled_symbols(path), {"WTI"})

    def test_disabled_seed_source_generates_enabled_target(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("EURUSD", "WTI"),
            {},
            symbol_map=symbol_map,
            disabled_symbols={"WTI"},
        )

        self.assertEqual(target, "EURUSD")
        self.assertNotEqual(policy, "exploit")

    def test_disabled_symbols_policy_is_account_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            self.assertEqual(
                account_disabled_symbols_path(base_dir, "ECN"),
                base_dir / "outputs" / "ubs_disabled_symbols_ECN.json",
            )
            self.assertEqual(
                account_disabled_symbols_path(base_dir, "PRO"),
                base_dir / "outputs" / "ubs_disabled_symbols_PRO.json",
            )

    def test_seed_validation_rejects_incomplete_ubs_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.set"
            path.write_text(
                "\n".join(
                    [
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=60||5||0||16385||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(path, "XAUUSD", "H1", "family", "")

            issues = validate_seed_backtest_set(seed)

            self.assertIn("sin ForceSymbol", issues)
            self.assertIn("sin Run_Strategy valido", issues)
            self.assertIn("Entry_Timing=60 no es timeframe MT5 valido", issues)

    def test_seed_validation_accepts_bound_ubs_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "good.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "ST1_Timeframe=16385||0||0||49153||N",
                        "Entry_Timing=16385||5||0||16385||N",
                        "ATR_Timeframe=16385||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(path, "XAUUSD", "H1", "family", "1")

            self.assertEqual(validate_seed_backtest_set(seed), [])

    def test_write_set_force_symbol_adds_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seed.set"
            path.write_text("Run_Strategy=1||1||0||2||N\nST1_Timeframe=16385||0||0||49153||N", encoding="utf-8")

            write_set_force_symbol(path, path, "XAUUSD")

            self.assertIn("ForceSymbol=XAUUSD", path.read_text(encoding="utf-8"))

    def test_copy_accepted_replaces_previous_copy_for_same_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = recreate_work_dir(Path(temp_dir))
            source = root / "candidate.set"
            source.write_text("set", encoding="utf-8")
            accepted_dir = root / "accepted"
            seed = Seed(source, "XAUUSD", "H1", "family", "1")
            variant = Variant(source, seed, "XAUUSD", "H1", (), (), "test")

            first = copy_accepted([(variant, score(10.0))], accepted_dir)
            second = copy_accepted([(variant, score(20.0))], accepted_dir)

            files = sorted(accepted_dir.glob("*.set"))
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(files, second)
            self.assertEqual(files[0].name, "score_0020.00__candidate.set")

    def test_variant_as_next_seed_preserves_target_timeframe(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        variant = Variant(Path("candidate.set"), seed, "XAUUSD", "D1", ("Exit_stop",), (), "tf_feedback")

        next_seed = variant_as_next_seed(variant)

        self.assertEqual(next_seed.path, variant.path)
        self.assertEqual(next_seed.symbol, "XAUUSD")
        self.assertEqual(next_seed.period, "D1")
        self.assertEqual(next_seed.family, seed.family)
        self.assertEqual(next_seed.run_strategy, seed.run_strategy)

    def test_unseeded_force_probabilities_drop_after_generation_one(self) -> None:
        self.assertEqual(unseeded_asset_force_probability(1, 10), 0.35)
        self.assertEqual(unseeded_asset_force_probability(2, 10), 0.25)
        self.assertEqual(unseeded_asset_force_probability(3, 10), 0.15)
        self.assertEqual(unseeded_asset_force_probability(1, 0), 0.0)
        self.assertEqual(unseeded_timeframe_force_probability(1, 2), 0.20)
        self.assertEqual(unseeded_timeframe_force_probability(3, 2), 0.08)

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

    def test_choose_diverse_target_avoids_capped_symbol(self) -> None:
        seed = Seed(Path("seed.set"), "META", "H4", "family", "1")
        limiter = TargetDiversityLimiter(4)
        limiter.record("META", "H4")
        limiter.record("META", "H1")

        target_symbol, target_period, _policy = choose_diverse_target(
            seed,
            {"META": 100.0, "AMZN": 10.0, "MSFT": 8.0},
            {"H4": 10.0, "H1": 8.0},
            random.Random(3),
            limiter,
            ("META", "AMZN", "MSFT"),
            {},
        )

        self.assertNotEqual(target_symbol, "META")
        self.assertTrue(limiter.allows(target_symbol, target_period))

    def test_create_variant_separates_timeframe_keys_from_mutated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "seed.set"
            source.write_text(
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "ST1_Timeframe=16385||0||1||16408||Y",
                        "Entry_Timing=16385||0||1||16408||Y",
                        "ATR_Timeframe=16385||0||1||16408||Y",
                        "Exit_stop=100||50||10||150||Y",
                        "Risk=2||2||0||10||N",
                        "StartLots=0.01||0.01||0.01||1||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(source, "XAUUSD", "H1", "family", "1")

            variant = create_variant(
                seed,
                "XAUUSD",
                "D1",
                Path(temp_dir) / "out",
                1,
                1,
                1,
                1,
                {},
                {},
                "test",
                random.Random(2),
            )

            self.assertIn("ST1_Timeframe", variant.timeframe_keys)
            self.assertIn("Entry_Timing", variant.timeframe_keys)
            self.assertIn("ATR_Timeframe", variant.timeframe_keys)
            self.assertNotIn("ST1_Timeframe", variant.mutated_keys)
            self.assertEqual(len(variant.mutated_keys), 1)
            self.assertEqual(variant.mutation_details[0]["key"], variant.mutated_keys[0])

    def test_recreate_work_dir_removes_previous_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "work"
            path.mkdir()
            (path / "old.set").write_text("old", encoding="utf-8")

            recreated = recreate_work_dir(path)

            self.assertEqual(recreated, path)
            self.assertTrue(path.exists())
            self.assertEqual(list(path.iterdir()), [])

    def test_final_tick_similarity_requires_history_quality(self) -> None:
        result = final_tick_similarity(
            score(10.0),
            score(10.0, history_quality=None),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertFalse(result["accepted"])
        self.assertIn("history_quality", result["reasons"])

    def test_final_tick_similarity_keeps_net_profit_drift_informational(self) -> None:
        result = final_tick_similarity(
            score(10.0, net_profit=100.0),
            score(10.0, net_profit=200.0),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertTrue(result["accepted"])
        self.assertNotIn("net_profit", result["reasons"])
        self.assertFalse(result["checks"]["net_profit"]["checked"])

    def test_final_tick_similarity_rejects_large_profit_factor_drift(self) -> None:
        result = final_tick_similarity(
            score(10.0, profit_factor=2.0),
            score(10.0, profit_factor=1.0),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertFalse(result["accepted"])
        self.assertIn("profit_factor", result["reasons"])

    def test_robust_pending_retry_includes_diagnostic_statuses(self) -> None:
        for status in ("", None, "no_report", "parse_error", "report_mismatch", "no_trades"):
            self.assertTrue(robust_status_pending_for_retry(status))
        for status in ("accepted", "rejected"):
            self.assertFalse(robust_status_pending_for_retry(status))


if __name__ == "__main__":
    unittest.main()
