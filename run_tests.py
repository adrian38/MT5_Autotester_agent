import argparse
import atexit
import configparser
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mt5_env import MT5_TERMINAL_ENV, terminal_path_from_env

# Reexportados para no cambiar a los consumidores de `run_tests`.
from run_tests_base import (  # noqa: F401
    BASE_DIR,
    CONFIG_DIR,
    DEFAULT_MT5_PATHS,
    DEFAULT_TESTER_MAX_RUNTIME_SECONDS,
    DEFAULT_TESTER_STALL_AFTER_SECONDS,
    EXPERTS_FILE,
    EXPERTS_ROOT_FILE,
    GENERATED_SET_ROOT_NAMES,
    GENERATED_SET_ROOT_PREFIXES,
    LOG_DIR,
    MODEL4_NO_HISTORY_EXIT_CODE,
    MODEL4_NO_HISTORY_RETRY_DELAY_SECONDS,
    MT5_TESTER_ABORT_CODES,
    NO_WINDOW,
    REPORT_DIR,
    REPORT_SAVE_CHECK_INTERVAL,
    REPORT_SAVE_STALL_SECONDS,
    RUNNING_TERMINAL_EXIT_CODE,
    RUN_LOG_QUEUE_MAX_BATCHES,
    SKIPPED_SYMBOL_EXIT_CODE,
    TEMPLATE_FILE,
    TESTER_STUCK_MARKERS,
    UI_SETTINGS_FILE,
    WATCHDOG_RESTART_MIN_INTERVAL_SECONDS,
    WATCHDOG_TERMINATE_MIN_INTERVAL_SECONDS,
    BacktestJob,
    HistoryCacheRotation,
    TerminalProfile,
    TesterSettings,
)
from run_tests_cli import parse_args  # noqa: F401
from run_tests_logging import ActionRateLimiter, RunLogger  # noqa: F401
from run_tests_watchdog import (  # noqa: F401
    terminate_process_tree,
    wait_for_mt5_process,
)
from run_tests_runner import (  # noqa: F401
    quote_command,
    run_backtest_job,
    run_jobs_parallel,
    run_test,
)
from run_tests_terminals import (  # noqa: F401
    TerminalStillRunningError,
    discover_terminal_data_dirs,
    find_matching_running_terminals,
    find_mt5_path,
    load_runner_tuning,
    load_terminal_profiles,
    log_runner_diagnostics,
    normalize_terminal_broker,
    normalized_path,
    parse_bool,
    parse_non_negative_int,
    portable_terminal_data_dir,
    profile_data_dir,
    read_origin_path,
    settings_from_profile,
    should_use_portable,
    terminal_data_dir_from_cli,
    terminal_data_dir_from_experts_dir,
    terminal_data_dir_from_origin,
    terminal_data_dirs_for_profile,
    terminal_section_sort_key,
    wait_for_terminal_release,
    get_running_terminal_processes,
)
from run_tests_experts import (  # noqa: F401
    TESTER_DEFAULTS,
    copy_set_file_to_tester_profiles,
    create_ini,
    ensure_tester_defaults,
    expert_file_path,
    expert_from_cli_value,
    expert_from_value,
    job_uses_profile_ubs_expert,
    load_experts,
    load_experts_from_dir,
    load_experts_root,
    load_set_files,
    load_template,
    looks_like_ubs_expert_file,
    mapped_set_text_for_tester,
    missing_experts_in_terminal_data_dirs,
    normalize_expert_for_tester,
    profile_expert_for_job,
    safe_name,
    validate_terminal_profiles,
)
from run_tests_reports import (  # noqa: F401
    _mt5_report_integer_metric,
    _read_mt5_report_text,
    copy_reports_to_project,
    delete_existing_report_files,
    delete_test_artifacts,
    filter_fresh_report_files,
    find_report_files,
    find_tester_journal_log,
    finish_model4_history_preflight,
    fresh_report_signature,
    ini_symbol_missing_from_universe,
    install_dir_reports,
    load_universe_symbols,
    log_ini_content,
    model4_history_cache_files,
    model4_report_has_empty_tester_data,
    mt5_tester_abort,
    prepare_model4_history_preflight,
    read_tester_journal_tail,
    read_tester_journal_tail_text,
    signed_exit_code,
    tester_journal_sidecar_path,
    tester_model_from_ini,
    tester_symbol_from_ini,
    write_tester_journal_sidecars,
    write_tester_journal_snapshot,
)
from run_tests_symbols import (  # noqa: F401
    EXCHANGE_SYMBOL_SUFFIXES,
    EXPLICIT_SYMBOLS,
    FOREX_SYMBOLS,
    KNOWN_TIMEFRAMES,
    SYMBOL_ALIASES,
    TIMEFRAME_ENUM,
    TIMEFRAME_PATTERNS,
    SymbolSuffixUniverse,
    apply_symbol_map,
    apply_symbol_suffix,
    infer_period_from_path,
    infer_period_from_set,
    infer_symbol_from_set,
    infer_tester_fields_from_set,
    load_set_params,
    load_symbol_suffix_target_map,
    load_symbol_suffix_universe,
    normalize_set_symbol,
    parse_symbol_map,
    read_set_text,
    validate_set_symbol,
)


def ensure_directories() -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    REPORT_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def create_logger() -> RunLogger:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_{stamp}.log"
    return RunLogger(log_path)


def main() -> int:
    args = parse_args()
    template_path = Path(args.template).expanduser()
    mt5_path = find_mt5_path(args.mt5_path)
    portable = should_use_portable(mt5_path, args.portable)
    try:
        explicit_data_dir = terminal_data_dir_from_cli(args.data_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"ERROR: {exc}")
        return 1
    try:
        symbol_map = parse_symbol_map(args.symbol_map)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    symbol_universe_path = Path(args.symbol_universe).expanduser() if args.symbol_universe.strip() else None
    args.symbol_suffix_universe = load_symbol_suffix_universe(
        symbol_universe_path,
        args.symbol_suffix,
        args.symbol_futures_suffix,
        args.symbol_shares_suffix,
    )
    args.universe_symbols = load_universe_symbols(symbol_universe_path)
    (
        tester_kick_after_seconds,
        tester_stall_after_seconds,
        tester_max_runtime_seconds,
        terminal_cooldown_seconds,
    ) = load_runner_tuning(
        Path(args.terminals_config).expanduser(),
        tester_kick_after=args.tester_kick_after,
        tester_stall_after=args.tester_stall_after,
        tester_max_runtime=args.tester_max_runtime,
        terminal_cooldown=args.terminal_cooldown,
    )
    args.tester_kick_after_seconds = tester_kick_after_seconds
    args.tester_stall_after_seconds = tester_stall_after_seconds
    args.tester_max_runtime_seconds = tester_max_runtime_seconds
    args.terminal_cooldown_seconds = terminal_cooldown_seconds
    data_dir = explicit_data_dir or (portable_terminal_data_dir(mt5_path) if portable else terminal_data_dir_from_origin(mt5_path))
    settings = TesterSettings(
        mt5_path=mt5_path,
        delay_seconds=args.delay,
        portable=portable,
        data_dir=data_dir,
        tester_kick_after_seconds=tester_kick_after_seconds,
        tester_stall_after_seconds=tester_stall_after_seconds,
        tester_max_runtime_seconds=tester_max_runtime_seconds,
        terminal_cooldown_seconds=terminal_cooldown_seconds,
    )

    ensure_directories()
    logger = create_logger()
    terminal_profiles: list[TerminalProfile] = []
    if args.multi_terminal:
        try:
            configured_profiles = load_terminal_profiles(
                Path(args.terminals_config).expanduser(),
                ignore_enabled=args.max_workers > 1,
            )
        except (OSError, ValueError) as exc:
            logger.write(f"ERROR: {exc}")
            return 1
        if not configured_profiles:
            logger.write("ERROR: no hay terminales habilitadas en la configuracion multiterminal.")
            return 1
        worker_limit = args.max_workers if args.max_workers > 0 else len(configured_profiles)
        terminal_profiles = configured_profiles[: max(1, min(worker_limit, len(configured_profiles)))]
        log_runner_diagnostics(logger, "AFTER_PROFILE_LOAD", terminal_profiles)

    experts_dir = (
        Path(args.experts_dir).expanduser()
        if args.experts_dir
        else (terminal_profiles[0].experts_root if terminal_profiles else load_experts_root())
    )
    set_dir = Path(args.set_dir).expanduser() if args.set_dir else None
    set_files = load_set_files(set_dir, args.set_file, recursive=args.recursive)
    if (set_dir or args.set_file) and not set_files:
        logger.write("ERROR: no se encontraron set files para testear.")
        logger.write(f"  Origen consultado: {set_dir if set_dir else 'argumentos --set-file'}")
        logger.write(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
        return 1
    if set_files and not args.expert and not args.multi_terminal:
        logger.write("ERROR: para testear multiples set files debes indicar un EA con --expert.")
        return 1
    if args.expert:
        experts = [expert_from_cli_value(args.expert, experts_dir)]
    elif set_files and args.multi_terminal:
        experts = [""]
    else:
        experts = (
            load_experts_from_dir(experts_dir, recursive=args.recursive, allow_sources=args.dry_run)
            if experts_dir
            else load_experts()
        )
    if not experts:
        source = experts_dir if experts_dir else EXPERTS_FILE
        logger.write("")
        logger.write("ERROR: no se encontraron Expert Advisors para backtestear.")
        logger.write(f"  Origen consultado: {source}")
        logger.write(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
        if experts_dir:
            logger.write("  Revisa que la ruta exista y contenga archivos .ex5.")
            try:
                if experts_dir.exists() and experts_dir.is_dir():
                    subdirs = [d for d in experts_dir.iterdir() if d.is_dir()][:10]
                    if subdirs:
                        logger.write(f"  Subcarpetas detectadas ({len(subdirs)}):")
                        for d in subdirs:
                            logger.write(f"    - {d.name}")
            except OSError:
                pass
        else:
            logger.write(f"  Revisa que {EXPERTS_FILE.name} liste rutas validas.")
        return 1

    if not args.multi_terminal and not settings.mt5_path.exists() and not args.dry_run:
        logger.write(f"No encuentro MT5 en: {settings.mt5_path}")
        logger.write(
            f"Indica la ruta con --mt5-path o define {MT5_TERMINAL_ENV[0]} en el entorno o en .env"
        )
        return 1

    if not args.multi_terminal and not args.dry_run and not args.skip_running_check:
        running = find_matching_running_terminals(settings.mt5_path)
        if running:
            logger.write("ERROR: RoboForex MT5 ya esta abierto.")
            for process in running:
                logger.write(f"  PID {process['pid']}: {process['path']}")
            logger.write("Cierra MT5 completamente y vuelve a ejecutar el script.")
            logger.write("MT5 puede ignorar /config si ya existe una instancia abierta con la misma carpeta de datos.")
            return RUNNING_TERMINAL_EXIT_CODE

    template = load_template(template_path)
    if args.from_date.strip():
        template.setdefault("Tester", {})
        template["Tester"]["FromDate"] = args.from_date.strip()
    if args.to_date.strip():
        template.setdefault("Tester", {})
        template["Tester"]["ToDate"] = args.to_date.strip()
    terminal_data_dirs = [] if args.multi_terminal else ([settings.data_dir] if settings.data_dir else [])
    if not args.multi_terminal and not terminal_data_dirs:
        terminal_data_dirs = discover_terminal_data_dirs(experts)
        if experts_dir:
            fallback_data_dir = terminal_data_dir_from_experts_dir(experts_dir)
            if fallback_data_dir:
                terminal_data_dirs = sorted(set([fallback_data_dir] + terminal_data_dirs))

    logger.write(f"Log: {logger.log_path}")
    logger.write(f"Ultimo log: {logger.last_log_path}")
    logger.write(f"Modo: {'DRY-RUN' if args.dry_run else 'REAL'}")
    logger.write(f"Proyecto: {BASE_DIR}")
    if args.multi_terminal:
        logger.write("MT5: perfiles multiterminal")
    else:
        logger.write(f"MT5: {settings.mt5_path}")
        logger.write(f"Portable: {'si' if settings.portable else 'no'}")
        if settings.data_dir:
            logger.write(f"Carpeta de datos MT5 seleccionada: {settings.data_dir}")
        else:
            logger.write("Aviso: no pude detectar la carpeta de datos exacta del terminal seleccionado.")
    logger.write(f"INI general: {template_path}")
    if tester_kick_after_seconds > 0:
        logger.write(
            f"Model=4 auto-restart: kick_after={tester_kick_after_seconds}s, "
            f"cooldown={terminal_cooldown_seconds}s"
        )
    else:
        logger.write("Model=4 auto-restart: desactivado")
    logger.write(
        "Watchdog general: "
        f"stall_after={tester_stall_after_seconds}s, "
        f"max_runtime={tester_max_runtime_seconds}s"
    )
    logger.write(f"Origen EAs: {experts_dir if experts_dir else EXPERTS_FILE}")
    logger.write(f"Expert Advisors: {len(experts)}")
    if set_files:
        logger.write(f"Set files: {len(set_files)}")
        logger.write(f"Origen sets: {set_dir if set_dir else 'argumentos --set-file'}")
    if not args.multi_terminal and terminal_data_dirs:
        logger.write("Carpetas de datos MT5 detectadas:")
        for directory in terminal_data_dirs:
            logger.write(f"  {directory}")
    elif not args.multi_terminal:
        logger.write("Aviso: no se detecto automaticamente la carpeta de datos MT5 con esos EAs.")

    raw_jobs: list[tuple[str, Path | None]] = (
        [(experts[0], set_file) for set_file in set_files]
        if set_files
        else [(expert, None) for expert in experts]
    )
    jobs = [BacktestJob(index, expert, set_file) for index, (expert, set_file) in enumerate(raw_jobs, start=1)]
    logger.write(f"DIAG JOBS_READY jobs={len(jobs)} multi_terminal={'si' if args.multi_terminal else 'no'}")

    if args.multi_terminal:
        log_runner_diagnostics(logger, "BEFORE_MULTITERMINAL_VALIDATE", terminal_profiles)
        profile_errors = validate_terminal_profiles(
            terminal_profiles,
            jobs,
            set_mode=bool(set_files),
            dry_run=args.dry_run,
        )
        if profile_errors:
            logger.write("ERROR: configuracion multiterminal invalida.")
            for error in profile_errors[:30]:
                logger.write(f"  {error}")
            if len(profile_errors) > 30:
                logger.write(f"  ... y {len(profile_errors) - 30} error(es) mas")
            return 1
        if not args.dry_run and not args.skip_running_check:
            for profile in terminal_profiles:
                running = find_matching_running_terminals(profile.mt5_path)
                if running:
                    logger.write(f"ERROR: {profile.name} ya esta abierta.")
                    for process in running:
                        logger.write(f"  PID {process['pid']}: {process['path']}")
                    logger.write("Cierra esas terminales y vuelve a ejecutar.")
                    return RUNNING_TERMINAL_EXIT_CODE
    else:
        missing_experts = missing_experts_in_terminal_data_dirs(experts, terminal_data_dirs)
        if missing_experts and not args.dry_run:
            logger.write("ERROR: estos EAs no estan en la carpeta de datos que usara MT5:")
            for expert in missing_experts[:20]:
                logger.write(f"  {expert}")
            if len(missing_experts) > 20:
                logger.write(f"  ... y {len(missing_experts) - 20} mas")
            logger.write("Compila/copialos dentro de MQL5\\Experts del terminal seleccionado.")
            if settings.portable:
                logger.write(f"Terminal portable esperado: {settings.mt5_path.parent / 'MQL5' / 'Experts'}")
            return 1

    if args.multi_terminal:
        logger.write("Multiterminal: si")
        logger.write(f"Terminales habilitadas: {len(terminal_profiles)}")
        logger.write(f"Workers: {len(terminal_profiles)}")
        for profile in terminal_profiles:
            logger.write(f"  {profile.name}: {profile.mt5_path}")
            logger.write(f"    Experts: {profile.experts_root}")
            if profile.data_dir:
                logger.write(f"    Data: {profile.data_dir}")
            if profile.ubs_ex5_file:
                logger.write(f"    UBS EX5: {profile.ubs_ex5_file}")
    logger.write(f"Backtests en cola: {len(jobs)}")
    if args.multi_terminal:
        failures = run_jobs_parallel(
            jobs,
            terminal_profiles,
            template,
            args,
            symbol_map,
            logger,
            set_mode=bool(set_files),
        )
    else:
        failures = 0
        for job in jobs:
            try:
                ini_path, report_path = create_ini(
                    job.expert,
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
                logger.write("")
                logger.write(f"ERROR: {exc}")
                failures += 1
                continue
            retired_symbol = ini_symbol_missing_from_universe(
                ini_path, getattr(args, "universe_symbols", set())
            )
            if retired_symbol:
                logger.write("")
                logger.write(
                    f"OMITIDO: Symbol={retired_symbol} ya no esta en el universo del "
                    "broker; no se abre MT5."
                )
                delete_test_artifacts(ini_path, report_path, logger)
                continue
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
            exit_code = run_test(
                ini_path,
                report_path,
                settings,
                args.dry_run,
                logger,
                terminal_data_dirs,
                protected_set_name,
            )
            if exit_code != 0:
                failures += 1

    if args.dry_run:
        logger.write("")
        if failures:
            logger.write(f"Dry-run terminado con {failures} test(s) fallido(s). No se abrio MT5.")
        else:
            logger.write("Dry-run terminado. Se generaron los .ini, no se abrio MT5.")
    elif failures:
        logger.write("")
        logger.write(f"Terminado con {failures} test(s) fallido(s).")
    else:
        logger.write("")
        logger.write("Todos los backtests han terminado.")

    logger.write(f"Log guardado en: {logger.log_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
