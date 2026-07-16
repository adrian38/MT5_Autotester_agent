from __future__ import annotations

from datetime import datetime
from typing import Iterable


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
}


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
