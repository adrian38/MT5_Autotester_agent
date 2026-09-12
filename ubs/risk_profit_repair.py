"""Restate stored verdicts under the risk-adjusted profit route.

The route arrived after these rows were judged, and it needs evidence they
never stored: ``equity_drawdown`` is younger than their ``metrics_json``, so it
is read back from the report on disk instead of rerunning MT5.

Two guards keep this from turning into a silent full rescore:

* only rows the route can actually move are considered — a base row rejected
  for the net gate alone, or an OOS row whose only absolute reason is that same
  gate. Rows failing profit factor, drawdown or sample size stay where they
  are, because the route never waives those.
* every row is re-judged with the thresholds **it** stored, and only when its
  stored verdict still follows from them. A verdict that no longer matches its
  own criteria is reported instead of rewritten, so a threshold change that was
  never applied cannot ride along disguised as this repair.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import sqlite3

from ubs.degradation import RobustnessDegradationConfig
from ubs.risk_profit import (
    RiskProfitConfig,
    combine_robustness_profit_gate,
    robustness_result_status,
)
from ubs.score import ScoreConfig, ScoreResult, equity_drawdown_from_report_file, rescore_result


NET_PROFIT_REASON = "net_profit"
BASE_SCOPE_STATUS = "rejected"
AUDIT_BLOB_KEYS = (
    "metrics_json",
    "degradation_json",
    "previous_metrics_json",
    "previous_degradation_json",
)
# ``pending_risk_evidence`` is re-examined on purpose: a row parked for missing
# evidence must be able to resolve once the report can be read.
ROBUST_SCOPE_STATUSES = ("rejected", "pending_risk_evidence")


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
    base_evidence: list[dict] = field(default_factory=list)
    robustness: list[dict] = field(default_factory=list)
    robustness_evidence: list[dict] = field(default_factory=list)
    pending_robustness: list[dict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def rows_to_write(self) -> int:
        return sum(len(rows) for rows in self.buckets().values())

    def verdict_changes(self) -> int:
        return len(self.base) + len(self.robustness)

    def is_empty(self) -> bool:
        return self.rows_to_write() == 0

    def buckets(self) -> dict[str, list[dict]]:
        """The four write groups: verdict changes and evidence-only rewrites."""

        return {
            "base": self.base,
            "base_evidence": self.base_evidence,
            "robustness": self.robustness,
            "robustness_evidence": self.robustness_evidence,
        }

    def to_audit(self) -> dict[str, object]:
        """Reversible record of the plan, without turning into a memory copy.

        Rows that change verdict keep every blob, old and new: that is what a
        revert needs. Evidence-only rows are recorded compactly — they keep
        their verdict, and rerunning the scan rebuilds their payload.
        """

        return {
            "rule": "risk_profit_v1",
            "policy": dict(self.policy),
            "base": list(self.base),
            "robustness": list(self.robustness),
            "base_evidence": [_compact(item) for item in self.base_evidence],
            "robustness_evidence": [_compact(item) for item in self.robustness_evidence],
            "pending_robustness": list(self.pending_robustness),
            "skipped": dict(self.skipped),
        }


def scan_risk_profit_restatements(
    conn,
    *,
    policy: RiskProfitConfig | None = None,
    read_equity=equity_drawdown_from_report_file,
    progress=None,
) -> RestatementPlan:
    """Plan every state change the route produces, without writing anything."""

    policy = policy or RiskProfitConfig()
    base_scope, robust_scope = collect_scope(conn)
    plan = RestatementPlan(policy=asdict(policy))
    base_ids = {int(item.row["id"]) for item in base_scope}
    parents = [
        item for item in robust_scope
        if int(item.row["candidate_id"]) not in base_ids and _needs_equity(item.base_metrics)
    ]
    total = len(base_scope) + len(robust_scope) + len(parents)
    done = 0
    restated_base: dict[int, dict] = {}
    # Scoping the memory takes a few seconds; publish the real total first so a
    # caller showing progress is not stuck on an unknown size.
    _tick(progress, done, total, "")

    for item in base_scope:
        done += 1
        _tick(progress, done, total, str(item.row["report_path"] or item.row["symbol"] or ""))
        change = _restate_base(item, item.metrics, policy, read_equity, plan)
        if change is None:
            continue
        restated_base[int(item.row["id"])] = json.loads(change["metrics_json"])
        bucket = plan.base if change["expected_status"] != change["stored_status"] else plan.base_evidence
        bucket.append(change)

    parent_ids = {int(item.row["candidate_id"]) for item in parents}
    for item in robust_scope:
        candidate_id = int(item.row["candidate_id"])
        if candidate_id in parent_ids and candidate_id not in restated_base:
            done += 1
            _tick(progress, done, total, str(item.row["base_report_path"] or ""))
            parent = _restate_base_parent(item, policy, read_equity, plan)
            if parent is not None:
                restated_base[candidate_id] = json.loads(parent["metrics_json"])
                plan.base_evidence.append(parent)
        done += 1
        _tick(progress, done, total, str(item.row["report_path"] or item.row["symbol"] or ""))
        base_metrics = restated_base.get(candidate_id, item.base_metrics)
        change = _restate_robustness(item, base_metrics, policy, read_equity, plan)
        if change is None:
            continue
        bucket = (
            plan.robustness
            if change["expected_status"] != change["stored_status"]
            else plan.robustness_evidence
        )
        bucket.append(change)

    plan.pending_robustness = pending_robustness_rows(
        conn, [int(item["candidate_id"]) for item in plan.base]
    )
    return plan


def collect_scope(conn) -> tuple[list[ScopedRow], list[ScopedRow]]:
    """Rows the route can move, selected from stored verdicts alone (no I/O)."""

    base_scope: list[ScopedRow] = []
    for row in conn.execute(
        """
        select id, run_id, symbol, target_symbol, period, status, report_path, metrics_json
        from candidates
        where status=? and coalesce(metrics_json, '') != ''
        order by run_id, generation, id
        """,
        (BASE_SCOPE_STATUS,),
    ).fetchall():
        metrics = _payload(row["metrics_json"])
        if _reasons(metrics) == (NET_PROFIT_REASON,):
            base_scope.append(ScopedRow(row=row, metrics=metrics))

    placeholders = ",".join("?" for _status in ROBUST_SCOPE_STATUSES)
    robust_scope: list[ScopedRow] = []
    for row in conn.execute(
        f"""
        select cr.candidate_id, cr.run_id, cr.status, cr.report_path, cr.metrics_json,
               cr.degradation_json, c.run_id as base_run_id, c.status as base_status,
               c.report_path as base_report_path, c.metrics_json as base_metrics_json,
               c.symbol, c.target_symbol, c.period
        from candidate_robustness cr
        join candidates c on c.id = cr.candidate_id
        where cr.status in ({placeholders}) and coalesce(cr.metrics_json, '') != ''
        order by cr.run_id, cr.candidate_id
        """,
        ROBUST_SCOPE_STATUSES,
    ).fetchall():
        metrics = _payload(row["metrics_json"])
        degradation = _payload(row["degradation_json"])
        if _absolute_reasons(metrics, degradation) != (NET_PROFIT_REASON,):
            continue
        robust_scope.append(
            ScopedRow(
                row=row,
                metrics=metrics,
                degradation=degradation,
                base_metrics=_payload(row["base_metrics_json"]),
            )
        )
    return base_scope, robust_scope


def pending_robustness_rows(conn, candidate_ids: list[int]) -> list[dict]:
    """Rescued base rows with no OOS row: the new work the rescue creates."""

    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _id in candidate_ids)
    rows = conn.execute(
        f"""
        select c.id as candidate_id, c.run_id, c.target_symbol, c.symbol, c.period
        from candidates c
        left join candidate_robustness cr on cr.candidate_id = c.id
        where c.id in ({placeholders}) and cr.candidate_id is null
        order by c.run_id, c.id
        """,
        candidate_ids,
    ).fetchall()
    return [
        {
            "candidate_id": int(row["candidate_id"]),
            "run_id": int(row["run_id"]),
            "symbol": row["target_symbol"] or row["symbol"],
            "period": row["period"],
        }
        for row in rows
    ]


def apply_risk_profit_restatements(conn, plan: RestatementPlan) -> dict[str, int]:
    """Write a reviewed plan without overwriting rows that moved meanwhile."""

    written = {name: 0 for name in plan.buckets()}
    for key in ("base", "base_evidence"):
        for item in plan.buckets()[key]:
            cursor = conn.execute(
                """
                update candidates
                set score=?, accepted=?, metrics_json=?, status=?
                where id=? and status=?
                """,
                (
                    item["score"],
                    item["expected_accepted"],
                    item["metrics_json"],
                    item["expected_status"],
                    item["candidate_id"],
                    item["stored_status"],
                ),
            )
            written[key] += max(0, int(cursor.rowcount))
    for key in ("robustness", "robustness_evidence"):
        for item in plan.buckets()[key]:
            cursor = conn.execute(
                """
                update candidate_robustness
                set status=?, accepted=?, score=?, metrics_json=?, degradation_json=?
                where candidate_id=? and status=?
                """,
                (
                    item["expected_status"],
                    item["expected_accepted"],
                    item["score"],
                    item["metrics_json"],
                    item["degradation_json"],
                    item["candidate_id"],
                    item["stored_status"],
                ),
            )
            written[key] += max(0, int(cursor.rowcount))
    conn.commit()
    return written


def _restate_base(
    item: ScopedRow, metrics: dict, policy: RiskProfitConfig, read_equity, plan: RestatementPlan
) -> dict | None:
    row = item.row
    config = _config_for_row(metrics, policy)
    try:
        result = ScoreResult.from_json(metrics)
    except (TypeError, ValueError, json.JSONDecodeError):
        _skip(plan, "metricas_invalidas")
        return None
    if not _verdict_follows_criteria(result, config, stage="base"):
        _skip(plan, "criterio_desfasado")
        return None
    enriched, source = _with_equity_evidence(result, row["report_path"], read_equity, plan)
    if enriched is None:
        return None
    restated = rescore_result(enriched, config)
    payload = _merged_payload(metrics, restated)
    expected = "accepted" if restated.accepted else "rejected"
    if expected == str(row["status"]) and payload == metrics:
        _skip(plan, "sin_cambio")
        return None
    return {
        "candidate_id": int(row["id"]),
        "run_id": int(row["run_id"]),
        "symbol": row["target_symbol"] or row["symbol"],
        "period": row["period"],
        "stored_status": str(row["status"]),
        "expected_status": expected,
        "expected_accepted": int(bool(restated.accepted)),
        "score": restated.score,
        "equity_source": source,
        "equity_drawdown": restated.equity_drawdown,
        "equity_drawdown_pct": restated.equity_drawdown_pct,
        "selected_route": str(restated.risk_profit_audit.get("selected_route", "")),
        "risk_status": str(restated.risk_profit_audit.get("status", "")),
        "reasons": list(restated.reasons),
        "metrics_json": _json(payload),
        "previous_metrics_json": row["metrics_json"],
    }


def _restate_base_parent(
    item: ScopedRow, policy: RiskProfitConfig, read_equity, plan: RestatementPlan
) -> dict | None:
    """Store the equity evidence of an OOS row's base, which stays as it is.

    The OOS comparison is relative: without base equity the route has nothing
    to compare against and can only answer "pending evidence".
    """

    row = item.row
    config = _config_for_row(item.base_metrics, policy)
    try:
        result = ScoreResult.from_json(item.base_metrics)
    except (TypeError, ValueError, json.JSONDecodeError):
        _skip(plan, "metricas_base_invalidas")
        return None
    if not _verdict_follows_criteria(result, config, stage="base"):
        _skip(plan, "criterio_base_desfasado")
        return None
    enriched, source = _with_equity_evidence(result, row["base_report_path"], read_equity, plan)
    if enriched is None:
        return None
    restated = rescore_result(enriched, config)
    if restated.accepted != (str(row["base_status"]) == "accepted"):
        _skip(plan, "criterio_base_desfasado")
        return None
    payload = _merged_payload(item.base_metrics, restated)
    if payload == item.base_metrics:
        return None
    return {
        "candidate_id": int(row["candidate_id"]),
        "run_id": int(row["base_run_id"]),
        "symbol": row["target_symbol"] or row["symbol"],
        "period": row["period"],
        "stored_status": str(row["base_status"]),
        "expected_status": str(row["base_status"]),
        "expected_accepted": int(bool(restated.accepted)),
        "score": restated.score,
        "equity_source": source,
        "equity_drawdown": restated.equity_drawdown,
        "equity_drawdown_pct": restated.equity_drawdown_pct,
        "selected_route": str(restated.risk_profit_audit.get("selected_route", "")),
        "risk_status": str(restated.risk_profit_audit.get("status", "")),
        "reasons": list(restated.reasons),
        "metrics_json": _json(payload),
        "previous_metrics_json": row["base_metrics_json"],
    }


def _restate_robustness(
    item: ScopedRow, base_metrics: dict, policy: RiskProfitConfig, read_equity, plan: RestatementPlan
) -> dict | None:
    row = item.row
    if not item.degradation:
        _skip(plan, "sin_degradacion")
        return None
    config = _config_for_row(item.metrics, policy)
    try:
        result = ScoreResult.from_json(item.metrics)
    except (TypeError, ValueError, json.JSONDecodeError):
        _skip(plan, "metricas_invalidas")
        return None
    if not _verdict_follows_criteria(
        result, config, stage="oos", stored_reasons=_absolute_reasons(item.metrics, item.degradation)
    ):
        _skip(plan, "criterio_desfasado")
        return None
    enriched, source = _with_equity_evidence(result, row["report_path"], read_equity, plan)
    if enriched is None:
        return None
    absolute = rescore_result(enriched, _without_route(config), risk_stage="oos")
    reasons, audit, degradation = combine_robustness_profit_gate(
        base_metrics,
        json.loads(absolute.to_json()),
        list(absolute.reasons),
        dict(item.degradation),
        policy,
        max_drawdown_pct=config.max_drawdown_pct,
        min_recovery_factor=config.min_recovery_factor,
        degradation_config=_degradation_config(item.degradation),
    )
    combined = replace(
        absolute,
        accepted=not reasons,
        reasons=tuple(reasons),
        risk_profit_audit=audit,
        score_config=config.to_dict(),
        score_config_hash=config.stable_hash(),
    )
    degradation = dict(degradation)
    degradation["absolute_accepted"] = not absolute.reasons
    degradation["final_accepted"] = combined.accepted
    expected = robustness_result_status(combined)
    payload = _merged_payload(item.metrics, combined)
    if expected == str(row["status"]) and payload == item.metrics:
        _skip(plan, "sin_cambio")
        return None
    return {
        "candidate_id": int(row["candidate_id"]),
        "run_id": int(row["run_id"]),
        "symbol": row["target_symbol"] or row["symbol"],
        "period": row["period"],
        "stored_status": str(row["status"]),
        "expected_status": expected,
        "expected_accepted": int(bool(combined.accepted)),
        "score": combined.score,
        "equity_source": source,
        "equity_drawdown": combined.equity_drawdown,
        "equity_drawdown_pct": combined.equity_drawdown_pct,
        "selected_route": str(audit.get("selected_route", "")),
        "risk_status": str(audit.get("status", "")),
        "missing_comparisons": list(audit.get("missing_comparisons") or ()),
        "reasons": list(combined.reasons),
        "metrics_json": _json(payload),
        "degradation_json": _json(degradation),
        "previous_metrics_json": row["metrics_json"],
        "previous_degradation_json": row["degradation_json"],
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
    """Stored reasons minus the ones the degradation blob owns."""

    relative = {str(reason) for reason in degradation.get("reasons") or ()}
    return tuple(reason for reason in _reasons(metrics) if reason not in relative)


def _needs_equity(metrics: dict) -> bool:
    return metrics.get("equity_drawdown") is None and metrics.get("equity_drawdown_pct") is None


def _skip(plan: RestatementPlan, reason: str) -> None:
    plan.skipped[reason] = plan.skipped.get(reason, 0) + 1


def _tick(progress, done: int, total: int, label: str) -> None:
    if progress is not None:
        progress(done, total, label)
