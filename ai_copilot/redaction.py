from __future__ import annotations

import copy
import re
from typing import Any


WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
LONG_SET_RE = re.compile(r"(?:^|\n)\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^\n]+(?:\n\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^\n]+){4,}")


def build_api_payload(
    snapshot: dict[str, Any],
    local_report: dict[str, Any],
    manual_context: list[dict[str, Any]],
    *,
    max_evidence_rows: int = 25,
) -> dict[str, Any]:
    payload = {
        "task": "Diagnose the UBS run and return the same JSON contract.",
        "snapshot": _compact_snapshot(snapshot),
        "deterministic_report": local_report,
        "manual_context": manual_context,
    }
    payload = redact_payload(payload)
    for evidence in payload.get("deterministic_report", {}).get("evidence", []):
        if isinstance(evidence, dict) and isinstance(evidence.get("rows"), list):
            evidence["rows"] = evidence["rows"][:max_evidence_rows]
    return payload


def redact_payload(value: Any) -> Any:
    data = copy.deepcopy(value)
    return _redact(data)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {"memory_path", "set_path", "seed_path", "report_path", "source_path"}:
                redacted[key_text] = _basename(str(item or ""))
            elif key_text in {"raw_text", "pdf_text", "set_text"}:
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        text = WINDOWS_PATH_RE.sub(lambda match: _basename(match.group(0)), value)
        text = LONG_SET_RE.sub("\n[REDACTED_SET_CONTENT]", text)
        return text[:1200]
    return value


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "broker": snapshot.get("broker"),
        "account_type": snapshot.get("account_type"),
        "run_id": snapshot.get("run_id"),
        "counts": snapshot.get("counts"),
        "reasons": snapshot.get("reasons"),
        "concentration": snapshot.get("concentration"),
        "top_mutated_keys": snapshot.get("top_mutated_keys"),
        "samples": snapshot.get("samples"),
    }


def _basename(path_text: str) -> str:
    text = str(path_text or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if "/" in text else text
