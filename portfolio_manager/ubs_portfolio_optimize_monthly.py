"""Optimizacion del portafolio mensual con validacion estricta."""
from __future__ import annotations

from dataclasses import dataclass
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


_PRESERVED_WARNING_PREFIX = "Existing portfolio strategies and units were preserved"


@dataclass
class _StrictMonthlyConfig:
    """Parametros de la busqueda mensual estricta."""

    target_month: int
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
    dd_reserve_pct: float = 0.0
    search_restarts: int = 0
    margin_balance: float | None = None
    max_margin_pct: float | None = None
    margin_profile: str | None = "roboforex"
    stock_leverage: float = 20.0
    default_leverage: float = 500.0
    stock_contract_size: float = 100.0
    default_contract_size: float = 1.0
    use_deep_refinement: bool = True
    max_daily_dd: float | None = None
    enforce_point_dd: bool = True
    daily_dd_full_history: bool = False


class _StrictMonthlySearch:
    """Busqueda determinista de una cartera que pasa la auditoria 5A del mes."""

    def __init__(
        self, monthly_sets: list[RobustStrategySet], full_sets: list[RobustStrategySet],
        config: _StrictMonthlyConfig,
    ) -> None:
        self.config = config
        self.month = int(config.target_month)
        if not 1 <= self.month <= 12:
            raise ValueError("target_month must be between 1 and 12")
        reserve_factor = 1.0 - min(max(float(config.dd_reserve_pct), 0.0), 99.0) / 100.0
        self.target_valley_dd = float(config.capital) * float(config.valley_dd_pct) * reserve_factor / 100.0
        self.target_point_dd = float(config.capital) * float(config.point_dd_pct) * reserve_factor / 100.0
        self.monthly_sets = monthly_sets
        self.monthly_by_id = {strategy.set_id: strategy for strategy in monthly_sets}
        self.full_by_id = {strategy.set_id: strategy for strategy in full_sets}
        self.full_sets = full_sets
        self.errors: list[str] = []
        self.base_label = ""

    def _optimize(
        self, raw_sets: list[RobustStrategySet], *, top_k_per_symbol: int,
        max_total_units: int | None, run_local_search: bool, search_restarts: int, **extra: object,
    ) -> PortfolioResult:
        """Llama al optimizador normal con los limites de esta busqueda."""
        config = self.config
        return optimize_portfolio(
            raw_sets=raw_sets,
            capital=config.capital,
            valley_dd_pct=config.valley_dd_pct,
            point_dd_pct=config.point_dd_pct,
            portfolio_type=config.portfolio_type,
            min_trades_2020_2026=config.min_trades_2020_2026,
            top_k_per_symbol=top_k_per_symbol,
            max_total_candidates=None,
            max_units_per_set=config.max_units_per_set,
            max_total_units=max_total_units,
            max_units_per_symbol=config.max_units_per_symbol,
            max_sets_per_symbol=config.max_sets_per_symbol,
            run_local_search=run_local_search,
            max_pair_corr=config.max_pair_corr,
            max_downside_corr=config.max_downside_corr,
            max_dd_overlap=config.max_dd_overlap,
            existing_portfolio_curves=config.existing_portfolio_curves,
            max_portfolio_corr=config.max_portfolio_corr,
            dd_reserve_pct=config.dd_reserve_pct,
            search_restarts=search_restarts,
            margin_balance=config.margin_balance,
            max_margin_pct=config.max_margin_pct,
            margin_profile=config.margin_profile,
            stock_leverage=config.stock_leverage,
            default_leverage=config.default_leverage,
            stock_contract_size=config.stock_contract_size,
            default_contract_size=config.default_contract_size,
            max_daily_dd=config.max_daily_dd,
            enforce_point_dd=config.enforce_point_dd,
            daily_dd_full_history=config.daily_dd_full_history,
            **extra,
        )

    def _rebuild_from_units(self, sets: list[RobustStrategySet], units: dict[str, int]) -> PortfolioResult:
        """Reconstruye el resultado conservando exactamente esas unidades."""
        return self._optimize(
            sets,
            top_k_per_symbol=max(1, len(sets)),
            max_total_units=sum(units.values()),
            run_local_search=False,
            search_restarts=0,
            required_initial_allocations=units,
            preserve_required_allocations=True,
        )

    def _refine_limits(self) -> dict[str, object]:
        """Limites comunes de los refinados mensuales estrictos."""
        config = self.config
        group_limits = group_limits_for_portfolio_type(config.portfolio_type)
        return {
            "target_month": self.month,
            "max_units_per_set": config.max_units_per_set,
            "max_total_units": config.max_total_units,
            "max_units_per_symbol": config.max_units_per_symbol,
            "max_sets_per_symbol": config.max_sets_per_symbol,
            "max_sets_per_group": group_limits.max_sets,
            "max_units_per_group_pct": group_limits.max_units_pct,
            "group_unit_cap_bootstrap": group_limits.bootstrap_units,
            "max_pair_corr": config.max_pair_corr,
            "max_downside_corr": config.max_downside_corr,
            "max_dd_overlap": config.max_dd_overlap,
            "existing_portfolio_curves": config.existing_portfolio_curves,
            "max_portfolio_corr": config.max_portfolio_corr,
            "margin_balance": config.margin_balance,
            "max_margin_pct": config.max_margin_pct,
            "margin_profile": config.margin_profile,
            "stock_leverage": config.stock_leverage,
            "default_leverage": config.default_leverage,
            "stock_contract_size": config.stock_contract_size,
            "default_contract_size": config.default_contract_size,
            "max_daily_dd": config.max_daily_dd,
            "enforce_point_dd": config.enforce_point_dd,
            "daily_dd_full_history": config.daily_dd_full_history,
        }

    def _validate(self, base_result: PortfolioResult, units: dict[str, int]) -> dict[str, object]:
        """Auditoria estricta del mes para una asignacion concreta."""
        return _strict_validation_for_allocations(
            self.full_by_id,
            units,
            target_month=self.month,
            target_valley_dd=base_result.target_valley_dd,
            target_point_dd=base_result.target_point_dd,
            enforce_point_dd=self.config.enforce_point_dd,
        )

    @staticmethod
    def _base_units(base_result: PortfolioResult) -> dict[str, int]:
        """Unidades activas del resultado base."""
        return {
            allocation.set_id: allocation.units
            for allocation in base_result.allocations
            if allocation.units > 0
        }

    def _candidate_variants(self):
        """Conjuntos candidatos ordenados por beneficio mensual y dominancia."""
        config = self.config
        variants = _strict_monthly_candidate_variants(
            self.monthly_sets,
            self.full_sets,
            target_month=self.month,
            target_valley_dd=self.target_valley_dd,
            target_point_dd=self.target_point_dd,
            min_trades_2020_2026=config.min_trades_2020_2026,
            top_k_per_symbol=config.top_k_per_symbol,
            max_total_candidates=config.max_total_candidates,
            enforce_point_dd=config.enforce_point_dd,
        )
        if not variants:
            raise ValueError("No hay candidatos mensuales elegibles para la busqueda estricta.")
        return variants

    def _apply_repair(
        self, label: str, candidate_pool: list[RobustStrategySet], base_result: PortfolioResult,
        base_units: dict[str, int], repaired_units: dict[str, int], repair_log: list,
    ) -> PortfolioResult | None:
        """Reconstruye la cartera tras la reparacion estricta, si hubo cambios."""
        active_repaired = _active_unit_allocations(repaired_units)
        if not active_repaired:
            self.errors.append(f"{label}: reparacion estricta dejo el portafolio sin estrategias")
            return None
        if active_repaired == base_units:
            return base_result
        repaired_sets = [
            strategy for strategy in candidate_pool if strategy.set_id in active_repaired
        ]
        try:
            rebuilt = self._rebuild_from_units(repaired_sets, active_repaired)
        except Exception as exc:
            self.errors.append(f"{label}: reparacion estricta no pudo reconstruirse: {exc}")
            return None
        rebuilt.warnings = [
            warning for warning in rebuilt.warnings
            if not warning.startswith(_PRESERVED_WARNING_PREFIX)
        ]
        rebuilt.decision_log.extend(repair_log)
        rebuilt.warnings.append(
            "Reparacion estricta mensual: se redujeron "
            f"{len(repair_log)} unidad(es) para cumplir DD de todos los meses y mejor mes 5A."
        )
        return rebuilt

    def _try_variant(self, label: str, candidate_pool: list[RobustStrategySet]) -> PortfolioResult | None:
        """Optimiza y repara un conjunto candidato; None si no es viable."""
        config = self.config
        try:
            base_result = self._optimize(
                candidate_pool,
                top_k_per_symbol=max(config.top_k_per_symbol, len(candidate_pool)),
                max_total_units=config.max_total_units,
                run_local_search=config.run_local_search,
                search_restarts=int(config.search_restarts),
            )
        except Exception as exc:
            self.errors.append(f"{label}: {exc}")
            return None
        base_units = self._base_units(base_result)
        repaired_units, _repaired_eval, validation, repair_log = _repair_allocations_to_strict_monthly(
            candidate_pool,
            self.full_by_id,
            base_units,
            target_month=self.month,
            target_valley_dd=base_result.target_valley_dd,
            target_point_dd=base_result.target_point_dd,
            max_daily_dd=config.max_daily_dd,
            enforce_point_dd=config.enforce_point_dd,
            daily_dd_full_history=config.daily_dd_full_history,
        )
        if not bool(validation.get("passed")):
            reasons = validation.get("reasons") or []
            self.errors.append(f"{label}: " + "; ".join(str(item) for item in list(reasons)[:3]))
            return None
        result = self._apply_repair(
            label, candidate_pool, base_result, base_units, repaired_units, repair_log
        )
        if result is None:
            return None
        result.seasonal_validation = validation
        self.base_label = label
        return result

    def _search_base(self, variants) -> PortfolioResult:
        """Primera variante que pasa la auditoria estricta del mes."""
        for label, candidate_pool in variants:
            result = self._try_variant(label, candidate_pool)
            if result is not None and bool(result.seasonal_validation.get("passed")):
                return result
        detail = " | ".join(self.errors[:6])
        raise ValueError(
            "Ninguna variante de busqueda estricta mensual fue viable."
            + (f" {detail}" if detail else "")
        )

    def _refine_pool(self, variants, base_result: PortfolioResult) -> list[RobustStrategySet]:
        """Universo sobre el que se busca mejorar la cartera base."""
        candidate_pool_by_id: dict[str, RobustStrategySet] = {}
        for _label, variant_pool in variants:
            for strategy in variant_pool:
                candidate_pool_by_id[strategy.set_id] = strategy
        for allocation in base_result.allocations:
            strategy = self.monthly_by_id.get(allocation.set_id)
            if strategy is not None:
                candidate_pool_by_id[allocation.set_id] = strategy
        return list(candidate_pool_by_id.values())

    def _base_evaluation(self, pool: list[RobustStrategySet], base_result: PortfolioResult, units: dict[str, int]):
        """Evaluacion de la cartera base sobre el universo de refinado."""
        config = self.config
        return evaluate_portfolio(
            pool,
            units,
            base_result.target_valley_dd,
            base_result.target_point_dd,
            config.max_daily_dd,
            config.enforce_point_dd,
            config.daily_dd_full_history,
        )

    def _safe_refill(self, pool: list[RobustStrategySet], base_result: PortfolioResult) -> PortfolioResult:
        """Relleno conservador cuando no se pide optimizacion profunda."""
        units = self._base_units(base_result)
        safe_allocations, safe_eval, safe_log, attempts = _strict_monthly_safe_refill_allocations(
            pool,
            self.full_by_id,
            units,
            self._base_evaluation(pool, base_result, units),
            **self._refine_limits(),
        )
        active_safe = _active_unit_allocations(safe_allocations)
        validation = self._validate(base_result, active_safe)
        if not (safe_eval.total_net_profit > base_result.total_net_profit + 1e-9 and bool(validation.get("passed"))):
            base_result.warnings.append(
                "Relleno seguro mensual: no encontro unidades adicionales validas "
                f"sin romper DD/margen/correlacion/5A ({attempts} intentos evaluados)."
            )
            base_result.warnings.append(
                f"Generacion estricta mensual OK sin optimizacion profunda; base '{self.base_label}'."
            )
            return base_result
        safe_sets = [self.monthly_by_id[set_id] for set_id in active_safe if set_id in self.monthly_by_id]
        safe_result = self._rebuild_from_units(safe_sets, active_safe)
        safe_result.seasonal_validation = validation
        safe_result.warnings = [
            warning for warning in safe_result.warnings
            if not warning.startswith(_PRESERVED_WARNING_PREFIX)
        ]
        safe_result.decision_log.extend(base_result.decision_log)
        safe_result.decision_log.extend(safe_log)
        safe_result.warnings.extend(base_result.warnings)
        safe_result.warnings.append(
            "Relleno seguro mensual aplicado sin optimizacion profunda: "
            f"net {base_result.total_net_profit:,.2f} -> {safe_result.total_net_profit:,.2f}; "
            f"unidades {base_result.total_units} -> {safe_result.total_units}; "
            f"base '{self.base_label}', {attempts} intentos evaluados."
        )
        return safe_result

    def _deep_refine(self, pool: list[RobustStrategySet], base_result: PortfolioResult) -> PortfolioResult:
        """Optimizacion profunda que solo se acepta si mantiene la validacion 5A."""
        units = self._base_units(base_result)
        refined_allocations, refined_eval, refinement_log, attempts = _strict_monthly_deep_refine_allocations(
            pool,
            self.full_by_id,
            units,
            self._base_evaluation(pool, base_result, units),
            minimum_active_strategies=base_result.active_strategies,
            **self._refine_limits(),
        )
        if refined_eval.total_net_profit <= base_result.total_net_profit + 1e-9:
            base_result.warnings.append(
                "Optimizacion profunda: no encontro mejora valida sobre la base estricta "
                f"({attempts} movimientos evaluados)."
            )
            return base_result
        active_refined = _active_unit_allocations(refined_allocations)
        validation = self._validate(base_result, active_refined)
        if (
            _portfolio_active_count(active_refined) < base_result.active_strategies
            or not bool(validation.get("passed"))
        ):
            base_result.warnings.append(
                "Optimizacion profunda: mejora descartada por diversificacion o validacion 5A."
            )
            return base_result
        refined_sets = [
            self.monthly_by_id[set_id] for set_id in active_refined if set_id in self.monthly_by_id
        ]
        refined_result = self._rebuild_from_units(refined_sets, active_refined)
        refined_result.seasonal_validation = validation
        refined_result.warnings = [
            warning for warning in refined_result.warnings
            if not warning.startswith(_PRESERVED_WARNING_PREFIX)
        ]
        refined_result.decision_log.extend(refinement_log)
        refined_result.warnings.append(
            "Optimizacion profunda aplicada: "
            f"net {base_result.total_net_profit:,.2f} -> {refined_result.total_net_profit:,.2f}; "
            f"estrategias {base_result.active_strategies} -> {refined_result.active_strategies}; "
            f"base '{self.base_label}', {attempts} movimientos evaluados."
        )
        return refined_result

    def run(self) -> PortfolioResult:
        """Construye la cartera estricta del mes y la mejora si se puede."""
        variants = self._candidate_variants()
        base_result = self._search_base(variants)
        pool = self._refine_pool(variants, base_result)
        if not self.config.use_deep_refinement:
            return self._safe_refill(pool, base_result)
        return self._deep_refine(pool, base_result)


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
    limits = dict(locals())
    monthly = limits.pop("monthly_sets")
    full = limits.pop("full_sets")
    return _StrictMonthlySearch(monthly, full, _StrictMonthlyConfig(**limits)).run()
