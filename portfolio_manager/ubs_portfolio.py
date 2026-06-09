"""UBS discrete DD-constrained portfolio builder.

This module is intentionally pure: no Tkinter and no SQLite. It receives
robustness-accepted strategy sets, merges their 2020-2024 and 2025-2026 reports
into one 2020-2026 curve, then allocates lots in integer 0.01-lot units. Every
possible increment is evaluated against the complete portfolio curve before it
can be accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from pathlib import Path
import re
import unicodedata
from typing import Callable, Iterable, Sequence

from .mt5_report import StrategyReport, parse_report


ProgressCallback = Callable[[str], None]


class PortfolioType(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class ClosedTrade:
    open_time: datetime | None
    close_time: datetime
    symbol: str
    volume: float
    profit: float
    commission: float = 0.0
    swap: float = 0.0

    @property
    def net_profit(self) -> float:
        return self.profit + self.commission + self.swap


@dataclass
class PeriodReport:
    period_name: str
    start_year: int
    end_year: int
    symbol: str
    timeframe: str
    pnl_curve_001: list[float]
    net_profit_001: float
    valley_dd_001: float
    point_dd_001: float
    profit_factor: float
    return_dd_ratio: float
    trades: int
    gross_profit: float | None = None
    gross_loss: float | None = None
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    pnl_points_001: list[tuple[datetime, float]] = field(default_factory=list)
    source_path: str = ""
    start_date: str = ""
    end_date: str = ""


@dataclass
class RobustStrategySet:
    set_id: str
    candidate_id: str
    symbol: str
    timeframe: str | None
    strategy_family: str | None
    robustness_status: str
    already_used: bool
    report_2020_2024: PeriodReport
    report_2025_2026: PeriodReport
    curve_2020_2026_001: list[float]
    net_profit_2020_2026_001: float
    valley_dd_2020_2026_001: float
    point_dd_2020_2026_001: float
    profit_factor_2020_2026: float
    return_dd_2020_2026: float
    trades_2020_2026: int
    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    curve_points_2020_2026_001: list[tuple[datetime, float]] = field(default_factory=list)


@dataclass
class PortfolioEvaluation:
    allocations: dict[str, int]
    equity_curve_2020_2026: list[float]
    total_net_profit: float
    valley_dd: float
    point_dd: float
    target_valley_dd: float
    target_point_dd: float
    valley_usage_pct: float
    point_usage_pct: float
    total_units: int
    total_lot: float
    active_strategies: int


@dataclass
class StrategyAllocation:
    set_id: str
    candidate_id: str
    symbol: str
    units: int
    lot: float
    net_profit_contribution: float
    standalone_valley_dd: float
    standalone_point_dd: float
    timeframe: str | None = None
    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    lot_size_step: float | None = None


@dataclass
class OptimizationDecision:
    step: int
    action: str
    set_id: str | None
    from_set_id: str | None
    to_set_id: str | None
    gain: float
    valley_cost: float
    point_cost: float
    score: float
    portfolio_net_profit_after: float
    portfolio_valley_dd_after: float
    portfolio_point_dd_after: float
    reason: str


@dataclass
class UnusedSetInfo:
    set_id: str
    symbol: str
    score: float
    reason: str


@dataclass(frozen=True)
class CorrelationPair:
    set_id_a: str
    set_id_b: str
    symbol_a: str
    symbol_b: str
    pearson_corr: float
    downside_corr: float
    dd_overlap: float
    observations: int


@dataclass
class PortfolioResult:
    allocations: list[StrategyAllocation]
    equity_curve_2020_2026: list[float]
    total_net_profit: float
    actual_valley_dd: float
    actual_point_dd: float
    target_valley_dd: float
    target_point_dd: float
    valley_usage_pct: float
    point_usage_pct: float
    total_lot: float
    total_units: int
    active_strategies: int
    stop_reason: str
    warnings: list[str]
    decision_log: list[OptimizationDecision]
    unused_sets: list[UnusedSetInfo] = field(default_factory=list)
    correlation_rejections: int = 0


@dataclass(frozen=True)
class PortfolioAvailability:
    robust_accepted: int
    already_used: int
    available: int
    symbols_available: int
    by_symbol: dict[str, int]


def merge_accumulated_curves(
    curve_2020_2024: list[float],
    curve_2025_2026: list[float],
) -> list[float]:
    if not curve_2020_2024:
        raise ValueError("2020-2024 curve is empty")
    if not curve_2025_2026:
        raise ValueError("2025-2026 curve is empty")
    last_value = curve_2020_2024[-1]
    return curve_2020_2024 + [last_value + value for value in curve_2025_2026[1:]]


def merge_incremental_curves(
    increments_2020_2024: list[float],
    increments_2025_2026: list[float],
) -> list[float]:
    return increments_2020_2024 + increments_2025_2026


def to_accumulated_curve(increments: list[float]) -> list[float]:
    curve = [0.0]
    total = 0.0
    for change in increments:
        total += change
        curve.append(total)
    return curve


def daily_pnl_series(strategy: RobustStrategySet) -> dict[str, float]:
    if strategy.curve_points_2020_2026_001:
        previous = 0.0
        series: dict[str, float] = {}
        for timestamp, value in strategy.curve_points_2020_2026_001:
            day = timestamp.date().isoformat()
            series[day] = series.get(day, 0.0) + (value - previous)
            previous = value
        return series

    increments = [
        current - previous
        for previous, current in zip(strategy.curve_2020_2026_001, strategy.curve_2020_2026_001[1:])
    ]
    return {str(index): value for index, value in enumerate(increments)}


def pearson_correlation(values_a: Sequence[float], values_b: Sequence[float]) -> float:
    if len(values_a) < 2 or len(values_b) < 2 or len(values_a) != len(values_b):
        return 0.0
    mean_a = sum(values_a) / len(values_a)
    mean_b = sum(values_b) / len(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    denom_a = math.sqrt(sum(value * value for value in centered_a))
    denom_b = math.sqrt(sum(value * value for value in centered_b))
    denom = denom_a * denom_b
    if denom <= 0:
        return 0.0
    return float(sum(a * b for a, b in zip(centered_a, centered_b)) / denom)


def curve_increment_correlation(curve_a: Sequence[float], curve_b: Sequence[float]) -> float:
    increments_a = [current - previous for previous, current in zip(curve_a, curve_a[1:])]
    increments_b = [current - previous for previous, current in zip(curve_b, curve_b[1:])]
    length = max(len(increments_a), len(increments_b))
    if length < 2:
        return 0.0
    padded_a = increments_a + [0.0] * (length - len(increments_a))
    padded_b = increments_b + [0.0] * (length - len(increments_b))
    return pearson_correlation(padded_a, padded_b)


def strategy_correlation_pair(strategy_a: RobustStrategySet, strategy_b: RobustStrategySet) -> CorrelationPair:
    series_a = daily_pnl_series(strategy_a)
    series_b = daily_pnl_series(strategy_b)
    keys = sorted(set(series_a) | set(series_b))
    values_a = [series_a.get(key, 0.0) for key in keys]
    values_b = [series_b.get(key, 0.0) for key in keys]
    pearson = pearson_correlation(values_a, values_b)

    downside_a: list[float] = []
    downside_b: list[float] = []
    overlap_losses = 0
    loss_days = 0
    for value_a, value_b in zip(values_a, values_b):
        if value_a < 0 or value_b < 0:
            downside_a.append(min(value_a, 0.0))
            downside_b.append(min(value_b, 0.0))
            loss_days += 1
            if value_a < 0 and value_b < 0:
                overlap_losses += 1

    downside = pearson_correlation(downside_a, downside_b)
    dd_overlap = overlap_losses / loss_days if loss_days else 0.0
    return CorrelationPair(
        set_id_a=strategy_a.set_id,
        set_id_b=strategy_b.set_id,
        symbol_a=strategy_a.symbol,
        symbol_b=strategy_b.symbol,
        pearson_corr=pearson,
        downside_corr=downside,
        dd_overlap=dd_overlap,
        observations=len(keys),
    )


def build_correlation_pairs(sets: Sequence[RobustStrategySet]) -> list[CorrelationPair]:
    pairs: list[CorrelationPair] = []
    for left_index, strategy_a in enumerate(sets):
        for strategy_b in sets[left_index + 1:]:
            pairs.append(strategy_correlation_pair(strategy_a, strategy_b))
    return pairs


def calc_valley_dd(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        max_dd = max(max_dd, peak - value)
    return float(max_dd)


def calc_point_dd(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    worst_loss = 0.0
    for previous, current in zip(equity_curve, equity_curve[1:]):
        change = current - previous
        if change < worst_loss:
            worst_loss = change
    return abs(float(worst_loss))


def extract_period_info(text: str) -> tuple[str, str, str]:
    match = re.search(
        r"([A-Z0-9]+)\s+\((\d{4}\.\d{2}\.\d{2})\s+-\s+(\d{4}\.\d{2}\.\d{2})\)",
        text,
    )
    if not match:
        raise ValueError("Period info not found")
    return match.group(1), match.group(2), match.group(3)


def build_equity_curve_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[float]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    curve = [0.0]
    total = 0.0
    for trade in ordered:
        total += trade.net_profit
        curve.append(total)
    return curve


def parse_mt5_html_report(html_path: str | Path, period_name: str) -> PeriodReport:
    report = parse_report(Path(html_path))
    return period_report_from_strategy_report(report, period_name)


def period_report_from_strategy_report(report: StrategyReport, period_name: str) -> PeriodReport:
    closed_trades = [
        ClosedTrade(
            open_time=trade.open_time,
            close_time=trade.close_time,
            symbol=report.symbol,
            volume=trade.size,
            profit=trade.profit_loss,
        )
        for trade in report.trades
    ]
    curve = build_equity_curve_from_closed_trades(closed_trades)
    pnl_points = _curve_points_from_closed_trades(closed_trades)
    metric_net = _metric_amount(report, "Total Net Profit", "Beneficio Neto")
    net_profit = curve[-1] if metric_net is None else metric_net
    _validate_curve_against_net(curve, net_profit)

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    gross_profit = _metric_amount(report, "Gross Profit", "Beneficio Bruto")
    gross_loss_amount = _metric_amount(report, "Gross Loss", "Perdidas Brutas", "Perdidas Brutas")
    if gross_profit is None or gross_loss_amount is None:
        profits = [trade.net_profit for trade in closed_trades]
        gross_profit = sum(value for value in profits if value > 0)
        gross_loss = sum(value for value in profits if value < 0)
    else:
        gross_loss = -abs(gross_loss_amount)
    profit_factor = _metric_amount(report, "Profit Factor", "Factor de Beneficio")
    if profit_factor is None:
        profit_factor = gross_profit / abs(gross_loss) if gross_loss else (float("inf") if gross_profit else 0.0)

    start_year, end_year = _period_years(report, period_name)
    return PeriodReport(
        period_name=period_name,
        start_year=start_year,
        end_year=end_year,
        symbol=report.symbol,
        timeframe=report.timeframe,
        pnl_curve_001=curve,
        net_profit_001=net_profit,
        valley_dd_001=valley_dd,
        point_dd_001=point_dd,
        profit_factor=float(profit_factor),
        return_dd_ratio=net_profit / max(valley_dd, 1.0),
        trades=len(closed_trades),
        gross_profit=float(gross_profit) if gross_profit is not None else None,
        gross_loss=float(gross_loss) if gross_loss is not None else None,
        closed_trades=closed_trades,
        pnl_points_001=pnl_points,
        source_path=str(report.path),
        start_date=report.period_start,
        end_date=report.period_end,
    )


def calc_combined_profit_factor(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> float:
    if (
        report_2020_2024.gross_profit is not None
        and report_2020_2024.gross_loss is not None
        and report_2025_2026.gross_profit is not None
        and report_2025_2026.gross_loss is not None
    ):
        gross_profit = report_2020_2024.gross_profit + report_2025_2026.gross_profit
        gross_loss = report_2020_2024.gross_loss + report_2025_2026.gross_loss
        if gross_loss == 0:
            return float("inf")
        return gross_profit / abs(gross_loss)
    return min(report_2020_2024.profit_factor, report_2025_2026.profit_factor)


def build_robust_strategy_set(
    set_id: str,
    candidate_id: str,
    symbol: str,
    timeframe: str | None,
    strategy_family: str | None,
    robustness_status: str,
    already_used: bool,
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    *,
    set_path: str = "",
    is_report_path: str = "",
    oos_report_path: str = "",
) -> RobustStrategySet:
    if _normalize_symbol(report_2020_2024.symbol) != _normalize_symbol(report_2025_2026.symbol):
        raise ValueError("Cannot merge reports with different symbols")
    _validate_period_order(report_2020_2024, report_2025_2026)

    curve_2020_2026_001 = merge_accumulated_curves(
        report_2020_2024.pnl_curve_001,
        report_2025_2026.pnl_curve_001,
    )
    curve_points = _merge_curve_points(report_2020_2024, report_2025_2026)
    if curve_points:
        curve_2020_2026_001 = [0.0] + [value for _time, value in curve_points]

    net_profit_2020_2026_001 = curve_2020_2026_001[-1]
    valley_dd_2020_2026_001 = calc_valley_dd(curve_2020_2026_001)
    point_dd_2020_2026_001 = calc_point_dd(curve_2020_2026_001)
    return_dd_2020_2026 = net_profit_2020_2026_001 / max(valley_dd_2020_2026_001, 1.0)
    trades_2020_2026 = report_2020_2024.trades + report_2025_2026.trades
    profit_factor_2020_2026 = calc_combined_profit_factor(report_2020_2024, report_2025_2026)

    return RobustStrategySet(
        set_id=str(set_id),
        candidate_id=str(candidate_id),
        symbol=_normalize_symbol(symbol or report_2020_2024.symbol),
        timeframe=timeframe or report_2020_2024.timeframe,
        strategy_family=strategy_family,
        robustness_status=robustness_status,
        already_used=already_used,
        report_2020_2024=report_2020_2024,
        report_2025_2026=report_2025_2026,
        curve_2020_2026_001=curve_2020_2026_001,
        net_profit_2020_2026_001=net_profit_2020_2026_001,
        valley_dd_2020_2026_001=valley_dd_2020_2026_001,
        point_dd_2020_2026_001=point_dd_2020_2026_001,
        profit_factor_2020_2026=profit_factor_2020_2026,
        return_dd_2020_2026=return_dd_2020_2026,
        trades_2020_2026=trades_2020_2026,
        set_path=set_path,
        is_report_path=is_report_path,
        oos_report_path=oos_report_path,
        curve_points_2020_2026_001=curve_points,
    )


def summarize_robust_rows(rows: Iterable[object], used_set_paths: Iterable[str]) -> PortfolioAvailability:
    used = {_norm_path(path) for path in used_set_paths}
    robust_accepted = 0
    already_used = 0
    by_symbol: dict[str, int] = {}
    seen: set[str] = set()
    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if not set_path or set_path in seen:
            continue
        seen.add(set_path)
        robust_accepted += 1
        symbol = _normalize_symbol(str(_row_value(row, "target_symbol", "symbol", default="")))
        if _norm_path(set_path) in used:
            already_used += 1
            continue
        by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
    available = sum(by_symbol.values())
    return PortfolioAvailability(
        robust_accepted=robust_accepted,
        already_used=already_used,
        available=available,
        symbols_available=len(by_symbol),
        by_symbol=dict(sorted(by_symbol.items())),
    )


def load_robust_sets_from_rows(
    rows: Sequence[object],
    used_set_paths: Iterable[str],
    *,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[RobustStrategySet], list[str]]:
    warnings: list[str] = []
    used = {_norm_path(path) for path in used_set_paths}
    latest_by_stem: dict[str, object] = {}

    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if not set_path:
            continue
        stem = _logical_stem(set_path)
        current = latest_by_stem.get(stem)
        if current is None or _row_int(row, "candidate_id") > _row_int(current, "candidate_id"):
            latest_by_stem[stem] = row

    loaded: list[RobustStrategySet] = []
    skipped_missing = 0
    skipped_parse = 0
    candidates = list(latest_by_stem.values())
    for index, row in enumerate(candidates, start=1):
        set_path = str(_row_value(row, "set_path", default=""))
        if _norm_path(set_path) in used:
            continue
        if progress:
            progress(f"Analizando set robusto {index}/{len(candidates)}")
        is_path = Path(str(_row_value(row, "is_report_path", "report_path", default="")))
        oos_path = Path(str(_row_value(row, "oos_report_path", "robust_report_path", default="")))
        if not is_path.is_file() or not oos_path.is_file():
            skipped_missing += 1
            continue
        try:
            is_period = period_report_from_strategy_report(parse(is_path), "2020_2024")
            oos_period = period_report_from_strategy_report(parse(oos_path), "2025_2026")
            target_symbol = str(_row_value(row, "target_symbol", "symbol", default=is_period.symbol))
            loaded.append(
                build_robust_strategy_set(
                    set_id=set_path,
                    candidate_id=str(_row_value(row, "candidate_id", "id", default=set_path)),
                    symbol=target_symbol,
                    timeframe=str(_row_value(row, "period", "timeframe", default=is_period.timeframe)),
                    strategy_family=str(_row_value(row, "family", "strategy_family", default="")),
                    robustness_status="accepted",
                    already_used=False,
                    report_2020_2024=is_period,
                    report_2025_2026=oos_period,
                    set_path=set_path,
                    is_report_path=str(is_path),
                    oos_report_path=str(oos_path),
                )
            )
        except Exception:
            skipped_parse += 1
            continue

    if skipped_missing:
        warnings.append(f"{skipped_missing} candidato(s) omitido(s): faltan reportes 2020-2024 o 2025-2026.")
    if skipped_parse:
        warnings.append(f"{skipped_parse} candidato(s) omitido(s): reporte ilegible o curva invalida.")
    return loaded, warnings


def filter_eligible_sets(
    sets: list[RobustStrategySet],
    min_trades_2020_2026: int = 100,
) -> list[RobustStrategySet]:
    eligible: list[RobustStrategySet] = []
    for strategy in sets:
        if strategy.robustness_status != "accepted":
            continue
        if strategy.already_used:
            continue
        if not strategy.curve_2020_2026_001:
            continue
        if strategy.trades_2020_2026 < min_trades_2020_2026:
            continue
        if strategy.net_profit_2020_2026_001 <= 0:
            continue
        eligible.append(strategy)
    return eligible


def score_set_for_portfolio(
    strategy: RobustStrategySet,
    min_trades_2020_2026: int = 100,
) -> float:
    profit_score = max(strategy.net_profit_2020_2026_001, 0.0)
    pf_score = min(max(strategy.profit_factor_2020_2026, 1.0), 3.0)
    return_dd_score = max(strategy.return_dd_2020_2026, 0.1)
    trades_confidence = min(1.0, strategy.trades_2020_2026 / max(min_trades_2020_2026, 1))
    dd_penalty = max(strategy.valley_dd_2020_2026_001, 1.0)
    return float((profit_score * pf_score * return_dd_score * trades_confidence) / dd_penalty)


def select_top_k_per_symbol(
    sets: list[RobustStrategySet],
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    *,
    min_trades_2020_2026: int = 100,
) -> list[RobustStrategySet]:
    grouped: dict[str, list[RobustStrategySet]] = {}
    for strategy in sets:
        grouped.setdefault(strategy.symbol, []).append(strategy)

    selected: list[RobustStrategySet] = []
    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
            reverse=True,
        )
        selected.extend(ordered[:top_k_per_symbol])

    selected = sorted(
        selected,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )
    if max_total_candidates is not None:
        selected = selected[:max_total_candidates]
    return selected


def evaluate_portfolio(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    target_valley_dd: float,
    target_point_dd: float,
) -> PortfolioEvaluation:
    active_sets = [strategy for strategy in sets if allocations.get(strategy.set_id, 0) > 0]
    if not active_sets:
        return PortfolioEvaluation(
            allocations=allocations.copy(),
            equity_curve_2020_2026=[0.0],
            total_net_profit=0.0,
            valley_dd=0.0,
            point_dd=0.0,
            target_valley_dd=target_valley_dd,
            target_point_dd=target_point_dd,
            valley_usage_pct=0.0,
            point_usage_pct=0.0,
            total_units=0,
            total_lot=0.0,
            active_strategies=0,
        )

    if all(strategy.curve_points_2020_2026_001 for strategy in active_sets):
        portfolio_curve = _evaluate_portfolio_on_time_axis(active_sets, allocations)
    else:
        length = len(active_sets[0].curve_2020_2026_001)
        for strategy in active_sets:
            if len(strategy.curve_2020_2026_001) != length:
                raise ValueError("All 2020-2026 curves must have the same length")
        portfolio_curve = [0.0] * length
        for strategy in active_sets:
            units = allocations[strategy.set_id]
            for index, value in enumerate(strategy.curve_2020_2026_001):
                portfolio_curve[index] += value * units

    total_net_profit = portfolio_curve[-1]
    valley_dd = calc_valley_dd(portfolio_curve)
    point_dd = calc_point_dd(portfolio_curve)
    return PortfolioEvaluation(
        allocations=allocations.copy(),
        equity_curve_2020_2026=portfolio_curve,
        total_net_profit=total_net_profit,
        valley_dd=valley_dd,
        point_dd=point_dd,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=valley_dd / target_valley_dd * 100 if target_valley_dd > 0 else 0.0,
        point_usage_pct=point_dd / target_point_dd * 100 if target_point_dd > 0 else 0.0,
        total_units=sum(max(value, 0) for value in allocations.values()),
        total_lot=sum(max(value, 0) for value in allocations.values()) * 0.01,
        active_strategies=sum(1 for value in allocations.values() if value > 0),
    )


def can_add_unit(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
) -> bool:
    current_units = allocations.get(target_set.set_id, 0)
    if max_units_per_set is not None and current_units >= max_units_per_set:
        return False
    if max_total_units is not None and sum(allocations.values()) + 1 > max_total_units:
        return False
    if max_units_per_symbol is not None:
        symbol_units = sum(
            allocations.get(strategy.set_id, 0)
            for strategy in sets
            if strategy.symbol == target_set.symbol
        )
        if symbol_units + 1 > max_units_per_symbol:
            return False
    if max_sets_per_symbol is not None:
        active_same_symbol = sum(
            1
            for strategy in sets
            if strategy.symbol == target_set.symbol and allocations.get(strategy.set_id, 0) > 0
        )
        if current_units == 0 and active_same_symbol >= max_sets_per_symbol:
            return False
    return True


def violates_correlation_limits(
    target_set: RobustStrategySet,
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_pair_corr: float | None,
    max_downside_corr: float | None,
    max_dd_overlap: float | None,
) -> tuple[bool, str]:
    if allocations.get(target_set.set_id, 0) > 0:
        return False, ""
    if max_pair_corr is None and max_downside_corr is None and max_dd_overlap is None:
        return False, ""

    for active in sets:
        if active.set_id == target_set.set_id or allocations.get(active.set_id, 0) <= 0:
            continue
        pair = strategy_correlation_pair(target_set, active)
        if max_pair_corr is not None and pair.pearson_corr > max_pair_corr:
            return True, f"pair_corr>{max_pair_corr:.2f} vs {Path(active.set_id).name}"
        if max_downside_corr is not None and pair.downside_corr > max_downside_corr:
            return True, f"downside_corr>{max_downside_corr:.2f} vs {Path(active.set_id).name}"
        if max_dd_overlap is not None and pair.dd_overlap > max_dd_overlap:
            return True, f"dd_overlap>{max_dd_overlap:.2f} vs {Path(active.set_id).name}"
    return False, ""


def _allocations_respect_constraints(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    max_units_per_set: int | None,
    max_total_units: int | None,
    max_units_per_symbol: int | None,
    max_sets_per_symbol: int | None,
) -> bool:
    total_units = 0
    units_by_symbol: dict[str, int] = {}
    active_sets_by_symbol: dict[str, int] = {}

    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        total_units += units
        if max_units_per_set is not None and units > max_units_per_set:
            return False
        if units <= 0:
            continue
        units_by_symbol[strategy.symbol] = units_by_symbol.get(strategy.symbol, 0) + units
        active_sets_by_symbol[strategy.symbol] = active_sets_by_symbol.get(strategy.symbol, 0) + 1

    if max_total_units is not None and total_units > max_total_units:
        return False
    if max_units_per_symbol is not None:
        for units in units_by_symbol.values():
            if units > max_units_per_symbol:
                return False
    if max_sets_per_symbol is not None:
        for count in active_sets_by_symbol.values():
            if count > max_sets_per_symbol:
                return False
    return True


def score_increment(
    current: PortfolioEvaluation,
    temp: PortfolioEvaluation,
    current_units_for_set: int,
    portfolio_type: PortfolioType,
) -> float:
    gain = temp.total_net_profit - current.total_net_profit
    if gain <= 0:
        return float("-inf")

    valley_cost = temp.valley_dd - current.valley_dd
    point_cost = temp.point_dd - current.point_dd
    epsilon = 1e-9
    valley_cost_pct = max(valley_cost, 0.0) / max(temp.target_valley_dd, epsilon)
    point_cost_pct = max(point_cost, 0.0) / max(temp.target_point_dd, epsilon)
    risk_cost = max(valley_cost_pct, point_cost_pct, epsilon)

    if valley_cost < 0 and point_cost <= 0:
        base_score = gain * 10.0 + abs(valley_cost)
    elif valley_cost <= 0 and point_cost <= 0:
        base_score = gain * 5.0
    else:
        if portfolio_type == PortfolioType.CONSERVATIVE:
            concentration_penalty = 1.0 + current_units_for_set * 0.30
            base_score = gain / risk_cost
        elif portfolio_type == PortfolioType.BALANCED:
            concentration_penalty = 1.0 + current_units_for_set * 0.15
            base_score = gain / risk_cost
        elif portfolio_type == PortfolioType.AGGRESSIVE:
            concentration_penalty = 1.0 + current_units_for_set * 0.05
            base_score = gain * 0.70 + (gain / risk_cost) * 0.30
        else:
            concentration_penalty = 1.0 + current_units_for_set * 0.15
            base_score = gain / risk_cost
        base_score = base_score / concentration_penalty

    if temp.point_usage_pct > 95:
        base_score *= 0.70
    if temp.valley_usage_pct > 98:
        base_score *= 0.85
    return float(base_score)


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
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision], str, int]:
    target_valley_dd = capital * valley_dd_pct / 100.0
    target_point_dd = capital * point_dd_pct / 100.0
    allocations = {strategy.set_id: 0 for strategy in sets}
    current = evaluate_portfolio(sets, allocations, target_valley_dd, target_point_dd)
    decision_log: list[OptimizationDecision] = []
    step = 0
    max_steps = max_total_units if max_total_units is not None else 10000
    correlation_rejections = 0
    portfolio_curves = list(existing_portfolio_curves or [])

    while step < max_steps:
        best_candidate: dict[str, object] | None = None
        for strategy in sets:
            if not can_add_unit(
                target_set=strategy,
                sets=sets,
                allocations=allocations,
                max_units_per_set=max_units_per_set,
                max_total_units=max_total_units,
                max_units_per_symbol=max_units_per_symbol,
                max_sets_per_symbol=max_sets_per_symbol,
            ):
                continue
            rejected_by_corr, corr_reason = violates_correlation_limits(
                strategy,
                sets,
                allocations,
                max_pair_corr,
                max_downside_corr,
                max_dd_overlap,
            )
            if rejected_by_corr:
                correlation_rejections += 1
                decision_log.append(
                    OptimizationDecision(
                        step=step + 1,
                        action="reject_corr",
                        set_id=strategy.set_id,
                        from_set_id=None,
                        to_set_id=None,
                        gain=0.0,
                        valley_cost=0.0,
                        point_cost=0.0,
                        score=float("-inf"),
                        portfolio_net_profit_after=current.total_net_profit,
                        portfolio_valley_dd_after=current.valley_dd,
                        portfolio_point_dd_after=current.point_dd,
                        reason=corr_reason,
                    )
                )
                continue
            temp_allocations = allocations.copy()
            temp_allocations[strategy.set_id] += 1
            temp = evaluate_portfolio(sets, temp_allocations, target_valley_dd, target_point_dd)
            if temp.valley_dd > target_valley_dd or temp.point_dd > target_point_dd:
                continue
            if max_portfolio_corr is not None and portfolio_curves:
                worst_portfolio_corr = max(
                    curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                    for curve in portfolio_curves
                )
                if worst_portfolio_corr > max_portfolio_corr:
                    correlation_rejections += 1
                    decision_log.append(
                        OptimizationDecision(
                            step=step + 1,
                            action="reject_portfolio_corr",
                            set_id=strategy.set_id,
                            from_set_id=None,
                            to_set_id=None,
                            gain=0.0,
                            valley_cost=0.0,
                            point_cost=0.0,
                            score=float("-inf"),
                            portfolio_net_profit_after=current.total_net_profit,
                            portfolio_valley_dd_after=current.valley_dd,
                            portfolio_point_dd_after=current.point_dd,
                            reason=f"portfolio_corr>{max_portfolio_corr:.2f}",
                        )
                    )
                    continue
            score = score_increment(current, temp, allocations[strategy.set_id], portfolio_type)
            if score == float("-inf"):
                continue
            if best_candidate is None or score > float(best_candidate["score"]):
                best_candidate = {
                    "set": strategy,
                    "allocations": temp_allocations,
                    "evaluation": temp,
                    "score": score,
                }

        if best_candidate is None:
            stop_reason = "No valid +0.01 increment found without breaking DD constraints"
            break

        selected_set = best_candidate["set"]
        assert isinstance(selected_set, RobustStrategySet)
        previous = current
        allocations = best_candidate["allocations"]  # type: ignore[assignment]
        current = best_candidate["evaluation"]  # type: ignore[assignment]
        step += 1
        decision_log.append(
            OptimizationDecision(
                step=step,
                action="add_unit",
                set_id=selected_set.set_id,
                from_set_id=None,
                to_set_id=None,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=float(best_candidate["score"]),
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Best valid +0.01 increment",
            )
        )
    else:
        stop_reason = "Max optimizer iterations reached"

    return allocations, current, decision_log, stop_reason, correlation_rejections


def improve_with_local_search(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    current: PortfolioEvaluation,
    target_valley_dd: float,
    target_point_dd: float,
    max_units_per_set: int | None = None,
    max_total_units: int | None = None,
    max_units_per_symbol: int | None = None,
    max_sets_per_symbol: int | None = None,
    max_pair_corr: float | None = None,
    max_downside_corr: float | None = None,
    max_dd_overlap: float | None = None,
    existing_portfolio_curves: Sequence[Sequence[float]] | None = None,
    max_portfolio_corr: float | None = None,
    max_iterations: int = 1000,
) -> tuple[dict[str, int], PortfolioEvaluation, list[OptimizationDecision]]:
    decision_log: list[OptimizationDecision] = []
    iteration = 0
    portfolio_curves = list(existing_portfolio_curves or [])
    while iteration < max_iterations:
        iteration += 1
        best_move: dict[str, object] | None = None
        for from_set in sets:
            if allocations.get(from_set.set_id, 0) <= 0:
                continue
            for to_set in sets:
                if from_set.set_id == to_set.set_id:
                    continue
                temp_allocations = allocations.copy()
                temp_allocations[from_set.set_id] -= 1
                temp_allocations[to_set.set_id] += 1
                if not _allocations_respect_constraints(
                    sets,
                    temp_allocations,
                    max_units_per_set,
                    max_total_units,
                    max_units_per_symbol,
                    max_sets_per_symbol,
                ):
                    continue
                if allocations.get(to_set.set_id, 0) <= 0:
                    corr_allocations = temp_allocations.copy()
                    corr_allocations[to_set.set_id] = 0
                    rejected_by_corr, _corr_reason = violates_correlation_limits(
                        to_set,
                        sets,
                        corr_allocations,
                        max_pair_corr,
                        max_downside_corr,
                        max_dd_overlap,
                    )
                    if rejected_by_corr:
                        continue
                temp = evaluate_portfolio(sets, temp_allocations, target_valley_dd, target_point_dd)
                if temp.valley_dd > target_valley_dd or temp.point_dd > target_point_dd:
                    continue
                if max_portfolio_corr is not None and portfolio_curves:
                    worst_portfolio_corr = max(
                        curve_increment_correlation(temp.equity_curve_2020_2026, curve)
                        for curve in portfolio_curves
                    )
                    if worst_portfolio_corr > max_portfolio_corr:
                        continue
                gain = temp.total_net_profit - current.total_net_profit
                if gain <= 0:
                    continue
                if best_move is None or gain > float(best_move["gain"]):
                    best_move = {
                        "from_set": from_set,
                        "to_set": to_set,
                        "allocations": temp_allocations,
                        "evaluation": temp,
                        "gain": gain,
                    }

        if best_move is None:
            break

        from_set = best_move["from_set"]
        to_set = best_move["to_set"]
        assert isinstance(from_set, RobustStrategySet)
        assert isinstance(to_set, RobustStrategySet)
        previous = current
        allocations = best_move["allocations"]  # type: ignore[assignment]
        current = best_move["evaluation"]  # type: ignore[assignment]
        decision_log.append(
            OptimizationDecision(
                step=iteration,
                action="swap_unit",
                set_id=None,
                from_set_id=from_set.set_id,
                to_set_id=to_set.set_id,
                gain=current.total_net_profit - previous.total_net_profit,
                valley_cost=current.valley_dd - previous.valley_dd,
                point_cost=current.point_dd - previous.point_dd,
                score=current.total_net_profit - previous.total_net_profit,
                portfolio_net_profit_after=current.total_net_profit,
                portfolio_valley_dd_after=current.valley_dd,
                portfolio_point_dd_after=current.point_dd,
                reason="Local search improved total net profit",
            )
        )
    return allocations, current, decision_log


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
) -> PortfolioResult:
    target_valley_dd = capital * valley_dd_pct / 100.0
    target_point_dd = capital * point_dd_pct / 100.0
    eligible = filter_eligible_sets(raw_sets, min_trades_2020_2026)
    if not eligible:
        raise ValueError("No eligible robust sets found")

    selected = select_top_k_per_symbol(
        eligible,
        top_k_per_symbol=top_k_per_symbol,
        max_total_candidates=max_total_candidates,
        min_trades_2020_2026=min_trades_2020_2026,
    )
    allocations, current, greedy_log, stop_reason, correlation_rejections = build_portfolio_greedy(
        sets=selected,
        capital=capital,
        valley_dd_pct=valley_dd_pct,
        point_dd_pct=point_dd_pct,
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
    )

    local_log: list[OptimizationDecision] = []
    if run_local_search:
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
        )

    executable_allocations, executable_steps = _execution_plan_allocations(selected, allocations, capital)
    execution_adjustments = {
        set_id: executable_allocations[set_id]
        for set_id, units in allocations.items()
        if units > 0 and executable_allocations.get(set_id, 0) != units
    }
    if execution_adjustments:
        current = evaluate_portfolio(selected, executable_allocations, target_valley_dd, target_point_dd)
        allocations = executable_allocations

    if current.valley_dd > target_valley_dd:
        raise ValueError("Final portfolio violates valley DD")
    if current.point_dd > target_point_dd:
        raise ValueError("Final portfolio violates point DD")

    result_allocations: list[StrategyAllocation] = []
    for strategy in selected:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            continue
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
                lot_size_step=float(executable_steps.get(strategy.set_id, _lot_size_step(capital, units) or 0)),
            )
        )
    result_allocations.sort(key=lambda item: (item.units, item.net_profit_contribution), reverse=True)

    warnings: list[str] = []
    if current.valley_usage_pct < 70:
        warnings.append(
            "Valley DD usage is below 70%. This can be acceptable if no efficient increments remained."
        )
    if current.point_usage_pct > 95:
        warnings.append("Point DD usage is above 95%. Portfolio is close to point DD limit.")
    if not result_allocations:
        warnings.append("No eligible robust sets found.")
    if execution_adjustments:
        warnings.append(
            "Lots were rounded down to match integer LotPerBalance_step export values."
        )
    if correlation_rejections:
        warnings.append(f"{correlation_rejections} increment candidate(s) rejected by correlation limits.")

    unused_sets = _build_unused_sets(raw_sets, eligible, selected, allocations, min_trades_2020_2026)
    return PortfolioResult(
        allocations=result_allocations,
        equity_curve_2020_2026=current.equity_curve_2020_2026,
        total_net_profit=current.total_net_profit,
        actual_valley_dd=current.valley_dd,
        actual_point_dd=current.point_dd,
        target_valley_dd=target_valley_dd,
        target_point_dd=target_point_dd,
        valley_usage_pct=current.valley_usage_pct,
        point_usage_pct=current.point_usage_pct,
        total_lot=current.total_lot,
        total_units=current.total_units,
        active_strategies=current.active_strategies,
        stop_reason=stop_reason,
        warnings=warnings,
        decision_log=greedy_log + local_log,
        unused_sets=unused_sets,
        correlation_rejections=correlation_rejections,
    )


def set_current_value(text: str, key: str, value: object) -> tuple[str, bool]:
    out: list[str] = []
    found = False
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(";"):
            lhs, rhs = line.split("=", 1)
            if lhs.strip() == key:
                if "||" in rhs:
                    parts = rhs.split("||")
                    parts[0] = str(value)
                    rhs = "||".join(parts)
                else:
                    rhs = str(value)
                line = f"{lhs}={rhs}"
                found = True
        out.append(line)
    return "\n".join(out), found


def apply_portfolio_lot_text(text: str, lot_size_step: float) -> tuple[str, int, bool]:
    step_int = max(1, int(math.ceil(lot_size_step)))
    text, _ = set_current_value(text, "Risk", 2)
    text, found_step = set_current_value(text, "LotPerBalance_step", step_int)
    return text, step_int, found_step


def execution_units_from_step(capital: float, lot_size_step: float | int | None) -> int:
    if lot_size_step is None:
        return 0
    step_int = max(1, int(math.ceil(float(lot_size_step))))
    return int(math.floor(capital / step_int)) if capital > 0 else 0


def _curve_points_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[tuple[datetime, float]]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    total = 0.0
    points: list[tuple[datetime, float]] = []
    for trade in ordered:
        total += trade.net_profit
        points.append((trade.close_time, total))
    return points


def _merge_curve_points(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> list[tuple[datetime, float]]:
    if not report_2020_2024.pnl_points_001 and not report_2025_2026.pnl_points_001:
        return []
    last_value = report_2020_2024.pnl_curve_001[-1] if report_2020_2024.pnl_curve_001 else 0.0
    points = list(report_2020_2024.pnl_points_001)
    points.extend((timestamp, last_value + value) for timestamp, value in report_2025_2026.pnl_points_001)
    return sorted(points, key=lambda item: item[0])


def _evaluate_portfolio_on_time_axis(
    active_sets: list[RobustStrategySet],
    allocations: dict[str, int],
) -> list[float]:
    events: list[tuple[datetime, str, int, float]] = []
    for strategy in active_sets:
        previous_value = 0.0
        for index, (timestamp, value) in enumerate(strategy.curve_points_2020_2026_001):
            events.append((timestamp, strategy.set_id, index, (value - previous_value) * allocations[strategy.set_id]))
            previous_value = value

    if not events:
        return [0.0]
    curve = [0.0]
    total = 0.0
    for _timestamp, _set_id, _index, change in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        total += change
        curve.append(total)
    return curve


def _metric_amount(report: StrategyReport, *keys: str) -> float | None:
    value = _first_metric(report, *keys)
    if value == "":
        return None
    return _to_float(value)


def _first_metric(report: StrategyReport, *keys: str) -> str:
    normalized = {_ascii_text(key): value for key, value in report.metrics.items()}
    for key in keys:
        value = report.metrics.get(key)
        if value:
            return value
        value = normalized.get(_ascii_text(key))
        if value:
            return value
    return ""


def _validate_curve_against_net(curve: list[float], html_net_profit: float) -> None:
    curve_net_profit = curve[-1] if curve else 0.0
    difference = abs(curve_net_profit - html_net_profit)
    tolerance = max(1.0, abs(html_net_profit) * 0.01)
    if difference > tolerance:
        raise ValueError("Parsed trade curve net profit differs from HTML net profit")


def _period_years(report: StrategyReport, period_name: str) -> tuple[int, int]:
    dates = [_parse_report_date(report.period_start), _parse_report_date(report.period_end)]
    if dates[0] and dates[1]:
        return dates[0].year, dates[1].year
    match = re.search(r"(\d{4})[_-](\d{4})", period_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    year = dates[0].year if dates[0] else 0
    return year, year


def _validate_period_order(report_2020_2024: PeriodReport, report_2025_2026: PeriodReport) -> None:
    first_end = _parse_report_date(report_2020_2024.end_date)
    second_start = _parse_report_date(report_2025_2026.start_date)
    if first_end and second_start and first_end >= second_start:
        raise ValueError("First report period must end before second report period starts")


def _parse_report_date(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _to_float(value: str) -> float:
    text = str(value or "").split("(")[0].strip()
    text = text.replace(" ", "").replace("%", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def _ascii_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_symbol(symbol: str) -> str:
    value = (symbol or "").strip()
    if value.startswith("."):
        return value.upper()
    return re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", value).upper()


def _logical_stem(set_path: str) -> str:
    stem = Path(set_path).stem
    return re.sub(r"^robust_\d{6}_", "", stem)


def _norm_path(value: str) -> str:
    try:
        return str(Path(value)).casefold()
    except (TypeError, ValueError):
        return str(value or "").casefold()


def _row_value(row: object, *keys: str, default: object = "") -> object:
    row_keys: set[str] | None = None
    try:
        row_keys = {str(key) for key in row.keys()}  # type: ignore[attr-defined]
    except Exception:
        row_keys = None
    for key in keys:
        if row_keys is not None and key not in row_keys:
            continue
        try:
            return row[key]  # type: ignore[index]
        except Exception:
            pass
        try:
            return getattr(row, key)
        except Exception:
            pass
    return default


def _row_int(row: object, key: str) -> int:
    try:
        return int(_row_value(row, key, default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _lot_size_step(capital: float, units: int) -> float | None:
    if units <= 0:
        return None
    return float(_step_for_max_units(capital, units))


def _execution_plan_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    capital: float,
) -> tuple[dict[str, int], dict[str, int]]:
    executable = allocations.copy()
    steps: dict[str, int] = {}
    for strategy in sets:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            executable[strategy.set_id] = 0
            continue
        step = _step_for_max_units(capital, units)
        executable[strategy.set_id] = execution_units_from_step(capital, step)
        steps[strategy.set_id] = step
    return executable, steps


def _step_for_max_units(capital: float, units: int) -> int:
    if capital <= 0 or units <= 0:
        return 1
    return max(1, int(math.floor(capital / (units + 1))) + 1)


def _build_unused_sets(
    raw_sets: list[RobustStrategySet],
    eligible: list[RobustStrategySet],
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    min_trades_2020_2026: int,
) -> list[UnusedSetInfo]:
    eligible_ids = {strategy.set_id for strategy in eligible}
    selected_ids = {strategy.set_id for strategy in selected}
    unused: list[UnusedSetInfo] = []
    for strategy in raw_sets:
        reason = ""
        if strategy.robustness_status != "accepted":
            reason = "not_accepted"
        elif strategy.already_used:
            reason = "already_used"
        elif strategy.trades_2020_2026 < min_trades_2020_2026:
            reason = "below_min_trades"
        elif strategy.net_profit_2020_2026_001 <= 0:
            reason = "non_positive_net_profit"
        elif strategy.set_id not in eligible_ids:
            reason = "not_eligible"
        elif strategy.set_id not in selected_ids:
            reason = "not_selected_top_k"
        elif allocations.get(strategy.set_id, 0) <= 0:
            reason = "received_zero_units"
        if reason:
            unused.append(
                UnusedSetInfo(
                    set_id=strategy.set_id,
                    symbol=strategy.symbol,
                    score=score_set_for_portfolio(strategy, min_trades_2020_2026),
                    reason=reason,
                )
            )
    return sorted(unused, key=lambda item: (item.reason, -item.score, item.symbol))
