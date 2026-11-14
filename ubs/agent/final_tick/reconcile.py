"""Reconciliacion del Final Tick desde el par de reportes en disco.

Vivia junto al repuntuado, en `rescore.py`, y no comparte con el nada mas que
la etapa: aquella recoge trabajo que MT5 ya hizo y esta no lo abre, una busca
el par de reportes por nombre y la otra lee las rutas que guardo la fila. Se
separa para que ninguna de las dos empuje a la otra por encima del techo de
lineas del repositorio.

`rescore.py` reexporta `reconcile_final_tick_reports()`, que es por donde
entran `pass_stage`, `run/modes`, `ubs_agent` y la pantalla de Final Tick.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.models import Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, score_report_file
from ubs.agent.final_tick.similarity import DEFAULT_MAX_DD_GAP_PP
from ubs.agent.final_tick.evaluate import (
    _evaluate_final_tick_tick_report,
    _read_ohlc_report_cfg_dates,
    final_tick_ohlc_trades_pending_payload,
    final_tick_stage_dir_name,
    final_tick_stage_prefixes,
    normalize_final_tick_stage,
)
from ubs.agent.evaluation.reports import (
    find_report_for_set,
    report_matches_variant,
)
from ubs.agent.generation.universe import (
    min_trades_for_period,
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
    max_dd_gap_pp: float = DEFAULT_MAX_DD_GAP_PP
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
            final_tick_max_dd_gap_pp=float(self.max_dd_gap_pp),
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
            max_dd_gap_pp=float(limits.max_dd_gap_pp),
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
