from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping


DEGRADATION_FORMULA_VERSION = "robustness_degradation_v2"
DEFAULT_MIN_NET_RETENTION = 0.50
DEFAULT_MIN_PF_EDGE_RETENTION = 0.50
DEFAULT_MIN_RECOVERY_RETENTION = 0.50
DEFAULT_MAX_DD_INFLATION = 2.0
DEFAULT_MIN_TRADE_RATE_RETENTION = 0.50
DEFAULT_MIN_RESIDUAL_PROFIT_RATIO = 0.20
DEFAULT_MIN_OOS_POSITIVE_MONTH_RATIO = 0.50
DEFAULT_MIN_TRADE_CURVE_STABILITY = 0.60
DEFAULT_MIN_STABILITY_RETENTION = 0.75
DEFAULT_MIN_BOOTSTRAP_NET_POSITIVE_PROBABILITY = 0.95
DEFAULT_MIN_BOOTSTRAP_PF_P05 = 1.05
PF_SENTINEL_CAP = 50.0
RECOVERY_SENTINEL_CAP = 50.0
DD_RATIO_FLOOR_PCT = 2.0


@dataclass(frozen=True)
class RobustnessDegradationConfig:
    min_net_retention: float = DEFAULT_MIN_NET_RETENTION
    min_pf_edge_retention: float = DEFAULT_MIN_PF_EDGE_RETENTION
    min_recovery_retention: float = DEFAULT_MIN_RECOVERY_RETENTION
    max_dd_inflation: float = DEFAULT_MAX_DD_INFLATION
    min_trade_rate_retention: float = DEFAULT_MIN_TRADE_RATE_RETENTION
    min_residual_profit_ratio: float = DEFAULT_MIN_RESIDUAL_PROFIT_RATIO
    min_oos_positive_month_ratio: float = DEFAULT_MIN_OOS_POSITIVE_MONTH_RATIO
    min_trade_curve_stability: float = DEFAULT_MIN_TRADE_CURVE_STABILITY
    min_stability_retention: float = DEFAULT_MIN_STABILITY_RETENTION
    min_bootstrap_net_positive_probability: float = DEFAULT_MIN_BOOTSTRAP_NET_POSITIVE_PROBABILITY
    min_bootstrap_pf_p05: float = DEFAULT_MIN_BOOTSTRAP_PF_P05

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
    base_recovery_annual = (
        base_recovery * 365.25 / base_days
        if base_recovery is not None
        and 0 < base_recovery < RECOVERY_SENTINEL_CAP
        and base_days is not None
        and base_days > 0
        else None
    )
    oos_recovery_annual = (
        oos_recovery * 365.25 / oos_days
        if oos_recovery is not None
        and oos_recovery < RECOVERY_SENTINEL_CAP
        and oos_days is not None
        and oos_days > 0
        else None
    )
    recovery_retention = (
        oos_recovery_annual / base_recovery_annual
        if base_recovery_annual is not None
        and base_recovery_annual > 0
        and oos_recovery_annual is not None
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
    residual_profit_ratio = _number(oos.get("residual_profit_ratio"))
    base_stability = _number(base.get("trade_curve_stability"))
    oos_stability = _number(oos.get("trade_curve_stability"))
    stability_retention = (
        oos_stability / base_stability
        if base_stability is not None
        and base_stability > 0
        and oos_stability is not None
        else None
    )
    bootstrap_net_probability = _number(oos.get("bootstrap_net_positive_probability"))
    bootstrap_pf_p05 = _number(oos.get("bootstrap_pf_p05"))

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
            details={
                "base": base_recovery,
                "oos": oos_recovery,
                "base_annualized": (
                    round(base_recovery_annual, 6) if base_recovery_annual is not None else None
                ),
                "oos_annualized": (
                    round(oos_recovery_annual, 6) if oos_recovery_annual is not None else None
                ),
            },
        ),
        "dd_inflation": _check(
            dd_inflation,
            cfg.max_dd_inflation,
            comparison="maximum",
            details={"base": base_dd, "oos": oos_dd, "base_floor_pct": DD_RATIO_FLOOR_PCT},
        ),
        "trade_rate_retention": _check(
            trade_rate_retention,
            cfg.min_trade_rate_retention,
            comparison="minimum",
            details={"base_trades": base_trades, "oos_trades": oos_trades},
        ),
        "residual_profit_ratio": _check(
            residual_profit_ratio,
            cfg.min_residual_profit_ratio,
            comparison="minimum",
            details={
                "oos_net": _number(oos.get("net_profit")),
                "top3_month_profit": _number(oos.get("top3_month_profit")),
                "residual_profit_after_top3": _number(oos.get("residual_profit_after_top3")),
            },
        ),
        "oos_positive_month_ratio": _check(
            oos_months,
            cfg.min_oos_positive_month_ratio,
            comparison="minimum",
            details={"oos_active_months": _number(oos.get("active_months"))},
        ),
        "trade_curve_stability": _check(
            oos_stability,
            cfg.min_trade_curve_stability,
            comparison="minimum",
            details={"oos": oos_stability},
        ),
        "stability_retention": _check(
            stability_retention,
            cfg.min_stability_retention,
            comparison="minimum",
            details={"base": base_stability, "oos": oos_stability},
        ),
        "bootstrap_net_positive_probability": _check(
            bootstrap_net_probability,
            cfg.min_bootstrap_net_positive_probability,
            comparison="minimum",
            details={"bootstrap_reps": _number(oos.get("bootstrap_reps"))},
        ),
        "bootstrap_pf_p05": _check(
            bootstrap_pf_p05,
            cfg.min_bootstrap_pf_p05,
            comparison="minimum",
            details={"bootstrap_reps": _number(oos.get("bootstrap_reps"))},
        ),
    }
    reason_by_check = {
        "net_retention": "degradation_net",
        "pf_edge_retention": "degradation_profit_factor",
        "recovery_retention": "degradation_recovery",
        "dd_inflation": "degradation_drawdown",
        "trade_rate_retention": "degradation_trade_rate",
        "residual_profit_ratio": "generalization_residual_profit",
        "oos_positive_month_ratio": "generalization_month_breadth",
        "trade_curve_stability": "generalization_stability",
        "stability_retention": "generalization_stability_retention",
        "bootstrap_net_positive_probability": "generalization_bootstrap_net",
        "bootstrap_pf_p05": "generalization_bootstrap_pf",
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
            "positive_month_ratio_delta": round(positive_month_delta, 6) if positive_month_delta is not None else None,
            "enabled_checks": enabled_checks,
            "available_checks": available_checks,
            "complete": enabled_checks == available_checks,
        },
    }
