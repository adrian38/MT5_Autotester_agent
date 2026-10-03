"""Continuacion de un run guardado: generaciones pendientes y nuevas."""
from __future__ import annotations

import argparse
from pathlib import Path

from run_tests import parse_symbol_map
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig
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
    generation_feedback_terminal_stage,
    generation_fitness_target,
    generation_seed_fitness_predictions,
    select_next_generation_survivors,
)
from ubs_agent_history import (
    evaluate_generation,
)
from ubs_agent_policy import (
    discovery_ranked_seed_selection,
    production_viable_source_seeds,
    unseeded_universe_targets,
)
from ubs_agent_run_config import (
    restore_run_unseeded_probabilities,
    restored_discovery_current_target_probability,
    restored_discovery_current_timeframe_probability,
    restored_discovery_exploitable_ratio,
    restored_discovery_universe_feedback_probability,
)
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
    seeds_from_variants,
    target_symbol_cap_ratio,
    timeframe_plan_summary,
)
from ubs_agent_targets import (
    choose_diverse_target,
)
from ubs_agent_universe import (
    target_timeframe_universe,
)
from ubs_agent_variants import (
    create_variant,
)


from ubs_agent_resume_generation import (
    build_resume_context,
    latest_run_seeds,
    resume_generations,
    resume_start_seeds,
)


def resume_last_run(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    """Continua el ultimo run guardado donde se quedo."""
    run = memory.latest_run()
    if run is None:
        print("ERROR: no hay runs guardados para continuar")
        return 1
    context = build_resume_context(args, memory, score_config, run)
    if isinstance(context, int):
        return context
    pending_generation = (
        memory.pending_generated_generation(context.run_id) if args.execute_backtests else 0
    )
    outcome, did_work = latest_run_seeds(context, pending_generation)
    if outcome.exit_code is not None:
        return outcome.exit_code
    current_seeds, error_code = resume_start_seeds(context, outcome.current_seeds or [])
    if current_seeds is None:
        return error_code
    return resume_generations(context, outcome.next_generation, current_seeds, did_work)
