from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_audit_bundle(
    out_dir: Path,
    *,
    report: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
    provider_response: dict[str, Any] | None = None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"run_{int(report.get('run_id') or 0)}_{str(report.get('generated_at') or '').replace(':', '').replace('-', '').replace('T', '_')}"
    paths = {
        "report": out_dir / f"{stem}_report.json",
        "markdown": out_dir / f"{stem}_summary.md",
    }
    paths["report"].write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["markdown"].write_text(_markdown_summary(report), encoding="utf-8")
    if request_payload is not None:
        paths["request"] = out_dir / f"{stem}_request_redacted.json"
        paths["request"].write_text(json.dumps(request_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if provider_response is not None:
        paths["response"] = out_dir / f"{stem}_provider_response.json"
        paths["response"].write_text(json.dumps(provider_response, indent=2, ensure_ascii=False), encoding="utf-8")
    return paths


def _markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# UBS Copiloto IA - Run {report.get('run_id')}",
        "",
        str(report.get("summary") or ""),
        "",
        "## Findings",
    ]
    for item in report.get("findings", []):
        lines.append(f"- [{item.get('severity')}] {item.get('claim')}")
    lines.extend(["", "## Recommendations"])
    for item in report.get("recommendations", []):
        lines.append(f"- {item.get('title')}: {item.get('rationale')}")
    lines.append("")
    return "\n".join(lines)
