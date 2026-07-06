import unittest

from ai_copilot.redaction import redact_payload
from ai_copilot.schema import validate_report


def valid_report() -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-07-06T12:00:00",
        "broker": "ROBOFOREX",
        "account_type": "ECN",
        "run_id": 1,
        "summary": "ok",
        "confidence": 0.5,
        "evidence": [{"id": "sql:test", "source": "sqlite", "title": "T", "value": "V", "rows": []}],
        "findings": [{"severity": "info", "claim": "c", "evidence_ids": ["sql:test"], "affected_count": 1}],
        "recommendations": [
            {
                "action_type": "review",
                "title": "r",
                "rationale": "because",
                "expected_effect": "effect",
                "risk": "low",
                "requires_approval": True,
                "cli_preview": "",
                "evidence_ids": ["sql:test"],
            }
        ],
        "usage": {"provider": "local", "model": "deterministic", "input_tokens": None, "output_tokens": None, "estimated_cost_usd": None},
    }


class AICopilotSchemaTests(unittest.TestCase):
    def test_rejects_unknown_evidence_id(self) -> None:
        report = valid_report()
        report["findings"][0]["evidence_ids"] = ["sql:missing"]

        with self.assertRaises(ValueError):
            validate_report(report)

    def test_rejects_auto_approval(self) -> None:
        report = valid_report()
        report["recommendations"][0]["requires_approval"] = False

        with self.assertRaises(ValueError):
            validate_report(report)

    def test_redacts_paths_and_set_content(self) -> None:
        payload = {
            "set_path": r"C:\Users\Adrian\private\strategy.set",
            "notes": "A=1\nB=2\nC=3\nD=4\nE=5\nF=6",
            "manual": {"raw_text": "full manual"},
        }

        redacted = redact_payload(payload)

        self.assertEqual(redacted["set_path"], "strategy.set")
        self.assertNotIn(r"C:\Users", str(redacted))
        self.assertNotIn("full manual", str(redacted))
        self.assertIn("[REDACTED_SET_CONTENT]", str(redacted))


if __name__ == "__main__":
    unittest.main()
