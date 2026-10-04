"""Auditoria por fila y resumen de pipeline de la generalizacion v2."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

FORMULA_VERSION = "2"
DEGRADATION_VERSION = "robustness_degradation_v2"
# ubs_agent.py solo ejecuta apply_robustness_degradation() en la rama que termina
# en accepted/rejected. no_trades, report_mismatch y parse_error guardan
# degradation_json vacio por diseno: no hay metricas OOS comparables.
SCORED_STATUSES = {"accepted", "rejected"}
REQUIRED_CHECKS: dict[str, float] = {
    "net_retention": 0.50,
    "pf_edge_retention": 0.50,
    "recovery_retention": 0.50,
    "dd_inflation": 2.00,
    "trade_rate_retention": 0.50,
    "residual_profit_ratio": 0.20,
    "oos_positive_month_ratio": 0.50,
    "trade_curve_stability": 0.60,
    "stability_retention": 0.75,
    "bootstrap_net_positive_probability": 0.95,
    "bootstrap_pf_p05": 1.05,
}


def safe_json(raw: object) -> dict[str, Any] | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def resolved_report_exists(raw: object) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    path = Path(text)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.exists()


def issue(severity: str, code: str, count: int, detail: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "count": int(count), "detail": detail}


def _audit_row_checks(
    checks: dict,
    check_stats: dict[str, Counter],
    check_threshold_mismatches: Counter,
) -> list[bool]:
    """Contabiliza cada regla obligatoria y devuelve los veredictos habilitados."""
    available_results: list[bool] = []
    for name, expected_threshold in REQUIRED_CHECKS.items():
        raw_check = checks.get(name)
        if not isinstance(raw_check, dict):
            continue
        stats = check_stats[name]
        stats["total"] += 1
        if bool(raw_check.get("enabled", False)):
            stats["enabled"] += 1
        if bool(raw_check.get("available", False)):
            stats["available"] += 1
            accepted = bool(raw_check.get("accepted", False))
            stats["passed" if accepted else "failed"] += 1
            if bool(raw_check.get("enabled", False)):
                available_results.append(accepted)
        else:
            stats["unavailable"] += 1
        try:
            threshold = float(raw_check.get("threshold"))
        except (TypeError, ValueError):
            check_threshold_mismatches[name] += 1
        else:
            if abs(threshold - expected_threshold) > 1e-9:
                check_threshold_mismatches[name] += 1
    return available_results


def _audit_row_score(row: dict[str, Any], status: str, counters: Counter) -> None:
    """Version de formula, existencia del reporte y coherencia del flag accepted."""
    metrics = safe_json(row.get("metrics_json"))
    if metrics is None:
        counters["invalid_metrics_json"] += 1
    elif str(metrics.get("score_formula_version", "")) != FORMULA_VERSION:
        counters["wrong_score_formula_version"] += 1
    else:
        counters["score_v2"] += 1

    if not resolved_report_exists(row.get("report_path")):
        counters["missing_report_file"] += 1

    expected_accepted = 1 if status == "accepted" else 0
    if status in {"accepted", "rejected", "no_trades"}:
        try:
            stored_accepted = int(row.get("accepted") or 0)
        except (TypeError, ValueError):
            stored_accepted = -1
        if stored_accepted != expected_accepted:
            counters["accepted_flag_mismatch"] += 1


def _audit_row(
    row: dict[str, Any],
    counters: Counter,
    check_stats: dict[str, Counter],
    check_threshold_mismatches: Counter,
) -> None:
    status = str(row.get("status") or "")
    degradation = safe_json(row.get("degradation_json"))
    _audit_row_score(row, status, counters)

    if status not in SCORED_STATUSES:
        # Estas filas no pasan por el motor de degradacion; exigirles version
        # v2 seria un falso positivo. Lo que si es un error es lo contrario:
        # que arrastren un payload de degradacion.
        if degradation not in (None, {}):
            counters["non_scored_with_degradation"] += 1
        return

    if degradation is None:
        counters["invalid_degradation_json"] += 1
        return
    if str(degradation.get("version", "")) != DEGRADATION_VERSION:
        counters["wrong_degradation_version"] += 1
        return
    counters["degradation_v2"] += 1
    checks = degradation.get("checks")
    if not isinstance(checks, dict):
        counters["missing_checks_object"] += 1
        return

    missing_checks = set(REQUIRED_CHECKS) - set(checks)
    if missing_checks:
        counters["rows_missing_required_checks"] += 1
        for name in missing_checks:
            check_stats[name]["missing"] += 1

    available_results = _audit_row_checks(checks, check_stats, check_threshold_mismatches)

    recovery = checks.get("recovery_retention")
    if isinstance(recovery, dict) and not {
        "base_annualized",
        "oos_annualized",
    }.issubset(recovery):
        counters["recovery_duration_fields_missing"] += 1

    absolute_accepted = bool(degradation.get("absolute_accepted", False))
    computed_final = absolute_accepted and all(available_results)
    stored_final = bool(degradation.get("final_accepted", degradation.get("accepted", False)))
    if computed_final != stored_final:
        counters["degradation_final_recompute_mismatch"] += 1
    expected_status = "accepted" if stored_final else "rejected"
    if status != expected_status:
        counters["status_vs_degradation_mismatch"] += 1


def audit_current_rows(rows: dict[int, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: Counter[str] = Counter()
    check_stats: dict[str, Counter[str]] = {name: Counter() for name in REQUIRED_CHECKS}
    check_threshold_mismatches: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []

    for row in rows.values():
        _audit_row(row, counters, check_stats, check_threshold_mismatches)

    critical_fields = (
        "invalid_metrics_json",
        "wrong_score_formula_version",
        "invalid_degradation_json",
        "wrong_degradation_version",
        "non_scored_with_degradation",
        "missing_checks_object",
        "rows_missing_required_checks",
        "accepted_flag_mismatch",
        "recovery_duration_fields_missing",
        "degradation_final_recompute_mismatch",
        "status_vs_degradation_mismatch",
    )
    for name in critical_fields:
        if counters[name]:
            issues.append(issue("critical", name, counters[name], "Inconsistencia en filas actuales de robustez."))
    if counters["missing_report_file"]:
        issues.append(
            issue(
                "warning",
                "missing_report_file",
                counters["missing_report_file"],
                "La fila fue auditada, pero el reporte local ya no existe.",
            )
        )
    for name, count in sorted(check_threshold_mismatches.items()):
        if count:
            issues.append(
                issue(
                    "critical",
                    f"threshold_mismatch:{name}",
                    count,
                    f"El umbral guardado no coincide con {REQUIRED_CHECKS[name]}",
                )
            )
    for name, stats in sorted(check_stats.items()):
        if stats["unavailable"]:
            issues.append(
                issue(
                    "warning",
                    f"unavailable_check:{name}",
                    stats["unavailable"],
                    "La regla quedo neutral en estas filas por falta de datos comparables.",
                )
            )

    summary = dict(counters)
    summary["total"] = len(rows)
    summary["checks"] = {name: dict(stats) for name, stats in check_stats.items()}
    summary["threshold_mismatches"] = dict(check_threshold_mismatches)
    return summary, issues


def stage_snapshot(
    candidate_id: int,
    probe: dict[int, str],
    six_month: dict[int, str],
    regression: dict[int, str],
    portfolio: set[int],
) -> dict[str, Any]:
    return {
        "final_tick": probe.get(candidate_id),
        "final_tick_6m": six_month.get(candidate_id),
        "regression": regression.get(candidate_id),
        "portfolio_member": candidate_id in portfolio,
    }


def full_downstream_rows(snapshot: dict[str, Any]) -> bool:
    return all(snapshot.get(name) is not None for name in ("final_tick", "final_tick_6m", "regression"))


def full_pass_chain(snapshot: dict[str, Any]) -> bool:
    return (
        snapshot.get("final_tick") in {"accepted", "pending_ohlc_trades"}
        and snapshot.get("final_tick_6m") == "accepted"
        and snapshot.get("regression") == "accepted"
    )


def pipeline_summary(rows: dict[int, dict[str, Any]], stages: dict[str, dict[int, str]]) -> dict[str, int]:
    eligible_probe = {
        candidate_id
        for candidate_id, row in rows.items()
        if row.get("base_status") == "accepted" and row.get("status") == "accepted"
    }
    probe = stages["final_tick"]
    six_month = stages["final_tick_6m"]
    regression = stages["regression"]
    eligible_6m = {
        candidate_id
        for candidate_id in eligible_probe
        if probe.get(candidate_id) in {"accepted", "pending_ohlc_trades"}
    }
    eligible_regression = {
        candidate_id for candidate_id in eligible_6m if six_month.get(candidate_id) == "accepted"
    }
    return {
        "eligible_final_tick": len(eligible_probe),
        "missing_final_tick": sum(candidate_id not in probe for candidate_id in eligible_probe),
        "eligible_final_tick_6m": len(eligible_6m),
        "missing_final_tick_6m": sum(candidate_id not in six_month for candidate_id in eligible_6m),
        "eligible_regression": len(eligible_regression),
        "missing_regression": sum(candidate_id not in regression for candidate_id in eligible_regression),
    }
