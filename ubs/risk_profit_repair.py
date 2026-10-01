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

from dataclasses import asdict, replace
import json

from ubs.risk_profit import (
    RiskProfitConfig,
    combine_robustness_profit_gate,
    robustness_result_status,
)
from ubs.risk_profit_repair_rows import (
    AUDIT_BLOB_KEYS,
    BASE_SCOPE_STATUS,
    NET_PROFIT_REASON,
    PARKED_STATUS,
    ROBUST_SCOPE_STATUSES,
    RestatementPlan,
    ScopedRow,
    _absolute_reasons,
    _compact,
    _config_for_row,
    _degradation_config,
    _judged_absolute_reasons,
    _json,
    _merged_payload,
    _needs_parent_evidence,
    _payload,
    _reasons,
    _same_payload,
    _skip,
    _tick,
    _verdict_follows_criteria,
    _with_equity_evidence,
    _with_route_evidence,
    _without_route,
)
from ubs.score import (
    RESIDUAL_TOP_MONTH_SHARE,
    ScoreConfig,
    ScoreResult,
    equity_drawdown_from_report_file,
    rescore_result,
    route_evidence_from_report_file,
)


def _parent_evidence_rows(
    base_scope: list[ScopedRow], robust_scope: list[ScopedRow],
) -> list[ScopedRow]:
    base_ids = {int(item.row["id"]) for item in base_scope}
    return [
        item for item in robust_scope
        if int(item.row["candidate_id"]) not in base_ids
        and _needs_parent_evidence(item.base_metrics)
    ]


def _scan_base_restatements(
    base_scope: list[ScopedRow], policy, read_equity, plan: RestatementPlan,
    progress, done: int, total: int, restated_base: dict[int, dict],
) -> int:
    for item in base_scope:
        done += 1
        _tick(progress, done, total, str(item.row["report_path"] or item.row["symbol"] or ""))
        change = _restate_base(item, item.metrics, policy, read_equity, plan)
        if change is None:
            continue
        restated_base[int(item.row["id"])] = json.loads(change["metrics_json"])
        bucket = (
            plan.base
            if change["expected_status"] != change["stored_status"]
            else plan.audit_only
        )
        bucket.append(change)
    return done


def scan_risk_profit_restatements(
    conn,
    *,
    policy: RiskProfitConfig | None = None,
    read_equity=equity_drawdown_from_report_file,
    read_oos_evidence=route_evidence_from_report_file,
    residual_share: float = RESIDUAL_TOP_MONTH_SHARE,
    progress=None,
) -> RestatementPlan:
    """Plan every state change the route produces, without writing anything."""

    policy = policy or RiskProfitConfig()
    base_scope, robust_scope = collect_scope(conn)
    plan = RestatementPlan(policy=asdict(policy))
    parents = _parent_evidence_rows(base_scope, robust_scope)
    total = len(base_scope) + len(robust_scope) + len(parents)
    done = 0
    restated_base: dict[int, dict] = {}
    # Scoping the memory takes a few seconds; publish the real total first so a
    # caller showing progress is not stuck on an unknown size.
    _tick(progress, done, total, "")

    done = _scan_base_restatements(
        base_scope, policy, read_equity, plan, progress, done, total, restated_base)
    parent_ids = {int(item.row["candidate_id"]) for item in parents}
    parent_changes: dict[int, dict] = {}
    for item in robust_scope:
        candidate_id = int(item.row["candidate_id"])
        if candidate_id in parent_ids and candidate_id not in restated_base:
            done += 1
            _tick(progress, done, total, str(item.row["base_report_path"] or ""))
            parent = _restate_base_parent(
                item, policy, lambda path: read_oos_evidence(path, residual_share), plan
            )
            if parent is not None:
                restated_base[candidate_id] = json.loads(parent["metrics_json"])
                parent_changes[candidate_id] = parent
        done += 1
        _tick(progress, done, total, str(item.row["report_path"] or item.row["symbol"] or ""))
        base_metrics = restated_base.get(candidate_id, item.base_metrics)
        change = _restate_robustness(
            item, base_metrics, policy,
            lambda path: read_oos_evidence(path, residual_share), plan,
        )
        if change is None:
            continue
        if change["expected_status"] == change["stored_status"]:
            plan.audit_only.append(change)
            continue
        plan.robustness.append(change)
        # The base equity is what the comparison was made against: storing it
        # next to the change keeps the new state derivable from the memory
        # instead of only from this run.
        parent = parent_changes.get(candidate_id)
        if parent is not None:
            plan.base_evidence.append(parent)

    rescued_ids = [int(item["candidate_id"]) for item in plan.base]
    plan.pending_robustness = pending_robustness_rows(conn, rescued_ids)
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
        if _judged_absolute_reasons(row, metrics, degradation) != (NET_PROFIT_REASON,):
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
    for key in ("robustness",):
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
    # A base row is rewritten when its verdict moves or when this pass measured
    # its equity for the first time. Refreshing the recorded policy is not a
    # reason: the OOS side of it changes without any base verdict depending on
    # it, and that would rewrite thousands of rows this repair does not judge.
    if expected == str(row["status"]) and (
        source == "almacenada" or _same_payload(metrics, payload)
    ):
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
    item: ScopedRow, policy: RiskProfitConfig, read_evidence, plan: RestatementPlan
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
    enriched, source = _with_route_evidence(result, row["base_report_path"], read_evidence, plan)
    if enriched is None:
        return None
    restated = rescore_result(enriched, config)
    if restated.accepted != (str(row["base_status"]) == "accepted"):
        _skip(plan, "criterio_base_desfasado")
        return None
    payload = _merged_payload(item.base_metrics, restated)
    if _same_payload(item.base_metrics, payload):
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
        "evidence_source": source,
        "equity_drawdown": restated.equity_drawdown,
        "equity_drawdown_pct": restated.equity_drawdown_pct,
        "selected_route": str(restated.risk_profit_audit.get("selected_route", "")),
        "risk_status": str(restated.risk_profit_audit.get("status", "")),
        "reasons": list(restated.reasons),
        "metrics_json": _json(payload),
        "previous_metrics_json": row["base_metrics_json"],
    }


def _robustness_change(
    row, combined: ScoreResult, audit: dict, payload: dict, degradation: dict,
    expected: str, source: str,
) -> dict:
    return {
        "candidate_id": int(row["candidate_id"]),
        "run_id": int(row["run_id"]),
        "symbol": row["target_symbol"] or row["symbol"],
        "period": row["period"],
        "stored_status": str(row["status"]),
        "expected_status": expected,
        "expected_accepted": int(bool(combined.accepted)),
        "score": combined.score,
        "evidence_source": source,
        "equity_drawdown": combined.equity_drawdown,
        "equity_drawdown_pct": combined.equity_drawdown_pct,
        "scaled_residual_profit_ratio": combined.scaled_residual_profit_ratio,
        "scaled_residual_top_months": combined.scaled_residual_top_months,
        "selected_route": str(audit.get("selected_route", "")),
        "risk_status": str(audit.get("status", "")),
        "missing_comparisons": list(audit.get("missing_comparisons") or ()),
        "reasons": list(combined.reasons),
        "metrics_json": _json(payload),
        "degradation_json": _json(degradation),
        "previous_metrics_json": row["metrics_json"],
        "previous_degradation_json": row["degradation_json"],
    }


def _restate_robustness(
    item: ScopedRow, base_metrics: dict, policy: RiskProfitConfig, read_oos_evidence,
    plan: RestatementPlan,
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
        result, config, stage="oos",
        stored_reasons=_judged_absolute_reasons(item.row, item.metrics, item.degradation),
    ):
        _skip(plan, "criterio_desfasado")
        return None
    enriched, source = _with_route_evidence(result, row["report_path"], read_oos_evidence, plan)
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
    if expected == str(row["status"]) and _same_payload(item.metrics, payload):
        _skip(plan, "sin_cambio")
        return None
    return _robustness_change(
        row, combined, audit, payload, degradation, expected, source,
    )
