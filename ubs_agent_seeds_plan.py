"""Seleccion de semillas, cupos de diversidad y plan de timeframes."""
from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

from ubs.account import DEFAULT_BROKER, account_disabled_symbols_path
from ubs.models import Seed, Variant
from ubs.score import ScoreResult
from ubs.universe import canonical_symbol, seed_symbol_disabled
from ubs_agent_config import (
    ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION,
    ASSET_UNSEEDED_FORCE_PROB_LATE,
    BASE_DIR,
    DEFAULT_TARGET_GROUP_CAP_RATIO,
    DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
    FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS,
    PRODUCTION_NEXT_SEED_BACKFILL_MIN_RATIO,
    PRODUCTION_SEED_SYMBOL_CAP_RATIO,
    PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
    SELECTION_FITNESS_APPLIED_SCALE,
    TARGET_GROUP_CAP_RATIOS,
    TARGET_PAIR_CAP_RATIO,
    TARGET_SYMBOL_CAP_RATIO,
    TARGET_TIMEFRAME_CAP_RATIO,
    TF_UNSEEDED_FORCE_PROB_BY_GENERATION,
    TF_UNSEEDED_FORCE_PROB_LATE,
)


def seeds_from_variants(variants: list[Variant]) -> list[Seed]:
    return [variant_as_next_seed(variant) for variant in variants]


def seeds_from_survivors(survivors: list[tuple[Variant, ScoreResult]]) -> list[Seed]:
    return [variant_as_next_seed(variant) for variant, _ in survivors]


def discovery_seed_pool(next_seeds: list[Seed], source_seeds: list[Seed]) -> list[Seed]:
    pool = list(next_seeds)
    seen_paths = {str(seed.path).lower() for seed in pool}
    for seed in source_seeds:
        key = str(seed.path).lower()
        if key in seen_paths:
            continue
        pool.append(seed)
        seen_paths.add(key)
    return pool


def production_seed_pool(next_seeds: list[Seed], source_seeds: list[Seed], max_seeds: int) -> list[Seed]:
    if max_seeds <= 0:
        return list(next_seeds)
    min_next_seeds = min(max_seeds, capped_count(max_seeds, PRODUCTION_NEXT_SEED_BACKFILL_MIN_RATIO))
    if len(next_seeds) >= min_next_seeds:
        return list(next_seeds)
    pool = list(next_seeds)
    seen_paths = {str(seed.path).lower() for seed in pool}
    for seed in source_seeds:
        key = str(seed.path).lower()
        if key in seen_paths:
            continue
        pool.append(seed)
        seen_paths.add(key)
        if len(pool) >= max_seeds:
            break
    return pool


def target_symbol_cap_ratio(force_unseeded_universe: bool) -> float:
    return DISCOVERY_TARGET_SYMBOL_CAP_RATIO if force_unseeded_universe else PRODUCTION_TARGET_SYMBOL_CAP_RATIO


def seed_symbol_cap_ratio(force_unseeded_universe: bool) -> float:
    return TARGET_SYMBOL_CAP_RATIO if force_unseeded_universe else PRODUCTION_SEED_SYMBOL_CAP_RATIO


def next_generation_seed_pool(
    next_seeds: list[Seed],
    source_seeds: list[Seed],
    max_seeds: int,
    *,
    force_unseeded_universe: bool,
) -> list[Seed]:
    if force_unseeded_universe:
        return discovery_seed_pool(next_seeds, source_seeds)
    return production_seed_pool(next_seeds, source_seeds, max_seeds)


def variant_as_next_seed(variant: Variant) -> Seed:
    return Seed(
        path=variant.path,
        symbol=variant.target_symbol,
        period=variant.target_period,
        family=variant.seed.family,
        run_strategy=variant.seed.run_strategy,
    )


def ranked_seed_selection(
    seeds: list[Seed],
    max_seeds: int,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    aliases: dict[str, str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    fitness_feedback: dict[str, float] | None = None,
    symbol_reserve_ratio: float = 0.0,
    symbol_cap_ratio: float = TARGET_SYMBOL_CAP_RATIO,
    allow_overflow: bool = True,
) -> list[tuple[float, Seed, float, float, float]]:
    aliases = aliases or {}
    fitness_feedback = fitness_feedback or {}
    valid = [seed for seed in seeds if seed.symbol != "UNKNOWN" and seed.period != "UNKNOWN"]
    if not valid:
        valid = seeds
    scored: list[tuple[float, Seed, float, float, float]] = []
    for seed in valid:
        asset_key = canonical_symbol(seed.symbol, aliases).upper()
        asset_weight = asset_feedback.get(asset_key, 0.0)
        timeframe_weight = timeframe_feedback.get(seed.period.upper(), 0.0) * 0.50
        diversity = rng.random() * 5.0
        fitness_weight = (
            fitness_feedback.get(str(seed.path), 0.0)
            * SELECTION_FITNESS_APPLIED_SCALE
        )
        scored.append((asset_weight + timeframe_weight + fitness_weight + diversity, seed, asset_weight, timeframe_weight, diversity))
    scored.sort(key=lambda item: item[0], reverse=True)
    limit = len(scored) if max_seeds <= 0 else min(max_seeds, len(scored))
    if limit <= 0:
        return []
    limiter = TargetDiversityLimiter(
        limit,
        aliases,
        group_by_symbol=group_by_symbol,
        symbol_cap_ratio=symbol_cap_ratio,
    )
    selected: list[tuple[float, Seed, float, float, float]] = []
    selected_ids: set[int] = set()
    overflow: list[tuple[float, Seed, float, float, float]] = []
    if symbol_reserve_ratio > 0:
        reserve_limit = min(limit, capped_count(limit, symbol_reserve_ratio))
        reserved_symbols: set[str] = set()
        for item in scored:
            seed = item[1]
            symbol_key = limiter.symbol_key(seed.symbol)
            if symbol_key in reserved_symbols:
                continue
            if not limiter.allows(seed.symbol, seed.period):
                continue
            selected.append(item)
            selected_ids.add(id(seed))
            reserved_symbols.add(symbol_key)
            limiter.record(seed.symbol, seed.period)
            if len(selected) >= reserve_limit:
                break
        if len(selected) >= limit:
            return selected
    for item in scored:
        seed = item[1]
        if id(seed) in selected_ids:
            continue
        if limiter.allows(seed.symbol, seed.period):
            selected.append(item)
            selected_ids.add(id(seed))
            limiter.record(seed.symbol, seed.period)
            if len(selected) >= limit:
                return selected
        else:
            overflow.append(item)
    if allow_overflow:
        for item in overflow:
            if len(selected) >= limit:
                break
            selected.append(item)
    return selected


def choose_seeds(
    seeds: list[Seed],
    max_seeds: int,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    aliases: dict[str, str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    fitness_feedback: dict[str, float] | None = None,
) -> list[Seed]:
    return [
        seed
        for _, seed, _, _, _ in ranked_seed_selection(
            seeds, max_seeds, asset_feedback, timeframe_feedback, rng, aliases, group_by_symbol, fitness_feedback
        )
    ]


def unseeded_asset_force_probability(
    generation: int,
    unseeded_count: int,
    generation_probabilities: dict[int, float] | None = None,
    late_probability: float | None = None,
) -> float:
    if unseeded_count <= 0:
        return 0.0
    probabilities = generation_probabilities or ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION
    late = ASSET_UNSEEDED_FORCE_PROB_LATE if late_probability is None else float(late_probability)
    return float(probabilities.get(generation, late))


def unseeded_timeframe_force_probability(
    generation: int,
    unseeded_count: int,
    generation_probabilities: dict[int, float] | None = None,
    late_probability: float | None = None,
) -> float:
    if unseeded_count <= 0:
        return 0.0
    probabilities = generation_probabilities or TF_UNSEEDED_FORCE_PROB_BY_GENERATION
    late = TF_UNSEEDED_FORCE_PROB_LATE if late_probability is None else float(late_probability)
    return float(probabilities.get(generation, late))


def configured_unseeded_force_probabilities(
    args: argparse.Namespace,
    generation: int,
    asset_unseeded_count: int,
    timeframe_unseeded_count: int,
) -> tuple[float, float]:
    asset_probability = unseeded_asset_force_probability(
        generation,
        asset_unseeded_count,
        {
            1: float(getattr(args, "asset_unseeded_prob_gen1", ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[1])),
            2: float(getattr(args, "asset_unseeded_prob_gen2", ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[2])),
        },
        float(getattr(args, "asset_unseeded_prob_late", ASSET_UNSEEDED_FORCE_PROB_LATE)),
    )
    timeframe_probability = unseeded_timeframe_force_probability(
        generation,
        timeframe_unseeded_count,
        {
            1: float(getattr(args, "timeframe_unseeded_prob_gen1", TF_UNSEEDED_FORCE_PROB_BY_GENERATION[1])),
            2: float(getattr(args, "timeframe_unseeded_prob_gen2", TF_UNSEEDED_FORCE_PROB_BY_GENERATION[2])),
        },
        float(getattr(args, "timeframe_unseeded_prob_late", TF_UNSEEDED_FORCE_PROB_LATE)),
    )
    return asset_probability, timeframe_probability


def reserved_timeframe_plan(
    selected_seeds: list[Seed],
    timeframe_universe: tuple[str, ...],
    capacity: int,
    min_ratios: dict[str, float] | None = None,
) -> list[str]:
    if capacity <= 0:
        return []
    allowed = {str(tf).upper() for tf in timeframe_universe}
    ratios = min_ratios or FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS
    plan: list[str] = []
    for timeframe, ratio in ratios.items():
        key = str(timeframe).upper()
        if key in allowed:
            plan.extend([key] * capped_count(capacity, float(ratio)))
    represented = {str(seed.period or "").upper() for seed in selected_seeds}
    planned = set(plan)
    missing = [
        str(tf).upper()
        for tf in timeframe_universe
        if str(tf).upper() not in represented and str(tf).upper() not in planned
    ]
    plan.extend(missing)
    return plan[:capacity]


def timeframe_plan_summary(plan: list[str]) -> str:
    counts = Counter(str(tf).upper() for tf in plan)
    return ", ".join(f"{tf}x{counts[tf]}" for tf in sorted(counts))


def apply_reserved_timeframe(
    *,
    reserved_timeframes: list[str],
    target_symbol: str,
    target_period: str,
    policy: str,
    seed: Seed,
    target_limiter: "TargetDiversityLimiter",
    universe_symbols: tuple[str, ...],
    disabled_symbols: set[str],
    aliases: dict[str, str],
    rng: random.Random,
) -> tuple[str, str, str]:
    if not reserved_timeframes:
        return target_symbol, target_period, policy
    reserved_period = reserved_timeframes[0]
    candidates = [target_symbol, seed.symbol]
    shuffled = list(universe_symbols)
    rng.shuffle(shuffled)
    candidates.extend(shuffled)
    seen: set[str] = set()
    for symbol in candidates:
        canonical = canonical_symbol(symbol, aliases).upper()
        if not canonical or canonical in seen or canonical in disabled_symbols:
            continue
        seen.add(canonical)
        if target_limiter.allows(symbol, reserved_period):
            reserved_timeframes.pop(0)
            suffix = "tf_reserved"
            next_policy = f"{policy}+{suffix}" if policy else suffix
            return symbol, reserved_period, next_policy
    return target_symbol, target_period, policy


def capped_count(total: int, ratio: float) -> int:
    if total <= 0:
        return 0
    return max(1, int(total * ratio + 0.999999))


def asset_group_map(asset_groups: dict[str, list[str]], aliases: dict[str, str]) -> dict[str, str]:
    group_by_symbol: dict[str, str] = {}
    for group, symbols in asset_groups.items():
        for symbol in symbols:
            group_by_symbol[canonical_symbol(symbol, aliases).upper()] = str(group)
    return group_by_symbol


def format_group_cap_summary(limiter: "TargetDiversityLimiter") -> str:
    groups = sorted(set(limiter.group_by_symbol.values()) | set(limiter.group_cap_ratios))
    if not groups:
        return f"default<={limiter.default_group_cap}"
    return ", ".join(f"{group}<={limiter.group_cap_for(group)}" for group in groups)


class TargetDiversityLimiter:
    def __init__(
        self,
        planned_variants: int,
        aliases: dict[str, str] | None = None,
        *,
        group_by_symbol: dict[str, str] | None = None,
        pair_cap_ratio: float = TARGET_PAIR_CAP_RATIO,
        symbol_cap_ratio: float = TARGET_SYMBOL_CAP_RATIO,
        timeframe_cap_ratio: float = TARGET_TIMEFRAME_CAP_RATIO,
        group_cap_ratios: dict[str, float] | None = None,
        default_group_cap_ratio: float = DEFAULT_TARGET_GROUP_CAP_RATIO,
    ) -> None:
        self.aliases = aliases or {}
        self.group_by_symbol = {str(k).upper(): str(v) for k, v in (group_by_symbol or {}).items()}
        self.group_cap_ratios = {
            str(group): float(ratio)
            for group, ratio in (group_cap_ratios or TARGET_GROUP_CAP_RATIOS).items()
        }
        self.pair_cap = capped_count(planned_variants, pair_cap_ratio)
        self.symbol_cap = capped_count(planned_variants, symbol_cap_ratio)
        self.timeframe_cap = capped_count(planned_variants, timeframe_cap_ratio)
        self.default_group_cap = capped_count(planned_variants, default_group_cap_ratio)
        self.group_caps = {
            group: capped_count(planned_variants, ratio)
            for group, ratio in self.group_cap_ratios.items()
        }
        self.pair_counts: Counter[tuple[str, str]] = Counter()
        self.symbol_counts: Counter[str] = Counter()
        self.timeframe_counts: Counter[str] = Counter()
        self.group_counts: Counter[str] = Counter()

    def symbol_key(self, symbol: str) -> str:
        return canonical_symbol(symbol, self.aliases).upper()

    def group_key(self, symbol: str) -> str:
        return self.group_by_symbol.get(self.symbol_key(symbol), "")

    def group_cap_for(self, group: str) -> int:
        return self.group_caps.get(str(group), self.default_group_cap)

    def pair_key(self, symbol: str, period: str) -> tuple[str, str]:
        return self.symbol_key(symbol), str(period or "").upper()

    def timeframe_key(self, period: str) -> str:
        return str(period or "").upper()

    def allows(self, symbol: str, period: str) -> bool:
        if self.pair_cap and self.pair_counts[self.pair_key(symbol, period)] >= self.pair_cap:
            return False
        if self.symbol_cap and self.symbol_counts[self.symbol_key(symbol)] >= self.symbol_cap:
            return False
        if self.timeframe_cap and self.timeframe_counts[self.timeframe_key(period)] >= self.timeframe_cap:
            return False
        group = self.group_key(symbol)
        group_cap = self.group_cap_for(group) if group else 0
        if group and group_cap and self.group_counts[group] >= group_cap:
            return False
        return True

    def record(self, symbol: str, period: str) -> None:
        self.pair_counts[self.pair_key(symbol, period)] += 1
        self.symbol_counts[self.symbol_key(symbol)] += 1
        self.timeframe_counts[self.timeframe_key(period)] += 1
        group = self.group_key(symbol)
        if group:
            self.group_counts[group] += 1


def generation_source_seeds(
    seeds: list[Seed],
    symbol_map: dict[str, str],
    disabled_symbols: set[str],
    seed_enabled_when_disabled: set[str],
) -> tuple[list[Seed], int]:
    allowed = [
        seed
        for seed in seeds
        if not seed_symbol_disabled(seed, disabled_symbols, symbol_map, seed_enabled_when_disabled)
    ]
    return allowed, len(seeds) - len(allowed)


def disabled_symbols_file_for_account(account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    return account_disabled_symbols_path(BASE_DIR, account_type, broker)
