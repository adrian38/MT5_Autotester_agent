"""Sondeo del historico del universo y cierre de generacion."""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult
from ubs.seeds import load_seeds
from ubs.set_utils import write_set_use_every_tick
from ubs.universe import (
    augment_aliases_with_symbol_map,
    canonical_symbol,
    load_asset_universe,
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
)
from ubs_agent_config import (
    BASE_DIR,
    TIMEFRAME_TO_ENUM,
)
from ubs_agent_evaluate import (
    copy_accepted,
    evaluate_history_probe,
    evaluate_variants,
    recreate_work_dir,
    select_survivors,
)
from ubs_agent_seeds_plan import (
    disabled_symbols_file_for_account,
    generation_source_seeds,
)
from ubs_agent_universe import (
    broker_universe_symbols,
)
from ubs_agent_variants import (
    create_history_probe_variant,
    run_backtests,
)


def select_history_probe_seed(seeds: list[Seed]) -> Seed | None:
    for seed in seeds:
        if seed.run_strategy in {"1", "2"} and seed.path.exists():
            return seed
    for seed in seeds:
        if seed.path.exists():
            return seed
    return None


HISTORY_PROBE_FINAL_STATUSES = {"history_ok", "no_history"}


def history_probe_latest_statuses(memory: AgentMemory, aliases: dict[str, str]) -> dict[str, str]:
    rows = memory.conn.execute(
        """
        select target_symbol, status
        from candidates
        where policy='history_probe'
        order by id
        """
    ).fetchall()
    statuses: dict[str, str] = {}
    for row in rows:
        key = canonical_symbol(str(row["target_symbol"] or ""), aliases).upper()
        if key:
            statuses[key] = str(row["status"] or "")
    return statuses


def probe_universe_history(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.execute_backtests and not args.dry_run:
        print("ERROR: probe de historico requiere --execute-backtests o --dry-run")
        return 1
    if args.execute_backtests and not args.expert and not args.multi_terminal:
        print("ERROR: probe de historico requiere --expert o --multi-terminal")
        return 1

    target_period = str(args.probe_history_timeframe or "H1").strip().upper()
    if target_period not in TIMEFRAME_TO_ENUM:
        print(f"ERROR: timeframe probe invalido: {target_period}")
        return 1

    source_dir = resolve_workspace_path(args.source_dir)
    output_root = resolve_workspace_path(args.output_dir)
    disabled_policy_path = disabled_symbols_file_for_account(args.account_type, args.broker)
    disabled_symbols = load_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled = load_seed_enabled_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled &= disabled_symbols
    symbol_map = parse_symbol_map(args.symbol_map)

    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    source_seeds, blocked_source_count = generation_source_seeds(
        seeds,
        symbol_map,
        disabled_symbols,
        seed_enabled_when_disabled,
    )
    template_seed = select_history_probe_seed(source_seeds)
    if template_seed is None:
        print("ERROR: no hay una seed valida para usar como plantilla del probe")
        return 1

    asset_groups, aliases = load_asset_universe(
        Path(args.assets).expanduser(),
        disabled_symbols=disabled_symbols,
    )
    all_universe_symbols = tuple(dict.fromkeys(symbol for symbols in asset_groups.values() for symbol in symbols))
    aliases = augment_aliases_with_symbol_map(aliases, symbol_map, all_universe_symbols)
    existing_statuses = history_probe_latest_statuses(memory, aliases)
    universe_symbols = tuple(
        symbol
        for symbol in all_universe_symbols
        if existing_statuses.get(canonical_symbol(symbol, aliases).upper()) not in HISTORY_PROBE_FINAL_STATUSES
    )
    if args.probe_history_limit > 0:
        universe_symbols = universe_symbols[: args.probe_history_limit]
    if not universe_symbols:
        print("No hay simbolos GEN=si pendientes de probe historico.")
        return 0

    run_dir = output_root / datetime.now().strftime("history_probe_%Y%m%d_%H%M%S")
    probe_dir = recreate_work_dir(run_dir / "gen_001")

    variants: list[Variant] = []
    for index, symbol in enumerate(universe_symbols, start=1):
        variant = create_history_probe_variant(template_seed, symbol, target_period, probe_dir, index)
        memory.conn.execute(
            """
            delete from candidates
            where policy='history_probe'
              and upper(target_symbol)=upper(?)
              and upper(period)=upper(?)
            """,
            (symbol, target_period),
        )
        memory.record_variant(0, 0, variant, status="history_probe")
        variants.append(variant)

    print(
        f"Probe historico universo: pendientes GEN=si={len(variants)} de {len(all_universe_symbols)} | "
        f"TF={target_period} | seed plantilla={template_seed.path.name}"
    )
    if blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {blocked_source_count}")
    print(f"Directorio probe: {run_dir}")
    if args.dry_run:
        print("Dry-run: sets de probe generados, MT5 no se abre.")
        return 0

    batch_started_at = time.time()
    code = run_backtests(args, probe_dir, model="1")
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza historico.")
        return 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")

    status_counts: dict[str, int] = {}
    for variant in variants:
        status, _ = evaluate_history_probe(
            memory,
            variant,
            score_config,
            symbol_map,
            args.broker,
            min_report_mtime=batch_started_at - 1.0,
            symbol_suffix=args.symbol_suffix,
            universe_symbols=broker_universe_symbols(args),
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    print(
        "Probe historico terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
    )
    print(f"Memoria: {memory.path}")
    return 0 if status_counts else 1


def evaluate_generation(
    args: argparse.Namespace,
    memory: AgentMemory,
    run_dir: Path,
    generation: int,
    variants: list[Variant],
    score_config: ScoreConfig,
) -> list[tuple[Variant, ScoreResult]]:
    scored: list[tuple[Variant, ScoreResult]] = []
    if not (args.execute_backtests or args.dry_run):
        return scored
    generation_dir = run_dir / f"gen_{generation:03d}"
    for variant in variants:
        write_set_use_every_tick(variant.path, variant.path, False)
    batch_started_at = time.time()
    code = run_backtests(args, generation_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        raise RuntimeError("run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta")
    partial_failure = code != 0
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            raise RuntimeError(f"run_tests.py termino con codigo {code}")
    if args.dry_run:
        return scored
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
    copied = copy_accepted(survivors, run_dir / f"accepted_gen_{generation:03d}")
    print(f"Reportes puntuados gen {generation}: {len(scored)}; accepted/copied: {len(copied)}")
    if partial_failure and not scored:
        raise RuntimeError(f"run_tests.py termino con codigo {code} y no produjo reportes puntuables")
    return scored
