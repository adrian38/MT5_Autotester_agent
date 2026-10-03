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
from run_tests_main import (  # noqa: F401
    build_jobs,
    expert_sources,
    load_run_template,
    log_run_header,
    log_run_summary,
    log_terminal_pool,
    prepare_settings,
    run_jobs,
    terminal_data_dirs_for_run,
    terminal_guard,
    terminal_profiles_for_run,
    validate_jobs,
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
    prepared = prepare_settings(args)
    if isinstance(prepared, int):
        return prepared
    settings, symbol_map, template_path = prepared
    ensure_directories()
    logger = create_logger()
    terminal_profiles = terminal_profiles_for_run(args, logger)
    if isinstance(terminal_profiles, int):
        return terminal_profiles
    sources = expert_sources(args, terminal_profiles, logger)
    if isinstance(sources, int):
        return sources
    experts, experts_dir, set_dir, set_files = sources
    blocked = terminal_guard(args, settings, logger)
    if blocked is not None:
        return blocked
    template = load_run_template(args, template_path)
    terminal_data_dirs = terminal_data_dirs_for_run(args, settings, experts, experts_dir)
    log_run_header(
        args, settings, logger, template_path, experts, experts_dir, set_dir, set_files,
        terminal_data_dirs,
    )
    jobs = build_jobs(args, experts, set_files, logger)
    invalid = validate_jobs(
        args, settings, logger, terminal_profiles, jobs, experts, set_files, terminal_data_dirs
    )
    if invalid is not None:
        return invalid
    log_terminal_pool(args, logger, terminal_profiles, jobs)
    failures = run_jobs(
        args, settings, symbol_map, logger, template, terminal_profiles, jobs, set_files,
        terminal_data_dirs,
    )
    log_run_summary(args, failures, logger)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
