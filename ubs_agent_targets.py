"""Eleccion del simbolo y el timeframe objetivo de cada variante."""
from __future__ import annotations

import math
import random

from run_tests import apply_symbol_map, normalize_set_symbol
from ubs.account import axi_cash_future_family_targets
from ubs.models import Seed
from ubs.selection import (
    DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
    DISCOVERY_CURRENT_TARGET_DEFAULT,
    DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
)
from ubs.universe import canonical_symbol
from ubs_agent_config import (
    DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT,
    DISCOVERY_GROUP_FEEDBACK_TEMPERATURE,
    DIVERSITY_REROLL_ATTEMPTS,
    PRODUCTION_CURRENT_SYMBOL_PROBABILITY,
    PRODUCTION_DIVERSITY_REROLL_ATTEMPTS,
    TIMEFRAME_UNIVERSE,
)
from ubs_agent_policy import (
    related_assets,
    target_symbol_disabled,
    target_symbol_options_for_seed,
)
from ubs_agent_seeds_plan import (
    TargetDiversityLimiter,
)


def choose_target_symbol(
    seed: Seed,
    asset_feedback: dict[str, float],
    rng: random.Random,
    universe_symbols: tuple[str, ...] = (),
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    force_unseeded_universe: bool = False,
    unseeded_universe_symbols: tuple[str, ...] = (),
    force_unseeded_probability: float = 0.65,
    production_mode: bool = False,
    group_by_symbol: dict[str, str] | None = None,
    asset_group_feedback: dict[str, float] | None = None,
    universe_feedback_probability: float = DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
    current_target_probability: float = DISCOVERY_CURRENT_TARGET_DEFAULT,
) -> tuple[str, str] | None:
    aliases = aliases or {}
    symbol_map = symbol_map or {}
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

    related = tuple(
        symbol
        for symbol in dict.fromkeys(
            candidate
            for source in related_assets(current)
            for candidate in (
                *axi_cash_future_family_targets(source, universe_symbols),
                exact_by_key.get(source.upper(), source),
            )
        )
        if not target_disabled(symbol)
    )
    universe_choices = tuple(
        symbol for symbol in dict.fromkeys(universe_symbols)
        if symbol.upper() != resolved_current.upper() and not target_disabled(symbol)
    )
    unseeded_choices = tuple(
        symbol for symbol in dict.fromkeys(unseeded_universe_symbols)
        if symbol.upper() != resolved_current.upper() and not target_disabled(symbol)
    )
    if force_unseeded_universe and unseeded_choices and rng.random() < force_unseeded_probability:
        unseen = [symbol for symbol in unseeded_choices if symbol.upper() not in asset_feedback]
        forced_pool = tuple(unseen or unseeded_choices)
        if group_by_symbol:
            return (
                choose_group_guided_unseeded_symbol(
                    forced_pool,
                    rng,
                    aliases,
                    group_by_symbol,
                    asset_group_feedback or {},
                ),
                "asset_unseeded_group_feedback",
            )
        return rng.choice(list(forced_pool)), "asset_unseeded_force"

    def asset_weight(symbol: str) -> float:
        canonical = canonical_symbol(symbol, aliases).upper()
        return asset_feedback.get(canonical, asset_feedback.get(symbol.upper(), 0.0))

    if production_mode:
        current_choices, related_choices, same_group_choices = target_symbol_options_for_seed(
            seed,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
            group_by_symbol=group_by_symbol,
        )
        if group_by_symbol:
            feedback_scope = tuple(dict.fromkeys((*related_choices, *same_group_choices)))
        else:
            feedback_scope = universe_choices
        evidence_choices = tuple(
            symbol
            for symbol in feedback_scope
            if asset_weight(symbol) > 0.0
        )
        if current_choices and rng.random() < PRODUCTION_CURRENT_SYMBOL_PROBABILITY:
            ranked = sorted(current_choices, key=asset_weight, reverse=True)
            return ranked[0], "production_exploit"
        if evidence_choices:
            ranked = sorted(
                evidence_choices,
                key=asset_weight,
                reverse=True,
            )
            return ranked[0], "production_asset_feedback"
        if related_choices:
            ranked = sorted(
                related_choices,
                key=asset_weight,
                reverse=True,
            )
            return ranked[0], "production_asset_related"
        if same_group_choices:
            ranked = sorted(
                same_group_choices,
                key=asset_weight,
                reverse=True,
            )
            return ranked[0], "production_asset_group"
        if current_choices:
            ranked = sorted(current_choices, key=asset_weight, reverse=True)
            return ranked[0], "production_exploit"
        if universe_choices and not group_by_symbol:
            return rng.choice(universe_choices), "production_asset_fallback"
        return None

    if current_targets and rng.random() < current_target_probability:
        ranked = sorted(current_targets, key=asset_weight, reverse=True)
        if ranked and rng.random() < 0.55:
            return ranked[0], "exploit"
        return rng.choice(current_targets), "exploit"
    if universe_choices and rng.random() < 0.65:
        ranked = sorted(universe_choices, key=lambda item: asset_feedback.get(item.upper(), -999999.0), reverse=True)
        ranked_with_feedback = [symbol for symbol in ranked if symbol.upper() in asset_feedback]
        if ranked_with_feedback and rng.random() < universe_feedback_probability:
            return ranked_with_feedback[0], "asset_universe_feedback"
        return rng.choice(universe_choices), "asset_universe_explore"

    choices = tuple(symbol for symbol in related if symbol.upper() != current.upper())
    if not choices:
        if universe_choices:
            return rng.choice(universe_choices), "asset_universe_fallback"
        return resolved_current, "exploit"
    ranked = sorted(choices, key=lambda item: asset_feedback.get(item.upper(), 0.0), reverse=True)
    if ranked and rng.random() < 0.50:
        return ranked[0], "asset_feedback"
    return rng.choice(choices), "asset_explore"


def choose_group_guided_unseeded_symbol(
    symbols: tuple[str, ...],
    rng: random.Random,
    aliases: dict[str, str],
    group_by_symbol: dict[str, str],
    asset_group_feedback: dict[str, float],
) -> str:
    """Sample an unseeded symbol using group capacity and lifecycle quality.

    Square-root capacity prevents very large universes from monopolising all
    discovery slots.  Bounded exponential feedback favours groups whose
    candidates survive the complete validation pipeline while retaining a
    non-zero probability for every group.
    """
    grouped: dict[str, list[str]] = {}
    for symbol in symbols:
        canonical = canonical_symbol(symbol, aliases).upper()
        group = group_by_symbol.get(canonical, "")
        grouped.setdefault(group, []).append(symbol)
    if not grouped:
        return rng.choice(list(symbols))
    weighted_groups: list[tuple[str, float]] = []
    for group, choices in grouped.items():
        score = float(asset_group_feedback.get(group, 0.0))
        exponent = max(
            -DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT,
            min(DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT, score / DISCOVERY_GROUP_FEEDBACK_TEMPERATURE),
        )
        weight = math.sqrt(float(len(choices))) * math.exp(exponent)
        weighted_groups.append((group, weight))
    total = sum(weight for _group, weight in weighted_groups)
    cursor = rng.random() * total
    selected_group = weighted_groups[-1][0]
    upto = 0.0
    for group, weight in weighted_groups:
        upto += weight
        if cursor <= upto:
            selected_group = group
            break
    return rng.choice(grouped[selected_group])


def filter_timeframe_universe(periods: tuple[str, ...], timeframe_universe: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {period.upper() for period in timeframe_universe}
    return tuple(period for period in dict.fromkeys(periods) if period.upper() in allowed)


def related_timeframes(
    period: str,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
) -> tuple[str, ...]:
    period = period.upper()
    related: tuple[str, ...]
    if period == "M15":
        related = ("M5", "M15", "M30", "H1")
    elif period == "M30":
        related = ("M15", "M30", "H1", "H2", "H4")
    elif period == "H1":
        related = ("M30", "H1", "H2", "H3", "H4", "D1")
    elif period == "H2":
        related = ("H1", "H2", "H3", "H4")
    elif period == "H3":
        related = ("H1", "H2", "H3", "H4")
    elif period == "H4":
        related = ("H1", "H2", "H3", "H4", "D1")
    elif period == "D1":
        related = ("H4", "D1", "W1", "MN")
    elif period == "M1":
        related = ("M1", "M5", "M15")
    elif period == "M5":
        related = ("M1", "M5", "M15", "M30")
    elif period == "W1":
        related = ("D1", "W1", "MN")
    elif period == "MN":
        related = ("D1", "W1", "MN")
    else:
        related = timeframe_universe
    return filter_timeframe_universe(related, timeframe_universe)


def choose_target_period(
    seed: Seed,
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    *,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
    force_unseeded_timeframes: bool = False,
    unseeded_timeframes: tuple[str, ...] = (),
    force_unseeded_probability: float = 0.50,
    production_mode: bool = False,
    current_timeframe_probability: float = DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
) -> tuple[str, str]:
    current = seed.period.upper()
    choices = tuple(dict.fromkeys(related_timeframes(current, timeframe_universe)))
    if not choices:
        return current, "tf_exploit"
    forced_choices = tuple(period for period in choices if period.upper() in {tf.upper() for tf in unseeded_timeframes})
    if force_unseeded_timeframes and forced_choices and rng.random() < force_unseeded_probability:
        unseen = [period for period in forced_choices if period.upper() not in timeframe_feedback]
        return rng.choice(unseen or list(forced_choices)), "tf_unseeded_force"
    if production_mode:
        if current in choices and rng.random() < PRODUCTION_CURRENT_SYMBOL_PROBABILITY:
            return current, "tf_production_exploit"
        ranked = sorted(choices, key=lambda item: timeframe_feedback.get(item.upper(), 0.0), reverse=True)
        ranked_with_positive_feedback = [period for period in ranked if timeframe_feedback.get(period.upper(), 0.0) > 0.0]
        if ranked_with_positive_feedback:
            return ranked_with_positive_feedback[0], "tf_production_feedback"
        if current in choices:
            return current, "tf_production_exploit"
        return choices[0], "tf_production_related"
    if current in choices and rng.random() < current_timeframe_probability:
        return current, "tf_exploit"
    ranked = sorted(choices, key=lambda item: timeframe_feedback.get(item.upper(), -999999.0), reverse=True)
    ranked_with_feedback = [period for period in ranked if period.upper() in timeframe_feedback]
    if ranked_with_feedback and rng.random() < 0.45:
        return ranked_with_feedback[0], "tf_feedback"
    unexplored = [period for period in choices if period.upper() not in timeframe_feedback]
    if unexplored and rng.random() < 0.70:
        return rng.choice(unexplored), "tf_explore_new"
    return rng.choice(choices), "tf_explore"


def diverse_target_fallback(
    seed: Seed,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    limiter: TargetDiversityLimiter,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    production_mode: bool = False,
    group_by_symbol: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    aliases = aliases or {}
    family_candidates = tuple(
        target
        for source in (seed.symbol, *related_assets(seed.symbol))
        for target in axi_cash_future_family_targets(source, universe_symbols)
    )
    related_candidates = tuple(dict.fromkeys((*family_candidates, *related_assets(seed.symbol))))
    if production_mode:
        _current_targets, related_targets, same_group_targets = target_symbol_options_for_seed(
            seed,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
            group_by_symbol=group_by_symbol,
        )
        if group_by_symbol:
            symbol_candidates = tuple(dict.fromkeys((seed.symbol, *related_targets, *same_group_targets)))
        else:
            evidence_symbols = tuple(
                symbol
                for symbol in universe_symbols
                if asset_feedback.get(canonical_symbol(symbol, aliases).upper(), 0.0) > 0.0
            )
            symbol_candidates = tuple(dict.fromkeys((seed.symbol, *related_candidates, *evidence_symbols)))
    else:
        symbol_candidates = tuple(dict.fromkeys((seed.symbol, *related_candidates, *universe_symbols)))
    if production_mode:
        positive_periods = tuple(
            period
            for period in timeframe_universe
            if timeframe_feedback.get(str(period).upper(), 0.0) > 0.0
        )
        period_candidates = filter_timeframe_universe(
            tuple(dict.fromkeys((seed.period, *positive_periods))),
            timeframe_universe,
        )
        if not period_candidates:
            period_candidates = related_timeframes(seed.period, timeframe_universe)
    else:
        period_candidates = tuple(dict.fromkeys((*related_timeframes(seed.period, timeframe_universe), *timeframe_universe)))
    scored: list[tuple[float, str, str]] = []
    for symbol in symbol_candidates:
        if not symbol or symbol == "UNKNOWN":
            continue
        if target_symbol_disabled(
            symbol,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
        ):
            continue
        asset_key = canonical_symbol(symbol, aliases).upper()
        asset_weight = asset_feedback.get(asset_key, 0.0)
        for period in period_candidates:
            if not period or period == "UNKNOWN":
                continue
            if not limiter.allows(symbol, period):
                continue
            timeframe_weight = timeframe_feedback.get(period.upper(), 0.0) * 0.50
            scored.append((asset_weight + timeframe_weight + rng.random() * 5.0, symbol, period))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    _, symbol, period = scored[0]
    return symbol, period


def choose_diverse_target(
    seed: Seed,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    limiter: TargetDiversityLimiter,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    force_unseeded_universe: bool = False,
    unseeded_universe_symbols: tuple[str, ...] = (),
    unseeded_timeframes: tuple[str, ...] = (),
    asset_unseeded_probability: float = 0.0,
    timeframe_unseeded_probability: float = 0.0,
    production_mode: bool = False,
    group_by_symbol: dict[str, str] | None = None,
    asset_group_feedback: dict[str, float] | None = None,
    universe_feedback_probability: float = DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
    current_target_probability: float = DISCOVERY_CURRENT_TARGET_DEFAULT,
    current_timeframe_probability: float = DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
) -> tuple[str, str, str] | None:
    last_symbol = seed.symbol
    last_period = seed.period
    last_policy = "exploit+tf_exploit"
    reroll_attempts = PRODUCTION_DIVERSITY_REROLL_ATTEMPTS if production_mode else DIVERSITY_REROLL_ATTEMPTS
    for attempt in range(reroll_attempts + 1):
        symbol_choice = choose_target_symbol(
            seed,
            asset_feedback,
            rng,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
            force_unseeded_universe=force_unseeded_universe,
            unseeded_universe_symbols=unseeded_universe_symbols,
            force_unseeded_probability=asset_unseeded_probability,
            production_mode=production_mode,
            group_by_symbol=group_by_symbol,
            asset_group_feedback=asset_group_feedback,
            universe_feedback_probability=universe_feedback_probability,
            current_target_probability=current_target_probability,
        )
        if symbol_choice is None:
            continue
        target_symbol, policy = symbol_choice
        target_period, period_policy = choose_target_period(
            seed,
            timeframe_feedback,
            rng,
            timeframe_universe=timeframe_universe,
            force_unseeded_timeframes=force_unseeded_universe,
            unseeded_timeframes=unseeded_timeframes,
            force_unseeded_probability=timeframe_unseeded_probability,
            production_mode=production_mode,
            current_timeframe_probability=current_timeframe_probability,
        )
        full_policy = f"{policy}+{period_policy}"
        last_symbol = target_symbol
        last_period = target_period
        last_policy = full_policy
        if limiter.allows(target_symbol, target_period):
            if attempt:
                full_policy = f"{full_policy}+diversity_reroll"
            return target_symbol, target_period, full_policy

    fallback = diverse_target_fallback(
        seed,
        asset_feedback,
        timeframe_feedback,
        rng,
        limiter,
        universe_symbols,
        aliases,
        timeframe_universe=timeframe_universe,
        symbol_map=symbol_map,
        disabled_symbols=disabled_symbols,
        production_mode=production_mode,
        group_by_symbol=group_by_symbol,
    )
    if fallback is not None:
        target_symbol, target_period = fallback
        return target_symbol, target_period, "diversity_fallback"
    if production_mode:
        return None
    return last_symbol, last_period, f"{last_policy}+diversity_overflow"
