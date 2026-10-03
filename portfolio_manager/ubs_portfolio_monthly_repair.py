"""Reparacion y refinado de asignaciones hasta cumplir el mes estricto."""
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
from .ubs_portfolio_monthly import (
    _strict_monthly_violation_score,
    _strict_validation_for_allocations,
)
from .ubs_portfolio_utils import (
    _portfolio_active_count,
    _portfolio_corr_allowed,
    score_set_for_portfolio,
)


def _repair_allocations_to_strict_monthly(
    monthly_sets: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    max_daily_dd: float | None = None,
    enforce_point_dd: bool = True,
    daily_dd_full_history: bool = False,
) -> tuple[dict[str, int], PortfolioEvaluation, dict[str, object], list[OptimizationDecision]]:
    current_allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in monthly_sets
    }
    current_eval = evaluate_portfolio(
        monthly_sets,
        current_allocations,
        target_valley_dd,
        target_point_dd,
        max_daily_dd,
        enforce_point_dd,
        daily_dd_full_history,
    )
    current_validation = _strict_validation_for_allocations(
        full_by_id,
        current_allocations,
        target_month=target_month,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        enforce_point_dd=enforce_point_dd,
    )
    decision_log: list[OptimizationDecision] = []
    if bool(current_validation.get("passed")):
        return current_allocations, current_eval, current_validation, decision_log

    step = 0
    while sum(current_allocations.values()) > 0:
        current_score = _strict_monthly_violation_score(current_validation)
        best_choice: tuple[
            float,
            float,
            float,
            str,
            RobustStrategySet,
            dict[str, int],
            PortfolioEvaluation,
            dict[str, object],
        ] | None = None
        for strategy in monthly_sets:
            if current_allocations.get(strategy.set_id, 0) <= 0:
                continue
            trial_allocations = current_allocations.copy()
            trial_allocations[strategy.set_id] -= 1
            trial_eval = evaluate_portfolio(
                monthly_sets,
                trial_allocations,
                target_valley_dd,
                target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            )
            trial_validation = _strict_validation_for_allocations(
                full_by_id,
                trial_allocations,
                target_month=target_month,
                target_valley_dd=target_valley_dd,
                target_point_dd=target_point_dd,
                enforce_point_dd=enforce_point_dd,
            )
            trial_score = _strict_monthly_violation_score(trial_validation)
            choice = (
                trial_score,
                -trial_eval.total_net_profit,
                -trial_eval.active_strategies,
                strategy.set_id,
                strategy,
                trial_allocations,
                trial_eval,
                trial_validation,
            )
            if best_choice is None or choice[:4] < best_choice[:4]:
                best_choice = choice
        if best_choice is None or best_choice[0] >= current_score - 1e-9:
            break
        (
            _score,
            _negative_net,
            _negative_active,
            _set_id,
            reduced_set,
            next_allocations,
            next_eval,
            next_validation,
        ) = best_choice
        previous_eval = current_eval
        current_allocations = next_allocations
        current_eval = next_eval
        current_validation = next_validation
        step += 1
        decision_log.append(
            OptimizationDecision(
                step=step,
                action="strict_monthly_reduce_unit",
                set_id=reduced_set.set_id,
                from_set_id=reduced_set.set_id,
                to_set_id=None,
                gain=-reduced_set.net_profit_2020_2026_001,
                valley_cost=current_eval.valley_dd - previous_eval.valley_dd,
                point_cost=current_eval.point_dd - previous_eval.point_dd,
                score=-float(best_choice[0]),
                portfolio_net_profit_after=current_eval.total_net_profit,
                portfolio_valley_dd_after=current_eval.valley_dd,
                portfolio_point_dd_after=current_eval.point_dd,
                reason="Reduccion necesaria para cumplir validacion mensual estricta 5A/DD",
            )
        )
        if bool(current_validation.get("passed")):
            break
    return current_allocations, current_eval, current_validation, decision_log


def _strict_monthly_safe_refill_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    *,
    target_month: int,
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
    sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
    allocations = {
        strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
        for strategy in sets
    }
    decision_log: list[OptimizationDecision] = []
    attempts = 0

    for iteration in range(1, max_iterations + 1):
        best_move: dict[str, object] | None = None
        ordered_targets = sorted(
            sets,
            key=lambda item: score_set_for_portfolio(item, 1),
            reverse=True,
        )
        for target in ordered_targets:
            attempts += 1
            if not can_add_unit(
                target_set=target,
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
            if allocations.get(target.set_id, 0) <= 0:
                rejected_by_corr, _reason = violates_correlation_limits(
                    target,
                    sets,
                    allocations,
                    max_pair_corr,
                    max_downside_corr,
                    max_dd_overlap,
                )
                if rejected_by_corr:
                    continue
            trial_allocations = allocations.copy()
            trial_allocations[target.set_id] = trial_allocations.get(target.set_id, 0) + 1
            trial = evaluate_portfolio(
                sets,
                trial_allocations,
                current.target_valley_dd,
                current.target_point_dd,
                max_daily_dd,
                enforce_point_dd,
                daily_dd_full_history,
            )
            if _evaluation_violates_dd_limits(trial):
                continue
            if not _portfolio_corr_allowed(trial, existing_portfolio_curves, max_portfolio_corr):
                continue
            validation = _strict_validation_for_allocations(
                full_by_id,
                trial_allocations,
                target_month=target_month,
                target_valley_dd=current.target_valley_dd,
                target_point_dd=current.target_point_dd,
                enforce_point_dd=enforce_point_dd,
            )
            if not bool(validation.get("passed")):
                continue
            gain = trial.total_net_profit - current.total_net_profit
            if gain <= 1e-9:
                continue
            choice = {
                "target": target,
                "allocations": trial_allocations,
                "evaluation": trial,
                "gain": gain,
            }
            if best_move is None or gain > float(best_move["gain"]):
                best_move = choice

        if best_move is None:
            break

        previous = current
        target = best_move["target"]
        assert isinstance(target, RobustStrategySet)
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action="strict_monthly_safe_add_unit",
                set_id=target.set_id,
                from_set_id=None,
                to_set_id=target.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_move["gain"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Relleno seguro: unidad anadida sin romper DD, margen, correlacion ni 5A",
            )
        )

    return allocations, current, decision_log, attempts


@dataclass
class _StrictDeepRefineConfig:
    """Limites de la optimizacion profunda con validacion mensual estricta."""

    target_month: int
    minimum_active_strategies: int
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
    max_iterations: int = 120


class _StrictDeepRefiner:
    """Altas y permutas que mejoran el beneficio sin romper la regla mensual."""

    def __init__(
        self, candidate_pool: list[RobustStrategySet], full_by_id: dict[str, RobustStrategySet],
        allocations: dict[str, int], current: PortfolioEvaluation, config: _StrictDeepRefineConfig,
    ) -> None:
        self.config = config
        self.full_by_id = full_by_id
        self.sets = list({strategy.set_id: strategy for strategy in candidate_pool}.values())
        self.allocations = {
            strategy.set_id: max(int(allocations.get(strategy.set_id, 0)), 0)
            for strategy in self.sets
        }
        self.current = current
        self.decision_log: list[OptimizationDecision] = []
        self.attempts = 0
        self.best_move: dict[str, object] | None = None

    def _evaluate(self, allocations: dict[str, int]) -> PortfolioEvaluation:
        """Evalua una asignacion con los objetivos de DD de la cartera actual."""
        config = self.config
        return evaluate_portfolio(
            self.sets,
            allocations,
            self.current.target_valley_dd,
            self.current.target_point_dd,
            config.max_daily_dd,
            config.enforce_point_dd,
            config.daily_dd_full_history,
        )

    def _strict_passed(self, temp_allocations: dict[str, int]) -> bool:
        """Si la asignacion sigue cumpliendo la validacion mensual estricta."""
        validation = _strict_validation_for_allocations(
            self.full_by_id,
            temp_allocations,
            target_month=self.config.target_month,
            target_valley_dd=self.current.target_valley_dd,
            target_point_dd=self.current.target_point_dd,
            enforce_point_dd=self.config.enforce_point_dd,
        )
        return bool(validation.get("passed"))

    def _correlation_rejected(self, target: RobustStrategySet, allocations: dict[str, int]) -> bool:
        """Correlacion de una estrategia que entraria nueva en la cartera."""
        config = self.config
        rejected_by_corr, _reason = violates_correlation_limits(
            target,
            self.sets,
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
        """Guarda el movimiento si gana mas y pasa DD, correlacion y 5A."""
        config = self.config
        gain = temp.total_net_profit - self.current.total_net_profit
        if gain <= 1e-9 or (self.best_move is not None and gain <= float(self.best_move["gain"])):
            return
        if _evaluation_violates_dd_limits(temp):
            return
        if not _portfolio_corr_allowed(temp, config.existing_portfolio_curves, config.max_portfolio_corr):
            return
        if not self._strict_passed(temp_allocations):
            return
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
            sets=self.sets,
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
        if _portfolio_active_count(temp_allocations) < config.minimum_active_strategies:
            return False
        if not _allocations_respect_constraints(
            self.sets,
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
            self.sets,
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
            self.sets,
            key=lambda item: score_set_for_portfolio(item, 1),
            reverse=True,
        )
        for target in ordered_targets:
            self.attempts += 1
            self._try_add(target)
            active_sources = [
                source for source in self.sets if self.allocations.get(source.set_id, 0) > 0
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
                reason="Optimizacion profunda: movimiento validado contra DD, margen, correlacion y 5A",
            )
        )

    def run(self) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
        """Itera hasta que ningun movimiento valido mejore el beneficio."""
        for iteration in range(1, self.config.max_iterations + 1):
            self._scan()
            if self.best_move is None:
                break
            self._apply_best_move(iteration)
        return self.allocations, self.current, self.decision_log, self.attempts


def _strict_monthly_deep_refine_allocations(
    candidate_pool: list[RobustStrategySet],
    full_by_id: dict[str, RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    **limits: object,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    """Refina la cartera manteniendo la validacion mensual estricta."""
    return _StrictDeepRefiner(
        candidate_pool, full_by_id, allocations, current, _StrictDeepRefineConfig(**limits)
    ).run()
