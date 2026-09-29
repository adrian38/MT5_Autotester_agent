"""Refinado profundo de las asignaciones ya construidas."""
from __future__ import annotations

from typing import Sequence

from .ubs_portfolio import (
    OptimizationDecision,
    PortfolioEvaluation,
    RobustStrategySet,
)
from .ubs_portfolio_constraints import (
    _allocations_respect_constraints,
    _target_group_units_pct_allowed,
    can_add_unit,
    violates_correlation_limits,
)
from .ubs_portfolio_evaluate import (
    _evaluation_violates_dd_limits,
    evaluate_portfolio,
)
from .ubs_portfolio_utils import (
    _portfolio_active_count,
    _portfolio_corr_allowed,
    score_set_for_portfolio,
)


def _deep_refine_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    minimum_active_strategies: int | None,
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
    max_sets_per_group: int | None,
    max_units_per_group_pct: float | None,
    group_unit_cap_bootstrap: int,
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
    margin_balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | None,
    stock_leverage: float,
    default_leverage: float,
    stock_contract_size: float,
    default_contract_size: float,
    max_daily_dd: float | None,
    enforce_point_dd: bool,
    daily_dd_full_history: bool,
    max_iterations: int = 160,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    working_sets = list({strategy.set_id: strategy for strategy in sets}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in working_sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0

    for iteration in range(1, max_iterations + 1):
        best_move: dict[str, object] | None = None
        ordered_targets = sorted(
            working_sets,
            key=lambda item: score_set_for_portfolio(item, max(int(allocations.get(item.set_id, 0)), 1)),
            reverse=True,
        )

        for target in ordered_targets:
            attempts += 1
            if can_add_unit(
                target_set=target,
                sets=working_sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_units_per_group_pct=max_units_per_group_pct,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                margin_balance=margin_balance,
                max_margin_pct=max_margin_pct,
                margin_profile=margin_profile,
                stock_leverage=stock_leverage,
                default_leverage=default_leverage,
                stock_contract_size=stock_contract_size,
                default_contract_size=default_contract_size,
            ):
                if allocations.get(target.set_id, 0) <= 0:
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        working_sets,
                        allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp_allocations = allocations.copy()
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                temp = evaluate_portfolio(
                    working_sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and not _evaluation_violates_dd_limits(temp)
                    and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_add_unit",
                        "from_set": None,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

            active_sources = [
                source for source in working_sets if allocations.get(source.set_id, 0) > 0
            ]
            for source in active_sources:
                if source.set_id == target.set_id:
                    continue
                attempts += 1
                temp_allocations = allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
                if (
                    minimum_active_strategies is not None
                    and _portfolio_active_count(temp_allocations) < minimum_active_strategies
                ):
                    continue
                if not _allocations_respect_constraints(
                    working_sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                    max_sets_per_group,
                    margin_balance,
                    max_margin_pct,
                    margin_profile,
                    stock_leverage,
                    default_leverage,
                    stock_contract_size,
                    default_contract_size,
                ):
                    continue
                if not _target_group_units_pct_allowed(
                    target,
                    working_sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected_by_corr, _reason = violates_correlation_limits(
                        target,
                        working_sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    working_sets,
                    temp_allocations,
                    current.target_valley_dd,
                    current.target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                gain = temp.total_net_profit - current.total_net_profit
                if (
                    gain > 1e-9
                    and not _evaluation_violates_dd_limits(temp)
                    and _portfolio_corr_allowed(temp, existing_portfolio_curves, max_portfolio_corr)
                    and (best_move is None or gain > float(best_move["gain"]))
                ):
                    best_move = {
                        "action": "deep_swap_unit",
                        "from_set": source,
                        "to_set": target,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        previous = current
        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert to_set is not None and isinstance(to_set, RobustStrategySet)
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action=str(best_move["action"]),
                set_id=to_set.set_id,
                from_set_id=from_set.set_id if isinstance(from_set, RobustStrategySet) else None,
                to_set_id=to_set.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_move["gain"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Optimizacion profunda: movimiento validado contra DD, margen y correlacion",
            )
        )

    return allocations, current, decision_log, attempts


