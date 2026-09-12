"""Alternative profit gate with enforce, shadow and off modes.

Only the net-profit gate can be waived. Equity risk, sample size and temporal
consistency must be observable; unavailable evidence never counts as a pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Mapping

from ubs.degradation import (
    DD_RATIO_FLOOR_PCT,
    RobustnessDegradationConfig,
    evaluate_robustness_degradation,
)


@dataclass(frozen=True)
class RiskProfitConfig:
    mode: str = "enforce"
    min_recovery: float = 3.0
    min_profit_factor: float = 1.5
    min_trades: int = 200
    min_active_months: int = 24
    min_positive_month_ratio: float = 0.70
    min_residual_profit_ratio: float = 0.50
    # There is no `oos_min_recovery`: see `evaluate_risk_profit`. The OOS window
    # is a fraction of the construction one and its drawdown is an extreme of a
    # third of the sample, so an absolute level there is not measurable. The
    # level was already proven by `min_recovery` in base, and how much of it
    # survived is what `recovery_retention` measures.
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
        """Unknown keys are dropped: rows stored under an older policy shape
        must still load their own thresholds instead of falling back whole."""

        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in dict(raw or {}).items() if key in known})


def finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def equity_recovery(
    metrics: Mapping[str, object], *, floor_pct: float | None = None
) -> float | None:
    """Net profit over the equity drawdown, optionally flooring the drawdown.

    A maximum drawdown of half a percent of the account is not a measurement of
    risk, it is noise: the same strategy shows 0.56% over five years and 0.98%
    over seventeen months, and dividing by it turns that into a 1.7x penalty.
    ``evaluate_robustness_degradation`` already refuses to compare drawdowns
    below ``DD_RATIO_FLOOR_PCT`` for exactly this reason, so the recovery of the
    same comparison floors them too.
    """

    net = finite_number(metrics.get("net_profit"))
    dd = finite_number(metrics.get("equity_drawdown"))
    if net is None or dd is None or dd <= 0:
        return None
    pct = finite_number(metrics.get("equity_drawdown_pct"))
    if floor_pct and pct is not None and 0 < pct < floor_pct:
        dd = dd * floor_pct / pct
    return finite_number(net / dd)


def active_years(metrics: Mapping[str, object]) -> float | None:
    months = finite_number(metrics.get("active_months"))
    return months / 12 if months is not None and months > 0 else None


def _duration_normalized(
    metrics: Mapping[str, object], values: dict[str, float | None]
) -> tuple[dict[str, float | None], dict[str, dict[str, object]]]:
    """Make the duration-dependent OOS checks comparable to the base window.

    Robustness measures far fewer years than construction, and some checks move
    with the length of the window rather than with the quality of the strategy:

    * recovery is net over drawdown. Net accumulates with time and the drawdown
      does not, and on top of that the drawdown of a third of the sample is a
      different extreme. So there is no absolute level here — it was proven in
      base, and `recovery_retention` measures how much of it survived. This
      check only requires the recovery to be measurable and positive, reported
      per year and over a floored drawdown for legibility.
    * the concentration measure removes the best months, and removing a fixed
      three out of seventeen is a different test than three out of sixty. The
      scaled measure removes a share of the months instead.

    A row scored before the scaled measure existed keeps the fixed top-three
    value. On these short windows that one removes *more* months than the share
    would, so the fallback can only withhold a rescue, never grant one.
    """

    years = active_years(metrics)
    floored = equity_recovery(metrics, floor_pct=DD_RATIO_FLOOR_PCT)
    updated = dict(values)
    updated["recovery"] = floored / years if floored is not None and years else None
    scaled = finite_number(metrics.get("scaled_residual_profit_ratio"))
    if scaled is not None:
        updated["residual_profit_ratio"] = scaled
    details = {
        "recovery": {
            "basis": "per_year_floored_drawdown",
            "raw": values.get("recovery"),
            "floored": floored,
            "floor_pct": DD_RATIO_FLOOR_PCT,
            "years": years,
            "judged_by": "recovery_retention",
        },
        "residual_profit_ratio": {
            "basis": "scaled_top_months" if scaled is not None else "fixed_top3_fallback",
            "top_months": metrics.get("scaled_residual_top_months"),
        },
    }
    return updated, details


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
    oos = stage == "oos"
    prefix = "oos_" if oos else ""
    thresholds = {
        name: getattr(config, prefix + "min_" + name)
        for name in ("profit_factor", "trades", "active_months",
                     "positive_month_ratio", "residual_profit_ratio")
    }
    # Positive and measurable in OOS; a proven level in base.
    thresholds["recovery"] = 0.0 if oos else config.min_recovery
    values = {key: finite_number(metrics.get(key)) for key in thresholds}
    values["recovery"] = equity_recovery(metrics)
    details: dict[str, dict[str, object]] = {}
    if oos:
        values, details = _duration_normalized(metrics, values)
    else:
        # The base threshold is a total over its own window, so the balance-based
        # floor of the normal gate is comparable and still applies.
        thresholds["recovery"] = max(thresholds["recovery"], min_recovery_factor)
    checks = {
        key: {"value": value, "threshold": thresholds[key], "available": value is not None,
              "accepted": value is not None and value >= thresholds[key],
              **details.get(key, {})}
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
    # Removing the best months is not meaningful when the required sample is absent.
    if "active_months" in insufficient and "residual_profit_ratio" in failed:
        failed.remove("residual_profit_ratio")
    status = "failed" if failed else "insufficient_evidence" if missing or insufficient else "eligible"
    return {
        # The base gate is untouched, so its audits keep declaring v1; only the
        # OOS side was duration-normalized. Versioning the stage that actually
        # changed keeps every stored base blob byte-identical.
        "version": "risk_profit_v2" if oos else "risk_profit_v1",
        "mode": config.mode, "stage": stage,
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
