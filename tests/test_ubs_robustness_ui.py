import unittest

from ui.ubs_robustness_logic import UBSRobustnessLogicMixin


class UBSRobustnessReasonTests(unittest.TestCase):
    def test_invalid_stops_reason_is_shown_for_rejected_zero_trade_result(self) -> None:
        view = object.__new__(UBSRobustnessLogicMixin)

        reason = view._ubs_robust_reason(
            "rejected",
            {"trades": 0, "reasons": ["trades"]},
            {"failure_type": "invalid_stops", "invalid_order_count": 5},
        )

        self.assertEqual(
            reason,
            "5 orden(es) rechazada(s) por stops invalidos; no pasa robustez",
        )


if __name__ == "__main__":
    unittest.main()
