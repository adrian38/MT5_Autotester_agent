"""Similitud, estados y fechas del Final Tick."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from ubs.account import DEFAULT_BROKER
from ubs.memory import AgentMemory
from ubs.models import Variant
from ubs.score import ScoreConfig, ScoreResult, run_is_lossless, score_report_file
from ubs_agent_final_tick_similarity import final_tick_similarity
from ubs_agent_config import (
    FINAL_TICK_6M_MIN_DAYS,
    FINAL_TICK_DATE_RETRYABLE_STATUSES,
    FINAL_TICK_RETRYABLE_STATUSES,
    LosslessControlGate,
)
from ubs_agent_reports import (
    report_has_empty_tester_context,
    report_matches_variant,
    tester_log_no_history_metadata,
)
from ubs_agent_robustness import (
    _bounded_profit_factor,
    _relative_delta_pct,
)
from ubs_agent_universe import (
    score_config_for_variant,
)


# Causas que dejan la fila pendiente de historico en vez de rechazarla.
# "history_quality" la emite final_tick_similarity; las otras dos las escriben
# directamente las rutas de fallo tecnico (descarga Real Tick interrumpida y
# reporte sin contexto de tester usable). Las tres tienen que estar aqui: un
# consumidor que solo conozca la primera leeria las otras como rechazos.
FINAL_TICK_PENDING_HISTORY_REASONS = frozenset(
    {"history_quality", "real_tick_no_history", "empty_tester_context"}
)


def final_tick_status_from_similarity(similarity: object) -> str | None:
    """Estado que corresponde a un veredicto de similitud ya calculado.

    Fuente unica de la regla: la escriben las dos rutas que evaluan Final Tick y
    la lee el reparador de la pestana Universo, para que un arreglo no pueda
    divergir de quien genero el estado.

    Devuelve ``None`` cuando el payload no es un veredicto cerrado y por tanto
    no determina un estado: un dict sin ``accepted``, algo que no es dict, o un
    payload ``pending`` (el de ``pending_ohlc_trades``, que corta antes de
    comparar nada). Un ``final_tick_similarity`` recien calculado siempre
    devuelve un estado.
    """

    if not isinstance(similarity, dict) or "accepted" not in similarity:
        return None
    if similarity.get("pending"):
        return None
    reasons = {str(reason) for reason in similarity.get("reasons") or ()}
    if reasons & FINAL_TICK_PENDING_HISTORY_REASONS:
        return "pending_history_quality"
    return "accepted" if bool(similarity.get("accepted")) else "rejected"


def lossless_control_gate_from_args(args: argparse.Namespace) -> LosslessControlGate:
    """Puertas absolutas de 6M para el caso de control OHLC sin perdidas.

    Lee con ``getattr`` porque por aqui pasan namespaces montados a mano (los
    reconciliadores y los tests no rellenan la lista completa de flags); lo que
    falte cae al default del propio dataclass, que es el mismo que publica el
    parser.
    """

    fallback = LosslessControlGate()
    return LosslessControlGate(
        min_trades=int(getattr(args, "final_tick_6m_lossless_min_trades", fallback.min_trades)),
        min_normalized_net_profit=float(
            getattr(args, "final_tick_6m_lossless_min_net", fallback.min_normalized_net_profit)
        ),
        min_profit_factor=float(
            getattr(args, "final_tick_6m_lossless_min_pf", fallback.min_profit_factor)
        ),
        max_drawdown_pct=float(
            getattr(args, "final_tick_6m_lossless_max_dd_pct", fallback.max_drawdown_pct)
        ),
        min_recovery_factor=float(
            getattr(args, "final_tick_6m_lossless_min_recovery", fallback.min_recovery_factor)
        ),
        min_positive_month_ratio=float(
            getattr(
                args,
                "final_tick_6m_lossless_min_positive_month_ratio",
                fallback.min_positive_month_ratio,
            )
        ),
    )


def final_tick_ohlc_trades_pending_payload(ohlc_result: ScoreResult, min_ohlc_trades: int) -> dict[str, object]:
    return {
        "accepted": False,
        "pending": True,
        "reasons": ["ohlc_trades"],
        "checks": {
            "ohlc_trades": {
                "ohlc": int(ohlc_result.trades),
                "min_trades": int(min_ohlc_trades),
                "accepted": False,
            }
        },
    }


def _read_ohlc_report_cfg_dates(report_path: Path) -> tuple[str, str]:
    """Read the configured FromDate/ToDate from an MT5 Strategy Tester HTML report.

    Parses the Period cell which looks like 'H4 (2026.05.01 - 2026.05.31)'.
    Returns empty strings if the file cannot be read or the pattern is not found.
    """
    for encoding in ("utf-16-le", "utf-8", "utf-16"):
        try:
            content = report_path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, UnicodeError, OSError):
            continue
    else:
        return "", ""
    match = re.search(r"\((\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})\)", content)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def final_tick_dates_match(row: sqlite3.Row, from_date: str, to_date: str) -> bool:
    stored_from = str(row["final_tick_from_date"] or "").strip()
    stored_to = str(row["final_tick_to_date"] or "").strip()
    if not stored_from and not stored_to:
        return False
    return stored_from == str(from_date or "").strip() and stored_to == str(to_date or "").strip()


def final_tick_row_pending_for_dates(
    row: sqlite3.Row,
    from_date: str,
    to_date: str,
    *,
    final_tick_stage: str = "probe",
    force_quality_retry: bool = False,
) -> bool:
    stage = normalize_final_tick_stage(final_tick_stage)
    status = str(row["final_tick_status"] or "").strip()
    if not status:
        return True
    if status in FINAL_TICK_RETRYABLE_STATUSES:
        return True
    if force_quality_retry and status == "pending_history_quality":
        return True  # retry regardless of stored dates
    if stage == "six_month" and status == "pending_ohlc_trades":
        return not final_tick_dates_match(row, from_date, to_date)
    if status in FINAL_TICK_DATE_RETRYABLE_STATUSES:
        return stage == "six_month" and not final_tick_dates_match(row, from_date, to_date)
    return False


def final_tick_ohlc_retry_needed_for_dates(
    rows: Iterable[sqlite3.Row],
    from_date: str,
    to_date: str,
    *,
    final_tick_stage: str = "probe",
    force_quality_retry: bool = False,
) -> bool:
    if normalize_final_tick_stage(final_tick_stage) != "six_month":
        return False
    for row in rows:
        if str(row["final_tick_status"] or "").strip() != "pending_ohlc_trades":
            continue
        if final_tick_row_pending_for_dates(
            row,
            from_date,
            to_date,
            final_tick_stage=final_tick_stage,
            force_quality_retry=force_quality_retry,
        ):
            return True
    return False


def final_tick_ohlc_retry_exhausted_for_dates(row: sqlite3.Row, from_date: str, to_date: str) -> bool:
    if str(row["final_tick_status"] or "").strip() != "pending_ohlc_trades":
        return False
    return final_tick_dates_match(row, from_date, to_date)


def normalize_final_tick_stage(value: object) -> str:
    text = str(value or "probe").strip().lower().replace("-", "_")
    if text in {"6m", "sixmonth", "six_month"}:
        return "six_month"
    return "probe"


def final_tick_stage_label(stage: str) -> str:
    return "Final Tick 6M" if stage == "six_month" else "Final Tick"


def final_tick_stage_dir_name(stage: str) -> str:
    return "final_tick_6m" if stage == "six_month" else "final_tick"


def final_tick_stage_prefixes(stage: str, *, ohlc_retry: bool = False) -> tuple[str, str]:
    if stage == "six_month":
        return ("ohlc6m_retry" if ohlc_retry else "ohlc6m", "tick6m")
    return ("ohlc", "tick")


def validate_final_tick_stage_dates(stage: str, from_date: str, to_date: str) -> str | None:
    if stage != "six_month":
        return None
    from_text = from_date.strip()
    to_text = to_date.strip()
    try:
        start = datetime.strptime(from_text, "%Y.%m.%d")
        end = datetime.strptime(to_text, "%Y.%m.%d")
    except ValueError:
        return "Final Tick 6M requiere fechas en formato YYYY.MM.DD."
    days = (end - start).days
    if days < FINAL_TICK_6M_MIN_DAYS:
        min_to_date = (start + timedelta(days=FINAL_TICK_6M_MIN_DAYS)).strftime("%Y.%m.%d")
        return (
            f"Final Tick 6M requiere un rango minimo de {FINAL_TICK_6M_MIN_DAYS} dias; "
            f"rango actual {days} dias ({from_text} -> {to_text}). "
            f"Usa Hasta >= {min_to_date}."
        )
    return None


def _evaluate_final_tick_tick_report(
    memory: AgentMemory,
    args: argparse.Namespace,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    run_id: int,
    candidate_id: int,
    real_tick_variant: Variant,
    ohlc_report: Path,
    ohlc_result: ScoreResult,
    real_tick_report: Path,
    status_counts: dict[str, int],
    *,
    reconcile: bool = False,
) -> bool:
    """Evalua un reporte Real Tick contra su OHLC y registra el resultado.

    Con reconcile=True solo registra cuando el reporte es utilizable
    (parseable, symbol/TF correctos); si no lo es devuelve False sin tocar
    memoria, para que el candidato vuelva a la cola de ejecucion.
    """
    tick_score_config = score_config_for_variant(
        score_config,
        real_tick_variant,
        min_trades_w1=args.final_tick_min_trades_w1,
        min_trades_mn=args.final_tick_min_trades_mn,
    )
    try:
        real_tick_result = score_report_file(real_tick_report, config=tick_score_config, broker=args.broker)
    except Exception as exc:
        if reconcile:
            return False
        print(f"AVISO: no pude parsear Real Tick Final Tick candidate #{candidate_id}: {exc}")
        memory.record_candidate_final_tick(
            candidate_id, run_id, "parse_error", ohlc_result, None,
            ohlc_report, real_tick_report, None, None,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
        return True

    no_tick_history = None
    if report_has_empty_tester_context(real_tick_result):
        no_tick_history = tester_log_no_history_metadata(
            real_tick_report,
            real_tick_variant,
            symbol_map,
            getattr(args, "symbol_suffix", ""),
        )
    if no_tick_history:
        if reconcile:
            return False
        similarity = {
            "accepted": False,
            "reasons": ["real_tick_no_history"],
            "history_quality": real_tick_result.history_quality,
            "min_history_quality": float(args.final_tick_min_history_quality),
            "technical_failure": True,
            "checks": {},
            "history": no_tick_history,
        }
        print(
            f"AVISO: descarga/sincronizacion Real Tick interrumpida para "
            f"{real_tick_variant.target_symbol}, candidate #{candidate_id}; "
            "se reintentara como historico pendiente."
        )
        memory.record_candidate_final_tick(
            candidate_id, run_id, "pending_history_quality", ohlc_result, None,
            ohlc_report, real_tick_report, json.dumps(similarity, sort_keys=True),
            real_tick_result.history_quality,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        status_counts["pending_history_quality"] = status_counts.get("pending_history_quality", 0) + 1
        return True

    real_matches, real_mismatch = report_matches_variant(
        real_tick_variant,
        real_tick_result,
        symbol_map,
        getattr(args, "symbol_suffix", ""),
        getattr(args, "broker", DEFAULT_BROKER),
    )
    if not real_matches:
        # MT5 can emit an empty Real Tick result (symbol="", timeframe="M0")
        # while still copying a seemingly valid History Quality value into the
        # report.  That percentage does not make the tester context usable: it
        # is a transient history/tick-data failure, not a genuine symbol/TF
        # mismatch.  Keep it pending so the dedicated history retry can recover
        # it.  A zero-trade report with a valid symbol/TF still proceeds to the
        # normal similarity checks and is rejected as expected.
        if report_has_empty_tester_context(real_tick_result):
            if reconcile:
                return False
            pending_similarity = {
                "accepted": False,
                "reasons": ["empty_tester_context"],
                "history_quality": real_tick_result.history_quality,
                "min_history_quality": float(args.final_tick_min_history_quality),
                "checks": {},
            }
            print(
                f"AVISO: reporte Real Tick Final Tick sin contexto usable para candidate #{candidate_id}: "
                f"{real_mismatch}; se reintentara como calidad/historico pendiente."
            )
            memory.record_candidate_final_tick(
                candidate_id, run_id, "pending_history_quality", ohlc_result, None,
                ohlc_report, real_tick_report,
                json.dumps(pending_similarity, ensure_ascii=True, sort_keys=True),
                real_tick_result.history_quality,
                args.final_tick_min_history_quality, args.from_date, args.to_date,
                args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
                args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
            )
            status_counts["pending_history_quality"] = status_counts.get("pending_history_quality", 0) + 1
            return True
        if reconcile:
            return False
        print(f"AVISO: reporte Real Tick Final Tick no coincide para candidate #{candidate_id}: {real_mismatch}")
        memory.record_candidate_final_tick(
            candidate_id, run_id, "report_mismatch", ohlc_result, real_tick_result,
            ohlc_report, real_tick_report, None, real_tick_result.history_quality,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
        return True

    is_six_month = memory.active_final_tick_stage == "six_month"
    similarity = final_tick_similarity(
        ohlc_result,
        real_tick_result,
        min_history_quality=args.final_tick_min_history_quality,
        max_net_delta_pct=args.final_tick_max_net_delta_pct,
        max_pf_delta_pct=min(float(args.final_tick_max_pf_delta_pct), 30.0) if is_six_month else args.final_tick_max_pf_delta_pct,
        max_dd_delta_pct=args.final_tick_max_dd_delta_pct,
        max_trades_delta_pct=args.final_tick_max_trades_delta_pct,
        min_model_profit_factor=float(score_config.min_profit_factor) if is_six_month else None,
        lossless_control_gate=lossless_control_gate_from_args(args) if is_six_month else None,
    )
    status = final_tick_status_from_similarity(similarity) or "rejected"
    memory.record_candidate_final_tick(
        candidate_id, run_id, status, ohlc_result, real_tick_result,
        ohlc_report, real_tick_report,
        json.dumps(similarity, ensure_ascii=True, sort_keys=True),
        real_tick_result.history_quality,
        args.final_tick_min_history_quality, args.from_date, args.to_date,
        args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
        args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
    )
    status_counts[status] = status_counts.get(status, 0) + 1
    return True
