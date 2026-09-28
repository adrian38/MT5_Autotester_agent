from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import (  # noqa: E402
    BROKERS,
    DEFAULT_BROKER,
    account_memory_path,
    account_scope_key,
    account_types_for_broker,
    normalize_broker,
)
from tools.audit_generalization_output import (  # noqa: E402
    write_changed_csv,
    write_text_report,
)
from tools.audit_generalization_rows import (  # noqa: E402
    DEGRADATION_VERSION,
    FORMULA_VERSION,
    REQUIRED_CHECKS,
    SCORED_STATUSES,
    audit_current_rows,
    full_downstream_rows,
    full_pass_chain,
    issue,
    pipeline_summary,
    resolved_report_exists,
    safe_json,
    stage_snapshot,
)


TERMINAL_REGRESSION_STATUSES = {
    "accepted",
    "rejected",
    "no_trades",
    "watchdog_timeout",
    "report_mismatch",
    "parse_error",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audita la migracion de reglas de degradacion v2 de robustez "
            "comparando la memoria actual con un backup pre-v2."
        )
    )
    parser.add_argument(
        "--broker",
        default=DEFAULT_BROKER,
        help=f"Broker a auditar ({', '.join(BROKERS)}); por defecto {DEFAULT_BROKER}.",
    )
    parser.add_argument(
        "--account-type",
        default=None,
        help="Tipo de cuenta del broker; por defecto la primera cuenta configurada.",
    )
    parser.add_argument("--memory", type=Path, help="BD actual; por defecto la memoria del broker/cuenta.")
    parser.add_argument("--before", type=Path, help="Backup anterior a v2; por defecto se descubre automaticamente.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR / "outputs" / "audits",
        help="Directorio para JSON, TXT y CSV.",
    )
    parser.add_argument("--skip-integrity-check", action="store_true")
    return parser.parse_args()


def resolve_scope(raw_broker: object, raw_account: object) -> tuple[str, str]:
    """Resolve BROKER/ACCOUNT, failing loudly instead of silently defaulting."""
    requested = str(raw_broker or DEFAULT_BROKER).strip().upper().replace(" ", "")
    broker = normalize_broker(requested)
    # normalize_broker() falls back to DEFAULT_BROKER for unknown input; only the
    # ROBO/ROBOFOREX aliases legitimately resolve to it.
    if broker == DEFAULT_BROKER and requested not in {"ROBO", DEFAULT_BROKER}:
        raise SystemExit(
            f"Broker desconocido: {requested!r}. Opciones: {', '.join(BROKERS)}"
        )

    accounts = account_types_for_broker(broker)
    if raw_account is None:
        return broker, accounts[0]
    account = str(raw_account).strip().upper()
    if account not in accounts:
        raise SystemExit(
            f"Cuenta {account!r} no valida para {broker}. Opciones: {', '.join(accounts)}"
        )
    return broker, account


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


def is_pre_v2_database(path: Path) -> bool:
    try:
        conn = connect_read_only(path)
        try:
            columns = table_columns(conn, "candidate_robustness")
            if not columns:
                return False
            if "degradation_json" not in columns:
                return True
            count = conn.execute(
                """
                select count(*) from candidate_robustness
                where json_valid(degradation_json)
                  and json_extract(degradation_json, '$.version')=?
                """,
                (DEGRADATION_VERSION,),
            ).fetchone()[0]
            return int(count or 0) == 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def discover_before_database(broker: str, account_type: str) -> Path:
    scope = account_scope_key(broker, account_type)
    pattern = f"ubs_memory_{scope}_pre_generalization_v2_*.sqlite"
    candidates = sorted((BASE_DIR / "outputs" / "backups").glob(pattern), reverse=True)
    for path in candidates:
        if is_pre_v2_database(path):
            return path
    raise FileNotFoundError(f"No se encontro un backup pre-v2 valido con patron {pattern}")


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


def build_audit(
    current_path: Path,
    before_path: Path,
    *,
    integrity_check: bool = True,
    scope: str = DEFAULT_BROKER,
) -> dict[str, Any]:
    current = connect_read_only(current_path)
    before = connect_read_only(before_path)
    try:
        current_rows = load_robustness_rows(current)
        before_rows = load_robustness_rows(before)
        current_stages = {
            "final_tick": load_stage_statuses(current, "candidate_final_tick"),
            "final_tick_6m": load_stage_statuses(current, "candidate_final_tick_6m"),
            "regression": load_stage_statuses(current, "candidate_regression"),
        }
        before_stages = {
            "final_tick": load_stage_statuses(before, "candidate_final_tick"),
            "final_tick_6m": load_stage_statuses(before, "candidate_final_tick_6m"),
            "regression": load_stage_statuses(before, "candidate_regression"),
        }
        current_portfolio = load_portfolio_members(current)
        before_portfolio = load_portfolio_members(before)
        coverage, issues = audit_current_rows(current_rows)

        eligible_probe_ids = {
            candidate_id
            for candidate_id, row in current_rows.items()
            if row.get("base_status") == "accepted" and row.get("status") == "accepted"
        }
        invalid_probe_ids = set(current_stages["final_tick"]) - eligible_probe_ids
        eligible_6m_ids = {
            candidate_id
            for candidate_id in eligible_probe_ids
            if current_stages["final_tick"].get(candidate_id)
            in {"accepted", "pending_ohlc_trades"}
        }
        invalid_6m_ids = set(current_stages["final_tick_6m"]) - eligible_6m_ids
        eligible_regression_ids = {
            candidate_id
            for candidate_id in eligible_6m_ids
            if current_stages["final_tick_6m"].get(candidate_id) == "accepted"
        }
        invalid_regression_ids = set(current_stages["regression"]) - eligible_regression_ids
        for stage, invalid_ids in (
            ("final_tick", invalid_probe_ids),
            ("final_tick_6m", invalid_6m_ids),
            ("regression", invalid_regression_ids),
        ):
            if invalid_ids:
                issues.append(
                    issue(
                        "critical",
                        f"incompatible_current_stage:{stage}",
                        len(invalid_ids),
                        "La etapa existe aunque la cadena actual ya no la hace elegible.",
                    )
                )

        current_ids = set(current_rows)
        before_ids = set(before_rows)
        missing_ids = sorted(before_ids - current_ids)
        added_ids = sorted(current_ids - before_ids)
        if missing_ids:
            issues.append(issue("critical", "robustness_rows_removed", len(missing_ids), "Filas presentes en el backup ya no existen."))
        if added_ids:
            issues.append(issue("warning", "robustness_rows_added", len(added_ids), "Filas nuevas posteriores al backup."))

        transition_counts: Counter[str] = Counter()
        changed: list[dict[str, Any]] = []
        newly_accepted: list[dict[str, Any]] = []
        newly_rejected: list[dict[str, Any]] = []
        for candidate_id in sorted(current_ids & before_ids):
            old = before_rows[candidate_id]
            new = current_rows[candidate_id]
            old_status = str(old.get("status") or "")
            new_status = str(new.get("status") or "")
            transition_counts[f"{old_status}->{new_status}"] += 1
            if old_status == new_status:
                continue
            old_stage = stage_snapshot(
                candidate_id,
                before_stages["final_tick"],
                before_stages["final_tick_6m"],
                before_stages["regression"],
                before_portfolio,
            )
            new_stage = stage_snapshot(
                candidate_id,
                current_stages["final_tick"],
                current_stages["final_tick_6m"],
                current_stages["regression"],
                current_portfolio,
            )
            detail = {
                "candidate_id": candidate_id,
                "run_id": int(new.get("run_id") or 0),
                "symbol": str(new.get("symbol") or ""),
                "period": str(new.get("period") or ""),
                "set_path": str(new.get("set_path") or ""),
                "before_status": old_status,
                "current_status": new_status,
                "before_downstream": old_stage,
                "current_downstream": new_stage,
                "prior_any_downstream": any(
                    old_stage.get(name) is not None
                    for name in ("final_tick", "final_tick_6m", "regression")
                ),
                "prior_complete_downstream": full_downstream_rows(old_stage),
                "prior_full_pass_chain": full_pass_chain(old_stage),
                "current_complete_downstream": full_downstream_rows(new_stage),
                "current_full_pass_chain": full_pass_chain(new_stage),
            }
            old_metrics = safe_json(old.get("metrics_json")) or {}
            new_metrics = safe_json(new.get("metrics_json")) or {}
            new_degradation = safe_json(new.get("degradation_json")) or {}
            new_checks = new_degradation.get("checks")
            if not isinstance(new_checks, dict):
                new_checks = {}
            detail.update(
                {
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
            )
            changed.append(detail)
            if old_status != "accepted" and new_status == "accepted":
                newly_accepted.append(detail)
            if old_status == "accepted" and new_status != "accepted":
                newly_rejected.append(detail)

        lost_prior_downstream = [
            row
            for row in newly_accepted
            if row["prior_any_downstream"]
            and row["before_downstream"] != row["current_downstream"]
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
            for row in newly_rejected
            if any(
                row["current_downstream"].get(name) is not None
                for name in ("final_tick", "final_tick_6m", "regression")
            )
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
            row for row in newly_accepted if row["current_downstream"]["final_tick"] is None
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

        integrity = {
            "current": str(current.execute("pragma integrity_check").fetchone()[0]) if integrity_check else "skipped",
            "before": str(before.execute("pragma integrity_check").fetchone()[0]) if integrity_check else "skipped",
        }
        for label, value in integrity.items():
            if value not in {"ok", "skipped"}:
                issues.append(issue("critical", f"integrity:{label}", 1, value))

        unavailable_newly_accepted: Counter[str] = Counter()
        cleared_reason_counts: Counter[str] = Counter()
        for row in newly_accepted:
            unavailable_newly_accepted.update(row["current_unavailable_checks"])
            reasons = row.get("before_reasons")
            if isinstance(reasons, list):
                cleared_reason_counts.update(str(reason) for reason in reasons)
        newly_accepted_summary = {
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
        pipeline = pipeline_summary(current_rows, current_stages)
        if pipeline["missing_regression"]:
            issues.append(
                issue(
                    "warning",
                    "accepted_6m_missing_regression",
                    pipeline["missing_regression"],
                    "Candidatos Final Tick 6M accepted sin resultado regresivo.",
                )
            )
        invalidated_stage_counts = {
            stage: sum(row["before_downstream"].get(stage) is not None for row in newly_rejected)
            for stage in ("final_tick", "final_tick_6m", "regression")
        }
        invalidated_stage_counts["portfolio_member"] = sum(
            row["before_downstream"].get("portfolio_member", False) for row in newly_rejected
        )
        result = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scope": scope,
            "current_database": str(current_path.resolve()),
            "before_database": str(before_path.resolve()),
            "integrity": integrity,
            "status_counts": {
                "before": status_counts(before),
                "current": status_counts(current),
            },
            "row_identity": {
                "before": len(before_rows),
                "current": len(current_rows),
                "missing_candidate_ids": missing_ids,
                "added_candidate_ids": added_ids,
            },
            "transition_counts": dict(sorted(transition_counts.items())),
            "coverage": coverage,
            "pipeline": pipeline,
            "newly_accepted": newly_accepted_summary,
            "newly_rejected": {
                "count": len(newly_rejected),
                "with_stale_current_downstream": len(stale_after_rejection),
                "prior_stage_rows_invalidated": invalidated_stage_counts,
                "details": newly_rejected,
            },
            "changed_candidates": changed,
            "issues": issues,
        }
        if any(item["severity"] == "critical" for item in issues):
            result["verdict"] = "FAIL"
        elif any(item["severity"] == "warning" for item in issues):
            result["verdict"] = "PASS_WITH_WARNINGS"
        else:
            result["verdict"] = "PASS"
        return result
    finally:
        before.close()
        current.close()


def main() -> int:
    args = parse_args()
    broker, account = resolve_scope(args.broker, args.account_type)
    scope = account_scope_key(broker, account)
    current_path = (args.memory or account_memory_path(BASE_DIR, account, broker)).resolve()
    before_path = (args.before or discover_before_database(broker, account)).resolve()
    if not current_path.exists():
        raise SystemExit(f"No existe la BD actual: {current_path}")
    if not before_path.exists():
        raise SystemExit(f"No existe el backup pre-v2: {before_path}")

    print(f"Auditando {broker} {account}", flush=True)
    print(f"Actual: {current_path}", flush=True)
    print(f"Antes:  {before_path}", flush=True)
    audit = build_audit(
        current_path,
        before_path,
        integrity_check=not args.skip_integrity_check,
        scope=scope,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"generalization_v2_audit_{scope}_{stamp}"
    json_path = output_dir / f"{stem}.json"
    text_path = output_dir / f"{stem}.txt"
    csv_path = output_dir / f"{stem}_transitions.csv"
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    write_text_report(audit, text_path)
    write_changed_csv(audit["changed_candidates"], csv_path)

    newly = audit["newly_accepted"]
    print(f"Veredicto: {audit['verdict']}", flush=True)
    print(f"Transiciones: {audit['transition_counts']}", flush=True)
    print(
        "Nuevos accepted: "
        f"{newly['count']}; con downstream previo={newly['with_prior_any_downstream']}; "
        f"con pipeline previo completo={newly['with_prior_complete_downstream']}; "
        f"sin Final Tick actual={newly['currently_missing_final_tick']}",
        flush=True,
    )
    print(f"Pipeline: {audit['pipeline']}", flush=True)
    print(f"JSON: {json_path}", flush=True)
    print(f"TXT:  {text_path}", flush=True)
    print(f"CSV:  {csv_path}", flush=True)
    return 1 if audit["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
