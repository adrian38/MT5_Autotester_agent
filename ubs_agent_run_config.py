"""Configuracion del run y restauracion de sus probabilidades."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.account import normalize_account_type, normalize_broker
from ubs.degradation import (
    DEFAULT_MAX_DD_INFLATION,
    DEFAULT_MIN_NET_RETENTION,
    DEFAULT_MIN_PF_EDGE_RETENTION,
    DEFAULT_MIN_RECOVERY_RETENTION,
)
from ubs.memory import variant_from_candidate_row
from ubs.regression import RegressionRuntime
from ubs.score import ScoreConfig
from ubs.selection import (
    DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
    DISCOVERY_CURRENT_TARGET_DEFAULT,
    DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
    DiscoverySourceMix,
    DiscoveryTargetPolicyMix,
)
from ubs_agent_config import (
    ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION,
    ASSET_UNSEEDED_FORCE_PROB_LATE,
    BASE_DIR,
    DEFAULT_TARGET_GROUP_CAP_RATIO,
    DISCOVERY_EXPLOITABLE_SEED_MIN_RATIO,
    DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT,
    DISCOVERY_GROUP_FEEDBACK_TEMPERATURE,
    DISCOVERY_SEED_SYMBOL_RESERVE_RATIO,
    DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
    DIVERSITY_REROLL_ATTEMPTS,
    FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS,
    PRODUCTION_CURRENT_SYMBOL_PROBABILITY,
    PRODUCTION_DIVERSITY_REROLL_ATTEMPTS,
    PRODUCTION_NEXT_SEED_BACKFILL_MIN_RATIO,
    PRODUCTION_SEED_SYMBOL_CAP_RATIO,
    PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
    RANDOM_STREAM_VERSION,
    SELECTION_FITNESS_APPLIED_SCALE,
    SELECTION_FITNESS_MODE,
    TARGET_GROUP_CAP_RATIOS,
    TARGET_PAIR_CAP_RATIO,
    TARGET_SYMBOL_CAP_RATIO,
    TARGET_TIMEFRAME_CAP_RATIO,
    TF_UNSEEDED_FORCE_PROB_BY_GENERATION,
    TF_UNSEEDED_FORCE_PROB_LATE,
    probability_argument,
)
from ubs_agent_evaluate import (
    recreate_work_dir,
    remove_report_artifacts,
)
from ubs_agent_final_tick import (
    _read_ohlc_report_cfg_dates,
)
from ubs_agent_reports import (
    find_report_for_set,
    find_watchdog_snapshot_for_set,
    report_has_empty_tester_context,
    report_matches_variant,
    tester_log_no_history_metadata,
)
from ubs_agent_seeds_plan import (
    target_symbol_cap_ratio,
)
from ubs_agent_sets import (
    write_retry_set,
)
from ubs_agent_universe import (
    missing_report_status,
    target_timeframe_universe,
)
from ubs_agent_variants import (
    run_backtests,
)


def json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def _config_paths(
    args: argparse.Namespace, source_dir: Path, output_root: Path, run_dir: Path
) -> dict[str, object]:
    """Rutas con las que se ejecuta el run."""
    return {
        "source_dir": str(source_dir),
        "output_root": str(output_root),
        "run_dir": str(run_dir),
        "memory": str(Path(args.memory).expanduser()),
        "template": str(Path(args.template).expanduser()),
        "assets": str(Path(args.assets).expanduser()),
    }


def _config_unseeded_probabilities(args: argparse.Namespace) -> dict[str, object]:
    """Probabilidades de forzar activo y marco sin seed, por generacion."""
    return {
        "asset_unseeded_force_probability": {
            "generation_1": float(getattr(args, "asset_unseeded_prob_gen1", ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[1])),
            "generation_2": float(getattr(args, "asset_unseeded_prob_gen2", ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[2])),
            "late": float(getattr(args, "asset_unseeded_prob_late", ASSET_UNSEEDED_FORCE_PROB_LATE)),
        },
        "timeframe_unseeded_force_probability": {
            "generation_1": float(getattr(args, "timeframe_unseeded_prob_gen1", TF_UNSEEDED_FORCE_PROB_BY_GENERATION[1])),
            "generation_2": float(getattr(args, "timeframe_unseeded_prob_gen2", TF_UNSEEDED_FORCE_PROB_BY_GENERATION[2])),
            "late": float(getattr(args, "timeframe_unseeded_prob_late", TF_UNSEEDED_FORCE_PROB_LATE)),
        },
        "force_unseeded_timeframe_min_ratios": FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS,
    }


def _config_target_diversity_caps(args: argparse.Namespace) -> dict[str, object]:
    """Topes de diversidad al elegir el target de cada variante."""
    return {
        "default_group_ratio": DEFAULT_TARGET_GROUP_CAP_RATIO,
        "group_ratios": TARGET_GROUP_CAP_RATIOS,
        "symbol_ratio": target_symbol_cap_ratio(bool(args.force_unseeded_universe)),
        "production_symbol_ratio": PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
        "discovery_symbol_ratio": DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
        "timeframe_ratio": TARGET_TIMEFRAME_CAP_RATIO,
        "symbol_timeframe_ratio": TARGET_PAIR_CAP_RATIO,
        "reroll_attempts": DIVERSITY_REROLL_ATTEMPTS,
        "production_reroll_attempts": PRODUCTION_DIVERSITY_REROLL_ATTEMPTS,
    }


def _config_seed_selection_caps(
    args: argparse.Namespace, discovery_source_mix: DiscoverySourceMix
) -> dict[str, object]:
    """Topes y reservas al elegir las seeds de cada generacion."""
    return {
        "default_group_ratio": DEFAULT_TARGET_GROUP_CAP_RATIO,
        "group_ratios": TARGET_GROUP_CAP_RATIOS,
        "symbol_ratio": TARGET_SYMBOL_CAP_RATIO,
        "timeframe_ratio": TARGET_TIMEFRAME_CAP_RATIO,
        "symbol_timeframe_ratio": TARGET_PAIR_CAP_RATIO,
        "discovery_symbol_reserve_ratio": DISCOVERY_SEED_SYMBOL_RESERVE_RATIO,
        "discovery_exploitable_seed_min_ratio": discovery_source_mix.exploitable_ratio,
        "discovery_source_mix_feedback": discovery_source_mix.to_dict(),
        "discovery_reinject_source_seeds": bool(args.force_unseeded_universe),
        "production_backfill_min_ratio": PRODUCTION_NEXT_SEED_BACKFILL_MIN_RATIO,
        "production_backfill_source_seeds": not bool(args.force_unseeded_universe),
        "production_symbol_ratio": PRODUCTION_SEED_SYMBOL_CAP_RATIO,
        "production_overflow_fill": False,
    }


def _config_target_policy(
    args: argparse.Namespace, discovery_target_policy_mix: DiscoveryTargetPolicyMix
) -> dict[str, object]:
    """Politica con la que se decide el target de cada variante."""
    return {
        "production_current_symbol_probability": PRODUCTION_CURRENT_SYMBOL_PROBABILITY,
        "production_random_universe_explore": False,
        "production_diversity_overflow": False,
        "production_rejected_survivor_fallback": False,
        "production_cross_group_feedback_fallback": False,
        "discovery_forced_unseeded": bool(args.force_unseeded_universe),
        "discovery_unseeded_asset_sampling": "sqrt_group_capacity_x_lifecycle_softmax",
        "discovery_group_feedback_temperature": DISCOVERY_GROUP_FEEDBACK_TEMPERATURE,
        "discovery_group_feedback_exp_limit": DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT,
        "discovery_adaptive_policy": discovery_target_policy_mix.to_dict(),
    }


def _config_selection_fitness(args: argparse.Namespace) -> dict[str, object]:
    """Modelos de aptitud con los que se puntuan y ordenan las seeds."""
    seed_model = (
        "beta_smoothed_root_descendant_final_tick_6m_v2"
        if args.force_unseeded_universe
        else "regularized_logistic_final_tick_6m_v1"
    )
    return {
        "model": seed_model,
        "source_seed_model": seed_model,
        "survivor_model": "regularized_logistic_final_tick_6m_v1",
        "target": "final_tick_6m_accepted",
        "exclude_current_run": True,
        "mode": SELECTION_FITNESS_MODE,
        "applied_weight_scale": SELECTION_FITNESS_APPLIED_SCALE,
    }


def _config_generation(
    args: argparse.Namespace, timeframe_universe, discovery_source_mix: DiscoverySourceMix,
    discovery_target_policy_mix: DiscoveryTargetPolicyMix,
) -> dict[str, object]:
    """Como se generan las variantes de cada generacion."""
    generation: dict[str, object] = {
        "mode": str(args.generation_mode),
        "generations": int(args.generations),
        "variants_per_seed": int(args.variants_per_seed),
        "max_seeds": int(args.max_seeds),
        "mutations_per_variant": int(args.mutations_per_variant),
        "top_percent": float(args.top_percent),
        "force_unseeded_universe": bool(args.force_unseeded_universe),
        "experimental_long_timeframes": bool(args.experimental_long_timeframes),
        "timeframe_universe": list(timeframe_universe),
        "long_timeframe_min_trades": {
            "W1": int(args.min_trades_w1),
            "MN": int(args.min_trades_mn),
        },
    }
    generation.update(_config_unseeded_probabilities(args))
    generation["target_diversity_caps"] = _config_target_diversity_caps(args)
    generation["seed_selection_diversity_caps"] = _config_seed_selection_caps(
        args, discovery_source_mix
    )
    generation["target_policy"] = _config_target_policy(args, discovery_target_policy_mix)
    generation["next_seed_diversity_caps"] = {
        "default_group_ratio": DEFAULT_TARGET_GROUP_CAP_RATIO,
        "group_ratios": TARGET_GROUP_CAP_RATIOS,
        "symbol_ratio": TARGET_SYMBOL_CAP_RATIO,
        "timeframe_ratio": TARGET_TIMEFRAME_CAP_RATIO,
        "symbol_timeframe_ratio": TARGET_PAIR_CAP_RATIO,
    }
    generation["selection_fitness"] = _config_selection_fitness(args)
    generation["feedback_model"] = {
        "model": "smoothed_stage_probability_v1",
        "stages": ["base", "robust", "probe", "six_month"],
        "selection_score": "relative_log_odds_x_confidence",
        "mutation_sampling": "percentile_multiplier_0.5_1.5",
    }
    return generation


def _config_execution(args: argparse.Namespace) -> dict[str, object]:
    """Como se ejecutan los backtests de este run."""
    return {
        "execute_backtests": bool(args.execute_backtests),
        "dry_run": bool(args.dry_run),
        "multi_terminal": bool(args.multi_terminal),
        "max_workers": int(args.max_workers),
        "delay": int(args.delay),
        "random_seed": args.random_seed,
        "random_stream_version": RANDOM_STREAM_VERSION,
        "from_date": str(args.from_date or ""),
        "to_date": str(args.to_date or ""),
        "symbol_map": str(args.symbol_map or ""),
    }


def _config_robustness_defaults(args: argparse.Namespace) -> dict[str, object]:
    """Umbrales por defecto de la etapa de robustez."""
    return {
        "positive_bonus": float(args.robust_positive_bonus),
        "negative_bonus": float(args.robust_negative_bonus),
        "min_net_retention": float(getattr(args, "robust_min_net_retention", DEFAULT_MIN_NET_RETENTION)),
        "min_pf_edge_retention": float(getattr(args, "robust_min_pf_edge_retention", DEFAULT_MIN_PF_EDGE_RETENTION)),
        "min_recovery_retention": float(getattr(args, "robust_min_recovery_retention", DEFAULT_MIN_RECOVERY_RETENTION)),
        "max_dd_inflation": float(getattr(args, "robust_max_dd_inflation", DEFAULT_MAX_DD_INFLATION)),
        "robust_run_id": args.robust_run_id,
    }


def _config_final_tick_defaults(args: argparse.Namespace) -> dict[str, object]:
    """Umbrales por defecto de la etapa de final tick."""
    return {
        "min_history_quality": float(args.final_tick_min_history_quality),
        "min_ohlc_trades": int(args.final_tick_min_ohlc_trades),
        "min_trades_w1": int(args.final_tick_min_trades_w1),
        "min_trades_mn": int(args.final_tick_min_trades_mn),
        "max_net_delta_pct": float(args.final_tick_max_net_delta_pct),
        "max_pf_delta_pct": float(args.final_tick_max_pf_delta_pct),
        "max_dd_delta_pct": float(args.final_tick_max_dd_delta_pct),
        "max_trades_delta_pct": float(args.final_tick_max_trades_delta_pct),
    }


def build_run_config(
    args: argparse.Namespace,
    score_config: ScoreConfig,
    *,
    source_dir: Path,
    output_root: Path,
    run_dir: Path,
    universe_symbol_count: int,
    universe_alias_count: int,
    disabled_symbol_count: int,
    seed_enabled_disabled_symbol_count: int,
    discovery_source_mix: DiscoverySourceMix,
    discovery_target_policy_mix: DiscoveryTargetPolicyMix,
) -> dict[str, object]:
    timeframe_universe = target_timeframe_universe(
        bool(args.experimental_long_timeframes),
        base_dir=BASE_DIR,
        broker=args.broker,
        account_type=args.account_type,
    )
    return {
        "schema_version": 2,
        "created_by": "ubs_agent.py",
        "broker": normalize_broker(args.broker),
        "account_type": normalize_account_type(args.account_type, args.broker),
        "paths": _config_paths(args, source_dir, output_root, run_dir),
        "generation": _config_generation(
            args, timeframe_universe, discovery_source_mix, discovery_target_policy_mix
        ),
        "execution": _config_execution(args),
        "universe": {
            "symbols": int(universe_symbol_count),
            "aliases": int(universe_alias_count),
            "disabled_symbols": int(disabled_symbol_count),
            "seed_enabled_disabled_symbols": int(seed_enabled_disabled_symbol_count),
        },
        "score": score_config.to_dict(),
        "robustness_defaults": _config_robustness_defaults(args),
        "final_tick_defaults": _config_final_tick_defaults(args),
        "args": {key: json_safe(value) for key, value in sorted(vars(args).items())},
    }


def restore_run_unseeded_probabilities(args: argparse.Namespace, config_json: object) -> None:
    """Restore persisted discovery schedules when a run is resumed."""

    try:
        config = json.loads(str(config_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(config, dict):
        return
    generation_config = config.get("generation")
    if not isinstance(generation_config, dict):
        return
    schedules = (
        (
            "asset_unseeded_force_probability",
            "asset_unseeded_prob_gen1",
            "asset_unseeded_prob_gen2",
            "asset_unseeded_prob_late",
        ),
        (
            "timeframe_unseeded_force_probability",
            "timeframe_unseeded_prob_gen1",
            "timeframe_unseeded_prob_gen2",
            "timeframe_unseeded_prob_late",
        ),
    )
    for config_key, gen1_attr, gen2_attr, late_attr in schedules:
        values = generation_config.get(config_key)
        if not isinstance(values, dict):
            continue
        for key, attr in (("generation_1", gen1_attr), ("generation_2", gen2_attr), ("late", late_attr)):
            if key not in values:
                continue
            try:
                setattr(args, attr, probability_argument(str(values[key])))
            except argparse.ArgumentTypeError:
                continue


def restored_discovery_exploitable_ratio(config_json: object) -> float:
    """Keep a resumed run on the source budget persisted when it was created."""

    try:
        config = json.loads(str(config_json or "{}"))
        value = config["generation"]["seed_selection_diversity_caps"][
            "discovery_exploitable_seed_min_ratio"
        ]
        return min(max(float(value), 0.0), 1.0)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DISCOVERY_EXPLOITABLE_SEED_MIN_RATIO


def restored_discovery_universe_feedback_probability(config_json: object) -> float:
    try:
        config = json.loads(str(config_json or "{}"))
        value = config["generation"]["target_policy"]["discovery_adaptive_policy"][
            "universe_feedback"
        ]["probability"]
        return min(max(float(value), 0.0), 1.0)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT


def restored_discovery_current_target_probability(config_json: object) -> float:
    try:
        config = json.loads(str(config_json or "{}"))
        value = config["generation"]["target_policy"]["discovery_adaptive_policy"][
            "current_target"
        ]["probability"]
        return min(max(float(value), 0.0), 1.0)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DISCOVERY_CURRENT_TARGET_DEFAULT


def restored_discovery_current_timeframe_probability(config_json: object) -> float:
    try:
        config = json.loads(str(config_json or "{}"))
        value = config["generation"]["target_policy"]["discovery_adaptive_policy"][
            "current_timeframe"
        ]["probability"]
        return min(max(float(value), 0.0), 1.0)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DISCOVERY_CURRENT_TIMEFRAME_DEFAULT


def regression_runtime(args: argparse.Namespace | None = None) -> RegressionRuntime:
    """Inject MT5/report helpers without coupling the regression domain module to this CLI."""

    return RegressionRuntime(
        missing_report_status=(
            (lambda symbol: missing_report_status(symbol, args)) if args is not None else None
        ),
        write_stage_set=write_retry_set,
        running_terminal_exit_code=RUNNING_TERMINAL_EXIT_CODE,
        recreate_work_dir=recreate_work_dir,
        remove_report_artifacts=remove_report_artifacts,
        variant_from_candidate_row=variant_from_candidate_row,
        run_backtests=run_backtests,
        find_report_for_set=find_report_for_set,
        find_watchdog_snapshot_for_set=find_watchdog_snapshot_for_set,
        parse_symbol_map=parse_symbol_map,
        report_matches_variant=report_matches_variant,
        report_has_empty_tester_context=report_has_empty_tester_context,
        read_report_dates=_read_ohlc_report_cfg_dates,
        tester_log_no_history_metadata=tester_log_no_history_metadata,
    )
