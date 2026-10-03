"""Repuntuado de la regresiva desde las metricas ya guardadas."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ubs.memory import AgentMemory
from ubs.regression_details import _details_payload, _score_config_for_period
from ubs.regression_rules import regression_degradation
from ubs.score import ScoreConfig, ScoreResult, rescore_result


def _regression_verdict(
    args: Any, result: ScoreResult, base_metrics: dict[str, object] | None
) -> tuple[str, tuple[str, ...], dict[str, float]]:
    """Veredicto de la regresiva comparada con la ventana de construccion."""
    if result.trades <= 0:
        return "no_trades", tuple(result.reasons), {}
    degradation_reasons, degradation_audit = regression_degradation(
        base_metrics,
        result.profit_factor,
        result.drawdown_pct,
        min_pf_efficiency=float(getattr(args, "regression_min_pf_efficiency", 0.0)),
        max_dd_ratio=float(getattr(args, "regression_max_dd_ratio", 0.0)),
    )
    combined_reasons = tuple(result.reasons) + degradation_reasons
    return ("accepted" if not combined_reasons else "rejected"), combined_reasons, degradation_audit


def _stored_regression_rows(memory: AgentMemory, run_id: int) -> list:
    """Filas regresivas finales con metricas guardadas para repuntuar."""
    return memory.conn.execute(
        """
        select
            c.id,
            c.run_id,
            c.period,
            c.metrics_json as base_metrics_json,
            rg.status as regression_status,
            rg.report_path as regression_report_path,
            rg.metrics_json as regression_metrics_json,
            rg.details_json as regression_details_json,
            rg.from_date as regression_from_date,
            rg.to_date as regression_to_date
        from candidate_regression rg
        join candidates c on c.id = rg.candidate_id
        where rg.status in ('accepted', 'rejected', 'no_trades')
          and coalesce(rg.metrics_json, '') != ''
          and (? = 0 or c.run_id = ?)
        order by c.run_id, c.generation, c.id
        """,
        (run_id, run_id),
    ).fetchall()


def _stored_base_metrics(row) -> dict | None:
    """Metricas de la ventana de construccion guardadas con el candidato."""
    try:
        base_metrics = json.loads(str(row["base_metrics_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return base_metrics if isinstance(base_metrics, dict) else None


def _stored_actual_dates(row, stored_dates: tuple[str, str]) -> tuple[str, str]:
    """Fechas reales que quedaron anotadas en el detalle de la regresiva."""
    try:
        previous_details = json.loads(str(row["regression_details_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return stored_dates
    if isinstance(previous_details, dict):
        actual_from = str(previous_details.get("actual_from_date") or "").strip()
        actual_to = str(previous_details.get("actual_to_date") or "").strip()
        if actual_from and actual_to:
            return actual_from, actual_to
    return stored_dates


@dataclass
class _StoredRescoreCounts:
    """Recuento del repuntuado desde las metricas ya guardadas."""

    counts: dict[str, int] = field(default_factory=dict)
    invalid_metrics: int = 0
    window_mismatch: int = 0

    def summary(self) -> str:
        """Linea final con lo repuntuado y lo que quedo fuera."""
        return (
            "Regresiva repuntuada desde SQLite: "
            + (", ".join(f"{status}={count}" for status, count in sorted(self.counts.items())) or "sin filas")
            + f"; total={sum(self.counts.values())}; ventana_distinta={self.window_mismatch}; "
            f"invalidos={self.invalid_metrics}"
        )


def _rescore_stored_regression_row(
    args: Any, memory: AgentMemory, score_config: ScoreConfig, row,
    expected_dates: tuple[str, str], tally: _StoredRescoreCounts,
) -> None:
    """Repuntua una fila regresiva desde sus metricas guardadas."""
    stored_dates = (
        str(row["regression_from_date"] or "").strip(),
        str(row["regression_to_date"] or "").strip(),
    )
    if stored_dates != expected_dates:
        tally.window_mismatch += 1
        return
    try:
        result = rescore_result(
            ScoreResult.from_json(str(row["regression_metrics_json"])),
            _score_config_for_period(score_config, str(row["period"] or ""), args),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        tally.invalid_metrics += 1
        print(f"AVISO: metrics_json regresiva invalido candidate #{int(row['id'])}: {exc}")
        return
    status, combined_reasons, degradation_audit = _regression_verdict(
        args, result, _stored_base_metrics(row)
    )
    details_json, points_applied = _details_payload(
        status,
        result,
        args,
        reasons=combined_reasons,
        actual_dates=_stored_actual_dates(row, stored_dates),
        metadata={"degradation": degradation_audit} if degradation_audit else None,
    )
    report_raw = str(row["regression_report_path"] or "").strip()
    memory.record_candidate_regression(
        int(row["id"]),
        int(row["run_id"]),
        status,
        result,
        Path(report_raw) if report_raw else None,
        details_json,
        expected_dates[0],
        expected_dates[1],
        args.regression_positive_points,
        args.regression_negative_points,
        points_applied,
    )
    tally.counts[status] = tally.counts.get(status, 0) + 1


def _rescore_regression_from_memory(
    args: Any, memory: AgentMemory, score_config: ScoreConfig
) -> int:
    """Repuntua la regresiva sin volver a leer reportes del disco."""
    run_id = int(args.regression_run_id or 0)
    expected_dates = (
        str(args.regression_from_date).strip(),
        str(args.regression_to_date).strip(),
    )
    tally = _StoredRescoreCounts()
    for row in _stored_regression_rows(memory, run_id):
        _rescore_stored_regression_row(args, memory, score_config, row, expected_dates, tally)
    print(tally.summary())
    return 0
