from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
import re
import statistics

from portfolio_manager.mt5_report import StrategyReport, parse_report
from ubs.normalization import net_profit_normalization


@dataclass(frozen=True)
class ScoreConfig:
    min_net_profit: float = 100.0
    min_profit_factor: float = 1.20
    min_trades: int = 50
    max_drawdown_pct: float = 25.0
    min_recovery_factor: float = 1.0
    min_positive_month_ratio: float = 0.0


@dataclass(frozen=True)
class ScoreResult:
    report_path: str
    name: str
    symbol: str
    timeframe: str
    score: float
    accepted: bool
    net_profit: float
    raw_net_profit: float
    normalized_net_profit: float
    net_profit_factor: float
    net_profit_basis: str
    normalization_group: str
    profit_factor: float
    recovery_factor: float
    drawdown: float
    drawdown_pct: float
    trades: int
    positive_month_ratio: float
    max_month_concentration: float
    avg_trade: float
    sqn: float
    reasons: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=True, sort_keys=True)


def score_report_file(path: Path, config: ScoreConfig | None = None) -> ScoreResult:
    return score_report(parse_report(path), config=config)


def score_report(report: StrategyReport, config: ScoreConfig | None = None) -> ScoreResult:
    config = config or ScoreConfig()
    profits = [trade.profit_loss for trade in report.trades]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    net_profit = round(sum(profits), 2)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss else (99.0 if gross_profit else 0.0)
    drawdown = _drawdown_amount(report)
    drawdown_pct = _drawdown_pct(report)
    recovery_factor = round(net_profit / drawdown, 4) if drawdown else (99.0 if net_profit > 0 else 0.0)
    monthly_values = [value for months in report.monthly.values() for value in months.values()]
    positive_month_ratio = (
        len([value for value in monthly_values if value > 0]) / len(monthly_values)
        if monthly_values
        else 0.0
    )
    total_positive_months = sum(value for value in monthly_values if value > 0)
    max_month = max((value for value in monthly_values if value > 0), default=0.0)
    max_month_concentration = max_month / total_positive_months if total_positive_months else 1.0
    avg_trade = net_profit / len(profits) if profits else 0.0
    deviation = statistics.pstdev(profits) if len(profits) > 1 else 0.0
    sqn = math.sqrt(len(profits)) * avg_trade / deviation if deviation else 0.0
    net_profit_factor, normalization_group, net_profit_basis = net_profit_normalization(report.symbol)
    normalized_net_profit = round(net_profit * net_profit_factor, 2)

    score = _score_formula(
        net_profit=normalized_net_profit,
        profit_factor=profit_factor,
        recovery_factor=recovery_factor,
        drawdown_pct=drawdown_pct,
        trades=len(profits),
        positive_month_ratio=positive_month_ratio,
        max_month_concentration=max_month_concentration,
        sqn=sqn,
    )

    reasons = []
    if normalized_net_profit <= config.min_net_profit:
        reasons.append("net_profit")
    if profit_factor < config.min_profit_factor:
        reasons.append("profit_factor")
    if len(profits) < config.min_trades:
        reasons.append("trades")
    if drawdown_pct > config.max_drawdown_pct:
        reasons.append("drawdown_pct")
    if recovery_factor < config.min_recovery_factor:
        reasons.append("recovery_factor")
    if positive_month_ratio < config.min_positive_month_ratio:
        reasons.append("positive_month_ratio")

    return ScoreResult(
        report_path=str(report.path),
        name=report.name,
        symbol=report.symbol,
        timeframe=report.timeframe,
        score=round(score, 4),
        accepted=not reasons,
        net_profit=net_profit,
        raw_net_profit=net_profit,
        normalized_net_profit=normalized_net_profit,
        net_profit_factor=round(net_profit_factor, 4),
        net_profit_basis=net_profit_basis,
        normalization_group=normalization_group,
        profit_factor=profit_factor,
        recovery_factor=recovery_factor,
        drawdown=round(drawdown, 2),
        drawdown_pct=round(drawdown_pct, 4),
        trades=len(profits),
        positive_month_ratio=round(positive_month_ratio, 4),
        max_month_concentration=round(max_month_concentration, 4),
        avg_trade=round(avg_trade, 4),
        sqn=round(sqn, 4),
        reasons=tuple(reasons),
    )


def _score_formula(
    *,
    net_profit: float,
    profit_factor: float,
    recovery_factor: float,
    drawdown_pct: float,
    trades: int,
    positive_month_ratio: float,
    max_month_concentration: float,
    sqn: float,
) -> float:
    profit_component = min(max(net_profit, -5000.0) / 100.0, 60.0)
    pf_component = min(max(profit_factor - 1.0, -1.0) * 35.0, 70.0)
    recovery_component = min(max(recovery_factor, -5.0) * 6.0, 60.0)
    trades_component = min(trades / 100.0, 1.0) * 15.0
    monthly_component = positive_month_ratio * 35.0
    sqn_component = min(max(sqn, -5.0), 5.0) * 4.0
    dd_penalty = max(drawdown_pct, 0.0) * 1.8
    concentration_penalty = max_month_concentration * 20.0
    return (
        profit_component
        + pf_component
        + recovery_component
        + trades_component
        + monthly_component
        + sqn_component
        - dd_penalty
        - concentration_penalty
    )


def _drawdown_amount(report: StrategyReport) -> float:
    value = _first_metric(
        report,
        "Balance Drawdown Maximal",
        "Reduccion maxima del balance",
        "Reducción máxima del balance",
    )
    amount, _ = _extract_drawdown(value)
    return amount


def _drawdown_pct(report: StrategyReport) -> float:
    value = _first_metric(
        report,
        "Balance Drawdown Relative",
        "Reduccion relativa del balance",
        "Reducción relativa del balance",
    )
    match = re.search(r"([-+]?\d+(?:[.,]\d+)?)%", value)
    if match:
        return _to_float(match.group(1))
    _, pct = _extract_drawdown(
        _first_metric(
            report,
            "Balance Drawdown Maximal",
            "Reduccion maxima del balance",
            "Reducción máxima del balance",
        )
    )
    return pct


def _first_metric(report: StrategyReport, *keys: str) -> str:
    for key in keys:
        value = report.metrics.get(key)
        if value:
            return value
    normalized = {_ascii_key(key): value for key, value in report.metrics.items()}
    for key in keys:
        value = normalized.get(_ascii_key(key))
        if value:
            return value
    return ""


def _ascii_key(value: str) -> str:
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "Á": "A",
        "É": "E",
        "Í": "I",
        "Ó": "O",
        "Ú": "U",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def _extract_drawdown(value: str) -> tuple[float, float]:
    match = re.search(r"([-+]?\d+(?:[ .]\d{3})*(?:[.,]\d+)?)\s*\(([-+]?\d+(?:[.,]\d+)?)%", value)
    if not match:
        return _to_float(value), 0.0
    return _to_float(match.group(1)), _to_float(match.group(2))


def _to_float(value: object) -> float:
    cleaned = str(value).replace(" ", "").replace("%", "").replace(",", ".").strip()
    if not cleaned:
        return 0.0
    match = re.match(r"([-+]?\d+(?:\.\d+)?)", cleaned)
    return float(match.group(1)) if match else 0.0
