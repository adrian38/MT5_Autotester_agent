"""Eleccion del simbolo y el timeframe objetivo de cada variante."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

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
from ubs_agent_target_types import DiverseTargetOptions


@dataclass
class _TargetSymbolScope:
    """Candidatos de target ya filtrados por universo, alias y politica."""

    seed: Seed
    asset_feedback: dict[str, float]
    aliases: dict[str, str]
    symbol_map: dict[str, str]
    disabled_symbols: set[str] | None
    universe_symbols: tuple[str, ...]
    group_by_symbol: dict[str, str] | None
    current: str
    resolved_current: str
    current_targets: tuple[str, ...]
    related: tuple[str, ...]
    universe_choices: tuple[str, ...]
    unseeded_choices: tuple[str, ...]

    def weight(self, symbol: str) -> float:
        """Realimentacion del activo, por su nombre canonico o literal."""
        canonical = canonical_symbol(symbol, self.aliases).upper()
        return self.asset_feedback.get(canonical, self.asset_feedback.get(symbol.upper(), 0.0))


def _target_symbol_disabled(
    symbol: str, universe_symbols: tuple[str, ...], aliases: dict[str, str],
    symbol_map: dict[str, str], disabled_symbols: set[str] | None,
) -> bool:
    """Si el simbolo no puede usarse como target con la politica actual."""
    return target_symbol_disabled(
        symbol,
        universe_symbols,
        aliases,
        symbol_map=symbol_map,
        disabled_symbols=disabled_symbols,
    )


def _current_target_candidates(
    current: str, mapped_current: str, resolved_current: str,
    universe_symbols: tuple[str, ...], disabled,
) -> tuple[str, ...]:
    """Targets de la familia del simbolo actual, o el propio simbolo."""
    current_family_targets = tuple(
        target
        for target in dict.fromkeys(
            (
                *axi_cash_future_family_targets(current, universe_symbols),
                *axi_cash_future_family_targets(mapped_current, universe_symbols),
            )
        )
        if target and not disabled(target)
    )
    return current_family_targets or tuple(
        target
        for target in (resolved_current,)
        if target and not disabled(target)
    )


def _related_target_candidates(
    current: str, universe_symbols: tuple[str, ...], exact_by_key: dict[str, str], disabled,
) -> tuple[str, ...]:
    """Targets relacionados con el simbolo actual que siguen habilitados."""
    return tuple(
        symbol
        for symbol in dict.fromkeys(
            candidate
            for source in related_assets(current)
            for candidate in (
                *axi_cash_future_family_targets(source, universe_symbols),
                exact_by_key.get(source.upper(), source),
            )
        )
        if not disabled(symbol)
    )


def _build_target_symbol_scope(
    seed: Seed, asset_feedback: dict[str, float], universe_symbols: tuple[str, ...],
    aliases: dict[str, str], symbol_map: dict[str, str], disabled_symbols: set[str] | None,
    unseeded_universe_symbols: tuple[str, ...], group_by_symbol: dict[str, str] | None,
) -> _TargetSymbolScope:
    """Resuelve el simbolo actual y los conjuntos de candidatos posibles."""
    current = seed.symbol or "UNKNOWN"
    exact_by_key = {symbol.upper(): symbol for symbol in universe_symbols}
    for alias, target in aliases.items():
        exact_by_key[str(alias).upper()] = target
    normalized_current = normalize_set_symbol(current)
    mapped_current = normalize_set_symbol(apply_symbol_map(current, symbol_map))
    resolved_current = exact_by_key.get(mapped_current, exact_by_key.get(normalized_current, current))

    def disabled(symbol: str) -> bool:
        return _target_symbol_disabled(
            symbol, universe_symbols, aliases, symbol_map, disabled_symbols
        )

    return _TargetSymbolScope(
        seed=seed,
        asset_feedback=asset_feedback,
        aliases=aliases,
        symbol_map=symbol_map,
        disabled_symbols=disabled_symbols,
        universe_symbols=universe_symbols,
        group_by_symbol=group_by_symbol,
        current=current,
        resolved_current=resolved_current,
        current_targets=_current_target_candidates(
            current, mapped_current, resolved_current, universe_symbols, disabled
        ),
        related=_related_target_candidates(current, universe_symbols, exact_by_key, disabled),
        universe_choices=tuple(
            symbol for symbol in dict.fromkeys(universe_symbols)
            if symbol.upper() != resolved_current.upper() and not disabled(symbol)
        ),
        unseeded_choices=tuple(
            symbol for symbol in dict.fromkeys(unseeded_universe_symbols)
            if symbol.upper() != resolved_current.upper() and not disabled(symbol)
        ),
    )


def _forced_unseeded_target(
    scope: _TargetSymbolScope, rng: random.Random, asset_group_feedback: dict[str, float] | None
) -> tuple[str, str]:
    """Target sin seed previa, guiado por la realimentacion del grupo."""
    unseen = [
        symbol for symbol in scope.unseeded_choices
        if symbol.upper() not in scope.asset_feedback
    ]
    forced_pool = tuple(unseen or scope.unseeded_choices)
    if scope.group_by_symbol:
        return (
            choose_group_guided_unseeded_symbol(
                forced_pool,
                rng,
                scope.aliases,
                scope.group_by_symbol,
                asset_group_feedback or {},
            ),
            "asset_unseeded_group_feedback",
        )
    return rng.choice(list(forced_pool)), "asset_unseeded_force"


def _production_target_symbol(
    scope: _TargetSymbolScope, rng: random.Random
) -> tuple[str, str] | None:
    """Target de produccion: explotar el actual o seguir la evidencia."""
    current_choices, related_choices, same_group_choices = target_symbol_options_for_seed(
        scope.seed,
        scope.universe_symbols,
        scope.aliases,
        symbol_map=scope.symbol_map,
        disabled_symbols=scope.disabled_symbols,
        group_by_symbol=scope.group_by_symbol,
    )
    if scope.group_by_symbol:
        feedback_scope = tuple(dict.fromkeys((*related_choices, *same_group_choices)))
    else:
        feedback_scope = scope.universe_choices
    evidence_choices = tuple(
        symbol for symbol in feedback_scope if scope.weight(symbol) > 0.0
    )
    if current_choices and rng.random() < PRODUCTION_CURRENT_SYMBOL_PROBABILITY:
        return sorted(current_choices, key=scope.weight, reverse=True)[0], "production_exploit"
    for choices, reason in (
        (evidence_choices, "production_asset_feedback"),
        (related_choices, "production_asset_related"),
        (same_group_choices, "production_asset_group"),
        (current_choices, "production_exploit"),
    ):
        if choices:
            return sorted(choices, key=scope.weight, reverse=True)[0], reason
    if scope.universe_choices and not scope.group_by_symbol:
        return rng.choice(scope.universe_choices), "production_asset_fallback"
    return None


def _discovery_target_symbol(
    scope: _TargetSymbolScope, rng: random.Random, current_target_probability: float,
    universe_feedback_probability: float,
) -> tuple[str, str] | None:
    """Target de descubrimiento: explotar, explorar el universo o relacionados."""
    asset_feedback = scope.asset_feedback
    if scope.current_targets and rng.random() < current_target_probability:
        ranked = sorted(scope.current_targets, key=scope.weight, reverse=True)
        if ranked and rng.random() < 0.55:
            return ranked[0], "exploit"
        return rng.choice(scope.current_targets), "exploit"
    if scope.universe_choices and rng.random() < 0.65:
        ranked = sorted(
            scope.universe_choices,
            key=lambda item: asset_feedback.get(item.upper(), -999999.0),
            reverse=True,
        )
        ranked_with_feedback = [symbol for symbol in ranked if symbol.upper() in asset_feedback]
        if ranked_with_feedback and rng.random() < universe_feedback_probability:
            return ranked_with_feedback[0], "asset_universe_feedback"
        return rng.choice(scope.universe_choices), "asset_universe_explore"
    choices = tuple(
        symbol for symbol in scope.related if symbol.upper() != scope.current.upper()
    )
    if not choices:
        if scope.universe_choices:
            return rng.choice(scope.universe_choices), "asset_universe_fallback"
        return scope.resolved_current, "exploit"
    ranked = sorted(choices, key=lambda item: asset_feedback.get(item.upper(), 0.0), reverse=True)
    if ranked and rng.random() < 0.50:
        return ranked[0], "asset_feedback"
    return rng.choice(choices), "asset_explore"


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
    scope = _build_target_symbol_scope(
        seed, asset_feedback, universe_symbols, aliases or {}, symbol_map or {},
        disabled_symbols, unseeded_universe_symbols, group_by_symbol,
    )
    if (
        force_unseeded_universe
        and scope.unseeded_choices
        and rng.random() < force_unseeded_probability
    ):
        return _forced_unseeded_target(scope, rng, asset_group_feedback)
    if production_mode:
        return _production_target_symbol(scope, rng)
    return _discovery_target_symbol(
        scope, rng, current_target_probability, universe_feedback_probability
    )


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


def _fallback_symbol_candidates(
    seed: Seed,
    asset_feedback: dict[str, float],
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str],
    symbol_map: dict[str, str] | None,
    disabled_symbols: set[str] | None,
    production_mode: bool,
    group_by_symbol: dict[str, str] | None,
) -> tuple[str, ...]:
    family_candidates = tuple(
        target
        for source in (seed.symbol, *related_assets(seed.symbol))
        for target in axi_cash_future_family_targets(source, universe_symbols)
    )
    related_candidates = tuple(dict.fromkeys((*family_candidates, *related_assets(seed.symbol))))
    if not production_mode:
        return tuple(dict.fromkeys((seed.symbol, *related_candidates, *universe_symbols)))
    _current_targets, related_targets, same_group_targets = target_symbol_options_for_seed(
        seed, universe_symbols, aliases, symbol_map=symbol_map,
        disabled_symbols=disabled_symbols, group_by_symbol=group_by_symbol,
    )
    if group_by_symbol:
        return tuple(dict.fromkeys((seed.symbol, *related_targets, *same_group_targets)))
    evidence_symbols = tuple(
        symbol for symbol in universe_symbols
        if asset_feedback.get(canonical_symbol(symbol, aliases).upper(), 0.0) > 0.0
    )
    return tuple(dict.fromkeys((seed.symbol, *related_candidates, *evidence_symbols)))


def _fallback_period_candidates(
    seed: Seed, timeframe_feedback: dict[str, float],
    timeframe_universe: tuple[str, ...], production_mode: bool,
) -> tuple[str, ...]:
    if not production_mode:
        return tuple(dict.fromkeys(
            (*related_timeframes(seed.period, timeframe_universe), *timeframe_universe)
        ))
    positive_periods = tuple(
        period for period in timeframe_universe
        if timeframe_feedback.get(str(period).upper(), 0.0) > 0.0
    )
    candidates = filter_timeframe_universe(
        tuple(dict.fromkeys((seed.period, *positive_periods))), timeframe_universe,
    )
    return candidates or related_timeframes(seed.period, timeframe_universe)


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
    symbol_candidates = _fallback_symbol_candidates(
        seed, asset_feedback, universe_symbols, aliases, symbol_map, disabled_symbols,
        production_mode, group_by_symbol,
    )
    period_candidates = _fallback_period_candidates(
        seed, timeframe_feedback, timeframe_universe, production_mode,
    )
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


def _rerolled_diverse_target(
    seed: Seed, asset_feedback: dict[str, float], timeframe_feedback: dict[str, float],
    rng: random.Random, limiter: TargetDiversityLimiter, universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None, options: DiverseTargetOptions,
) -> tuple[str, str, str] | None:
    last_symbol, last_period, last_policy = seed.symbol, seed.period, "exploit+tf_exploit"
    attempts = PRODUCTION_DIVERSITY_REROLL_ATTEMPTS if options.production_mode else DIVERSITY_REROLL_ATTEMPTS
    for attempt in range(attempts + 1):
        symbol_choice = choose_target_symbol(
            seed, asset_feedback, rng, universe_symbols, aliases,
            symbol_map=options.symbol_map, disabled_symbols=options.disabled_symbols,
            force_unseeded_universe=options.force_unseeded_universe,
            unseeded_universe_symbols=options.unseeded_universe_symbols,
            force_unseeded_probability=options.asset_unseeded_probability,
            production_mode=options.production_mode, group_by_symbol=options.group_by_symbol,
            asset_group_feedback=options.asset_group_feedback,
            universe_feedback_probability=options.universe_feedback_probability,
            current_target_probability=options.current_target_probability,
        )
        if symbol_choice is None:
            continue
        target_symbol, policy = symbol_choice
        target_period, period_policy = choose_target_period(
            seed, timeframe_feedback, rng, timeframe_universe=options.timeframe_universe,
            force_unseeded_timeframes=options.force_unseeded_universe,
            unseeded_timeframes=options.unseeded_timeframes,
            force_unseeded_probability=options.timeframe_unseeded_probability,
            production_mode=options.production_mode,
            current_timeframe_probability=options.current_timeframe_probability,
        )
        last_symbol, last_period = target_symbol, target_period
        last_policy = f"{policy}+{period_policy}"
        if limiter.allows(target_symbol, target_period):
            suffix = "+diversity_reroll" if attempt else ""
            return target_symbol, target_period, f"{last_policy}{suffix}"
    fallback = diverse_target_fallback(
        seed, asset_feedback, timeframe_feedback, rng, limiter, universe_symbols, aliases,
        timeframe_universe=options.timeframe_universe, symbol_map=options.symbol_map,
        disabled_symbols=options.disabled_symbols, production_mode=options.production_mode,
        group_by_symbol=options.group_by_symbol,
    )
    if fallback is not None:
        return fallback[0], fallback[1], "diversity_fallback"
    if options.production_mode:
        return None
    return last_symbol, last_period, f"{last_policy}+diversity_overflow"


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
    options = DiverseTargetOptions(
        timeframe_universe, symbol_map, disabled_symbols, force_unseeded_universe,
        unseeded_universe_symbols, unseeded_timeframes, asset_unseeded_probability,
        timeframe_unseeded_probability, production_mode, group_by_symbol,
        asset_group_feedback, universe_feedback_probability,
        current_target_probability, current_timeframe_probability,
    )
    return _rerolled_diverse_target(
        seed, asset_feedback, timeframe_feedback, rng, limiter,
        universe_symbols, aliases, options,
    )
