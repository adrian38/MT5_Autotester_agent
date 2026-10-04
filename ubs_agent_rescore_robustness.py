"""Repuntuado de la robustez desde los reportes OOS guardados."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from run_tests import parse_symbol_map
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.path_utils import resolve_workspace_path
from ubs.risk_profit import robustness_result_status
from ubs.score import ScoreConfig, score_report_file
from ubs.weights import DEFAULT_ROBUST_NEGATIVE_BONUS, DEFAULT_ROBUST_POSITIVE_BONUS
from ubs_agent_reports import classify_zero_trade_robustness, report_matches_variant
from ubs_agent_rescore_base import (
    _stored_or_discovered_report,
    _stored_score_config,
    apply_robustness_degradation,
    robustness_degradation_config,
)
from ubs_agent_universe import score_config_for_variant


def _rescore_robustness_rows(memory: AgentMemory) -> list:
    """Filas de robustez con su candidato y la configuracion de su run."""
    return memory.conn.execute(
        """
        select
            c.*,
            cr.status as robust_status,
            cr.report_path as robust_report_path,
            cr.from_date as robust_from_date,
            cr.to_date as robust_to_date,
            cr.positive_bonus as robust_positive_bonus,
            cr.negative_bonus as robust_negative_bonus,
            r.config_json as run_config_json
        from candidate_robustness cr
        join candidates c on c.id = cr.candidate_id
        join runs r on r.id = cr.run_id
        order by cr.run_id, c.generation, c.id
        """
    ).fetchall()


class _RescoreProgress:
    """Avance de un repuntuado largo, con ritmo y tiempo restante."""

    def __init__(self, total_rows: int) -> None:
        self.total_rows = total_rows
        self.step = max(total_rows // 100, 1)
        self.started = time.monotonic()

    def print(self, done: int) -> None:
        """Imprime el avance acumulado hasta esta fila."""
        elapsed = max(time.monotonic() - self.started, 0.001)
        percent = (done / self.total_rows * 100.0) if self.total_rows else 100.0
        rate = done / elapsed if done else 0.0
        remaining = (self.total_rows - done) / rate if rate > 0 else 0.0
        print(
            f"[generalization-v2] {done}/{self.total_rows} ({percent:5.1f}%) "
            f"elapsed={elapsed / 60.0:.1f}m eta={remaining / 60.0:.1f}m",
            flush=True,
        )

    def tick(self, completed: int) -> None:
        """Imprime el avance solo en los cortes configurados."""
        if completed == 0 or completed % self.step == 0:
            self.print(completed)


def _record_rescored_robustness(
    memory: AgentMemory, row, candidate_id: int, run_id: int, result, status: str,
    report: Path, degradation: dict[str, object] | None = None,
) -> None:
    """Guarda el estado repuntuado conservando bonos y fechas originales."""
    extra = {"degradation": degradation} if degradation is not None else {}
    memory.record_candidate_robustness(
        candidate_id,
        run_id,
        result,
        status,
        report,
        str(row["robust_from_date"] or ""),
        str(row["robust_to_date"] or ""),
        float(row["robust_positive_bonus"] or DEFAULT_ROBUST_POSITIVE_BONUS),
        float(row["robust_negative_bonus"] or DEFAULT_ROBUST_NEGATIVE_BONUS),
        **extra,
    )


def _refresh_base_metrics(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    row, candidate_id: int,
) -> object:
    """Vuelve a puntuar la ventana de construccion para comparar contra ella."""
    base_metrics_raw: object = row["metrics_json"]
    base_report = _stored_or_discovered_report(row)
    if base_report is None or not base_report.exists():
        return base_metrics_raw
    try:
        base_result = score_report_file(
            base_report,
            config=_stored_score_config(row["metrics_json"], score_config),
            broker=args.broker,
        )
    except Exception as exc:
        print(f"AVISO: no pude actualizar base candidate #{candidate_id}: {exc}")
        return base_metrics_raw
    base_metrics_raw = base_result.to_json()
    memory.conn.execute(
        "update candidates set score=?, metrics_json=? where id=?",
        (base_result.score, base_metrics_raw, candidate_id),
    )
    return base_metrics_raw


def _rescored_robustness_verdict(
    row, variant, report: Path, result, matches: bool, mismatch_reason: str,
    base_metrics_raw: object, degradation_config, candidate_id: int,
):
    """Estado y degradacion de un reporte de robustez ya puntuado."""
    if not matches:
        print(f"AVISO: reporte robustez no coincide para candidate #{candidate_id}: {mismatch_reason}")
        return result, "report_mismatch", {}
    if result.trades <= 0:
        status, degradation = classify_zero_trade_robustness(report, variant)
        return result, status, degradation
    result, degradation = apply_robustness_degradation(
        result,
        base_metrics_raw=base_metrics_raw,
        run_config_raw=row["run_config_json"],
        oos_from_date=row["robust_from_date"],
        oos_to_date=row["robust_to_date"],
        config=degradation_config,
    )
    return result, robustness_result_status(result), degradation


def _rescore_robustness_row(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    symbol_map: dict[str, str], row, degradation_config, status_counts: dict[str, int],
) -> bool:
    """Repuntua una fila de robustez; False si no tiene reporte local."""
    report_raw = str(row["robust_report_path"] or "").strip()
    report = resolve_workspace_path(report_raw) if report_raw else None
    if report is None or not report.exists():
        return False
    candidate_id = int(row["id"])
    run_id = int(row["run_id"])
    variant = variant_from_candidate_row(row)
    try:
        result = score_report_file(
            report,
            config=score_config_for_variant(
                score_config,
                variant,
                min_trades_w1=args.min_trades_w1,
                min_trades_mn=args.min_trades_mn,
            ),
            broker=args.broker,
            include_generalization_bootstrap=True,
            risk_stage="oos",
        )
    except Exception as exc:
        print(f"AVISO: no pude parsear robustez candidate #{candidate_id}: {exc}")
        _record_rescored_robustness(
            memory, row, candidate_id, run_id, None, "parse_error", report
        )
        status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
        return True
    matches, mismatch_reason = report_matches_variant(
        variant, result, symbol_map, args.symbol_suffix, args.broker
    )
    base_metrics_raw: object = row["metrics_json"]
    if matches and result.trades > 0 and result.accepted:
        base_metrics_raw = _refresh_base_metrics(
            args, memory, score_config, row, candidate_id
        )
    result, status, degradation = _rescored_robustness_verdict(
        row, variant, report, result, matches, mismatch_reason, base_metrics_raw,
        degradation_config, candidate_id,
    )
    _record_rescored_robustness(
        memory, row, candidate_id, run_id, result, status, report, degradation
    )
    status_counts[status] = status_counts.get(status, 0) + 1
    return True


def _print_rescore_summary(
    memory: AgentMemory, status_counts: dict[str, int], skipped_no_report: int
) -> None:
    """Resume el repuntuado y las etapas posteriores que quedaron invalidadas."""
    removed = memory.cleanup_stale_stage_rows()
    if any(removed.values()):
        print(
            "Etapas posteriores invalidadas: "
            + ", ".join(f"{stage}={count}" for stage, count in removed.items() if count)
        )
    total = sum(status_counts.values())
    if status_counts:
        print(
            "Robustez repuntuada con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay resultados de robustez con reporte disponible para repuntuar.")
    if skipped_no_report:
        print(f"Robustez sin reporte local omitida: {skipped_no_report}")
    print(f"Memoria: {memory.path}")


def _rescore_robustness_from_reports(
    args: argparse.Namespace,
    memory: AgentMemory,
    score_config: ScoreConfig,
) -> int:
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = _rescore_robustness_rows(memory)
    status_counts: dict[str, int] = {}
    skipped_no_report = 0
    degradation_config = robustness_degradation_config(args)
    progress = _RescoreProgress(len(rows))
    for index, row in enumerate(rows, start=1):
        progress.tick(index - 1)
        if not _rescore_robustness_row(
            args, memory, score_config, symbol_map, row, degradation_config, status_counts
        ):
            skipped_no_report += 1
    progress.print(len(rows))
    _print_rescore_summary(memory, status_counts, skipped_no_report)
    return 0
