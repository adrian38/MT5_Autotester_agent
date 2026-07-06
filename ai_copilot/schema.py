from __future__ import annotations

from datetime import datetime
from typing import Any


REPORT_SCHEMA_VERSION = "1.0"

SEVERITIES = {"info", "warning", "critical"}
ACTION_TYPES = {
    "retry",
    "change_mode",
    "adjust_threshold",
    "freeze_param",
    "unfreeze_param",
    "run_robustness",
    "run_final_tick_6m",
    "review",
}
RISKS = {"low", "medium", "high"}

COPILOT_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "generated_at",
        "broker",
        "account_type",
        "run_id",
        "summary",
        "confidence",
        "evidence",
        "findings",
        "recommendations",
        "usage",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "generated_at": {"type": "string"},
        "broker": {"type": "string"},
        "account_type": {"type": "string"},
        "run_id": {"type": "integer"},
        "summary": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "source", "title", "value", "rows"],
                "properties": {
                    "id": {"type": "string"},
                    "source": {"type": "string"},
                    "title": {"type": "string"},
                    "value": {"type": "string"},
                    "rows": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "claim", "evidence_ids", "affected_count"],
                "properties": {
                    "severity": {"type": "string", "enum": sorted(SEVERITIES)},
                    "claim": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "affected_count": {"type": "integer", "minimum": 0},
                },
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action_type",
                    "title",
                    "rationale",
                    "expected_effect",
                    "risk",
                    "requires_approval",
                    "cli_preview",
                    "evidence_ids",
                ],
                "properties": {
                    "action_type": {"type": "string", "enum": sorted(ACTION_TYPES)},
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "expected_effect": {"type": "string"},
                    "risk": {"type": "string", "enum": sorted(RISKS)},
                    "requires_approval": {"type": "boolean", "const": True},
                    "cli_preview": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "usage": {
            "type": "object",
            "additionalProperties": True,
            "required": ["provider", "model", "input_tokens", "output_tokens", "estimated_cost_usd"],
            "properties": {
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "input_tokens": {"type": ["integer", "null"]},
                "output_tokens": {"type": ["integer", "null"]},
                "estimated_cost_usd": {"type": ["number", "null"]},
            },
        },
    },
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def evidence_ids(report: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id"))
        for item in report.get("evidence", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


def validate_report(report: dict[str, Any], *, allowed_evidence_ids: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("Copilot report must be a JSON object.")
    for field in COPILOT_REPORT_JSON_SCHEMA["required"]:
        if field not in report:
            raise ValueError(f"Missing required report field: {field}")
    confidence = report.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be a number in [0, 1].")
    known_ids = allowed_evidence_ids or evidence_ids(report)
    if not known_ids:
        raise ValueError("Report must include at least one evidence item.")
    for item in report.get("evidence", []):
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise ValueError("Every evidence item must have an id.")
        if not isinstance(item.get("rows"), list):
            raise ValueError(f"Evidence {item.get('id')} rows must be a list.")
    for finding in report.get("findings", []):
        _validate_claim(finding, known_ids, kind="finding")
    for recommendation in report.get("recommendations", []):
        _validate_claim(recommendation, known_ids, kind="recommendation")
        if recommendation.get("requires_approval") is not True:
            raise ValueError("Every recommendation must require approval.")
        action_type = str(recommendation.get("action_type") or "")
        if action_type not in ACTION_TYPES:
            raise ValueError(f"Invalid recommendation action_type: {action_type}")
        risk = str(recommendation.get("risk") or "")
        if risk not in RISKS:
            raise ValueError(f"Invalid recommendation risk: {risk}")
    return report


def _validate_claim(item: dict[str, Any], known_ids: set[str], *, kind: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"Invalid {kind}: expected object.")
    if kind == "finding":
        severity = str(item.get("severity") or "")
        if severity not in SEVERITIES:
            raise ValueError(f"Invalid finding severity: {severity}")
    ids = item.get("evidence_ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError(f"Every {kind} must include evidence_ids.")
    unknown = sorted(str(value) for value in ids if str(value) not in known_ids)
    if unknown:
        raise ValueError(f"{kind} references unknown evidence ids: {', '.join(unknown)}")
