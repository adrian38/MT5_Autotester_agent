"""Politica de exploracion: universo sin semilla y mezcla de fuentes."""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from run_tests import apply_symbol_map, normalize_set_symbol
from ubs.account import axi_cash_future_family_targets
from ubs.memory import AgentMemory
from ubs.models import Seed
from ubs.selection import (
    DiscoverySourceMix,
    DiscoveryTargetPolicyMix,
    estimate_discovery_source_mix,
    estimate_discovery_target_policy_mix,
)
from ubs.universe import canonical_symbol
from ubs_agent_config import (
    DISCOVERY_EXPLOITABLE_SEED_MIN_RATIO,
    DISCOVERY_SEED_SYMBOL_RESERVE_RATIO,
    TIMEFRAME_UNIVERSE,
)
from ubs_agent_seeds_plan import (
    capped_count,
    ranked_seed_selection,
)


def unseeded_universe_targets(
    seeds: list[Seed],
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    aliases = aliases or {}
    seed_symbols = {
        canonical_symbol(seed.symbol, aliases).upper()
        for seed in seeds
        if seed.symbol and seed.symbol != "UNKNOWN"
    }
    seed_timeframes = {
        seed.period.upper()
        for seed in seeds
        if seed.period and seed.period != "UNKNOWN"
    }
    unseeded_symbols = tuple(
        symbol
        for symbol in dict.fromkeys(universe_symbols)
        if canonical_symbol(symbol, aliases).upper() not in seed_symbols
    )
    unseeded_timeframes = tuple(period for period in timeframe_universe if period.upper() not in seed_timeframes)
    return unseeded_symbols, unseeded_timeframes


def related_assets(symbol: str) -> tuple[str, ...]:
    symbol = symbol.upper()
    if symbol in {"XAUUSD", "XAGUSD", "XAUEUR"}:
        return ("XAUUSD", "XAGUSD", "XAUEUR")
    if symbol in {"US30", ".US30CASH", "US500", ".US500CASH", "USTEC", "US100", ".USTECHCASH", "DAX", "DE40", ".DE40CASH"}:
        return ("US30", "US500", "USTEC", "DAX")
    if symbol in {"BTCUSD", "ETHUSD", "XRPUSD", "ADAUSD", "DOGEUSD"}:
        return ("BTCUSD", "ETHUSD")
    if symbol in {"XTIUSD", "WTI", "BRENT", "CRUDEOIL"}:
        return ("XTIUSD", "BRENT")
    if len(symbol) == 6:
        base = symbol[:3]
        quote = symbol[3:]
        major = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
        related = [item for item in major if base in item or quote in item]
        return tuple(dict.fromkeys([symbol, *related]))
    return (symbol,)


def target_symbol_disabled(
    symbol: str,
    universe_symbols: tuple[str, ...] = (),
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
) -> bool:
    aliases = aliases or {}
    symbol_map = symbol_map or {}
    disabled = {str(item).strip().upper() for item in (disabled_symbols or set())}
    exact_by_key = {symbol.upper(): symbol for symbol in universe_symbols}
    for alias, target in aliases.items():
        exact_by_key[str(alias).upper()] = target
    normalized = normalize_set_symbol(symbol)
    resolved = exact_by_key.get(normalized, symbol)
    mapped_symbol = apply_symbol_map(symbol, symbol_map)
    mapped = normalize_set_symbol(mapped_symbol)
    return (
        str(symbol).strip().upper() in disabled
        or normalized in disabled
        or resolved.upper() in disabled
        or str(mapped_symbol).strip().upper() in disabled
        or mapped in disabled
    )


def target_symbol_options_for_seed(
    seed: Seed,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    aliases = aliases or {}
    symbol_map = symbol_map or {}
    group_by_symbol = {str(k).upper(): str(v) for k, v in (group_by_symbol or {}).items()}
    current = seed.symbol or "UNKNOWN"
    exact_by_key = {symbol.upper(): symbol for symbol in universe_symbols}
    for alias, target in aliases.items():
        exact_by_key[str(alias).upper()] = target

    normalized_current = normalize_set_symbol(current)
    mapped_current = normalize_set_symbol(apply_symbol_map(current, symbol_map))
    resolved_current = exact_by_key.get(mapped_current, exact_by_key.get(normalized_current, current))

    def target_disabled(symbol: str) -> bool:
        return target_symbol_disabled(
            symbol,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
        )

    def group_for(symbol: str) -> str:
        return group_by_symbol.get(canonical_symbol(symbol, aliases).upper(), "")

    source_group = group_for(current) or group_for(mapped_current) or group_for(resolved_current)
    universe_keys = {symbol.upper() for symbol in universe_symbols}
    current_family_targets = tuple(
        target
        for target in dict.fromkeys(
            (
                *axi_cash_future_family_targets(current, universe_symbols),
                *axi_cash_future_family_targets(mapped_current, universe_symbols),
            )
        )
        if target and not target_disabled(target)
    )
    current_targets = current_family_targets or tuple(
        target
        for target in (resolved_current,)
        if target and not target_disabled(target)
    )
    current_choice_keys = {symbol.upper() for symbol in current_targets}
    related_targets = tuple(
        symbol
        for symbol in dict.fromkeys(
            candidate
            for source in related_assets(current)
            for candidate in (
                *axi_cash_future_family_targets(source, universe_symbols),
                exact_by_key.get(source.upper(), source),
            )
        )
        if symbol.upper() in universe_keys
        and symbol.upper() not in current_choice_keys
        and not target_disabled(symbol)
    )
    same_group_targets = tuple(
        symbol
        for symbol in dict.fromkeys(universe_symbols)
        if source_group
        and symbol.upper() not in current_choice_keys
        and group_for(symbol) == source_group
        and not target_disabled(symbol)
    )
    return tuple(dict.fromkeys(current_targets)), related_targets, same_group_targets


def production_viable_source_seeds(
    seeds: list[Seed],
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
) -> list[Seed]:
    viable: list[Seed] = []
    for seed in seeds:
        current_targets, related_targets, same_group_targets = target_symbol_options_for_seed(
            seed,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
            group_by_symbol=group_by_symbol,
        )
        if current_targets or related_targets or same_group_targets:
            viable.append(seed)
    return viable


def discovery_ranked_seed_selection(
    seeds: list[Seed],
    max_seeds: int,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    fitness_feedback: dict[str, float] | None = None,
    exploitable_min_ratio: float = DISCOVERY_EXPLOITABLE_SEED_MIN_RATIO,
) -> list[tuple[float, Seed, float, float, float]]:
    """Budget discovery seeds between direct exploitation and cross-asset search.

    A source is directly exploitable only when its current symbol resolves to
    an enabled broker target. Cross-asset sources remain represented, but can
    no longer consume most of a bounded cohort merely because their historical
    feedback score is high on a symbol that cannot be executed here.
    """

    if max_seeds <= 0 or len(seeds) <= max_seeds:
        return ranked_seed_selection(
            seeds,
            max_seeds,
            asset_feedback,
            timeframe_feedback,
            rng,
            aliases,
            group_by_symbol,
            fitness_feedback,
            DISCOVERY_SEED_SYMBOL_RESERVE_RATIO,
        )

    exploitable: list[Seed] = []
    cross_asset: list[Seed] = []
    universe_keys = {symbol.upper() for symbol in universe_symbols}
    for seed in seeds:
        current_targets, _related_targets, _same_group_targets = target_symbol_options_for_seed(
            seed,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
            group_by_symbol=group_by_symbol,
        )
        has_broker_target = any(target.upper() in universe_keys for target in current_targets)
        (exploitable if has_broker_target else cross_asset).append(seed)

    limit = min(max_seeds, len(seeds))
    exploitable_quota = min(len(exploitable), capped_count(limit, exploitable_min_ratio))
    cross_asset_quota = min(len(cross_asset), limit - exploitable_quota)

    def select(pool: list[Seed], count: int) -> list[tuple[float, Seed, float, float, float]]:
        if count <= 0:
            return []
        return ranked_seed_selection(
            pool,
            count,
            asset_feedback,
            timeframe_feedback,
            rng,
            aliases,
            group_by_symbol,
            fitness_feedback,
            DISCOVERY_SEED_SYMBOL_RESERVE_RATIO,
        )

    selected = select(exploitable, exploitable_quota)
    selected.extend(select(cross_asset, cross_asset_quota))
    selected_ids = {id(item[1]) for item in selected}
    remaining = [seed for seed in seeds if id(seed) not in selected_ids]
    selected.extend(select(remaining, limit - len(selected)))
    return selected


def discovery_source_mix_feedback(
    memory: AgentMemory,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str],
    *,
    symbol_map: dict[str, str],
    disabled_symbols: set[str],
    group_by_symbol: dict[str, str],
) -> DiscoverySourceMix:
    universe_keys = {symbol.upper() for symbol in universe_symbols}
    observations: list[dict[str, object]] = []
    exploitable_by_symbol: dict[str, bool] = {}
    for row in memory.candidate_source_feedback_rows():
        source_symbol = str(row["symbol"] or "UNKNOWN")
        source_key = source_symbol.upper()
        if source_key not in exploitable_by_symbol:
            seed = Seed(
                Path(str(row["seed_path"])),
                source_symbol,
                str(row["period"] or "UNKNOWN"),
                str(row["family"] or ""),
                "1",
            )
            current_targets, _related_targets, _same_group_targets = target_symbol_options_for_seed(
                seed,
                universe_symbols,
                aliases,
                symbol_map=symbol_map,
                disabled_symbols=disabled_symbols,
                group_by_symbol=group_by_symbol,
            )
            exploitable_by_symbol[source_key] = any(
                target.upper() in universe_keys for target in current_targets
            )
        observations.append(
            {
                "run_id": row["run_id"],
                "generation": row["generation"],
                "seed_path": row["seed_path"],
                "status": row["status"],
                "robust_status": row["robust_status"],
                "final_tick_status": row["final_tick_status"],
                "final_tick_6m_status": row["final_tick_6m_status"],
                "exploitable": exploitable_by_symbol[source_key],
            }
        )
    return estimate_discovery_source_mix(observations)


def discovery_target_policy_feedback(memory: AgentMemory) -> DiscoveryTargetPolicyMix:
    return estimate_discovery_target_policy_mix(memory.candidate_policy_feedback_rows())


def apply_discovery_target_policy_schedule(
    args: argparse.Namespace,
    mix: DiscoveryTargetPolicyMix,
) -> None:
    for attr in (
        "asset_unseeded_prob_gen1",
        "asset_unseeded_prob_gen2",
        "asset_unseeded_prob_late",
    ):
        setattr(args, attr, float(getattr(args, attr)) * mix.unseeded_multiplier)
