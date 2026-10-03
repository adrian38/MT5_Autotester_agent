"""Constantes, plan y ayudantes de fila de la reparacion riesgo/beneficio."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json

from ubs.degradation import RobustnessDegradationConfig
from ubs.risk_profit import RiskProfitConfig
from ubs.score import ScoreConfig, ScoreResult, rescore_result


NET_PROFIT_REASON = "net_profit"
BASE_SCOPE_STATUS = "rejected"
# Reasons the route itself appends, not stored criteria. A previous pass writes
# them, so reading them as failing criteria would permanently exclude the very
# rows this rule already moved once.
ROUTE_MARKER_REASONS = ("risk_profit_evidence",)
PARKED_STATUS = "pending_risk_evidence"
AUDIT_BLOB_KEYS = (
    "metrics_json",
    "degradation_json",
    "previous_metrics_json",
    "previous_degradation_json",
)
# ``pending_risk_evidence`` is re-examined on purpose: a row parked for missing
# evidence must be able to resolve once the report can be read.
ROBUST_SCOPE_STATUSES = ("rejected", "pending_risk_evidence")
# Measurements of the report the OOS route reads and older rows never stored.
ROUTE_EVIDENCE_FIELDS = (
    "equity_drawdown",
    "equity_drawdown_pct",
    "equity_recovery_factor",
    "scaled_residual_profit_ratio",
    "scaled_residual_top_months",
    "trade_curve_stability",
    "bootstrap_reps",
    "bootstrap_mean_block",
    "bootstrap_net_positive_probability",
    "bootstrap_net_p05",
    "bootstrap_pf_p05",
)


@dataclass(frozen=True)
class ScopedRow:
    """A candidate row plus the payloads already parsed to scope it."""

    row: sqlite3.Row
    metrics: dict
    degradation: dict = field(default_factory=dict)
    base_metrics: dict = field(default_factory=dict)


@dataclass
class RestatementPlan:
    policy: dict[str, object] = field(default_factory=dict)
    base: list[dict] = field(default_factory=list)
    robustness: list[dict] = field(default_factory=list)
    base_evidence: list[dict] = field(default_factory=list)
    audit_only: list[dict] = field(default_factory=list)
    pending_robustness: list[dict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def rows_to_write(self) -> int:
        return sum(len(rows) for rows in self.buckets().values())

    def verdict_changes(self) -> int:
        return len(self.base) + len(self.robustness)

    def is_empty(self) -> bool:
        return self.rows_to_write() == 0

    def buckets(self) -> dict[str, list[dict]]:
        """What gets written: rows whose state the route moved, plus the base
        evidence each of those moves was compared against.

        Rows the route examined and left where they were are not rewritten, not
        even to store what this pass measured on them. They go to ``audit_only``
        and the audit file explains them, so the repair only touches robustness
        rows that actually changed.
        """

        return {
            "base": self.base,
            "robustness": self.robustness,
            "base_evidence": self.base_evidence,
        }

    def to_audit(self) -> dict[str, object]:
        """Reversible record of the plan, without turning into a memory copy.

        Rows that change verdict keep every blob, old and new: that is what a
        revert needs. Evidence-only rows are recorded compactly — they keep
        their verdict, and rerunning the scan rebuilds their payload.
        """

        return {
            "rule": "risk_profit_v2",
            "policy": dict(self.policy),
            "base": list(self.base),
            "robustness": list(self.robustness),
            "base_evidence": list(self.base_evidence),
            "audit_only": [_compact(item) for item in self.audit_only],
            "pending_robustness": list(self.pending_robustness),
            "skipped": dict(self.skipped),
        }


def _with_equity_evidence(
    result: ScoreResult, report_path: object, read_equity, plan: RestatementPlan
) -> tuple[ScoreResult | None, str]:
    """Complete the equity fields, reading the report only when they are absent."""

    if result.equity_drawdown is not None or result.equity_drawdown_pct is not None:
        return result, "almacenada"
    path = str(report_path or "").strip()
    if not path:
        _skip(plan, "sin_reporte")
        return None, "sin_reporte"
    try:
        amount, pct = read_equity(path)
    except OSError:
        _skip(plan, "reporte_ilegible")
        return None, "reporte_ilegible"
    if amount is None and pct is None:
        _skip(plan, "sin_equity_en_reporte")
        return None, "sin_equity_en_reporte"
    return (
        replace(
            result,
            equity_drawdown=amount,
            equity_drawdown_pct=pct,
            equity_recovery_factor=(
                result.net_profit / amount if amount is not None and amount > 0 else None
            ),
        ),
        "reporte",
    )


def _with_route_evidence(
    result: ScoreResult, report_path: object, read_oos_evidence, plan: RestatementPlan
) -> tuple[ScoreResult | None, str]:
    """Complete every measurement the OOS route reads, in one parse.

    Robustness needs more than the equity drawdown: the scale-free
    concentration, the trade-curve stability and the generalization bootstrap
    all come from the trade and monthly series. They are measurements of the
    report, not criteria — the bootstrap seeds itself from the trade series, so
    it is reproducible — and without them the route can only answer "pending
    evidence" on rows it has already judged eligible.

    Fields the row already stored are kept: this only fills gaps.
    """

    missing = [name for name in ROUTE_EVIDENCE_FIELDS if getattr(result, name) is None]
    if not missing:
        return result, "almacenada"
    path = str(report_path or "").strip()
    if not path:
        _skip(plan, "sin_reporte")
        return None, "sin_reporte"
    try:
        evidence = read_oos_evidence(path)
    except OSError:
        _skip(plan, "reporte_ilegible")
        return None, "reporte_ilegible"
    except Exception:  # parser roto para esta fila: se reporta, no se adivina
        _skip(plan, "evidencia_no_calculable")
        return None, "evidencia_no_calculable"
    filled = {
        name: value for name, value in evidence.items()
        if name in ROUTE_EVIDENCE_FIELDS and getattr(result, name) is None and value is not None
    }
    if not filled:
        _skip(plan, "sin_evidencia_en_reporte")
        return None, "sin_evidencia_en_reporte"
    return replace(result, **filled), "reporte"


def _verdict_follows_criteria(
    result: ScoreResult,
    config: ScoreConfig,
    *,
    stage: str,
    stored_reasons: tuple[str, ...] | None = None,
) -> bool:
    """True when the stored reasons are what the stored thresholds produce."""

    legacy = rescore_result(result, _without_route(config), risk_stage=stage)
    expected = result.reasons if stored_reasons is None else stored_reasons
    return tuple(legacy.reasons) == tuple(expected)


def _config_for_row(metrics: dict, policy: RiskProfitConfig) -> ScoreConfig:
    """The thresholds the row stored, combined with the policy being applied."""

    config = ScoreConfig.from_dict(metrics.get("score_config"), ScoreConfig(risk_profit=policy))
    return replace(config, risk_profit=policy)


def _merged_payload(stored: dict, restated: ScoreResult) -> dict:
    """Refresh the score fields while keeping the execution diagnostics.

    ``metrics_json`` also carries evidence that is not part of the score
    dataclass (invalid stops, volume evidence, log sources). Serializing only
    the dataclass would drop it, so the stored blob is updated in place.
    """

    return {**stored, **json.loads(restated.to_json())}


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _same_payload(stored: dict, payload: dict) -> bool:
    """True when nothing measurable changed.

    A field that appears with no value is not a change: base rows gain the
    scaled concentration keys as nulls, because that measure is read from the
    report only for the robustness rows the route can move. Rewriting thousands
    of base blobs to store two nulls would reopen a stage this repair does not
    touch.
    """

    if payload == stored:
        return True
    empty_additions = {
        key for key in payload.keys() - stored.keys() if payload[key] is None
    }
    return {
        key: value for key, value in payload.items() if key not in empty_additions
    } == stored


def _compact(item: dict) -> dict:
    return {key: value for key, value in item.items() if key not in AUDIT_BLOB_KEYS}


def _without_route(config: ScoreConfig) -> ScoreConfig:
    return replace(config, risk_profit=replace(config.risk_profit, mode="off"))


def _degradation_config(degradation: dict) -> RobustnessDegradationConfig:
    stored = degradation.get("config")
    stored = stored if isinstance(stored, dict) else {}
    return RobustnessDegradationConfig(
        **{
            key: value
            for key, value in stored.items()
            if key in RobustnessDegradationConfig.__dataclass_fields__
        }
    )


def _payload(raw: object) -> dict:
    try:
        payload = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _reasons(metrics: dict) -> tuple[str, ...]:
    return tuple(str(reason) for reason in metrics.get("reasons") or ())


def _absolute_reasons(metrics: dict, degradation: dict) -> tuple[str, ...]:
    """Stored reasons minus the relative ones and the route's own markers.

    The degradation blob owns the relative comparisons, and a previous pass of
    this route owns ``risk_profit_evidence``. Neither is a stored threshold, so
    neither may push a row out of scope: doing that would freeze out exactly the
    rows the rule has already moved once.
    """

    ignored = {str(reason) for reason in degradation.get("reasons") or ()}
    ignored.update(ROUTE_MARKER_REASONS)
    return tuple(reason for reason in _reasons(metrics) if reason not in ignored)


def _judged_absolute_reasons(row, metrics: dict, degradation: dict) -> tuple[str, ...]:
    """The absolute gate the stored thresholds have to reproduce.

    A row the route parked as ``pending_risk_evidence`` has already had its net
    gate waived by the route, so its stored reasons no longer name it even
    though that is still the gate it stands or falls on. Reading them literally
    would put every parked row out of scope — the rule could never revisit what
    it had parked.
    """

    absolute = _absolute_reasons(metrics, degradation)
    if not absolute and str(row["status"]) == PARKED_STATUS:
        return (NET_PROFIT_REASON,)
    return absolute


def _needs_parent_evidence(metrics: dict) -> bool:
    """What the equity-basis comparison needs from the base side of a pair."""

    missing_equity = (
        metrics.get("equity_drawdown") is None
        and metrics.get("equity_drawdown_pct") is None
    )
    return missing_equity or metrics.get("trade_curve_stability") is None


def _skip(plan: RestatementPlan, reason: str) -> None:
    plan.skipped[reason] = plan.skipped.get(reason, 0) + 1


def _tick(progress, done: int, total: int, label: str) -> None:
    if progress is not None:
        progress(done, total, label)
