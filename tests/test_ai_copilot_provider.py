import json
import unittest
from unittest import mock

from ai_copilot.providers.openai_provider import call_openai


class FakeResponse:
    def __init__(self, data: dict) -> None:
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.data).encode("utf-8")


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


class AICopilotProviderTests(unittest.TestCase):
    def test_posts_responses_api_and_parses_report(self) -> None:
        report = valid_report()
        response = {
            "output": [{"content": [{"text": json.dumps(report)}]}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        with mock.patch("ai_copilot.providers.openai_provider.request.urlopen", return_value=FakeResponse(response)) as urlopen:
            parsed, raw = call_openai(
                {"hello": "world"},
                model="gpt-test",
                api_key="sk-test",
                base_url="https://example.test",
                allowed_evidence_ids={"sql:test"},
                timeout=3,
            )

        self.assertEqual(parsed["usage"]["provider"], "openai")
        self.assertEqual(raw["usage"]["input_tokens"], 10)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://example.test/v1/responses")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
