"""Candidatos y puntuacion de la validacion mensual estricta."""
from __future__ import annotations

from typing import Sequence

from .ubs_portfolio import (
    RobustStrategySet,
    portfolio_symbol_key,
)
from .ubs_portfolio_candidates import (
    filter_eligible_sets,
    select_top_k_per_symbol,
)
from .ubs_portfolio_strict import (
    validate_strict_monthly_portfolio,
)
from .ubs_portfolio_utils import (
    _active_unit_allocations,
    score_set_for_portfolio,
)


def _strict_validation_for_allocations(
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    active_units = _active_unit_allocations(allocations)
    return validate_strict_monthly_portfolio(
        [full_by_id[set_id] for set_id in active_units if set_id in full_by_id],
        active_units,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        lookback_years=5,
        enforce_point_dd=enforce_point_dd,
    )


def _strict_monthly_candidate_validation(
    strategy: RobustStrategySet,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    return validate_strict_monthly_portfolio(
        [strategy],
        {strategy.set_id: 1},
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        lookback_years=5,
        enforce_point_dd=enforce_point_dd,
    )


def _strict_monthly_candidate_score(
    monthly_strategy: RobustStrategySet,
    full_strategy: RobustStrategySet,
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
    enforce_point_dd: bool = True,
) -> float:
    validation = _strict_monthly_candidate_validation(
        full_strategy,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    target_net = float(validation.get("target_month_net") or 0.0)
    best_net = float(validation.get("best_month_net") or 0.0)
    best_month = int(validation.get("best_month") or 0)
    best_gap = max(best_net - target_net, 0.0) if best_month != target_month else 0.0
    yearly = validation.get("yearly") if isinstance(validation.get("yearly"), list) else []
    positive_years = 0
    dd_over = 0.0
    for item in yearly:
        if not isinstance(item, dict):
            continue
        net = float(item.get("net") or 0.0)
        if int(item.get("trades") or 0) > 0 and net > 0:
            positive_years += 1
        dd_over += max(float(item.get("valley_dd") or 0.0) - target_valley_dd, 0.0)
        if enforce_point_dd:
            dd_over += max(float(item.get("point_dd") or 0.0) - target_point_dd, 0.0)
    base_score = score_set_for_portfolio(monthly_strategy, min_trades_2020_2026)
    return (
        target_net * 4.0
        - best_gap * 6.0
        + positive_years * 10_000.0
        - dd_over * 25.0
        + base_score * 0.05
    )


def _limit_sorted_candidates_with_symbol_reserve(
    ordered: Sequence[RobustStrategySet],
    limit: int | None,
) -> list[RobustStrategySet]:
    unique: list[RobustStrategySet] = []
    seen_ids: set[str] = set()
    for strategy in ordered:
        if strategy.set_id in seen_ids:
            continue
        unique.append(strategy)
        seen_ids.add(strategy.set_id)
    if limit is None or limit <= 0 or len(unique) <= limit:
        return unique

    selected: list[RobustStrategySet] = []
    selected_ids: set[str] = set()
    by_symbol: dict[str, list[RobustStrategySet]] = {}
    for strategy in unique:
        by_symbol.setdefault(portfolio_symbol_key(strategy.symbol), []).append(strategy)
    for group in by_symbol.values():
        if len(selected) >= limit:
            break
        strategy = group[0]
        selected.append(strategy)
        selected_ids.add(strategy.set_id)
    for strategy in unique:
        if len(selected) >= limit:
            break
        if strategy.set_id in selected_ids:
            continue
        selected.append(strategy)
        selected_ids.add(strategy.set_id)
    return selected


def _strict_monthly_candidate_variants(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    min_trades_2020_2026: int,
    top_k_per_symbol: int,
    max_total_candidates: int | None,
    enforce_point_dd: bool = True,
) -> list[tuple[str, list[RobustStrategySet]]]:
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    eligible = [
        strategy
        for strategy in filter_eligible_sets(monthly_sets, min_trades_2020_2026)
        if strategy.set_id in full_by_id
    ]
    if not eligible:
        return []

    symbol_count = len({portfolio_symbol_key(strategy.symbol) for strategy in eligible})
    configured_limit = max_total_candidates if max_total_candidates is not None else len(eligible)
    if configured_limit is None or configured_limit <= 0:
        configured_limit = len(eligible)
    strict_limit = min(len(eligible), max(symbol_count, min(int(configured_limit), 40)))

    normal = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=strict_limit,
        min_trades_2020_2026=min_trades_2020_2026,
    )

    candidate_validations = {
        strategy.set_id: _strict_monthly_candidate_validation(
            full_by_id[strategy.set_id],
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            enforce_point_dd=enforce_point_dd,
        )
        for strategy in eligible
    }
    individual_target_best = [
        strategy
        for strategy in eligible
        if int(candidate_validations[strategy.set_id].get("best_month") or 0) == target_month
        and float(candidate_validations[strategy.set_id].get("target_month_net") or 0.0) > 0
    ]
    individual_target_best = sorted(
        individual_target_best,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )

    seasonal = sorted(
        eligible,
        key=lambda item: _strict_monthly_candidate_score(
            item,
            full_by_id[item.set_id],
            target_month=target_month,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            min_trades_2020_2026=min_trades_2020_2026,
            enforce_point_dd=enforce_point_dd,
        ),
        reverse=True,
    )
    target_net = sorted(
        eligible,
        key=lambda item: (
            float(candidate_validations[item.set_id].get("target_month_net") or 0.0),
            score_set_for_portfolio(item, min_trades_2020_2026),
        ),
        reverse=True,
    )

    ordered_variant_sources: list[tuple[str, list[RobustStrategySet]]] = []
    if individual_target_best:
        ordered_variant_sources.append(("mejor_mes_individual", individual_target_best))
    ordered_variant_sources.extend(
        [
            ("estacionalidad", seasonal),
            ("net_mes_objetivo", target_net),
        ]
    )
    if not individual_target_best:
        ordered_variant_sources.append(("normal", normal))

    variants: list[tuple[str, list[RobustStrategySet]]] = []
    seen_signatures: set[tuple[str, ...]] = set()
    for label, ordered in ordered_variant_sources:
        limited = _limit_sorted_candidates_with_symbol_reserve(ordered, strict_limit)
        signature = tuple(strategy.set_id for strategy in limited)
        if not limited or signature in seen_signatures:
            continue
        variants.append((label, limited))
        seen_signatures.add(signature)
    return variants


def _strict_monthly_violation_score(validation: dict[str, object]) -> float:
    if bool(validation.get("passed")):
        return 0.0
    score = 0.0
    enforce_point_dd = bool(validation.get("enforce_point_dd", True))
    target_valley = float(validation.get("target_valley_dd") or 0.0)
    target_point = float(validation.get("target_point_dd") or 0.0)
    monthly_dd = validation.get("monthly_dd")
    if isinstance(monthly_dd, dict):
        for item in monthly_dd.values():
            if not isinstance(item, dict):
                continue
            score += max(float(item.get("valley_dd") or 0.0) - target_valley, 0.0) * 10.0
            if enforce_point_dd:
                score += max(float(item.get("point_dd") or 0.0) - target_point, 0.0) * 10.0
    yearly = validation.get("yearly")
    if isinstance(yearly, list):
        for item in yearly:
            if not isinstance(item, dict):
                continue
            if int(item.get("trades") or 0) <= 0:
                score += 1_000_000.0
            if float(item.get("net") or 0.0) <= 0.0:
                score += 1_000_000.0 + abs(float(item.get("net") or 0.0)) * 100.0
            score += max(float(item.get("valley_dd") or 0.0) - target_valley, 0.0) * 20.0
            if enforce_point_dd:
                score += max(float(item.get("point_dd") or 0.0) - target_point, 0.0) * 20.0
    best_month = int(validation.get("best_month") or 0)
    target_month = int(validation.get("target_month") or 0)
    if best_month != target_month:
        best_net = float(validation.get("best_month_net") or 0.0)
        target_net = float(validation.get("target_month_net") or 0.0)
        score += 1_000_000.0 + max(best_net - target_net, 0.0) * 100.0
    return score + len(validation.get("reasons") or []) * 1_000.0
