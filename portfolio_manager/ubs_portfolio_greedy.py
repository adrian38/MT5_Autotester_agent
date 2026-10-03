"""Construccion voraz de la asignacion inicial del portafolio."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _GreedyConfig:
    """Limites y parametros con los que se construye la asignacion voraz."""

    sets: list[RobustStrategySet]
    capital: float
    valley_dd_pct: float
    point_dd_pct: float
    portfolio_type: PortfolioType
    max_units_per_set: int | None = None
    max_total_units: int | None = None
    max_units_per_symbol: int | None = None
    max_sets_per_symbol: int | None = 1
    max_pair_corr: float | None = None
    max_downside_corr: float | None = None
    max_dd_overlap: float | None = None
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None
    max_portfolio_corr: float | None = None
    max_units_per_group_pct: float | None = None
    max_sets_per_group: int | None = None
    group_unit_cap_bootstrap: int = 10
    initial_allocations: dict[str, int] | None = None
    minimum_active_strategies: int | None = None
    maximum_active_strategies: int | None = None
    fixed_set_ids: Sequence[str] | None = None
    allow_fixed_reductions_for_repair: bool = False
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


class _GreedyBuilder:
    """Anade unidades de 0.01 mientras el portafolio siga dentro de los limites."""

    def __init__(self, config: _GreedyConfig) -> None:
        self.config = config
        self.sets = config.sets
        self.target_valley_dd = config.capital * config.valley_dd_pct / 100.0
        self.target_point_dd = config.capital * config.point_dd_pct / 100.0
        self.allocations = {
            strategy.set_id: max(int((config.initial_allocations or {}).get(strategy.set_id, 0)), 0)
            for strategy in self.sets
        }
        self._check_initial_allocations()
        self.current = self._evaluate(self.allocations)
        if _evaluation_violates_dd_limits(self.current) and not config.allow_fixed_reductions_for_repair:
            raise ValueError("Initial portfolio allocations violate DD limits")
        self.decision_log: list[OptimizationDecision] = []
        self.step = sum(self.allocations.values())
        self.max_steps = config.max_total_units if config.max_total_units is not None else 10000
        self.correlation_rejections = 0
        self.portfolio_curves = list(config.existing_portfolio_curves or [])
        self.fixed_ids = {str(set_id) for set_id in (config.fixed_set_ids or ())}
        self.best_candidate: dict[str, object] | None = None
        self.best_repair_candidate: dict[str, object] | None = None
        self.blocked_by_risk = False

    def _check_initial_allocations(self) -> None:
        """Rechaza de entrada una asignacion que ya incumple los limites."""
        config = self.config
        if not _allocations_respect_constraints(
            self.sets,
            self.allocations,
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
            raise ValueError("Initial portfolio allocations violate configured limits")

    def _evaluate(self, allocations: dict[str, int]) -> PortfolioEvaluation:
        """Evalua una asignacion con los objetivos de DD de esta construccion."""
        config = self.config
        return evaluate_portfolio(
            self.sets,
            allocations,
            self.target_valley_dd,
            self.target_point_dd,
            config.max_daily_dd,
            config.enforce_point_dd,
            config.daily_dd_full_history,
        )

    def _rejection(self, action: str, set_id: str, reason: str) -> OptimizationDecision:
        """Entrada de bitacora para un incremento descartado por correlacion."""
        return OptimizationDecision(
            step=self.step + 1,
            action=action,
            set_id=set_id,
            from_set_id=None,
            to_set_id=None,
            gain=0.0,
            valley_cost=0.0,
            point_cost=0.0,
            score=float("-inf"),
            portfolio_net_profit_after=self.current.total_net_profit,
            portfolio_valley_dd_after=self.current.valley_dd,
            portfolio_point_dd_after=self.current.point_dd,
            reason=reason,
        )

    def _can_add_unit(self, strategy: RobustStrategySet) -> bool:
        """Si cabe una unidad mas de esta estrategia dentro de los cupos."""
        config = self.config
        return can_add_unit(
            target_set=strategy,
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

    def _skip_strategy(self, strategy: RobustStrategySet) -> bool:
        """Descarta estrategias fijas, fuera de cupo de activas o sin hueco."""
        config = self.config
        if strategy.set_id in self.fixed_ids:
            return True
        if (
            config.maximum_active_strategies is not None
            and self.current.active_strategies >= config.maximum_active_strategies
            and self.allocations.get(strategy.set_id, 0) <= 0
        ):
            return True
        if (
            config.minimum_active_strategies is not None
            and self.current.active_strategies < config.minimum_active_strategies
            and self.allocations.get(strategy.set_id, 0) > 0
        ):
            # During repair, fill the missing strategy slots before adding
            # more risk to strategies that are already active.
            return True
        return not self._can_add_unit(strategy)

    def _rejected_by_correlation(self, strategy: RobustStrategySet) -> bool:
        """Correlacion por pares, bajista o solape de DD contra la cartera."""
        config = self.config
        rejected_by_corr, corr_reason = violates_correlation_limits(
            strategy,
            self.sets,
            self.allocations,
            config.max_pair_corr,
            config.max_downside_corr,
            config.max_dd_overlap,
        )
        if not rejected_by_corr:
            return False
        self.correlation_rejections += 1
        self.decision_log.append(self._rejection("reject_corr", strategy.set_id, corr_reason))
        return True

    def _consider_repair(
        self, strategy: RobustStrategySet, temp_allocations: dict[str, int], temp: PortfolioEvaluation
    ) -> None:
        """Guarda el incremento que mas reduce la violacion durante una reparacion."""
        current_violation = _evaluation_violation_ratio(self.current)
        temp_violation = _evaluation_violation_ratio(temp)
        if temp_violation >= current_violation - 1e-9:
            return
        repair_score = (current_violation - temp_violation) * 1_000_000_000.0
        repair_score += max(temp.total_net_profit - self.current.total_net_profit, 0.0)
        if self.best_repair_candidate is None or repair_score > float(self.best_repair_candidate["score"]):
            self.best_repair_candidate = {
                "set": strategy,
                "allocations": temp_allocations,
                "evaluation": temp,
                "score": repair_score,
                "reason": "Replacement increment reduced the DD violation",
            }

    def _rejected_by_portfolio_correlation(
        self, strategy: RobustStrategySet, temp: PortfolioEvaluation
    ) -> bool:
        """Correlacion del incremento contra las carteras ya en produccion."""
        config = self.config
        if config.max_portfolio_corr is None or not self.portfolio_curves:
            return False
        worst_portfolio_corr = max(
            curve_increment_correlation(temp.equity_curve_2020_2026, curve)
            for curve in self.portfolio_curves
        )
        if worst_portfolio_corr <= config.max_portfolio_corr:
            return False
        self.blocked_by_risk = True
        self.correlation_rejections += 1
        self.decision_log.append(
            self._rejection(
                "reject_portfolio_corr",
                strategy.set_id,
                f"portfolio_corr>{config.max_portfolio_corr:.2f}",
            )
        )
        return True

    def _consider_strategy(self, strategy: RobustStrategySet) -> None:
        """Evalua el incremento de una estrategia y lo guarda si es el mejor."""
        if self._skip_strategy(strategy) or self._rejected_by_correlation(strategy):
            return
        temp_allocations = self.allocations.copy()
        temp_allocations[strategy.set_id] += 1
        temp = self._evaluate(temp_allocations)
        if _evaluation_violates_dd_limits(temp):
            self.blocked_by_risk = True
            if self.config.allow_fixed_reductions_for_repair:
                self._consider_repair(strategy, temp_allocations, temp)
            return
        if self._rejected_by_portfolio_correlation(strategy, temp):
            return
        score = score_increment(
            self.current, temp, self.allocations[strategy.set_id], self.config.portfolio_type
        )
        if score == float("-inf"):
            return
        if self.best_candidate is None or score > float(self.best_candidate["score"]):
            self.best_candidate = {
                "set": strategy,
                "allocations": temp_allocations,
                "evaluation": temp,
                "score": score,
                "reason": "Best valid +0.01 increment",
            }

    def _best_reduction(self, current_violation: float):
        """Menor reduccion de una estrategia fija que alivia la violacion."""
        best_reduction: tuple[float, float, RobustStrategySet, dict[str, int], PortfolioEvaluation] | None = None
        for strategy in self.sets:
            if strategy.set_id not in self.fixed_ids or self.allocations.get(strategy.set_id, 0) <= 1:
                continue
            temp_allocations = self.allocations.copy()
            temp_allocations[strategy.set_id] -= 1
            temp = self._evaluate(temp_allocations)
            temp_violation = _evaluation_violation_ratio(temp)
            if temp_violation >= current_violation - 1e-9:
                continue
            choice = (temp_violation, -temp.total_net_profit, strategy, temp_allocations, temp)
            if best_reduction is None or choice[:2] < best_reduction[:2]:
                best_reduction = choice
        return best_reduction

    def _try_repair_reduction(self) -> bool:
        """Quita una unidad fija cuando no hay incremento que haga viable la reparacion."""
        config = self.config
        if not config.allow_fixed_reductions_for_repair:
            return False
        current_violation = _evaluation_violation_ratio(self.current)
        missing_required_strategy = (
            config.minimum_active_strategies is not None
            and self.current.active_strategies < config.minimum_active_strategies
        )
        if not (current_violation > 1.0 or (missing_required_strategy and self.blocked_by_risk)):
            return False
        best_reduction = self._best_reduction(current_violation)
        if best_reduction is None:
            return False
        _violation, _negative_net, reduced_set, self.allocations, self.current = best_reduction
        self.step = sum(self.allocations.values())
        self.decision_log.append(
            OptimizationDecision(
                step=len(self.decision_log) + 1,
                action="reduce_unit_for_repair",
                set_id=reduced_set.set_id,
                from_set_id=reduced_set.set_id,
                to_set_id=None,
                gain=-reduced_set.net_profit_2020_2026_001,
                valley_cost=0.0,
                point_cost=0.0,
                score=-current_violation,
                portfolio_net_profit_after=self.current.total_net_profit,
                portfolio_valley_dd_after=self.current.valley_dd,
                portfolio_point_dd_after=self.current.point_dd,
                reason="Minimum existing-lot reduction required to make portfolio repair feasible",
            )
        )
        return True

    def _accept_best_candidate(self) -> None:
        """Aplica el mejor incremento encontrado y lo anota en la bitacora."""
        best_candidate = self.best_candidate
        assert best_candidate is not None
        selected_set = best_candidate["set"]
        assert isinstance(selected_set, RobustStrategySet)
        previous = self.current
        self.allocations = best_candidate["allocations"]  # type: ignore[assignment]
        self.current = best_candidate["evaluation"]  # type: ignore[assignment]
        self.step += 1
        self.decision_log.append(
            OptimizationDecision(
                step=self.step,
                action="add_unit",
                set_id=selected_set.set_id,
                from_set_id=None,
                to_set_id=None,
                gain=self.current.total_net_profit - previous.total_net_profit,
                valley_cost=self.current.valley_dd - previous.valley_dd,
                point_cost=self.current.point_dd - previous.point_dd,
                score=float(best_candidate["score"]),
                portfolio_net_profit_after=self.current.total_net_profit,
                portfolio_valley_dd_after=self.current.valley_dd,
                portfolio_point_dd_after=self.current.point_dd,
                reason=str(best_candidate.get("reason") or "Best valid +0.01 increment"),
            )
        )

    def run(self) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
        """Itera hasta agotar los incrementos validos o el limite de pasos."""
        while self.step < self.max_steps:
            self.best_candidate = None
            self.best_repair_candidate = None
            self.blocked_by_risk = False
            for strategy in self.sets:
                self._consider_strategy(strategy)
            if self.best_candidate is None and self.best_repair_candidate is not None:
                self.best_candidate = self.best_repair_candidate
            if self.best_candidate is None and self._try_repair_reduction():
                continue
            if self.best_candidate is None:
                stop_reason = "No valid +0.01 increment found without breaking DD constraints"
                break
            self._accept_best_candidate()
        else:
            stop_reason = "Max optimizer iterations reached"
        return self.allocations, self.current, self.decision_log, stop_reason, self.correlation_rejections


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
    """Asignacion inicial del portafolio por incrementos de 0.01."""
    return _GreedyBuilder(_GreedyConfig(**locals())).run()
