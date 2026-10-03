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
from ubs_agent_rescore_base import (  # noqa: F401
    _rescore_metrics_json,
    _run_execution_dates,
    _stored_or_discovered_report,
    _stored_score_config,
    apply_robustness_degradation,
    robustness_degradation_config,
)
from ubs_agent_rescore_robustness import _rescore_robustness_from_reports  # noqa: F401
from ubs_agent_universe import (
    score_config_for_variant,
)


def _batched_memory_updates(function):
    @wraps(function)
    def wrapped(args, memory, score_config):
        context = memory.batch_updates() if hasattr(memory, "batch_updates") else nullcontext()
        with context:
            return function(args, memory, score_config)

    return wrapped


def _candidate_score_update(row, args: argparse.Namespace, score_config: ScoreConfig):
    variant = variant_from_candidate_row(row)
    config = score_config_for_variant(
        score_config, variant, min_trades_w1=args.min_trades_w1,
        min_trades_mn=args.min_trades_mn)
    result = _rescore_metrics_json(row["metrics_json"], config)
    payload, stored = json.loads(result.to_json()), json.loads(row["metrics_json"])
    invalid_stops = result.trades <= 0 and stored.get("failure_type") in {"invalid_stops", "incompatible_volume"}
    if invalid_stops:
        for key in (
            "failure_type", "reasons", "invalid_order_count", "invalid_order_count_scope",
            "invalid_order_sample", "log_source", "retryable", "volume_min", "max_lots", "volume_evidence",
        ):
            if key in stored:
                payload[key] = stored[key]
        payload["accepted"] = False
    status = (
        "rejected" if invalid_stops else "no_trades" if result.trades <= 0 else
        "accepted" if result.accepted else "rejected")
    return (
        result.score, int(status == "accepted" and result.accepted),
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        status, int(row["id"])), status


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
        try:
            update, status = _candidate_score_update(row, args, score_config)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            invalid_metrics += 1
            print(f"AVISO: metrics_json base invalido candidate #{int(row['id'])}: {exc}")
            continue
        updates.append(update)
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
