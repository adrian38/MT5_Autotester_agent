from __future__ import annotations

from dataclasses import dataclass
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
    validate_regression_date_range,
)
from ubs.regression_details import (
    _base_metrics_from_row,
    _details_payload,
    _record_technical,
    _score_config_for_period,
    _watchdog_snapshot_metadata,
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
    # Devuelve "no_report" o el estado terminal cuando el broker ya no ofrece el
    # simbolo: no_report esta en REGRESSION_RETRYABLE_STATUSES y el manager lo
    # reencolaria para siempre. Opcional para no romper runtimes existentes.
    missing_report_status: Callable[[str], str] | None = None
    # Copia de etapa que ademas repara la ortografia MT5 del ForceSymbol y
    # devuelve el nombre exacto. Opcional: sin ella se copia el .set tal cual.
    write_stage_set: Callable[..., str] | None = None


@dataclass
class _RegressionTarget:
    """Candidato concreto cuya regresiva se esta evaluando."""

    memory: AgentMemory
    args: Any
    run_id: int
    candidate_id: int
    variant: Variant

    def technical(self, status: str, report: Path | None, **extra) -> str:
        """Registra un resultado tecnico de la regresiva de este candidato."""
        return _record_technical(
            self.memory,
            self.args,
            candidate_id=self.candidate_id,
            run_id=self.run_id,
            status=status,
            report=report,
            **extra,
        )


def _regression_missing_report(
    target: _RegressionTarget, runtime: RegressionRuntime, watchdog_snapshot: Path | None
) -> str:
    """Sin reporte: lo deja como timeout del watchdog o como falta de reporte."""
    if watchdog_snapshot is not None:
        return target.technical(
            "watchdog_timeout",
            watchdog_snapshot,
            reasons=("watchdog_timeout",),
            metadata=_watchdog_snapshot_metadata(watchdog_snapshot, target.variant),
        )
    status = (
        runtime.missing_report_status(target.variant.target_symbol)
        if runtime.missing_report_status is not None
        else "no_report"
    )
    return target.technical(status, None, reasons=(status,))


def _regression_report_checks(
    target: _RegressionTarget, runtime: RegressionRuntime, symbol_map: dict[str, str],
    report: Path, result: ScoreResult,
) -> str | None:
    """Historico, contexto del tester y correspondencia con la variante."""
    no_history = runtime.tester_log_no_history_metadata(report, target.variant)
    if no_history:
        return target.technical(
            "no_history", report, result=result, reasons=("no_history",), metadata=no_history
        )
    if runtime.report_has_empty_tester_context(result):
        return target.technical(
            "report_mismatch", report, result=result, reasons=("empty_tester_context",)
        )
    matches, mismatch_reason = runtime.report_matches_variant(
        target.variant,
        result,
        symbol_map,
        target.args.symbol_suffix,
    )
    if not matches:
        print(f"AVISO: reporte regresivo no coincide para candidate #{target.candidate_id}: {mismatch_reason}")
        return target.technical(
            "report_mismatch", report, result=result, reasons=("report_mismatch",),
            metadata={"mismatch": mismatch_reason},
        )
    return None


def _regression_dates(
    target: _RegressionTarget, runtime: RegressionRuntime, report: Path, result: ScoreResult
) -> tuple[tuple[str, str] | None, str | None]:
    """Fechas reales del reporte y el estado si no son las pedidas."""
    try:
        actual_dates = runtime.read_report_dates(report)
    except Exception as exc:
        actual_dates = None
        date_error = str(exc)
    else:
        date_error = ""
    expected_dates = (
        str(target.args.regression_from_date).strip(),
        str(target.args.regression_to_date).strip(),
    )
    if actual_dates != expected_dates:
        return actual_dates, target.technical(
            "date_mismatch", report, result=result, reasons=("date_mismatch",),
            actual_dates=actual_dates,
            metadata={"date_error": date_error} if date_error else None,
        )
    return actual_dates, None


def _regression_verdict(
    args: Any, result: ScoreResult, base_metrics: dict[str, object] | None
) -> tuple[str, tuple[str, ...], dict[str, float]]:
    """Veredicto de la regresiva comparada con la ventana de construccion."""
    if result.trades <= 0:
        return "no_trades", tuple(result.reasons), {}
    degradation_reasons, degradation_audit = regression_degradation(
        base_metrics,
        result.profit_factor,
        result.drawdown_pct,
        min_pf_efficiency=float(getattr(args, "regression_min_pf_efficiency", 0.0)),
        max_dd_ratio=float(getattr(args, "regression_max_dd_ratio", 0.0)),
    )
    combined_reasons = tuple(result.reasons) + degradation_reasons
    return ("accepted" if not combined_reasons else "rejected"), combined_reasons, degradation_audit


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
    target = _RegressionTarget(memory, args, run_id, candidate_id, variant)
    if report is None:
        return _regression_missing_report(target, runtime, watchdog_snapshot)
    period_config = _score_config_for_period(score_config, variant.target_period, args)
    try:
        result = score_report_file(report, config=period_config, broker=args.broker)
    except Exception as exc:
        print(f"AVISO: no pude parsear regresiva {report}: {exc}")
        return target.technical(
            "parse_error", report, reasons=("parse_error",), metadata={"error": str(exc)}
        )
    technical = _regression_report_checks(target, runtime, symbol_map, report, result)
    if technical is not None:
        return technical
    actual_dates, technical = _regression_dates(target, runtime, report, result)
    if technical is not None:
        return technical
    status, combined_reasons, degradation_audit = _regression_verdict(args, result, base_metrics)
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
    # Un simbolo que el broker retiro no puede producir reporte: MT5 no abre el
    # tester. Se aparta antes de copiar sets y lanzar backtests, pero hay que
    # grabar el estado terminal o la seleccion (sin fila O retryable) lo volveria
    # a encolar en cada pasada.
    if runtime.missing_report_status is not None:
        kept: list[tuple[sqlite3.Row, Path]] = []
        retired: list[tuple[sqlite3.Row, str]] = []
        for row, path in rows_with_paths:
            status = runtime.missing_report_status(str(row["target_symbol"] or ""))
            if status == "no_report":
                kept.append((row, path))
            else:
                retired.append((row, status))
        rows_with_paths = kept
        for row, status in retired:
            _record_technical(
                memory,
                args,
                candidate_id=int(row["id"]),
                run_id=run_id,
                status=status,
                report=None,
                reasons=(status,),
            )
        if retired:
            symbols = sorted({str(row["target_symbol"] or "") for row, _status in retired})
            print(
                f"Regresiva: {len(retired)} candidato(s) omitidos sin abrir MT5 por simbolo "
                f"retirado del broker ({', '.join(symbols)})."
            )

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
        original = runtime.variant_from_candidate_row(row)
        # Un ForceSymbol mal escrito heredado del .set guardado cierra MT5 sin
        # reporte, y no_report es retryable: la regresiva no avanzaria nunca.
        if runtime.write_stage_set is not None:
            target_symbol = runtime.write_stage_set(
                source_set, destination, False, args, original.target_symbol
            )
        else:
            write_set_use_every_tick(source_set, destination, False)
            target_symbol = original.target_symbol
        if not args.dry_run:
            runtime.remove_report_artifacts(destination)
        copied.append(
            (
                row,
                Variant(
                    path=destination,
                    seed=original.seed,
                    target_symbol=target_symbol,
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
