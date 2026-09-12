"""Alternative profit gate with enforce, shadow and off modes.

Only the net-profit gate can be waived. Equity risk, sample size and temporal
consistency must be observable; unavailable evidence never counts as a pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping

from ubs.degradation import RobustnessDegradationConfig, evaluate_robustness_degradation


@dataclass(frozen=True)
class RiskProfitConfig:
    mode: str = "enforce"
    min_recovery: float = 3.0
    min_profit_factor: float = 1.5
    min_trades: int = 200
    min_active_months: int = 24
    min_positive_month_ratio: float = 0.70
    min_residual_profit_ratio: float = 0.50
    oos_min_recovery: float = 1.0
    oos_min_profit_factor: float = 1.2
    oos_min_trades: int = 50
    oos_min_active_months: int = 6
    oos_min_positive_month_ratio: float = 0.50
    oos_min_residual_profit_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.mode not in {"off", "shadow", "enforce"}:
            raise ValueError("risk profit mode must be off, shadow or enforce")
        for key, value in asdict(self).items():
            if key == "mode":
                continue
            if isinstance(value, bool) or finite_number(value) is None or value <= 0:
                raise ValueError(f"{key} must be a finite positive number")
            if "ratio" in key and value > 1:
                raise ValueError(f"{key} must be <= 1")
            if ("trades" in key or "months" in key) and int(value) != value:
                raise ValueError(f"{key} must be an integer")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object] | None) -> RiskProfitConfig:
        return cls(**dict(raw or {}))


def finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def equity_recovery(metrics: Mapping[str, object]) -> float | None:
    net = finite_number(metrics.get("net_profit"))
    dd = finite_number(metrics.get("equity_drawdown"))
    if net is None or dd is None or dd <= 0:
        return None
    return finite_number(net / dd)


def evaluate_risk_profit(
    metrics: Mapping[str, object],
    config: RiskProfitConfig,
    *,
    stage: str = "base",
    max_drawdown_pct: float = 25.0,
    min_recovery_factor: float = 1.0,
) -> dict[str, object]:
    if stage not in {"base", "oos"}:
        raise ValueError("risk profit stage must be base or oos")
    prefix = "oos_" if stage == "oos" else ""
    thresholds = {
        name: getattr(config, prefix + "min_" + name)
        for name in ("recovery", "profit_factor", "trades", "active_months",
                     "positive_month_ratio", "residual_profit_ratio")
    }
    thresholds["recovery"] = max(thresholds["recovery"], min_recovery_factor)
    values = {key: finite_number(metrics.get(key)) for key in thresholds}
    values["recovery"] = equity_recovery(metrics)
    checks = {
        key: {"value": value, "threshold": thresholds[key], "available": value is not None,
              "accepted": value is not None and value >= thresholds[key]}
        for key, value in values.items()
    }
    net = finite_number(metrics.get("net_profit"))
    dd_pct = finite_number(metrics.get("equity_drawdown_pct"))
    checks["positive_net"] = {
        "value": net, "threshold": 0, "available": net is not None,
        "accepted": net is not None and net > 0,
    }
    checks["equity_drawdown_pct"] = {
        "value": dd_pct, "threshold": max_drawdown_pct, "available": dd_pct is not None,
        "accepted": dd_pct is not None and 0 <= dd_pct <= max_drawdown_pct,
    }
    missing = [name for name, check in checks.items() if not check["available"]]
    insufficient = [name for name in ("trades", "active_months") if not checks[name]["accepted"]]
    failed = [name for name, check in checks.items()
              if check["available"] and not check["accepted"] and name not in insufficient]
    # Removing three months is not meaningful when the required sample is absent.
    if "active_months" in insufficient and "residual_profit_ratio" in failed:
        failed.remove("residual_profit_ratio")
    status = "failed" if failed else "insufficient_evidence" if missing or insufficient else "eligible"
    return {
        "version": "risk_profit_v1", "mode": config.mode, "stage": stage,
        "config": asdict(config), "risk_basis": "equity", "status": status,
        "eligible": status == "eligible", "checks": checks,
        "missing": missing, "insufficient": insufficient, "failed": failed,
        "selected_route": "none",
    }


def apply_base_profit_gate(
    metrics: Mapping[str, object], reasons: list[str], config: RiskProfitConfig,
    *, stage: str = "base", max_drawdown_pct: float = 25.0,
    min_recovery_factor: float = 1.0,
) -> tuple[list[str], dict[str, object]]:
    audit = evaluate_risk_profit(metrics, config, stage=stage,
                                max_drawdown_pct=max_drawdown_pct,
                                min_recovery_factor=min_recovery_factor)
    audit["other_reasons"] = [reason for reason in reasons if reason != "net_profit"]
    audit["would_rescue"] = reasons == ["net_profit"] and audit["eligible"] and config.mode != "off"
    updated = list(reasons)
    # OOS needs construction-window comparisons before it can waive any gate.
    if stage == "base" and config.mode == "enforce" and audit["would_rescue"]:
        updated.remove("net_profit")
        audit["selected_route"] = "risk_adjusted"
    elif "net_profit" not in reasons:
        audit["selected_route"] = "absolute"
    return updated, audit


def combine_robustness_profit_gate(
    base: Mapping[str, object], oos: Mapping[str, object], reasons: list[str],
    degradation: dict[str, object], config: RiskProfitConfig,
    *, max_drawdown_pct: float, min_recovery_factor: float,
    degradation_config: RobustnessDegradationConfig,
) -> tuple[list[str], dict[str, object], dict[str, object]]:
    """Evaluate the OOS alternative with complete, duration-adjusted equity evidence."""
    audit = evaluate_risk_profit(oos, config, stage="oos",
                                max_drawdown_pct=max_drawdown_pct,
                                min_recovery_factor=min_recovery_factor)
    base_window = degradation.get("base_window", {})
    oos_window = degradation.get("oos_window", {})
    equity_degradation = evaluate_robustness_degradation(
        base, oos, base_from_date=base_window.get("from_date"),
        base_to_date=base_window.get("to_date"),
        oos_from_date=oos_window.get("from_date"), oos_to_date=oos_window.get("to_date"),
        config=degradation_config, risk_basis="equity",
    )
    unavailable = [name for name, check in equity_degradation["checks"].items()
                   if check["enabled"] and not check["available"]]
    base_days = finite_number(base_window.get("days"))
    oos_days = finite_number(oos_window.get("days"))
    dates_valid = bool(base_days and base_days > 0 and oos_days and oos_days > 0)
    # Require valid equity on both sides even if a relative check is disabled.
    risk_valid = equity_recovery(base) is not None and equity_recovery(base) > 0
    base_dd_pct = finite_number(base.get("equity_drawdown_pct"))
    risk_valid = risk_valid and base_dd_pct is not None and base_dd_pct >= 0
    if not dates_valid:
        unavailable.append("comparison_dates")
    if not risk_valid:
        unavailable.append("base_equity")
    audit["equity_degradation"] = equity_degradation
    audit["missing_comparisons"] = unavailable
    if equity_degradation["reasons"]:
        audit["status"] = "failed"
    elif unavailable and audit["status"] != "failed":
        audit["status"] = "insufficient_evidence"
    audit["eligible"] = audit["status"] == "eligible"
    other_reasons = [reason for reason in reasons if reason != "net_profit"]
    audit["other_reasons"] = other_reasons
    audit["would_rescue"] = reasons == ["net_profit"] and audit["eligible"] and config.mode != "off"
    selected = degradation
    updated = list(reasons)
    base_audit = base.get("risk_profit_audit") or {}
    base_used_risk = isinstance(base_audit, dict) and base_audit.get("selected_route") == "risk_adjusted"
    use_equity = config.mode == "enforce" and (audit["would_rescue"] or base_used_risk)
    if use_equity:
        selected = equity_degradation
        if unavailable:
            updated.append("risk_profit_evidence")
        if audit["would_rescue"]:
            updated.remove("net_profit")
            audit["selected_route"] = "risk_adjusted"
    if "net_profit" not in reasons:
        audit["selected_route"] = "absolute"
    # Missing evidence is a pending outcome only when it is the sole obstacle.
    pending = (config.mode == "enforce" and audit["status"] == "insufficient_evidence"
               and not [r for r in other_reasons if r != "trades"]
               and ("net_profit" in reasons or base_used_risk))
    if pending:
        audit["selected_route"] = "pending_evidence"
        selected = equity_degradation
        updated = [r for r in updated if r not in {"net_profit", "trades"}]
        updated.append("risk_profit_evidence")
    combined = list(dict.fromkeys([*updated, *selected.get("reasons", [])]))
    audit["would_accept"] = not other_reasons and ("net_profit" not in reasons or audit["eligible"])
    selected = dict(selected)
    selected["risk_profit_audit"] = audit
    return combined, audit, selected


def robustness_result_status(result: object) -> str:
    if result.accepted:
        return "accepted"
    if result.risk_profit_audit.get("selected_route") == "pending_evidence":
        return "pending_risk_evidence"
    return "rejected"
