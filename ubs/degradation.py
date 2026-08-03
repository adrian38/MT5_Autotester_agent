from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping


DEGRADATION_FORMULA_VERSION = "robustness_degradation_v1"
DEFAULT_MIN_NET_RETENTION = 0.50
DEFAULT_MIN_PF_EDGE_RETENTION = 0.50
DEFAULT_MIN_RECOVERY_RETENTION = 0.50
DEFAULT_MAX_DD_INFLATION = 2.0
PF_SENTINEL_CAP = 50.0
RECOVERY_SENTINEL_CAP = 50.0
DD_RATIO_FLOOR_PCT = 2.0


@dataclass(frozen=True)
class RobustnessDegradationConfig:
    min_net_retention: float = DEFAULT_MIN_NET_RETENTION
    min_pf_edge_retention: float = DEFAULT_MIN_PF_EDGE_RETENTION
    min_recovery_retention: float = DEFAULT_MIN_RECOVERY_RETENTION
    max_dd_inflation: float = DEFAULT_MAX_DD_INFLATION

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _date(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _window(from_date: object, to_date: object) -> dict[str, object]:
    start = _date(from_date)
    end = _date(to_date)
    days = (end - start).days + 1 if start is not None and end is not None and end >= start else None
    return {
        "from_date": str(from_date or "").strip(),
        "to_date": str(to_date or "").strip(),
        "days": days,
    }


def _check(
    value: float | None,
    threshold: float,
    *,
    comparison: str,
    details: dict[str, object],
) -> dict[str, object]:
    enabled = float(threshold) > 0
    available = value is not None
    if not enabled or not available:
        accepted = True
    elif comparison == "minimum":
        accepted = value >= float(threshold)
    else:
        accepted = value <= float(threshold)
    return {
        "enabled": enabled,
        "available": available,
        "accepted": accepted,
        "value": round(value, 6) if value is not None else None,
        "threshold": float(threshold),
        "comparison": comparison,
        **details,
    }


def evaluate_robustness_degradation(
    base_metrics: Mapping[str, object] | None,
    oos_metrics: Mapping[str, object] | None,
    *,
    base_from_date: object,
    base_to_date: object,
    oos_from_date: object,
    oos_to_date: object,
    config: RobustnessDegradationConfig | None = None,
) -> dict[str, object]:
    """Measure how much of the construction-window edge survives OOS.

    Missing or sentinel metrics are recorded as unavailable and remain neutral.
    The caller still applies the normal absolute OOS gates independently.
    """

    cfg = config or RobustnessDegradationConfig()
    base = base_metrics or {}
    oos = oos_metrics or {}
    base_window = _window(base_from_date, base_to_date)
    oos_window = _window(oos_from_date, oos_to_date)

    base_net = _number(base.get("normalized_net_profit"))
    oos_net = _number(oos.get("normalized_net_profit"))
    base_days = _number(base_window.get("days"))
    oos_days = _number(oos_window.get("days"))
    base_net_annual = (
        base_net * 365.25 / base_days
        if base_net is not None and base_net > 0 and base_days is not None and base_days > 0
        else None
    )
    oos_net_annual = (
        oos_net * 365.25 / oos_days
        if oos_net is not None and oos_days is not None and oos_days > 0
        else None
    )
    net_retention = (
        oos_net_annual / base_net_annual
        if base_net_annual is not None and base_net_annual > 0 and oos_net_annual is not None
        else None
    )

    base_pf = _number(base.get("profit_factor"))
    oos_pf = _number(oos.get("profit_factor"))
    pf_edge_retention = (
        (oos_pf - 1.0) / (base_pf - 1.0)
        if base_pf is not None
        and oos_pf is not None
        and 1.0 < base_pf < PF_SENTINEL_CAP
        and oos_pf < PF_SENTINEL_CAP
        else None
    )

    base_recovery = _number(base.get("recovery_factor"))
    oos_recovery = _number(oos.get("recovery_factor"))
    recovery_retention = (
        oos_recovery / base_recovery
        if base_recovery is not None
        and oos_recovery is not None
        and 0 < base_recovery < RECOVERY_SENTINEL_CAP
        and oos_recovery < RECOVERY_SENTINEL_CAP
        else None
    )

    base_dd = _number(base.get("drawdown_pct"))
    oos_dd = _number(oos.get("drawdown_pct"))
    dd_inflation = (
        oos_dd / max(base_dd, DD_RATIO_FLOOR_PCT)
        if base_dd is not None and oos_dd is not None and base_dd >= 0 and oos_dd >= 0
        else None
    )

    base_trades = _number(base.get("trades"))
    oos_trades = _number(oos.get("trades"))
    trade_rate_retention = (
        (oos_trades / oos_days) / (base_trades / base_days)
        if base_trades is not None
        and oos_trades is not None
        and base_trades > 0
        and base_days is not None
        and oos_days is not None
        and base_days > 0
        and oos_days > 0
        else None
    )
    base_months = _number(base.get("positive_month_ratio"))
    oos_months = _number(oos.get("positive_month_ratio"))
    positive_month_delta = (
        oos_months - base_months if base_months is not None and oos_months is not None else None
    )

    checks = {
        "net_retention": _check(
            net_retention,
            cfg.min_net_retention,
            comparison="minimum",
            details={
                "base_annualized": round(base_net_annual, 6) if base_net_annual is not None else None,
                "oos_annualized": round(oos_net_annual, 6) if oos_net_annual is not None else None,
            },
        ),
        "pf_edge_retention": _check(
            pf_edge_retention,
            cfg.min_pf_edge_retention,
            comparison="minimum",
            details={"base": base_pf, "oos": oos_pf, "neutral_point": 1.0},
        ),
        "recovery_retention": _check(
            recovery_retention,
            cfg.min_recovery_retention,
            comparison="minimum",
            details={"base": base_recovery, "oos": oos_recovery},
        ),
        "dd_inflation": _check(
            dd_inflation,
            cfg.max_dd_inflation,
            comparison="maximum",
            details={"base": base_dd, "oos": oos_dd, "base_floor_pct": DD_RATIO_FLOOR_PCT},
        ),
    }
    reason_by_check = {
        "net_retention": "degradation_net",
        "pf_edge_retention": "degradation_profit_factor",
        "recovery_retention": "degradation_recovery",
        "dd_inflation": "degradation_drawdown",
    }
    reasons = tuple(
        reason_by_check[name]
        for name, check in checks.items()
        if check["enabled"] and check["available"] and not check["accepted"]
    )
    available_checks = sum(1 for check in checks.values() if check["enabled"] and check["available"])
    enabled_checks = sum(1 for check in checks.values() if check["enabled"])
    return {
        "version": DEGRADATION_FORMULA_VERSION,
        "accepted": not reasons,
        "reasons": list(reasons),
        "config": cfg.to_dict(),
        "base_window": base_window,
        "oos_window": oos_window,
        "checks": checks,
        "diagnostics": {
            "trade_rate_retention": round(trade_rate_retention, 6) if trade_rate_retention is not None else None,
            "positive_month_ratio_delta": round(positive_month_delta, 6) if positive_month_delta is not None else None,
            "enabled_checks": enabled_checks,
            "available_checks": available_checks,
            "complete": enabled_checks == available_checks,
        },
    }
