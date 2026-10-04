import random
import tempfile
import unittest
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.account import account_disabled_symbols_path
from ubs.universe import (
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
    save_disabled_symbols,
    seed_symbol_disabled,
)
from ubs_agent import (
    choose_target_symbol,
    copy_accepted,
    recreate_work_dir,
    target_symbol_disabled,
    repair_seed_backtest_set,
    validate_seed_backtest_set,
    variant_as_next_seed,
    write_set_force_symbol,
)
from run_tests import infer_period_from_set, load_set_params, parse_symbol_map
from tests.ubs_agent_files_fixtures import score


class UBSAgentSeedTests(unittest.TestCase):
    def test_crudeoil_seed_is_disabled_when_wti_is_disabled(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertTrue(seed_symbol_disabled(seed, {"WTI"}, symbol_map))

    def test_seed_enabled_symbol_allows_disabled_seed(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertFalse(seed_symbol_disabled(seed, {"WTI"}, symbol_map, {"WTI"}))

    def test_axi_suffix_policy_blocks_normalized_seed_unless_seeds_enabled(self) -> None:
        seed = Seed(Path("Gold.set"), "XAUUSD", "H1", "GOLD", "1")
        symbol_map = parse_symbol_map("XAUUSD=XAUUSD.sa")

        self.assertTrue(seed_symbol_disabled(seed, {"XAUUSD.SA"}, symbol_map))
        self.assertFalse(
            seed_symbol_disabled(seed, {"XAUUSD.SA"}, symbol_map, {"XAUUSD.SA"})
        )

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

    def test_alias_seed_exploit_returns_canonical_universe_symbol(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=XTIUSD")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XTIUSD", "XBRUSD"),
            {"CRUDEOIL": "XTIUSD"},
            symbol_map=symbol_map,
            disabled_symbols=set(),
        )

        self.assertEqual(target, "XTIUSD")
        self.assertEqual(policy, "exploit")

    def test_axi_cash_seed_can_exploit_enabled_future_equivalent(self) -> None:
        seed = Seed(Path("DAX_M15_a.set"), "DAX", "M15", "family", "1")
        symbol_map = parse_symbol_map("DAX=GER40,DE40=GER40")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("GER40.sa", "DAX40.fs"),
            {},
            symbol_map=symbol_map,
            disabled_symbols={"GER40.SA"},
        )

        self.assertEqual(target, "DAX40.fs")
        self.assertEqual(policy, "exploit")

    def test_target_disabled_without_policy_does_not_read_default_account_file(self) -> None:
        self.assertFalse(target_symbol_disabled("WTI", ("WTI",), {}, disabled_symbols=None))

    def test_axi_suffix_policy_blocks_normalized_generation_target(self) -> None:
        symbol_map = parse_symbol_map("XAUUSD=XAUUSD.sa")

        self.assertTrue(
            target_symbol_disabled(
                "XAUUSD",
                (),
                {},
                symbol_map=symbol_map,
                disabled_symbols={"XAUUSD.SA"},
            )
        )

    def test_disabled_symbols_policy_is_account_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            self.assertEqual(
                account_disabled_symbols_path(base_dir, "ECN"),
                base_dir / "outputs" / "ubs_disabled_symbols_ROBOFOREX_ECN.json",
            )
            self.assertEqual(
                account_disabled_symbols_path(base_dir, "PRO"),
                base_dir / "outputs" / "ubs_disabled_symbols_ROBOFOREX_PRO.json",
            )
            self.assertEqual(
                account_disabled_symbols_path(base_dir, "PREMIUM", "AXI"),
                base_dir / "outputs" / "ubs_disabled_symbols_AXI_PREMIUM.json",
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
            self.assertTrue(
                any(issue.startswith("Entry_Timing=60 fuera del universo soportado") for issue in issues),
                issues,
            )

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

    def test_seed_validation_accepts_range_strategy_and_uses_rng_timeframe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DE40_range.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=DE40",
                        "Run_Strategy=3||1||0||3||N",
                        "RNG_Timeframe=16385||0||0||49153||N",
                        "RNG_ATR_Timeframe=16385||0||0||49153||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            params = load_set_params(path)
            seed = Seed(path, "DE40", "H1", "family", "3")

            self.assertEqual(infer_period_from_set(path, params), "H1")
            self.assertEqual(validate_seed_backtest_set(seed), [])

    def test_repair_seed_backtest_set_sets_range_strategy_timeframe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DE40_range.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=DE40",
                        "Run_Strategy=3||1||0||3||N",
                        "RNG_Timeframe=0||0||0||49153||N",
                        "RNG_ATR_Timeframe=0||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "DE40", "H1")

            self.assertIn("RNG_Timeframe", result["changed"])
            self.assertIn("RNG_ATR_Timeframe", result["changed"])
            self.assertIn("RNG_Timeframe=16385||0||0||49153||N", path.read_text(encoding="utf-8"))
            self.assertIn("RNG_ATR_Timeframe=16385||0||0||49153||N", path.read_text(encoding="utf-8"))

    def test_write_set_force_symbol_adds_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seed.set"
            path.write_text("Run_Strategy=1||1||0||2||N\nST1_Timeframe=16385||0||0||49153||N", encoding="utf-8")

            write_set_force_symbol(path, path, "XAUUSD")

            self.assertIn("ForceSymbol=XAUUSD", path.read_text(encoding="utf-8"))
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_repair_seed_backtest_set_fixes_bitcoin_reaper_st1_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "BTC_H1__Bitcoin_Reaper__updated__k.set"
            path.write_text(
                "\n".join(
                    [
                        "ATR_Timeframe=16408||0||0||49153||N",
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=16385||0||0||49153||N",
                        "EA_Comment=Ultimate Breakout System_k",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "BTCUSD", "H1")
            text = path.read_text(encoding="utf-8")

            self.assertEqual(result["run_strategy"], "1")
            self.assertIn("ForceSymbol=BTCUSD", text)
            self.assertIn("Run_Strategy=1||1||0||2||N", text)
            self.assertIn("ST1_Timeframe=16385||0||0||49153||N", text)
            self.assertEqual(validate_seed_backtest_set(Seed(path, "BTCUSD", "H1", "family", "1")), [])

    def test_repair_seed_backtest_set_uses_volatility_strategy_when_vol_key_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Volatility_Breakout__BTCUSD__H1_A.set"
            path.write_text(
                "\n".join(
                    [
                        "ATR_Timeframe=16408||0||0||49153||N",
                        "VolTimeframe=16385||0||0||49153||N",
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=0||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "BTCUSD", "H1")
            text = path.read_text(encoding="utf-8")

            self.assertEqual(result["run_strategy"], "2")
            self.assertIn("ForceSymbol=BTCUSD", text)
            self.assertIn("Run_Strategy=2||1||0||2||N", text)
            self.assertEqual(validate_seed_backtest_set(Seed(path, "BTCUSD", "H1", "family", "2")), [])

    def test_repair_seed_backtest_set_converts_legacy_timeframe_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Old_Optimizations_H1__MT4A.set"
            path.write_text(
                "\n".join(
                    [
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=60||0||0||49153||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "XAUUSD", "H1")
            text = path.read_text(encoding="utf-8")

            self.assertIn("Entry_Timing=16385||0||0||49153||N", text)
            self.assertEqual(result["run_strategy"], "1")
            self.assertEqual(validate_seed_backtest_set(Seed(path, "XAUUSD", "H1", "family", "1")), [])

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
