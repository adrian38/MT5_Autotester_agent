"""Reanudacion del ultimo run con su configuracion guardada."""
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


def resume_last_run(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    run = memory.latest_run()
    if run is None:
        print("ERROR: no hay runs guardados para continuar")
        return 1
    run_id = int(run["id"])
    restore_run_unseeded_probabilities(args, run["config_json"])
    discovery_exploitable_ratio = restored_discovery_exploitable_ratio(run["config_json"])
    universe_feedback_probability = restored_discovery_universe_feedback_probability(
        run["config_json"]
    )
    current_target_probability = restored_discovery_current_target_probability(
        run["config_json"]
    )
    current_timeframe_probability = restored_discovery_current_timeframe_probability(
        run["config_json"]
    )
    run_dir = resolve_workspace_path(run["output_dir"])
    planned_generations = int(run["generations"])
    args.variants_per_seed = int(run["variants_per_seed"])
    args.max_seeds = int(run["max_seeds"])
    args.execute_backtests = bool(run["execute_backtests"])
    args.dry_run = bool(run["dry_run"]) or args.dry_run
    if args.execute_backtests and not args.expert and not args.multi_terminal:
        print("ERROR: el run pendiente requiere backtests; indica --expert o activa --multi-terminal para continuar")
        return 1

    max_generation = memory.max_generation(run_id)
    pending_generation = memory.pending_generated_generation(run_id) if args.execute_backtests else 0
    current_seeds: list[Seed] = []
    did_work = False
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
    group_by_symbol = asset_group_map(asset_groups, aliases)
    if not universe_symbols:
        print("ERROR: no hay simbolos activos en Universo para generar targets")
        return 1
    print(f"Universo {args.broker} cargado: {len(universe_symbols)} simbolos, {len(aliases)} aliases")

    print(f"Continuando run #{run_id}: plan={planned_generations}, ultima_gen={max_generation}")
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

    if pending_generation:
        variants = memory.variants_for_generation(run_id, pending_generation, status="generated")
        if not variants:
            print(f"ERROR: gen {pending_generation} marcada pendiente, pero no hay .set disponibles")
            return 1
        print(f"Reanudando gen {pending_generation}: backtests pendientes={len(variants)}")
        try:
            scored = evaluate_generation(args, memory, run_dir, pending_generation, variants, score_config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        did_work = True
        if args.execute_backtests and not args.dry_run and not scored:
            print(f"ERROR: gen {pending_generation} no produjo reportes puntuables; no se genera la siguiente generacion")
            return 1
        if scored:
            next_survivors = select_next_generation_survivors(
                memory,
                run_id,
                scored,
                args.top_percent,
                args.max_seeds,
                aliases,
                group_by_symbol,
                allow_rejected_fallback=bool(args.force_unseeded_universe),
                fitness_target=generation_fitness_target(bool(args.force_unseeded_universe)),
            )
            if not args.force_unseeded_universe and not next_survivors:
                print(
                    f"Production: gen {pending_generation} sin accepted; "
                    "no se usan rechazados como seeds, se reponen source seeds"
                )
                diag_log(f"PRODUCTION_NO_ACCEPTED_SOURCE_BACKFILL run_id={run_id} generation={pending_generation}")
            next_seeds = seeds_from_survivors(next_survivors)
            current_seeds = next_generation_seed_pool(
                next_seeds,
                run_source_seeds,
                args.max_seeds,
                force_unseeded_universe=args.force_unseeded_universe,
            )
        else:
            next_seeds = seeds_from_variants(variants)
            current_seeds = next_generation_seed_pool(
                next_seeds,
                run_source_seeds,
                args.max_seeds,
                force_unseeded_universe=args.force_unseeded_universe,
            )
        if args.backtest_pending_only:
            print(f"Backtests pendientes completados en gen {pending_generation}; no se generan nuevas generaciones.")
            print(f"Run dir: {run_dir}")
            print(f"Memoria: {memory.path}")
            return 0
        next_generation = pending_generation + 1
    else:
        if args.backtest_pending_only:
            print(f"Run #{run_id} no tiene candidatos generated pendientes para backtest.")
            return 0
        if max_generation >= planned_generations:
            print(f"Run #{run_id} ya esta completo: {max_generation}/{planned_generations}")
            return 0
        seed_limit = args.max_seeds if args.max_seeds > 0 else 0
        _, latest_generation, current_seeds = memory.continuation_seeds(seed_limit)
        current_seeds = next_generation_seed_pool(
            current_seeds,
            run_source_seeds,
            args.max_seeds,
            force_unseeded_universe=args.force_unseeded_universe,
        )
        next_generation = latest_generation + 1

    current_seeds, blocked_source_count = generation_source_seeds(
        current_seeds,
        symbol_map,
        disabled_symbols,
        seed_enabled_when_disabled,
    )
    if blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {blocked_source_count}")
    if not current_seeds:
        print("ERROR: no hay seeds disponibles para generar con la politica actual del Universo")
        return 1

    all_generated = 0
    timeframe_universe = target_timeframe_universe(
        bool(args.experimental_long_timeframes),
        base_dir=BASE_DIR,
        broker=args.broker,
        account_type=args.account_type,
    )
    print(f"Universo TF target: {', '.join(timeframe_universe)}")
    for generation in range(next_generation, planned_generations + 1):
        selection_rng = generation_random_stream(args.random_seed, generation, "selection")
        generation_dir = run_dir / f"gen_{generation:03d}"
        feedback_terminal_stage = generation_feedback_terminal_stage(
            bool(args.force_unseeded_universe)
        )
        mutation_feedback = memory.mutation_feedback(terminal_stage=feedback_terminal_stage)
        mutation_direction_feedback = memory.mutation_direction_feedback(
            terminal_stage=feedback_terminal_stage
        )
        asset_feedback, asset_group_feedback = memory.asset_feedback_with_groups(
            aliases,
            group_by_symbol,
            terminal_stage=feedback_terminal_stage,
        )
        timeframe_feedback = memory.timeframe_feedback(terminal_stage=feedback_terminal_stage)
        fitness_predictions = generation_seed_fitness_predictions(
            memory,
            current_seeds,
            run_id=run_id,
            force_unseeded_universe=bool(args.force_unseeded_universe),
        )
        fitness_feedback = {path: prediction.weight for path, prediction in fitness_predictions.items()}
        selection_pool = current_seeds
        if not args.force_unseeded_universe:
            selection_pool = production_viable_source_seeds(
                current_seeds,
                universe_symbols,
                aliases,
                symbol_map=symbol_map,
                disabled_symbols=disabled_symbols,
                group_by_symbol=group_by_symbol,
            )
            skipped = len(current_seeds) - len(selection_pool)
            if skipped:
                print(f"Production: seeds sin target viable por grupo/relacion descartadas: {skipped}")
                diag_log(f"PRODUCTION_SKIPPED_UNVIABLE_SOURCE_SEEDS run_id={run_id} generation={generation} skipped={skipped}")
            if not selection_pool:
                print(f"Production: gen {generation} sin seeds con target viable; se detiene")
                diag_log(f"GENERATION_STOP_NO_VIABLE_SOURCE_SEEDS run_id={run_id} generation={generation}")
                break
        selected_seed_rankings = (
            discovery_ranked_seed_selection(
                selection_pool,
                args.max_seeds,
                asset_feedback,
                timeframe_feedback,
                selection_rng,
                universe_symbols,
                aliases,
                symbol_map=symbol_map,
                disabled_symbols=disabled_symbols,
                group_by_symbol=group_by_symbol,
                fitness_feedback=fitness_feedback,
                exploitable_min_ratio=discovery_exploitable_ratio,
            )
            if args.force_unseeded_universe
            else ranked_seed_selection(
                selection_pool,
                args.max_seeds,
                asset_feedback,
                timeframe_feedback,
                selection_rng,
                aliases,
                group_by_symbol,
                fitness_feedback,
                symbol_cap_ratio=seed_symbol_cap_ratio(False),
                allow_overflow=False,
            )
        )
        selected_seeds = [seed for _, seed, _, _, _ in selected_seed_rankings]
        memory.record_seed_selection(run_id, generation, selected_seed_rankings, fitness_predictions)
        unseeded_symbols, unseeded_timeframes = unseeded_universe_targets(
            current_seeds,
            universe_symbols,
            aliases,
            timeframe_universe,
        )
        asset_unseeded_probability, tf_unseeded_probability = configured_unseeded_force_probabilities(
            args,
            generation,
            len(unseeded_symbols),
            len(unseeded_timeframes),
        )
        target_limiter = TargetDiversityLimiter(
            len(selected_seeds) * args.variants_per_seed,
            aliases,
            group_by_symbol=group_by_symbol,
            symbol_cap_ratio=target_symbol_cap_ratio(args.force_unseeded_universe),
        )
        variants: list[Variant] = []
        reserved_timeframes = (
            reserved_timeframe_plan(selected_seeds, timeframe_universe, len(selected_seeds) * args.variants_per_seed)
            if args.force_unseeded_universe
            else []
        )
        print(f"Generacion {generation}: seeds={len(selected_seeds)}")
        print(
            "Caps diversidad target: "
            f"grupos[{format_group_cap_summary(target_limiter)}], "
            f"simbolo<={target_limiter.symbol_cap}, TF<={target_limiter.timeframe_cap}, "
            f"simbolo+TF<={target_limiter.pair_cap}"
        )
        if args.force_unseeded_universe:
            print(
                f"Exploracion forzada sin seed: activos={len(unseeded_symbols)}, "
                f"TF={len(unseeded_timeframes)} | "
                f"prob_activo={asset_unseeded_probability:.0%}, prob_TF={tf_unseeded_probability:.0%}"
            )
            if reserved_timeframes:
                print(f"Cuota/reserva TF exploratoria: {timeframe_plan_summary(reserved_timeframes)}")
        for seed_index, seed in enumerate(selected_seeds, start=1):
            for variant_index in range(1, args.variants_per_seed + 1):
                target_choice = choose_diverse_target(
                    seed,
                    asset_feedback,
                    timeframe_feedback,
                    selection_rng,
                    target_limiter,
                    universe_symbols,
                    aliases,
                    timeframe_universe=timeframe_universe,
                    symbol_map=symbol_map,
                    disabled_symbols=disabled_symbols,
                    force_unseeded_universe=args.force_unseeded_universe,
                    unseeded_universe_symbols=unseeded_symbols,
                    unseeded_timeframes=unseeded_timeframes,
                    asset_unseeded_probability=asset_unseeded_probability,
                    timeframe_unseeded_probability=tf_unseeded_probability,
                    production_mode=not args.force_unseeded_universe,
                    group_by_symbol=group_by_symbol,
                    asset_group_feedback=asset_group_feedback,
                    universe_feedback_probability=universe_feedback_probability,
                    current_target_probability=current_target_probability,
                    current_timeframe_probability=current_timeframe_probability,
                )
                if target_choice is None:
                    diag_log(
                        f"TARGET_CAPS_EXHAUSTED run_id={run_id} generation={generation} "
                        f"seed_index={seed_index} variant_index={variant_index} seed={seed.symbol}/{seed.period}"
                    )
                    break
                target_symbol, target_period, policy = target_choice
                target_symbol, target_period, policy = apply_reserved_timeframe(
                    reserved_timeframes=reserved_timeframes,
                    target_symbol=target_symbol,
                    target_period=target_period,
                    policy=policy,
                    seed=seed,
                    target_limiter=target_limiter,
                    universe_symbols=universe_symbols,
                    disabled_symbols=disabled_symbols,
                    aliases=aliases,
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
                    mutation_feedback,
                    mutation_direction_feedback,
                    policy,
                    generation_random_stream(
                        args.random_seed,
                        generation,
                        "mutation",
                        seed_index,
                        variant_index,
                    ),
                )
                memory.record_variant(run_id, generation, variant)
                target_limiter.record(target_symbol, target_period)
                variants.append(variant)
        if not variants:
            print(f"Generacion {generation}: sin targets permitidos bajo caps production; se detiene")
            diag_log(f"GENERATION_STOP_NO_TARGETS run_id={run_id} generation={generation}")
            break
        all_generated += len(variants)
        did_work = True
        print(f"Generados: {len(variants)} en {generation_dir}")
        try:
            scored = evaluate_generation(args, memory, run_dir, generation, variants, score_config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        if args.execute_backtests and not args.dry_run and not scored:
            print(f"ERROR: gen {generation} no produjo reportes puntuables; se detiene la continuacion")
            return 1
        if scored:
            next_survivors = select_next_generation_survivors(
                memory,
                run_id,
                scored,
                args.top_percent,
                args.max_seeds,
                aliases,
                group_by_symbol,
                allow_rejected_fallback=bool(args.force_unseeded_universe),
                fitness_target=generation_fitness_target(bool(args.force_unseeded_universe)),
            )
            if not args.force_unseeded_universe and not next_survivors:
                print(
                    f"Production: gen {generation} sin accepted; "
                    "no se usan rechazados como seeds, se reponen source seeds"
                )
                diag_log(f"PRODUCTION_NO_ACCEPTED_SOURCE_BACKFILL run_id={run_id} generation={generation}")
            current_seeds = next_generation_seed_pool(
                seeds_from_survivors(next_survivors),
                run_source_seeds,
                args.max_seeds,
                force_unseeded_universe=args.force_unseeded_universe,
            )
        else:
            current_seeds = next_generation_seed_pool(
                seeds_from_variants(variants),
                run_source_seeds,
                args.max_seeds,
                force_unseeded_universe=args.force_unseeded_universe,
            )

    print(f"Run dir: {run_dir}")
    print(f"Memoria: {memory.path}")
    print(f"Sets nuevos generados: {all_generated}")
    return 0 if did_work else 1
