from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Callable

from ubs.memory import AgentMemory
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.regression_rules import (
    REGRESSION_RETRYABLE_STATUSES,
    regression_points_breakdown,
    validate_regression_date_range,
)
from ubs.score import ScoreConfig, ScoreResult, score_report_file
from ubs.set_utils import compact_safe_part


@dataclass(frozen=True)
class RegressionRuntime:
    running_terminal_exit_code: int
    recreate_work_dir: Callable[[Path], Path]
    remove_report_artifacts: Callable[[Path], None]
    variant_from_candidate_row: Callable[[sqlite3.Row], Variant]
    run_backtests: Callable[..., int]
    find_report_for_set: Callable[..., Path | None]
    parse_symbol_map: Callable[[str], dict[str, str]]
    report_matches_variant: Callable[..., tuple[bool, str]]
    report_has_empty_tester_context: Callable[[ScoreResult], bool]
    read_report_dates: Callable[[Path], tuple[str, str] | None]
    tester_log_no_history_metadata: Callable[[Path, Variant], dict[str, object] | None]


def _score_config_for_period(config: ScoreConfig, period: str, args: Any) -> ScoreConfig:
    normalized = str(period or "").strip().upper()
    if normalized == "W1":
        return replace(config, min_trades=int(args.regression_min_trades_w1))
    if normalized in {"MN", "MN1"}:
        return replace(config, min_trades=int(args.regression_min_trades_mn))
    return config


def _details_payload(
    status: str,
    result: ScoreResult | None,
    args: Any,
    *,
    reasons: tuple[str, ...] = (),
    actual_dates: tuple[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> tuple[str, float]:
    reason_items = reasons or (tuple(result.reasons) if result is not None else ())
    points = regression_points_breakdown(
        status,
        reason_items,
        positive_points=float(args.regression_positive_points),
        negative_points=float(args.regression_negative_points),
    )
    payload: dict[str, object] = {
        "accepted": status == "accepted",
        "reasons": list(reason_items),
        "model": "1_minute_ohlc",
        "expected_from_date": str(args.regression_from_date).strip(),
        "expected_to_date": str(args.regression_to_date).strip(),
        "actual_from_date": actual_dates[0] if actual_dates else "",
        "actual_to_date": actual_dates[1] if actual_dates else "",
        "points": points,
    }
    if metadata:
        payload.update(metadata)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True), float(points["applied"])


def _record_technical(
    memory: AgentMemory,
    args: Any,
    *,
    candidate_id: int,
    run_id: int,
    status: str,
    report: Path | None,
    result: ScoreResult | None = None,
    reasons: tuple[str, ...] = (),
    actual_dates: tuple[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> str:
    details_json, points_applied = _details_payload(
        status,
        result,
        args,
        reasons=reasons,
        actual_dates=actual_dates,
        metadata=metadata,
    )
    memory.record_candidate_regression(
        candidate_id,
        run_id,
        status,
        result,
        report,
        details_json,
        args.regression_from_date,
        args.regression_to_date,
        args.regression_positive_points,
        args.regression_negative_points,
        points_applied,
    )
    return status


def evaluate_regression_report(
    memory: AgentMemory,
    args: Any,
    runtime: RegressionRuntime,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    run_id: int,
    candidate_id: int,
    variant: Variant,
    report: Path | None,
) -> str:
    if report is None:
        return _record_technical(
            memory,
            args,
            candidate_id=candidate_id,
            run_id=run_id,
            status="no_report",
            report=None,
            reasons=("no_report",),
        )

    period_config = _score_config_for_period(score_config, variant.target_period, args)
    try:
        result = score_report_file(report, config=period_config, broker=args.broker)
    except Exception as exc:
        print(f"AVISO: no pude parsear regresiva {report}: {exc}")
        return _record_technical(
            memory,
            args,
            candidate_id=candidate_id,
            run_id=run_id,
            status="parse_error",
            report=report,
            reasons=("parse_error",),
            metadata={"error": str(exc)},
        )

    no_history = runtime.tester_log_no_history_metadata(report, variant)
    if no_history:
        return _record_technical(
            memory,
            args,
            candidate_id=candidate_id,
            run_id=run_id,
            status="no_history",
            report=report,
            result=result,
            reasons=("no_history",),
            metadata=no_history,
        )
    if runtime.report_has_empty_tester_context(result):
        return _record_technical(
            memory,
            args,
            candidate_id=candidate_id,
            run_id=run_id,
            status="report_mismatch",
            report=report,
            result=result,
            reasons=("empty_tester_context",),
        )

    matches, mismatch_reason = runtime.report_matches_variant(
        variant,
        result,
        symbol_map,
        args.symbol_suffix,
    )
    if not matches:
        print(f"AVISO: reporte regresivo no coincide para candidate #{candidate_id}: {mismatch_reason}")
        return _record_technical(
            memory,
            args,
            candidate_id=candidate_id,
            run_id=run_id,
            status="report_mismatch",
            report=report,
            result=result,
            reasons=("report_mismatch",),
            metadata={"mismatch": mismatch_reason},
        )

    try:
        actual_dates = runtime.read_report_dates(report)
    except Exception as exc:
        actual_dates = None
        date_error = str(exc)
    else:
        date_error = ""
    expected_dates = (
        str(args.regression_from_date).strip(),
        str(args.regression_to_date).strip(),
    )
    if actual_dates != expected_dates:
        return _record_technical(
            memory,
            args,
            candidate_id=candidate_id,
            run_id=run_id,
            status="date_mismatch",
            report=report,
            result=result,
            reasons=("date_mismatch",),
            actual_dates=actual_dates,
            metadata={"date_error": date_error} if date_error else None,
        )

    status = "accepted" if result.accepted else "rejected"
    if result.trades <= 0:
        status = "no_trades"
    details_json, points_applied = _details_payload(
        status,
        result,
        args,
        actual_dates=actual_dates,
    )
    memory.record_candidate_regression(
        candidate_id,
        run_id,
        status,
        result,
        report,
        details_json,
        args.regression_from_date,
        args.regression_to_date,
        args.regression_positive_points,
        args.regression_negative_points,
        points_applied,
    )
    return status


def evaluate_candidate_regression(
    args: Any,
    memory: AgentMemory,
    score_config: ScoreConfig,
    runtime: RegressionRuntime,
) -> int:
    date_error = validate_regression_date_range(args.regression_from_date, args.regression_to_date)
    if date_error:
        print(f"ERROR: rango regresivo invalido: {date_error}")
        return 1
    if not args.expert and not args.multi_terminal and not args.dry_run:
        print("ERROR: prueba regresiva requiere --expert o --multi-terminal")
        return 1

    run = memory.run_by_id(args.regression_run_id) if args.regression_run_id else memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para prueba regresiva")
        return 1
    run_id = int(run["id"])
    run_dir = resolve_workspace_path(run["output_dir"])
    rows_with_paths = [
        (row, resolve_workspace_path(row["set_path"]))
        for row in memory.accepted_candidates_for_regression(run_id)
    ]
    rows_with_paths = [(row, path) for row, path in rows_with_paths if path.exists()]
    candidate_ids = {int(value) for value in (args.regression_candidate_id or [])}
    if candidate_ids:
        rows_with_paths = [(row, path) for row, path in rows_with_paths if int(row["id"]) in candidate_ids]
    if args.regression_pending_only:
        rows_with_paths = [
            (row, path)
            for row, path in rows_with_paths
            if not str(row["regression_status"] or "").strip()
            or str(row["regression_status"] or "").strip().lower() in REGRESSION_RETRYABLE_STATUSES
        ]
    if not rows_with_paths:
        mode = "pendientes/retryables" if args.regression_pending_only else "Final Tick 6M accepted"
        print(f"Regresiva run #{run_id}: no hay candidatos {mode} con .set existente.")
        return 0

    run_mode = "pending" if args.regression_pending_only else "all"
    regression_dir = runtime.recreate_work_dir(run_dir / "regression_2017_2019" / f"run_{run_id}_{run_mode}")
    copied: list[tuple[sqlite3.Row, Variant]] = []
    for row, source_set in rows_with_paths:
        set_label = compact_safe_part(source_set.stem, 72, fallback="candidate")
        destination = regression_dir / f"regression_{int(row['id']):06d}_{set_label}.set"
        shutil.copy2(source_set, destination)
        if not args.dry_run:
            runtime.remove_report_artifacts(destination)
        original = runtime.variant_from_candidate_row(row)
        copied.append(
            (
                row,
                Variant(
                    path=destination,
                    seed=original.seed,
                    target_symbol=original.target_symbol,
                    target_period=original.target_period,
                    mutated_keys=original.mutated_keys,
                    missing_lot_keys=original.missing_lot_keys,
                    policy=f"{original.policy}+regression_2017_2019",
                    timeframe_keys=original.timeframe_keys,
                    mutation_details=original.mutation_details,
                ),
            )
        )

    print(
        f"Regresiva run #{run_id}: candidatos Final Tick 6M accepted={len(copied)}; "
        f"fechas={args.regression_from_date}->{args.regression_to_date}; Model=1 OHLC"
    )
    print(
        f"Puntos regresiva: OK={float(args.regression_positive_points):+.2f}; "
        f"FAIL base={float(args.regression_negative_points):+.2f} (penalizacion adicional por causa <=60)"
    )
    started_at = time.time()
    code = runtime.run_backtests(
        args,
        regression_dir,
        model="1",
        from_date=args.regression_from_date,
        to_date=args.regression_to_date,
    )
    if code == runtime.running_terminal_exit_code:
        print("ERROR: run_tests.py no ejecuto la prueba regresiva porque hay una terminal MT5 abierta.")
        return 1
    if code != 0:
        print(f"AVISO: prueba regresiva termino con codigo {code}; se evaluaran reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0

    symbol_map = runtime.parse_symbol_map(args.symbol_map)
    status_counts: dict[str, int] = {}
    for row, variant in copied:
        report = runtime.find_report_for_set(variant.path, min_mtime=started_at - 1.0)
        status = evaluate_regression_report(
            memory,
            args,
            runtime,
            score_config,
            symbol_map,
            run_id,
            int(row["id"]),
            variant,
            report,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    print(
        "Regresiva terminada: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0


def rescore_regression_only(
    args: Any,
    memory: AgentMemory,
    score_config: ScoreConfig,
    runtime: RegressionRuntime,
) -> int:
    rows = memory.regression_rows_for_rescore(args.regression_run_id or None)
    if not rows:
        print("Regresiva rescore: no hay filas finales con reporte guardado.")
        return 0
    symbol_map = runtime.parse_symbol_map(args.symbol_map)
    counts: dict[str, int] = {}
    for row in rows:
        report = resolve_workspace_path(row["regression_report_path"])
        variant = runtime.variant_from_candidate_row(row)
        status = evaluate_regression_report(
            memory,
            args,
            runtime,
            score_config,
            symbol_map,
            int(row["run_id"]),
            int(row["id"]),
            variant,
            report if report.exists() else None,
        )
        counts[status] = counts.get(status, 0) + 1
    print(
        "Regresiva rescore terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    )
    return 0
