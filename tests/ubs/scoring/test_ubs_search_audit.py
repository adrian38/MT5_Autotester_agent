import unittest

from ui.ubs_search_logic import audit_nonfinal_count


class AuditNonfinalCountTests(unittest.TestCase):
    def test_treats_only_pass_fail_as_final(self) -> None:
        counts = {
            "accepted": 22,
            "rejected": 146,
            "no_report": 62,
            "report_mismatch": 11,
        }

        self.assertEqual(audit_nonfinal_count(counts), 73)

    def test_includes_all_pending_and_diagnostic_states(self) -> None:
        counts = {
            "accepted": 10,
            "rejected": 3,
            "pending": 1,
            "pending_history_quality": 2,
            "pending_ohlc_trades": 4,
            "parse_error": 5,
            "no_trades": 6,
        }

        self.assertEqual(audit_nonfinal_count(counts), 18)

    def test_short_ohlc_pending_can_be_a_valid_6m_handoff(self) -> None:
        counts = {
            "accepted": 173,
            "pending_ohlc_trades": 65,
            "rejected": 508,
        }

        self.assertEqual(
            audit_nonfinal_count(
                counts,
                additional_final_statuses={"pending_ohlc_trades"},
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
