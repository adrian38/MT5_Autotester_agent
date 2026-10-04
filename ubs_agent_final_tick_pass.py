"""Pasada completa del Final Tick sobre un candidato."""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs_agent_final_tick_ohlc import (
    _final_tick_rows_in_scope,
    _final_tick_run_ohlc,
    collect_final_tick_ohlc_results,
)
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult
from ubs.set_utils import set_matches_use_every_tick_source
from ubs_agent_config import (
    SYMBOL_NOT_EXIST_STATUS,
)
from ubs_agent_evaluate import (
    count_valid_existing_reports,
    prepare_final_tick_exec_dir,
    recreate_work_dir,
    remove_report_artifacts,
)
from ubs_agent_final_tick import (
    _evaluate_final_tick_tick_report,
    _read_ohlc_report_cfg_dates,
    final_tick_dates_match,
    final_tick_ohlc_retry_exhausted_for_dates,
    final_tick_ohlc_retry_needed_for_dates,
    final_tick_row_pending_for_dates,
    final_tick_stage_dir_name,
    final_tick_stage_label,
    final_tick_stage_prefixes,
    normalize_final_tick_stage,
    validate_final_tick_stage_dates,
)
from ubs_agent_final_tick_rescore import (
    reconcile_final_tick_reports,
)
from ubs_agent_reports import find_report_for_set
from ubs_agent_sets import (
    write_retry_set,
)
from ubs_agent_universe import (
    format_retired_symbol_rows,
    missing_report_status,
    split_retired_symbols,
)
from ubs_agent_variants import (
    run_backtests,
)


def _final_tick_reconcile_only(args, memory, score_config, final_tick_stage, final_tick_label) -> int:
    """Reconcilia el Final Tick desde los reportes en disco, sin abrir MT5."""
    run = memory.run_by_id(args.final_tick_run_id) if args.final_tick_run_id else memory.latest_run()
    if run is None:
        print(f"ERROR: no hay run SQLite disponible para {final_tick_label}")
        return 1
    counts = reconcile_final_tick_reports(
        memory,
        int(run["id"]),
        score_config,
        parse_symbol_map(args.symbol_map),
        broker=args.broker,
        final_tick_stage=final_tick_stage,
        min_history_quality=args.final_tick_min_history_quality,
        min_ohlc_trades=args.final_tick_min_ohlc_trades,
        min_trades_w1=args.final_tick_min_trades_w1,
        min_trades_mn=args.final_tick_min_trades_mn,
        max_net_delta_pct=args.final_tick_max_net_delta_pct,
        max_pf_delta_pct=args.final_tick_max_pf_delta_pct,
        max_dd_delta_pct=args.final_tick_max_dd_delta_pct,
        max_trades_delta_pct=args.final_tick_max_trades_delta_pct,
        symbol_suffix=args.symbol_suffix,
    )
    if counts:
        print(
            f"{final_tick_label} reconciliado desde disco: "
            + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
        )
    else:
        print(f"{final_tick_label} reconcile: no hay reportes en disco utilizables para filas pendientes.")
    print(f"Memoria: {memory.path}")
    return 0


def _final_tick_argument_errors(args, final_tick_stage, final_tick_label) -> int | None:
    """Comprueba experto, terminal y fechas antes de tocar la memoria."""
    if not args.expert and not args.multi_terminal and not args.dry_run:
        print(f"ERROR: {final_tick_label} requiere --expert o --multi-terminal")
        return 1
    if not str(args.from_date or "").strip() or not str(args.to_date or "").strip():
        print(f"ERROR: {final_tick_label} requiere --from-date y --to-date para comparar el mismo tramo OHLC vs real tick.")
        return 1
    date_error = validate_final_tick_stage_dates(final_tick_stage, str(args.from_date or ""), str(args.to_date or ""))
    if date_error:
        print(f"ERROR: {date_error}")
        return 1
    return None


def _report_retired_final_tick_rows(args, memory, retired_rows, run_id, final_tick_label) -> None:
    """Marca y anuncia los candidatos cuyo simbolo ya no ofrece el broker."""
    for row in retired_rows:
        memory.record_candidate_final_tick(
            int(row["id"]),
            run_id,
            SYMBOL_NOT_EXIST_STATUS,
            None,
            None,
            None,
            None,
            None,
            None,
            args.final_tick_min_history_quality,
            args.from_date,
            args.to_date,
            args.final_tick_max_net_delta_pct,
            args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct,
            args.final_tick_max_trades_delta_pct,
        )
    if retired_rows:
        print(
            f"{final_tick_label}: {len(retired_rows)} candidato(s) omitidos sin abrir MT5 por "
            f"simbolo retirado del broker ({format_retired_symbol_rows(retired_rows)}); "
            f"marcados {SYMBOL_NOT_EXIST_STATUS}."
        )


def _copy_final_tick_sets(
    args, rows, ohlc_dir, real_tick_dir, final_dir,
    final_tick_stage, final_tick_label, using_ohlc_retry_dates, resume_pending_dir,
):
    """Copia cada candidato a su .set OHLC y Real Tick de la pasada."""
    copied: list[tuple[sqlite3.Row, Variant, Variant]] = []
    used_names: set[str] = set()
    ohlc_sets_unchanged = True
    ohlc_prefix, tick_prefix = final_tick_stage_prefixes(final_tick_stage, ohlc_retry=using_ohlc_retry_dates)
    for row in rows:
        candidate_id = int(row["id"])
        source_set = resolve_workspace_path(row["set_path"])
        ohlc_name = f"{ohlc_prefix}_{candidate_id:06d}_{source_set.name}"
        real_tick_name = f"{tick_prefix}_{candidate_id:06d}_{source_set.name}"
        if ohlc_name in used_names or real_tick_name in used_names:
            print(f"ERROR: nombre duplicado en {final_tick_label} para candidate #{candidate_id}")
            return 1
        used_names.update({ohlc_name, real_tick_name})

        ohlc_set = ohlc_dir / ohlc_name
        real_tick_set = real_tick_dir / real_tick_name
        if resume_pending_dir and not set_matches_use_every_tick_source(
            source_set,
            ohlc_set,
            False,
        ):
            ohlc_sets_unchanged = False
        original_variant = variant_from_candidate_row(row)
        # Las dos copias salen del mismo .set guardado, asi que ambas heredan
        # un ForceSymbol mal escrito; sin reparar, MT5 cierra sin reporte.
        exact_symbol = write_retry_set(
            source_set, ohlc_set, False, args, original_variant.target_symbol
        )
        write_retry_set(source_set, real_tick_set, True, args, original_variant.target_symbol)
        if not args.dry_run:
            remove_report_artifacts(real_tick_set)
            if not resume_pending_dir:
                remove_report_artifacts(ohlc_set)

        ohlc_variant = Variant(
            path=ohlc_set,
            seed=original_variant.seed,
            target_symbol=exact_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+{final_tick_stage_dir_name(final_tick_stage)}_ohlc",
        )
        real_tick_variant = Variant(
            path=real_tick_set,
            seed=original_variant.seed,
            target_symbol=exact_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+{final_tick_stage_dir_name(final_tick_stage)}_real",
        )
        copied.append((row, ohlc_variant, real_tick_variant))
    return copied, ohlc_sets_unchanged


def _print_final_tick_header(args, final_tick_label, run_id, mode, copied, final_dir) -> None:
    """Cabecera con el modo, el directorio, las fechas y los criterios."""
    print(f"{final_tick_label} run #{run_id}: modo={mode}; candidatos={len(copied)}")
    print(f"Directorio {final_tick_label}: {final_dir}")
    print(f"Fechas {final_tick_label}: {args.from_date or '(template)'} -> {args.to_date or '(template)'}")
    print(
        f"Criterios {final_tick_label}: "
        f"History Quality>={args.final_tick_min_history_quality:.2f}% | "
        f"Net delta<={args.final_tick_max_net_delta_pct:.2f}% | "
        f"PF delta<={args.final_tick_max_pf_delta_pct:.2f}% | "
        f"DD delta<={args.final_tick_max_dd_delta_pct:.2f}% | "
        f"Trades delta<={args.final_tick_max_trades_delta_pct:.2f}%"
    )


def _evaluate_final_tick_rows(
    args, memory, score_config, symbol_map, run_id,
    ready_for_tick, ohlc_results, real_tick_min_report_mtime, status_counts,
) -> None:
    """Puntua el informe Real Tick de cada candidato listo y guarda su estado."""
    for row, ohlc_variant, real_tick_variant in ready_for_tick:
        candidate_id = int(row["id"])
        ohlc_report, ohlc_result = ohlc_results[candidate_id]
        real_tick_report = find_report_for_set(real_tick_variant.path, min_mtime=real_tick_min_report_mtime)
        if not real_tick_report:
            status = missing_report_status(
                real_tick_variant.target_symbol, args,
                set_path=real_tick_variant.path,
            )
            memory.record_candidate_final_tick(
                candidate_id,
                run_id,
                status,
                ohlc_result,
                None,
                ohlc_report,
                None,
                None,
                None,
                args.final_tick_min_history_quality,
                args.from_date,
                args.to_date,
                args.final_tick_max_net_delta_pct,
                args.final_tick_max_pf_delta_pct,
                args.final_tick_max_dd_delta_pct,
                args.final_tick_max_trades_delta_pct,
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            continue

        _evaluate_final_tick_tick_report(
            memory,
            args,
            score_config,
            symbol_map,
            run_id,
            candidate_id,
            real_tick_variant,
            ohlc_report,
            ohlc_result,
            real_tick_report,
            status_counts,
        )


def _final_tick_stage_dirs(args, run_dir, run_id, final_tick_stage, final_tick_label):
    """Crea el directorio de la pasada y sus dos subcarpetas de .set."""
    mode = "pending" if args.final_tick_pending_only else "all"
    final_dir = run_dir / final_tick_stage_dir_name(final_tick_stage) / f"run_{run_id}_{mode}"
    resume_pending_dir = args.final_tick_pending_only and final_dir.exists()
    if resume_pending_dir:
        if not final_dir.is_dir():
            raise NotADirectoryError(final_dir)
        final_dir.mkdir(parents=True, exist_ok=True)
    else:
        final_dir = recreate_work_dir(final_dir)
    ohlc_dir = final_dir / "ohlc_sets"
    real_tick_dir = final_dir / "real_tick_sets"
    ohlc_dir.mkdir(parents=True, exist_ok=True)
    real_tick_dir.mkdir(parents=True, exist_ok=True)
    return mode, final_dir, resume_pending_dir, ohlc_dir, real_tick_dir


def _run_final_tick_real_tick(
    args, memory, score_config, symbol_map, run_id,
    ready_for_tick, ohlc_results, real_tick_dir, final_dir, status_counts,
):
    """Lanza el tramo Real Tick y puntua sus informes."""
    ready_real_tick_variants = [real_tick_variant for _, _, real_tick_variant in ready_for_tick]
    real_tick_backtest_dir = real_tick_dir
    if args.final_tick_pending_only:
        real_tick_backtest_dir = prepare_final_tick_exec_dir(final_dir / "_pending_real_tick_sets", ready_real_tick_variants)
        print(f"Final Tick pending: Every Tick en cola={len(ready_real_tick_variants)} set(s).")
    real_tick_started_at = time.time()
    real_tick_min_report_mtime = real_tick_started_at - 1.0
    real_tick_code = run_backtests(args, real_tick_backtest_dir, model="4")
    if real_tick_code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto Real Tick Final Tick porque hay una terminal MT5 abierta.")
        return 1
    if real_tick_code != 0:
        print(f"AVISO: Real Tick Final Tick termino con codigo {real_tick_code}; se evaluaran reportes disponibles")

    _evaluate_final_tick_rows(
        args, memory, score_config, symbol_map, run_id,
        ready_for_tick, ohlc_results, real_tick_min_report_mtime, status_counts,
    )
    return None


def _final_tick_run_stages(
    args, memory, score_config, run_id, final_tick_label,
    copied, final_dir, ohlc_dir, real_tick_dir, ohlc_sets_unchanged,
    stored_dates_match, resume_pending_dir, final_tick_stage,
) -> int:
    """Ejecuta el tramo OHLC, reaprovecha lo que haya y corre el Real Tick."""
    symbol_map = parse_symbol_map(args.symbol_map)
    ohlc_variants = [ohlc_variant for _, ohlc_variant, _ in copied]
    real_tick_variants = [real_tick_variant for _, _, real_tick_variant in copied]
    skip_ohlc_flag = bool(getattr(args, "final_tick_skip_ohlc", False))
    ohlc_code, skip_ohlc, ohlc_min_report_mtime = _final_tick_run_ohlc(
        args, score_config, symbol_map, copied, ohlc_variants, ohlc_dir, final_dir,
        skip_ohlc_flag, ohlc_sets_unchanged, stored_dates_match, resume_pending_dir,
    )
    if ohlc_code is not None:
        return ohlc_code

    status_counts: dict[str, int] = {}
    ready_for_tick: list[tuple[sqlite3.Row, Variant, Variant]] = []
    ohlc_results: dict[int, tuple[Path, ScoreResult]] = {}
    default_min_ohlc_trades = max(0, int(args.final_tick_min_ohlc_trades))

    collect_final_tick_ohlc_results(
        args,
        memory,
        score_config,
        copied,
        symbol_map,
        run_id,
        skip_ohlc_flag,
        ohlc_min_report_mtime,
        default_min_ohlc_trades,
        status_counts,
        ready_for_tick,
        ohlc_results,
    )

    resume_code, ready_for_tick = _final_tick_resume_reconcile(
        args, memory, score_config, symbol_map, run_id, final_tick_label,
        final_tick_stage, resume_pending_dir, ready_for_tick, ohlc_results,
        status_counts,
    )
    if resume_code is not None:
        return resume_code

    real_tick_code = _run_final_tick_real_tick(
        args, memory, score_config, symbol_map, run_id,
        ready_for_tick, ohlc_results, real_tick_dir, final_dir, status_counts,
    )
    if real_tick_code is not None:
        return real_tick_code

    print(
        "Final Tick terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0


def _final_tick_resume_reconcile(
    args, memory, score_config, symbol_map, run_id, final_tick_label,
    final_tick_stage, resume_pending_dir, ready_for_tick, ohlc_results,
    status_counts,
):
    """Aprovecha los informes Real Tick ya en disco antes de relanzar MT5."""
    reconciled_tick = 0
    if resume_pending_dir and ready_for_tick:
        # Reconciliar reportes Real Tick ya existentes en disco (p. ej. de un
        # proceso interrumpido manualmente) antes de relanzar MT5. Solo se
        # reutiliza un reporte si su rango de fechas coincide con el del OHLC
        # de control y el symbol/TF es el esperado.
        still_pending = []
        for row, ohlc_variant, real_tick_variant in ready_for_tick:
            candidate_id = int(row["id"])
            ohlc_report, ohlc_result = ohlc_results[candidate_id]
            existing_tick = find_report_for_set(real_tick_variant.path)
            reused = False
            if existing_tick is not None:
                tick_dates = _read_ohlc_report_cfg_dates(existing_tick)
                ohlc_dates = _read_ohlc_report_cfg_dates(Path(ohlc_report))
                if tick_dates[0] and tick_dates == ohlc_dates:
                    reused = _evaluate_final_tick_tick_report(
                        memory, args, score_config, symbol_map, run_id,
                        candidate_id, real_tick_variant, ohlc_report, ohlc_result,
                        existing_tick, status_counts, reconcile=True,
                    )
            if reused:
                reconciled_tick += 1
                print(f"Final Tick resume: reporte Real Tick existente reutilizado para candidate #{candidate_id}.")
            else:
                still_pending.append((row, ohlc_variant, real_tick_variant))
        ready_for_tick = still_pending

    if not ready_for_tick:
        if reconciled_tick:
            print(f"Final Tick: {reconciled_tick} reporte(s) Real Tick reconciliados desde disco; no hay nada que ejecutar.")
        else:
            print("Final Tick: ningun OHLC cumple el minimo de operaciones; no se lanza Every Tick.")
        print(
            "Final Tick terminado: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; memoria={memory.path}"
        )
        return 0, ready_for_tick
    return None, ready_for_tick


def _evaluate_candidate_final_tick_pass(
    args: argparse.Namespace,
    memory: AgentMemory,
    score_config: ScoreConfig,
    *,
    allow_ohlc_retry: bool = True,
    deferred_out: list[sqlite3.Row] | None = None,
) -> int:
    final_tick_stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    memory.active_final_tick_stage = final_tick_stage
    final_tick_label = final_tick_stage_label(final_tick_stage)
    if getattr(args, "final_tick_reconcile_only", False):
        return _final_tick_reconcile_only(
            args, memory, score_config, final_tick_stage, final_tick_label,
        )
    argument_error = _final_tick_argument_errors(args, final_tick_stage, final_tick_label)
    if argument_error is not None:
        return argument_error

    run = memory.run_by_id(args.final_tick_run_id) if args.final_tick_run_id else memory.latest_run()
    if run is None:
        print(f"ERROR: no hay run SQLite disponible para {final_tick_label}")
        return 1

    run_id = int(run["id"])
    run_dir = resolve_workspace_path(run["output_dir"])
    rows = [
        row
        for row in memory.accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
        if resolve_workspace_path(row["set_path"]).exists()
    ]
    rows, retired_rows = split_retired_symbols(rows, args)
    _report_retired_final_tick_rows(args, memory, retired_rows, run_id, final_tick_label)
    scope_code, rows, using_ohlc_retry_dates, stored_dates_match = _final_tick_rows_in_scope(
        args, rows, run_id, final_tick_stage, final_tick_label, allow_ohlc_retry, deferred_out,
    )
    if scope_code is not None:
        return scope_code

    mode, final_dir, resume_pending_dir, ohlc_dir, real_tick_dir = _final_tick_stage_dirs(
        args, run_dir, run_id, final_tick_stage, final_tick_label,
    )

    copied, ohlc_sets_unchanged = _copy_final_tick_sets(
        args, rows, ohlc_dir, real_tick_dir, final_dir,
        final_tick_stage, final_tick_label, using_ohlc_retry_dates, resume_pending_dir,
    )

    _print_final_tick_header(args, final_tick_label, run_id, mode, copied, final_dir)

    return _final_tick_run_stages(
        args, memory, score_config, run_id, final_tick_label,
        copied, final_dir, ohlc_dir, real_tick_dir, ohlc_sets_unchanged,
        stored_dates_match, resume_pending_dir, final_tick_stage,
    )


