"""Una generacion del agente: seleccion, variantes, backtests y relevo."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.models import Variant
from ubs_agent_config import (
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
from ubs_agent_policy import (
    discovery_ranked_seed_selection,
    production_viable_source_seeds,
    unseeded_universe_targets,
)
from ubs_agent_seeds_plan import (
    TargetDiversityLimiter,
    apply_reserved_timeframe,
    configured_unseeded_force_probabilities,
    format_group_cap_summary,
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
from ubs_agent_universe import (
    broker_universe_symbols,
)
from ubs_agent_variants import (
    create_variant,
    run_backtests,
)

from ubs_agent_run_setup import _AgentSetup


@dataclass
class _GenerationFeedback:
    """Realimentacion de memoria que guia una generacion."""

    mutation: object
    mutation_direction: object
    asset: object
    asset_group: object
    timeframe: object
    fitness_predictions: dict
    fitness: dict


@dataclass
class _GenerationOutcome:
    """Que hacer tras una generacion: seguir, parar o salir con un codigo."""

    exit_code: int | None = None
    stop: bool = False
    variants_generated: int = 0
    current_seeds: list | None = None


def _generation_feedback(setup: _AgentSetup, current_seeds: list) -> _GenerationFeedback:
    """Realimentacion de mutaciones, activos y marcos para esta generacion."""
    terminal_stage = generation_feedback_terminal_stage(
        bool(setup.args.force_unseeded_universe)
    )
    asset_feedback, asset_group_feedback = setup.memory.asset_feedback_with_groups(
        setup.aliases,
        setup.group_by_symbol,
        terminal_stage=terminal_stage,
    )
    fitness_predictions = generation_seed_fitness_predictions(
        setup.memory,
        current_seeds,
        run_id=setup.run_id,
        force_unseeded_universe=bool(setup.args.force_unseeded_universe),
    )
    return _GenerationFeedback(
        mutation=setup.memory.mutation_feedback(terminal_stage=terminal_stage),
        mutation_direction=setup.memory.mutation_direction_feedback(terminal_stage=terminal_stage),
        asset=asset_feedback,
        asset_group=asset_group_feedback,
        timeframe=setup.memory.timeframe_feedback(terminal_stage=terminal_stage),
        fitness_predictions=fitness_predictions,
        fitness={path: prediction.weight for path, prediction in fitness_predictions.items()},
    )


def _selection_pool(setup: _AgentSetup, current_seeds: list, generation: int) -> list | None:
    """Seeds que pueden usarse como fuente; None si production se queda sin ellas."""
    if setup.args.force_unseeded_universe:
        return current_seeds
    selection_pool = production_viable_source_seeds(
        current_seeds,
        setup.universe_symbols,
        setup.aliases,
        symbol_map=setup.symbol_map,
        disabled_symbols=setup.disabled_symbols,
        group_by_symbol=setup.group_by_symbol,
    )
    skipped = len(current_seeds) - len(selection_pool)
    if skipped:
        print(f"Production: seeds sin target viable por grupo/relacion descartadas: {skipped}")
        diag_log(f"PRODUCTION_SKIPPED_UNVIABLE_SOURCE_SEEDS run_id={setup.run_id} generation={generation} skipped={skipped}")
    if not selection_pool:
        print(f"Production: gen {generation} sin seeds con target viable; se detiene")
        diag_log(f"GENERATION_STOP_NO_VIABLE_SOURCE_SEEDS run_id={setup.run_id} generation={generation}")
        return None
    return selection_pool


def _select_seeds(
    setup: _AgentSetup, selection_pool: list, feedback: _GenerationFeedback, selection_rng: object
) -> list:
    """Ranking de seeds con el que se construiran las variantes."""
    args = setup.args
    if args.force_unseeded_universe:
        return discovery_ranked_seed_selection(
            selection_pool,
            args.max_seeds,
            feedback.asset,
            feedback.timeframe,
            selection_rng,
            setup.universe_symbols,
            setup.aliases,
            symbol_map=setup.symbol_map,
            disabled_symbols=setup.disabled_symbols,
            group_by_symbol=setup.group_by_symbol,
            fitness_feedback=feedback.fitness,
            exploitable_min_ratio=setup.exploitable_ratio,
        )
    return ranked_seed_selection(
        selection_pool,
        args.max_seeds,
        feedback.asset,
        feedback.timeframe,
        selection_rng,
        setup.aliases,
        setup.group_by_symbol,
        feedback.fitness,
        symbol_cap_ratio=seed_symbol_cap_ratio(False),
        allow_overflow=False,
    )


@dataclass
class _TargetPlan:
    """Limites y cuotas con los que se eligen los targets de una generacion."""

    limiter: TargetDiversityLimiter
    unseeded_symbols: list
    unseeded_timeframes: list
    asset_unseeded_probability: float
    timeframe_unseeded_probability: float
    reserved_timeframes: list


def _target_plan(
    setup: _AgentSetup, generation: int, current_seeds: list, selected_seeds: list
) -> _TargetPlan:
    """Prepara caps de diversidad, cuotas sin seed y reserva de marcos."""
    args = setup.args
    unseeded_symbols, unseeded_timeframes = unseeded_universe_targets(
        current_seeds,
        setup.universe_symbols,
        setup.aliases,
        setup.timeframe_universe,
    )
    asset_unseeded_probability, tf_unseeded_probability = configured_unseeded_force_probabilities(
        args,
        generation,
        len(unseeded_symbols),
        len(unseeded_timeframes),
    )
    planned_variants = len(selected_seeds) * args.variants_per_seed
    return _TargetPlan(
        limiter=TargetDiversityLimiter(
            planned_variants,
            setup.aliases,
            group_by_symbol=setup.group_by_symbol,
            symbol_cap_ratio=target_symbol_cap_ratio(args.force_unseeded_universe),
        ),
        unseeded_symbols=unseeded_symbols,
        unseeded_timeframes=unseeded_timeframes,
        asset_unseeded_probability=asset_unseeded_probability,
        timeframe_unseeded_probability=tf_unseeded_probability,
        reserved_timeframes=(
            reserved_timeframe_plan(selected_seeds, setup.timeframe_universe, planned_variants)
            if args.force_unseeded_universe
            else []
        ),
    )


def _print_generation_plan(
    setup: _AgentSetup, generation: int, selected_seeds: list, plan: _TargetPlan
) -> None:
    """Anuncia seeds, caps de diversidad y cuotas exploratorias."""
    print(f"Generacion {generation}: seeds={len(selected_seeds)}")
    print(
        "Caps diversidad target: "
        f"grupos[{format_group_cap_summary(plan.limiter)}], "
        f"simbolo<={plan.limiter.symbol_cap}, TF<={plan.limiter.timeframe_cap}, "
        f"simbolo+TF<={plan.limiter.pair_cap}"
    )
    if setup.args.force_unseeded_universe:
        print(
            f"Exploracion forzada sin seed: activos={len(plan.unseeded_symbols)}, "
            f"TF={len(plan.unseeded_timeframes)} | "
            f"prob_activo={plan.asset_unseeded_probability:.0%}, "
            f"prob_TF={plan.timeframe_unseeded_probability:.0%}"
        )
        if plan.reserved_timeframes:
            print(f"Cuota/reserva TF exploratoria: {timeframe_plan_summary(plan.reserved_timeframes)}")


def _choose_target(
    setup: _AgentSetup, seed: object, feedback: _GenerationFeedback,
    selection_rng: object, plan: _TargetPlan,
):
    """Elige simbolo, marco y politica del siguiente target."""
    args = setup.args
    return choose_diverse_target(
        seed,
        feedback.asset,
        feedback.timeframe,
        selection_rng,
        plan.limiter,
        setup.universe_symbols,
        setup.aliases,
        timeframe_universe=setup.timeframe_universe,
        symbol_map=setup.symbol_map,
        disabled_symbols=setup.disabled_symbols,
        force_unseeded_universe=args.force_unseeded_universe,
        unseeded_universe_symbols=plan.unseeded_symbols,
        unseeded_timeframes=plan.unseeded_timeframes,
        asset_unseeded_probability=plan.asset_unseeded_probability,
        timeframe_unseeded_probability=plan.timeframe_unseeded_probability,
        production_mode=not args.force_unseeded_universe,
        group_by_symbol=setup.group_by_symbol,
        asset_group_feedback=feedback.asset_group,
        universe_feedback_probability=setup.universe_feedback_probability,
        current_target_probability=setup.current_target_probability,
        current_timeframe_probability=setup.current_timeframe_probability,
    )


def _create_seed_variants(
    setup: _AgentSetup, generation: int, generation_dir: Path, seed: object, seed_index: int,
    feedback: _GenerationFeedback, selection_rng: object, plan: _TargetPlan,
    variants: list[Variant], planned_variants: int,
) -> None:
    """Crea las variantes de una seed hasta agotar sus targets permitidos."""
    args = setup.args
    for variant_index in range(1, args.variants_per_seed + 1):
        target_choice = _choose_target(setup, seed, feedback, selection_rng, plan)
        if target_choice is None:
            diag_log(
                f"TARGET_CAPS_EXHAUSTED run_id={setup.run_id} generation={generation} "
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
            universe_symbols=setup.universe_symbols,
            disabled_symbols=setup.disabled_symbols,
            aliases=setup.aliases,
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
        setup.memory.record_variant(setup.run_id, generation, variant)
        plan.limiter.record(target_symbol, target_period)
        variants.append(variant)
        if len(variants) == 1 or len(variants) % 25 == 0 or len(variants) == planned_variants:
            diag_log(
                f"GENERATION_PROGRESS run_id={setup.run_id} generation={generation} "
                f"variants={len(variants)}/{planned_variants} seed_index={seed_index} "
                f"variant_index={variant_index} target={target_symbol}/{target_period} "
                f"set={variant.path}"
            )


def _create_generation_variants(
    setup: _AgentSetup, generation: int, generation_dir: Path, selected_seeds: list,
    feedback: _GenerationFeedback, selection_rng: object, plan: _TargetPlan,
) -> list[Variant]:
    """Crea todas las variantes planificadas para la generacion."""
    variants: list[Variant] = []
    planned_variants = len(selected_seeds) * setup.args.variants_per_seed
    diag_log(
        f"GENERATION_START run_id={setup.run_id} generation={generation} "
        f"seeds={len(selected_seeds)} planned_variants={planned_variants} dir={generation_dir}"
    )
    for seed_index, seed in enumerate(selected_seeds, start=1):
        _create_seed_variants(
            setup, generation, generation_dir, seed, seed_index, feedback, selection_rng,
            plan, variants, planned_variants,
        )
    return variants


def _evaluate_generation(
    setup: _AgentSetup, generation: int, accepted_dir: Path, variants: list[Variant],
    batch_started_at: float, code: int,
) -> tuple[list, int | None]:
    """Puntua los reportes de la generacion y copia los aceptados."""
    args = setup.args
    diag_log(f"EVALUATE_VARIANTS_START run_id={setup.run_id} generation={generation} variants={len(variants)}")
    scored = evaluate_variants(
        setup.memory,
        variants,
        setup.score_config,
        parse_symbol_map(args.symbol_map),
        args.broker,
        min_report_mtime=batch_started_at - 1.0,
        min_trades_w1=args.min_trades_w1,
        min_trades_mn=args.min_trades_mn,
        symbol_suffix=args.symbol_suffix,
        universe_symbols=broker_universe_symbols(args),
    )
    survivors = select_survivors(
        scored,
        args.top_percent,
        allow_rejected_fallback=bool(args.force_unseeded_universe),
    )
    copied = copy_accepted(survivors, accepted_dir)
    print(f"Reportes puntuados: {len(scored)}; accepted/copied: {len(copied)}")
    diag_log(
        f"EVALUATE_VARIANTS_DONE run_id={setup.run_id} generation={generation} "
        f"scored={len(scored)} survivors={len(survivors)} copied={len(copied)}"
    )
    if code != 0 and not scored:
        print(f"ERROR: run_tests.py termino con codigo {code} y no produjo reportes puntuables")
        return scored, code
    return scored, None


def _run_generation_backtests(
    setup: _AgentSetup, generation: int, generation_dir: Path, accepted_dir: Path,
    variants: list[Variant],
) -> tuple[list, int | None]:
    """Lanza los backtests de la generacion y puntua lo que devuelvan."""
    args = setup.args
    if not (args.execute_backtests or args.dry_run):
        return [], None
    batch_started_at = time.time()
    diag_log(f"GENERATION_BACKTESTS_BEFORE run_id={setup.run_id} generation={generation} variants={len(variants)}")
    code = run_backtests(args, generation_dir)
    diag_log(f"GENERATION_BACKTESTS_AFTER run_id={setup.run_id} generation={generation} code={code}")
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
        return [], code
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return [], code
    if args.dry_run:
        return [], None
    return _evaluate_generation(setup, generation, accepted_dir, variants, batch_started_at, code)


def _next_seed_pool(
    setup: _AgentSetup, generation: int, scored: list, variants: list[Variant]
) -> list:
    """Seeds con las que arranca la generacion siguiente."""
    args = setup.args
    if scored:
        next_survivors = select_next_generation_survivors(
            setup.memory,
            setup.run_id,
            scored,
            args.top_percent,
            args.max_seeds,
            setup.aliases,
            setup.group_by_symbol,
            allow_rejected_fallback=bool(args.force_unseeded_universe),
            fitness_target=generation_fitness_target(bool(args.force_unseeded_universe)),
        )
        if not args.force_unseeded_universe and not next_survivors:
            print(
                f"Production: gen {generation} sin accepted; "
                "no se usan rechazados como seeds, se reponen source seeds"
            )
            diag_log(f"PRODUCTION_NO_ACCEPTED_SOURCE_BACKFILL run_id={setup.run_id} generation={generation}")
        next_seeds = seeds_from_survivors(next_survivors)
    else:
        next_seeds = [variant_as_next_seed(variant) for variant in variants]
    return next_generation_seed_pool(
        next_seeds,
        setup.source_seeds,
        args.max_seeds,
        force_unseeded_universe=args.force_unseeded_universe,
    )


def _run_generation(setup: _AgentSetup, generation: int, current_seeds: list) -> _GenerationOutcome:
    """Una generacion completa: seleccion, variantes, backtests y relevo."""
    selection_rng = generation_random_stream(setup.args.random_seed, generation, "selection")
    generation_dir = setup.run_dir / f"gen_{generation:03d}"
    accepted_dir = setup.run_dir / f"accepted_gen_{generation:03d}"
    feedback = _generation_feedback(setup, current_seeds)
    selection_pool = _selection_pool(setup, current_seeds, generation)
    if selection_pool is None:
        return _GenerationOutcome(stop=True)
    selected_seed_rankings = _select_seeds(setup, selection_pool, feedback, selection_rng)
    selected_seeds = [seed for _, seed, _, _, _ in selected_seed_rankings]
    setup.memory.record_seed_selection(
        setup.run_id, generation, selected_seed_rankings, feedback.fitness_predictions
    )
    plan = _target_plan(setup, generation, current_seeds, selected_seeds)
    _print_generation_plan(setup, generation, selected_seeds, plan)
    variants = _create_generation_variants(
        setup, generation, generation_dir, selected_seeds, feedback, selection_rng, plan
    )
    if not variants:
        print(f"Generacion {generation}: sin targets permitidos bajo caps production; se detiene")
        diag_log(f"GENERATION_STOP_NO_TARGETS run_id={setup.run_id} generation={generation}")
        return _GenerationOutcome(stop=True)
    print(f"Generados: {len(variants)} en {generation_dir}")
    diag_log(f"GENERATION_DONE run_id={setup.run_id} generation={generation} variants={len(variants)}")
    scored, exit_code = _run_generation_backtests(
        setup, generation, generation_dir, accepted_dir, variants
    )
    if exit_code is not None:
        return _GenerationOutcome(exit_code=exit_code)
    return _GenerationOutcome(
        variants_generated=len(variants),
        current_seeds=_next_seed_pool(setup, generation, scored, variants),
    )


def _run_generations(setup: _AgentSetup) -> int:
    """Encadena las generaciones y resume la ejecucion al terminar."""
    current_seeds = setup.source_seeds
    all_generated = 0
    for generation in range(1, setup.args.generations + 1):
        outcome = _run_generation(setup, generation, current_seeds)
        if outcome.exit_code is not None:
            return outcome.exit_code
        if outcome.stop:
            break
        all_generated += outcome.variants_generated
        current_seeds = outcome.current_seeds or []
    print(f"Run dir: {setup.run_dir}")
    print(f"Memoria: {setup.memory.path}")
    print(f"Sets generados: {all_generated}")
    return 0 if all_generated else 1
