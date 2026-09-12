from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
import hashlib
import json
import math
import random
import re
import statistics
from types import SimpleNamespace
from typing import Mapping

from portfolio_manager.mt5_report import StrategyReport, parse_report, parse_report_metrics
from ubs.account import DEFAULT_BROKER
from ubs.normalization import net_profit_normalization
from ubs.risk_profit import RiskProfitConfig, apply_base_profit_gate


SCORE_FORMULA_VERSION = "2"
GENERALIZATION_BOOTSTRAP_REPS = 2000
GENERALIZATION_BOOTSTRAP_MEAN_BLOCK = 5.0
# Share of the active months the scale-free concentration measure removes. 5% of
# a 60-month construction window is the historical fixed top three, so both
# measures agree there and only diverge on the shorter windows robustness uses.
# Deliberately not part of ScoreConfig: putting it there would change the score
# config hash of every stored row to express something no verdict depends on
# outside the OOS risk route.
RESIDUAL_TOP_MONTH_SHARE = 0.05


@dataclass(frozen=True)
class ScoreConfig:
    min_net_profit: float = 100.0
    min_profit_factor: float = 1.20
    min_trades: int = 50
    max_drawdown_pct: float = 25.0
    min_recovery_factor: float = 1.0
    min_positive_month_ratio: float = 0.0
    risk_profit: RiskProfitConfig = field(default_factory=RiskProfitConfig)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object, fallback: ScoreConfig | None = None) -> ScoreConfig:
        """Rebuild the thresholds a row was judged with, from its stored copy.

        Every field falls back one by one: a blob written before a threshold
        existed must keep the caller's value for that one field instead of
        losing the rest of the stored configuration.
        """

        base = fallback or cls()
        if not isinstance(raw, Mapping):
            return base
        try:
            return cls(
                min_net_profit=float(raw.get("min_net_profit", base.min_net_profit)),
                min_profit_factor=float(raw.get("min_profit_factor", base.min_profit_factor)),
                min_trades=int(raw.get("min_trades", base.min_trades)),
                max_drawdown_pct=float(raw.get("max_drawdown_pct", base.max_drawdown_pct)),
                min_recovery_factor=float(raw.get("min_recovery_factor", base.min_recovery_factor)),
                min_positive_month_ratio=float(
                    raw.get("min_positive_month_ratio", base.min_positive_month_ratio)
                ),
                risk_profit=(
                    RiskProfitConfig.from_dict(raw["risk_profit"])
                    if "risk_profit" in raw
                    else base.risk_profit
                ),
            )
        except (TypeError, ValueError):
            return base

    def stable_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
    history_quality: float | None
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
    # Number of losing trades. ``None`` marks a row persisted before this field
    # existed: legacy rows must be probed with ``run_is_lossless`` instead of
    # being read as zero, which would label every old report loss-free.
    losing_trades: int | None = None
    active_months: int | None = None
    top3_month_profit: float | None = None
    residual_profit_after_top3: float | None = None
    residual_profit_ratio: float | None = None
    trade_curve_stability: float | None = None
    bootstrap_reps: int | None = None
    bootstrap_mean_block: float | None = None
    bootstrap_net_positive_probability: float | None = None
    bootstrap_net_p05: float | None = None
    bootstrap_pf_p05: float | None = None
    score_formula_version: str = SCORE_FORMULA_VERSION
    score_config: dict[str, float | int] = field(default_factory=dict)
    score_config_hash: str = ""
    equity_drawdown: float | None = None
    equity_drawdown_pct: float | None = None
    equity_recovery_factor: float | None = None
    risk_profit_audit: dict[str, object] = field(default_factory=dict)
    # Concentration measured by removing a *share* of the active months instead
    # of a fixed three. Only the OOS risk route reads it; `None` marks a row
    # scored before the field existed, whose monthly series is not stored.
    scaled_residual_profit_ratio: float | None = None
    scaled_residual_top_months: int | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=True, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str | bytes | dict[str, object]) -> ScoreResult:
        """Rehydrate metrics persisted in SQLite, including legacy rows."""

        data = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
        if not isinstance(data, dict):
            raise ValueError("ScoreResult JSON must contain an object")
        data["reasons"] = tuple(str(reason) for reason in data.get("reasons") or ())
        # metrics_json can also carry execution diagnostics. They belong to
        # the persisted audit, not to the numeric score dataclass.
        score_fields = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in score_fields})


def score_report_file(
    path: Path,
    config: ScoreConfig | None = None,
    *,
    broker: object = DEFAULT_BROKER,
    include_generalization_bootstrap: bool = False,
    risk_stage: str = "base",
) -> ScoreResult:
    return score_report(
        parse_report(path),
        config=config,
        broker=broker,
        include_generalization_bootstrap=include_generalization_bootstrap,
        risk_stage=risk_stage,
    )


def score_report(
    report: StrategyReport,
    config: ScoreConfig | None = None,
    *,
    broker: object = DEFAULT_BROKER,
    include_generalization_bootstrap: bool = False,
    risk_stage: str = "base",
) -> ScoreResult:
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
    top3_month_profit = sum(sorted((value for value in monthly_values if value > 0), reverse=True)[:3])
    residual_profit_after_top3 = net_profit - top3_month_profit
    residual_profit_ratio = residual_profit_after_top3 / net_profit if net_profit > 0 else -1.0
    scaled_residual_top_months = top_month_count(len(monthly_values), RESIDUAL_TOP_MONTH_SHARE)
    scaled_residual_profit_ratio = (
        residual_profit_ratio_after_top_months(monthly_values, net_profit, scaled_residual_top_months)
        if scaled_residual_top_months
        else None
    )
    trade_curve_stability = _trade_curve_stability(profits)
    avg_trade = net_profit / len(profits) if profits else 0.0
    deviation = statistics.pstdev(profits) if len(profits) > 1 else 0.0
    sqn = math.sqrt(len(profits)) * avg_trade / deviation if deviation else 0.0
    net_profit_factor, normalization_group, net_profit_basis = net_profit_normalization(report.symbol, broker=broker)
    normalized_net_profit = round(net_profit * net_profit_factor, 2)
    history_quality = _history_quality(report)
    equity_drawdown, equity_drawdown_pct = _equity_drawdown(report)
    equity_recovery_factor = (
        net_profit / equity_drawdown
        if equity_drawdown is not None and equity_drawdown > 0 else None
    )
    risk_metrics = dict(
        net_profit=net_profit, equity_drawdown=equity_drawdown,
        equity_drawdown_pct=equity_drawdown_pct, profit_factor=profit_factor,
        trades=len(profits), active_months=len(monthly_values),
        positive_month_ratio=positive_month_ratio, residual_profit_ratio=residual_profit_ratio,
    )
    _, preliminary_risk = apply_base_profit_gate(
        risk_metrics, ["net_profit"], config.risk_profit, stage=risk_stage,
        max_drawdown_pct=config.max_drawdown_pct, min_recovery_factor=config.min_recovery_factor,
    )
    absolute_gate_candidate = (
        (normalized_net_profit > config.min_net_profit
         or (config.risk_profit.mode != "off" and preliminary_risk["eligible"]))
        and profit_factor >= config.min_profit_factor
        and len(profits) >= config.min_trades
        and drawdown_pct <= config.max_drawdown_pct
        and recovery_factor >= config.min_recovery_factor
        and positive_month_ratio >= config.min_positive_month_ratio
    )
    bootstrap = (
        _generalization_bootstrap(profits)
        if include_generalization_bootstrap and profits and absolute_gate_candidate
        else {}
    )

    score = _score_formula(
        net_profit=normalized_net_profit,
        profit_factor=profit_factor,
        recovery_factor=recovery_factor,
        drawdown_pct=drawdown_pct,
        trades=len(profits),
        positive_month_ratio=positive_month_ratio,
        max_month_concentration=max_month_concentration,
        sqn=sqn,
        residual_profit_ratio=residual_profit_ratio,
        trade_curve_stability=trade_curve_stability,
        bootstrap_net_positive_probability=bootstrap.get("net_positive_probability"),
        bootstrap_pf_p05=bootstrap.get("pf_p05"),
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
    reasons, risk_audit = apply_base_profit_gate(
        risk_metrics, reasons, config.risk_profit, stage=risk_stage,
        max_drawdown_pct=config.max_drawdown_pct, min_recovery_factor=config.min_recovery_factor,
    )

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
        history_quality=history_quality,
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
        losing_trades=len(losses),
        active_months=len(monthly_values),
        top3_month_profit=round(top3_month_profit, 4),
        residual_profit_after_top3=round(residual_profit_after_top3, 4),
        residual_profit_ratio=round(residual_profit_ratio, 6),
        trade_curve_stability=(
            round(trade_curve_stability, 6) if trade_curve_stability is not None else None
        ),
        bootstrap_reps=int(bootstrap["reps"]) if bootstrap else None,
        bootstrap_mean_block=float(bootstrap["mean_block"]) if bootstrap else None,
        bootstrap_net_positive_probability=(
            round(float(bootstrap["net_positive_probability"]), 6) if bootstrap else None
        ),
        bootstrap_net_p05=round(float(bootstrap["net_p05"]), 4) if bootstrap else None,
        bootstrap_pf_p05=round(float(bootstrap["pf_p05"]), 6) if bootstrap else None,
        score_formula_version=SCORE_FORMULA_VERSION,
        score_config=config.to_dict(),
        score_config_hash=config.stable_hash(),
        equity_drawdown=equity_drawdown,
        equity_drawdown_pct=equity_drawdown_pct,
        equity_recovery_factor=equity_recovery_factor,
        risk_profit_audit=risk_audit,
        scaled_residual_profit_ratio=(
            round(scaled_residual_profit_ratio, 6)
            if scaled_residual_profit_ratio is not None
            else None
        ),
        scaled_residual_top_months=scaled_residual_top_months,
    )


def rescore_result(
    result: ScoreResult, config: ScoreConfig | None = None, *, risk_stage: str = "base",
) -> ScoreResult:
    """Apply current score weights and gates to already persisted raw metrics.

    This deliberately preserves normalization and parser-derived fields. Use a
    fresh report parse instead when either of those algorithms changes.
    """

    config = config or ScoreConfig()
    score = _score_formula(
        net_profit=result.normalized_net_profit,
        profit_factor=result.profit_factor,
        recovery_factor=result.recovery_factor,
        drawdown_pct=result.drawdown_pct,
        trades=result.trades,
        positive_month_ratio=result.positive_month_ratio,
        max_month_concentration=result.max_month_concentration,
        sqn=result.sqn,
        residual_profit_ratio=result.residual_profit_ratio,
        trade_curve_stability=result.trade_curve_stability,
        bootstrap_net_positive_probability=result.bootstrap_net_positive_probability,
        bootstrap_pf_p05=result.bootstrap_pf_p05,
    )
    reasons: list[str] = []
    if result.normalized_net_profit <= config.min_net_profit:
        reasons.append("net_profit")
    if result.profit_factor < config.min_profit_factor:
        reasons.append("profit_factor")
    if result.trades < config.min_trades:
        reasons.append("trades")
    if result.drawdown_pct > config.max_drawdown_pct:
        reasons.append("drawdown_pct")
    if result.recovery_factor < config.min_recovery_factor:
        reasons.append("recovery_factor")
    if result.positive_month_ratio < config.min_positive_month_ratio:
        reasons.append("positive_month_ratio")
    reasons, risk_audit = apply_base_profit_gate(
        asdict(result), reasons, config.risk_profit, stage=risk_stage,
        max_drawdown_pct=config.max_drawdown_pct, min_recovery_factor=config.min_recovery_factor,
    )
    return replace(
        result,
        score=round(score, 4),
        accepted=not reasons,
        reasons=tuple(reasons),
        score_formula_version=SCORE_FORMULA_VERSION,
        score_config=config.to_dict(),
        score_config_hash=config.stable_hash(),
        risk_profit_audit=risk_audit,
    )


def run_is_lossless(result: ScoreResult) -> bool:
    """True when the backtest closed without a single losing trade.

    That state makes ``profit_factor`` and ``drawdown_pct`` degenerate: the PF is
    the 99.0 sentinel (there is no gross loss to divide by) and the drawdown is a
    flat 0.0. Any *relative* comparison against those two is meaningless, so
    callers use this to switch to absolute checks instead of reading the
    artifact as a real divergence.

    Rows written before ``losing_trades`` existed carry ``None``; for those we
    fall back to the sentinel pair. The fallback is deliberately an exact 99.0
    match because a genuine profit factor can exceed 99 (the memory holds 28
    such rows), and requiring a zero drawdown alongside it removes the rest of
    the ambiguity.
    """

    if float(result.drawdown_pct) != 0.0:
        return False
    if result.losing_trades is not None:
        return int(result.losing_trades) == 0
    return float(result.profit_factor) == 99.0


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
    residual_profit_ratio: float | None = None,
    trade_curve_stability: float | None = None,
    bootstrap_net_positive_probability: float | None = None,
    bootstrap_pf_p05: float | None = None,
) -> float:
    profit_component = min(max(net_profit, -5000.0) / 100.0, 60.0)
    pf_component = min(max(profit_factor - 1.0, -1.0) * 35.0, 70.0)
    recovery_component = min(max(recovery_factor, -5.0) * 6.0, 60.0)
    trades_component = min(trades / 100.0, 1.0) * 15.0
    monthly_component = positive_month_ratio * 35.0
    sqn_component = min(max(sqn, -5.0), 5.0) * 4.0
    dd_penalty = max(drawdown_pct, 0.0) * 1.8
    concentration_penalty = max_month_concentration * 20.0
    generalization_component = 0.0
    if residual_profit_ratio is not None:
        generalization_component += min(max(residual_profit_ratio - 0.20, -1.0), 1.0) * 20.0
    if trade_curve_stability is not None:
        generalization_component += min(max(trade_curve_stability - 0.60, -1.0), 1.0) * 15.0
    if bootstrap_net_positive_probability is not None:
        generalization_component += min(
            max(bootstrap_net_positive_probability - 0.95, -0.20), 0.05
        ) * 100.0
    if bootstrap_pf_p05 is not None:
        generalization_component += min(max(bootstrap_pf_p05 - 1.05, -0.50), 0.50) * 20.0
    return (
        profit_component
        + pf_component
        + recovery_component
        + trades_component
        + monthly_component
        + sqn_component
        + generalization_component
        - dd_penalty
        - concentration_penalty
    )


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


def route_evidence_from_report_file(
    path: Path | str, share: float = RESIDUAL_TOP_MONTH_SHARE
) -> dict[str, object]:
    """Measurements the OOS risk route reads and legacy rows never stored.

    The equity drawdown is in the Results block, but the scaled concentration,
    the trade-curve stability and the generalization bootstrap all come from the
    trade and monthly series, so the report is parsed in full. Every value here
    is a measurement of the report — deterministic and threshold-free, the
    bootstrap included (it seeds itself from the trade series) — so completing
    them is not re-judging the row.

    Only the keys it could measure are returned.
    """

    report = parse_report(Path(path))
    profits = [trade.profit_loss for trade in report.trades]
    monthly_values = [value for months in report.monthly.values() for value in months.values()]
    net_profit = round(sum(profits), 2)
    amount, pct = _equity_drawdown(report)
    evidence: dict[str, object] = {}
    if amount is not None or pct is not None:
        evidence["equity_drawdown"] = amount
        evidence["equity_drawdown_pct"] = pct
        evidence["equity_recovery_factor"] = (
            round(net_profit / amount, 6) if amount is not None and amount > 0 else None
        )
    count = top_month_count(len(monthly_values), share)
    if count is not None:
        evidence["scaled_residual_top_months"] = count
        evidence["scaled_residual_profit_ratio"] = round(
            residual_profit_ratio_after_top_months(monthly_values, net_profit, count), 6
        )
    stability = _trade_curve_stability(profits)
    if stability is not None:
        evidence["trade_curve_stability"] = stability
    bootstrap = _generalization_bootstrap(profits)
    if bootstrap:
        evidence.update(
            bootstrap_reps=bootstrap.get("reps"),
            bootstrap_mean_block=bootstrap.get("mean_block"),
            bootstrap_net_positive_probability=bootstrap.get("net_positive_probability"),
            bootstrap_net_p05=bootstrap.get("net_p05"),
            bootstrap_pf_p05=bootstrap.get("pf_p05"),
        )
    return evidence


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
