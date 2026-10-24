import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ubs_generate_sets
import ubs_set_rules
from ubs.set_rules import (
    ADJUST_LOTSIZE_KEY,
    ADJUST_LOTSIZE_RULE_VALUE,
    NFP_RULE_KEYS,
    ONLY_UP_KEY,
    ONLY_UP_RULE_VALUE,
    apply_nfp_rules_file,
    apply_nfp_rules_text,
    apply_set_rules_text,
    nfp_rule_violations,
    normalize_generated_set_text,
    set_line_values,
)
from ubs_agent import is_agent_mutable_key
from ubs_agent_sets import repair_seed_backtest_set


def set_text(run_strategy: str, enable_nfp: str, **overrides: str) -> str:
    values = {
        "Run_Strategy": f"{run_strategy}||1||0||2||N",
        "ST1_Timeframe": "16385||0||0||49153||N",
        "Exit_stop": "100||10||5||200||Y",
        "EnableNFP_Filter": f"{enable_nfp}||false||0||true||N",
        "NFP_CloseOpenTrades": "true||false||0||true||N",
        "NFP_ClosePendingOrders": "true||false||0||true||N",
        "NFP_MinutesBefore": "50||0||0||300||N",
        "NFP_MinutesAfter": "30||0||0||300||N",
    }
    values.update(overrides)
    return "; cabecera\n" + "\n".join(f"{key}={value}" for key, value in values.items())


class NfpRuleTests(unittest.TestCase):
    def test_breakout_support_resistance_forces_the_filter_on(self) -> None:
        result = apply_nfp_rules_text(set_text("1", "false"))

        self.assertEqual(result.changed, ("EnableNFP_Filter",))
        self.assertEqual(set_line_values(result.text.splitlines())["EnableNFP_Filter"], "true")

    def test_volatility_breakout_forces_the_filter_off(self) -> None:
        result = apply_nfp_rules_text(set_text("2", "true"))

        self.assertEqual(result.changed, ("EnableNFP_Filter",))
        self.assertEqual(set_line_values(result.text.splitlines())["EnableNFP_Filter"], "false")

    def test_range_strategy_forces_the_filter_off(self) -> None:
        result = apply_nfp_rules_text(
            set_text("3", "true", ST1_Timeframe="0||0||0||49153||N", RNG_Timeframe="16385||0||0||49153||N")
        )

        self.assertEqual(set_line_values(result.text.splitlines())["EnableNFP_Filter"], "false")

    def test_a_compliant_set_is_returned_untouched(self) -> None:
        source = set_text("1", "true")

        result = apply_nfp_rules_text(source)

        self.assertEqual(result.changed, ())
        self.assertEqual(result.text, source)

    def test_the_four_behaviour_keys_are_constant_in_every_strategy(self) -> None:
        source = set_text(
            "2", "false",
            NFP_CloseOpenTrades="false||false||0||true||N",
            NFP_MinutesBefore="10||0||0||300||N",
        )

        values = set_line_values(apply_nfp_rules_text(source).text.splitlines())

        self.assertEqual(values["NFP_CloseOpenTrades"], "true")
        self.assertEqual(values["NFP_ClosePendingOrders"], "true")
        self.assertEqual(values["NFP_MinutesBefore"], "50")
        self.assertEqual(values["NFP_MinutesAfter"], "30")

    def test_the_optimizer_metadata_and_the_rest_of_the_set_survive(self) -> None:
        result = apply_nfp_rules_text(set_text("1", "false"))

        self.assertIn("EnableNFP_Filter=true||false||0||true||N", result.text)
        self.assertIn("Exit_stop=100||10||5||200||Y", result.text)

    def test_a_missing_run_strategy_is_inferred_from_the_active_strategy(self) -> None:
        source = set_text("1", "false").replace("Run_Strategy=1||1||0||2||N\n", "")

        result = apply_nfp_rules_text(source)

        self.assertEqual(result.run_strategy, "1")
        self.assertEqual(set_line_values(result.text.splitlines())["EnableNFP_Filter"], "true")

    def test_an_undecidable_strategy_leaves_the_filter_alone(self) -> None:
        source = set_text("", "false", Run_Strategy="0||1||0||2||N", VolTimeframe="16385||0||0||49153||N")

        result = apply_nfp_rules_text(source)

        self.assertEqual(result.run_strategy, "")
        self.assertEqual(set_line_values(result.text.splitlines())["EnableNFP_Filter"], "false")

    def test_a_set_without_the_nfp_block_gets_no_invented_keys(self) -> None:
        source = "; cabecera\nRun_Strategy=1||1||0||2||N\nExit_stop=100||10||5||200||Y"

        result = apply_nfp_rules_text(source)

        self.assertEqual(result.text, source)
        self.assertEqual(result.absent, NFP_RULE_KEYS)

    def test_line_endings_and_plain_values_are_preserved(self) -> None:
        source = "; cabecera\r\nRun_Strategy=1\r\nEnableNFP_Filter=false\r\n"

        result = apply_nfp_rules_text(source)

        self.assertEqual(result.text, "; cabecera\r\nRun_Strategy=1\r\nEnableNFP_Filter=true\r\n")

    def test_violations_report_the_value_the_rule_demands(self) -> None:
        values = set_line_values(set_text("2", "true").splitlines())

        self.assertEqual(nfp_rule_violations(values), {"EnableNFP_Filter": "false"})


class NfpRuleIntegrationTests(unittest.TestCase):
    def test_all_rules_force_variable_lots_off_and_not_optimizable(self) -> None:
        source = set_text("1", "true") + "\nAdjustLotsizeToVariableValues=true||true||1||true||Y"

        result = apply_set_rules_text(source)

        self.assertIn(ADJUST_LOTSIZE_KEY, result.changed)
        self.assertIn(f"{ADJUST_LOTSIZE_KEY}={ADJUST_LOTSIZE_RULE_VALUE}", result.text)

    def test_a_compliant_variable_lot_rule_is_untouched(self) -> None:
        source = (
            set_text("1", "true")
            + f"\n{ADJUST_LOTSIZE_KEY}={ADJUST_LOTSIZE_RULE_VALUE}"
            + f"\n{ONLY_UP_KEY}={ONLY_UP_RULE_VALUE}"
        )

        result = apply_set_rules_text(source)

        self.assertNotIn(ADJUST_LOTSIZE_KEY, result.changed)
        self.assertEqual(result.text, source)

    def test_a_missing_variable_lot_parameter_is_added(self) -> None:
        source = set_text("1", "true")

        result = apply_set_rules_text(source)

        self.assertIn(ADJUST_LOTSIZE_KEY, result.absent)
        self.assertIn(ADJUST_LOTSIZE_KEY, result.changed)
        self.assertIn(f"{ADJUST_LOTSIZE_KEY}={ADJUST_LOTSIZE_RULE_VALUE}", result.text)

    def test_all_rules_force_only_up_off_and_not_optimizable(self) -> None:
        source = set_text("1", "true") + "\nOnlyUp=true||false||0||true||Y"

        result = apply_set_rules_text(source)

        self.assertIn(ONLY_UP_KEY, result.changed)
        self.assertIn(f"{ONLY_UP_KEY}={ONLY_UP_RULE_VALUE}", result.text)

    def test_a_compliant_only_up_rule_is_untouched(self) -> None:
        source = (
            set_text("1", "true")
            + f"\n{ADJUST_LOTSIZE_KEY}={ADJUST_LOTSIZE_RULE_VALUE}"
            + f"\n{ONLY_UP_KEY}={ONLY_UP_RULE_VALUE}"
        )

        result = apply_set_rules_text(source)

        self.assertNotIn(ONLY_UP_KEY, result.changed)
        self.assertEqual(result.text, source)

    def test_a_missing_only_up_parameter_is_added(self) -> None:
        source = set_text("1", "true")

        result = apply_set_rules_text(source)

        self.assertIn(ONLY_UP_KEY, result.absent)
        self.assertIn(ONLY_UP_KEY, result.changed)
        self.assertIn(f"{ONLY_UP_KEY}={ONLY_UP_RULE_VALUE}", result.text)

    def test_bulk_sweep_rewrites_variable_lots_and_optimizer_flag(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "existing.set"
            path.write_text(
                set_text("1", "true")
                + "\nAdjustLotsizeToVariableValues=true||true||1||true||Y",
                encoding="utf-8",
            )
            tally = ubs_set_rules.RuleSweepTally()

            ubs_set_rules.process_set_file(root, path, tally, None, False)

            self.assertEqual(tally.rewritten, 1)
            self.assertEqual(tally.by_key[ADJUST_LOTSIZE_KEY], 1)
            self.assertIn(
                f"{ADJUST_LOTSIZE_KEY}={ADJUST_LOTSIZE_RULE_VALUE}",
                path.read_text(encoding="utf-8"),
            )

    def test_the_generation_normalizer_applies_the_rule(self) -> None:
        normalized, _found, _missing = normalize_generated_set_text(set_text("2", "true"))

        self.assertEqual(set_line_values(normalized.splitlines())["EnableNFP_Filter"], "false")

    def test_the_file_helper_only_rewrites_what_breaks_the_rule(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "seed.set"
            path.write_text(set_text("1", "true"), encoding="utf-8")
            before = path.stat().st_mtime_ns

            result = apply_nfp_rules_file(path)

            self.assertEqual(result.changed, ())
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_repairing_a_seed_set_restores_the_filter(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "seed.set"
            path.write_text(set_text("1", "false") + "\nForceSymbol=EURUSD", encoding="utf-8")

            result = repair_seed_backtest_set(path, "EURUSD", "H1")

            self.assertIn("EnableNFP_Filter", result["changed"])
            self.assertIn("EnableNFP_Filter=true", path.read_text(encoding="utf-8"))

    def test_the_rule_keys_cannot_be_made_agent_mutable(self) -> None:
        rule_keys = (*NFP_RULE_KEYS, ADJUST_LOTSIZE_KEY, ONLY_UP_KEY)
        with patch("ubs_agent_config.load_mutation_overrides", return_value=({}, set(rule_keys))):
            for key in rule_keys:
                self.assertFalse(is_agent_mutable_key(key), key)

    def test_the_legacy_generator_also_freezes_the_rule_keys(self) -> None:
        for key in (*NFP_RULE_KEYS, ADJUST_LOTSIZE_KEY, ONLY_UP_KEY):
            self.assertFalse(ubs_generate_sets.is_mutable_key(key), key)


if __name__ == "__main__":
    unittest.main()
