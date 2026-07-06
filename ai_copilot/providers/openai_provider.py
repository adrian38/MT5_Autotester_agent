from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

from ai_copilot.schema import COPILOT_REPORT_JSON_SCHEMA, validate_report


class OpenAIProviderError(RuntimeError):
    pass


def call_openai(
    payload: dict[str, Any],
    *,
    model: str,
    reasoning_effort: str = "low",
    api_key: str | None = None,
    base_url: str | None = None,
    allowed_evidence_ids: set[str] | None = None,
    timeout: float = 60.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise OpenAIProviderError("OPENAI_API_KEY is required for --provider openai.")
    root = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are UBS Copiloto IA. Return only validated JSON. "
                    "Do not invent evidence IDs. Every recommendation must require approval."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "reasoning": {"effort": reasoning_effort},
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ubs_copilot_report",
                "schema": COPILOT_REPORT_JSON_SCHEMA,
                "strict": True,
            }
        },
    }
    raw = _post_json(f"{root}/v1/responses", body, key, timeout=timeout)
    report_text = _extract_output_text(raw)
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as exc:
        raise OpenAIProviderError(f"OpenAI returned invalid JSON: {exc}") from exc
    usage = raw.get("usage") if isinstance(raw, dict) else {}
    if isinstance(usage, dict):
        report.setdefault("usage", {})
        report["usage"].update(
            {
                "provider": "openai",
                "model": model,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "estimated_cost_usd": None,
            }
        )
    return validate_report(report, allowed_evidence_ids=allowed_evidence_ids), raw


def _post_json(url: str, body: dict[str, Any], api_key: str, *, timeout: float) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenAIProviderError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpenAIProviderError(f"OpenAI request failed: {exc}") from exc


def _extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return str(response["output_text"])
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return str(content["text"])
    raise OpenAIProviderError("OpenAI response did not contain output text.")
