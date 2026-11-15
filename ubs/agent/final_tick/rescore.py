"""Repuntuacion del Final Tick desde los reportes que guardo cada fila.

La reconciliacion desde disco vive en `reconcile.py` y se reexporta aqui, que
es por donde la llaman desde siempre.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from run_tests import parse_symbol_map
from ubs.memory import AgentMemory, final_tick_table_for_stage, variant_from_candidate_row
from ubs.models import Variant
from ubs.score import ScoreConfig, score_report_file
from ubs.stage_rules_audit import missing_window_date
from ubs.agent.final_tick.reconcile import reconcile_final_tick_reports  # noqa: F401
from ubs.agent.final_tick.evaluate import (
    _evaluate_final_tick_tick_report,
    _read_ohlc_report_cfg_dates,
    final_tick_ohlc_trades_pending_payload,
    final_tick_similarity,
    final_tick_status_from_similarity,
    lossless_control_gate_from_args,
    normalize_final_tick_stage,
)
from ubs.agent.evaluation.reports import (
    report_matches_variant,
)
from ubs.agent.rescore.main import (
    _batched_memory_updates,
    _rescore_metrics_json,
)
from ubs.agent.generation.universe import (
    min_trades_for_period,
    missing_report_status,
    score_config_for_variant,
)


# Lo que cuenta como veredicto en una fila de final tick. Lo demas son estados
# por los que la fila pasa, y de esos ya se ocupa Arreglar pendientes.
FINAL_TICK_VERDICTS = frozenset({"accepted", "rejected"})


def _windowless_verdict(row, from_date: object, to_date: object) -> bool:
    """Si la fila tiene veredicto y lo dio sobre una ventana que no existe.

    MT5 escribe `1970.01.01` cuando el backtest no produjo nada, asi que no hay
    periodo sobre el que juzgar y el veredicto guardado no vale: repuntuarlo no
    lo arregla -la parte rota es el reporte- y dejarlo lo da por bueno sobre una
    ventana que no existio.

    Solo el veredicto estorba. Una fila que ya esta en un estado por el que se
    pasa -pendiente, sin reporte, pocas operaciones- no afirma nada que haya que
    retirar, y reescribirla le borraria el motivo por el que su propio reintento
    la reconoce.
    """
    if str(row["ft_status"] or "").strip().lower() not in FINAL_TICK_VERDICTS:
        return False
    return missing_window_date(from_date) or missing_window_date(to_date)


@dataclass
class _FinalTickStoredRescore:
    """Recuento del repuntuado de final tick desde las metricas guardadas."""

    stage: str
    status_counts: dict[str, int] = field(default_factory=dict)
    invalid_metrics: int = 0
    skipped_without_tick: int = 0

    def count(self, status: str) -> None:
        """Suma un estado al recuento del repuntuado."""
        self.status_counts[status] = self.status_counts.get(status, 0) + 1

    def summary(self) -> str:
        """Linea final con lo repuntuado y lo que quedo fuera."""
        return (
            f"Final Tick {self.stage} repuntuado desde SQLite: "
            + (", ".join(f"{status}={count}" for status, count in sorted(self.status_counts.items())) or "sin filas")
            + f"; total={sum(self.status_counts.values())}; "
            f"sin_tick={self.skipped_without_tick}; invalidos={self.invalid_metrics}"
        )


def _stored_final_tick_rows(memory: AgentMemory, final_tick_table: str) -> list:
    """Filas con metricas OHLC guardadas, listas para repuntuar."""
    return memory.conn.execute(
        f"""
        select
            c.*,
            ft.run_id as ft_run_id,
            ft.status as ft_status,
            ft.ohlc_report_path as ft_ohlc_report_path,
            ft.real_tick_report_path as ft_real_tick_report_path,
            ft.ohlc_metrics_json as ft_ohlc_metrics_json,
            ft.real_tick_metrics_json as ft_real_tick_metrics_json,
            ft.from_date as ft_from_date,
            ft.to_date as ft_to_date
        from {final_tick_table} ft
        join candidates c on c.id = ft.candidate_id
        where coalesce(ft.ohlc_metrics_json, '') != ''
        order by ft.run_id, c.generation, c.id
        """
    ).fetchall()


def _record_stored_final_tick(
    args: argparse.Namespace, memory: AgentMemory, row, status: str, ohlc_result,
    real_result, details: str, history_quality: float | None, stage: str,
) -> None:
    """Guarda el estado repuntuado con los umbrales vigentes."""
    ohlc_report_raw = str(row["ft_ohlc_report_path"] or "").strip()
    real_report_raw = str(row["ft_real_tick_report_path"] or "").strip()
    memory.record_candidate_final_tick(
        int(row["id"]), int(row["ft_run_id"] or row["run_id"]), status,
        ohlc_result, real_result,
        Path(ohlc_report_raw) if ohlc_report_raw else None,
        Path(real_report_raw) if real_report_raw else None,
        details, history_quality,
        float(args.final_tick_min_history_quality),
        str(row["ft_from_date"] or ""), str(row["ft_to_date"] or ""),
        float(args.final_tick_max_net_delta_pct), float(args.final_tick_max_pf_delta_pct),
        float(args.final_tick_max_dd_delta_pct), float(args.final_tick_max_trades_delta_pct),
        max_dd_gap_pp=float(args.final_tick_max_dd_gap_pp),
        final_tick_stage=stage,
    )


def _stored_final_tick_similarity(
    args: argparse.Namespace, score_config: ScoreConfig, ohlc_result, real_result, is_six_month: bool
) -> dict:
    """Compara OHLC y Real Tick con los umbrales de la etapa."""
    return final_tick_similarity(
        ohlc_result,
        real_result,
        min_history_quality=float(args.final_tick_min_history_quality),
        max_net_delta_pct=float(args.final_tick_max_net_delta_pct),
        max_pf_delta_pct=min(float(args.final_tick_max_pf_delta_pct), 30.0)
        if is_six_month else float(args.final_tick_max_pf_delta_pct),
        max_dd_delta_pct=float(args.final_tick_max_dd_delta_pct),
        max_trades_delta_pct=float(args.final_tick_max_trades_delta_pct),
        max_dd_gap_pp=float(args.final_tick_max_dd_gap_pp),
        min_model_profit_factor=float(score_config.min_profit_factor) if is_six_month else None,
        lossless_control_gate=lossless_control_gate_from_args(args) if is_six_month else None,
    )


def _demote_stored_if_windowless(
    args: argparse.Namespace, memory: AgentMemory, row, tally: _FinalTickStoredRescore,
) -> bool:
    """Lo mismo que `_demote_if_windowless()`, desde las metricas guardadas.

    Este es el camino que usa el boton Repuntuar afectados, y por eso la
    comprobacion tiene que estar tambien aqui: las metricas de la fila se
    midieron sobre la ventana que la fila guarda, asi que si esa ventana no
    existe no hay nada que volver a juzgar, lo relea de donde lo relea.
    """
    if not _windowless_verdict(row, row["ft_from_date"], row["ft_to_date"]):
        return False
    _record_stored_final_tick(
        args, memory, row, "no_report", None, None,
        "reporte OHLC sin periodo", None, tally.stage,
    )
    tally.count("no_report")
    return True


def _rescore_stored_final_tick_row(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig, row,
    tally: _FinalTickStoredRescore, is_six_month: bool,
) -> None:
    """Repuntua una fila de final tick desde sus metricas guardadas."""
    candidate_id = int(row["id"])
    if _demote_stored_if_windowless(args, memory, row, tally):
        return
    variant = variant_from_candidate_row(row)
    config = score_config_for_variant(
        score_config,
        variant,
        min_trades_w1=args.final_tick_min_trades_w1,
        min_trades_mn=args.final_tick_min_trades_mn,
    )
    try:
        ohlc_result = _rescore_metrics_json(row["ft_ohlc_metrics_json"], config)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        tally.invalid_metrics += 1
        print(f"AVISO: metrics_json OHLC Final Tick invalido candidate #{candidate_id}: {exc}")
        return
    min_ohlc_trades = min_trades_for_period(
        variant.target_period,
        int(args.final_tick_min_ohlc_trades),
        int(args.final_tick_min_trades_w1),
        int(args.final_tick_min_trades_mn),
    )
    if ohlc_result.trades < min_ohlc_trades:
        payload = final_tick_ohlc_trades_pending_payload(ohlc_result, min_ohlc_trades)
        _record_stored_final_tick(
            args, memory, row, "pending_ohlc_trades", ohlc_result, None,
            json.dumps(payload, ensure_ascii=True, sort_keys=True), None, tally.stage,
        )
        tally.count("pending_ohlc_trades")
        return
    real_raw = row["ft_real_tick_metrics_json"]
    if real_raw is None or not str(real_raw).strip():
        tally.skipped_without_tick += 1
        return
    try:
        real_result = _rescore_metrics_json(real_raw, config)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        tally.invalid_metrics += 1
        print(f"AVISO: metrics_json Real Tick invalido candidate #{candidate_id}: {exc}")
        return
    similarity = _stored_final_tick_similarity(
        args, score_config, ohlc_result, real_result, is_six_month
    )
    status = final_tick_status_from_similarity(similarity) or "rejected"
    _record_stored_final_tick(
        args, memory, row, status, ohlc_result, real_result,
        json.dumps(similarity, ensure_ascii=True, sort_keys=True),
        real_result.history_quality, tally.stage,
    )
    tally.count(status)


@_batched_memory_updates
def rescore_final_tick_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if bool(getattr(args, "rescore_from_reports", False)):
        return _rescore_final_tick_from_reports(args, memory, score_config)
    final_tick_stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    memory.active_final_tick_stage = final_tick_stage
    rows = _stored_final_tick_rows(memory, final_tick_table_for_stage(final_tick_stage))
    tally = _FinalTickStoredRescore(final_tick_stage)
    is_six_month = final_tick_stage == "six_month"
    for row in rows:
        _rescore_stored_final_tick_row(args, memory, score_config, row, tally, is_six_month)
    print(tally.summary())
    print(f"Memoria: {memory.path}")
    return 0


def _rescore_final_tick_thresholds(
    args: argparse.Namespace, from_date: str, to_date: str
) -> argparse.Namespace:
    """Umbrales de final tick vigentes para repuntuar un candidato."""
    return argparse.Namespace(
        broker=args.broker,
        final_tick_min_history_quality=float(args.final_tick_min_history_quality),
        symbol_suffix=args.symbol_suffix,
        from_date=from_date,
        to_date=to_date,
        final_tick_max_net_delta_pct=float(args.final_tick_max_net_delta_pct),
        final_tick_max_pf_delta_pct=float(args.final_tick_max_pf_delta_pct),
        final_tick_max_dd_delta_pct=float(args.final_tick_max_dd_delta_pct),
        final_tick_max_trades_delta_pct=float(args.final_tick_max_trades_delta_pct),
        final_tick_max_dd_gap_pp=float(args.final_tick_max_dd_gap_pp),
        final_tick_min_trades_w1=int(args.final_tick_min_trades_w1),
        final_tick_min_trades_mn=int(args.final_tick_min_trades_mn),
    )


def _record_rescored_final_tick(
    memory: AgentMemory, candidate_id: int, run_id: int, status: str, ohlc_result,
    thresholds: argparse.Namespace, ohlc_report: Path | None, real_tick_report: Path | None,
    details: str | None = None, history_quality: float | None = None,
) -> None:
    """Guarda el estado repuntuado con los umbrales que lo decidieron."""
    memory.record_candidate_final_tick(
        candidate_id, run_id, status, ohlc_result, None,
        ohlc_report, real_tick_report,
        details, history_quality,
        thresholds.final_tick_min_history_quality, thresholds.from_date, thresholds.to_date,
        thresholds.final_tick_max_net_delta_pct, thresholds.final_tick_max_pf_delta_pct,
        thresholds.final_tick_max_dd_delta_pct, thresholds.final_tick_max_trades_delta_pct,
        max_dd_gap_pp=thresholds.final_tick_max_dd_gap_pp,
    )


def _rescore_stage_variant(original_variant: Variant, report: Path, suffix: str) -> Variant:
    """Variante que representa un reporte guardado de esta etapa."""
    return Variant(
        path=report,
        seed=original_variant.seed,
        target_symbol=original_variant.target_symbol,
        target_period=original_variant.target_period,
        mutated_keys=original_variant.mutated_keys,
        missing_lot_keys=original_variant.missing_lot_keys,
        policy=f"{original_variant.policy}+{suffix}",
    )


def _rescore_ohlc_result(
    args: argparse.Namespace, score_config: ScoreConfig, symbol_map: dict[str, str],
    ohlc_variant: Variant, ohlc_report: Path, candidate_id: int, record,
):
    """Puntua el OHLC guardado; None si ya quedo resuelto por su estado."""
    try:
        ohlc_result = score_report_file(
            ohlc_report,
            config=score_config_for_variant(
                score_config,
                ohlc_variant,
                min_trades_w1=args.final_tick_min_trades_w1,
                min_trades_mn=args.final_tick_min_trades_mn,
            ),
            broker=args.broker,
        )
    except Exception as exc:
        print(f"AVISO: no pude parsear OHLC Final Tick candidate #{candidate_id}: {exc}")
        record("parse_error")
        return None
    ohlc_matches, ohlc_mismatch = report_matches_variant(
        ohlc_variant,
        ohlc_result,
        symbol_map,
        args.symbol_suffix,
        args.broker,
    )
    if not ohlc_matches:
        print(f"AVISO: reporte OHLC Final Tick no coincide para candidate #{candidate_id}: {ohlc_mismatch}")
        record("report_mismatch", ohlc_result, quality=ohlc_result.history_quality)
        return None
    period_min_ohlc_trades = min_trades_for_period(
        ohlc_variant.target_period,
        int(args.final_tick_min_ohlc_trades),
        int(args.final_tick_min_trades_w1),
        int(args.final_tick_min_trades_mn),
    )
    if ohlc_result.trades < period_min_ohlc_trades:
        payload = final_tick_ohlc_trades_pending_payload(ohlc_result, period_min_ohlc_trades)
        record(
            "pending_ohlc_trades", ohlc_result,
            details=json.dumps(payload, ensure_ascii=True, sort_keys=True),
        )
        return None
    return ohlc_result


def _rescored_window(args: argparse.Namespace, row, ohlc_report: Path | None) -> tuple[str, str]:
    """La ventana sobre la que se juzgo la fila.

    Manda la del reporte, que es la que de verdad corrio; cuando no se puede
    leer -no existe, o quedo apuntando al disco de otra maquina- queda la que
    guardo la fila. Si esa tampoco es una ventana, `_demote_if_windowless()` lo
    vera y devolvera la fila a pendiente.
    """
    readable = ohlc_report is not None and ohlc_report.exists()
    report_from, report_to = _read_ohlc_report_cfg_dates(ohlc_report) if readable else ("", "")
    return (
        report_from or str(row["ft_from_date"] or args.from_date or ""),
        report_to or str(row["ft_to_date"] or args.to_date or ""),
    )


def _demote_if_windowless(
    memory: AgentMemory, candidate_id: int, run_id: int, row, thresholds,
    ohlc_report: Path | None, real_tick_report: Path | None, status_counts: dict[str, int],
) -> bool:
    """Devuelve a pendiente el veredicto dado sobre una ventana que no existe.

    Lo mismo que hace el repuntuado desde SQLite en
    `_rescore_stored_final_tick_row()`, dicho aqui para el camino que relee los
    reportes de disco: los dos tienen que contestar igual a la misma fila.
    """
    if not _windowless_verdict(row, thresholds.from_date, thresholds.to_date):
        return False
    _record_rescored_final_tick(
        memory, candidate_id, run_id, "no_report", None, thresholds,
        ohlc_report, real_tick_report, "reporte OHLC sin periodo", None,
    )
    status_counts["no_report"] = status_counts.get("no_report", 0) + 1
    return True


def _rescore_final_tick_row(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    symbol_map: dict[str, str], row, status_counts: dict[str, int],
) -> bool:
    """Repuntua un candidato desde sus reportes guardados; False si falta el OHLC."""
    candidate_id = int(row["id"])
    run_id = int(row["ft_run_id"] or row["run_id"])
    ohlc_report_raw = str(row["ft_ohlc_report_path"] or "").strip()
    real_tick_report_raw = str(row["ft_real_tick_report_path"] or "").strip()
    ohlc_report = Path(ohlc_report_raw) if ohlc_report_raw else None
    real_tick_report = Path(real_tick_report_raw) if real_tick_report_raw else None
    thresholds = _rescore_final_tick_thresholds(args, *_rescored_window(args, row, ohlc_report))
    # Antes de exigir el reporte: un veredicto sin ventana no vale tenga o no
    # tenga reporte en disco, y si no lo tiene menos todavia.
    if _demote_if_windowless(memory, candidate_id, run_id, row, thresholds,
                             ohlc_report, real_tick_report, status_counts):
        return True
    if ohlc_report is None or not ohlc_report.exists():
        return False
    original_variant = variant_from_candidate_row(row)
    ohlc_variant = _rescore_stage_variant(original_variant, ohlc_report, "final_tick_ohlc_rescore")

    def record(status: str, result=None, details: str | None = None, quality: float | None = None) -> None:
        _record_rescored_final_tick(
            memory, candidate_id, run_id, status, result, thresholds,
            ohlc_report, real_tick_report, details, quality,
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    ohlc_result = _rescore_ohlc_result(
        args, score_config, symbol_map, ohlc_variant, ohlc_report, candidate_id, record
    )
    if ohlc_result is None:
        return True
    if real_tick_report is None or not real_tick_report.exists():
        record(
            missing_report_status(
                ohlc_variant.target_symbol, args, symbol_map,
                set_path=ohlc_variant.path,
            ),
            ohlc_result,
        )
        return True
    _evaluate_final_tick_tick_report(
        memory,
        thresholds,
        score_config,
        symbol_map,
        run_id,
        candidate_id,
        _rescore_stage_variant(original_variant, real_tick_report, "final_tick_real_rescore"),
        ohlc_report,
        ohlc_result,
        real_tick_report,
        status_counts,
    )
    return True


def _rescore_final_tick_rows(memory: AgentMemory, final_tick_table: str) -> list:
    """Candidatos con reporte de esta etapa, en orden de run y generacion."""
    return memory.conn.execute(
        f"""
        select
            c.*,
            ft.run_id as ft_run_id,
            ft.status as ft_status,
            ft.ohlc_report_path as ft_ohlc_report_path,
            ft.real_tick_report_path as ft_real_tick_report_path,
            ft.from_date as ft_from_date,
            ft.to_date as ft_to_date
        from {final_tick_table} ft
        join candidates c on c.id = ft.candidate_id
        order by ft.run_id, c.generation, c.id
        """
    ).fetchall()


def _rescore_final_tick_from_reports(
    args: argparse.Namespace,
    memory: AgentMemory,
    score_config: ScoreConfig,
) -> int:
    final_tick_stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    memory.active_final_tick_stage = final_tick_stage
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = _rescore_final_tick_rows(memory, final_tick_table_for_stage(final_tick_stage))
    status_counts: dict[str, int] = {}
    skipped_missing = 0
    for row in rows:
        if not _rescore_final_tick_row(
            args, memory, score_config, symbol_map, row, status_counts
        ):
            skipped_missing += 1
    total = sum(status_counts.values())
    if status_counts:
        print(
            "Final Tick repuntuado con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay Final Tick con reportes guardados para repuntuar.")
    if skipped_missing:
        print(f"Final Tick sin OHLC local omitidos: {skipped_missing}")
    print(f"Memoria: {memory.path}")
    return 0
