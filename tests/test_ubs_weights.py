import json
import unittest

from ubs.weights import (
    ASSET_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_REJECTED_PENALTY,
    FINAL_TICK_REASON_PENALTIES,
    NO_TRADES_WEIGHT,
    feedback_weight,
    percentile_multipliers,
    probability_feedback_signals,
    row_stage_outcome,
)


class UBSWeightsTests(unittest.TestCase):
    def test_pending_ohlc_trades_is_probe_success_but_technical_errors_are_not_trials(self) -> None:
        self.assertEqual(row_stage_outcome({"final_tick_status": "pending_ohlc_trades"}, "probe"), (True, 1.0))
        self.assertEqual(row_stage_outcome({"final_tick_status": "parse_error"}, "probe"), (False, 0.0))

    def test_probability_feedback_is_relative_and_smoothed(self) -> None:
        good = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "accepted",
            "final_tick_6m_status": "accepted",
        }
        bad = {
            "status": "rejected",
            "robust_status": "",
            "final_tick_status": "",
            "final_tick_6m_status": "",
        }
        global_groups = {("good",): [good], ("bad",): [bad]}
        signals = probability_feedback_signals(
            {
                "GOOD": {("good",): [good]},
                "BAD": {("bad",): [bad]},
            },
            global_groups,
            prior_strength=2.0,
        )
        self.assertGreater(signals["GOOD"].score, 0.0)
        self.assertLess(signals["BAD"].score, 0.0)
        self.assertGreater(signals["GOOD"].probability, signals["BAD"].probability)

    def test_probe_rejected_without_six_month_acceptance_is_terminal_negative_weight(self) -> None:
        terminal = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "rejected",
            "final_tick_6m_status": "",
        }
        good = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "accepted",
            "final_tick_6m_status": "accepted",
        }
        bad = {
            "status": "rejected",
            "robust_status": "",
            "final_tick_status": "",
            "final_tick_6m_status": "",
        }
        signals = probability_feedback_signals(
            {
                "TERMINAL": {("terminal",): [terminal]},
                "GOOD": {("good",): [good]},
                "BAD": {("bad",): [bad]},
            },
            {("terminal",): [terminal], ("good",): [good], ("bad",): [bad]},
            prior_strength=20.0,
        )

        self.assertLess(signals["TERMINAL"].score, 0.0)
        self.assertEqual(signals["TERMINAL"].probability, 0.0)
        self.assertGreater(signals["GOOD"].score, signals["TERMINAL"].score)

    def test_percentile_mutation_multipliers_preserve_order_and_ties(self) -> None:
        ordered = percentile_multipliers({"a": -100.0, "b": -50.0, "c": 10.0}, ("a", "b", "c", "new"))
        self.assertEqual(ordered["a"], 0.5)
        self.assertEqual(ordered["b"], 1.0)
        self.assertEqual(ordered["c"], 1.5)
        self.assertEqual(ordered["new"], 1.0)
        tied = percentile_multipliers({"a": -40.0, "b": -40.0}, ("a", "b"))
        self.assertEqual(tied, {"a": 1.0, "b": 1.0})

    def test_short_final_tick_accepted_is_neutral_until_six_month_passes(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "accepted",
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            100.0 + ASSET_ACCEPTED_BONUS,
        )

    def test_final_tick_6m_accepted_adds_live_signal_bonus(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "accepted",
            "final_tick_6m_status": "accepted",
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            100.0 + ASSET_ACCEPTED_BONUS + DEFAULT_FINAL_TICK_ACCEPTED_BONUS,
        )

    def test_regression_points_are_added_after_six_month_acceptance(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_6m_status": "accepted",
            "regression_status": "accepted",
            "regression_points_applied": 80.0,
        }
        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            100.0 + ASSET_ACCEPTED_BONUS + DEFAULT_FINAL_TICK_ACCEPTED_BONUS + 80.0,
        )

    def test_regression_failure_caps_previous_positive_weight(self) -> None:
        row = {
            "status": "accepted",
            "score": 400.0,
            "final_tick_6m_status": "accepted",
            "regression_status": "rejected",
            "regression_points_applied": -145.0,
        }
        self.assertEqual(feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS), -145.0)

    def test_regression_technical_status_is_neutral(self) -> None:
        base = {"status": "accepted", "score": 100.0, "final_tick_6m_status": "accepted"}
        row = {**base, "regression_status": "no_history", "regression_points_applied": -100.0}
        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            feedback_weight(base, accepted_bonus=ASSET_ACCEPTED_BONUS),
        )
        self.assertEqual(row_stage_outcome(row, "regression"), (False, 0.0))
        self.assertEqual(row_stage_outcome({"regression_status": "accepted"}, "regression"), (True, 1.0))
        self.assertEqual(row_stage_outcome({"regression_status": "no_trades"}, "regression"), (True, 0.0))

    def test_final_tick_rejected_adds_live_signal_penalty_and_reason_penalties(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "rejected",
            "final_tick_similarity_json": json.dumps({"reasons": ["profit_factor", "drawdown_pct"]}),
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            DEFAULT_FINAL_TICK_REJECTED_PENALTY
            - FINAL_TICK_REASON_PENALTIES["profit_factor"]
            - FINAL_TICK_REASON_PENALTIES["drawdown_pct"],
        )

    def test_final_tick_pending_and_no_trades_statuses_are_neutral(self) -> None:
        base = {
            "status": "accepted",
            "score": 100.0,
        }
        expected = feedback_weight(base, accepted_bonus=ASSET_ACCEPTED_BONUS)

        for status in (
            "",
            "no_trades",
            "pending_ohlc_trades",
            "pending_history_quality",
            "no_report",
            "parse_error",
            "report_mismatch",
        ):
            with self.subTest(status=status):
                row = {
                    "status": "accepted",
                    "score": 100.0,
                    "final_tick_status": status,
                    "final_tick_similarity_json": json.dumps({"reasons": ["profit_factor", "drawdown_pct"]}),
                }
                self.assertEqual(feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS), expected)

    def test_final_tick_6m_rejected_adds_live_signal_penalty_and_reason_penalties(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "accepted",
            "final_tick_6m_status": "rejected",
            "final_tick_6m_similarity_json": json.dumps({"reasons": ["profit_factor", "drawdown_pct"]}),
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            DEFAULT_FINAL_TICK_REJECTED_PENALTY
            - FINAL_TICK_REASON_PENALTIES["profit_factor"]
            - FINAL_TICK_REASON_PENALTIES["drawdown_pct"],
        )

    def test_final_tick_6m_rejected_by_ohlc_zero_trades_has_strong_reason_penalty(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "accepted",
            "final_tick_6m_status": "rejected",
            "final_tick_6m_similarity_json": json.dumps({"reasons": ["ohlc_trades"]}),
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            DEFAULT_FINAL_TICK_REJECTED_PENALTY - FINAL_TICK_REASON_PENALTIES["ohlc_trades"],
        )

    def test_final_tick_6m_rejected_caps_high_prior_weight(self) -> None:
        row = {
            "status": "accepted",
            "score": 400.0,
            "robust_status": "accepted",
            "robust_positive_bonus": 70.0,
            "final_tick_status": "accepted",
            "final_tick_6m_status": "rejected",
            "final_tick_6m_similarity_json": json.dumps({"reasons": ["profit_factor"]}),
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            DEFAULT_FINAL_TICK_REJECTED_PENALTY - FINAL_TICK_REASON_PENALTIES["profit_factor"],
        )

    def test_no_trades_with_report_contributes_fixed_penalty(self) -> None:
        row = {
            "status": "no_trades",
            "score": -55.0,
            "report_path": "C:/reports/some_report.htm",
        }
        self.assertEqual(feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS), NO_TRADES_WEIGHT)

    def test_no_trades_without_report_does_not_contribute(self) -> None:
        for report_path in (None, "", "   "):
            with self.subTest(report_path=report_path):
                row = {
                    "status": "no_trades",
                    "score": -55.0,
                    "report_path": report_path,
                }
                self.assertIsNone(feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS))

    def test_non_accepted_or_rejected_statuses_do_not_contribute_to_weights(self) -> None:
        for status in (
            "",
            "generated",
            "pending",
            "no_report",
            "parse_error",
            "report_mismatch",
            "pending_history_quality",
            "pending_ohlc_trades",
        ):
            with self.subTest(status=status):
                row = {
                    "status": status,
                    "score": 100.0,
                    "final_tick_status": "accepted",
                    "robust_status": "accepted",
                }
                self.assertIsNone(feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS))


if __name__ == "__main__":
    unittest.main()
