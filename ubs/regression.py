from __future__ import annotations

from dataclasses import dataclass, replace
from functools import wraps
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable

from ubs.memory import AgentMemory
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.regression_rules import (
    REGRESSION_RETRYABLE_STATUSES,
    regression_degradation,
    regression_points_breakdown,
    validate_regression_date_range,
)
from ubs.score import ScoreConfig, ScoreResult, rescore_result, score_report_file
from ubs.set_utils import compact_safe_part, write_set_use_every_tick


def _batched_memory_updates(function):
    @wraps(function)
    def wrapped(args, memory, score_config, runtime):
        with memory.batch_updates():
            return function(args, memory, score_config, runtime)

    return wrapped


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
    find_watchdog_snapshot_for_set: Callable[..., Path | None] | None = None


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


def _base_metrics_from_row(row: sqlite3.Row | None) -> dict[str, object] | None:
    """Parse the candidate's base-window metrics for degradation comparison."""

    if row is None:
        return None
    try:
        raw = row["metrics_json"]
    except (KeyError, IndexError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _watchdog_snapshot_metadata(snapshot: Path, variant: Variant) -> dict[str, object]:
    metadata: dict[str, object] = {"watchdog_snapshot": str(snapshot)}
    try:
        text = snapshot.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        metadata["watchdog_snapshot_error"] = str(exc)
        return metadata

    symbol = str(variant.target_symbol or "").strip().lower()
    lines = text.splitlines()
    old_tick_lines = sum(
        1
        for line in lines
        if "old tick" in line.lower() and (not symbol or symbol in line.lower())
    )
    gmt_url_error_lines = sum(
        1 for line in lines if "error when reading gmt url" in line.lower()
    )
    if old_tick_lines:
        metadata["history_signal"] = "old_tick_seen"
        metadata["old_tick_lines"] = old_tick_lines
    if gmt_url_error_lines:
        metadata["gmt_url_error_lines"] = gmt_url_error_lines
    return metadata


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
    base_metrics: dict[str, object] | None = None,
    watchdog_snapshot: Path | None = None,
) -> str:
    if report is None:
        if watchdog_snapshot is not None:
            return _record_technical(
                memory,
                args,
                candidate_id=candidate_id,
                run_id=run_id,
                status="watchdog_timeout",
                report=watchdog_snapshot,
                reasons=("watchdog_timeout",),
                metadata=_watchdog_snapshot_metadata(watchdog_snapshot, variant),
            )
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

    if result.trades <= 0:
        status = "no_trades"
        combined_reasons = tuple(result.reasons)
        degradation_audit: dict[str, float] = {}
    else:
        degradation_reasons, degradation_audit = regression_degradation(
            base_metrics,
            result.profit_factor,
            result.drawdown_pct,
            min_pf_efficiency=float(getattr(args, "regression_min_pf_efficiency", 0.0)),
            max_dd_ratio=float(getattr(args, "regression_max_dd_ratio", 0.0)),
        )
        combined_reasons = tuple(result.reasons) + degradation_reasons
        status = "accepted" if not combined_reasons else "rejected"
    details_json, points_applied = _details_payload(
        status,
        result,
        args,
        reasons=combined_reasons,
        actual_dates=actual_dates,
        metadata={"degradation": degradation_audit} if degradation_audit else None,
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
        write_set_use_every_tick(source_set, destination, False)
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
        watchdog_snapshot = None
        if report is None and runtime.find_watchdog_snapshot_for_set is not None:
            watchdog_snapshot = runtime.find_watchdog_snapshot_for_set(
                variant.path,
                min_mtime=started_at - 1.0,
            )
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
            _base_metrics_from_row(row),
            watchdog_snapshot=watchdog_snapshot,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    print(
        "Regresiva terminada: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0


@_batched_memory_updates
def rescore_regression_only(
    args: Any,
    memory: AgentMemory,
    score_config: ScoreConfig,
    runtime: RegressionRuntime,
) -> int:
    if not bool(getattr(args, "rescore_from_reports", False)):
        rows = memory.conn.execute(
            """
            select
                c.id,
                c.run_id,
                c.period,
                c.metrics_json as base_metrics_json,
                rg.status as regression_status,
                rg.report_path as regression_report_path,
                rg.metrics_json as regression_metrics_json,
                rg.details_json as regression_details_json,
                rg.from_date as regression_from_date,
                rg.to_date as regression_to_date
            from candidate_regression rg
            join candidates c on c.id = rg.candidate_id
            where rg.status in ('accepted', 'rejected', 'no_trades')
              and coalesce(rg.metrics_json, '') != ''
              and (? = 0 or c.run_id = ?)
            order by c.run_id, c.generation, c.id
            """,
            (int(args.regression_run_id or 0), int(args.regression_run_id or 0)),
        ).fetchall()
        counts: dict[str, int] = {}
        invalid_metrics = 0
        window_mismatch = 0
        expected_dates = (
            str(args.regression_from_date).strip(),
            str(args.regression_to_date).strip(),
        )
        for row in rows:
            stored_dates = (
                str(row["regression_from_date"] or "").strip(),
                str(row["regression_to_date"] or "").strip(),
            )
            if stored_dates != expected_dates:
                window_mismatch += 1
                continue
            try:
                result = rescore_result(
                    ScoreResult.from_json(str(row["regression_metrics_json"])),
                    _score_config_for_period(score_config, str(row["period"] or ""), args),
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                invalid_metrics += 1
                print(f"AVISO: metrics_json regresiva invalido candidate #{int(row['id'])}: {exc}")
                continue
            try:
                base_metrics = json.loads(str(row["base_metrics_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                base_metrics = None
            if not isinstance(base_metrics, dict):
                base_metrics = None
            if result.trades <= 0:
                status = "no_trades"
                combined_reasons = tuple(result.reasons)
                degradation_audit: dict[str, float] = {}
            else:
                degradation_reasons, degradation_audit = regression_degradation(
                    base_metrics,
                    result.profit_factor,
                    result.drawdown_pct,
                    min_pf_efficiency=float(getattr(args, "regression_min_pf_efficiency", 0.0)),
                    max_dd_ratio=float(getattr(args, "regression_max_dd_ratio", 0.0)),
                )
                combined_reasons = tuple(result.reasons) + degradation_reasons
                status = "accepted" if not combined_reasons else "rejected"
            try:
                previous_details = json.loads(str(row["regression_details_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                previous_details = {}
            actual_dates = None
            if isinstance(previous_details, dict):
                actual_from = str(previous_details.get("actual_from_date") or "").strip()
                actual_to = str(previous_details.get("actual_to_date") or "").strip()
                if actual_from and actual_to:
                    actual_dates = (actual_from, actual_to)
            details_json, points_applied = _details_payload(
                status,
                result,
                args,
                reasons=combined_reasons,
                actual_dates=actual_dates or stored_dates,
                metadata={"degradation": degradation_audit} if degradation_audit else None,
            )
            report_raw = str(row["regression_report_path"] or "").strip()
            memory.record_candidate_regression(
                int(row["id"]),
                int(row["run_id"]),
                status,
                result,
                Path(report_raw) if report_raw else None,
                details_json,
                expected_dates[0],
                expected_dates[1],
                args.regression_positive_points,
                args.regression_negative_points,
                points_applied,
            )
            counts[status] = counts.get(status, 0) + 1
        print(
            "Regresiva repuntuada desde SQLite: "
            + (", ".join(f"{status}={count}" for status, count in sorted(counts.items())) or "sin filas")
            + f"; total={sum(counts.values())}; ventana_distinta={window_mismatch}; invalidos={invalid_metrics}"
        )
        return 0

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
            _base_metrics_from_row(row),
        )
        counts[status] = counts.get(status, 0) + 1
    print(
        "Regresiva rescore terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    )
    return 0
