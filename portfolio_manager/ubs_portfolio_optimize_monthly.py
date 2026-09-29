"""Optimizacion del portafolio mensual con validacion estricta."""
from __future__ import annotations

from typing import Sequence

from .ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    RobustStrategySet,
)
from .ubs_portfolio_evaluate import (
    evaluate_portfolio,
)
from .ubs_portfolio_monthly import (
    _strict_monthly_candidate_variants,
    _strict_validation_for_allocations,
)
from .ubs_portfolio_monthly_repair import (
    _repair_allocations_to_strict_monthly,
    _strict_monthly_deep_refine_allocations,
    _strict_monthly_safe_refill_allocations,
)
from .ubs_portfolio_optimize import (
    optimize_portfolio,
)
from .ubs_portfolio_utils import (
    _active_unit_allocations,
    _portfolio_active_count,
    group_limits_for_portfolio_type,
)


def optimize_strict_monthly_portfolio(
    monthly_sets: list[RobustStrategySet],
    full_sets: list[RobustStrategySet],
    *,
    target_month: int,
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
    dd_reserve_pct: float = 0.0,
    search_restarts: int = 0,
    margin_balance: float | None = None,
    max_margin_pct: float | None = None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
    use_deep_refinement: bool = True,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> PortfolioResult:
    """Optimize a monthly portfolio with the 5-year seasonal test in the loop.

    This is a bounded, deterministic deep search.  It builds several candidate
    pools ranked by monthly profit and seasonal dominance, optimizes each pool
    with the normal DD/margin/correlation engine, and keeps only portfolios that
    pass the strict year-by-year and "best month in 5Y" audit.
    """
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")

    reserve_factor = 1.0 - min(max(float(dd_reserve_pct), 0.0), 99.0) / 100.0
    target_valley_dd = float(capital) * float(valley_dd_pct) * reserve_factor / 100.0
    target_point_dd = float(capital) * float(point_dd_pct) * reserve_factor / 100.0
    full_by_id = {strategy.set_id: strategy for strategy in full_sets}
    variants = _strict_monthly_candidate_variants(
        monthly_sets,
        full_sets,
        target_month=month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        min_trades_2020_2026=min_trades_2020_2026,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        enforce_point_dd=enforce_point_dd,
    )
    if not variants:
        raise ValueError("No hay candidatos mensuales elegibles para la busqueda estricta.")

    base_result: PortfolioResult | None = None
    base_label = ""
    errors: list[str] = []
    for label, candidate_pool in variants:
        try:
            base_result = optimize_portfolio(
                raw_sets=candidate_pool,
                capital=capital,
                valley_dd_pct=valley_dd_pct,
                point_dd_pct=point_dd_pct,
                portfolio_type=portfolio_type,
                min_trades_2020_2026=min_trades_2020_2026,
                top_k_per_symbol=max(top_k_per_symbol, len(candidate_pool)),
                max_total_candidates=None,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                run_local_search=run_local_search,
                max_pair_corr=max_pair_corr,
                max_downside_corr=max_downside_corr,
                max_dd_overlap=max_dd_overlap,
                existing_portfolio_curves=existing_portfolio_curves,
                max_portfolio_corr=max_portfolio_corr,
                dd_reserve_pct=dd_reserve_pct,
                search_restarts=int(search_restarts),
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
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        base_units = {
            allocation.set_id: allocation.units
            for allocation in base_result.allocations
            if allocation.units > 0
        }
        repaired_units, repaired_eval, validation, repair_log = _repair_allocations_to_strict_monthly(
            candidate_pool,
            full_by_id,
            base_units,
            target_month=month,
            target_valley_dd=base_result.target_valley_dd,
            target_point_dd=base_result.target_point_dd,
            max_daily_dd=max_daily_dd,
            enforce_point_dd=enforce_point_dd,
            daily_dd_full_history=daily_dd_full_history,
        )
        if bool(validation.get("passed")):
            active_repaired = _active_unit_allocations(repaired_units)
            if not active_repaired:
                errors.append(f"{label}: reparacion estricta dejo el portafolio sin estrategias")
                continue
            if active_repaired != base_units:
                repaired_sets = [
                    strategy for strategy in candidate_pool if strategy.set_id in active_repaired
                ]
                try:
                    base_result = optimize_portfolio(
                        raw_sets=repaired_sets,
                        capital=capital,
                        valley_dd_pct=valley_dd_pct,
                        point_dd_pct=point_dd_pct,
                        portfolio_type=portfolio_type,
                        min_trades_2020_2026=min_trades_2020_2026,
                        top_k_per_symbol=max(1, len(repaired_sets)),
                        max_total_candidates=None,
                        max_units_per_set=max_units_per_set,
                        max_total_units=sum(active_repaired.values()),
                        max_units_per_symbol=max_units_per_symbol,
                        max_sets_per_symbol=max_sets_per_symbol,
                        run_local_search=False,
                        max_pair_corr=max_pair_corr,
                        max_downside_corr=max_downside_corr,
                        max_dd_overlap=max_dd_overlap,
                        existing_portfolio_curves=existing_portfolio_curves,
                        max_portfolio_corr=max_portfolio_corr,
                        dd_reserve_pct=dd_reserve_pct,
                        search_restarts=0,
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
                        required_initial_allocations=active_repaired,
                        preserve_required_allocations=True,
                    )
                except Exception as exc:
                    errors.append(f"{label}: reparacion estricta no pudo reconstruirse: {exc}")
                    continue
                base_result.warnings = [
                    warning for warning in base_result.warnings
                    if not warning.startswith("Existing portfolio strategies and units were preserved")
                ]
                base_result.decision_log.extend(repair_log)
                base_result.warnings.append(
                    "Reparacion estricta mensual: se redujeron "
                    f"{len(repair_log)} unidad(es) para cumplir DD de todos los meses y mejor mes 5A."
                )
            base_result.seasonal_validation = validation
            base_label = label
            break
        reasons = validation.get("reasons") or []
        errors.append(f"{label}: " + "; ".join(str(item) for item in list(reasons)[:3]))

    if base_result is None or not bool(base_result.seasonal_validation.get("passed")):
        detail = " | ".join(errors[:6])
        raise ValueError(
            "Ninguna variante de busqueda estricta mensual fue viable."
            + (f" {detail}" if detail else "")
        )

    monthly_by_id = {strategy.set_id: strategy for strategy in monthly_sets}
    candidate_pool_by_id: dict[str, RobustStrategySet] = {}
    for _label, variant_pool in variants:
        for strategy in variant_pool:
            candidate_pool_by_id[strategy.set_id] = strategy
    for allocation in base_result.allocations:
        strategy = monthly_by_id.get(allocation.set_id)
        if strategy is not None:
            candidate_pool_by_id[allocation.set_id] = strategy

    if not use_deep_refinement:
        group_limits = group_limits_for_portfolio_type(portfolio_type)
        safe_allocations, safe_eval, safe_log, attempts = _strict_monthly_safe_refill_allocations(
            list(candidate_pool_by_id.values()),
            full_by_id,
            {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
            evaluate_portfolio(
                list(candidate_pool_by_id.values()),
                {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
                base_result.target_valley_dd,
                base_result.target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            ),
            target_month=month,
            max_units_per_set=max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=max_units_per_symbol,
            max_sets_per_symbol=max_sets_per_symbol,
            max_sets_per_group=group_limits.max_sets,
            max_units_per_group_pct=group_limits.max_units_pct,
            group_unit_cap_bootstrap=group_limits.bootstrap_units,
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
        active_safe = _active_unit_allocations(safe_allocations)
        validation = _strict_validation_for_allocations(
            full_by_id,
            active_safe,
            target_month=month,
            target_valley_dd=base_result.target_valley_dd,
            target_point_dd=base_result.target_point_dd,
            enforce_point_dd=enforce_point_dd,
        )
        if safe_eval.total_net_profit > base_result.total_net_profit + 1e-9 and bool(validation.get("passed")):
            safe_sets = [
                monthly_by_id[set_id]
                for set_id in active_safe
                if set_id in monthly_by_id
            ]
            safe_result = optimize_portfolio(
                raw_sets=safe_sets,
                capital=capital,
                valley_dd_pct=valley_dd_pct,
                point_dd_pct=point_dd_pct,
                portfolio_type=portfolio_type,
                min_trades_2020_2026=min_trades_2020_2026,
                top_k_per_symbol=max(1, len(safe_sets)),
                max_total_candidates=None,
                max_units_per_set=max_units_per_set,
                max_total_units=sum(active_safe.values()),
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
                run_local_search=False,
                max_pair_corr=max_pair_corr,
                max_downside_corr=max_downside_corr,
                max_dd_overlap=max_dd_overlap,
                existing_portfolio_curves=existing_portfolio_curves,
                max_portfolio_corr=max_portfolio_corr,
                required_initial_allocations=active_safe,
                preserve_required_allocations=True,
                dd_reserve_pct=dd_reserve_pct,
                search_restarts=0,
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
            safe_result.seasonal_validation = validation
            safe_result.warnings = [
                warning for warning in safe_result.warnings
                if not warning.startswith("Existing portfolio strategies and units were preserved")
            ]
            safe_result.decision_log.extend(base_result.decision_log)
            safe_result.decision_log.extend(safe_log)
            safe_result.warnings.extend(base_result.warnings)
            safe_result.warnings.append(
                "Relleno seguro mensual aplicado sin optimizacion profunda: "
                f"net {base_result.total_net_profit:,.2f} -> {safe_result.total_net_profit:,.2f}; "
                f"unidades {base_result.total_units} -> {safe_result.total_units}; "
                f"base '{base_label}', {attempts} intentos evaluados."
            )
            return safe_result
        base_result.warnings.append(
            "Relleno seguro mensual: no encontro unidades adicionales validas "
            f"sin romper DD/margen/correlacion/5A ({attempts} intentos evaluados)."
        )
        base_result.warnings.append(
            f"Generacion estricta mensual OK sin optimizacion profunda; base '{base_label}'."
        )
        return base_result

    group_limits = group_limits_for_portfolio_type(portfolio_type)
    refined_allocations, refined_eval, refinement_log, attempts = _strict_monthly_deep_refine_allocations(
        list(candidate_pool_by_id.values()),
        full_by_id,
        {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
        evaluate_portfolio(
            list(candidate_pool_by_id.values()),
            {allocation.set_id: allocation.units for allocation in base_result.allocations if allocation.units > 0},
            base_result.target_valley_dd,
            base_result.target_point_dd,
            max_daily_dd,
            enforce_point_dd,
            daily_dd_full_history,
        ),
        target_month=month,
        minimum_active_strategies=base_result.active_strategies,
        max_units_per_set=max_units_per_set,
        max_total_units=max_total_units,
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        max_sets_per_group=group_limits.max_sets,
        max_units_per_group_pct=group_limits.max_units_pct,
        group_unit_cap_bootstrap=group_limits.bootstrap_units,
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
    if refined_eval.total_net_profit <= base_result.total_net_profit + 1e-9:
        base_result.warnings.append(
            "Optimizacion profunda: no encontro mejora valida sobre la base estricta "
            f"({attempts} movimientos evaluados)."
        )
        return base_result

    active_refined = _active_unit_allocations(refined_allocations)
    validation = _strict_validation_for_allocations(
        full_by_id,
        active_refined,
        target_month=month,
        target_valley_dd=base_result.target_valley_dd,
        target_point_dd=base_result.target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    if _portfolio_active_count(active_refined) < base_result.active_strategies or not bool(validation.get("passed")):
        base_result.warnings.append(
            "Optimizacion profunda: mejora descartada por diversificacion o validacion 5A."
        )
        return base_result

    refined_sets = [
        monthly_by_id[set_id]
        for set_id in active_refined
        if set_id in monthly_by_id
    ]
    refined_result = optimize_portfolio(
        raw_sets=refined_sets,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
        portfolio_type=portfolio_type,
        min_trades_2020_2026=min_trades_2020_2026,
        top_k_per_symbol=max(1, len(refined_sets)),
        max_total_candidates=None,
        max_units_per_set=max_units_per_set,
        max_total_units=sum(active_refined.values()),
        max_units_per_symbol=max_units_per_symbol,
        max_sets_per_symbol=max_sets_per_symbol,
        run_local_search=False,
        max_pair_corr=max_pair_corr,
        max_downside_corr=max_downside_corr,
        max_dd_overlap=max_dd_overlap,
        existing_portfolio_curves=existing_portfolio_curves,
        max_portfolio_corr=max_portfolio_corr,
        required_initial_allocations=active_refined,
        preserve_required_allocations=True,
        dd_reserve_pct=dd_reserve_pct,
        search_restarts=0,
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
    refined_result.seasonal_validation = validation
    refined_result.warnings = [
        warning for warning in refined_result.warnings
        if not warning.startswith("Existing portfolio strategies and units were preserved")
    ]
    refined_result.decision_log.extend(refinement_log)
    refined_result.warnings.append(
        "Optimizacion profunda aplicada: "
        f"net {base_result.total_net_profit:,.2f} -> {refined_result.total_net_profit:,.2f}; "
        f"estrategias {base_result.active_strategies} -> {refined_result.active_strategies}; "
        f"base '{base_label}', {attempts} movimientos evaluados."
    )
    return refined_result
