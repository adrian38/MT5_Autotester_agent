import unittest

from ai_copilot.manual import build_manual_index_from_text, select_manual_context


class AICopilotManualTests(unittest.TestCase):
    def test_builds_compact_key_index_from_text(self) -> None:
        index = build_manual_index_from_text(
            "MaxSpread controls the maximum allowed spread before opening a trade. "
            "Exit_stop defines the stop loss size.",
            param_names=("MaxSpread", "Exit_stop", "MissingKey"),
        )

        self.assertIn("MaxSpread", index["entries"])
        self.assertEqual(index["entries"]["MaxSpread"]["pages"], [1])
        self.assertNotIn("MissingKey", index["entries"])

    def test_selects_manual_context_with_evidence_ids(self) -> None:
        index = build_manual_index_from_text("MaxSpread controls spread.", param_names=("MaxSpread",))

        context = select_manual_context(index, ("MaxSpread",), max_keys=1)

        self.assertEqual(context[0]["id"], "manual:key:MaxSpread")
        self.assertIn("MaxSpread", context[0]["summary"])


if __name__ == "__main__":
    unittest.main()
