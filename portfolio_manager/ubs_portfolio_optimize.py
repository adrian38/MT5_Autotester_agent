"""Optimizacion del portafolio discreto con restriccion de drawdown."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .ubs_portfolio import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_BOOTSTRAP_SIMULATIONS,
    OptimizationDecision,
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
)
from .ubs_portfolio_candidates import (
    filter_eligible_sets,
    select_top_k_per_symbol,
)
from .ubs_portfolio_constraints import (
    _candidate_group_count,
)
from .ubs_portfolio_evaluate import evaluate_portfolio
from .ubs_portfolio_greedy import (
    build_portfolio_greedy,
)
from .ubs_portfolio_phases import (
    _check_execution_plan,
    _deep_refinement_phase,
    _greedy_and_local_search,
    _prepare_candidate_pool,
    _search_phases,
)
from .ubs_portfolio_result import OptimizationRun, build_portfolio_result
from .ubs_portfolio_refine import (
    _deep_refine_allocations,
)
from .ubs_portfolio_search import (
    improve_with_local_search,
    improve_with_multi_start_search,
)
from .ubs_portfolio_utils import _execution_plan_allocations, group_limits_for_portfolio_type


def optimize_portfolio(
    raw_sets: list[RobustStrategySet],
    capital: float,
    valley_dd_pct: float,
    point_dd_pct: float,
    portfolio_type: PortfolioType = PortfolioType.BALANCED,
    min_trades_2020_2026: int = 100,
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = 1,
    run_local_search: bool = True,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_units_per_group_pct: float | None = None,
    max_sets_per_group: int | None = None,
    group_unit_cap_bootstrap: int | None = None,
    required_set_ids: Sequence[str] | None = None,
    minimum_active_strategies: int | None = None,
    maximum_active_strategies: int | None = None,
    required_initial_allocations: dict[str, int] | None = None,
    preserve_required_allocations: bool = False,
    dd_reserve_pct: float = 0.0,
    search_restarts: int = 0,
    bootstrap_simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS,
    bootstrap_block_size: int | None = None,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
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
    use_deep_refinement: bool = False,
) -> PortfolioResult:
    reserve_factor = 1.0 - min(max(float(dd_reserve_pct), 0.0), 99.0) / 100.0
    effective_valley_dd_pct = valley_dd_pct * reserve_factor
    effective_point_dd_pct = point_dd_pct * reserve_factor
    target_valley_dd = capital * effective_valley_dd_pct / 100.0
    target_point_dd = capital * effective_point_dd_pct / 100.0
    group_limits = group_limits_for_portfolio_type(portfolio_type)
    if max_units_per_group_pct is None:
        max_units_per_group_pct = group_limits.max_units_pct
    if max_sets_per_group is None:
        max_sets_per_group = group_limits.max_sets
    if group_unit_cap_bootstrap is None:
        group_unit_cap_bootstrap = group_limits.bootstrap_units
    (
        eligible,
        selected,
        base_selected_count,
        required_ids,
        eligible_by_id,
        configured_group_units_pct,
        candidate_group_count,
        group_units_pct_feasibility_floor,
        max_units_per_group_pct,
        initial_allocations,
        fixed_set_ids,
    ) = _prepare_candidate_pool(
        raw_sets, min_trades_2020_2026, top_k_per_symbol, max_total_candidates,
        max_units_per_group_pct, required_set_ids, required_initial_allocations,
        preserve_required_allocations, group_limits,
    )
    (
        allocations,
        current,
        greedy_log,
        stop_reason,
        correlation_rejections,
        local_log,
    ) = _greedy_and_local_search(
        selected, capital, portfolio_type, target_valley_dd, target_point_dd,
        effective_valley_dd_pct, effective_point_dd_pct, group_limits,
        max_units_per_group_pct, max_sets_per_group, group_unit_cap_bootstrap,
        enforce_point_dd, max_daily_dd, daily_dd_full_history, initial_allocations,
        fixed_set_ids, required_ids, preserve_required_allocations, run_local_search,
        max_units_per_set, max_total_units, max_units_per_symbol, max_sets_per_symbol,
        max_pair_corr, max_downside_corr, max_dd_overlap, existing_portfolio_curves,
        max_portfolio_corr, margin_balance, max_margin_pct, margin_profile,
        stock_leverage, default_leverage, stock_contract_size, default_contract_size,
        minimum_active_strategies, maximum_active_strategies,
    )

    (
        allocations,
        current,
        group_cap_relaxed,
        multi_start_log,
        valid_restarts,
        correlation_rejections,
        stop_reason,
    ) = _search_phases(
        allocations, current, selected, capital, portfolio_type, target_valley_dd,
        target_point_dd, group_limits, max_units_per_group_pct, max_sets_per_group,
        group_unit_cap_bootstrap, enforce_point_dd, max_daily_dd, daily_dd_full_history,
        preserve_required_allocations, fixed_set_ids, max_units_per_set, max_total_units,
        max_units_per_symbol, max_sets_per_symbol, max_pair_corr, max_downside_corr,
        max_dd_overlap, existing_portfolio_curves, max_portfolio_corr, margin_balance,
        max_margin_pct, margin_profile, stock_leverage, default_leverage,
        stock_contract_size, default_contract_size, minimum_active_strategies,
        maximum_active_strategies, search_restarts, correlation_rejections, stop_reason,
        effective_valley_dd_pct, effective_point_dd_pct, initial_allocations,
        required_ids, run_local_search,
    )

    (
        allocations,
        current,
        deep_log,
        deep_attempts,
        deep_pool_expanded,
        deep_pool_count,
        selected,
        stop_reason,
    ) = _deep_refinement_phase(
        allocations, current, selected, eligible, capital, portfolio_type,
        target_valley_dd, target_point_dd, group_limits, max_units_per_group_pct,
        max_sets_per_group, enforce_point_dd, max_daily_dd, daily_dd_full_history,
        use_deep_refinement, preserve_required_allocations, fixed_set_ids,
        top_k_per_symbol, min_trades_2020_2026, max_units_per_set, max_total_units,
        max_units_per_symbol, max_sets_per_symbol, max_pair_corr,
        max_downside_corr, max_dd_overlap, max_portfolio_corr,
        margin_balance, max_margin_pct, margin_profile, stock_leverage,
        default_leverage, stock_contract_size, default_contract_size,
        eligible_by_id, existing_portfolio_curves, group_cap_relaxed,
        group_unit_cap_bootstrap, max_total_candidates, minimum_active_strategies,
        required_ids, stop_reason,
    )

    allocations, current, executable_steps, execution_adjustments = _check_execution_plan(
        selected, allocations, current, capital,
        target_valley_dd, target_point_dd, enforce_point_dd, max_daily_dd,
        daily_dd_full_history,
    )

    run = OptimizationRun(
        raw_sets=raw_sets,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        portfolio_type=portfolio_type,
        min_trades_2020_2026=min_trades_2020_2026,
        max_units_per_group_pct=max_units_per_group_pct,
        preserve_required_allocations=preserve_required_allocations,
        dd_reserve_pct=dd_reserve_pct,
        search_restarts=search_restarts,
        bootstrap_simulations=bootstrap_simulations,
        bootstrap_block_size=bootstrap_block_size,
        bootstrap_seed=bootstrap_seed,
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
        use_deep_refinement=use_deep_refinement,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        eligible=eligible,
        selected=selected,
        base_selected_count=base_selected_count,
        required_ids=required_ids,
        configured_group_units_pct=configured_group_units_pct,
        candidate_group_count=candidate_group_count,
        group_units_pct_feasibility_floor=group_units_pct_feasibility_floor,
        allocations=allocations,
        current=current,
        greedy_log=greedy_log,
        stop_reason=stop_reason,
        correlation_rejections=correlation_rejections,
        local_log=local_log,
        group_cap_relaxed=group_cap_relaxed,
        multi_start_log=multi_start_log,
        valid_restarts=valid_restarts,
        deep_log=deep_log,
        deep_attempts=deep_attempts,
        deep_pool_expanded=deep_pool_expanded,
        deep_pool_count=deep_pool_count,
        executable_steps=executable_steps,
        execution_adjustments=execution_adjustments,
    )
    return build_portfolio_result(run)
