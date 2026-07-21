from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping


DEFAULT_REGRESSION_FROM_DATE = "2017.01.01"
DEFAULT_REGRESSION_TO_DATE = "2019.12.31"
REGRESSION_MIN_DAYS = 730

DEFAULT_REGRESSION_MIN_NET_PROFIT = 0.0
DEFAULT_REGRESSION_MIN_PROFIT_FACTOR = 1.10
DEFAULT_REGRESSION_MIN_TRADES = 36
DEFAULT_REGRESSION_MIN_TRADES_W1 = 12
DEFAULT_REGRESSION_MIN_TRADES_MN = 4
DEFAULT_REGRESSION_MAX_DRAWDOWN_PCT = 30.0
DEFAULT_REGRESSION_MIN_RECOVERY_FACTOR = 0.75
DEFAULT_REGRESSION_MIN_POSITIVE_MONTH_RATIO = 0.50

# Degradation-relative criteria (walk-forward-efficiency style). They compare the
# backward holdout against the base window the candidate was selected on, using
# only length-independent ratios (profit factor and drawdown %). 0 disables a check.
DEFAULT_REGRESSION_MIN_PF_EFFICIENCY = 0.50
DEFAULT_REGRESSION_MAX_DD_RATIO = 2.0
# Guards that keep the ratios meaningful: ignore the "no losing trades" profit-factor
# sentinel, and floor a tiny base drawdown so it does not explode the ratio.
REGRESSION_PF_EFFICIENCY_CAP = 50.0
REGRESSION_DD_RATIO_FLOOR_PCT = 2.0

DEFAULT_REGRESSION_POSITIVE_POINTS = 80.0
DEFAULT_REGRESSION_NEGATIVE_POINTS = -100.0
MAX_REGRESSION_REASON_PENALTY = 60.0

REGRESSION_RETRYABLE_STATUSES = frozenset(
    {"no_report", "parse_error", "report_mismatch", "date_mismatch", "no_history"}
)
REGRESSION_FAILURE_STATUSES = frozenset({"rejected", "no_trades"})
REGRESSION_TECHNICAL_STATUSES = REGRESSION_RETRYABLE_STATUSES

REGRESSION_REASON_PENALTIES = {
    "net_profit": 20.0,
    "profit_factor": 15.0,
    "trades": 15.0,
    "drawdown_pct": 20.0,
    "recovery_factor": 15.0,
    "positive_month_ratio": 10.0,
    "pf_efficiency": 15.0,
    "dd_ratio": 20.0,
}


def _ratio_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def regression_degradation(
    base_metrics: Mapping[str, object] | None,
    reg_profit_factor: object,
    reg_drawdown_pct: object,
    *,
    min_pf_efficiency: float = DEFAULT_REGRESSION_MIN_PF_EFFICIENCY,
    max_dd_ratio: float = DEFAULT_REGRESSION_MAX_DD_RATIO,
) -> tuple[tuple[str, ...], dict[str, float]]:
    """Compare the backward holdout against the candidate's base window.

    Returns ``(reasons, audit)``. ``reasons`` extends the absolute failure reasons;
    ``audit`` carries the computed ratios for storage/inspection. A check is skipped
    (never a failure) when its threshold is <= 0 or the base metric is unusable, so a
    missing base window is treated as neutral rather than a strategy loss.
    """

    reasons: list[str] = []
    audit: dict[str, float] = {}
    if not base_metrics:
        return (), audit

    base_pf = _ratio_float(base_metrics.get("profit_factor"))
    reg_pf = _ratio_float(reg_profit_factor)
    if (
        float(min_pf_efficiency) > 0
        and base_pf is not None
        and reg_pf is not None
        and 0 < base_pf < REGRESSION_PF_EFFICIENCY_CAP
        and reg_pf < REGRESSION_PF_EFFICIENCY_CAP
    ):
        efficiency = reg_pf / max(base_pf, 1.0)
        audit["pf_efficiency"] = round(efficiency, 4)
        audit["pf_efficiency_min"] = float(min_pf_efficiency)
        if efficiency < float(min_pf_efficiency):
            reasons.append("pf_efficiency")

    base_dd = _ratio_float(base_metrics.get("drawdown_pct"))
    reg_dd = _ratio_float(reg_drawdown_pct)
    if (
        float(max_dd_ratio) > 0
        and base_dd is not None
        and reg_dd is not None
        and base_dd >= 0
        and reg_dd >= 0
    ):
        ratio = reg_dd / max(base_dd, REGRESSION_DD_RATIO_FLOOR_PCT)
        audit["dd_ratio"] = round(ratio, 4)
        audit["dd_ratio_max"] = float(max_dd_ratio)
        if ratio > float(max_dd_ratio):
            reasons.append("dd_ratio")

    return tuple(reasons), audit


def validate_regression_date_range(from_date: str, to_date: str) -> str:
    """Validate a long, backward-looking holdout range."""

    try:
        start = datetime.strptime(str(from_date or "").strip(), "%Y.%m.%d")
        end = datetime.strptime(str(to_date or "").strip(), "%Y.%m.%d")
    except ValueError:
        return "las fechas deben usar YYYY.MM.DD"
    if end <= start:
        return "Hasta debe ser posterior a Desde"
    days = (end - start).days
    if days < REGRESSION_MIN_DAYS:
        return f"la prueba regresiva requiere al menos {REGRESSION_MIN_DAYS} dias (actual: {days})"
    return ""


def regression_reason_penalty(reasons: Iterable[str]) -> float:
    raw = sum(float(REGRESSION_REASON_PENALTIES.get(str(reason), 10.0)) for reason in reasons)
    return min(raw, MAX_REGRESSION_REASON_PENALTY)


def regression_points(
    status: str,
    reasons: Iterable[str] = (),
    *,
    positive_points: float = DEFAULT_REGRESSION_POSITIVE_POINTS,
    negative_points: float = DEFAULT_REGRESSION_NEGATIVE_POINTS,
) -> float:
    normalized = str(status or "").strip().lower()
    if normalized == "accepted":
        return float(positive_points)
    if normalized in REGRESSION_FAILURE_STATUSES:
        return float(negative_points) - regression_reason_penalty(reasons)
    return 0.0


def regression_points_breakdown(
    status: str,
    reasons: Iterable[str] = (),
    *,
    positive_points: float = DEFAULT_REGRESSION_POSITIVE_POINTS,
    negative_points: float = DEFAULT_REGRESSION_NEGATIVE_POINTS,
) -> dict[str, float]:
    reason_items = tuple(str(reason) for reason in reasons if str(reason))
    normalized = str(status or "").strip().lower()
    base = float(positive_points) if normalized == "accepted" else (
        float(negative_points) if normalized in REGRESSION_FAILURE_STATUSES else 0.0
    )
    penalty = regression_reason_penalty(reason_items) if normalized in REGRESSION_FAILURE_STATUSES else 0.0
    return {
        "base": base,
        "reason_penalty": penalty,
        "applied": regression_points(
            normalized,
            reason_items,
            positive_points=positive_points,
            negative_points=negative_points,
        ),
    }
