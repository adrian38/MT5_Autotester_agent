"""Preparacion del ciclo del agente: universo, seeds y mezclas."""
from __future__ import annotations

import argparse
import json
import sys
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

@dataclass
class _AgentSetup:
    """Universo, seeds y mezclas con los que arranca el ciclo del agente."""

    args: argparse.Namespace
    memory: AgentMemory
    score_config: ScoreConfig
    source_dir: Path
    output_root: Path
    run_dir: Path
    seeds: list
    source_seeds: list
    blocked_source_count: int
    symbol_map: dict
    disabled_symbols: set
    seed_enabled_when_disabled: set
    universe_symbols: tuple
    aliases: dict
    group_by_symbol: dict
    timeframe_universe: tuple
    discovery_source_mix: object = None
    discovery_target_policy_mix: object = None
    run_id: int = 0


def _build_setup(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    source_dir: Path, output_root: Path, run_dir: Path,
) -> _AgentSetup | int:
    """Carga seeds y universo; devuelve un codigo de error si falta algo."""
    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    disabled_policy_path = disabled_symbols_file_for_account(args.account_type, args.broker)
    disabled_symbols = load_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled = load_seed_enabled_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled &= disabled_symbols
    symbol_map = parse_symbol_map(args.symbol_map)
    source_seeds, blocked_source_count = generation_source_seeds(
        seeds,
        symbol_map,
        disabled_symbols,
        seed_enabled_when_disabled,
    )
    if not source_seeds:
        print("ERROR: no hay seeds disponibles como fuente con la politica actual del Universo")
        return 1
    asset_groups, aliases = load_asset_universe(
        Path(args.assets).expanduser(),
        disabled_symbols=disabled_symbols,
    )
    universe_symbols = tuple(symbol for symbols in asset_groups.values() for symbol in symbols)
    if not universe_symbols:
        print("ERROR: no hay simbolos activos en Universo para generar targets")
        return 1
    aliases = augment_aliases_with_symbol_map(aliases, symbol_map, universe_symbols)
    return _AgentSetup(
        args=args, memory=memory, score_config=score_config, source_dir=source_dir,
        output_root=output_root, run_dir=run_dir, seeds=seeds, source_seeds=source_seeds,
        blocked_source_count=blocked_source_count, symbol_map=symbol_map,
        disabled_symbols=disabled_symbols, seed_enabled_when_disabled=seed_enabled_when_disabled,
        universe_symbols=universe_symbols, aliases=aliases,
        group_by_symbol=asset_group_map(asset_groups, aliases),
        timeframe_universe=target_timeframe_universe(
            bool(args.experimental_long_timeframes),
            base_dir=BASE_DIR,
            broker=args.broker,
            account_type=args.account_type,
        ),
    )


def _print_universe_summary(setup: _AgentSetup) -> None:
    """Resumen de seeds y universo disponibles para esta ejecucion."""
    print(f"Seeds disponibles: {len(setup.seeds)} ({setup.source_dir})")
    if setup.blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {setup.blocked_source_count}")
    if setup.seed_enabled_when_disabled:
        print(f"Symbols deshabilitados con SEEDS activo: {len(setup.seed_enabled_when_disabled)}")
    print(
        f"Universo {setup.args.broker} cargado: {len(setup.universe_symbols)} simbolos, "
        f"{len(setup.aliases)} aliases"
    )
    print(f"Universo TF target: {', '.join(setup.timeframe_universe)}")


def _apply_discovery_mixes(setup: _AgentSetup) -> None:
    """Calcula y anuncia la mezcla de fuentes y de politicas de target."""
    setup.discovery_source_mix = discovery_source_mix_feedback(
        setup.memory,
        setup.universe_symbols,
        setup.aliases,
        symbol_map=setup.symbol_map,
        disabled_symbols=setup.disabled_symbols,
        group_by_symbol=setup.group_by_symbol,
    )
    setup.discovery_target_policy_mix = discovery_target_policy_feedback(setup.memory)
    apply_discovery_target_policy_schedule(setup.args, setup.discovery_target_policy_mix)
    print(
        "Discovery source mix: "
        f"explotable={setup.discovery_source_mix.exploitable_ratio:.1%}, "
        f"cross={1.0 - setup.discovery_source_mix.exploitable_ratio:.1%}, "
        f"evidence={setup.discovery_source_mix.reason}"
    )
    print(
        "Discovery target mix: "
        f"unseeded_scale={setup.discovery_target_policy_mix.unseeded_multiplier:.1%}, "
        f"universe_feedback={setup.discovery_target_policy_mix.universe_feedback_probability:.1%}, "
        f"current_target={setup.discovery_target_policy_mix.current_target_probability:.1%}, "
        f"current_tf={setup.discovery_target_policy_mix.current_timeframe_probability:.1%}"
    )


def _print_pass_config(score_config: ScoreConfig) -> None:
    """Umbrales con los que se aceptara o rechazara cada candidato."""
    monthly_pass = (
        f"meses+>={score_config.min_positive_month_ratio}"
        if score_config.min_positive_month_ratio > 0
        else "estabilidad mensual solo score"
    )
    print(
        "Pass config: "
        f"net>{score_config.min_net_profit}, "
        f"pf>={score_config.min_profit_factor}, "
        f"trades>={score_config.min_trades}, "
        f"dd%<={score_config.max_drawdown_pct}, "
        f"recovery>={score_config.min_recovery_factor}, "
        f"{monthly_pass}"
    )


def _create_run(setup: _AgentSetup) -> int:
    """Abre el run en memoria con la configuracion completa de la ejecucion."""
    args = setup.args
    return setup.memory.create_run(
        setup.source_dir,
        setup.run_dir,
        args.generations,
        args.variants_per_seed,
        args.max_seeds,
        args.execute_backtests,
        args.dry_run,
        config=build_run_config(
            args,
            setup.score_config,
            source_dir=setup.source_dir,
            output_root=setup.output_root,
            run_dir=setup.run_dir,
            universe_symbol_count=len(setup.universe_symbols),
            universe_alias_count=len(setup.aliases),
            disabled_symbol_count=len(setup.disabled_symbols),
            seed_enabled_disabled_symbol_count=len(setup.seed_enabled_when_disabled),
            discovery_source_mix=setup.discovery_source_mix,
            discovery_target_policy_mix=setup.discovery_target_policy_mix,
        ),
    )
