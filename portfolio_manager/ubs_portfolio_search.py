"""Busqueda local y multiarranque sobre una asignacion inicial."""
from __future__ import annotations

import random
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
    violates_correlation_limits,
)
from .ubs_portfolio_curves import (
    curve_increment_correlation,
)
from .ubs_portfolio_evaluate import (
    _evaluation_violates_dd_limits,
    evaluate_portfolio,
)


@dataclass
class _LocalSearchConfig:
    """Limites con los que la busqueda local puede permutar unidades."""

    target_valley_dd: float
    target_point_dd: float
    max_units_per_set: int | None = None
    max_total_units: int | None = None
    max_units_per_symbol: int | None = None
    max_sets_per_symbol: int | None = None
    max_pair_corr: float | None = None
    max_downside_corr: float | None = None
    max_dd_overlap: float | None = None
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None
    max_portfolio_corr: float | None = None
    max_units_per_group_pct: float | None = None
    max_sets_per_group: int | None = None
    group_unit_cap_bootstrap: int = 10
    max_iterations: int = 1000
    protected_set_ids: Sequence[str] | None = None
    minimum_active_strategies: int | None = None
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


class _LocalSearch:
    """Permuta unidades entre estrategias mientras suba el beneficio."""

    def __init__(
        self, sets: list[RobustStrategySet], allocations: dict[str, int],
        current: PortfolioEvaluation, config: _LocalSearchConfig,
    ) -> None:
        self.sets = sets
        self.allocations = allocations
        self.current = current
        self.config = config
        self.portfolio_curves = list(config.existing_portfolio_curves or [])
        self.protected_ids = {str(set_id) for set_id in (config.protected_set_ids or ())}
        self.decision_log: list[OptimizationDecision] = []
        self.best_move: dict[str, object] | None = None

    def _evaluate(self, allocations: dict[str, int]) -> PortfolioEvaluation:
        """Evalua una asignacion con los objetivos de DD de la busqueda."""
        config = self.config
        return evaluate_portfolio(
            self.sets,
            allocations,
            config.target_valley_dd,
            config.target_point_dd,
            config.max_daily_dd,
            config.enforce_point_dd,
            config.daily_dd_full_history,
        )

    def _swap_allowed(self, to_set: RobustStrategySet, temp_allocations: dict[str, int]) -> bool:
        """Minimo de activas, cupos, grupo y correlacion de la permuta."""
        config = self.config
        if config.minimum_active_strategies is not None:
            active_count = sum(1 for units in temp_allocations.values() if units > 0)
            if active_count < config.minimum_active_strategies:
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
            to_set,
            self.sets,
            temp_allocations,
            config.max_units_per_group_pct,
            config.group_unit_cap_bootstrap,
        ):
            return False
        if self.allocations.get(to_set.set_id, 0) > 0:
            return True
        corr_allocations = temp_allocations.copy()
        corr_allocations[to_set.set_id] = 0
        rejected_by_corr, _corr_reason = violates_correlation_limits(
            to_set,
            self.sets,
            corr_allocations,
            config.max_pair_corr,
            config.max_downside_corr,
            config.max_dd_overlap,
        )
        return not rejected_by_corr

    def _portfolio_corr_blocked(self, temp: PortfolioEvaluation) -> bool:
        """Correlacion de la cartera resultante contra las ya existentes."""
        config = self.config
        if config.max_portfolio_corr is None or not self.portfolio_curves:
            return False
        worst_portfolio_corr = max(
            curve_increment_correlation(temp.equity_curve_2020_2026, curve)
            for curve in self.portfolio_curves
        )
        return worst_portfolio_corr > config.max_portfolio_corr

    def _consider_swap(self, from_set: RobustStrategySet, to_set: RobustStrategySet) -> None:
        """Prueba mover una unidad y guarda la permuta si es la que mas gana."""
        temp_allocations = self.allocations.copy()
        temp_allocations[from_set.set_id] -= 1
        temp_allocations[to_set.set_id] += 1
        if not self._swap_allowed(to_set, temp_allocations):
            return
        temp = self._evaluate(temp_allocations)
        if _evaluation_violates_dd_limits(temp) or self._portfolio_corr_blocked(temp):
            return
        gain = temp.total_net_profit - self.current.total_net_profit
        if gain <= 0:
            return
        if self.best_move is None or gain > float(self.best_move["gain"]):
            self.best_move = {
                "from_set": from_set,
                "to_set": to_set,
                "allocations": temp_allocations,
                "evaluation": temp,
                "gain": gain,
            }

    def _movable(self, from_set: RobustStrategySet) -> bool:
        """Si se puede quitar una unidad de esta estrategia."""
        units = self.allocations.get(from_set.set_id, 0)
        if units <= 0:
            return False
        return not (from_set.set_id in self.protected_ids and units <= 1)

    def _scan(self) -> None:
        """Busca la permuta que mas sube el beneficio total."""
        self.best_move = None
        for from_set in self.sets:
            if not self._movable(from_set):
                continue
            for to_set in self.sets:
                if from_set.set_id != to_set.set_id:
                    self._consider_swap(from_set, to_set)

    def _apply_best_move(self, iteration: int) -> None:
        """Aplica la permuta elegida y la anota en la bitacora."""
        best_move = self.best_move
        assert best_move is not None
        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert isinstance(from_set, RobustStrategySet)
        assert isinstance(to_set, RobustStrategySet)
        previous = self.current
        self.allocations = best_move["allocations"]  # type: ignore[assignment]
        self.current = best_move["evaluation"]  # type: ignore[assignment]
        self.decision_log.append(
            OptimizationDecision(
                step=iteration,
                action="swap_unit",
                set_id=None,
                from_set_id=from_set.set_id,
                to_set_id=to_set.set_id,
                gain=self.current.total_net_profit - previous.total_net_profit,
                valley_cost=self.current.valley_dd - previous.valley_dd,
                point_cost=self.current.point_dd - previous.point_dd,
                score=self.current.total_net_profit - previous.total_net_profit,
                portfolio_net_profit_after=self.current.total_net_profit,
                portfolio_valley_dd_after=self.current.valley_dd,
                portfolio_point_dd_after=self.current.point_dd,
                reason="Local search improved total net profit",
            )
        )

    def run(self) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
        """Itera hasta que ninguna permuta mejore el beneficio."""
        for iteration in range(1, self.config.max_iterations + 1):
            self._scan()
            if self.best_move is None:
                break
            self._apply_best_move(iteration)
        return self.allocations, self.current, self.decision_log


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    **limits: object,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    """Mejora el beneficio permutando unidades entre estrategias."""
    config = _LocalSearchConfig(
        target_valley_dd=target_valley_dd, target_point_dd=target_point_dd, **limits
    )
    return _LocalSearch(sets, allocations, current, config).run()


@dataclass
class _MultiStartConfig:
    """Limites con los que se perturba y se vuelve a optimizar la cartera."""

    target_valley_dd: float
    target_point_dd: float
    restarts: int
    perturbations: int = 2
    max_units_per_set: int | None = None
    max_total_units: int | None = None
    max_units_per_symbol: int | None = None
    max_sets_per_symbol: int | None = None
    max_pair_corr: float | None = None
    max_downside_corr: float | None = None
    max_dd_overlap: float | None = None
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None
    max_portfolio_corr: float | None = None
    max_units_per_group_pct: float | None = None
    max_sets_per_group: int | None = None
    group_unit_cap_bootstrap: int = 10
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


class _MultiStartSearch:
    """Reinicia la busqueda desde carteras perturbadas y se queda con la mejor."""

    def __init__(
        self, sets: list[RobustStrategySet], allocations: dict[str, int],
        current: PortfolioEvaluation, config: _MultiStartConfig,
    ) -> None:
        self.sets = sets
        self.allocations = allocations
        self.current = current
        self.config = config
        self.portfolio_curves = list(config.existing_portfolio_curves or [])

    def _evaluate(self, allocations: dict[str, int]) -> PortfolioEvaluation:
        """Evalua una asignacion con los objetivos de DD de la busqueda."""
        config = self.config
        return evaluate_portfolio(
            self.sets,
            allocations,
            config.target_valley_dd,
            config.target_point_dd,
            config.max_daily_dd,
            config.enforce_point_dd,
            config.daily_dd_full_history,
        )

    def _move_allowed(
        self, target: RobustStrategySet, trial_allocations: dict[str, int],
        temp_allocations: dict[str, int],
    ) -> bool:
        """Cupos, grupo y correlacion de un movimiento de una unidad."""
        config = self.config
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
        if trial_allocations.get(target.set_id, 0) > 0:
            return True
        corr_allocations = temp_allocations.copy()
        corr_allocations[target.set_id] = 0
        rejected, _reason = violates_correlation_limits(
            target,
            self.sets,
            corr_allocations,
            config.max_pair_corr,
            config.max_downside_corr,
            config.max_dd_overlap,
        )
        return not rejected

    def _portfolio_corr_blocked(self, temp: PortfolioEvaluation) -> bool:
        """Correlacion de la cartera perturbada contra las ya existentes."""
        config = self.config
        if config.max_portfolio_corr is None or not self.portfolio_curves:
            return False
        worst = max(
            curve_increment_correlation(temp.equity_curve_2020_2026, curve)
            for curve in self.portfolio_curves
        )
        return worst > config.max_portfolio_corr

    @staticmethod
    def _perturbation_decision(
        restart: int, perturbation: int, source: RobustStrategySet, target: RobustStrategySet,
        trial: PortfolioEvaluation, temp: PortfolioEvaluation,
    ) -> OptimizationDecision:
        """Entrada de bitacora de una perturbacion aceptada."""
        return OptimizationDecision(
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

    def _perturb_once(
        self, rng: random.Random, restart: int, perturbation: int,
        trial_allocations: dict[str, int], trial: PortfolioEvaluation,
        perturb_log: list[OptimizationDecision],
    ):
        """Mueve una unidad al azar entre dos estrategias validas."""
        active = [item for item in self.sets if trial_allocations.get(item.set_id, 0) > 0]
        moves = [
            (source, target) for source in active for target in self.sets
            if source.set_id != target.set_id
        ]
        rng.shuffle(moves)
        for source, target in moves:
            temp_allocations = trial_allocations.copy()
            temp_allocations[source.set_id] -= 1
            temp_allocations[target.set_id] += 1
            if not self._move_allowed(target, trial_allocations, temp_allocations):
                continue
            temp = self._evaluate(temp_allocations)
            if _evaluation_violates_dd_limits(temp) or self._portfolio_corr_blocked(temp):
                continue
            perturb_log.append(
                self._perturbation_decision(restart, perturbation, source, target, trial, temp)
            )
            return temp_allocations, temp
        return None

    def _perturbed_start(self, restart: int):
        """Cartera de arranque de un reinicio, con su bitacora de perturbaciones."""
        rng = random.Random(104729 + restart * 7919 + len(self.sets) * 17)
        trial_allocations = self.allocations.copy()
        trial = self.current
        perturb_log: list[OptimizationDecision] = []
        for perturbation in range(self.config.perturbations):
            moved = self._perturb_once(
                rng, restart, perturbation, trial_allocations, trial, perturb_log
            )
            if moved is None:
                break
            trial_allocations, trial = moved
        return trial_allocations, trial, perturb_log

    def _local_search(self, trial_allocations: dict[str, int], trial: PortfolioEvaluation):
        """Optimizacion local sobre la cartera perturbada."""
        config = self.config
        return improve_with_local_search(
            sets=self.sets,
            allocations=trial_allocations,
            current=trial,
            target_valley_dd=config.target_valley_dd,
            target_point_dd=config.target_point_dd,
            max_units_per_set=config.max_units_per_set,
            max_total_units=config.max_total_units,
            max_units_per_symbol=config.max_units_per_symbol,
            max_sets_per_symbol=config.max_sets_per_symbol,
            max_pair_corr=config.max_pair_corr,
            max_downside_corr=config.max_downside_corr,
            max_dd_overlap=config.max_dd_overlap,
            existing_portfolio_curves=self.portfolio_curves,
            max_portfolio_corr=config.max_portfolio_corr,
            max_units_per_group_pct=config.max_units_per_group_pct,
            max_sets_per_group=config.max_sets_per_group,
            group_unit_cap_bootstrap=config.group_unit_cap_bootstrap,
            max_iterations=200,
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
        )

    def run(self) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
        """Recorre los reinicios y devuelve la mejor cartera encontrada."""
        best_allocations = self.allocations.copy()
        best = self.current
        best_log: list[OptimizationDecision] = []
        valid_restarts = 0
        for restart in range(self.config.restarts):
            trial_allocations, trial, perturb_log = self._perturbed_start(restart)
            if not perturb_log:
                continue
            valid_restarts += 1
            trial_allocations, trial, local_log = self._local_search(trial_allocations, trial)
            if trial.total_net_profit > best.total_net_profit + 1e-9:
                best_allocations = trial_allocations
                best = trial
                best_log = perturb_log + local_log
        return best_allocations, best, best_log, valid_restarts


def improve_with_multi_start_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    **limits: object,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], int]:
    """Busca desde varias carteras perturbadas y se queda con la mejor."""
    config = _MultiStartConfig(
        target_valley_dd=target_valley_dd, target_point_dd=target_point_dd, **limits
    )
    if config.restarts <= 0 or config.perturbations <= 0 or len(sets) < 2:
        return allocations, current, [], 0
    return _MultiStartSearch(sets, allocations, current, config).run()
