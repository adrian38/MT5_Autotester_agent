"""Metricas derivadas del reporte y remuestreo usados por la puntuacion."""
from __future__ import annotations

from pathlib import Path
import hashlib
import math
import random
import re
import statistics
from types import SimpleNamespace

from dataclasses import dataclass

from portfolio_manager.mt5_report import StrategyReport, parse_report_metrics
from ubs.normalization import net_profit_normalization


GENERALIZATION_BOOTSTRAP_REPS = 2000
GENERALIZATION_BOOTSTRAP_MEAN_BLOCK = 5.0


def _trade_curve_stability(profits: list[float]) -> float | None:
    """R-squared of cumulative trade P/L against trade order."""

    if len(profits) < 2:
        return None
    cumulative: list[float] = []
    running = 0.0
    for profit in profits:
        running += float(profit)
        cumulative.append(running)
    count = len(cumulative)
    mean_x = (count - 1) / 2.0
    mean_y = sum(cumulative) / count
    variance_x = sum((index - mean_x) ** 2 for index in range(count))
    variance_y = sum((value - mean_y) ** 2 for value in cumulative)
    if variance_x <= 0 or variance_y <= 0:
        return 0.0
    covariance = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(cumulative)
    )
    return min(max((covariance * covariance) / (variance_x * variance_y), 0.0), 1.0)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(max(float(quantile), 0.0), 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _generalization_bootstrap(
    profits: list[float],
    *,
    reps: int = GENERALIZATION_BOOTSTRAP_REPS,
    mean_block: float = GENERALIZATION_BOOTSTRAP_MEAN_BLOCK,
) -> dict[str, float | int]:
    """Deterministic circular stationary-block bootstrap over trade P/L."""

    values = [float(value) for value in profits]
    count = len(values)
    if count <= 0 or reps <= 0 or mean_block <= 0:
        return {}
    positive = [max(value, 0.0) for value in values]
    negative = [max(-value, 0.0) for value in values]
    positive_prefix = [0.0]
    negative_prefix = [0.0]
    for value in positive * 2:
        positive_prefix.append(positive_prefix[-1] + value)
    for value in negative * 2:
        negative_prefix.append(negative_prefix[-1] + value)
    seed_payload = ",".join(f"{value:.10g}" for value in values).encode("ascii", "replace")
    seed = int.from_bytes(hashlib.sha256(seed_payload).digest()[:8], "big")
    rng = random.Random(seed)
    restart_probability = min(max(1.0 / mean_block, 1e-9), 1.0)
    log_continue = math.log1p(-restart_probability) if restart_probability < 1.0 else None
    nets: list[float] = []
    profit_factors: list[float] = []
    positive_nets = 0
    for _ in range(int(reps)):
        sampled = 0
        gross_profit = 0.0
        gross_loss = 0.0
        while sampled < count:
            if log_continue is None:
                block_length = 1
            else:
                block_length = int(math.log1p(-rng.random()) / log_continue) + 1
            block_length = min(block_length, count - sampled)
            start = rng.randrange(count)
            gross_profit += positive_prefix[start + block_length] - positive_prefix[start]
            gross_loss += negative_prefix[start + block_length] - negative_prefix[start]
            sampled += block_length
        net = gross_profit - gross_loss
        nets.append(net)
        if net > 0:
            positive_nets += 1
        profit_factors.append(gross_profit / gross_loss if gross_loss > 0 else 99.0)
    return {
        "reps": int(reps),
        "mean_block": float(mean_block),
        "net_positive_probability": positive_nets / float(reps),
        "net_p05": _percentile(nets, 0.05),
        "pf_p05": _percentile(profit_factors, 0.05),
    }


def equity_drawdown_from_report_file(path: Path | str) -> tuple[float | None, float | None]:
    """Equity drawdown of an already-scored report, without reparsing its trades.

    Rows scored before the risk-adjusted route existed carry no equity
    drawdown: the field is younger than their ``metrics_json``. Reading the
    Results block back recovers that evidence from the report on disk, so the
    rule can be applied to them without an MT5 run.
    """

    return _equity_drawdown(SimpleNamespace(metrics=parse_report_metrics(Path(path))))


def _equity_drawdown(report: StrategyReport) -> tuple[float | None, float | None]:
    """Read maximal equity amount and maximum relative equity %, without balance fallback."""
    maximal = _first_metric(
        report, "Equity Drawdown Maximal", "Reducción máxima de la equidad",
        "Reduccion maxima de la equidad", "Reducción máxima del patrimonio",
    )
    relative = _first_metric(
        report, "Equity Drawdown Relative", "Reducción relativa de la equidad",
        "Reduccion relativa de la equidad", "Reducción relativa del patrimonio",
    )
    number = r"[-+]?\d(?:[\d\s.,]*\d)?"
    amount = pct = None
    match = re.fullmatch(rf"\s*({number})\s*\(\s*({number})\s*%\s*\)\s*", maximal)
    if match:
        amount, pct = _to_float(match[1]), _to_float(match[2])
    else:
        match = re.fullmatch(rf"\s*({number})\s*%\s*\(\s*({number})\s*\)\s*", maximal)
        if match:
            pct, amount = _to_float(match[1]), _to_float(match[2])
        elif re.fullmatch(rf"\s*{number}\s*", maximal):
            amount = _to_float(maximal)
    match = re.search(rf"({number})\s*%", relative)
    if match:
        pct = _to_float(match[1])
    if amount is not None and (not math.isfinite(amount) or amount < 0):
        amount = None
    if pct is not None and (not math.isfinite(pct) or pct < 0):
        pct = None
    return amount, pct


def _drawdown_amount(report: StrategyReport) -> float:
    value = _first_metric(
        report,
        "Balance Drawdown Maximal",
        "Reduccion maxima del balance",
        "Reducción máxima del balance",
    )
    amount, _ = _extract_drawdown(value)
    return amount


def _history_quality(report: StrategyReport) -> float | None:
    value = _first_metric(
        report,
        "History Quality",
        "Calidad del historial",
        "Calidad de historial",
        "Calidad historial",
    )
    if value == "":
        for key, candidate in report.metrics.items():
            normalized_key = _ascii_key(str(key)).casefold()
            if "history quality" in normalized_key or "calidad del historial" in normalized_key:
                value = candidate
                break
        else:
            return None
    match = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*%", value)
    return round(_to_float(match.group(1) if match else value), 4)


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
    number_pattern = r"[-+]?\d(?:[\d\s.,]*\d)?"
    match = re.search(rf"({number_pattern})\s*\(({number_pattern})\s*%", value)
    if not match:
        return _to_float(value), 0.0
    return _to_float(match.group(1)), _to_float(match.group(2))


def _to_float(value: object) -> float:
    text = str(value or "").replace("\xa0", " ").replace("%", "").strip()
    if not text:
        return 0.0
    match = re.search(r"[-+]?\d(?:[\d\s.,]*\d)?", text)
    if not match:
        return 0.0
    cleaned = re.sub(r"\s+", "", match.group(0))
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) > 2 and all(len(part) == 3 for part in parts[1:]):
            cleaned = "".join(parts)
        else:
            cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        parts = cleaned.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            cleaned = "".join(parts)
    return float(cleaned)

# Share of the active months the scale-free concentration measure removes. 5% of
# a 60-month construction window is the historical fixed top three, so both
# measures agree there and only diverge on the shorter windows robustness uses.
# Deliberately not part of ScoreConfig: putting it there would change the score
# config hash of every stored row to express something no verdict depends on
# outside the OOS risk route.
RESIDUAL_TOP_MONTH_SHARE = 0.05


def top_month_count(active_months: int, share: float) -> int | None:
    """How many best months the concentration measure removes, half-up.

    A fixed three months is 5% of a 60-month construction window but 18% of a
    17-month OOS window, so the same number makes a categorically harsher test
    on the shorter one. Scaling the count keeps the test comparable; it never
    drops below one month, or there would be nothing to remove.
    """

    if active_months <= 0 or share <= 0:
        return None
    return max(1, int(active_months * share + 0.5))


def residual_profit_ratio_after_top_months(
    monthly_values: list[float], net_profit: float, count: int
) -> float:
    """Share of the net profit that survives removing the `count` best months."""

    top = sum(sorted((value for value in monthly_values if value > 0), reverse=True)[:count])
    return (net_profit - top) / net_profit if net_profit > 0 else -1.0


@dataclass
class _ReportMetrics:
    """Metricas crudas de un reporte, antes de aplicar puntuacion y filtros."""

    profits: list[float]
    losses: list[float]
    monthly_values: list[float]
    net_profit: float
    profit_factor: float
    drawdown: float
    drawdown_pct: float
    recovery_factor: float
    positive_month_ratio: float
    max_month_concentration: float
    top3_month_profit: float
    residual_profit_after_top3: float
    residual_profit_ratio: float
    scaled_residual_top_months: int | None
    scaled_residual_profit_ratio: float | None
    trade_curve_stability: float | None
    avg_trade: float
    sqn: float
    net_profit_factor: float
    normalization_group: str
    net_profit_basis: object
    normalized_net_profit: float
    history_quality: object
    equity_drawdown: float | None
    equity_drawdown_pct: float | None
    equity_recovery_factor: float | None

    @property
    def trades(self) -> int:
        """Operaciones cerradas del reporte."""
        return len(self.profits)

    @property
    def active_months(self) -> int:
        """Meses con actividad en el reporte."""
        return len(self.monthly_values)

    def risk_metrics(self) -> dict[str, object]:
        """Lo que la politica de riesgo/beneficio necesita para decidir."""
        return dict(
            net_profit=self.net_profit, equity_drawdown=self.equity_drawdown,
            equity_drawdown_pct=self.equity_drawdown_pct, profit_factor=self.profit_factor,
            trades=self.trades, active_months=self.active_months,
            positive_month_ratio=self.positive_month_ratio,
            residual_profit_ratio=self.residual_profit_ratio,
        )


def _monthly_concentration(monthly_values: list[float], net_profit: float) -> tuple:
    """Reparto mensual del beneficio y cuanto depende de los mejores meses."""
    total_positive_months = sum(value for value in monthly_values if value > 0)
    max_month = max((value for value in monthly_values if value > 0), default=0.0)
    max_month_concentration = max_month / total_positive_months if total_positive_months else 1.0
    top3_month_profit = sum(sorted((value for value in monthly_values if value > 0), reverse=True)[:3])
    residual_profit_after_top3 = net_profit - top3_month_profit
    residual_profit_ratio = residual_profit_after_top3 / net_profit if net_profit > 0 else -1.0
    scaled_residual_top_months = top_month_count(len(monthly_values), RESIDUAL_TOP_MONTH_SHARE)
    scaled_residual_profit_ratio = (
        residual_profit_ratio_after_top_months(monthly_values, net_profit, scaled_residual_top_months)
        if scaled_residual_top_months
        else None
    )
    return (
        max_month_concentration, top3_month_profit, residual_profit_after_top3,
        residual_profit_ratio, scaled_residual_top_months, scaled_residual_profit_ratio,
    )


def _report_metrics(report: StrategyReport, broker: object) -> _ReportMetrics:
    """Calcula todas las metricas que la puntuacion y los filtros necesitan."""
    profits = [trade.profit_loss for trade in report.trades]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    net_profit = round(sum(profits), 2)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    drawdown = _drawdown_amount(report)
    monthly_values = [value for months in report.monthly.values() for value in months.values()]
    positive_month_ratio = (
        len([value for value in monthly_values if value > 0]) / len(monthly_values)
        if monthly_values
        else 0.0
    )
    (
        max_month_concentration, top3_month_profit, residual_profit_after_top3,
        residual_profit_ratio, scaled_residual_top_months, scaled_residual_profit_ratio,
    ) = _monthly_concentration(monthly_values, net_profit)
    avg_trade = net_profit / len(profits) if profits else 0.0
    deviation = statistics.pstdev(profits) if len(profits) > 1 else 0.0
    net_profit_factor, normalization_group, net_profit_basis = net_profit_normalization(
        report.symbol, broker=broker
    )
    equity_drawdown, equity_drawdown_pct = _equity_drawdown(report)
    return _ReportMetrics(
        profits=profits,
        losses=losses,
        monthly_values=monthly_values,
        net_profit=net_profit,
        profit_factor=round(gross_profit / gross_loss, 4) if gross_loss else (99.0 if gross_profit else 0.0),
        drawdown=drawdown,
        drawdown_pct=_drawdown_pct(report),
        recovery_factor=round(net_profit / drawdown, 4) if drawdown else (99.0 if net_profit > 0 else 0.0),
        positive_month_ratio=positive_month_ratio,
        max_month_concentration=max_month_concentration,
        top3_month_profit=top3_month_profit,
        residual_profit_after_top3=residual_profit_after_top3,
        residual_profit_ratio=residual_profit_ratio,
        scaled_residual_top_months=scaled_residual_top_months,
        scaled_residual_profit_ratio=scaled_residual_profit_ratio,
        trade_curve_stability=_trade_curve_stability(profits),
        avg_trade=avg_trade,
        sqn=math.sqrt(len(profits)) * avg_trade / deviation if deviation else 0.0,
        net_profit_factor=net_profit_factor,
        normalization_group=normalization_group,
        net_profit_basis=net_profit_basis,
        normalized_net_profit=round(net_profit * net_profit_factor, 2),
        history_quality=_history_quality(report),
        equity_drawdown=equity_drawdown,
        equity_drawdown_pct=equity_drawdown_pct,
        equity_recovery_factor=(
            net_profit / equity_drawdown
            if equity_drawdown is not None and equity_drawdown > 0 else None
        ),
    )
