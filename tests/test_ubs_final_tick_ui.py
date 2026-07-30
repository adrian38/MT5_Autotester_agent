import unittest

from ui.ubs_final_tick_logic import UBSFinalTickLogicMixin


class FinalTickReasonHarness(UBSFinalTickLogicMixin):
    @staticmethod
    def _format_ubs_number(value: object) -> str:
        if value is None:
            return ""
        return f"{float(value):g}"


class UBSFinalTickReasonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logic = FinalTickReasonHarness()

    def test_pending_low_quality_uses_database_fallback_values(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "pending_history_quality",
            {},
            history_quality=33.0,
            min_history_quality=80.0,
        )

        self.assertEqual(reason, "calidad pendiente: 33% < 80%")

    def test_pending_empty_context_does_not_claim_high_quality_is_below_minimum(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "pending_history_quality",
            {
                "reasons": ["empty_tester_context"],
                "history_quality": 99.0,
                "min_history_quality": 80.0,
            },
        )

        self.assertEqual(reason, "contexto tester pendiente (calidad reportada 99%)")

    def test_pending_tick_download_failure_is_explained_as_retryable(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "pending_history_quality",
            {
                "reasons": ["real_tick_no_history"],
                "history_quality": 99.0,
                "min_history_quality": 80.0,
            },
        )

        self.assertEqual(reason, "descarga Real Tick interrumpida; reintento pendiente")

    def test_legacy_high_quality_pending_row_has_context_explanation(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "pending_history_quality",
            {},
            history_quality=99.0,
            min_history_quality=80.0,
        )

        self.assertEqual(
            reason,
            "historico/contexto pendiente: calidad 99% (minimo 80%)",
        )


if __name__ == "__main__":
    unittest.main()
