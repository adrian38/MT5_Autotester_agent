from __future__ import annotations

from typing import Any

from .schema import REPORT_SCHEMA_VERSION, now_iso, validate_report


FINAL_STATUSES = {"accepted", "rejected"}


def build_local_report(
    snapshot: dict[str, Any],
    *,
    manual_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manual_context = manual_context or []
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    broker = str(snapshot.get("broker") or "")
    account_type = str(snapshot.get("account_type") or "")
    run_id = int(snapshot.get("run_id") or 0)
    counts = snapshot.get("counts") or {}
    reasons = snapshot.get("reasons") or {}
    concentration = snapshot.get("concentration") or {}

    base_status = dict(counts.get("base_status") or {})
    robust_status = dict(counts.get("robustness_status") or {})
    ft_status = dict(counts.get("final_tick_status") or {})
    ft6_status = dict(counts.get("final_tick_6m_status") or {})
    missing = dict(counts.get("missing") or {})
    stale = dict(counts.get("stale") or {})

    total = sum(int(value or 0) for value in base_status.values())
    accepted = int(base_status.get("accepted", 0) or 0)
    rejected = int(base_status.get("rejected", 0) or 0)
    no_trades = int(base_status.get("no_trades", 0) or 0)
    report_mismatch = int(base_status.get("report_mismatch", 0) or 0)
    no_report = int(base_status.get("no_report", 0) or 0)

    _add_evidence(
        evidence,
        "sql:candidates_status_by_stage",
        "sqlite",
        "Base candidate statuses",
        _fmt_counts(base_status),
        [{"status": key, "count": value} for key, value in sorted(base_status.items())],
    )
    _add_evidence(
        evidence,
        "sql:pipeline_missing_rows",
        "sqlite",
        "Missing downstream rows",
        _fmt_counts(missing),
        [{"stage": key, "missing": value} for key, value in sorted(missing.items())],
    )
    _add_evidence(
        evidence,
        "sql:pipeline_stale_rows",
        "sqlite",
        "Stale downstream rows",
        _fmt_counts(stale),
        [{"stage": key, "stale": value} for key, value in sorted(stale.items())],
    )
    _add_evidence(
        evidence,
        "sql:reason_counts",
        "sqlite",
        "Reason counts by stage",
        _reason_value(reasons),
        [{"stage": stage, "reason": reason, "count": count} for stage, values in reasons.items() for reason, count in dict(values).items()],
    )
    _add_evidence(
        evidence,
        "sql:symbol_timeframe_concentration",
        "sqlite",
        "Symbol and timeframe concentration",
        _concentration_value(concentration),
        [
            {"dimension": dimension, "key": row.get("key"), "count": row.get("count")}
            for dimension, rows in concentration.items()
            for row in rows
        ],
    )

    for item in manual_context:
        evidence.append(
            {
                "id": str(item.get("id")),
                "source": "manual",
                "title": f"Manual key {item.get('key')}",
                "value": str(item.get("summary") or ""),
                "rows": [
                    {
                        "key": item.get("key"),
                        "pages": item.get("pages") or [],
                        "source": item.get("source") or "manual",
                    }
                ],
            }
        )

    if no_report or report_mismatch:
        affected = no_report + report_mismatch
        findings.append(
            _finding(
                "critical",
                f"{affected} candidates have missing or mismatched reports; these rows cannot teach selection until retried.",
                ["sql:candidates_status_by_stage"],
                affected,
            )
        )
        recommendations.append(
            _recommendation(
                "retry",
                "Retry technical report failures",
                "no_report and report_mismatch are execution/data issues, not strategy evidence.",
                "Recover scorable reports before changing thresholds or parameters.",
                "medium",
                f"python ubs_agent.py --broker {broker} --account-type {account_type} --retry-run-id {run_id} --retry-mismatch-run --dry-run",
                ["sql:candidates_status_by_stage"],
            )
        )
    if no_trades:
        findings.append(
            _finding(
                "warning",
                f"{no_trades} candidates produced zero closed trades.",
                ["sql:candidates_status_by_stage", "sql:reason_counts"],
                no_trades,
            )
        )
    rejected_ratio = (rejected / total) if total else 0.0
    if rejected_ratio >= 0.5:
        findings.append(
            _finding(
                "warning",
                f"Rejected candidates are {rejected_ratio:.0%} of the run.",
                ["sql:candidates_status_by_stage", "sql:reason_counts"],
                rejected,
            )
        )
        recommendations.append(
            _recommendation(
                "review",
                "Review dominant rejection reasons",
                "A high rejection ratio usually means the next experiment should target the dominant failed criteria.",
                "Avoid spending MT5 time on the same weak area.",
                "medium",
                "",
                ["sql:reason_counts"],
            )
        )
    missing_robust = int(missing.get("robustness", 0) or 0)
    if missing_robust:
        findings.append(
            _finding(
                "info",
                f"{missing_robust} accepted base candidates are pending robustness.",
                ["sql:pipeline_missing_rows"],
                missing_robust,
            )
        )
        recommendations.append(
            _recommendation(
                "run_robustness",
                "Continue robustness for accepted base rows",
                "Accepted base rows are not fully qualified until OOS robustness is stored.",
                "Moves candidates to the next validation gate without changing generation policy.",
                "low",
                f"python ubs_agent.py --broker {broker} --account-type {account_type} --evaluate-robustness --robust-run-id {run_id} --robust-pending-only --dry-run",
                ["sql:pipeline_missing_rows"],
            )
        )
    missing_6m = int(missing.get("final_tick_6m", 0) or 0)
    if missing_6m:
        findings.append(
            _finding(
                "info",
                f"{missing_6m} candidates are eligible for Final Tick 6M but have no 6M row.",
                ["sql:pipeline_missing_rows"],
                missing_6m,
            )
        )
        recommendations.append(
            _recommendation(
                "run_final_tick_6m",
                "Continue Final Tick 6M",
                "Portfolio/live eligibility depends on the 6M gate.",
                "Completes the hard gate for candidates that already passed previous stages.",
                "low",
                f"python ubs_agent.py --broker {broker} --account-type {account_type} --evaluate-final-tick --final-tick-run-id {run_id} --final-tick-stage six_month --final-tick-pending-only --dry-run",
                ["sql:pipeline_missing_rows"],
            )
        )
    stale_total = sum(int(value or 0) for value in stale.values())
    if stale_total:
        findings.append(
            _finding(
                "warning",
                f"{stale_total} downstream rows are stale relative to their upstream gate.",
                ["sql:pipeline_stale_rows"],
                stale_total,
            )
        )
    _add_concentration_findings(findings, recommendations, concentration, total)
    for item in manual_context[:5]:
        evidence_id = str(item.get("id"))
        key = str(item.get("key") or "")
        if not key:
            continue
        recommendations.append(
            _recommendation(
                "review",
                f"Review parameter {key}",
                f"The run frequently mutated {key}; use the local UBS manual meaning before freezing or widening it.",
                "Improves parameter decisions without uploading the full manual.",
                "low",
                "",
                [evidence_id],
            )
        )

    if not findings:
        findings.append(
            _finding(
                "info",
                "No critical bottleneck was detected in the deterministic snapshot.",
                ["sql:candidates_status_by_stage"],
                total,
            )
        )
    summary = (
        f"Run {run_id} {broker}/{account_type}: {total} candidates, "
        f"{accepted} accepted, {rejected} rejected, {no_trades} no-trades."
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "broker": broker,
        "account_type": account_type,
        "run_id": run_id,
        "summary": summary,
        "confidence": 0.82 if total else 0.35,
        "evidence": evidence,
        "findings": findings,
        "recommendations": recommendations,
        "usage": {
            "provider": "local",
            "model": "deterministic",
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost_usd": None,
        },
    }
    return validate_report(report)


def top_manual_keys(snapshot: dict[str, Any], *, limit: int = 20) -> list[str]:
    return [str(row.get("key")) for row in snapshot.get("top_mutated_keys", [])[:limit] if row.get("key")]


def _add_concentration_findings(
    findings: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    concentration: dict[str, Any],
    total: int,
) -> None:
    if total <= 0:
        return
    for dimension, label in (("target_symbol", "symbol"), ("period", "timeframe")):
        rows = concentration.get(dimension) or []
        if not rows:
            continue
        top = rows[0]
        ratio = int(top.get("count") or 0) / total
        if ratio < 0.45:
            continue
        findings.append(
            _finding(
                "warning",
                f"Run is concentrated in one {label}: {top.get('key')} has {ratio:.0%} of candidates.",
                ["sql:symbol_timeframe_concentration"],
                int(top.get("count") or 0),
            )
        )
        recommendations.append(
            _recommendation(
                "change_mode",
                f"Increase {label} diversity in the next run",
                "Concentration reduces what the run can teach about the broader universe.",
                "More diverse evidence for source selection and weight updates.",
                "medium",
                "",
                ["sql:symbol_timeframe_concentration"],
            )
        )


def _add_evidence(
    evidence: list[dict[str, Any]],
    evidence_id: str,
    source: str,
    title: str,
    value: str,
    rows: list[dict[str, Any]],
) -> None:
    evidence.append({"id": evidence_id, "source": source, "title": title, "value": value, "rows": rows})


def _finding(severity: str, claim: str, evidence_ids: list[str], affected_count: int) -> dict[str, Any]:
    return {
        "severity": severity,
        "claim": claim,
        "evidence_ids": evidence_ids,
        "affected_count": int(max(0, affected_count)),
    }


def _recommendation(
    action_type: str,
    title: str,
    rationale: str,
    expected_effect: str,
    risk: str,
    cli_preview: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    return {
        "action_type": action_type,
        "title": title,
        "rationale": rationale,
        "expected_effect": expected_effect,
        "risk": risk,
        "requires_approval": True,
        "cli_preview": cli_preview,
        "evidence_ids": evidence_ids,
    }


def _fmt_counts(counts: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"


def _reason_value(reasons: dict[str, Any]) -> str:
    parts = []
    for stage, values in reasons.items():
        if values:
            parts.append(f"{stage}: {_fmt_counts(dict(values))}")
    return " | ".join(parts) or "none"


def _concentration_value(concentration: dict[str, Any]) -> str:
    parts = []
    for dimension, rows in concentration.items():
        if rows:
            top = rows[0]
            parts.append(f"{dimension}: {top.get('key')}={top.get('count')}")
    return " | ".join(parts) or "none"
