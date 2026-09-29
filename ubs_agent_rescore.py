"""Repuntuacion de candidatos y robustez con los criterios actuales."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from functools import wraps
from pathlib import Path

from run_tests import parse_symbol_map
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
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult, rescore_result, score_report_file
from ubs.risk_profit import RiskProfitConfig, combine_robustness_profit_gate, robustness_result_status
from ubs.weights import DEFAULT_ROBUST_NEGATIVE_BONUS, DEFAULT_ROBUST_POSITIVE_BONUS
from ubs_agent_evaluate import (
    evaluate_history_probe,
    evaluate_variant_report,
)
from ubs_agent_reports import (
    classify_zero_trade_robustness,
    find_report_for_set,
    report_matches_variant,
)
from ubs_agent_universe import (
    score_config_for_variant,
)


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


def _batched_memory_updates(function):
    @wraps(function)
    def wrapped(args, memory, score_config):
        context = memory.batch_updates() if hasattr(memory, "batch_updates") else nullcontext()
        with context:
            return function(args, memory, score_config)

    return wrapped


@_batched_memory_updates
def rescore_candidate_scores_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if bool(getattr(args, "rescore_from_reports", False)):
        return _rescore_candidate_scores_from_reports(args, memory, score_config)
    rows = memory.conn.execute(
        """
        select *
        from candidates
        where status in ('accepted', 'rejected', 'no_trades')
          and coalesce(metrics_json, '') != ''
        order by run_id, generation, id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    invalid_metrics = 0
    updates: list[tuple[object, ...]] = []
    for row in rows:
        variant = variant_from_candidate_row(row)
        config = score_config_for_variant(
            score_config,
            variant,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        try:
            result = _rescore_metrics_json(row["metrics_json"], config)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            invalid_metrics += 1
            print(f"AVISO: metrics_json base invalido candidate #{int(row['id'])}: {exc}")
            continue
        payload = json.loads(result.to_json())
        stored = json.loads(row["metrics_json"])
        invalid_stops = result.trades <= 0 and stored.get("failure_type") in {"invalid_stops", "incompatible_volume"}
        if invalid_stops:
            for key in ("failure_type", "reasons", "invalid_order_count", "invalid_order_count_scope",
                        "invalid_order_sample", "log_source", "retryable", "volume_min", "max_lots", "volume_evidence"):
                if key in stored:
                    payload[key] = stored[key]
            payload["accepted"] = False
        status = "rejected" if invalid_stops else "no_trades" if result.trades <= 0 else ("accepted" if result.accepted else "rejected")
        updates.append(
            (
                result.score,
                int(status == "accepted" and result.accepted),
                json.dumps(payload, ensure_ascii=True, sort_keys=True),
                status,
                int(row["id"]),
            )
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    if updates:
        memory.conn.executemany(
            """
            update candidates
            set score=?, accepted=?, metrics_json=?, status=?
            where id=?
            """,
            updates,
        )
        memory.cleanup_stale_stage_rows()

    total = sum(status_counts.values())
    print(
        "Candidatos repuntuados desde SQLite: "
        + (", ".join(f"{status}={count}" for status, count in sorted(status_counts.items())) or "sin filas")
        + f"; total={total}; invalidos={invalid_metrics}"
    )
    print(f"Memoria: {memory.path}")
    return 0


def _rescore_candidate_report(
    row, args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig, symbol_map: dict[str, str],
) -> str | None:
    report = _stored_or_discovered_report(row)
    if report is None:
        return None
    variant = variant_from_candidate_row(row)
    if str(row["policy"] or "") == "history_probe":
        status, _ = evaluate_history_probe(
            memory,
            variant,
            score_config,
            symbol_map,
            args.broker,
            report_path=report,
            symbol_suffix=args.symbol_suffix,
        )
    else:
        status, _ = evaluate_variant_report(
            memory,
            variant,
            report,
            score_config,
            symbol_map,
            args.broker,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
            symbol_suffix=args.symbol_suffix,
        )
    return status


def _rescore_candidate_scores_from_reports(
    args: argparse.Namespace,
    memory: AgentMemory,
    score_config: ScoreConfig,
) -> int:
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = memory.conn.execute(
        """
        select *
        from candidates
        where status in (
            'accepted', 'rejected', 'no_trades', 'history_ok', 'no_history', 'report_mismatch',
            'parse_error', 'no_report', 'generated'
        )
        order by run_id, generation, id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    skipped_no_report = 0
    for row in rows:
        status = _rescore_candidate_report(row, args, memory, score_config, symbol_map)
        if status is None:
            skipped_no_report += 1
            continue
        status_counts[status] = status_counts.get(status, 0) + 1

    total = sum(status_counts.values())
    if status_counts:
        print(
            "Candidatos repuntuados con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay candidatos con reporte disponible para repuntuar.")
    if skipped_no_report:
        print(f"Candidatos sin reporte local omitidos: {skipped_no_report}")
    print(f"Memoria: {memory.path}")
    return 0


@_batched_memory_updates
def rescore_robustness_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if bool(getattr(args, "rescore_from_reports", False)):
        return _rescore_robustness_from_reports(args, memory, score_config)
    rows = memory.conn.execute(
        """
        select
            c.*,
            cr.metrics_json as robust_metrics_json,
            cr.degradation_json as robust_degradation_json,
            cr.report_path as robust_report_path,
            cr.from_date as robust_from_date,
            cr.to_date as robust_to_date,
            cr.positive_bonus as robust_positive_bonus,
            cr.negative_bonus as robust_negative_bonus,
            r.config_json as run_config_json
        from candidate_robustness cr
        join candidates c on c.id = cr.candidate_id
        join runs r on r.id = cr.run_id
        where cr.status in ('accepted', 'rejected', 'no_trades', 'pending_risk_evidence')
          and coalesce(cr.metrics_json, '') != ''
        order by cr.run_id, c.generation, c.id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    invalid_metrics = 0
    updates: list[tuple[object, ...]] = []
    degradation_config = robustness_degradation_config(args)
    for row in rows:
        candidate_id = int(row["id"])
        variant = variant_from_candidate_row(row)
        config = score_config_for_variant(
            score_config,
            variant,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        try:
            result = _rescore_metrics_json(row["robust_metrics_json"], config, risk_stage="oos")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            invalid_metrics += 1
            print(f"AVISO: metrics_json robustez invalido candidate #{candidate_id}: {exc}")
            continue
        try:
            stored_degradation = json.loads(str(row["robust_degradation_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            stored_degradation = {}
        if not isinstance(stored_degradation, dict):
            stored_degradation = {}
        degradation: dict[str, object] = {}
        if result.trades > 0:
            result, degradation = apply_robustness_degradation(
                result,
                base_metrics_raw=row["metrics_json"],
                run_config_raw=row["run_config_json"],
                oos_from_date=row["robust_from_date"],
                oos_to_date=row["robust_to_date"],
                config=degradation_config,
            )
        elif stored_degradation.get("failure_type") in {"invalid_stops", "incompatible_volume"}:
            degradation = stored_degradation
        status = (
            "rejected"
            if result.trades <= 0 and degradation.get("failure_type") in {"invalid_stops", "incompatible_volume"}
            else "no_trades"
            if result.trades <= 0
            else robustness_result_status(result)
        )
        updates.append(
            (
                status,
                int(status == "accepted" and result.accepted),
                result.score,
                result.to_json(),
                json.dumps(degradation, ensure_ascii=True, sort_keys=True),
                datetime.now().isoformat(timespec="seconds"),
                candidate_id,
            )
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    if updates:
        memory.conn.executemany(
            """
            update candidate_robustness
            set status=?, accepted=?, score=?, metrics_json=?, degradation_json=?, evaluated_at=?
            where candidate_id=?
            """,
            updates,
        )
        removed = memory.cleanup_stale_stage_rows()
        if any(removed.values()):
            print(
                "Etapas posteriores invalidadas: "
                + ", ".join(f"{stage}={count}" for stage, count in removed.items() if count)
            )

    total = sum(status_counts.values())
    print(
        "Robustez repuntuada desde SQLite: "
        + (", ".join(f"{status}={count}" for status, count in sorted(status_counts.items())) or "sin filas")
        + f"; total={total}; invalidos={invalid_metrics}"
    )
    print(f"Memoria: {memory.path}")
    return 0


def _rescore_robustness_from_reports(
    args: argparse.Namespace,
    memory: AgentMemory,
    score_config: ScoreConfig,
) -> int:
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = memory.conn.execute(
        """
        select
            c.*,
            cr.status as robust_status,
            cr.report_path as robust_report_path,
            cr.from_date as robust_from_date,
            cr.to_date as robust_to_date,
            cr.positive_bonus as robust_positive_bonus,
            cr.negative_bonus as robust_negative_bonus,
            r.config_json as run_config_json
        from candidate_robustness cr
        join candidates c on c.id = cr.candidate_id
        join runs r on r.id = cr.run_id
        order by cr.run_id, c.generation, c.id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    skipped_no_report = 0
    degradation_config = robustness_degradation_config(args)
    total_rows = len(rows)
    progress_step = max(total_rows // 100, 1)
    progress_started = time.monotonic()

    def print_progress(done: int) -> None:
        elapsed = max(time.monotonic() - progress_started, 0.001)
        percent = (done / total_rows * 100.0) if total_rows else 100.0
        rate = done / elapsed if done else 0.0
        remaining = (total_rows - done) / rate if rate > 0 else 0.0
        print(
            f"[generalization-v2] {done}/{total_rows} ({percent:5.1f}%) "
            f"elapsed={elapsed / 60.0:.1f}m eta={remaining / 60.0:.1f}m",
            flush=True,
        )

    for index, row in enumerate(rows, start=1):
        completed = index - 1
        if completed == 0 or completed % progress_step == 0:
            print_progress(completed)
        report_raw = str(row["robust_report_path"] or "").strip()
        report = resolve_workspace_path(report_raw) if report_raw else None
        if report is None or not report.exists():
            skipped_no_report += 1
            continue
        candidate_id = int(row["id"])
        run_id = int(row["run_id"])
        variant = variant_from_candidate_row(row)
        try:
            period_score_config = score_config_for_variant(
                score_config,
                variant,
                min_trades_w1=args.min_trades_w1,
                min_trades_mn=args.min_trades_mn,
            )
            result = score_report_file(
                report,
                config=period_score_config,
                broker=args.broker,
                include_generalization_bootstrap=True,
                risk_stage="oos",
            )
        except Exception as exc:
            print(f"AVISO: no pude parsear robustez candidate #{candidate_id}: {exc}")
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                None,
                "parse_error",
                report,
                str(row["robust_from_date"] or ""),
                str(row["robust_to_date"] or ""),
                float(row["robust_positive_bonus"] or DEFAULT_ROBUST_POSITIVE_BONUS),
                float(row["robust_negative_bonus"] or DEFAULT_ROBUST_NEGATIVE_BONUS),
            )
            status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
            continue
        matches, mismatch_reason = report_matches_variant(
            variant, result, symbol_map, args.symbol_suffix, args.broker
        )
        base_metrics_raw: object = row["metrics_json"]
        if matches and result.trades > 0 and result.accepted:
            base_report = _stored_or_discovered_report(row)
            if base_report is not None and base_report.exists():
                try:
                    base_result = score_report_file(
                        base_report,
                        config=_stored_score_config(row["metrics_json"], score_config),
                        broker=args.broker,
                    )
                except Exception as exc:
                    print(f"AVISO: no pude actualizar base candidate #{candidate_id}: {exc}")
                else:
                    base_metrics_raw = base_result.to_json()
                    memory.conn.execute(
                        "update candidates set score=?, metrics_json=? where id=?",
                        (base_result.score, base_metrics_raw, candidate_id),
                    )
        degradation: dict[str, object] = {}
        if not matches:
            print(f"AVISO: reporte robustez no coincide para candidate #{candidate_id}: {mismatch_reason}")
            status = "report_mismatch"
        elif result.trades <= 0:
            status, degradation = classify_zero_trade_robustness(report, variant)
        else:
            result, degradation = apply_robustness_degradation(
                result,
                base_metrics_raw=base_metrics_raw,
                run_config_raw=row["run_config_json"],
                oos_from_date=row["robust_from_date"],
                oos_to_date=row["robust_to_date"],
                config=degradation_config,
            )
            status = robustness_result_status(result)
        memory.record_candidate_robustness(
            candidate_id,
            run_id,
            result,
            status,
            report,
            str(row["robust_from_date"] or ""),
            str(row["robust_to_date"] or ""),
            float(row["robust_positive_bonus"] or DEFAULT_ROBUST_POSITIVE_BONUS),
            float(row["robust_negative_bonus"] or DEFAULT_ROBUST_NEGATIVE_BONUS),
            degradation=degradation,
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    print_progress(total_rows)

    removed = memory.cleanup_stale_stage_rows()
    if any(removed.values()):
        print(
            "Etapas posteriores invalidadas: "
            + ", ".join(f"{stage}={count}" for stage, count in removed.items() if count)
        )

    total = sum(status_counts.values())
    if status_counts:
        print(
            "Robustez repuntuada con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay resultados de robustez con reporte disponible para repuntuar.")
    if skipped_no_report:
        print(f"Robustez sin reporte local omitida: {skipped_no_report}")
    print(f"Memoria: {memory.path}")
    return 0


