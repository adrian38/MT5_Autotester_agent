"""Piezas compartidas por los repuntuados: reportes base y degradacion."""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ubs.degradation import (
    DEFAULT_MAX_DD_INFLATION,
    DEFAULT_MIN_BOOTSTRAP_NET_POSITIVE_PROBABILITY,
    DEFAULT_MIN_BOOTSTRAP_PF_P05,
    DEFAULT_MIN_NET_RETENTION,
    DEFAULT_MIN_OOS_POSITIVE_MONTH_RATIO,
    DEFAULT_MIN_PF_EDGE_RETENTION,
    DEFAULT_MIN_RECOVERY_RETENTION,
    DEFAULT_MIN_RESIDUAL_PROFIT_RATIO,
    DEFAULT_MIN_STABILITY_RETENTION,
    DEFAULT_MIN_TRADE_CURVE_STABILITY,
    DEFAULT_MIN_TRADE_RATE_RETENTION,
    RobustnessDegradationConfig,
    evaluate_robustness_degradation,
)
from ubs.path_utils import resolve_workspace_path
from ubs_agent_reports import find_report_for_set
from ubs.risk_profit import RiskProfitConfig, combine_robustness_profit_gate
from ubs.score import ScoreConfig, ScoreResult, rescore_result


def _stored_or_discovered_report(row: sqlite3.Row) -> Path | None:
    report_raw = str(row["report_path"] or "").strip()
    if report_raw:
        report = resolve_workspace_path(report_raw)
        if report.exists():
            return report
    set_path = resolve_workspace_path(str(row["set_path"] or ""))
    if set_path.exists():
        return find_report_for_set(set_path)
    return None


def _rescore_metrics_json(raw: object, config: ScoreConfig, *, risk_stage: str = "base") -> ScoreResult:
    if raw is None or not str(raw).strip():
        raise ValueError("metrics_json vacio")
    return rescore_result(ScoreResult.from_json(str(raw)), config, risk_stage=risk_stage)


def _stored_score_config(raw: object, fallback: ScoreConfig) -> ScoreConfig:
    """Thresholds a stored row was judged with, falling back field by field.

    Every field the blob lacks keeps the caller's value, the risk-profit policy
    included: a row written before the route existed must be re-judged with the
    policy this invocation asked for, not with the route's defaults.
    """

    try:
        payload = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return ScoreConfig.from_dict(
        payload.get("score_config") if isinstance(payload, dict) else None, fallback
    )


def robustness_degradation_config(args: argparse.Namespace) -> RobustnessDegradationConfig:
    return RobustnessDegradationConfig(
        min_net_retention=float(getattr(args, "robust_min_net_retention", DEFAULT_MIN_NET_RETENTION)),
        min_pf_edge_retention=float(
            getattr(args, "robust_min_pf_edge_retention", DEFAULT_MIN_PF_EDGE_RETENTION)
        ),
        min_recovery_retention=float(
            getattr(args, "robust_min_recovery_retention", DEFAULT_MIN_RECOVERY_RETENTION)
        ),
        max_dd_inflation=float(getattr(args, "robust_max_dd_inflation", DEFAULT_MAX_DD_INFLATION)),
        min_trade_rate_retention=float(
            getattr(args, "robust_min_trade_rate_retention", DEFAULT_MIN_TRADE_RATE_RETENTION)
        ),
        min_residual_profit_ratio=float(
            getattr(args, "robust_min_residual_profit_ratio", DEFAULT_MIN_RESIDUAL_PROFIT_RATIO)
        ),
        min_oos_positive_month_ratio=float(
            getattr(args, "robust_min_oos_positive_month_ratio", DEFAULT_MIN_OOS_POSITIVE_MONTH_RATIO)
        ),
        min_trade_curve_stability=float(
            getattr(args, "robust_min_trade_curve_stability", DEFAULT_MIN_TRADE_CURVE_STABILITY)
        ),
        min_stability_retention=float(
            getattr(args, "robust_min_stability_retention", DEFAULT_MIN_STABILITY_RETENTION)
        ),
        min_bootstrap_net_positive_probability=float(
            getattr(
                args,
                "robust_min_bootstrap_net_probability",
                DEFAULT_MIN_BOOTSTRAP_NET_POSITIVE_PROBABILITY,
            )
        ),
        min_bootstrap_pf_p05=float(
            getattr(args, "robust_min_bootstrap_pf_p05", DEFAULT_MIN_BOOTSTRAP_PF_P05)
        ),
    )


def _run_execution_dates(raw: object) -> tuple[str, str]:
    try:
        payload = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    execution = payload.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    run_args = payload.get("args")
    run_args = run_args if isinstance(run_args, dict) else {}
    return (
        str(execution.get("from_date") or run_args.get("from_date") or "").strip(),
        str(execution.get("to_date") or run_args.get("to_date") or "").strip(),
    )


def apply_robustness_degradation(
    result: ScoreResult,
    *,
    base_metrics_raw: object,
    run_config_raw: object,
    oos_from_date: object,
    oos_to_date: object,
    config: RobustnessDegradationConfig,
) -> tuple[ScoreResult, dict[str, object]]:
    try:
        base_metrics = json.loads(str(base_metrics_raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        base_metrics = {}
    if not isinstance(base_metrics, dict):
        base_metrics = {}
    base_from_date, base_to_date = _run_execution_dates(run_config_raw)
    oos_metrics = json.loads(result.to_json())
    degradation = evaluate_robustness_degradation(
        base_metrics,
        oos_metrics,
        base_from_date=base_from_date,
        base_to_date=base_to_date,
        oos_from_date=oos_from_date,
        oos_to_date=oos_to_date,
        config=config,
    )
    absolute_accepted = bool(result.accepted)
    policy = RiskProfitConfig.from_dict(result.score_config.get("risk_profit"))
    reasons, risk_audit, degradation = combine_robustness_profit_gate(
        base_metrics, oos_metrics, list(result.reasons), degradation, policy,
        max_drawdown_pct=float(result.score_config.get("max_drawdown_pct", 25.0)),
        min_recovery_factor=float(result.score_config.get("min_recovery_factor", 1.0)),
        degradation_config=config,
    )
    combined = replace(
        result,
        accepted=not reasons,
        reasons=tuple(reasons),
        risk_profit_audit=risk_audit,
    )
    degradation["absolute_accepted"] = absolute_accepted
    degradation["final_accepted"] = combined.accepted
    return combined, degradation
