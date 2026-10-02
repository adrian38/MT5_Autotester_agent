"""Lectura de las dos memorias y construccion del informe de generalizacion v2."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import DEFAULT_BROKER  # noqa: E402
from tools.audit_generalization_rows import (  # noqa: E402
    audit_current_rows,
    full_downstream_rows,
    full_pass_chain,
    issue,
    pipeline_summary,
    safe_json,
    stage_snapshot,
)


STAGE_TABLES = {
    "final_tick": "candidate_final_tick",
    "final_tick_6m": "candidate_final_tick_6m",
    "regression": "candidate_regression",
}
STAGE_NAMES = tuple(STAGE_TABLES)


def connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    if not table_exists(conn, "candidate_robustness"):
        return {}
    return {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "select status, count(*) from candidate_robustness group by status order by status"
        )
    }


def load_robustness_rows(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(conn, "candidate_robustness"):
        return {}
    candidate_columns = table_columns(conn, "candidates")
    select_parts = [
        "cr.candidate_id",
        "cr.run_id",
        "cr.status",
        "cr.accepted",
        "cr.score",
        "cr.report_path",
        "cr.metrics_json",
    ]
    if "degradation_json" in table_columns(conn, "candidate_robustness"):
        select_parts.append("cr.degradation_json")
    else:
        select_parts.append("'' as degradation_json")
    for column in ("symbol", "period", "set_path", "status"):
        alias = "base_status" if column == "status" else column
        if column in candidate_columns:
            select_parts.append(f"c.{column} as {alias}")
        else:
            select_parts.append(f"'' as {alias}")
    rows = conn.execute(
        f"""
        select {', '.join(select_parts)}
        from candidate_robustness cr
        left join candidates c on c.id=cr.candidate_id
        """
    ).fetchall()
    return {int(row["candidate_id"]): dict(row) for row in rows}


def load_stage_statuses(conn: sqlite3.Connection, table: str) -> dict[int, str]:
    if not table_exists(conn, table):
        return {}
    return {
        int(row[0]): str(row[1])
        for row in conn.execute(f"select candidate_id, status from {table}")
    }


def load_portfolio_members(conn: sqlite3.Connection) -> set[int]:
    if not table_exists(conn, "portfolio_members"):
        return set()
    return {
        int(row[0])
        for row in conn.execute(
            "select distinct candidate_id from portfolio_members where candidate_id is not null"
        )
    }


@dataclass
class _AuditSide:
    """Fotografia de una memoria: robustez, etapas posteriores y cartera."""

    rows: dict[int, dict[str, Any]]
    stages: dict[str, dict[int, str]]
    portfolio: set[int]

    def snapshot(self, candidate_id: int) -> dict[str, Any]:
        """Estado de las etapas posteriores de un candidato en esta memoria."""
        return stage_snapshot(
            candidate_id,
            self.stages["final_tick"],
            self.stages["final_tick_6m"],
            self.stages["regression"],
            self.portfolio,
        )


def _load_side(conn: sqlite3.Connection) -> _AuditSide:
    """Carga de una memoria todo lo que la auditoria compara."""
    return _AuditSide(
        rows=load_robustness_rows(conn),
        stages={name: load_stage_statuses(conn, table) for name, table in STAGE_TABLES.items()},
        portfolio=load_portfolio_members(conn),
    )


def _incompatible_stage_issues(side: _AuditSide, issues: list[dict[str, Any]]) -> None:
    """Avisa de etapas que sobreviven aunque la cadena actual ya no las permita."""
    eligible_probe_ids = {
        candidate_id
        for candidate_id, row in side.rows.items()
        if row.get("base_status") == "accepted" and row.get("status") == "accepted"
    }
    eligible_6m_ids = {
        candidate_id
        for candidate_id in eligible_probe_ids
        if side.stages["final_tick"].get(candidate_id) in {"accepted", "pending_ohlc_trades"}
    }
    eligible_regression_ids = {
        candidate_id
        for candidate_id in eligible_6m_ids
        if side.stages["final_tick_6m"].get(candidate_id) == "accepted"
    }
    eligible_by_stage = {
        "final_tick": eligible_probe_ids,
        "final_tick_6m": eligible_6m_ids,
        "regression": eligible_regression_ids,
    }
    for stage, eligible in eligible_by_stage.items():
        invalid_ids = set(side.stages[stage]) - eligible
        if invalid_ids:
            issues.append(
                issue(
                    "critical",
                    f"incompatible_current_stage:{stage}",
                    len(invalid_ids),
                    "La etapa existe aunque la cadena actual ya no la hace elegible.",
                )
            )


def _row_identity(current: _AuditSide, before: _AuditSide, issues: list[dict[str, Any]]):
    """Filas que desaparecieron o aparecieron respecto al backup."""
    missing_ids = sorted(set(before.rows) - set(current.rows))
    added_ids = sorted(set(current.rows) - set(before.rows))
    if missing_ids:
        issues.append(issue("critical", "robustness_rows_removed", len(missing_ids), "Filas presentes en el backup ya no existen."))
    if added_ids:
        issues.append(issue("warning", "robustness_rows_added", len(added_ids), "Filas nuevas posteriores al backup."))
    return missing_ids, added_ids


def _change_metrics(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Puntuacion, motivos y cobertura de degradacion de un cambio de estado."""
    old_metrics = safe_json(old.get("metrics_json")) or {}
    new_metrics = safe_json(new.get("metrics_json")) or {}
    new_degradation = safe_json(new.get("degradation_json")) or {}
    new_checks = new_degradation.get("checks")
    if not isinstance(new_checks, dict):
        new_checks = {}
    return {
        "before_score": old.get("score"),
        "current_score": new.get("score"),
        "before_reasons": old_metrics.get("reasons", []),
        "current_reasons": new_metrics.get("reasons", []),
        "current_degradation_complete": bool(
            (new_degradation.get("diagnostics") or {}).get("complete", False)
        ),
        "current_unavailable_checks": sorted(
            name
            for name, raw_check in new_checks.items()
            if isinstance(raw_check, dict)
            and bool(raw_check.get("enabled", False))
            and not bool(raw_check.get("available", False))
        ),
    }


def _change_detail(
    candidate_id: int,
    old: dict[str, Any],
    new: dict[str, Any],
    old_stage: dict[str, Any],
    new_stage: dict[str, Any],
) -> dict[str, Any]:
    """Ficha completa de un candidato que cambio de estado de robustez."""
    detail = {
        "candidate_id": candidate_id,
        "run_id": int(new.get("run_id") or 0),
        "symbol": str(new.get("symbol") or ""),
        "period": str(new.get("period") or ""),
        "set_path": str(new.get("set_path") or ""),
        "before_status": str(old.get("status") or ""),
        "current_status": str(new.get("status") or ""),
        "before_downstream": old_stage,
        "current_downstream": new_stage,
        "prior_any_downstream": any(old_stage.get(name) is not None for name in STAGE_NAMES),
        "prior_complete_downstream": full_downstream_rows(old_stage),
        "prior_full_pass_chain": full_pass_chain(old_stage),
        "current_complete_downstream": full_downstream_rows(new_stage),
        "current_full_pass_chain": full_pass_chain(new_stage),
    }
    detail.update(_change_metrics(old, new))
    return detail


@dataclass
class _Changes:
    """Transiciones de robustez entre el backup y la memoria actual."""

    transition_counts: Counter
    changed: list[dict[str, Any]]
    newly_accepted: list[dict[str, Any]]
    newly_rejected: list[dict[str, Any]]


def _collect_changes(current: _AuditSide, before: _AuditSide) -> _Changes:
    """Recorre los candidatos comunes y clasifica los cambios de estado."""
    changes = _Changes(Counter(), [], [], [])
    for candidate_id in sorted(set(current.rows) & set(before.rows)):
        old = before.rows[candidate_id]
        new = current.rows[candidate_id]
        old_status = str(old.get("status") or "")
        new_status = str(new.get("status") or "")
        changes.transition_counts[f"{old_status}->{new_status}"] += 1
        if old_status == new_status:
            continue
        detail = _change_detail(
            candidate_id, old, new, before.snapshot(candidate_id), current.snapshot(candidate_id)
        )
        changes.changed.append(detail)
        if old_status != "accepted" and new_status == "accepted":
            changes.newly_accepted.append(detail)
        if old_status == "accepted" and new_status != "accepted":
            changes.newly_rejected.append(detail)
    return changes


def _transition_issues(changes: _Changes, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Avisos sobre evidencia posterior mal conservada o mal retirada."""
    lost_prior_downstream = [
        row
        for row in changes.newly_accepted
        if row["prior_any_downstream"] and row["before_downstream"] != row["current_downstream"]
    ]
    if lost_prior_downstream:
        issues.append(
            issue(
                "critical",
                "newly_accepted_prior_downstream_not_preserved",
                len(lost_prior_downstream),
                "Un candidato que ahora pasa tenia evidencia posterior distinta en el backup.",
            )
        )
    stale_after_rejection = [
        row
        for row in changes.newly_rejected
        if any(row["current_downstream"].get(name) is not None for name in STAGE_NAMES)
    ]
    if stale_after_rejection:
        issues.append(
            issue(
                "critical",
                "rejected_with_stale_downstream",
                len(stale_after_rejection),
                "La invalidacion no retiro toda la evidencia posterior incompatible.",
            )
        )
    newly_accepted_missing_probe = [
        row for row in changes.newly_accepted if row["current_downstream"]["final_tick"] is None
    ]
    if newly_accepted_missing_probe:
        issues.append(
            issue(
                "warning",
                "newly_accepted_missing_final_tick",
                len(newly_accepted_missing_probe),
                "Candidatos que ahora pasan robustez y deben iniciar Final Tick.",
            )
        )
    return stale_after_rejection


def _integrity_report(
    current: sqlite3.Connection,
    before: sqlite3.Connection,
    integrity_check: bool,
    issues: list[dict[str, Any]],
) -> dict[str, str]:
    """pragma integrity_check de ambas memorias, salvo que se pida saltarlo."""
    integrity = {
        "current": str(current.execute("pragma integrity_check").fetchone()[0]) if integrity_check else "skipped",
        "before": str(before.execute("pragma integrity_check").fetchone()[0]) if integrity_check else "skipped",
    }
    for label, value in integrity.items():
        if value not in {"ok", "skipped"}:
            issues.append(issue("critical", f"integrity:{label}", 1, value))
    return integrity


def _newly_accepted_summary(newly_accepted: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumen agregado de los candidatos que ahora pasan robustez."""
    unavailable_newly_accepted: Counter = Counter()
    cleared_reason_counts: Counter = Counter()
    for row in newly_accepted:
        unavailable_newly_accepted.update(row["current_unavailable_checks"])
        reasons = row.get("before_reasons")
        if isinstance(reasons, list):
            cleared_reason_counts.update(str(reason) for reason in reasons)
    return {
        "count": len(newly_accepted),
        "with_prior_any_downstream": sum(row["prior_any_downstream"] for row in newly_accepted),
        "with_prior_complete_downstream": sum(row["prior_complete_downstream"] for row in newly_accepted),
        "with_prior_full_pass_chain": sum(row["prior_full_pass_chain"] for row in newly_accepted),
        "currently_missing_final_tick": sum(
            row["current_downstream"]["final_tick"] is None for row in newly_accepted
        ),
        "with_complete_degradation_data": sum(
            row["current_degradation_complete"] for row in newly_accepted
        ),
        "with_incomplete_degradation_data": sum(
            not row["current_degradation_complete"] for row in newly_accepted
        ),
        "unavailable_checks": dict(sorted(unavailable_newly_accepted.items())),
        "previous_reasons": dict(cleared_reason_counts.most_common()),
        "details": newly_accepted,
    }


def _newly_rejected_summary(newly_rejected: list[dict[str, Any]], stale_after_rejection: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumen de los rechazados y de la evidencia previa que se invalido."""
    invalidated_stage_counts = {
        stage: sum(row["before_downstream"].get(stage) is not None for row in newly_rejected)
        for stage in STAGE_NAMES
    }
    invalidated_stage_counts["portfolio_member"] = sum(
        row["before_downstream"].get("portfolio_member", False) for row in newly_rejected
    )
    return {
        "count": len(newly_rejected),
        "with_stale_current_downstream": len(stale_after_rejection),
        "prior_stage_rows_invalidated": invalidated_stage_counts,
        "details": newly_rejected,
    }


def _verdict(issues: list[dict[str, Any]]) -> str:
    """Veredicto global segun la peor severidad encontrada."""
    if any(item["severity"] == "critical" for item in issues):
        return "FAIL"
    if any(item["severity"] == "warning" for item in issues):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def build_audit(
    current_path: Path,
    before_path: Path,
    *,
    integrity_check: bool = True,
    scope: str = DEFAULT_BROKER,
) -> dict[str, Any]:
    current_conn = connect_read_only(current_path)
    before_conn = connect_read_only(before_path)
    try:
        current = _load_side(current_conn)
        before = _load_side(before_conn)
        coverage, issues = audit_current_rows(current.rows)
        _incompatible_stage_issues(current, issues)
        missing_ids, added_ids = _row_identity(current, before, issues)
        changes = _collect_changes(current, before)
        stale_after_rejection = _transition_issues(changes, issues)
        integrity = _integrity_report(current_conn, before_conn, integrity_check, issues)
        pipeline = pipeline_summary(current.rows, current.stages)
        if pipeline["missing_regression"]:
            issues.append(
                issue(
                    "warning",
                    "accepted_6m_missing_regression",
                    pipeline["missing_regression"],
                    "Candidatos Final Tick 6M accepted sin resultado regresivo.",
                )
            )
        result = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scope": scope,
            "current_database": str(current_path.resolve()),
            "before_database": str(before_path.resolve()),
            "integrity": integrity,
            "status_counts": {
                "before": status_counts(before_conn),
                "current": status_counts(current_conn),
            },
            "row_identity": {
                "before": len(before.rows),
                "current": len(current.rows),
                "missing_candidate_ids": missing_ids,
                "added_candidate_ids": added_ids,
            },
            "transition_counts": dict(sorted(changes.transition_counts.items())),
            "coverage": coverage,
            "pipeline": pipeline,
            "newly_accepted": _newly_accepted_summary(changes.newly_accepted),
            "newly_rejected": _newly_rejected_summary(changes.newly_rejected, stale_after_rejection),
            "changed_candidates": changes.changed,
            "issues": issues,
        }
        result["verdict"] = _verdict(issues)
        return result
    finally:
        before_conn.close()
        current_conn.close()
