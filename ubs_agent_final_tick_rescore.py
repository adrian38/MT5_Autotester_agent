"""Reconciliacion y repuntuacion del Final Tick desde reportes."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from run_tests import parse_symbol_map
from ubs.memory import AgentMemory, final_tick_table_for_stage, variant_from_candidate_row
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, score_report_file
from ubs_agent_final_tick import (
    _evaluate_final_tick_tick_report,
    _read_ohlc_report_cfg_dates,
    final_tick_ohlc_trades_pending_payload,
    final_tick_similarity,
    final_tick_stage_dir_name,
    final_tick_stage_prefixes,
    final_tick_status_from_similarity,
    lossless_control_gate_from_args,
    normalize_final_tick_stage,
)
from ubs_agent_reports import (
    find_report_for_set,
    report_matches_variant,
)
from ubs_agent_rescore import (
    _batched_memory_updates,
    _rescore_metrics_json,
)
from ubs_agent_universe import (
    min_trades_for_period,
    missing_report_status,
    score_config_for_variant,
)


@dataclass
class _ReconcileLimits:
    """Umbrales con los que se concilia un par de reportes desde disco."""

    broker: str = ""
    min_history_quality: float = 80.0
    min_ohlc_trades: int = 5
    min_trades_w1: int = 2
    min_trades_mn: int = 1
    max_net_delta_pct: float = 35.0
    max_pf_delta_pct: float = 35.0
    max_dd_delta_pct: float = 35.0
    max_trades_delta_pct: float = 35.0
    symbol_suffix: str = ""

    def thresholds(self, from_date: str, to_date: str) -> argparse.Namespace:
        """Umbrales en la forma que espera la evaluacion de final tick."""
        return argparse.Namespace(
            broker=self.broker,
            final_tick_min_history_quality=float(self.min_history_quality),
            symbol_suffix=self.symbol_suffix,
            from_date=from_date,
            to_date=to_date,
            final_tick_max_net_delta_pct=float(self.max_net_delta_pct),
            final_tick_max_pf_delta_pct=float(self.max_pf_delta_pct),
            final_tick_max_dd_delta_pct=float(self.max_dd_delta_pct),
            final_tick_max_trades_delta_pct=float(self.max_trades_delta_pct),
            final_tick_min_trades_w1=int(self.min_trades_w1),
            final_tick_min_trades_mn=int(self.min_trades_mn),
        )


def _reconcile_stage_variant(
    original_variant: Variant, set_name: str, policy_prefix: str, kind: str
) -> Variant:
    """Variante que representa el reporte de una etapa en disco."""
    return Variant(
        path=Path(set_name),
        seed=original_variant.seed,
        target_symbol=original_variant.target_symbol,
        target_period=original_variant.target_period,
        mutated_keys=original_variant.mutated_keys,
        missing_lot_keys=original_variant.missing_lot_keys,
        policy=f"{original_variant.policy}+{policy_prefix}_{kind}",
    )


def _reconcile_ohlc_report(
    score_config: ScoreConfig, symbol_map: dict[str, str], limits: _ReconcileLimits,
    ohlc_variant: Variant, ohlc_report: Path, candidate_id: int,
):
    """Puntua el OHLC en disco y comprueba que sea el del candidato."""
    try:
        ohlc_result = score_report_file(
            ohlc_report,
            config=score_config_for_variant(
                score_config,
                ohlc_variant,
                min_trades_w1=limits.min_trades_w1,
                min_trades_mn=limits.min_trades_mn,
            ),
            broker=limits.broker,
        )
    except Exception as exc:
        print(
            f"AVISO: no pude parsear OHLC Final Tick para reconciliar "
            f"candidate #{candidate_id}: {exc}"
        )
        return None
    ohlc_matches, _ = report_matches_variant(
        ohlc_variant, ohlc_result, symbol_map, limits.symbol_suffix, limits.broker
    )
    return ohlc_result if ohlc_matches else None


def _reconcile_candidate(
    memory: AgentMemory, run_id: int, score_config: ScoreConfig, symbol_map: dict[str, str],
    limits: _ReconcileLimits, row, prefixes: tuple[str, str], policy_prefix: str,
    status_counts: dict[str, int],
) -> None:
    """Registra un candidato cuyo par de reportes ya esta en disco."""
    ohlc_prefix, tick_prefix = prefixes
    candidate_id = int(row["id"])
    source_set = resolve_workspace_path(row["set_path"])
    ohlc_set_name = f"{ohlc_prefix}_{candidate_id:06d}_{source_set.name}"
    tick_set_name = f"{tick_prefix}_{candidate_id:06d}_{source_set.name}"
    ohlc_report = find_report_for_set(Path(ohlc_set_name))
    if ohlc_report is None:
        return
    ohlc_dates = _read_ohlc_report_cfg_dates(ohlc_report)
    if not ohlc_dates[0] or not ohlc_dates[1]:
        return
    original_variant = variant_from_candidate_row(row)
    ohlc_variant = _reconcile_stage_variant(original_variant, ohlc_set_name, policy_prefix, "ohlc")
    ohlc_result = _reconcile_ohlc_report(
        score_config, symbol_map, limits, ohlc_variant, ohlc_report, candidate_id
    )
    if ohlc_result is None:
        return
    thresholds = limits.thresholds(ohlc_dates[0], ohlc_dates[1])
    period_min_ohlc_trades = min_trades_for_period(
        ohlc_variant.target_period,
        int(limits.min_ohlc_trades),
        int(limits.min_trades_w1),
        int(limits.min_trades_mn),
    )
    if ohlc_result.trades < period_min_ohlc_trades:
        payload = final_tick_ohlc_trades_pending_payload(ohlc_result, period_min_ohlc_trades)
        memory.record_candidate_final_tick(
            candidate_id, run_id, "pending_ohlc_trades", ohlc_result, None,
            ohlc_report, None,
            json.dumps(payload, ensure_ascii=True, sort_keys=True), None,
            thresholds.final_tick_min_history_quality,
            thresholds.from_date, thresholds.to_date,
            float(limits.max_net_delta_pct), float(limits.max_pf_delta_pct),
            float(limits.max_dd_delta_pct), float(limits.max_trades_delta_pct),
        )
        status_counts["pending_ohlc_trades"] = status_counts.get("pending_ohlc_trades", 0) + 1
        return
    tick_report = find_report_for_set(Path(tick_set_name))
    if tick_report is None or _read_ohlc_report_cfg_dates(tick_report) != ohlc_dates:
        return
    if _evaluate_final_tick_tick_report(
        memory, thresholds, score_config, symbol_map, run_id,
        candidate_id,
        _reconcile_stage_variant(original_variant, tick_set_name, policy_prefix, "real"),
        ohlc_report, ohlc_result, tick_report, status_counts, reconcile=True,
    ):
        print(f"Final Tick reconcile: candidate #{candidate_id} registrado desde reportes en disco.")


def reconcile_final_tick_reports(
    memory: AgentMemory,
    run_id: int,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    *,
    final_tick_stage: str = "probe",
    **limit_values: object,
) -> dict[str, int]:
    """Concilia desde disco los reportes Final Tick ya generados, sin abrir MT5.

    Para cada candidato robust-accepted sin estado final, busca su par de
    reportes `ohlc_*`/`tick_*` en `reports/`. El par solo se registra cuando el
    OHLC coincide con el target del candidato y ambos reportes comparten el
    mismo rango de fechas (verdad de disco, no la DB ni la UI). Permite
    recuperar el trabajo completado de un proceso interrumpido manualmente.
    """
    limits = _ReconcileLimits(**limit_values)
    rows = [
        row
        for row in memory.accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
        if resolve_workspace_path(row["set_path"]).exists()
    ]
    memory.active_final_tick_stage = normalize_final_tick_stage(final_tick_stage)
    prefixes = final_tick_stage_prefixes(memory.active_final_tick_stage)
    policy_prefix = final_tick_stage_dir_name(memory.active_final_tick_stage)
    status_counts: dict[str, int] = {}
    for row in rows:
        if str(row["final_tick_status"] or "").strip() in {"accepted", "rejected"}:
            continue
        _reconcile_candidate(
            memory, run_id, score_config, symbol_map, limits, row, prefixes, policy_prefix,
            status_counts,
        )
    return status_counts


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
        min_model_profit_factor=float(score_config.min_profit_factor) if is_six_month else None,
        lossless_control_gate=lossless_control_gate_from_args(args) if is_six_month else None,
    )


def _rescore_stored_final_tick_row(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig, row,
    tally: _FinalTickStoredRescore, is_six_month: bool,
) -> None:
    """Repuntua una fila de final tick desde sus metricas guardadas."""
    candidate_id = int(row["id"])
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
        final_tick_min_trades_w1=int(args.final_tick_min_trades_w1),
        final_tick_min_trades_mn=int(args.final_tick_min_trades_mn),
    )


def _record_rescored_final_tick(
    memory: AgentMemory, candidate_id: int, run_id: int, status: str, ohlc_result,
    thresholds: argparse.Namespace, ohlc_report: Path, real_tick_report: Path | None,
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
    if ohlc_report is None or not ohlc_report.exists():
        return False
    from_date, to_date = _read_ohlc_report_cfg_dates(ohlc_report)
    thresholds = _rescore_final_tick_thresholds(
        args,
        from_date or str(row["ft_from_date"] or args.from_date or ""),
        to_date or str(row["ft_to_date"] or args.to_date or ""),
    )
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
        record(missing_report_status(ohlc_variant.target_symbol, args, symbol_map), ohlc_result)
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
