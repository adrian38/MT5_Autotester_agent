from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from run_tests import (
    RUNNING_TERMINAL_EXIT_CODE,
    TIMEFRAME_ENUM,
    apply_symbol_map,
    apply_symbol_suffix,
    load_symbol_suffix_target_map,
    load_set_params,
    normalize_set_symbol,
    parse_symbol_map,
    tester_journal_sidecar_path,
)
from ubs_generate_sets import format_like, parse_numeric
from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_disabled_symbols_path,
    account_memory_path,
    account_output_dir,
    account_seed_dir,
    axi_cash_future_family_targets,
    broker_asset_universe_path_with_fallback,
    default_symbol_map_for_broker,
    load_account_timeframe_universe,
    migrate_legacy_account_storage,
    normalize_account_type,
    normalize_broker,
    strip_broker_identity_suffix,
)
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
from ubs.memory import (
    AgentMemory,
    final_tick_table_for_stage,
    metrics_have_empty_tester_context,
    variant_from_candidate_row,
)
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.regression import (
    RegressionRuntime,
    evaluate_candidate_regression,
    rescore_regression_only,
)
from ubs.regression_rules import (
    DEFAULT_REGRESSION_FROM_DATE,
    DEFAULT_REGRESSION_MAX_DD_RATIO,
    DEFAULT_REGRESSION_MAX_DRAWDOWN_PCT,
    DEFAULT_REGRESSION_MIN_NET_PROFIT,
    DEFAULT_REGRESSION_MIN_PF_EFFICIENCY,
    DEFAULT_REGRESSION_MIN_POSITIVE_MONTH_RATIO,
    DEFAULT_REGRESSION_MIN_PROFIT_FACTOR,
    DEFAULT_REGRESSION_MIN_RECOVERY_FACTOR,
    DEFAULT_REGRESSION_MIN_TRADES,
    DEFAULT_REGRESSION_MIN_TRADES_MN,
    DEFAULT_REGRESSION_MIN_TRADES_W1,
    DEFAULT_REGRESSION_NEGATIVE_POINTS,
    DEFAULT_REGRESSION_POSITIVE_POINTS,
    DEFAULT_REGRESSION_TO_DATE,
    validate_regression_date_range,
)
from ubs.score import ScoreConfig, ScoreResult, rescore_result, run_is_lossless, score_report_file
from ubs.risk_profit import RiskProfitConfig, combine_robustness_profit_gate, robustness_result_status
from ubs.selection import (
    FITNESS_TARGET_FINAL_TICK_6M,
    DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
    DISCOVERY_CURRENT_TARGET_DEFAULT,
    DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
    DISCOVERY_SOURCE_MIX_FLOOR,
    DiscoverySourceMix,
    DiscoveryTargetPolicyMix,
    SelectionPrediction,
    estimate_discovery_source_mix,
    estimate_discovery_target_policy_mix,
)
from ubs.seeds import file_digest, load_seeds, seed_eval_filename, seed_from_path
from ubs.set_utils import (
    compact_safe_part,
    force_fixed_lot_text,
    read_set_with_encoding,
    safe_part,
    set_matches_use_every_tick_source,
    set_use_every_tick_text,
    write_set_text,
    write_set_use_every_tick,
)
from ubs.tester_diagnostics import TRADE_DISABLED_STATUS, trade_disabled_metadata, execution_failure_metadata, execution_failure_reason
from ubs.universe import (
    augment_aliases_with_symbol_map,
    canonical_symbol,
    load_asset_universe,
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
    seed_symbol_disabled,
)
from ubs.weights import (
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    MUTATION_SCORE_FULL_STRENGTH,
    score_aware_percentile_multipliers,
)
from ubs_agent_cli import (
    parse_args,
)
from ubs_agent_config import (
    diag_args_summary,
    diag_log,
)
from ubs_agent_run import (
    run_agent,
)


# Reexportados para no cambiar a los consumidores de `ubs_agent`.
from ubs_agent_cli import (  # noqa: F401
    parse_args,
)
from ubs_agent_config import (  # noqa: F401
    ALLOWED_MUTATION_KEYS,
    ALLOWED_MUTATION_PREFIXES,
    ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION,
    ASSET_UNSEEDED_FORCE_PROB_LATE,
    BASE_DIR,
    BASE_TIMEFRAME_UNIVERSE,
    CORE_MUTATION_KEYS,
    DEFAULT_ASSETS,
    DEFAULT_MEMORY,
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE,
    DEFAULT_SYMBOL_MAP,
    DEFAULT_TARGET_GROUP_CAP_RATIO,
    DEFAULT_TEMPLATE,
    DIAG_LOG_FILE,
    DISCOVERY_EXPLOITABLE_SEED_MIN_RATIO,
    DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT,
    DISCOVERY_GROUP_FEEDBACK_TEMPERATURE,
    DISCOVERY_SEED_SYMBOL_RESERVE_RATIO,
    DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
    DIVERSITY_REROLL_ATTEMPTS,
    EXPERIMENTAL_LONG_TIMEFRAMES,
    FINAL_TICK_6M_MIN_DAYS,
    FINAL_TICK_DATE_RETRYABLE_STATUSES,
    FINAL_TICK_RETRYABLE_STATUSES,
    FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS,
    FROZEN_KEYS,
    FROZEN_PREFIXES,
    GENERATION_MODES,
    GLOBAL_PARAMS_FILE,
    LEGACY_TIMEFRAME_TO_ENUM,
    LosslessControlGate,
    MUTATION_OVERRIDES_FILE,
    PRODUCTION_CURRENT_SYMBOL_PROBABILITY,
    PRODUCTION_DIVERSITY_REROLL_ATTEMPTS,
    PRODUCTION_NEXT_SEED_BACKFILL_MIN_RATIO,
    PRODUCTION_SEED_SYMBOL_CAP_RATIO,
    PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
    RANDOM_STREAM_VERSION,
    SELECTION_FITNESS_APPLIED_SCALE,
    SELECTION_FITNESS_MODE,
    SYMBOL_NOT_EXIST_STATUS,
    TARGET_GROUP_CAP_RATIOS,
    TARGET_PAIR_CAP_RATIO,
    TARGET_SYMBOL_CAP_RATIO,
    TARGET_TIMEFRAME_CAP_RATIO,
    TF_UNSEEDED_FORCE_PROB_BY_GENERATION,
    TF_UNSEEDED_FORCE_PROB_LATE,
    TIMEFRAME_TO_ENUM,
    TIMEFRAME_UNIVERSE,
    _overrides_cache,
    _overrides_mtime,
    augment_symbol_map_with_suffix_targets,
    diag_args_summary,
    diag_log,
    generation_random_stream,
    is_agent_mutable_key,
    load_global_params,
    load_mutation_overrides,
    paths_belong_to_workspace,
    probability_argument,
    save_global_params,
    save_mutation_overrides,
)
from ubs_agent_evaluate import (  # noqa: F401
    copy_accepted,
    count_valid_existing_reports,
    evaluate_history_probe,
    evaluate_seed_report,
    evaluate_variant,
    evaluate_variant_report,
    evaluate_variants,
    generation_feedback_terminal_stage,
    generation_fitness_target,
    generation_seed_fitness_predictions,
    prepare_final_tick_exec_dir,
    recreate_work_dir,
    remove_candidate_copies,
    remove_report_artifacts,
    select_next_generation_survivors,
    select_next_seed_survivors,
    select_survivors,
)
from ubs_agent_final_tick import (  # noqa: F401
    FINAL_TICK_PENDING_HISTORY_REASONS,
    _evaluate_final_tick_tick_report,
    _read_ohlc_report_cfg_dates,
    final_tick_dates_match,
    final_tick_ohlc_retry_exhausted_for_dates,
    final_tick_ohlc_retry_needed_for_dates,
    final_tick_ohlc_trades_pending_payload,
    final_tick_row_pending_for_dates,
    final_tick_similarity,
    final_tick_stage_dir_name,
    final_tick_stage_label,
    final_tick_stage_prefixes,
    final_tick_status_from_similarity,
    lossless_control_gate_from_args,
    normalize_final_tick_stage,
    validate_final_tick_stage_dates,
)
from ubs_agent_final_tick_entry import evaluate_candidate_final_tick  # noqa: F401
from ubs_agent_final_tick_pass import (  # noqa: F401
    _evaluate_candidate_final_tick_pass,
)
from ubs_agent_final_tick_rescore import (  # noqa: F401
    _rescore_final_tick_from_reports,
    reconcile_final_tick_reports,
    rescore_final_tick_only,
)
from ubs_agent_history import (  # noqa: F401
    HISTORY_PROBE_FINAL_STATUSES,
    evaluate_generation,
    history_probe_latest_statuses,
    probe_universe_history,
    select_history_probe_seed,
)
from ubs_agent_policy import (  # noqa: F401
    apply_discovery_target_policy_schedule,
    discovery_ranked_seed_selection,
    discovery_source_mix_feedback,
    discovery_target_policy_feedback,
    production_viable_source_seeds,
    related_assets,
    target_symbol_disabled,
    target_symbol_options_for_seed,
    unseeded_universe_targets,
)
from ubs_agent_reports import (  # noqa: F401
    REPORT_SUFFIXES,
    WATCHDOG_SUFFIX,
    _REPORTS_NAME_INDEX,
    _indexed_names_with_prefix,
    _report_is_fresh,
    _reports_name_index,
    classify_zero_trade_robustness,
    find_report_for_set,
    find_watchdog_snapshot_for_set,
    record_history_probe_status,
    record_score_with_metadata,
    record_seed_score_with_metadata,
    report_has_empty_tester_context,
    report_matches_variant,
    tester_log_invalid_stops_metadata,
    tester_log_no_history_metadata,
)
from ubs_agent_rescore import (  # noqa: F401
    _batched_memory_updates,
    _rescore_candidate_scores_from_reports,
    _rescore_metrics_json,
    _rescore_robustness_from_reports,
    _run_execution_dates,
    _stored_or_discovered_report,
    _stored_score_config,
    apply_robustness_degradation,
    rescore_candidate_scores_only,
    rescore_robustness_only,
    robustness_degradation_config,
)
from ubs_agent_resume import (  # noqa: F401
    resume_last_run,
)
from ubs_agent_retry import (  # noqa: F401
    _retry_single_candidate,
    retry_candidate,
    retry_full_run,
    retry_generation_mismatches,
    retry_run_mismatches,
    retry_seed,
)
from ubs_agent_robustness import (  # noqa: F401
    ROBUST_RETRYABLE_STATUSES,
    _bounded_profit_factor,
    _relative_delta_pct,
    evaluate_candidate_robustness,
    robust_status_pending_for_retry,
)
from ubs_agent_run import (  # noqa: F401
    run_agent,
)
from ubs_agent_run_config import (  # noqa: F401
    build_run_config,
    json_safe,
    regression_runtime,
    restore_run_unseeded_probabilities,
    restored_discovery_current_target_probability,
    restored_discovery_current_timeframe_probability,
    restored_discovery_exploitable_ratio,
    restored_discovery_universe_feedback_probability,
)
from ubs_agent_seed_scores import (  # noqa: F401
    _parse_eval_dir_timestamp,
    _seed_override_updated_at,
    evaluate_seed_scores,
    format_disabled_seed_counts,
    reconcile_seed_eval_reports,
    rescore_existing_seed_scores,
    rescore_seed_scores_only,
)
from ubs_agent_seeds_plan import (  # noqa: F401
    TargetDiversityLimiter,
    apply_reserved_timeframe,
    asset_group_map,
    capped_count,
    choose_seeds,
    configured_unseeded_force_probabilities,
    disabled_symbols_file_for_account,
    discovery_seed_pool,
    format_group_cap_summary,
    generation_source_seeds,
    next_generation_seed_pool,
    production_seed_pool,
    ranked_seed_selection,
    reserved_timeframe_plan,
    seed_symbol_cap_ratio,
    seeds_from_survivors,
    seeds_from_variants,
    target_symbol_cap_ratio,
    timeframe_plan_summary,
    unseeded_asset_force_probability,
    unseeded_timeframe_force_probability,
    variant_as_next_seed,
)
from ubs_agent_sets import (  # noqa: F401
    _set_param_active,
    copy_seed_for_backtest,
    infer_missing_run_strategy_from_set,
    line_candidates,
    record_invalid_seed,
    repair_seed_backtest_set,
    replace_existing_current_value,
    replace_existing_plain_key,
    replace_or_add_current_value,
    replace_or_add_plain_key,
    replace_timeframe_keys,
    validate_seed_backtest_set,
    weighted_sample,
    write_retry_set,
    write_set_force_symbol,
)
from ubs_agent_targets import (  # noqa: F401
    choose_diverse_target,
    choose_group_guided_unseeded_symbol,
    choose_target_period,
    choose_target_symbol,
    diverse_target_fallback,
    filter_timeframe_universe,
    related_timeframes,
)
from ubs_agent_universe import (  # noqa: F401
    _BROKER_UNIVERSE_SYMBOLS_CACHE,
    _row_target_symbol,
    broker_universe_symbols,
    current_set_symbol,
    format_retired_symbol_rows,
    min_trades_for_period,
    missing_report_status,
    regression_score_config,
    score_config_for_period,
    score_config_for_variant,
    split_retired_symbols,
    symbol_not_offered,
    target_timeframe_universe,
    variant_symbol_not_offered,
)
from ubs_agent_variants import (  # noqa: F401
    create_history_probe_variant,
    create_variant,
    run_backtests,
)


def main() -> int:
    args = parse_args()
    diag_log(f"MAIN_START ppid={os.getppid()} {diag_args_summary(args)}")
    if args.generations <= 0 or args.variants_per_seed <= 0:
        print("ERROR: generations y variants-per-seed deben ser mayores que 0")
        return 1
    if args.min_trades < 0:
        print("ERROR: --min-trades no puede ser negativo")
        return 1
    if args.min_trades_w1 < 0 or args.min_trades_mn < 0:
        print("ERROR: --min-trades-w1 y --min-trades-mn no pueden ser negativos")
        return 1
    if args.final_tick_min_ohlc_trades < 0 or args.final_tick_min_trades_w1 < 0 or args.final_tick_min_trades_mn < 0:
        print("ERROR: minimos de operaciones Final Tick no pueden ser negativos")
        return 1
    if args.min_profit_factor < 0 or args.max_drawdown_pct < 0:
        print("ERROR: profit factor y drawdown deben ser mayores o iguales a 0")
        return 1
    if min(
        args.robust_min_net_retention,
        args.robust_min_pf_edge_retention,
        args.robust_min_recovery_retention,
        args.robust_max_dd_inflation,
    ) < 0:
        print("ERROR: los limites de degradacion robusta deben ser >= 0 (0 desactiva)")
        return 1
    if not 0 <= args.min_positive_month_ratio <= 1:
        print("ERROR: --min-positive-month-ratio debe estar entre 0 y 1")
        return 1
    if args.evaluate_regression or args.rescore_regression_only:
        date_error = validate_regression_date_range(args.regression_from_date, args.regression_to_date)
        if date_error:
            print(f"ERROR: rango regresivo invalido: {date_error}")
            return 1
        if min(
            args.regression_min_trades,
            args.regression_min_trades_w1,
            args.regression_min_trades_mn,
        ) < 0:
            print("ERROR: minimos de operaciones de la prueba regresiva no pueden ser negativos")
            return 1
        if args.regression_min_profit_factor < 0 or args.regression_max_drawdown_pct < 0:
            print("ERROR: profit factor y drawdown regresivos deben ser mayores o iguales a 0")
            return 1
        if args.regression_min_recovery_factor < 0:
            print("ERROR: recovery regresivo debe ser mayor o igual a 0")
            return 1
        if args.regression_min_pf_efficiency < 0 or args.regression_max_dd_ratio < 0:
            print("ERROR: eficiencia PF y cociente DD regresivos deben ser >= 0 (0 desactiva)")
            return 1
        if not 0 <= args.regression_min_positive_month_ratio <= 1:
            print("ERROR: --regression-min-positive-month-ratio debe estar entre 0 y 1")
            return 1
        if args.regression_positive_points < 0 or args.regression_negative_points > 0:
            print("ERROR: puntos regresivos OK deben ser >=0 y FAIL <=0")
            return 1
    return run_agent(args, sys.modules[__name__])


if __name__ == "__main__":
    raise SystemExit(main())
