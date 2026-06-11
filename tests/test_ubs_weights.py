import json
import unittest

from ubs.weights import (
    ASSET_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_REJECTED_PENALTY,
    FINAL_TICK_REASON_PENALTIES,
    NO_TRADES_WEIGHT,
    feedback_weight,
)


class UBSWeightsTests(unittest.TestCase):
    def test_final_tick_accepted_adds_live_signal_bonus(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "accepted",
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            100.0 + ASSET_ACCEPTED_BONUS + DEFAULT_FINAL_TICK_ACCEPTED_BONUS,
        )

    def test_final_tick_rejected_adds_live_signal_penalty_and_reason_penalties(self) -> None:
        row = {
            "status": "accepted",
            "score": 100.0,
            "final_tick_status": "rejected",
            "final_tick_similarity_json": json.dumps({"reasons": ["profit_factor", "drawdown_pct"]}),
        }

        self.assertEqual(
            feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS),
            100.0
            + ASSET_ACCEPTED_BONUS
            + DEFAULT_FINAL_TICK_REJECTED_PENALTY
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
