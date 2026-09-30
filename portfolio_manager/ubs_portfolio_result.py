"""Resultado de una optimizacion: asignaciones, avisos y analisis de estres."""
from __future__ import annotations

from dataclasses import dataclass

from .ubs_portfolio import PortfolioResult, PortfolioType, RobustStrategySet, StrategyAllocation
from .ubs_portfolio_curves import (
    bootstrap_valley_drawdown,
    portfolio_daily_closed_floating_dd,
)
from .ubs_portfolio_evaluate import portfolio_group_summary
from .ubs_portfolio_margin import (
    margin_profile_label,
    normalize_margin_profile,
    portfolio_margin_summary,
)
from .ubs_portfolio_utils import _build_unused_sets, _lot_size_step, portfolio_group_key


@dataclass
class OptimizationRun:
    """Estado que dejan las fases de la optimizacion al armar el resultado."""

    raw_sets: list[RobustStrategySet]
    capital: float
    valley_dd_pct: float
    portfolio_type: PortfolioType
    min_trades_2020_2026: int
    max_units_per_group_pct: float | None
    preserve_required_allocations: bool
    dd_reserve_pct: float
    search_restarts: int
    bootstrap_simulations: int
    bootstrap_block_size: int | None
    bootstrap_seed: int
    margin_balance: float | None
    max_margin_pct: float | None
    margin_profile: str | None
    stock_leverage: float
    default_leverage: float
    stock_contract_size: float
    default_contract_size: float
    max_daily_dd: float | None
    enforce_point_dd: bool
    daily_dd_full_history: bool
    use_deep_refinement: bool
    target_valley_dd: object
    target_point_dd: object
    eligible: object
    selected: object
    base_selected_count: object
    required_ids: object
    configured_group_units_pct: object
    candidate_group_count: object
    group_units_pct_feasibility_floor: object
    allocations: object
    current: object
    greedy_log: object
    stop_reason: object
    correlation_rejections: object
    local_log: object
    group_cap_relaxed: object
    multi_start_log: object
    valid_restarts: object
    deep_log: object
    deep_attempts: object
    deep_pool_expanded: object
    deep_pool_count: object
    executable_steps: object
    execution_adjustments: object


def _portfolio_limit_warnings(run, daily_dd_summary, group_limit_overages, margin_summary, warnings):
    """Avisos de ejecucion, correlacion, cupo por grupo, margen y DD diario."""
    if run.execution_adjustments:
        warnings.append(
            "Lots were rounded down to match integer LotPerBalance_step export values."
        )
    if run.correlation_rejections:
        warnings.append(f"{run.correlation_rejections} increment candidate(s) rejected by correlation limits.")
    if group_limit_overages:
        limit_pct = run.max_units_per_group_pct * 100.0
        warnings.append(
            f"Concentracion por grupo sobre {limit_pct:.0f}% tras optimizar: "
            + ", ".join(group_limit_overages)
        )
    if margin_summary:
        profile_label = str(margin_summary.get("profile_label") or margin_profile_label(run.margin_profile))
        if normalize_margin_profile(str(margin_summary.get("profile") or run.margin_profile)) == "ttp":
            rule_text = (
                "Forex 1:50; indices 1:15; commodities/metales/energias 1:10; "
                "stocks/crypto 1:2; contract_size stocks 100/resto 1."
            )
        else:
            rule_text = "Stocks 1:20 contract_size 100; resto 1:500 contract_size 1."
        warnings.append(
            f"Margen {profile_label} aplicado: {rule_text} "
            f"Uso estimado {float(margin_summary['total']):.2f}/"
            f"{float(margin_summary['limit']):.2f} "
            f"({float(margin_summary['usage_pct']):.1f}% del limite)."
        )
    if run.max_daily_dd is not None:
        worst_day = str(daily_dd_summary.get("worst_day") or "-")
        warnings.append(
            "DD diario max aplicado: cerrado + flotante estimado "
            f"({'historico completo' if run.daily_dd_full_history else 'mes objetivo'}) "
            f"{run.current.daily_dd:.2f}/{float(run.max_daily_dd):.2f}"
            + (f" en {worst_day}." if worst_day != "-" else ".")
        )

def _portfolio_search_warnings(run, eligible_groups, result_allocations, warnings):
    """Avisos del refinado profundo, los sets fijados y el reparto por grupo."""
    if run.use_deep_refinement:
        if run.deep_log:
            warnings.append(
                "Optimizacion profunda aplicada: "
                f"{len(run.deep_log)} movimiento(s), {run.deep_attempts} intento(s), "
                f"pool {run.base_selected_count}->{len(run.selected)} candidato(s)."
            )
        else:
            pool_text = (
                f" pool {run.base_selected_count}->{run.deep_pool_count} candidato(s)"
                if run.deep_pool_expanded
                else f" pool {len(run.selected)} candidato(s)"
            )
            warnings.append(
                "Optimizacion profunda: no encontro mejora valida "
                f"tras {run.deep_attempts} intento(s),{pool_text}."
            )
    if run.preserve_required_allocations and run.required_ids:
        repair_reductions = sum(
            1 for decision in run.greedy_log if decision.action == "reduce_unit_for_repair"
        )
        if repair_reductions:
            warnings.append(
                f"Repair preserved every existing strategy and changed only {repair_reductions} existing unit(s) required by DD limits."
            )
        else:
            warnings.append(
                "Existing portfolio strategies and units were preserved; only replacement allocations were optimized."
            )
    if run.group_cap_relaxed:
        warnings.append(
            "Balanced relajo el limite porcentual por grupo porque la asignacion estricta dejaba el portfolio infrautilizado."
        )
    if run.portfolio_type != PortfolioType.AGGRESSIVE and len(eligible_groups) <= 1:
        only_group = next(iter(eligible_groups), "none")
        warnings.append(
            f"Solo un grupo de activo tuvo curvas elegibles ({only_group}); "
            "no fue posible diversificar por grupo en Balanced/Conservative."
        )
    if run.current.valley_usage_pct < 70:
        warnings.append(
            "Valley DD usage is below 70%. This can be acceptable if no efficient increments remained."
        )
    if run.enforce_point_dd and run.current.point_usage_pct > 95:
        warnings.append("Point DD usage is above 95%. Portfolio is close to point DD limit.")
    if not result_allocations:
        warnings.append("No eligible robust sets found.")

def _portfolio_result_warnings(run, daily_dd_summary, eligible_groups, group_limit_overages, margin_summary, result_allocations):
    """Avisos sobre limites, reservas, correlacion, margen y drawdown diario."""
    warnings: list[str] = []
    if (
        run.configured_group_units_pct is not None
        and run.group_units_pct_feasibility_floor is not None
        and float(run.max_units_per_group_pct) > float(run.configured_group_units_pct) + 1e-9
    ):
        warnings.append(
            "Group unit cap adjusted to the feasible diversification floor: "
            f"{float(run.configured_group_units_pct) * 100.0:.1f}% -> "
            f"{float(run.max_units_per_group_pct) * 100.0:.1f}% for "
            f"{run.candidate_group_count} available asset groups."
        )
    if run.dd_reserve_pct > 0:
        warnings.append(
            f"DD reserve {float(run.dd_reserve_pct):.1f}% applied; optimizer used reduced effective DD targets."
        )
    if run.search_restarts > 0:
        warnings.append(
            f"Multi-start search evaluated {run.valid_restarts}/{int(run.search_restarts)} valid restart(s)."
        )
    _portfolio_search_warnings(run, eligible_groups, result_allocations, warnings)
    _portfolio_limit_warnings(run, daily_dd_summary, group_limit_overages, margin_summary, warnings)

    unused_sets = _build_unused_sets(run.raw_sets, run.eligible, run.selected, run.allocations, run.min_trades_2020_2026)
    stress_bootstrap = bootstrap_valley_drawdown(
        run.current.equity_curve_2020_2026,
        nominal_valley_dd_limit=run.capital * run.valley_dd_pct / 100.0,
        effective_valley_dd_limit=run.target_valley_dd,
        simulations=run.bootstrap_simulations,
        block_size=run.bootstrap_block_size,
        seed=run.bootstrap_seed,
    )
    if stress_bootstrap.alert:
        warnings.append(
            f"ALERTA bootstrap: DD valle P95 {stress_bootstrap.valley_dd_p95:.2f} "
            f"supera el limite efectivo {stress_bootstrap.effective_valley_dd_limit:.2f}."
        )
    return stress_bootstrap, unused_sets, warnings

def _portfolio_result_allocations(run, margin_by_set):
    """Asignaciones finales del portafolio con su reparto por grupo."""
    result_allocations: list[StrategyAllocation] = []
    for strategy in run.selected:
        units = run.allocations.get(strategy.set_id, 0)
        if units <= 0:
            continue
        margin_row = margin_by_set.get(strategy.set_id, {}) if isinstance(margin_by_set, dict) else {}
        result_allocations.append(
            StrategyAllocation(
                set_id=strategy.set_id,
                candidate_id=strategy.candidate_id,
                symbol=strategy.symbol,
                units=units,
                lot=round(units * 0.01, 2),
                net_profit_contribution=strategy.net_profit_2020_2026_001 * units,
                standalone_valley_dd=strategy.valley_dd_2020_2026_001 * units,
                standalone_point_dd=strategy.point_dd_2020_2026_001 * units,
                timeframe=strategy.timeframe,
                set_path=strategy.set_path,
                is_report_path=strategy.is_report_path,
                oos_report_path=strategy.oos_report_path,
                lot_size_step=float(run.executable_steps.get(strategy.set_id, _lot_size_step(run.capital, units) or 0)),
                margin_required=float(margin_row.get("margin", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                margin_pct=(
                    float(margin_row.get("margin", 0.0) or 0.0) / max(float(run.margin_balance or 0.0), 1e-9) * 100.0
                    if run.margin_balance is not None and isinstance(margin_row, dict)
                    else 0.0
                ),
                margin_leverage=float(margin_row.get("leverage", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                margin_contract_size=float(margin_row.get("contract_size", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
                margin_price=float(margin_row.get("price", 0.0) or 0.0) if isinstance(margin_row, dict) else 0.0,
            )
        )
    result_allocations.sort(key=lambda item: (item.units, item.net_profit_contribution), reverse=True)

    group_summary = portfolio_group_summary(run.selected, run.allocations)
    eligible_groups = {portfolio_group_key(strategy.symbol) for strategy in run.eligible}
    group_limit_overages: list[str] = []
    return eligible_groups, group_summary, result_allocations

def _portfolio_result_margin(run):
    """Resumen de margen requerido y de drawdown diario del portafolio."""
    margin_summary = (
        portfolio_margin_summary(
            run.selected,
            run.allocations,
            balance=float(run.margin_balance),
            max_margin_pct=float(run.max_margin_pct),
            margin_profile=run.margin_profile,
            stock_leverage=run.stock_leverage,
            default_leverage=run.default_leverage,
            stock_contract_size=run.stock_contract_size,
            default_contract_size=run.default_contract_size,
        )
        if run.margin_balance is not None and run.max_margin_pct is not None
        else {}
    )
    margin_by_set = margin_summary.get("by_set", {}) if isinstance(margin_summary, dict) else {}
    daily_dd_summary: dict[str, object] = {}
    return margin_by_set, margin_summary

def build_portfolio_result(run: OptimizationRun) -> PortfolioResult:
    """Arma el resultado final con sus metricas, avisos y sets sin usar."""
    margin_by_set, margin_summary = _portfolio_result_margin(run)
    if run.max_daily_dd is not None:
        _daily_dd, daily_dd_summary = portfolio_daily_closed_floating_dd(
            run.selected,
            run.allocations,
            full_history=bool(run.daily_dd_full_history),
        )
        daily_dd_summary["limit"] = float(run.max_daily_dd)
        daily_dd_summary["usage_pct"] = run.current.daily_dd / float(run.max_daily_dd) * 100.0 if float(run.max_daily_dd) > 0 else 0.0

    eligible_groups, group_summary, result_allocations = _portfolio_result_allocations(run, margin_by_set)
    if run.max_units_per_group_pct is not None and len(eligible_groups) > 1:
        limit_pct = run.max_units_per_group_pct * 100.0
        group_limit_overages = [
            f"{group} {float(stats['unit_pct']):.1f}%"
            for group, stats in group_summary.items()
            if float(stats["unit_pct"]) > limit_pct + 0.1
        ]

    stress_bootstrap, unused_sets, warnings = _portfolio_result_warnings(run, daily_dd_summary, eligible_groups, group_limit_overages, margin_summary, result_allocations)
    return PortfolioResult(
        allocations=result_allocations,
        equity_curve_2020_2026=run.current.equity_curve_2020_2026,
        total_net_profit=run.current.total_net_profit,
        actual_valley_dd=run.current.valley_dd,
        actual_point_dd=run.current.point_dd,
        target_valley_dd=run.target_valley_dd,
        target_point_dd=run.target_point_dd,
        valley_usage_pct=run.current.valley_usage_pct,
        point_usage_pct=run.current.point_usage_pct,
        total_lot=run.current.total_lot,
        total_units=run.current.total_units,
        active_strategies=run.current.active_strategies,
        stop_reason=run.stop_reason,
        warnings=warnings,
        decision_log=run.greedy_log + run.local_log + run.multi_start_log + run.deep_log,
        unused_sets=unused_sets,
        correlation_rejections=run.correlation_rejections,
        group_summary=group_summary,
        stress_bootstrap=stress_bootstrap,
        margin_summary=margin_summary,
        max_daily_dd=run.current.daily_dd,
        target_daily_dd=float(run.max_daily_dd) if run.max_daily_dd is not None else None,
        daily_dd_summary=daily_dd_summary,
        daily_dd_full_history=bool(run.daily_dd_full_history),
        enforce_point_dd=bool(run.enforce_point_dd),
    )
