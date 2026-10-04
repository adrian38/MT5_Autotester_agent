"""Puntuacion de los informes OHLC de cada candidato del Final Tick."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import time

from ubs.score import ScoreResult, score_report_file

from run_tests import RUNNING_TERMINAL_EXIT_CODE
from ubs_agent_evaluate import (
    count_valid_existing_reports,
    prepare_final_tick_exec_dir,
    remove_report_artifacts,
)
from ubs_agent_final_tick import (
    _read_ohlc_report_cfg_dates,
    final_tick_dates_match,
    final_tick_ohlc_retry_exhausted_for_dates,
    final_tick_ohlc_retry_needed_for_dates,
    final_tick_ohlc_trades_pending_payload,
    final_tick_row_pending_for_dates,
    validate_final_tick_stage_dates,
)
from ubs_agent_reports import (
    find_report_for_set,
    report_matches_variant,
)
from ubs_agent_variants import run_backtests
from ubs_agent_universe import (
    min_trades_for_period,
    missing_report_status,
    score_config_for_variant,
)


@dataclass
class _OhlcRecorder:
    """Registro de los estados OHLC de una tanda de final tick."""

    memory: object
    args: object
    run_id: int
    status_counts: dict[str, int]

    def write(
        self, candidate_id: int, status: str, ohlc_result=None,
        ohlc_report: Path | None = None, details: str | None = None,
    ) -> None:
        """Guarda el estado del candidato y lo suma al recuento."""
        args = self.args
        self.memory.record_candidate_final_tick(
            candidate_id, self.run_id, status, ohlc_result, None,
            ohlc_report, None, details, None,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        self.status_counts[status] = self.status_counts.get(status, 0) + 1


def _stored_ohlc_result(row, ohlc_variant, recorder: _OhlcRecorder):
    """Reconstruye el OHLC ya guardado en memoria; None si no se puede."""
    candidate_id = int(row["id"])
    try:
        data = json.loads(str(row["ft_ohlc_metrics_json"]))
        data["reasons"] = tuple(data.get("reasons", []))
        ohlc_result = ScoreResult(**data)
    except Exception as exc:
        print(f"AVISO: no pude reconstruir OHLC metrics para candidate #{candidate_id}: {exc}")
        recorder.write(candidate_id, "parse_error")
        return None
    stored_path = row["ft_ohlc_report_path"]
    return (Path(stored_path) if stored_path else ohlc_variant.path), ohlc_result


def _fresh_ohlc_result(
    args, score_config, symbol_map, row, ohlc_variant, ohlc_min_report_mtime: float,
    default_min_ohlc_trades: int, recorder: _OhlcRecorder,
):
    """Puntua el OHLC recien generado; None si queda resuelto por su estado."""
    candidate_id = int(row["id"])
    ohlc_report = find_report_for_set(ohlc_variant.path, min_mtime=ohlc_min_report_mtime)
    if not ohlc_report:
        recorder.write(
            candidate_id,
            missing_report_status(
                ohlc_variant.target_symbol, args, set_path=ohlc_variant.path,
            ),
            ohlc_report=ohlc_report,
        )
        return None
    try:
        ohlc_result = score_report_file(
            ohlc_report,
            config=score_config_for_variant(
                score_config,
                ohlc_variant,
                min_trades_w1=args.final_tick_min_trades_w1,
                min_trades_mn=args.final_tick_min_trades_mn,
            ),
            broker=args.broker,
        )
    except Exception as exc:
        print(f"AVISO: no pude parsear OHLC Final Tick candidate #{candidate_id}: {exc}")
        recorder.write(candidate_id, "parse_error", ohlc_report=ohlc_report)
        return None
    ohlc_matches, ohlc_mismatch = report_matches_variant(
        ohlc_variant,
        ohlc_result,
        symbol_map,
        args.symbol_suffix,
        args.broker,
    )
    if not ohlc_matches:
        print(f"AVISO: reporte OHLC Final Tick no coincide para candidate #{candidate_id}: {ohlc_mismatch}")
        recorder.write(candidate_id, "report_mismatch", ohlc_result, ohlc_report)
        return None
    min_ohlc_trades = min_trades_for_period(
        ohlc_variant.target_period,
        default_min_ohlc_trades,
        args.final_tick_min_trades_w1,
        args.final_tick_min_trades_mn,
    )
    if ohlc_result.trades < min_ohlc_trades:
        payload = final_tick_ohlc_trades_pending_payload(ohlc_result, min_ohlc_trades)
        recorder.write(
            candidate_id, "pending_ohlc_trades", ohlc_result, ohlc_report,
            details=json.dumps(payload, ensure_ascii=True, sort_keys=True),
        )
        return None
    return ohlc_report, ohlc_result


def collect_final_tick_ohlc_results(
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
) -> None:
    """Rellena los contadores, los listos para tick y los resultados OHLC."""
    recorder = _OhlcRecorder(memory, args, run_id, status_counts)
    for row, ohlc_variant, real_tick_variant in copied:
        if skip_ohlc_flag:
            collected = _stored_ohlc_result(row, ohlc_variant, recorder)
        else:
            collected = _fresh_ohlc_result(
                args, score_config, symbol_map, row, ohlc_variant, ohlc_min_report_mtime,
                default_min_ohlc_trades, recorder,
            )
        if collected is None:
            continue
        ready_for_tick.append((row, ohlc_variant, real_tick_variant))
        ohlc_results[int(row["id"])] = collected


def _final_tick_stored_ohlc_decision(args, copied, final_dir) -> tuple[int | None, bool]:
    """Comprueba que el OHLC guardado en memoria y en disco sirve tal cual."""
    skip_ohlc = False
    missing_metrics = [int(row["id"]) for row, _, _ in copied if not row["ft_ohlc_metrics_json"]]
    if missing_metrics:
        ids = ", ".join(f"#{i}" for i in missing_metrics)
        print(f"ERROR: --final-tick-skip-ohlc pero faltan ohlc_metrics_json para candidates {ids}.")
        return 1, skip_ohlc
    # Read the configured dates directly from the OHLC report file (ground truth).
    # The DB's from_date/to_date can be stale if a previous tick retry used a
    # different UI date and overwrote the stored value.
    first_ohlc_path_str = str(copied[0][0]["ft_ohlc_report_path"] or "")
    if first_ohlc_path_str:
        ohlc_cfg_from, ohlc_cfg_to = _read_ohlc_report_cfg_dates(Path(first_ohlc_path_str))
    else:
        ohlc_cfg_from, ohlc_cfg_to = "", ""
    if ohlc_cfg_from and ohlc_cfg_to:
        if ohlc_cfg_from != str(args.from_date or "").strip() or ohlc_cfg_to != str(args.to_date or "").strip():
            print(
                f"Final Tick skip-ohlc: fechas leidas del reporte OHLC "
                f"{ohlc_cfg_from} -> {ohlc_cfg_to} "
                f"(UI: {args.from_date} -> {args.to_date}); se usan las del reporte."
            )
        args.from_date = ohlc_cfg_from
        args.to_date = ohlc_cfg_to
    else:
        print(
            f"AVISO: no se pudo leer fecha del reporte OHLC '{first_ohlc_path_str}'; "
            f"se usa fecha del UI ({args.from_date} -> {args.to_date})."
        )
    skip_ohlc = True
    print(
        f"Final Tick skip-ohlc: usando OHLC guardado en DB; "
        f"solo se ejecuta Every Tick ({len(copied)} candidatos)."
    )
    return None, skip_ohlc


def _final_tick_skip_ohlc_decision(
    args, score_config, symbol_map, copied, ohlc_variants, final_dir,
    skip_ohlc_flag, ohlc_sets_unchanged, stored_dates_match, resume_pending_dir,
):
    """Decide si el tramo OHLC se puede reutilizar tal cual esta en disco."""
    skip_ohlc = False
    if skip_ohlc_flag:
        stored_code, skip_ohlc = _final_tick_stored_ohlc_decision(args, copied, final_dir)
        if stored_code is not None:
            return stored_code, skip_ohlc
    elif resume_pending_dir and ohlc_sets_unchanged and stored_dates_match:
        valid_ohlc_reports = count_valid_existing_reports(
            ohlc_variants,
            score_config,
            symbol_map,
            args.broker,
            min_trades_w1=args.final_tick_min_trades_w1,
            min_trades_mn=args.final_tick_min_trades_mn,
            symbol_suffix=args.symbol_suffix,
        )
        if valid_ohlc_reports == len(ohlc_variants):
            # Verify the OHLC reports on disk have the expected dates.
            # A previous interrupted run may have overwritten the files with different dates
            # without updating the DB, so stored_dates_match=True is insufficient.
            first_rep = find_report_for_set(ohlc_variants[0].path, min_mtime=None) if ohlc_variants else None
            ohlc_disk_from, ohlc_disk_to = _read_ohlc_report_cfg_dates(first_rep) if first_rep else ("", "")
            if ohlc_disk_from and ohlc_disk_from != str(args.from_date or "").strip():
                print(
                    f"Final Tick resume: reporte OHLC en disco tiene fechas "
                    f"{ohlc_disk_from} -> {ohlc_disk_to} pero UI={args.from_date} -> {args.to_date}; "
                    f"se recalcula OHLC."
                )
                if not args.dry_run:
                    for variant in ohlc_variants:
                        remove_report_artifacts(variant.path)
            else:
                skip_ohlc = True
                print(
                    "Final Tick resume: OHLC existente completo; "
                    "se salta OHLC y se continua con Every Tick."
                )
        else:
            print(
                "Final Tick resume: OHLC incompleto "
                f"({valid_ohlc_reports}/{len(ohlc_variants)} reportes validos); se recalcula OHLC."
            )
            if not args.dry_run:
                for variant in ohlc_variants:
                    remove_report_artifacts(variant.path)
    elif resume_pending_dir:
        if not stored_dates_match:
            print("Final Tick resume: las fechas cambiaron; se recalcula OHLC.")
        else:
            print("Final Tick resume: los .set OHLC faltan o cambiaron; se recalcula OHLC.")
        if not args.dry_run:
            for variant in ohlc_variants:
                remove_report_artifacts(variant.path)
    return None, skip_ohlc


def _final_tick_run_ohlc(
    args, score_config, symbol_map, copied, ohlc_variants, ohlc_dir, final_dir,
    skip_ohlc_flag, ohlc_sets_unchanged, stored_dates_match, resume_pending_dir,
):
    """Decide si hay que relanzar el tramo OHLC y lo ejecuta."""
    skip_ohlc = False
    ohlc_min_report_mtime: float | None = None
    skip_code, skip_ohlc = _final_tick_skip_ohlc_decision(
        args, score_config, symbol_map, copied, ohlc_variants, final_dir,
        skip_ohlc_flag, ohlc_sets_unchanged, stored_dates_match, resume_pending_dir,
    )
    if skip_code is not None:
        return skip_code, skip_ohlc, ohlc_min_report_mtime

    if not skip_ohlc:
        ohlc_backtest_dir = ohlc_dir
        if args.final_tick_pending_only:
            ohlc_backtest_dir = prepare_final_tick_exec_dir(final_dir / "_pending_ohlc_sets", ohlc_variants)
            print(f"Final Tick pending: OHLC en cola={len(ohlc_variants)} set(s).")
        ohlc_started_at = time.time()
        ohlc_min_report_mtime = ohlc_started_at - 1.0
        ohlc_code = run_backtests(args, ohlc_backtest_dir, model="1")
        if ohlc_code == RUNNING_TERMINAL_EXIT_CODE:
            print("ERROR: run_tests.py no ejecuto OHLC Final Tick porque hay una terminal MT5 abierta.")
            return 1, skip_ohlc, ohlc_min_report_mtime
        if ohlc_code != 0:
            print(f"AVISO: OHLC Final Tick termino con codigo {ohlc_code}; se evaluaran reportes disponibles")
            if args.dry_run:
                return ohlc_code, skip_ohlc, ohlc_min_report_mtime

    if args.dry_run:
        return 0, skip_ohlc, ohlc_min_report_mtime
    return None, skip_ohlc, ohlc_min_report_mtime


def _final_tick_filter_pending(
    args, rows, deferred_out, main_from_date, main_to_date,
    ohlc_retry_from, ohlc_retry_to, final_tick_stage, final_tick_label,
    using_ohlc_retry_dates, retry_pending_quality,
    _row_uses_retry_dates, _row_in_retry_scope,
):
    """Aplica el filtro de pendientes y aparta las filas del otro tramo."""
    if using_ohlc_retry_dates:
        deferred_main_rows = [
            row for row in rows
            if not _row_in_retry_scope(row)
            and final_tick_row_pending_for_dates(
                row,
                main_from_date,
                main_to_date,
                final_tick_stage=final_tick_stage,
                force_quality_retry=retry_pending_quality,
            )
        ]
        rows = [
            row for row in rows
            if _row_in_retry_scope(row)
            and final_tick_row_pending_for_dates(
                row,
                args.from_date,
                args.to_date,
                final_tick_stage=final_tick_stage,
                force_quality_retry=retry_pending_quality,
            )
        ]
        if deferred_main_rows:
            print(
                f"{final_tick_label} OHLC retry: "
                f"{len(deferred_main_rows)} fila(s) pendientes de fechas principales "
                f"{main_from_date} -> {main_to_date} se dejan para la siguiente continuacion."
            )
            if deferred_out is not None:
                deferred_out.extend(deferred_main_rows)
    else:
        rows = [
            row for row in rows
            if final_tick_row_pending_for_dates(
                row,
                args.from_date,
                args.to_date,
                final_tick_stage=final_tick_stage,
                force_quality_retry=retry_pending_quality,
            )
            and not (
                final_tick_stage == "six_month"
                and final_tick_ohlc_retry_exhausted_for_dates(row, ohlc_retry_from, ohlc_retry_to)
            )
        ]
    return rows


def _final_tick_retry_dates(
    args, rows, final_tick_stage, final_tick_label, allow_ohlc_retry,
    ohlc_retry_from, ohlc_retry_to, retry_pending_quality, _row_uses_retry_dates,
) -> tuple[int | None, bool]:
    """Decide si la pasada usa las fechas alternativas de reintento OHLC."""
    has_ohlc_trades_pending = False
    if ohlc_retry_from and ohlc_retry_to:
        has_ohlc_trades_pending = final_tick_ohlc_retry_needed_for_dates(
            rows,
            ohlc_retry_from,
            ohlc_retry_to,
            final_tick_stage=final_tick_stage,
            force_quality_retry=retry_pending_quality,
        )
    else:
        has_ohlc_trades_pending = any(
            str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades"
            for row in rows
        )
    using_ohlc_retry_dates = False
    if allow_ohlc_retry and final_tick_stage == "six_month" and args.final_tick_pending_only and has_ohlc_trades_pending and (ohlc_retry_from or ohlc_retry_to):
        if not ohlc_retry_from or not ohlc_retry_to:
            print(f"ERROR: {final_tick_label} OHLC retry requiere ambas fechas alternativas Desde y Hasta.")
            return 1, False
        args.from_date = ohlc_retry_from
        args.to_date = ohlc_retry_to
        date_error = validate_final_tick_stage_dates(final_tick_stage, str(args.from_date or ""), str(args.to_date or ""))
        if date_error:
            print(f"ERROR: {date_error}")
            return 1, False
        using_ohlc_retry_dates = True
        print(f"{final_tick_label} OHLC retry: usando fechas alternativas {args.from_date} -> {args.to_date}.")
    return None, using_ohlc_retry_dates


def _final_tick_pending_rows(
    args, rows, run_id, final_tick_stage, final_tick_label, deferred_out,
    main_from_date, main_to_date, ohlc_retry_from, ohlc_retry_to,
    using_ohlc_retry_dates, retry_pending_quality,
    _row_uses_retry_dates, _row_in_retry_scope,
):
    """Deja solo las filas pendientes del alcance elegido."""
    if args.final_tick_pending_only:
        rows = _final_tick_filter_pending(
            args, rows, deferred_out, main_from_date, main_to_date,
            ohlc_retry_from, ohlc_retry_to, final_tick_stage, final_tick_label,
            using_ohlc_retry_dates, retry_pending_quality,
            _row_uses_retry_dates, _row_in_retry_scope,
        )
    if not rows:
        if args.final_tick_pending_only:
            print(f"{final_tick_label} run #{run_id}: no hay candidatos pendientes.")
        else:
            print(f"{final_tick_label} run #{run_id}: no hay candidatos elegibles con .set existente.")
        return 0, rows, False
    stored_dates_match = all(
        not str(row["final_tick_status"] or "").strip()
        or final_tick_dates_match(row, args.from_date, args.to_date)
        for row in rows
    )
    return None, rows, stored_dates_match


def _final_tick_rows_in_scope(
    args, rows, run_id, final_tick_stage, final_tick_label, allow_ohlc_retry, deferred_out,
):
    """Filtra las filas de la pasada y decide si se usan las fechas alternativas."""
    retry_pending_quality = bool(getattr(args, "final_tick_retry_pending_quality", False))
    if args.final_tick_pending_only and retry_pending_quality:
        # La etapa de calidad va siempre con ``--final-tick-skip-ohlc``, que solo
        # puede servir filas con su pata OHLC ya guardada en DB. El manager
        # decide si la lanza contando exactamente eso
        # (``pipeline_stage_pending_count``, rama ``quality_only``), asi que la
        # seleccion se restringe al mismo conjunto en vez de ampliarlo: el resto
        # de pendientes son trabajo de la etapa normal, que corre justo antes.
        rows = [
            row for row in rows
            if str(row["final_tick_status"] or "").strip() == "pending_history_quality"
        ]
    main_from_date = str(args.from_date or "").strip()
    main_to_date = str(args.to_date or "").strip()
    ohlc_retry_from = str(getattr(args, "final_tick_ohlc_from_date", "") or "").strip()
    ohlc_retry_to = str(getattr(args, "final_tick_ohlc_to_date", "") or "").strip()
    def _row_uses_retry_dates(row: sqlite3.Row) -> bool:
        # Una fila que ya quedo registrada con el rango retry OHLC (p. ej. un
        # mismatch ocurrido durante un retry) debe re-ejecutarse con ese mismo
        # rango, no con las fechas principales.
        if not ohlc_retry_from or not ohlc_retry_to:
            return False
        return (
            str(row["final_tick_from_date"] or "").strip() == ohlc_retry_from
            and str(row["final_tick_to_date"] or "").strip() == ohlc_retry_to
        )

    def _row_in_retry_scope(row: sqlite3.Row) -> bool:
        return (
            str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades"
            or _row_uses_retry_dates(row)
        )

    retry_code, using_ohlc_retry_dates = _final_tick_retry_dates(
        args, rows, final_tick_stage, final_tick_label, allow_ohlc_retry,
        ohlc_retry_from, ohlc_retry_to, retry_pending_quality, _row_uses_retry_dates,
    )
    if retry_code is not None:
        return retry_code, rows, False, False
    pending_code, rows, stored_dates_match = _final_tick_pending_rows(
        args, rows, run_id, final_tick_stage, final_tick_label, deferred_out,
        main_from_date, main_to_date, ohlc_retry_from, ohlc_retry_to,
        using_ohlc_retry_dates, retry_pending_quality,
        _row_uses_retry_dates, _row_in_retry_scope,
    )
    if pending_code is not None:
        return pending_code, rows, using_ohlc_retry_dates, stored_dates_match
    return None, rows, using_ohlc_retry_dates, stored_dates_match
