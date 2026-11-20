import unittest

from ui.ubs_final_tick_logic import UBSFinalTickLogicMixin


class FinalTickReasonHarness(UBSFinalTickLogicMixin):
    @staticmethod
    def _format_ubs_number(value: object) -> str:
        if value is None:
            return ""
        return f"{float(value):g}"

    @staticmethod
    def _format_ubs_int(value: object) -> str:
        return "" if value is None else str(int(float(value)))


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

    def test_empty_ohlc_context_is_explained_as_retryable(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "no_report",
            {"reasons": ["empty_ohlc_context"], "technical_failure": True},
        )

        self.assertEqual(reason, "histórico OHLC no disponible; reintento pendiente")

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

    def test_economic_direction_names_the_divergent_components(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "rejected",
            {
                "reasons": ["economic_direction"],
                "checks": {
                    "economic_direction": {
                        "components": {
                            "score": {"matches": False},
                            "net_profit": {"matches": True},
                            "profit_factor": {"matches": False},
                        }
                    }
                },
            },
        )

        self.assertEqual(reason, "direccion economica distinta: score, PF")

    def test_pending_economic_sample_names_counts_and_long_window(self) -> None:
        reason = self.logic._ubs_final_tick_reason(
            "pending_economic_sample",
            {
                "checks": {
                    "economic_sample": {
                        "ohlc_trades": 11,
                        "real_tick_trades": 12,
                        "min_trades": 50,
                        "retry_from": "2024.07.01",
                        "retry_to": "2026.06.30",
                    }
                }
            },
        )

        self.assertEqual(
            reason,
            "muestra economica pendiente: OHLC 11 / tick 12 < 50; "
            "ampliar 2024.07.01 -> 2026.06.30",
        )


if __name__ == "__main__":
    unittest.main()
