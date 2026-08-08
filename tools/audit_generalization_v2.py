from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import account_memory_path  # noqa: E402


FORMULA_VERSION = "2"
DEGRADATION_VERSION = "robustness_degradation_v2"
REQUIRED_CHECKS: dict[str, float] = {
    "net_retention": 0.50,
    "pf_edge_retention": 0.50,
    "recovery_retention": 0.50,
    "dd_inflation": 2.00,
    "trade_rate_retention": 0.50,
    "residual_profit_ratio": 0.20,
    "oos_positive_month_ratio": 0.50,
    "trade_curve_stability": 0.60,
    "stability_retention": 0.75,
    "bootstrap_net_positive_probability": 0.95,
    "bootstrap_pf_p05": 1.05,
}
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
            "Audita la migracion de reglas de degradacion v2 de robustez para "
            "RoboForex comparando la memoria actual con un backup pre-v2."
        )
    )
    parser.add_argument("--account-type", choices=("ECN", "PRO"), default="ECN")
    parser.add_argument("--memory", type=Path, help="BD actual; por defecto la memoria RoboForex de la cuenta.")
    parser.add_argument("--before", type=Path, help="Backup anterior a v2; por defecto se descubre automaticamente.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR / "outputs" / "audits",
        help="Directorio para JSON, TXT y CSV.",
    )
    parser.add_argument("--skip-integrity-check", action="store_true")
    return parser.parse_args()


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


def safe_json(raw: object) -> dict[str, Any] | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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


def discover_before_database(account_type: str) -> Path:
    pattern = f"ubs_memory_ROBOFOREX_{account_type}_pre_generalization_v2_*.sqlite"
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


def resolved_report_exists(raw: object) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    path = Path(text)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.exists()


def issue(severity: str, code: str, count: int, detail: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "count": int(count), "detail": detail}


def audit_current_rows(rows: dict[int, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: Counter[str] = Counter()
    check_stats: dict[str, Counter[str]] = {name: Counter() for name in REQUIRED_CHECKS}
    check_threshold_mismatches: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []

    for row in rows.values():
        status = str(row.get("status") or "")
        metrics = safe_json(row.get("metrics_json"))
        degradation = safe_json(row.get("degradation_json"))
        if metrics is None:
            counters["invalid_metrics_json"] += 1
        elif str(metrics.get("score_formula_version", "")) != FORMULA_VERSION:
            counters["wrong_score_formula_version"] += 1
        else:
            counters["score_v2"] += 1

        if not resolved_report_exists(row.get("report_path")):
            counters["missing_report_file"] += 1

        expected_accepted = 1 if status == "accepted" else 0
        if status in {"accepted", "rejected", "no_trades"}:
            try:
                stored_accepted = int(row.get("accepted") or 0)
            except (TypeError, ValueError):
                stored_accepted = -1
            if stored_accepted != expected_accepted:
                counters["accepted_flag_mismatch"] += 1

        if status == "no_trades":
            if degradation not in (None, {}):
                counters["no_trades_with_degradation"] += 1
            continue

        if degradation is None:
            counters["invalid_degradation_json"] += 1
            continue
        if str(degradation.get("version", "")) != DEGRADATION_VERSION:
            counters["wrong_degradation_version"] += 1
            continue
        counters["degradation_v2"] += 1
        checks = degradation.get("checks")
        if not isinstance(checks, dict):
            counters["missing_checks_object"] += 1
            continue

        missing_checks = set(REQUIRED_CHECKS) - set(checks)
        if missing_checks:
            counters["rows_missing_required_checks"] += 1
            for name in missing_checks:
                check_stats[name]["missing"] += 1

        available_results: list[bool] = []
        for name, expected_threshold in REQUIRED_CHECKS.items():
            raw_check = checks.get(name)
            if not isinstance(raw_check, dict):
                continue
            stats = check_stats[name]
            stats["total"] += 1
            if bool(raw_check.get("enabled", False)):
                stats["enabled"] += 1
            if bool(raw_check.get("available", False)):
                stats["available"] += 1
                accepted = bool(raw_check.get("accepted", False))
                stats["passed" if accepted else "failed"] += 1
                if bool(raw_check.get("enabled", False)):
                    available_results.append(accepted)
            else:
                stats["unavailable"] += 1
            try:
                threshold = float(raw_check.get("threshold"))
            except (TypeError, ValueError):
                check_threshold_mismatches[name] += 1
            else:
                if abs(threshold - expected_threshold) > 1e-9:
                    check_threshold_mismatches[name] += 1

        recovery = checks.get("recovery_retention")
        if isinstance(recovery, dict) and not {
            "base_annualized",
            "oos_annualized",
        }.issubset(recovery):
            counters["recovery_duration_fields_missing"] += 1

        absolute_accepted = bool(degradation.get("absolute_accepted", False))
        computed_final = absolute_accepted and all(available_results)
        stored_final = bool(degradation.get("final_accepted", degradation.get("accepted", False)))
        if computed_final != stored_final:
            counters["degradation_final_recompute_mismatch"] += 1
        expected_status = "accepted" if stored_final else "rejected"
        if status != expected_status:
            counters["status_vs_degradation_mismatch"] += 1

    critical_fields = (
        "invalid_metrics_json",
        "wrong_score_formula_version",
        "invalid_degradation_json",
        "wrong_degradation_version",
        "missing_checks_object",
        "rows_missing_required_checks",
        "accepted_flag_mismatch",
        "recovery_duration_fields_missing",
        "degradation_final_recompute_mismatch",
        "status_vs_degradation_mismatch",
    )
    for name in critical_fields:
        if counters[name]:
            issues.append(issue("critical", name, counters[name], "Inconsistencia en filas actuales de robustez."))
    if counters["missing_report_file"]:
        issues.append(
            issue(
                "warning",
                "missing_report_file",
                counters["missing_report_file"],
                "La fila fue auditada, pero el reporte local ya no existe.",
            )
        )
    for name, count in sorted(check_threshold_mismatches.items()):
        if count:
            issues.append(
                issue(
                    "critical",
                    f"threshold_mismatch:{name}",
                    count,
                    f"El umbral guardado no coincide con {REQUIRED_CHECKS[name]}",
                )
            )
    for name, stats in sorted(check_stats.items()):
        if stats["unavailable"]:
            issues.append(
                issue(
                    "warning",
                    f"unavailable_check:{name}",
                    stats["unavailable"],
                    "La regla quedo neutral en estas filas por falta de datos comparables.",
                )
            )

    summary = dict(counters)
    summary["total"] = len(rows)
    summary["checks"] = {name: dict(stats) for name, stats in check_stats.items()}
    summary["threshold_mismatches"] = dict(check_threshold_mismatches)
    return summary, issues


def stage_snapshot(
    candidate_id: int,
    probe: dict[int, str],
    six_month: dict[int, str],
    regression: dict[int, str],
    portfolio: set[int],
) -> dict[str, Any]:
    return {
        "final_tick": probe.get(candidate_id),
        "final_tick_6m": six_month.get(candidate_id),
        "regression": regression.get(candidate_id),
        "portfolio_member": candidate_id in portfolio,
    }


def full_downstream_rows(snapshot: dict[str, Any]) -> bool:
    return all(snapshot.get(name) is not None for name in ("final_tick", "final_tick_6m", "regression"))


def full_pass_chain(snapshot: dict[str, Any]) -> bool:
    return (
        snapshot.get("final_tick") in {"accepted", "pending_ohlc_trades"}
        and snapshot.get("final_tick_6m") == "accepted"
        and snapshot.get("regression") == "accepted"
    )


def pipeline_summary(rows: dict[int, dict[str, Any]], stages: dict[str, dict[int, str]]) -> dict[str, int]:
    eligible_probe = {
        candidate_id
        for candidate_id, row in rows.items()
        if row.get("base_status") == "accepted" and row.get("status") == "accepted"
    }
    probe = stages["final_tick"]
    six_month = stages["final_tick_6m"]
    regression = stages["regression"]
    eligible_6m = {
        candidate_id
        for candidate_id in eligible_probe
        if probe.get(candidate_id) in {"accepted", "pending_ohlc_trades"}
    }
    eligible_regression = {
        candidate_id for candidate_id in eligible_6m if six_month.get(candidate_id) == "accepted"
    }
    return {
        "eligible_final_tick": len(eligible_probe),
        "missing_final_tick": sum(candidate_id not in probe for candidate_id in eligible_probe),
        "eligible_final_tick_6m": len(eligible_6m),
        "missing_final_tick_6m": sum(candidate_id not in six_month for candidate_id in eligible_6m),
        "eligible_regression": len(eligible_regression),
        "missing_regression": sum(candidate_id not in regression for candidate_id in eligible_regression),
    }


def build_audit(
    current_path: Path,
    before_path: Path,
    *,
    integrity_check: bool = True,
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
            "scope": "ROBOFOREX",
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


def write_text_report(audit: dict[str, Any], path: Path) -> None:
    newly = audit["newly_accepted"]
    pipeline = audit["pipeline"]
    coverage = audit["coverage"]
    lines = [
        "AUDITORIA GENERALIZATION-V2 - ROBOFOREX",
        f"Veredicto: {audit['verdict']}",
        f"Actual: {audit['current_database']}",
        f"Antes:  {audit['before_database']}",
        f"Integridad: {audit['integrity']}",
        "",
        f"Estados antes:  {audit['status_counts']['before']}",
        f"Estados ahora:  {audit['status_counts']['current']}",
        f"Transiciones:   {audit['transition_counts']}",
        "",
        f"Cobertura score v2: {coverage.get('score_v2', 0)}/{coverage['total']}",
        f"Cobertura degradacion v2: {coverage.get('degradation_v2', 0)}/{coverage['total']}",
        "",
        f"Nuevos accepted: {newly['count']}",
        f"  Con alguna etapa posterior previa: {newly['with_prior_any_downstream']}",
        f"  Con todas las etapas posteriores previas: {newly['with_prior_complete_downstream']}",
        f"  Con cadena previa completamente accepted: {newly['with_prior_full_pass_chain']}",
        f"  Sin Final Tick actual: {newly['currently_missing_final_tick']}",
        f"  Con degradacion completa: {newly['with_complete_degradation_data']}",
        f"  Con degradacion incompleta/neutral: {newly['with_incomplete_degradation_data']}",
        f"  Checks neutrales en nuevos accepted: {newly['unavailable_checks']}",
        f"  Motivos anteriores: {newly['previous_reasons']}",
        "",
        f"Nuevos rejected: {audit['newly_rejected']['count']}",
        f"  Etapas previas invalidadas: {audit['newly_rejected']['prior_stage_rows_invalidated']}",
        f"  Etapas incompatibles que permanecen: {audit['newly_rejected']['with_stale_current_downstream']}",
        "",
        f"Pipeline actual: {pipeline}",
        "",
        "Disponibilidad de checks:",
    ]
    for name, stats in coverage["checks"].items():
        lines.append(f"  {name}: {stats}")
    lines.extend(["", "Incidencias:"])
    if audit["issues"]:
        for item in audit["issues"]:
            lines.append(
                f"  [{item['severity'].upper()}] {item['code']}={item['count']}: {item['detail']}"
            )
    else:
        lines.append("  Ninguna.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_changed_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "candidate_id",
        "run_id",
        "symbol",
        "period",
        "before_status",
        "current_status",
        "before_final_tick",
        "before_final_tick_6m",
        "before_regression",
        "before_portfolio_member",
        "current_final_tick",
        "current_final_tick_6m",
        "current_regression",
        "current_portfolio_member",
        "prior_any_downstream",
        "prior_complete_downstream",
        "prior_full_pass_chain",
        "current_degradation_complete",
        "current_unavailable_checks",
        "before_reasons",
        "current_reasons",
        "before_score",
        "current_score",
        "set_path",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            before = row["before_downstream"]
            current = row["current_downstream"]
            writer.writerow(
                {
                    **{name: row.get(name, "") for name in fieldnames},
                    "before_final_tick": before.get("final_tick"),
                    "before_final_tick_6m": before.get("final_tick_6m"),
                    "before_regression": before.get("regression"),
                    "before_portfolio_member": before.get("portfolio_member"),
                    "current_final_tick": current.get("final_tick"),
                    "current_final_tick_6m": current.get("final_tick_6m"),
                    "current_regression": current.get("regression"),
                    "current_portfolio_member": current.get("portfolio_member"),
                    "current_unavailable_checks": "|".join(row["current_unavailable_checks"]),
                    "before_reasons": "|".join(str(value) for value in row["before_reasons"]),
                    "current_reasons": "|".join(str(value) for value in row["current_reasons"]),
                }
            )


def main() -> int:
    args = parse_args()
    account = str(args.account_type).upper()
    current_path = (args.memory or account_memory_path(BASE_DIR, account, "ROBOFOREX")).resolve()
    before_path = (args.before or discover_before_database(account)).resolve()
    if not current_path.exists():
        raise SystemExit(f"No existe la BD actual: {current_path}")
    if not before_path.exists():
        raise SystemExit(f"No existe el backup pre-v2: {before_path}")

    print(f"Auditando RoboForex {account}", flush=True)
    print(f"Actual: {current_path}", flush=True)
    print(f"Antes:  {before_path}", flush=True)
    audit = build_audit(
        current_path,
        before_path,
        integrity_check=not args.skip_integrity_check,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"generalization_v2_audit_ROBOFOREX_{account}_{stamp}"
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
