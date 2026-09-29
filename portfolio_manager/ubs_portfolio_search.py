"""Busqueda local y multiarranque sobre una asignacion inicial."""
from __future__ import annotations

import random
from typing import Sequence

from .ubs_portfolio import (
    OptimizationDecision,
    PortfolioEvaluation,
    RobustStrategySet,
)
from .ubs_portfolio_constraints import (
    _allocations_respect_constraints,
    _target_group_units_pct_allowed,
    violates_correlation_limits,
)
from .ubs_portfolio_curves import (
    curve_increment_correlation,
)
from .ubs_portfolio_evaluate import (
    _evaluation_violates_dd_limits,
    evaluate_portfolio,
)


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    max_iterations: int = 1000,
    protected_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    decision_log: list[OptimizationDecision] = []
    iteration = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    protected_ids = {str(set_id) for set_id in (protected_set_ids or ())}
    while iteration < max_iterations:
        iteration += 1
        best_move: dict[str, object] | None = None
        for from_set in sets:
            if allocations.get(from_set.set_id, 0) <= 0:
                continue
            if from_set.set_id in protected_ids and allocations.get(from_set.set_id, 0) <= 1:
                continue
            for to_set in sets:
                if from_set.set_id == to_set.set_id:
                    continue
                temp_allocations = allocations.copy()
                temp_allocations[from_set.set_id] -= 1
                temp_allocations[to_set.set_id] += 1
                if minimum_active_strategies is not None:
                    active_count = sum(1 for units in temp_allocations.values() if units > 0)
                    if active_count < minimum_active_strategies:
                        continue
                if not _allocations_respect_constraints(
                    sets,
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
                    to_set,
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if allocations.get(to_set.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[to_set.set_id] = 0
                    rejected_by_corr, _corr_reason = violates_correlation_limits(
                        to_set,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    target_valley_dd,
                    target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    worst_portfolio_corr = max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    )
                    if worst_portfolio_corr > max_portfolio_corr:
                        continue
                gain = temp.total_net_profit - current.total_net_profit
                if gain <= 0:
                    continue
                if best_move is None or gain > float(best_move["gain"]):
                    best_move = {
                        "from_set": from_set,
                        "to_set": to_set,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert isinstance(from_set, RobustStrategySet)
        assert isinstance(to_set, RobustStrategySet)
        previous = current
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action="swap_unit",
                set_id=None,
                from_set_id=from_set.set_id,
                to_set_id=to_set.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=current.total_net_profit - previous.total_net_profit,
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Local search improved total net profit",
            )
        )
    return allocations, current, decision_log


def improve_with_multi_start_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    *,
    restarts: int,
    perturbations: int = 2,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    if restarts <= 0 or perturbations <= 0 or len(sets) < 2:
        return allocations, current, [], 0

    best_allocations = allocations.copy()
    best = current
    best_log: list[OptimizationDecision] = []
    valid_restarts = 0
    portfolio_curves = list(existing_portfolio_curves or [])

    for restart in range(restarts):
        rng = random.Random(104729 + restart * 7919 + len(sets) * 17)
        trial_allocations = allocations.copy()
        trial = current
        perturb_log: list[OptimizationDecision] = []

        for perturbation in range(perturbations):
            active = [item for item in sets if trial_allocations.get(item.set_id, 0) > 0]
            targets = list(sets)
            moves = [(source, target) for source in active for target in targets if source.set_id != target.set_id]
            rng.shuffle(moves)
            accepted_move = False
            for source, target in moves:
                temp_allocations = trial_allocations.copy()
                temp_allocations[source.set_id] -= 1
                temp_allocations[target.set_id] += 1
                if not _allocations_respect_constraints(
                    sets,
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
                    sets,
                    temp_allocations,
                    max_units_per_group_pct,
                    group_unit_cap_bootstrap,
                ):
                    continue
                if trial_allocations.get(target.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[target.set_id] = 0
                    rejected, _reason = violates_correlation_limits(
                        target,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected:
                        continue
                temp = evaluate_portfolio(
                    sets,
                    temp_allocations,
                    target_valley_dd,
                    target_point_dd,
                    max_daily_dd,
                    enforce_point_dd,
                    daily_dd_full_history,
                )
                if _evaluation_violates_dd_limits(temp):
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    if max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    ) > max_portfolio_corr:
                        continue
                perturb_log.append(
                    OptimizationDecision(
                        step=perturbation + 1,
                        action="multi_start_perturb",
                        set_id=None,
                        from_set_id=source.set_id,
                        to_set_id=target.set_id,
                        gain=temp.total_net_profit - trial.total_net_profit,
                        valley_cost=temp.valley_dd - trial.valley_dd,
                        point_cost=temp.point_dd - trial.point_dd,
                        score=temp.total_net_profit - trial.total_net_profit,
                        portfolio_net_profit_after=temp.total_net_profit,
                        portfolio_valley_dd_after=temp.valley_dd,
                        portfolio_point_dd_after=temp.point_dd,
                        reason=f"Multi-start perturbation {restart + 1}",
                    )
                )
                trial_allocations = temp_allocations
                trial = temp
                accepted_move = True
                break
            if not accepted_move:
                break

        if not perturb_log:
            continue
        valid_restarts += 1
        trial_allocations, trial, local_log = improve_with_local_search(
            sets=sets,
            allocations=trial_allocations,
            current=trial,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            max_iterations=200,
            margin_balance=margin_balance,
            max_margin_pct=max_margin_pct,
            margin_profile=margin_profile,
            stock_leverage=stock_leverage,
            default_leverage=default_leverage,
            stock_contract_size=stock_contract_size,
            default_contract_size=default_contract_size,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        if trial.total_net_profit > best.total_net_profit + 1e-9:
            best_allocations = trial_allocations
            best = trial
            best_log = perturb_log + local_log

    return best_allocations, best, best_log, valid_restarts
