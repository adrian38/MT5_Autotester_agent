"""Evaluacion de la robustez OOS de un candidato."""
from __future__ import annotations

import argparse
import sqlite3
import time

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, score_report_file
from ubs.risk_profit import robustness_result_status
from ubs_agent_config import (
    SYMBOL_NOT_EXIST_STATUS,
)
from ubs_agent_evaluate import (
    recreate_work_dir,
    remove_report_artifacts,
)
from ubs_agent_reports import (
    classify_zero_trade_robustness,
    find_report_for_set,
    report_matches_variant,
)
from ubs_agent_rescore import (
    apply_robustness_degradation,
    robustness_degradation_config,
)
from ubs_agent_sets import (
    write_retry_set,
)
from ubs_agent_universe import (
    format_retired_symbol_rows,
    missing_report_status,
    score_config_for_variant,
    split_retired_symbols,
)
from ubs_agent_variants import (
    run_backtests,
)


ROBUST_RETRYABLE_STATUSES = {"pending", "no_report", "parse_error", "report_mismatch", "no_trades"}


def robust_status_pending_for_retry(status: object) -> bool:
    value = str(status or "").strip()
    return not value or value in ROBUST_RETRYABLE_STATUSES


def evaluate_candidate_robustness(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.expert and not args.multi_terminal and not args.dry_run:
        print("ERROR: robustez requiere --expert o --multi-terminal")
        return 1

    run = memory.run_by_id(args.robust_run_id) if args.robust_run_id else memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para robustez")
        return 1

    run_id = int(run["id"])
    run_dir = resolve_workspace_path(run["output_dir"])
    rows_with_paths = [
        (row, resolve_workspace_path(row["set_path"]))
        for row in memory.accepted_candidates_for_robustness(run_id)
    ]
    rows_with_paths = [(row, set_path) for row, set_path in rows_with_paths if set_path.exists()]
    robust_candidate_ids = {int(value) for value in (args.robust_candidate_id or [])}
    if robust_candidate_ids:
        rows_with_paths = [
            (row, set_path) for row, set_path in rows_with_paths
            if int(row["id"]) in robust_candidate_ids
        ]
    if args.robust_pending_only:
        rows_with_paths = [
            (row, set_path) for row, set_path in rows_with_paths
            if robust_status_pending_for_retry(row["robust_status"])
        ]
    rows_with_paths, retired_rows = split_retired_symbols(
        rows_with_paths, args, row_of=lambda item: item[0]
    )
    for row in retired_rows:
        memory.record_candidate_robustness(
            int(row["id"]),
            run_id,
            None,
            SYMBOL_NOT_EXIST_STATUS,
            None,
            args.from_date,
            args.to_date,
            args.robust_positive_bonus,
            args.robust_negative_bonus,
        )
    if retired_rows:
        print(
            f"Robustez: {len(retired_rows)} candidato(s) omitidos sin abrir MT5 por simbolo "
            f"retirado del broker ({format_retired_symbol_rows(retired_rows)}); "
            f"marcados {SYMBOL_NOT_EXIST_STATUS}."
        )
    if not rows_with_paths:
        if args.robust_pending_only:
            print(f"Robustez run #{run_id}: no hay candidatos accepted pendientes ni retryables de OOS.")
        else:
            print(f"Robustez run #{run_id}: no hay candidatos accepted con .set existente.")
        return 0

    robust_mode = "pending" if args.robust_pending_only else "all"
    robust_dir = recreate_work_dir(run_dir / "robustness" / f"run_{run_id}_{robust_mode}")
    copied: list[tuple[sqlite3.Row, Variant]] = []
    used_names: set[str] = set()
    for row, source_set in rows_with_paths:
        name = f"robust_{int(row['id']):06d}_{source_set.name}"
        if name in used_names:
            print(f"ERROR: nombre duplicado en robustez: {name}")
            return 1
        used_names.add(name)
        retry_set = robust_dir / name
        original_variant = variant_from_candidate_row(row)
        # El .set guardado puede llevar un ForceSymbol con la ortografia
        # equivocada; sin repararlo MT5 cierra sin reporte y robustez nunca
        # avanza, igual que pasaba en los retries.
        exact_symbol = write_retry_set(
            source_set, retry_set, False, args, original_variant.target_symbol
        )
        if not args.dry_run:
            remove_report_artifacts(retry_set)
        copied.append(
            (
                row,
                Variant(
                    path=retry_set,
                    seed=original_variant.seed,
                    target_symbol=exact_symbol,
                    target_period=original_variant.target_period,
                    mutated_keys=original_variant.mutated_keys,
                    missing_lot_keys=original_variant.missing_lot_keys,
                    policy=f"{original_variant.policy}+robustness",
                ),
            )
        )

    mode_label = "pendientes sin OOS" if args.robust_pending_only else "todos los accepted"
    print(f"Robustez run #{run_id}: modo={mode_label}; candidatos accepted={len(copied)}")
    print(f"Directorio robustez: {robust_dir}")
    print(f"Fechas robustez: {args.from_date or '(template)'} -> {args.to_date or '(template)'}")
    print(
        "Bonus robustez: "
        f"accepted={args.robust_positive_bonus:+.2f}, rejected={args.robust_negative_bonus:+.2f}"
    )
    degradation_config = robustness_degradation_config(args)
    print(
        "Degradacion maxima: "
        f"net>={degradation_config.min_net_retention:.2f}, "
        f"edge PF>={degradation_config.min_pf_edge_retention:.2f}, "
        f"recovery>={degradation_config.min_recovery_retention:.2f}, "
        f"DD<={degradation_config.max_dd_inflation:.2f}x"
    )
    batch_started_at = time.time()
    code = run_backtests(args, robust_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza robustez.")
        return 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0

    symbol_map = parse_symbol_map(args.symbol_map)
    status_counts: dict[str, int] = {}
    for row, variant in copied:
        candidate_id = int(row["id"])
        report = find_report_for_set(variant.path, min_mtime=batch_started_at - 1.0)
        if not report:
            status = missing_report_status(variant.target_symbol, args)
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                None,
                status,
                None,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            continue
        period_score_config = score_config_for_variant(
            score_config,
            variant,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        try:
            result = score_report_file(
                report,
                config=period_score_config,
                broker=args.broker,
                include_generalization_bootstrap=True,
                risk_stage="oos",
            )
        except Exception as exc:
            print(f"AVISO: no pude parsear robustez {report}: {exc}")
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                None,
                "parse_error",
                report,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
            continue
        matches, mismatch_reason = report_matches_variant(
            variant, result, symbol_map, args.symbol_suffix, args.broker
        )
        if not matches:
            print(f"AVISO: reporte robustez no coincide para candidate #{candidate_id}: {mismatch_reason}")
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                result,
                "report_mismatch",
                report,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
            continue
        if result.trades <= 0:
            status, failure_metadata = classify_zero_trade_robustness(report, variant)
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                result,
                status,
                report,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
                degradation=failure_metadata,
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            continue
        result, degradation = apply_robustness_degradation(
            result,
            base_metrics_raw=row["metrics_json"],
            run_config_raw=run["config_json"],
            oos_from_date=args.from_date,
            oos_to_date=args.to_date,
            config=degradation_config,
        )
        status = robustness_result_status(result)
        memory.record_candidate_robustness(
            candidate_id,
            run_id,
            result,
            status,
            report,
            args.from_date,
            args.to_date,
            args.robust_positive_bonus,
            args.robust_negative_bonus,
            degradation=degradation,
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    print(
        "Robustez terminada: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0


def _relative_delta_pct(reference: float, observed: float, *, floor: float = 1.0) -> float:
    """Return the symmetric max-denominator percentage difference.

    This is intentionally not the classic percentage change from ``reference``:
    for example, 100 versus 150 is 33.33%, regardless of argument order.
    """

    denominator = max(abs(reference), abs(observed), floor)
    return abs(observed - reference) / denominator * 100.0


def _bounded_profit_factor(value: float) -> float:
    return min(max(float(value), 0.0), 10.0)
