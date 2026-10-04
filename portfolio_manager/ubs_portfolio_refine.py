"""Refinado profundo de las asignaciones ya construidas."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _DeepRefineConfig:
    """Limites con los que la optimizacion profunda puede mover unidades."""

    minimum_active_strategies: int | None
    max_units_per_set: int | None
    max_total_units: int | None
    max_units_per_symbol: int | None
    max_sets_per_symbol: int | None
    max_sets_per_group: int | None
    max_units_per_group_pct: float | None
    group_unit_cap_bootstrap: int
    max_pair_corr: float | None
    max_downside_corr: float | None
    max_dd_overlap: float | None
    existing_portfolio_curves: Sequence[Sequence[float]] | None
    max_portfolio_corr: float | None
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
    max_iterations: int = 160


class _DeepRefiner:
    """Busca altas y permutas de unidades que mejoren el beneficio sin romper limites."""

    def __init__(
        self, sets: list[RobustStrategySet], allocations: dict[str, int],
        current: PortfolioEvaluation, config: _DeepRefineConfig,
    ) -> None:
        self.config = config
        self.working_sets = list({strategy.set_id: strategy for strategy in sets}.values())
        self.allocations = {
            strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
            for strategy in self.working_sets
        }
        self.current = current
        self.decision_log: list[OptimizationDecision] = []
        self.attempts = 0
        self.best_move: dict[str, object] | None = None

    def _evaluate(self, allocations: dict[str, int]) -> PortfolioEvaluation:
        """Evalua una asignacion con los objetivos de DD de la cartera actual."""
        config = self.config
        return evaluate_portfolio(
            self.working_sets,
            allocations,
            self.current.target_valley_dd,
            self.current.target_point_dd,
            config.max_daily_dd,
            config.enforce_point_dd,
            config.daily_dd_full_history,
        )

    def _correlation_rejected(self, target: RobustStrategySet, allocations: dict[str, int]) -> bool:
        """Correlacion de una estrategia que entraria nueva en la cartera."""
        config = self.config
        rejected_by_corr, _reason = violates_correlation_limits(
            target,
            self.working_sets,
            allocations,
            config.max_pair_corr,
            config.max_downside_corr,
            config.max_dd_overlap,
        )
        return rejected_by_corr

    def _consider(
        self, action: str, from_set: RobustStrategySet | None, to_set: RobustStrategySet,
        temp_allocations: dict[str, int], temp: PortfolioEvaluation,
    ) -> None:
        """Guarda el movimiento si gana mas que el mejor encontrado hasta ahora."""
        config = self.config
        gain = temp.total_net_profit - self.current.total_net_profit
        if (
            gain > 1e-9
            and not _evaluation_violates_dd_limits(temp)
            and _portfolio_corr_allowed(temp, config.existing_portfolio_curves, config.max_portfolio_corr)
            and (self.best_move is None or gain > float(self.best_move["gain"]))
        ):
            self.best_move = {
                "action": action,
                "from_set": from_set,
                "to_set": to_set,
                "allocations": temp_allocations,
                "evaluation": temp,
                "gain": gain,
            }

    def _can_add_unit(self, target: RobustStrategySet) -> bool:
        """Si cabe una unidad mas de esta estrategia dentro de los cupos."""
        config = self.config
        return can_add_unit(
            target_set=target,
            sets=self.working_sets,
            allocations=self.allocations,
            max_units_per_set=config.max_units_per_set,
            max_total_units=config.max_total_units,
            max_units_per_symbol=config.max_units_per_symbol,
            max_sets_per_symbol=config.max_sets_per_symbol,
            max_units_per_group_pct=config.max_units_per_group_pct,
            max_sets_per_group=config.max_sets_per_group,
            group_unit_cap_bootstrap=config.group_unit_cap_bootstrap,
            margin_balance=config.margin_balance,
            max_margin_pct=config.max_margin_pct,
            margin_profile=config.margin_profile,
            stock_leverage=config.stock_leverage,
            default_leverage=config.default_leverage,
            stock_contract_size=config.stock_contract_size,
            default_contract_size=config.default_contract_size,
        )

    def _try_add(self, target: RobustStrategySet) -> None:
        """Prueba a anadir una unidad de la estrategia objetivo."""
        if not self._can_add_unit(target):
            return
        if self.allocations.get(target.set_id, 0) <= 0 and self._correlation_rejected(
            target, self.allocations
        ):
            return
        temp_allocations = self.allocations.copy()
        temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
        self._consider(
            "deep_add_unit", None, target, temp_allocations, self._evaluate(temp_allocations)
        )

    def _swap_allowed(self, target: RobustStrategySet, temp_allocations: dict[str, int]) -> bool:
        """Comprueba cupos, minimo de activas y correlacion de la permuta."""
        config = self.config
        if (
            config.minimum_active_strategies is not None
            and _portfolio_active_count(temp_allocations) < config.minimum_active_strategies
        ):
            return False
        if not _allocations_respect_constraints(
            self.working_sets,
            temp_allocations,
            config.max_units_per_set,
            config.max_total_units,
            config.max_units_per_symbol,
            config.max_sets_per_symbol,
            config.max_sets_per_group,
            config.margin_balance,
            config.max_margin_pct,
            config.margin_profile,
            config.stock_leverage,
            config.default_leverage,
            config.stock_contract_size,
            config.default_contract_size,
        ):
            return False
        if not _target_group_units_pct_allowed(
            target,
            self.working_sets,
            temp_allocations,
            config.max_units_per_group_pct,
            config.group_unit_cap_bootstrap,
        ):
            return False
        if self.allocations.get(target.set_id, 0) <= 0:
            corr_allocations = temp_allocations.copy()
            corr_allocations[target.set_id] = 0
            if self._correlation_rejected(target, corr_allocations):
                return False
        return True

    def _try_swap(self, target: RobustStrategySet, source: RobustStrategySet) -> None:
        """Prueba a mover una unidad de la estrategia origen a la objetivo."""
        temp_allocations = self.allocations.copy()
        temp_allocations[source.set_id] -= 1
        temp_allocations[target.set_id] = temp_allocations.get(target.set_id, 0) + 1
        if not self._swap_allowed(target, temp_allocations):
            return
        self._consider(
            "deep_swap_unit", source, target, temp_allocations, self._evaluate(temp_allocations)
        )

    def _scan(self) -> None:
        """Recorre las estrategias por atractivo y busca el mejor movimiento."""
        self.best_move = None
        ordered_targets = sorted(
            self.working_sets,
            key=lambda item: score_set_for_portfolio(item, max(int(self.allocations.get(item.set_id, 0)), 1)),
            reverse=True,
        )
        for target in ordered_targets:
            self.attempts += 1
            self._try_add(target)
            active_sources = [
                source for source in self.working_sets if self.allocations.get(source.set_id, 0) > 0
            ]
            for source in active_sources:
                if source.set_id == target.set_id:
                    continue
                self.attempts += 1
                self._try_swap(target, source)

    def _apply_best_move(self, iteration: int) -> None:
        """Aplica el movimiento elegido y lo anota en la bitacora."""
        best_move = self.best_move
        assert best_move is not None
        previous = self.current
        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert to_set is not None and isinstance(to_set, RobustStrategySet)
        self.allocations = best_move["allocations"]  # type: ignore[assignment]
        self.current = best_move["evaluation"]  # type: ignore[assignment]
        self.decision_log.append(
            OptimizationDecision(
                step=iteration,
                action=str(best_move["action"]),
                set_id=to_set.set_id,
                from_set_id=from_set.set_id if isinstance(from_set, RobustStrategySet) else None,
                to_set_id=to_set.set_id,
                gain=self.current.total_net_profit - previous.total_net_profit,
                valley_cost=self.current.valley_dd - previous.valley_dd,
                point_cost=self.current.point_dd - previous.point_dd,
                score=float(best_move["gain"]),
                portfolio_net_profit_after=self.current.total_net_profit,
                portfolio_valley_dd_after=self.current.valley_dd,
                portfolio_point_dd_after=self.current.point_dd,
                reason="Optimizacion profunda: movimiento validado contra DD, margen y correlacion",
            )
        )

    def run(self) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
        """Itera hasta que ningun movimiento mejore el beneficio."""
        for iteration in range(1, self.config.max_iterations + 1):
            self._scan()
            if self.best_move is None:
                break
            self._apply_best_move(iteration)
        return self.allocations, self.current, self.decision_log, self.attempts


def _deep_refine_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    **limits: object,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    """Mejora el beneficio con altas y permutas de unidades ya validadas."""
    return _DeepRefiner(sets, allocations, current, _DeepRefineConfig(**limits)).run()
