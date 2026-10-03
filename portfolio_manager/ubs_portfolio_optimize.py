"""Optimizacion del portafolio discreto con restriccion de drawdown."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _OptimizeParams:
    """Todos los parametros con los que se pide una optimizacion."""

    raw_sets: list[RobustStrategySet]
    capital: float
    valley_dd_pct: float
    point_dd_pct: float
    portfolio_type: PortfolioType = PortfolioType.BALANCED
    min_trades_2020_2026: int = 100
    top_k_per_symbol: int = 3
    max_total_candidates: int | None = 30
    max_units_per_set: int | None = None
    max_total_units: int | None = None
    max_units_per_symbol: int | None = None
    max_sets_per_symbol: int | None = 1
    run_local_search: bool = True
    max_pair_corr: float | None = None
    max_downside_corr: float | None = None
    max_dd_overlap: float | None = None
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None
    max_portfolio_corr: float | None = None
    max_units_per_group_pct: float | None = None
    max_sets_per_group: int | None = None
    group_unit_cap_bootstrap: int | None = None
    required_set_ids: Sequence[str] | None = None
    minimum_active_strategies: int | None = None
    maximum_active_strategies: int | None = None
    required_initial_allocations: dict[str, int] | None = None
    preserve_required_allocations: bool = False
    dd_reserve_pct: float = 0.0
    search_restarts: int = 0
    bootstrap_simulations: int = DEFAULT_BOOTSTRAP_SIMULATIONS
    bootstrap_block_size: int | None = None
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED
    margin_balance: float | None = None
    max_margin_pct: float | None = None
    margin_profile: str | None = "roboforex"
    stock_leverage: float = 20.0
    default_leverage: float = 500.0
    stock_contract_size: float = 100.0
    default_contract_size: float = 1.0
    max_daily_dd: float | None = None
    enforce_point_dd: bool = True
    daily_dd_full_history: bool = False
    use_deep_refinement: bool = False


@dataclass
class _OptimizeTargets:
    """Objetivos de DD y cupos de grupo ya resueltos para esta ejecucion."""

    effective_valley_dd_pct: float
    effective_point_dd_pct: float
    target_valley_dd: float
    target_point_dd: float
    group_limits: object
    max_units_per_group_pct: float | None
    max_sets_per_group: int | None
    group_unit_cap_bootstrap: int | None


def _resolve_targets(params: _OptimizeParams) -> _OptimizeTargets:
    """Aplica la reserva de DD y completa los cupos de grupo por tipo."""
    reserve_factor = 1.0 - min(max(float(params.dd_reserve_pct), 0.0), 99.0) / 100.0
    effective_valley_dd_pct = params.valley_dd_pct * reserve_factor
    effective_point_dd_pct = params.point_dd_pct * reserve_factor
    group_limits = group_limits_for_portfolio_type(params.portfolio_type)
    return _OptimizeTargets(
        effective_valley_dd_pct=effective_valley_dd_pct,
        effective_point_dd_pct=effective_point_dd_pct,
        target_valley_dd=params.capital * effective_valley_dd_pct / 100.0,
        target_point_dd=params.capital * effective_point_dd_pct / 100.0,
        group_limits=group_limits,
        max_units_per_group_pct=(
            group_limits.max_units_pct if params.max_units_per_group_pct is None
            else params.max_units_per_group_pct
        ),
        max_sets_per_group=(
            group_limits.max_sets if params.max_sets_per_group is None else params.max_sets_per_group
        ),
        group_unit_cap_bootstrap=(
            group_limits.bootstrap_units if params.group_unit_cap_bootstrap is None
            else params.group_unit_cap_bootstrap
        ),
    )


@dataclass
class _CandidatePool:
    """Candidatos elegibles y seleccionados, con los cupos que de ahi salen."""

    eligible: list
    selected: list
    base_selected_count: int
    required_ids: object
    eligible_by_id: dict
    configured_group_units_pct: object
    candidate_group_count: int
    group_units_pct_feasibility_floor: object
    max_units_per_group_pct: float | None
    initial_allocations: dict[str, int] | None
    fixed_set_ids: object

    def run_fields(self) -> dict[str, object]:
        """Lo que de aqui viaja al informe de la optimizacion."""
        return {
            name: getattr(self, name) for name in (
                "eligible", "selected", "base_selected_count", "required_ids",
                "configured_group_units_pct", "candidate_group_count",
                "group_units_pct_feasibility_floor", "max_units_per_group_pct",
            )
        }


def _prepare_pool(params: _OptimizeParams, targets: _OptimizeTargets) -> _CandidatePool:
    """Selecciona los candidatos y ajusta el cupo de unidades por grupo."""
    pool = _CandidatePool(*_prepare_candidate_pool(
        params.raw_sets, params.min_trades_2020_2026, params.top_k_per_symbol,
        params.max_total_candidates, targets.max_units_per_group_pct, params.required_set_ids,
        params.required_initial_allocations, params.preserve_required_allocations,
        targets.group_limits,
    ))
    targets.max_units_per_group_pct = pool.max_units_per_group_pct
    return pool


def _greedy_phase(
    params: _OptimizeParams, targets: _OptimizeTargets, pool: _CandidatePool
) -> dict[str, object]:
    """Construccion voraz mas la busqueda local inicial."""
    names = (
        "allocations", "current", "greedy_log", "stop_reason", "correlation_rejections", "local_log",
    )
    return dict(zip(names, _greedy_and_local_search(
        pool.selected, params.capital, params.portfolio_type, targets.target_valley_dd,
        targets.target_point_dd, targets.effective_valley_dd_pct, targets.effective_point_dd_pct,
        targets.group_limits, targets.max_units_per_group_pct, targets.max_sets_per_group,
        targets.group_unit_cap_bootstrap, params.enforce_point_dd, params.max_daily_dd,
        params.daily_dd_full_history, pool.initial_allocations, pool.fixed_set_ids,
        pool.required_ids, params.preserve_required_allocations, params.run_local_search,
        params.max_units_per_set, params.max_total_units, params.max_units_per_symbol,
        params.max_sets_per_symbol, params.max_pair_corr, params.max_downside_corr,
        params.max_dd_overlap, params.existing_portfolio_curves, params.max_portfolio_corr,
        params.margin_balance, params.max_margin_pct, params.margin_profile,
        params.stock_leverage, params.default_leverage, params.stock_contract_size,
        params.default_contract_size, params.minimum_active_strategies,
        params.maximum_active_strategies,
    )))


def _search_phase(
    params: _OptimizeParams, targets: _OptimizeTargets, pool: _CandidatePool, state: dict[str, object]
) -> dict[str, object]:
    """Reinicios multiples y relajacion del cupo de grupo."""
    names = (
        "allocations", "current", "group_cap_relaxed", "multi_start_log", "valid_restarts",
        "correlation_rejections", "stop_reason",
    )
    return dict(zip(names, _search_phases(
        state["allocations"], state["current"], pool.selected, params.capital,
        params.portfolio_type, targets.target_valley_dd, targets.target_point_dd,
        targets.group_limits, targets.max_units_per_group_pct, targets.max_sets_per_group,
        targets.group_unit_cap_bootstrap, params.enforce_point_dd, params.max_daily_dd,
        params.daily_dd_full_history, params.preserve_required_allocations, pool.fixed_set_ids,
        params.max_units_per_set, params.max_total_units, params.max_units_per_symbol,
        params.max_sets_per_symbol, params.max_pair_corr, params.max_downside_corr,
        params.max_dd_overlap, params.existing_portfolio_curves, params.max_portfolio_corr,
        params.margin_balance, params.max_margin_pct, params.margin_profile,
        params.stock_leverage, params.default_leverage, params.stock_contract_size,
        params.default_contract_size, params.minimum_active_strategies,
        params.maximum_active_strategies, params.search_restarts,
        state["correlation_rejections"], state["stop_reason"], targets.effective_valley_dd_pct,
        targets.effective_point_dd_pct, pool.initial_allocations, pool.required_ids,
        params.run_local_search,
    )))


def _deep_phase(
    params: _OptimizeParams, targets: _OptimizeTargets, pool: _CandidatePool, state: dict[str, object]
) -> dict[str, object]:
    """Refinado profundo sobre un universo de candidatos ampliado."""
    names = (
        "allocations", "current", "deep_log", "deep_attempts", "deep_pool_expanded",
        "deep_pool_count", "selected", "stop_reason",
    )
    return dict(zip(names, _deep_refinement_phase(
        state["allocations"], state["current"], pool.selected, pool.eligible, params.capital,
        params.portfolio_type, targets.target_valley_dd, targets.target_point_dd,
        targets.group_limits, targets.max_units_per_group_pct, targets.max_sets_per_group,
        params.enforce_point_dd, params.max_daily_dd, params.daily_dd_full_history,
        params.use_deep_refinement, params.preserve_required_allocations, pool.fixed_set_ids,
        params.top_k_per_symbol, params.min_trades_2020_2026, params.max_units_per_set,
        params.max_total_units, params.max_units_per_symbol, params.max_sets_per_symbol,
        params.max_pair_corr, params.max_downside_corr, params.max_dd_overlap,
        params.max_portfolio_corr, params.margin_balance, params.max_margin_pct,
        params.margin_profile, params.stock_leverage, params.default_leverage,
        params.stock_contract_size, params.default_contract_size, pool.eligible_by_id,
        params.existing_portfolio_curves, state["group_cap_relaxed"],
        targets.group_unit_cap_bootstrap, params.max_total_candidates,
        params.minimum_active_strategies, pool.required_ids, state["stop_reason"],
    )))


def _execution_phase(
    params: _OptimizeParams, targets: _OptimizeTargets, state: dict[str, object]
) -> dict[str, object]:
    """Comprueba que el plan de ejecucion por pasos siga dentro de los limites."""
    names = ("allocations", "current", "executable_steps", "execution_adjustments")
    return dict(zip(names, _check_execution_plan(
        state["selected"], state["allocations"], state["current"], params.capital,
        targets.target_valley_dd, targets.target_point_dd, params.enforce_point_dd,
        params.max_daily_dd, params.daily_dd_full_history,
    )))


def _run_params(params: _OptimizeParams, targets: _OptimizeTargets) -> dict[str, object]:
    """Lo que la peticion aporta al informe de la optimizacion."""
    fields = {
        name: getattr(params, name) for name in (
            "raw_sets", "capital", "valley_dd_pct", "portfolio_type", "min_trades_2020_2026",
            "preserve_required_allocations", "dd_reserve_pct", "search_restarts",
            "bootstrap_simulations", "bootstrap_block_size", "bootstrap_seed", "margin_balance",
            "max_margin_pct", "margin_profile", "stock_leverage", "default_leverage",
            "stock_contract_size", "default_contract_size", "max_daily_dd", "enforce_point_dd",
            "daily_dd_full_history", "use_deep_refinement",
        )
    }
    fields["target_valley_dd"] = targets.target_valley_dd
    fields["target_point_dd"] = targets.target_point_dd
    return fields


def _optimize(params: _OptimizeParams) -> PortfolioResult:
    """Encadena las fases de la optimizacion y arma el informe final."""
    targets = _resolve_targets(params)
    pool = _prepare_pool(params, targets)
    state: dict[str, object] = dict(pool.run_fields())
    state.update(_greedy_phase(params, targets, pool))
    state.update(_search_phase(params, targets, pool, state))
    state.update(_deep_phase(params, targets, pool, state))
    state.update(_execution_phase(params, targets, state))
    run = OptimizationRun(**_run_params(params, targets), **state)
    return build_portfolio_result(run)


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
    """Optimiza la cartera por fases y devuelve el informe completo."""
    return _optimize(_OptimizeParams(**locals()))
