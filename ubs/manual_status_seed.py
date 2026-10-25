"""Veredicto manual de las etapas tick a tick de una seed.

Vive aparte de ``manual_status`` porque esa unidad ya esta en su techo de
lineas; la regla que aplica es la misma que la de los candidatos.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable


SEED_FINAL_TICK_TABLES = {
    "probe": "seed_final_tick",
    "six_month": "seed_final_tick_6m",
}

SEED_FINAL_TICK_UPSERT_SQL = """
insert into {table} (
    seed_path, symbol, period, status, accepted,
    ohlc_report_path, real_tick_report_path, ohlc_score, real_tick_score,
    ohlc_metrics_json, real_tick_metrics_json, similarity_json,
    history_quality, min_history_quality, from_date, to_date,
    max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
    evaluated_at
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
on conflict(seed_path) do update set
    symbol=excluded.symbol, period=excluded.period,
    status=excluded.status, accepted=excluded.accepted,
    ohlc_report_path=excluded.ohlc_report_path,
    real_tick_report_path=excluded.real_tick_report_path,
    ohlc_score=excluded.ohlc_score, real_tick_score=excluded.real_tick_score,
    ohlc_metrics_json=excluded.ohlc_metrics_json,
    real_tick_metrics_json=excluded.real_tick_metrics_json,
    similarity_json=excluded.similarity_json,
    history_quality=excluded.history_quality,
    min_history_quality=excluded.min_history_quality,
    from_date=excluded.from_date, to_date=excluded.to_date,
    max_net_delta_pct=excluded.max_net_delta_pct,
    max_pf_delta_pct=excluded.max_pf_delta_pct,
    max_dd_delta_pct=excluded.max_dd_delta_pct,
    max_trades_delta_pct=excluded.max_trades_delta_pct,
    evaluated_at=excluded.evaluated_at
"""

SEED_FINAL_TICK_SELECT_SQL = """
select
    s.seed_path as seed_path,
    s.symbol as seed_symbol,
    s.period as seed_period,
    ft.symbol as symbol,
    ft.period as period,
    ft.ohlc_report_path, ft.real_tick_report_path,
    ft.ohlc_score, ft.real_tick_score,
    ft.ohlc_metrics_json, ft.real_tick_metrics_json, ft.similarity_json,
    ft.history_quality, ft.min_history_quality,
    ft.from_date, ft.to_date,
    ft.max_net_delta_pct, ft.max_pf_delta_pct,
    ft.max_dd_delta_pct, ft.max_trades_delta_pct
from seed_scores s
left join {table} ft on ft.seed_path = s.seed_path
where s.seed_path in ({placeholders})
"""


def seed_final_tick_table(final_tick_stage: str) -> str:
    key = str(final_tick_stage or "probe").strip().lower().replace("-", "_")
    if key in {"6m", "sixmonth", "six_month"}:
        key = "six_month"
    if key not in SEED_FINAL_TICK_TABLES:
        raise ValueError(f"Etapa Final Tick de seeds desconocida: {final_tick_stage}")
    return SEED_FINAL_TICK_TABLES[key]


def _seed_final_tick_values(row: sqlite3.Row, status: str, accepted: object, thresholds: dict, now: str) -> tuple:
    """Fila a escribir: conserva informes y metricas, cambia el veredicto."""

    def existing_or(key: str, default: object) -> object:
        return row[key] if row[key] is not None else default

    return (
        str(row["seed_path"]),
        str(existing_or("symbol", row["seed_symbol"]) or "").strip().upper(),
        str(existing_or("period", row["seed_period"]) or "").strip().upper(),
        status,
        accepted,
        row["ohlc_report_path"], row["real_tick_report_path"],
        row["ohlc_score"], row["real_tick_score"],
        row["ohlc_metrics_json"], row["real_tick_metrics_json"], row["similarity_json"],
        row["history_quality"],
        float(existing_or("min_history_quality", thresholds["min_history_quality"])),
        str(row["from_date"] or thresholds["from_date"] or ""),
        str(row["to_date"] or thresholds["to_date"] or ""),
        float(existing_or("max_net_delta_pct", thresholds["max_net_delta_pct"])),
        float(existing_or("max_pf_delta_pct", thresholds["max_pf_delta_pct"])),
        float(existing_or("max_dd_delta_pct", thresholds["max_dd_delta_pct"])),
        float(existing_or("max_trades_delta_pct", thresholds["max_trades_delta_pct"])),
        now,
    )


def mark_seed_final_tick(
    conn: sqlite3.Connection,
    seed_paths: Iterable[object],
    status: str,
    *,
    final_tick_stage: str = "probe",
    min_history_quality: float = 80.0,
    from_date: str = "",
    to_date: str = "",
    max_net_delta_pct: float = 35.0,
    max_pf_delta_pct: float = 35.0,
    max_dd_delta_pct: float = 35.0,
    max_trades_delta_pct: float = 35.0,
) -> int:
    """Fija a mano el veredicto tick a tick de unas seeds.

    Un probe que deja de ser elegible borra su 6M, igual que en los candidatos:
    si no, la seed seguiria cobrando el bonus de 6M con el corto ya rechazado.
    """
    from ubs.manual_status import _accepted_value, _normalize_status, _placeholders

    status = _normalize_status(status)
    paths = [str(path) for path in seed_paths if str(path or "").strip()]
    if not paths:
        return 0
    table = seed_final_tick_table(final_tick_stage)
    placeholders = _placeholders(len(paths))
    thresholds = {
        "min_history_quality": min_history_quality,
        "from_date": from_date,
        "to_date": to_date,
        "max_net_delta_pct": max_net_delta_pct,
        "max_pf_delta_pct": max_pf_delta_pct,
        "max_dd_delta_pct": max_dd_delta_pct,
        "max_trades_delta_pct": max_trades_delta_pct,
    }
    now = datetime.now().isoformat(timespec="seconds")
    rows = conn.execute(
        SEED_FINAL_TICK_SELECT_SQL.format(table=table, placeholders=placeholders),
        tuple(paths),
    ).fetchall()
    for row in rows:
        conn.execute(
            SEED_FINAL_TICK_UPSERT_SQL.format(table=table),
            _seed_final_tick_values(row, status, _accepted_value(status), thresholds, now),
        )
    if table == "seed_final_tick" and status not in {"accepted", "pending_ohlc_trades"}:
        conn.execute(
            f"delete from seed_final_tick_6m where seed_path in ({placeholders})",
            tuple(paths),
        )
    return len(rows)
