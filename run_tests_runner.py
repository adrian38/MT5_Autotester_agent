"""Lanzamiento, vigilancia y paralelizacion de los backtests de MT5."""
from __future__ import annotations

import os
import queue
import re
import sys
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_tests_base import (
    MODEL4_NO_HISTORY_EXIT_CODE,
    MODEL4_NO_HISTORY_RETRY_DELAY_SECONDS,
    NO_WINDOW,
    REPORT_SAVE_CHECK_INTERVAL,
    REPORT_SAVE_STALL_SECONDS,
    RUNNING_TERMINAL_EXIT_CODE,
    SKIPPED_SYMBOL_EXIT_CODE,
    TESTER_STUCK_MARKERS,
    BacktestJob,
    TerminalProfile,
    TesterSettings,
)
from run_tests_experts import (
    copy_set_file_to_tester_profiles,
    create_ini,
    job_uses_profile_ubs_expert,
    profile_expert_for_job,
)
from run_tests_logging import (
    _WATCHDOG_RESTART_LIMITER,
    _WATCHDOG_TERMINATE_LIMITER,
)
from run_tests_reports import (
    copy_reports_to_project,
    delete_test_artifacts,
    delete_existing_report_files,
    filter_fresh_report_files,
    find_report_files,
    find_tester_journal_log,
    finish_model4_history_preflight,
    fresh_report_signature,
    ini_symbol_missing_from_universe,
    install_dir_reports,
    load_universe_symbols,
    log_ini_content,
    model4_report_has_empty_tester_data,
    mt5_tester_abort,
    prepare_model4_history_preflight,
    read_tester_journal_tail,
    read_tester_journal_tail_text,
    signed_exit_code,
    tester_model_from_ini,
    tester_symbol_from_ini,
    write_tester_journal_sidecars,
    write_tester_journal_snapshot,
)
from run_tests_watchdog import wait_for_mt5_process
from run_tests_terminals import (
    TerminalStillRunningError,
    log_runner_diagnostics,
    settings_from_profile,
    terminal_data_dirs_for_profile,
    wait_for_terminal_release,
)


def quote_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def run_test(
    ini_path: Path,
    report_path: Path,
    settings: TesterSettings,
    dry_run: bool,
    logger: RunLogger,
    terminal_data_dirs: list[Path],
    protected_set_name: str = "",
) -> int:
    command = [str(settings.mt5_path)]
    if settings.portable:
        command.append("/portable")
    command.append(f"/config:{ini_path}")
    log_ini_content(ini_path, logger, [
        "",
        f"Config: {ini_path}",
        f"Reporte esperado: {report_path}.*",
        f"Comando: {quote_command(command)}",
    ])

    if dry_run:
        return 0

    tester_model = tester_model_from_ini(ini_path)
    real_tick_model = tester_model == "4"
    kick_after_seconds = settings.tester_kick_after_seconds if real_tick_model else 0
    stall_after_seconds = settings.tester_stall_after_seconds
    max_runtime_seconds = settings.tester_max_runtime_seconds
    # MT5 can exit without producing a fresh report for every tester model.  It
    # happens independently of the watchdog (for example when a terminal
    # silently ignores /config after a previous job).  Reserve one retry for
    # that false-success path as well as for Model=4 empty history and watchdog
    # restarts.
    max_attempts = 2
    last_exit_code = 1
    retry_rate_limit_label = ""

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            logger.write(f"Reintentando MT5 Model={tester_model or 'desconocido'} ({attempt}/{max_attempts}).")
            if retry_rate_limit_label:
                _WATCHDOG_RESTART_LIMITER.wait_for_turn(logger, retry_rate_limit_label)
        retry_rate_limit_label = ""
        delete_existing_report_files(
            report_path,
            terminal_data_dirs,
            settings.mt5_path,
            logger,
            protected_set_name=protected_set_name,
        )

        history_rotations = (
            prepare_model4_history_preflight(ini_path, terminal_data_dirs, logger)
            if real_tick_model
            else []
        )
        try:
            before = time.time()
            logger.write(f"DIAG MT5_POPEN_BEFORE mt5={settings.mt5_path} attempt={attempt}/{max_attempts}")
            process = subprocess.Popen(command, creationflags=NO_WINDOW)
            logger.write(
                f"DIAG MT5_POPEN_AFTER pid={process.pid} mt5={settings.mt5_path} "
                f"attempt={attempt}/{max_attempts}"
            )
            attempt_kick_after = kick_after_seconds if attempt < max_attempts else max(0, kick_after_seconds * 2)
            exit_code, restarted, elapsed = wait_for_mt5_process(
                process,
                logger,
                kick_after_seconds=attempt_kick_after,
                stall_after_seconds=stall_after_seconds,
                max_runtime_seconds=max_runtime_seconds,
                tester_model=tester_model,
                tester_log_dirs=terminal_data_dirs,
                log_check_min_mtime=before,
                report_path=report_path,
                mt5_path=settings.mt5_path,
            )
            # Popen.wait/poll only observes the original PID, not an updater's
            # successor. Do not restore history, retry or finish this job yet.
            try:
                wait_for_terminal_release(
                    settings.mt5_path, logger,
                    timeout=max(120, settings.tester_max_runtime_seconds),
                )
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                raise TerminalStillRunningError(str(exc)) from exc
        finally:
            finish_model4_history_preflight(history_rotations, logger)
        last_exit_code = exit_code
        logger.write(f"MT5 termino con codigo: {exit_code}")
        logger.write(f"Duracion: {elapsed:.1f} segundos")

        # MT5 puede cerrarse sin reporte porque ni siquiera arranco el tester.  El
        # motivo esta en su codigo de salida y en el journal; sin traducirlo el
        # fallo se confunde con "no se genero reporte" y se reintenta a ciegas.
        abort = mt5_tester_abort(exit_code)
        if abort is not None:
            reason, transient = abort
            symbol = tester_symbol_from_ini(ini_path)
            logger.write(
                f"MT5 no arranco el tester: {reason} "
                f"(Symbol={symbol or '(sin dato)'}, codigo {signed_exit_code(exit_code)})."
            )
            write_tester_journal_snapshot(
                report_path,
                terminal_data_dirs,
                before,
                logger,
                label=f"tester_abort_attempt_{attempt}",
            )
            if not transient:
                logger.write(
                    f"DIAG TESTER_ABORT symbol={symbol} code={signed_exit_code(exit_code)} retry=no"
                )
                logger.write(
                    "ERROR: backtest abortado por MT5 sin reintento: "
                    f"{reason}. Revisa el universo del broker para {symbol or 'este simbolo'}."
                )
                return 1
            logger.write(
                f"DIAG TESTER_ABORT symbol={symbol} code={signed_exit_code(exit_code)} retry=si"
            )

        if restarted:
            write_tester_journal_snapshot(
                report_path,
                terminal_data_dirs,
                before,
                logger,
                label=f"watchdog_attempt_{attempt}",
            )

        if settings.terminal_cooldown_seconds > 0:
            logger.write(f"Cooldown MT5: {settings.terminal_cooldown_seconds}s")
            time.sleep(settings.terminal_cooldown_seconds)

        if restarted and attempt < max_attempts:
            retry_rate_limit_label = "Reinicio watchdog"
            continue
        if restarted:
            logger.write(
                f"ERROR: MT5 Model={tester_model or 'desconocido'} volvio a bloquearse "
                "tras el reintento automatico."
            )
            return 1

        time.sleep(settings.delay_seconds)

        report_files = filter_fresh_report_files(
            find_report_files(report_path, terminal_data_dirs, settings.mt5_path),
            before,
            logger,
        )
        if report_files:
            empty_tester_data = (
                real_tick_model
                and model4_report_has_empty_tester_data(report_files)
            )
            if empty_tester_data and attempt < max_attempts:
                logger.write(
                    "MT5 Model=4 genero un reporte vacio (0 barras / 0 ticks); "
                    "se considera un fallo tecnico de historico y se reintentara."
                )
                time.sleep(MODEL4_NO_HISTORY_RETRY_DELAY_SECONDS)
                continue

            logger.write("Reportes encontrados:")
            for path in report_files:
                logger.write(f"  {path} ({path.stat().st_size} bytes)")
            copied_reports = copy_reports_to_project(report_files, logger)
            if not copied_reports:
                logger.write("ERROR: No quedo ningun reporte nuevo copiado a reports.")
                return 1
            write_tester_journal_sidecars(copied_reports, terminal_data_dirs, before, logger)
            if empty_tester_data:
                logger.write(
                    "ERROR: MT5 Model=4 volvio a generar 0 barras / 0 ticks; "
                    "el reporte se conserva como evidencia y el trabajo queda reintentable."
                )
                return MODEL4_NO_HISTORY_EXIT_CODE
            return exit_code

        if attempt < max_attempts:
            logger.write(
                f"No se encontro reporte en Model={tester_model or 'desconocido'}; "
                "se reintentara una vez."
            )
            retry_rate_limit_label = "Reintento sin reporte"
            continue

        logger.write("ERROR: No se encontro ningun reporte generado para este backtest.")
        logger.write(
            "Revisa que el EA exista dentro de la carpeta MQL5 del terminal "
            f"({settings.mt5_path}) y que el simbolo/fechas tengan datos."
        )
        return 1

    return last_exit_code

def run_backtest_job(
    job: BacktestJob,
    profile: TerminalProfile,
    settings: TesterSettings,
    template: configparser.ConfigParser,
    args: argparse.Namespace,
    symbol_map: dict[str, str],
    logger: RunLogger,
    *,
    set_mode: bool,
) -> int:
    terminal_data_dirs = terminal_data_dirs_for_profile(profile, settings)
    expert = profile_expert_for_job(profile, job, set_mode)
    logger.write("")
    logger.write(
        f"[{profile.name}] Job #{job.index}: "
        f"{Path(expert).name if expert else '(perfil UBS)'}"
        + (f" | set={job.set_file.name}" if job.set_file else "")
    )
    try:
        ini_path, report_path = create_ini(
            expert,
            job.index,
            template,
            job.set_file,
            args.symbol_suffix,
            args.symbol_futures_suffix,
            args.symbol_shares_suffix,
            getattr(args, "symbol_suffix_universe", {}),
            symbol_map,
            args.infer_tester_from_set,
            args.prefer_set_path_timeframe,
            args.model,
            logger,
        )
    except ValueError as exc:
        logger.write(f"[{profile.name}] ERROR: {exc}")
        return 1
    retired_symbol = ini_symbol_missing_from_universe(
        ini_path, getattr(args, "universe_symbols", set())
    )
    if retired_symbol:
        logger.write(
            f"[{profile.name}] OMITIDO: Symbol={retired_symbol} ya no esta en el universo "
            "del broker; no se abre MT5."
        )
        delete_test_artifacts(ini_path, report_path, logger)
        return SKIPPED_SYMBOL_EXIT_CODE
    if job.set_file and not args.dry_run:
        copy_set_file_to_tester_profiles(
            job.set_file,
            terminal_data_dirs,
            logger,
            symbol_map,
            args.symbol_suffix,
            args.symbol_futures_suffix,
            args.symbol_shares_suffix,
            getattr(args, "symbol_suffix_universe", {}),
        )
    protected_set_name = job.set_file.name if job.set_file else ""
    return run_test(ini_path, report_path, settings, args.dry_run, logger, terminal_data_dirs, protected_set_name)

def run_jobs_parallel(
    jobs: list[BacktestJob],
    profiles: list[TerminalProfile],
    template: configparser.ConfigParser,
    args: argparse.Namespace,
    symbol_map: dict[str, str],
    logger: RunLogger,
    *,
    set_mode: bool,
) -> int:
    job_queue: queue.Queue[BacktestJob] = queue.Queue()
    for job in jobs:
        job_queue.put(job)

    def worker(profile: TerminalProfile) -> int:
        failures = 0
        logger.write(
            f"DIAG WORKER_START profile={profile.name} thread={threading.current_thread().name} "
            f"mt5={profile.mt5_path}"
        )
        settings = settings_from_profile(
            profile,
            args.delay,
            args.tester_kick_after_seconds,
            args.tester_stall_after_seconds,
            args.tester_max_runtime_seconds,
            args.terminal_cooldown_seconds,
        )
        while True:
            try:
                job = job_queue.get_nowait()
            except queue.Empty:
                break
            logger.write(
                f"DIAG WORKER_JOB_START profile={profile.name} thread={threading.current_thread().name} "
                f"job={job.index} remaining_queue={job_queue.qsize()}"
            )
            try:
                exit_code = run_backtest_job(
                    job,
                    profile,
                    settings,
                    template,
                    args,
                    symbol_map,
                    logger,
                    set_mode=set_mode,
                )
            except TerminalStillRunningError as exc:
                logger.write(f"[{profile.name}] ERROR: {exc}")
                job_queue.task_done()
                return failures + 1
            except Exception as exc:
                logger.write(f"[{profile.name}] ERROR inesperado: {exc}")
                exit_code = 1
            # Un simbolo omitido por politica no es un fallo tecnico del runner.
            if exit_code not in (0, SKIPPED_SYMBOL_EXIT_CODE):
                failures += 1
            logger.write(
                f"DIAG WORKER_JOB_DONE profile={profile.name} thread={threading.current_thread().name} "
                f"job={job.index} exit_code={exit_code} failures={failures}"
            )
            job_queue.task_done()
        logger.write(
            f"DIAG WORKER_DONE profile={profile.name} thread={threading.current_thread().name} "
            f"failures={failures}"
        )
        return failures

    log_runner_diagnostics(logger, "PARALLEL_BEFORE", profiles)
    with ThreadPoolExecutor(max_workers=len(profiles)) as executor:
        result = sum(future.result() for future in [executor.submit(worker, profile) for profile in profiles])
    log_runner_diagnostics(logger, "PARALLEL_AFTER", profiles)
    return result
