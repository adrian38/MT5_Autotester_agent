"""Contexto y generaciones de un run que se reanuda."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from run_tests import parse_symbol_map
from ubs.memory import AgentMemory
from ubs.models import Variant
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
    generation_fitness_target,
    select_next_generation_survivors,
)
from ubs_agent_history import (
    evaluate_generation,
)
from ubs_agent_run_config import (
    restore_run_unseeded_probabilities,
    restored_discovery_current_target_probability,
    restored_discovery_current_timeframe_probability,
    restored_discovery_exploitable_ratio,
    restored_discovery_universe_feedback_probability,
)
from ubs_agent_run_generation import (
    _choose_target,
    _generation_feedback,
    _next_seed_pool,
    _print_generation_plan,
    _select_seeds,
    _selection_pool,
    _target_plan,
)
from ubs_agent_seeds_plan import (
    apply_reserved_timeframe,
    asset_group_map,
    disabled_symbols_file_for_account,
    generation_source_seeds,
    next_generation_seed_pool,
    seeds_from_survivors,
    seeds_from_variants,
)
from ubs_agent_universe import target_timeframe_universe
from ubs_agent_variants import create_variant


@dataclass
class _ResumeContext:
    """Todo lo que la continuacion necesita del run guardado y del universo."""

    args: argparse.Namespace
    memory: AgentMemory
    score_config: ScoreConfig
    run_id: int
    run_dir: Path
    planned_generations: int
    max_generation: int
    source_seeds: list
    symbol_map: dict
    disabled_symbols: set
    seed_enabled_when_disabled: set
    universe_symbols: tuple
    aliases: dict
    group_by_symbol: dict
    timeframe_universe: tuple
    exploitable_ratio: float
    universe_feedback_probability: float
    current_target_probability: float
    current_timeframe_probability: float


@dataclass
class _ResumeOutcome:
    """Que hacer tras una generacion reanudada."""

    exit_code: int | None = None
    stop: bool = False
    variants_generated: int = 0
    next_generation: int = 0
    current_seeds: list | None = None


def _restore_run_args(args: argparse.Namespace, run) -> None:
    """Devuelve a los argumentos la configuracion con la que nacio el run."""
    restore_run_unseeded_probabilities(args, run["config_json"])
    args.variants_per_seed = int(run["variants_per_seed"])
    args.max_seeds = int(run["max_seeds"])
    args.execute_backtests = bool(run["execute_backtests"])
    args.dry_run = bool(run["dry_run"]) or args.dry_run


def _resume_universe(args: argparse.Namespace):
    """Universo y politica de simbolos vigentes para la continuacion."""
    disabled_policy_path = disabled_symbols_file_for_account(args.account_type, args.broker)
    disabled_symbols = load_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled = load_seed_enabled_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled &= disabled_symbols
    symbol_map = parse_symbol_map(args.symbol_map)
    asset_groups, aliases = load_asset_universe(
        Path(args.assets).expanduser(),
        disabled_symbols=disabled_symbols,
    )
    universe_symbols = tuple(symbol for symbols in asset_groups.values() for symbol in symbols)
    aliases = augment_aliases_with_symbol_map(aliases, symbol_map, universe_symbols)
    return (
        symbol_map, disabled_symbols, seed_enabled_when_disabled,
        universe_symbols, aliases, asset_group_map(asset_groups, aliases),
    )


def _resume_source_seeds(args: argparse.Namespace, memory: AgentMemory, run, universe) -> list:
    """Seeds fuente del run, filtradas por la politica actual del universo."""
    symbol_map, disabled_symbols, seed_enabled_when_disabled = universe
    run_source_dir = resolve_workspace_path(run["source_dir"])
    run_source_seeds = memory.apply_seed_overrides(load_seeds(run_source_dir, base_dir=BASE_DIR))
    run_source_seeds, source_blocked_count = generation_source_seeds(
        run_source_seeds,
        symbol_map,
        disabled_symbols,
        seed_enabled_when_disabled,
    )
    if source_blocked_count:
        print(f"Seeds fuente bloqueadas para reserva discovery: {source_blocked_count}")
    return run_source_seeds


def build_resume_context(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig, run
) -> _ResumeContext | int:
    """Prepara la continuacion; devuelve un codigo de error si no es posible."""
    run_id = int(run["id"])
    _restore_run_args(args, run)
    if args.execute_backtests and not args.expert and not args.multi_terminal:
        print("ERROR: el run pendiente requiere backtests; indica --expert o activa --multi-terminal para continuar")
        return 1
    (
        symbol_map, disabled_symbols, seed_enabled_when_disabled,
        universe_symbols, aliases, group_by_symbol,
    ) = _resume_universe(args)
    if not universe_symbols:
        print("ERROR: no hay simbolos activos en Universo para generar targets")
        return 1
    print(f"Universo {args.broker} cargado: {len(universe_symbols)} simbolos, {len(aliases)} aliases")
    planned_generations = int(run["generations"])
    max_generation = memory.max_generation(run_id)
    print(f"Continuando run #{run_id}: plan={planned_generations}, ultima_gen={max_generation}")
    return _ResumeContext(
        args=args, memory=memory, score_config=score_config, run_id=run_id,
        run_dir=resolve_workspace_path(run["output_dir"]),
        planned_generations=planned_generations,
        max_generation=max_generation,
        source_seeds=_resume_source_seeds(
            args, memory, run, (symbol_map, disabled_symbols, seed_enabled_when_disabled)
        ),
        symbol_map=symbol_map,
        disabled_symbols=disabled_symbols,
        seed_enabled_when_disabled=seed_enabled_when_disabled,
        universe_symbols=universe_symbols,
        aliases=aliases,
        group_by_symbol=group_by_symbol,
        timeframe_universe=target_timeframe_universe(
            bool(args.experimental_long_timeframes),
            base_dir=BASE_DIR,
            broker=args.broker,
            account_type=args.account_type,
        ),
        exploitable_ratio=restored_discovery_exploitable_ratio(run["config_json"]),
        universe_feedback_probability=restored_discovery_universe_feedback_probability(
            run["config_json"]
        ),
        current_target_probability=restored_discovery_current_target_probability(
            run["config_json"]
        ),
        current_timeframe_probability=restored_discovery_current_timeframe_probability(
            run["config_json"]
        ),
    )


def _seeds_after_generation(ctx: _ResumeContext, generation: int, scored: list, variants: list) -> list:
    """Seeds de la generacion siguiente tras puntuar la actual."""
    args = ctx.args
    if scored:
        next_survivors = select_next_generation_survivors(
            ctx.memory,
            ctx.run_id,
            scored,
            args.top_percent,
            args.max_seeds,
            ctx.aliases,
            ctx.group_by_symbol,
            allow_rejected_fallback=bool(args.force_unseeded_universe),
            fitness_target=generation_fitness_target(bool(args.force_unseeded_universe)),
        )
        if not args.force_unseeded_universe and not next_survivors:
            print(
                f"Production: gen {generation} sin accepted; "
                "no se usan rechazados como seeds, se reponen source seeds"
            )
            diag_log(f"PRODUCTION_NO_ACCEPTED_SOURCE_BACKFILL run_id={ctx.run_id} generation={generation}")
        next_seeds = seeds_from_survivors(next_survivors)
    else:
        next_seeds = seeds_from_variants(variants)
    return next_generation_seed_pool(
        next_seeds,
        ctx.source_seeds,
        args.max_seeds,
        force_unseeded_universe=args.force_unseeded_universe,
    )


def resume_pending_generation(ctx: _ResumeContext, pending_generation: int) -> _ResumeOutcome:
    """Termina los backtests que quedaron pendientes en la ultima generacion."""
    args = ctx.args
    variants = ctx.memory.variants_for_generation(ctx.run_id, pending_generation, status="generated")
    if not variants:
        print(f"ERROR: gen {pending_generation} marcada pendiente, pero no hay .set disponibles")
        return _ResumeOutcome(exit_code=1)
    print(f"Reanudando gen {pending_generation}: backtests pendientes={len(variants)}")
    try:
        scored = evaluate_generation(
            args, ctx.memory, ctx.run_dir, pending_generation, variants, ctx.score_config
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return _ResumeOutcome(exit_code=1)
    if args.execute_backtests and not args.dry_run and not scored:
        print(f"ERROR: gen {pending_generation} no produjo reportes puntuables; no se genera la siguiente generacion")
        return _ResumeOutcome(exit_code=1)
    current_seeds = _seeds_after_generation(ctx, pending_generation, scored, variants)
    if args.backtest_pending_only:
        print(f"Backtests pendientes completados en gen {pending_generation}; no se generan nuevas generaciones.")
        print(f"Run dir: {ctx.run_dir}")
        print(f"Memoria: {ctx.memory.path}")
        return _ResumeOutcome(exit_code=0)
    return _ResumeOutcome(current_seeds=current_seeds, next_generation=pending_generation + 1)


def continuation_seeds(ctx: _ResumeContext) -> _ResumeOutcome:
    """Seeds y generacion de arranque cuando no hay backtests pendientes."""
    args = ctx.args
    if args.backtest_pending_only:
        print(f"Run #{ctx.run_id} no tiene candidatos generated pendientes para backtest.")
        return _ResumeOutcome(exit_code=0)
    if ctx.max_generation >= ctx.planned_generations:
        print(f"Run #{ctx.run_id} ya esta completo: {ctx.max_generation}/{ctx.planned_generations}")
        return _ResumeOutcome(exit_code=0)
    seed_limit = args.max_seeds if args.max_seeds > 0 else 0
    _, latest_generation, current_seeds = ctx.memory.continuation_seeds(seed_limit)
    return _ResumeOutcome(
        current_seeds=next_generation_seed_pool(
            current_seeds,
            ctx.source_seeds,
            args.max_seeds,
            force_unseeded_universe=args.force_unseeded_universe,
        ),
        next_generation=latest_generation + 1,
    )


def _create_resume_variants(
    ctx: _ResumeContext, generation: int, generation_dir: Path, selected_seeds: list,
    feedback, selection_rng, plan,
) -> list[Variant]:
    """Crea las variantes de la generacion reanudada."""
    args = ctx.args
    variants: list[Variant] = []
    for seed_index, seed in enumerate(selected_seeds, start=1):
        for variant_index in range(1, args.variants_per_seed + 1):
            target_choice = _choose_target(ctx, seed, feedback, selection_rng, plan)
            if target_choice is None:
                diag_log(
                    f"TARGET_CAPS_EXHAUSTED run_id={ctx.run_id} generation={generation} "
                    f"seed_index={seed_index} variant_index={variant_index} seed={seed.symbol}/{seed.period}"
                )
                break
            target_symbol, target_period, policy = target_choice
            target_symbol, target_period, policy = apply_reserved_timeframe(
                reserved_timeframes=plan.reserved_timeframes,
                target_symbol=target_symbol,
                target_period=target_period,
                policy=policy,
                seed=seed,
                target_limiter=plan.limiter,
                universe_symbols=ctx.universe_symbols,
                disabled_symbols=ctx.disabled_symbols,
                aliases=ctx.aliases,
                rng=selection_rng,
            )
            variant = create_variant(
                seed,
                target_symbol,
                target_period,
                generation_dir,
                generation,
                seed_index,
                variant_index,
                args.mutations_per_variant,
                feedback.mutation,
                feedback.mutation_direction,
                policy,
                generation_random_stream(
                    args.random_seed,
                    generation,
                    "mutation",
                    seed_index,
                    variant_index,
                ),
            )
            ctx.memory.record_variant(ctx.run_id, generation, variant)
            plan.limiter.record(target_symbol, target_period)
            variants.append(variant)
    return variants


def resume_generation(ctx: _ResumeContext, generation: int, current_seeds: list) -> _ResumeOutcome:
    """Una generacion nueva del run reanudado, con sus backtests."""
    args = ctx.args
    selection_rng = generation_random_stream(args.random_seed, generation, "selection")
    generation_dir = ctx.run_dir / f"gen_{generation:03d}"
    feedback = _generation_feedback(ctx, current_seeds)
    selection_pool = _selection_pool(ctx, current_seeds, generation)
    if selection_pool is None:
        return _ResumeOutcome(stop=True)
    selected_seed_rankings = _select_seeds(ctx, selection_pool, feedback, selection_rng)
    selected_seeds = [seed for _, seed, _, _, _ in selected_seed_rankings]
    ctx.memory.record_seed_selection(
        ctx.run_id, generation, selected_seed_rankings, feedback.fitness_predictions
    )
    plan = _target_plan(ctx, generation, current_seeds, selected_seeds)
    _print_generation_plan(ctx, generation, selected_seeds, plan)
    variants = _create_resume_variants(
        ctx, generation, generation_dir, selected_seeds, feedback, selection_rng, plan
    )
    if not variants:
        print(f"Generacion {generation}: sin targets permitidos bajo caps production; se detiene")
        diag_log(f"GENERATION_STOP_NO_TARGETS run_id={ctx.run_id} generation={generation}")
        return _ResumeOutcome(stop=True)
    print(f"Generados: {len(variants)} en {generation_dir}")
    try:
        scored = evaluate_generation(
            args, ctx.memory, ctx.run_dir, generation, variants, ctx.score_config
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return _ResumeOutcome(exit_code=1)
    if args.execute_backtests and not args.dry_run and not scored:
        print(f"ERROR: gen {generation} no produjo reportes puntuables; se detiene la continuacion")
        return _ResumeOutcome(exit_code=1)
    return _ResumeOutcome(
        variants_generated=len(variants),
        current_seeds=_seeds_after_generation(ctx, generation, scored, variants),
    )


def resume_start_seeds(ctx: _ResumeContext, current_seeds: list) -> tuple[list | None, int]:
    """Filtra las seeds de arranque por la politica actual del universo."""
    current_seeds, blocked_source_count = generation_source_seeds(
        current_seeds,
        ctx.symbol_map,
        ctx.disabled_symbols,
        ctx.seed_enabled_when_disabled,
    )
    if blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {blocked_source_count}")
    if not current_seeds:
        print("ERROR: no hay seeds disponibles para generar con la politica actual del Universo")
        return None, 1
    return current_seeds, 0


def resume_generations(
    ctx: _ResumeContext, next_generation: int, current_seeds: list, did_work: bool
) -> int:
    """Encadena las generaciones que faltan y resume la continuacion."""
    all_generated = 0
    print(f"Universo TF target: {', '.join(ctx.timeframe_universe)}")
    for generation in range(next_generation, ctx.planned_generations + 1):
        outcome = resume_generation(ctx, generation, current_seeds)
        if outcome.exit_code is not None:
            return outcome.exit_code
        if outcome.stop:
            break
        all_generated += outcome.variants_generated
        did_work = True
        current_seeds = outcome.current_seeds or []
    print(f"Run dir: {ctx.run_dir}")
    print(f"Memoria: {ctx.memory.path}")
    print(f"Sets nuevos generados: {all_generated}")
    return 0 if did_work else 1


def latest_run_seeds(ctx: _ResumeContext, pending_generation: int) -> tuple[_ResumeOutcome, bool]:
    """Seeds y generacion de arranque, segun haya backtests pendientes o no."""
    if pending_generation:
        return resume_pending_generation(ctx, pending_generation), True
    return continuation_seeds(ctx), False
