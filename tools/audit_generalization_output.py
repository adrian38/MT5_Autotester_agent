"""Salidas de texto y CSV de la auditoria de generalizacion v2."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def write_text_report(audit: dict[str, Any], path: Path) -> None:
    newly = audit["newly_accepted"]
    pipeline = audit["pipeline"]
    coverage = audit["coverage"]
    lines = [
        f"AUDITORIA GENERALIZATION-V2 - {audit['scope']}",
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
