"""Construccion voraz de la asignacion inicial del portafolio."""
from __future__ import annotations

from typing import Sequence

from .ubs_portfolio import (
    OptimizationDecision,
    PortfolioEvaluation,
    PortfolioType,
    RobustStrategySet,
)
from .ubs_portfolio_constraints import (
    _allocations_respect_constraints,
    can_add_unit,
    score_increment,
    violates_correlation_limits,
)
from .ubs_portfolio_curves import (
    curve_increment_correlation,
)
from .ubs_portfolio_evaluate import (
    _evaluation_violates_dd_limits,
    _evaluation_violation_ratio,
    evaluate_portfolio,
)


def build_portfolio_greedy(
    sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int = 10,
    initial_allocations: dict[str, int] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    fixed_set_ids: Sequence[str] | None = None,
    allow_fixed_reductions_for_repair: bool = False,
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
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
    target_valley_dd = capital * valley_dd_pct / 100.0
    target_point_dd = capital * point_dd_pct / 100.0
    allocations = {
        strategy.set_id: max(int((initial_allocations or {}).get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    if not _allocations_respect_constraints(
        sets,
        allocations,
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
        raise ValueError("Initial portfolio allocations violate configured limits")
    current = evaluate_portfolio(
        sets,
        allocations,
        target_valley_dd,
        target_point_dd,
        max_daily_dd,
        enforce_point_dd,
        daily_dd_full_history,
    )
    if _evaluation_violates_dd_limits(current) and not allow_fixed_reductions_for_repair:
        raise ValueError("Initial portfolio allocations violate DD limits")
    decision_log: list[OptimizationDecision] = []
    step = sum(allocations.values())
    max_steps = max_total_units if max_total_units is not None else 10000
    correlation_rejections = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    fixed_ids = {str(set_id) for set_id in (fixed_set_ids or ())}

    while step < max_steps:
        best_candidate: dict[str, object] | None = None
        best_repair_candidate: dict[str, object] | None = None
        blocked_by_risk = False
        for strategy in sets:
            if strategy.set_id in fixed_ids:
                continue
            if (
                maximum_active_strategies is not None
                and current.active_strategies >= maximum_active_strategies
                and allocations.get(strategy.set_id, 0) <= 0
            ):
                continue
            if (
                minimum_active_strategies is not None
                and current.active_strategies < minimum_active_strategies
                and allocations.get(strategy.set_id, 0) > 0
            ):
                # During repair, fill the missing strategy slots before adding
                # more risk to strategies that are already active.
                continue
            if not can_add_unit(
                target_set=strategy,
                sets=sets,
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
                continue
            rejected_by_corr, corr_reason = violates_correlation_limits(
                strategy,
                sets,
                allocations,
                max_pair_corr,
                max_downside_corr,
                max_dd_overlap,
            )
            if rejected_by_corr:
                correlation_rejections += 1
                decision_log.append(
                    OptimizationDecision(
                        step=step + 1,
                        action="reject_corr",
                        set_id=strategy.set_id,
                        from_set_id=None,
                        to_set_id=None,
                        gain=0.0,
                        valley_cost=0.0,
                        point_cost=0.0,
                        score=float("-inf"),
                        portfolio_net_profit_after=current.total_net_profit,
                        portfolio_valley_dd_after=current.valley_dd,
                        portfolio_point_dd_after=current.point_dd,
                        reason=corr_reason,
                    )
                )
                continue
            temp_allocations = allocations.copy()
            temp_allocations[strategy.set_id] += 1
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
                blocked_by_risk = True
                if allow_fixed_reductions_for_repair:
                    current_violation = _evaluation_violation_ratio(current)
                    temp_violation = _evaluation_violation_ratio(temp)
                    if temp_violation < current_violation - 1e-9:
                        repair_score = (current_violation - temp_violation) * 1_000_000_000.0
                        repair_score += max(temp.total_net_profit - current.total_net_profit, 0.0)
                        if (
                            best_repair_candidate is None
                            or repair_score > float(best_repair_candidate["score"])
                        ):
                            best_repair_candidate = {
                                "set": strategy,
                                "allocations": temp_allocations,
                                "evaluation": temp,
                                "score": repair_score,
                                "reason": "Replacement increment reduced the DD violation",
                            }
                continue
            if max_portfolio_corr is not None and portfolio_curves:
                worst_portfolio_corr = max(
                    curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                    for curve in portfolio_curves
                )
                if worst_portfolio_corr > max_portfolio_corr:
                    blocked_by_risk = True
                    correlation_rejections += 1
                    decision_log.append(
                        OptimizationDecision(
                            step=step + 1,
                            action="reject_portfolio_corr",
                            set_id=strategy.set_id,
                            from_set_id=None,
                            to_set_id=None,
                            gain=0.0,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=float("-inf"),
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason=f"portfolio_corr>{max_portfolio_corr:.2f}",
                        )
                    )
                    continue
            score = score_increment(current, temp, allocations[strategy.set_id], portfolio_type)
            if score == float("-inf"):
                continue
            if best_candidate is None or score > float(best_candidate["score"]):
                best_candidate = {
                    "set": strategy,
                    "allocations": temp_allocations,
                    "evaluation": temp,
                    "score": score,
                    "reason": "Best valid +0.01 increment",
                }

        if best_candidate is None and best_repair_candidate is not None:
            best_candidate = best_repair_candidate

        if best_candidate is None and allow_fixed_reductions_for_repair:
            current_violation = _evaluation_violation_ratio(current)
            missing_required_strategy = (
                minimum_active_strategies is not None
                and current.active_strategies < minimum_active_strategies
            )
            if current_violation > 1.0 or (missing_required_strategy and blocked_by_risk):
                best_reduction: tuple[float, float, RobustStrategySet, dict[str, int], PortfolioEvaluation] | None = None
                for strategy in sets:
                    if strategy.set_id not in fixed_ids or allocations.get(strategy.set_id, 0) <= 1:
                        continue
                    temp_allocations = allocations.copy()
                    temp_allocations[strategy.set_id] -= 1
                    temp = evaluate_portfolio(
                        sets,
                        temp_allocations,
                        target_valley_dd,
                        target_point_dd,
                        max_daily_dd,
                        enforce_point_dd,
                        daily_dd_full_history,
                    )
                    temp_violation = _evaluation_violation_ratio(temp)
                    if temp_violation >= current_violation - 1e-9:
                        continue
                    choice = (temp_violation, -temp.total_net_profit, strategy, temp_allocations, temp)
                    if best_reduction is None or choice[:2] < best_reduction[:2]:
                        best_reduction = choice
                if best_reduction is not None:
                    _violation, _negative_net, reduced_set, allocations, current = best_reduction
                    step = sum(allocations.values())
                    decision_log.append(
                        OptimizationDecision(
                            step=len(decision_log) + 1,
                            action="reduce_unit_for_repair",
                            set_id=reduced_set.set_id,
                            from_set_id=reduced_set.set_id,
                            to_set_id=None,
                            gain=-reduced_set.net_profit_2020_2026_001,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=-current_violation,
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason="Minimum existing-lot reduction required to make portfolio repair feasible",
                        )
                    )
                    continue

        if best_candidate is None:
            stop_reason = "No valid +0.01 increment found without breaking DD constraints"
            break

        selected_set = best_candidate["set"]
        assert isinstance(selected_set, RobustStrategySet)
        previous = current
        allocations = best_candidate["allocations"]  # type: ignore[assignment]
        current = best_candidate["evaluation"]  # type: ignore[assignment]
        step += 1
        decision_log.append(
            OptimizationDecision(
                step=step,
                action="add_unit",
                set_id=selected_set.set_id,
                from_set_id=None,
                to_set_id=None,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_candidate["score"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason=str(best_candidate.get("reason") or "Best valid +0.01 increment"),
            )
        )
    else:
        stop_reason = "Max optimizer iterations reached"

    return allocations, current, decision_log, stop_reason, correlation_rejections
