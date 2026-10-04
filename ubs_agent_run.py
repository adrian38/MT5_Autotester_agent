"""Ciclo completo del agente: generaciones, etapas y cierre."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.memory import AgentMemory
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult
from ubs.risk_profit import RiskProfitConfig
from ubs.seeds import load_seeds
from ubs.universe import (
    augment_aliases_with_symbol_map,
    load_asset_universe,
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
)
from ubs_agent_config import (
    BASE_DIR,
    diag_log,
    generation_random_stream,
)
from ubs_agent_evaluate import (
    copy_accepted,
    evaluate_variants,
    generation_feedback_terminal_stage,
    generation_fitness_target,
    generation_seed_fitness_predictions,
    select_next_generation_survivors,
    select_survivors,
)
from ubs_agent_run_modes import run_standalone_mode
from ubs_agent_policy import (
    apply_discovery_target_policy_schedule,
    discovery_ranked_seed_selection,
    discovery_source_mix_feedback,
    discovery_target_policy_feedback,
    production_viable_source_seeds,
    unseeded_universe_targets,
)
from ubs_agent_run_config import build_run_config
from ubs_agent_seeds_plan import (
    TargetDiversityLimiter,
    apply_reserved_timeframe,
    asset_group_map,
    configured_unseeded_force_probabilities,
    disabled_symbols_file_for_account,
    format_group_cap_summary,
    generation_source_seeds,
    next_generation_seed_pool,
    ranked_seed_selection,
    reserved_timeframe_plan,
    seed_symbol_cap_ratio,
    seeds_from_survivors,
    target_symbol_cap_ratio,
    timeframe_plan_summary,
    variant_as_next_seed,
)
from ubs_agent_targets import (
    choose_diverse_target,
)
from ubs_agent_universe import broker_universe_symbols, target_timeframe_universe
from ubs_agent_variants import (
    create_variant,
    run_backtests,
)

from ubs_agent_run_setup import (  # noqa: F401
    _AgentSetup,
    _apply_discovery_mixes,
    _build_setup,
    _create_run,
    _print_pass_config,
    _print_universe_summary,
)
from ubs_agent_run_generation import _run_generations  # noqa: F401


def _score_config_from_args(args: argparse.Namespace) -> ScoreConfig:
    """Umbrales de aceptacion, incluida la politica de riesgo/beneficio."""
    policy_values = {}
    policy_path = getattr(args, "risk_profit_config", None)
    if policy_path:
        policy_values = json.loads(Path(policy_path).read_text(encoding="utf-8-sig"))
    if getattr(args, "risk_profit_mode", None) is not None:
        policy_values["mode"] = args.risk_profit_mode
    return ScoreConfig(
        min_net_profit=args.min_net_profit,
        min_profit_factor=args.min_profit_factor,
        min_trades=args.min_trades,
        max_drawdown_pct=args.max_drawdown_pct,
        min_recovery_factor=args.min_recovery_factor,
        min_positive_month_ratio=args.min_positive_month_ratio,
        risk_profit=RiskProfitConfig.from_dict(policy_values),
    )


def run_agent(args: argparse.Namespace, api=None) -> int:
    """Ciclo completo del agente sobre el universo y las seeds configuradas."""
    score_config = _score_config_from_args(args)
    source_dir = resolve_workspace_path(args.source_dir)
    output_root = resolve_workspace_path(args.output_dir)
    run_dir = output_root / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    memory = AgentMemory(resolve_workspace_path(args.memory))
    if getattr(args, "prepared_manifest", None):
        try:
            from ubs.prepared import run_prepared
            if api is None:
                raise RuntimeError("Prepared mode requires the ubs_agent facade API")
            return run_prepared(args, memory, score_config, api)
        finally:
            memory.close()
    standalone_code = run_standalone_mode(args, memory, score_config)
    if standalone_code is not None:
        return standalone_code
    setup = _build_setup(args, memory, score_config, source_dir, output_root, run_dir)
    if isinstance(setup, int):
        return setup
    _print_universe_summary(setup)
    _apply_discovery_mixes(setup)
    _print_pass_config(score_config)
    setup.run_id = _create_run(setup)
    try:
        return _run_generations(setup)
    finally:
        memory.close()
