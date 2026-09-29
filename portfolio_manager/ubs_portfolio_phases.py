"""Fases de la optimizacion del portafolio discreto."""
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
from .ubs_portfolio_result import OptimizationRun, build_portfolio_result
from .ubs_portfolio_refine import (
    _deep_refine_allocations,
)
from .ubs_portfolio_search import (
    improve_with_local_search,
    improve_with_multi_start_search,
)
from .ubs_portfolio_utils import _execution_plan_allocations, group_limits_for_portfolio_type

def _prepare_candidate_pool(
    raw_sets, min_trades_2020_2026, top_k_per_symbol, max_total_candidates,
    max_units_per_group_pct, required_set_ids, required_initial_allocations,
    preserve_required_allocations, group_limits,
):
    """Filtra, recorta y completa el universo de candidatos de la optimizacion."""
    eligible = filter_eligible_sets(raw_sets, min_trades_2020_2026)
    if not eligible:
        raise ValueError("No eligible robust sets found")

    selected = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    base_selected_count = len(selected)
    required_ids = {str(set_id) for set_id in (required_set_ids or ())}
    required_ids.update(str(set_id) for set_id in (required_initial_allocations or {}))
    eligible_by_id = {strategy.set_id: strategy for strategy in eligible}
    missing_required = sorted(required_ids - set(eligible_by_id))
    if missing_required:
        raise ValueError(
            "Required portfolio sets are no longer eligible: "
            + ", ".join(Path(set_id).name for set_id in missing_required)
        )
    selected_ids = {strategy.set_id for strategy in selected}
    selected.extend(eligible_by_id[set_id] for set_id in required_ids - selected_ids)
    configured_group_units_pct = max_units_per_group_pct
    candidate_group_count = _candidate_group_count(selected)
    group_units_pct_feasibility_floor: float | None = None
    if max_units_per_group_pct is not None and candidate_group_count > 1:
        # A cap below 1/N is impossible when only N asset groups are in the
        # candidate pool (for example, 40% with Forex + Metals only). Use the
        # smallest feasible cap instead of leaving the greedy allocator stuck.
        group_units_pct_feasibility_floor = 1.0 / candidate_group_count
        max_units_per_group_pct = max(
            float(max_units_per_group_pct),
            group_units_pct_feasibility_floor,
        )
    initial_allocations = {
        set_id: max(int((required_initial_allocations or {}).get(set_id, 1)), 1)
        for set_id in required_ids
    }
    fixed_set_ids = required_ids if preserve_required_allocations else set()
    return (
        eligible, selected, base_selected_count, required_ids, eligible_by_id,
        configured_group_units_pct, candidate_group_count,
        group_units_pct_feasibility_floor, max_units_per_group_pct,
        initial_allocations, fixed_set_ids,
    )


def _greedy_and_local_search(
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
):
    """Construye la asignacion inicial y la mejora con la busqueda local."""
    allocations, current, greedy_log, stop_reason, correlation_rejections = build_portfolio_greedy(
        sets=selected,
        capital=capital,
        valley_dd_pct=effective_valley_dd_pct,
        point_dd_pct=effective_point_dd_pct,
        portfolio_type=portfolio_type,
        max_units_per_set=max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        max_pair_corr=max_pair_corr,
        max_downside_corr=max_downside_corr,
        max_dd_overlap=max_dd_overlap,
        existing_portfolio_curves=existing_portfolio_curves,
        max_portfolio_corr=max_portfolio_corr,
        max_units_per_group_pct=max_units_per_group_pct,
        max_sets_per_group=max_sets_per_group,
        group_unit_cap_bootstrap=group_unit_cap_bootstrap,
        initial_allocations=initial_allocations,
        minimum_active_strategies=minimum_active_strategies,
        maximum_active_strategies=maximum_active_strategies,
        fixed_set_ids=fixed_set_ids,
        allow_fixed_reductions_for_repair=preserve_required_allocations,
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

    local_log: list[OptimizationDecision] = []
    if run_local_search and not preserve_required_allocations:
        allocations, current, local_log = improve_with_local_search(
            sets=selected,
            allocations=allocations,
            current=current,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            protected_set_ids=required_ids,
            minimum_active_strategies=minimum_active_strategies,
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
    return (
        allocations, current, greedy_log, stop_reason, correlation_rejections, local_log,
    )


def _relax_group_cap(
    allocations, current, selected, capital, portfolio_type, target_valley_dd,
    target_point_dd, effective_valley_dd_pct, effective_point_dd_pct, group_limits,
    max_units_per_group_pct, max_sets_per_group, group_unit_cap_bootstrap,
    enforce_point_dd, max_daily_dd, daily_dd_full_history, initial_allocations,
    fixed_set_ids, required_ids, preserve_required_allocations, run_local_search,
    max_units_per_set, max_total_units, max_units_per_symbol, max_sets_per_symbol,
    max_pair_corr, max_downside_corr, max_dd_overlap, existing_portfolio_curves,
    max_portfolio_corr, margin_balance, max_margin_pct, margin_profile,
    stock_leverage, default_leverage, stock_contract_size, default_contract_size,
    minimum_active_strategies, maximum_active_strategies,
    correlation_rejections, stop_reason,
):
    """Reintenta Balanced sin cupo por grupo cuando el estricto desaprovecha DD."""
    group_cap_relaxed = False
    if (
        portfolio_type == PortfolioType.BALANCED
        and max_units_per_group_pct is not None
        and _candidate_group_count(selected) > 1
        and current.valley_usage_pct < 70
    ):
        (
            relaxed_allocations,
            relaxed_current,
            relaxed_greedy_log,
            relaxed_stop_reason,
            relaxed_rejections,
        ) = build_portfolio_greedy(
            sets=selected,
            capital=capital,
            valley_dd_pct=effective_valley_dd_pct,
            point_dd_pct=effective_point_dd_pct,
            portfolio_type=portfolio_type,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=None,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            initial_allocations=initial_allocations,
            minimum_active_strategies=minimum_active_strategies,
            maximum_active_strategies=maximum_active_strategies,
            fixed_set_ids=fixed_set_ids,
            allow_fixed_reductions_for_repair=preserve_required_allocations,
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
        relaxed_local_log: list[OptimizationDecision] = []
        if run_local_search and not preserve_required_allocations:
            relaxed_allocations, relaxed_current, relaxed_local_log = improve_with_local_search(
                sets=selected,
                allocations=relaxed_allocations,
                current=relaxed_current,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                max_pair_corr=max_pair_corr,
                max_downside_corr=max_downside_corr,
                max_dd_overlap=max_dd_overlap,
                existing_portfolio_curves=existing_portfolio_curves,
                max_portfolio_corr=max_portfolio_corr,
                max_units_per_group_pct=None,
                max_sets_per_group=max_sets_per_group,
                group_unit_cap_bootstrap=group_unit_cap_bootstrap,
                protected_set_ids=required_ids,
                minimum_active_strategies=minimum_active_strategies,
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
        if relaxed_current.total_net_profit > current.total_net_profit and relaxed_current.total_units > current.total_units:
            allocations = relaxed_allocations
            current = relaxed_current
            greedy_log = relaxed_greedy_log
            local_log = relaxed_local_log
            correlation_rejections = relaxed_rejections
            group_cap_relaxed = True
            stop_reason = f"{relaxed_stop_reason}; group unit cap relaxed after strict Balanced allocation underused DD"
    return allocations, current, group_cap_relaxed, correlation_rejections, stop_reason


def _search_phases(
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
):
    """Relaja el cupo por grupo en Balanced y prueba la busqueda multiarranque."""
    (
        allocations,
        current,
        group_cap_relaxed,
        correlation_rejections,
        stop_reason,
    ) = _relax_group_cap(
        allocations, current, selected, capital, portfolio_type, target_valley_dd,
        target_point_dd, effective_valley_dd_pct, effective_point_dd_pct, group_limits,
        max_units_per_group_pct, max_sets_per_group, group_unit_cap_bootstrap,
        enforce_point_dd, max_daily_dd, daily_dd_full_history, initial_allocations,
        fixed_set_ids, required_ids, preserve_required_allocations, run_local_search,
        max_units_per_set, max_total_units, max_units_per_symbol, max_sets_per_symbol,
        max_pair_corr, max_downside_corr, max_dd_overlap, existing_portfolio_curves,
        max_portfolio_corr, margin_balance, max_margin_pct, margin_profile,
        stock_leverage, default_leverage, stock_contract_size, default_contract_size,
        minimum_active_strategies, maximum_active_strategies,
        correlation_rejections, stop_reason,
    )

    multi_start_log: list[OptimizationDecision] = []
    valid_restarts = 0
    if search_restarts > 0 and not preserve_required_allocations:
        allocations, current, multi_start_log, valid_restarts = improve_with_multi_start_search(
            sets=selected,
            allocations=allocations,
            current=current,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            restarts=int(search_restarts),
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
            max_units_per_group_pct=None if group_cap_relaxed else max_units_per_group_pct,
            max_sets_per_group=max_sets_per_group,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
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
    if multi_start_log:
        stop_reason += "; multi-start search improved the local solution"
    return (
        allocations, current, group_cap_relaxed, multi_start_log,
        valid_restarts, correlation_rejections, stop_reason,
    )


def _deep_refinement_phase(
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
):
    """Refinado profundo con el universo ampliado si hace falta."""
    deep_log: list[OptimizationDecision] = []
    deep_attempts = 0
    deep_pool_expanded = False
    deep_pool_count = len(selected)
    if use_deep_refinement and not preserve_required_allocations:
        deep_top_k = max(int(top_k_per_symbol), min(20, int(top_k_per_symbol) * 2))
        if max_total_candidates is None:
            deep_max_candidates = None
        else:
            deep_max_candidates = min(len(eligible), max(int(max_total_candidates), int(max_total_candidates) * 2))
        deep_selected = select_top_k_per_symbol(
            eligible,
            top_k_per_symbol=deep_top_k,
            max_total_candidates=deep_max_candidates,
            min_trades_2020_2026=min_trades_2020_2026,
        )
        deep_selected_by_id = {strategy.set_id: strategy for strategy in deep_selected}
        for set_id in required_ids:
            if set_id in eligible_by_id:
                deep_selected_by_id.setdefault(set_id, eligible_by_id[set_id])
        for strategy in selected:
            deep_selected_by_id.setdefault(strategy.set_id, strategy)
        deep_selected = list(deep_selected_by_id.values())
        deep_pool_count = len(deep_selected)
        deep_pool_expanded = len(deep_selected) > len(selected)
        refined_allocations, refined_current, deep_log, deep_attempts = _deep_refine_allocations(
            deep_selected,
            allocations,
            current,
            minimum_active_strategies=minimum_active_strategies,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_sets_per_group=max_sets_per_group,
            max_units_per_group_pct=None if group_cap_relaxed else max_units_per_group_pct,
            group_unit_cap_bootstrap=group_unit_cap_bootstrap,
            max_pair_corr=max_pair_corr,
            max_downside_corr=max_downside_corr,
            max_dd_overlap=max_dd_overlap,
            existing_portfolio_curves=existing_portfolio_curves,
            max_portfolio_corr=max_portfolio_corr,
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
        if refined_current.total_net_profit > current.total_net_profit + 1e-9:
            selected = deep_selected
            allocations = refined_allocations
            current = refined_current
            stop_reason += "; deep optimization refined the solution"
    return (
        allocations, current, deep_log, deep_attempts,
        deep_pool_expanded, deep_pool_count, selected, stop_reason,
    )


def _check_execution_plan(
    selected, allocations, current, capital,
    target_valley_dd, target_point_dd, enforce_point_dd, max_daily_dd,
    daily_dd_full_history,
):
    """Ajusta las unidades al paso de lote y comprueba los limites finales."""
    executable_allocations, executable_steps = _execution_plan_allocations(selected, allocations, capital)
    execution_adjustments = {
        set_id: executable_allocations[set_id]
        for set_id, units in allocations.items()
        if units > 0 and executable_allocations.get(set_id, 0) != units
    }
    if execution_adjustments:
        current = evaluate_portfolio(
            selected,
            executable_allocations,
            target_valley_dd,
            target_point_dd,
            max_daily_dd,
            enforce_point_dd,
            daily_dd_full_history,
        )
        allocations = executable_allocations

    if current.valley_dd > target_valley_dd:
        raise ValueError("Final portfolio violates valley DD")
    if enforce_point_dd and current.point_dd > target_point_dd:
        raise ValueError("Final portfolio violates point DD")
    if max_daily_dd is not None and current.daily_dd > float(max_daily_dd) + 1e-9:
        raise ValueError("Final portfolio violates daily DD")
    return allocations, current, executable_steps, execution_adjustments
