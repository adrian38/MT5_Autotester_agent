"""Ciclo completo del agente: generaciones, etapas y cierre."""
from __future__ import annotations

import argparse
import json
import sys
import time
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


def run_agent(args: argparse.Namespace) -> int:
    policy_values = {}
    policy_path = getattr(args, "risk_profit_config", None)
    if policy_path:
        policy_values = json.loads(Path(policy_path).read_text(encoding="utf-8-sig"))
    if getattr(args, "risk_profit_mode", None) is not None:
        policy_values["mode"] = args.risk_profit_mode
    risk_policy = RiskProfitConfig.from_dict(policy_values)
    source_dir = resolve_workspace_path(args.source_dir)
    output_root = resolve_workspace_path(args.output_dir)
    run_dir = output_root / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    memory = AgentMemory(resolve_workspace_path(args.memory))
    score_config = ScoreConfig(
        min_net_profit=args.min_net_profit,
        min_profit_factor=args.min_profit_factor,
        min_trades=args.min_trades,
        max_drawdown_pct=args.max_drawdown_pct,
        min_recovery_factor=args.min_recovery_factor,
        min_positive_month_ratio=args.min_positive_month_ratio,
        risk_profit=risk_policy,
    )

    if getattr(args, "prepared_manifest", None):
        try:
            from ubs.prepared import run_prepared
            return run_prepared(args, memory, score_config, sys.modules[__name__])
        finally:
            memory.close()
    standalone_code = run_standalone_mode(args, memory, score_config)
    if standalone_code is not None:
        return standalone_code

    seed_source = str(source_dir)
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
    group_by_symbol = asset_group_map(asset_groups, aliases)
    timeframe_universe = target_timeframe_universe(
        bool(args.experimental_long_timeframes),
        base_dir=BASE_DIR,
        broker=args.broker,
        account_type=args.account_type,
    )
    print(f"Seeds disponibles: {len(seeds)} ({seed_source})")
    if blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {blocked_source_count}")
    if seed_enabled_when_disabled:
        print(f"Symbols deshabilitados con SEEDS activo: {len(seed_enabled_when_disabled)}")
    print(f"Universo {args.broker} cargado: {len(universe_symbols)} simbolos, {len(aliases)} aliases")
    print(f"Universo TF target: {', '.join(timeframe_universe)}")
    discovery_source_mix = discovery_source_mix_feedback(
        memory,
        universe_symbols,
        aliases,
        symbol_map=symbol_map,
        disabled_symbols=disabled_symbols,
        group_by_symbol=group_by_symbol,
    )
    discovery_target_policy_mix = discovery_target_policy_feedback(memory)
    apply_discovery_target_policy_schedule(args, discovery_target_policy_mix)
    print(
        "Discovery source mix: "
        f"explotable={discovery_source_mix.exploitable_ratio:.1%}, "
        f"cross={1.0 - discovery_source_mix.exploitable_ratio:.1%}, "
        f"evidence={discovery_source_mix.reason}"
    )
    print(
        "Discovery target mix: "
        f"unseeded_scale={discovery_target_policy_mix.unseeded_multiplier:.1%}, "
        f"universe_feedback={discovery_target_policy_mix.universe_feedback_probability:.1%}, "
        f"current_target={discovery_target_policy_mix.current_target_probability:.1%}, "
        f"current_tf={discovery_target_policy_mix.current_timeframe_probability:.1%}"
    )
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
    run_id = memory.create_run(
        source_dir,
        run_dir,
        args.generations,
        args.variants_per_seed,
        args.max_seeds,
        args.execute_backtests,
        args.dry_run,
        config=build_run_config(
            args,
            score_config,
            source_dir=source_dir,
            output_root=output_root,
            run_dir=run_dir,
            universe_symbol_count=len(universe_symbols),
            universe_alias_count=len(aliases),
            disabled_symbol_count=len(disabled_symbols),
            seed_enabled_disabled_symbol_count=len(seed_enabled_when_disabled),
            discovery_source_mix=discovery_source_mix,
            discovery_target_policy_mix=discovery_target_policy_mix,
        ),
    )

    current_seeds = source_seeds
    all_generated = 0
    try:
        for generation in range(1, args.generations + 1):
            selection_rng = generation_random_stream(args.random_seed, generation, "selection")
            generation_dir = run_dir / f"gen_{generation:03d}"
            accepted_dir = run_dir / f"accepted_gen_{generation:03d}"
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
                    exploitable_min_ratio=discovery_source_mix.exploitable_ratio,
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
            planned_variants = len(selected_seeds) * args.variants_per_seed
            diag_log(
                f"GENERATION_START run_id={run_id} generation={generation} "
                f"seeds={len(selected_seeds)} planned_variants={planned_variants} dir={generation_dir}"
            )
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
                        universe_feedback_probability=(
                            discovery_target_policy_mix.universe_feedback_probability
                        ),
                        current_target_probability=(
                            discovery_target_policy_mix.current_target_probability
                        ),
                        current_timeframe_probability=(
                            discovery_target_policy_mix.current_timeframe_probability
                        ),
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
                    if len(variants) == 1 or len(variants) % 25 == 0 or len(variants) == planned_variants:
                        diag_log(
                            f"GENERATION_PROGRESS run_id={run_id} generation={generation} "
                            f"variants={len(variants)}/{planned_variants} seed_index={seed_index} "
                            f"variant_index={variant_index} target={target_symbol}/{target_period} "
                            f"set={variant.path}"
                        )
            if not variants:
                print(f"Generacion {generation}: sin targets permitidos bajo caps production; se detiene")
                diag_log(f"GENERATION_STOP_NO_TARGETS run_id={run_id} generation={generation}")
                break
            all_generated += len(variants)
            print(f"Generados: {len(variants)} en {generation_dir}")
            diag_log(f"GENERATION_DONE run_id={run_id} generation={generation} variants={len(variants)}")

            scored: list[tuple[Variant, ScoreResult]] = []
            if args.execute_backtests or args.dry_run:
                batch_started_at = time.time()
                diag_log(f"GENERATION_BACKTESTS_BEFORE run_id={run_id} generation={generation} variants={len(variants)}")
                code = run_backtests(args, generation_dir)
                diag_log(f"GENERATION_BACKTESTS_AFTER run_id={run_id} generation={generation} code={code}")
                if code == RUNNING_TERMINAL_EXIT_CODE:
                    print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
                    return code
                partial_failure = code != 0
                if code != 0:
                    print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
                    if args.dry_run:
                        return code
                if not args.dry_run:
                    diag_log(f"EVALUATE_VARIANTS_START run_id={run_id} generation={generation} variants={len(variants)}")
                    scored = evaluate_variants(
                        memory,
                        variants,
                        score_config,
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
                        f"EVALUATE_VARIANTS_DONE run_id={run_id} generation={generation} "
                        f"scored={len(scored)} survivors={len(survivors)} copied={len(copied)}"
                    )
                    if partial_failure and not scored:
                        print(f"ERROR: run_tests.py termino con codigo {code} y no produjo reportes puntuables")
                        return code

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
                next_seeds = seeds_from_survivors(next_survivors)
                current_seeds = next_generation_seed_pool(
                    next_seeds,
                    source_seeds,
                    args.max_seeds,
                    force_unseeded_universe=args.force_unseeded_universe,
                )
            else:
                next_seeds = [variant_as_next_seed(variant) for variant in variants]
                current_seeds = next_generation_seed_pool(
                    next_seeds,
                    source_seeds,
                    args.max_seeds,
                    force_unseeded_universe=args.force_unseeded_universe,
                )

        print(f"Run dir: {run_dir}")
        print(f"Memoria: {memory.path}")
        print(f"Sets generados: {all_generated}")
        return 0 if all_generated else 1
    finally:
        memory.close()
