from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ubs.weights import DEFAULT_ROBUST_NEGATIVE_BONUS, DEFAULT_ROBUST_POSITIVE_BONUS


MANUAL_STATUSES = {"accepted", "rejected"}
FINAL_TICK_TABLES = {
    "probe": "candidate_final_tick",
    "six_month": "candidate_final_tick_6m",
}


def _normalize_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in MANUAL_STATUSES:
        raise ValueError(f"Estado manual no soportado: {status}")
    return normalized


def _accepted_value(status: str) -> int:
    return 1 if status == "accepted" else 0


def _ids(values: Iterable[object]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0:
            result.append(item)
    return sorted(set(result))


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _final_tick_table(stage: str) -> str:
    key = str(stage or "probe").strip().lower().replace("-", "_")
    if key in {"6m", "sixmonth", "six_month"}:
        key = "six_month"
    if key not in FINAL_TICK_TABLES:
        raise ValueError(f"Etapa Final Tick no soportada: {stage}")
    return FINAL_TICK_TABLES[key]


def mark_seed_scores(conn: sqlite3.Connection, seed_paths: Iterable[str], status: str) -> int:
    status = _normalize_status(status)
    paths = sorted({str(path) for path in seed_paths if str(path)})
    if not paths:
        return 0
    cur = conn.execute(
        f"""
        update seed_scores
        set status=?,
            accepted=?,
            active=1,
            evaluated_at=?
        where seed_path in ({_placeholders(len(paths))})
        """,
        (status, _accepted_value(status), datetime.now().isoformat(timespec="seconds"), *paths),
    )
    return int(cur.rowcount or 0)


def mark_candidates(conn: sqlite3.Connection, candidate_ids: Iterable[object], status: str) -> int:
    status = _normalize_status(status)
    ids = _ids(candidate_ids)
    if not ids:
        return 0
    cur = conn.execute(
        f"""
        update candidates
        set status=?,
            accepted=?
        where id in ({_placeholders(len(ids))})
        """,
        (status, _accepted_value(status), *ids),
    )
    if status != "accepted":
        placeholders = _placeholders(len(ids))
        conn.execute(f"delete from candidate_final_tick_6m where candidate_id in ({placeholders})", tuple(ids))
        conn.execute(f"delete from candidate_final_tick where candidate_id in ({placeholders})", tuple(ids))
        conn.execute(f"delete from candidate_robustness where candidate_id in ({placeholders})", tuple(ids))
    return int(cur.rowcount or 0)


def sync_manual_accepted_candidate_copies(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[object],
) -> int:
    ids = _ids(candidate_ids)
    if not ids:
        return 0
    rows = conn.execute(
        f"""
        select
            c.id,
            c.generation,
            c.set_path,
            c.score,
            r.output_dir
        from candidates c
        join runs r on r.id = c.run_id
        where c.id in ({_placeholders(len(ids))})
          and c.status='accepted'
        """,
        tuple(ids),
    ).fetchall()
    copied = 0
    for row in rows:
        source = Path(str(row["set_path"] or ""))
        if not source.exists() or not source.is_file():
            continue
        run_dir_raw = str(row["output_dir"] or "").strip()
        if not run_dir_raw:
            continue
        run_dir = Path(run_dir_raw)
        try:
            generation = int(row["generation"] or 0)
        except (TypeError, ValueError):
            generation = 0
        if generation <= 0:
            continue
        accepted_dir = run_dir / f"accepted_gen_{generation:03d}"
        accepted_dir.mkdir(parents=True, exist_ok=True)
        for previous in accepted_dir.glob(f"*__{source.name}"):
            if previous.is_file():
                previous.unlink()
        try:
            score = float(row["score"])
            prefix = f"score_{score:07.2f}"
        except (TypeError, ValueError):
            prefix = "score_manual"
        shutil.copy2(source, accepted_dir / f"{prefix}__{source.name}")
        copied += 1
    return copied


def mark_candidate_robustness(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[object],
    status: str,
    *,
    from_date: str = "",
    to_date: str = "",
    positive_bonus: float = DEFAULT_ROBUST_POSITIVE_BONUS,
    negative_bonus: float = DEFAULT_ROBUST_NEGATIVE_BONUS,
) -> int:
    status = _normalize_status(status)
    ids = _ids(candidate_ids)
    if not ids:
        return 0
    rows = conn.execute(
        f"""
        select
            c.id as candidate_id,
            c.run_id as run_id,
            cr.report_path as report_path,
            cr.score as score,
            cr.metrics_json as metrics_json,
            cr.from_date as from_date,
            cr.to_date as to_date,
            cr.positive_bonus as positive_bonus,
            cr.negative_bonus as negative_bonus
        from candidates c
        left join candidate_robustness cr on cr.candidate_id = c.id
        where c.id in ({_placeholders(len(ids))})
        """,
        tuple(ids),
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        conn.execute(
            """
            insert into candidate_robustness (
                candidate_id, run_id, status, report_path, score, accepted,
                metrics_json, from_date, to_date, positive_bonus, negative_bonus, evaluated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_id) do update set
                run_id=excluded.run_id,
                status=excluded.status,
                accepted=excluded.accepted,
                report_path=excluded.report_path,
                score=excluded.score,
                metrics_json=excluded.metrics_json,
                from_date=excluded.from_date,
                to_date=excluded.to_date,
                positive_bonus=excluded.positive_bonus,
                negative_bonus=excluded.negative_bonus,
                evaluated_at=excluded.evaluated_at
            """,
            (
                int(row["candidate_id"]),
                int(row["run_id"]),
                status,
                row["report_path"],
                row["score"],
                _accepted_value(status),
                row["metrics_json"],
                str(row["from_date"] or from_date or ""),
                str(row["to_date"] or to_date or ""),
                float(row["positive_bonus"] if row["positive_bonus"] is not None else positive_bonus),
                float(row["negative_bonus"] if row["negative_bonus"] is not None else negative_bonus),
                now,
            ),
        )
    if status != "accepted":
        placeholders = _placeholders(len(ids))
        conn.execute(f"delete from candidate_final_tick_6m where candidate_id in ({placeholders})", tuple(ids))
        conn.execute(f"delete from candidate_final_tick where candidate_id in ({placeholders})", tuple(ids))
    return len(rows)


def mark_candidate_final_tick(
    conn: sqlite3.Connection,
    candidate_ids: Iterable[object],
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
    status = _normalize_status(status)
    ids = _ids(candidate_ids)
    if not ids:
        return 0
    table = _final_tick_table(final_tick_stage)
    rows = conn.execute(
        f"""
        select
            c.id as candidate_id,
            c.run_id as run_id,
            ft.ohlc_report_path as ohlc_report_path,
            ft.real_tick_report_path as real_tick_report_path,
            ft.ohlc_score as ohlc_score,
            ft.real_tick_score as real_tick_score,
            ft.ohlc_metrics_json as ohlc_metrics_json,
            ft.real_tick_metrics_json as real_tick_metrics_json,
            ft.similarity_json as similarity_json,
            ft.history_quality as history_quality,
            ft.min_history_quality as min_history_quality,
            ft.from_date as from_date,
            ft.to_date as to_date,
            ft.max_net_delta_pct as max_net_delta_pct,
            ft.max_pf_delta_pct as max_pf_delta_pct,
            ft.max_dd_delta_pct as max_dd_delta_pct,
            ft.max_trades_delta_pct as max_trades_delta_pct
        from candidates c
        left join {table} ft on ft.candidate_id = c.id
        where c.id in ({_placeholders(len(ids))})
        """,
        tuple(ids),
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        conn.execute(
            f"""
            insert into {table} (
                candidate_id, run_id, status, accepted,
                ohlc_report_path, real_tick_report_path,
                ohlc_score, real_tick_score,
                ohlc_metrics_json, real_tick_metrics_json, similarity_json,
                history_quality, min_history_quality, from_date, to_date,
                max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
                evaluated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_id) do update set
                run_id=excluded.run_id,
                status=excluded.status,
                accepted=excluded.accepted,
                ohlc_report_path=excluded.ohlc_report_path,
                real_tick_report_path=excluded.real_tick_report_path,
                ohlc_score=excluded.ohlc_score,
                real_tick_score=excluded.real_tick_score,
                ohlc_metrics_json=excluded.ohlc_metrics_json,
                real_tick_metrics_json=excluded.real_tick_metrics_json,
                similarity_json=excluded.similarity_json,
                history_quality=excluded.history_quality,
                min_history_quality=excluded.min_history_quality,
                from_date=excluded.from_date,
                to_date=excluded.to_date,
                max_net_delta_pct=excluded.max_net_delta_pct,
                max_pf_delta_pct=excluded.max_pf_delta_pct,
                max_dd_delta_pct=excluded.max_dd_delta_pct,
                max_trades_delta_pct=excluded.max_trades_delta_pct,
                evaluated_at=excluded.evaluated_at
            """,
            (
                int(row["candidate_id"]),
                int(row["run_id"]),
                status,
                _accepted_value(status),
                row["ohlc_report_path"],
                row["real_tick_report_path"],
                row["ohlc_score"],
                row["real_tick_score"],
                row["ohlc_metrics_json"],
                row["real_tick_metrics_json"],
                row["similarity_json"],
                row["history_quality"],
                float(row["min_history_quality"] if row["min_history_quality"] is not None else min_history_quality),
                str(row["from_date"] or from_date or ""),
                str(row["to_date"] or to_date or ""),
                float(row["max_net_delta_pct"] if row["max_net_delta_pct"] is not None else max_net_delta_pct),
                float(row["max_pf_delta_pct"] if row["max_pf_delta_pct"] is not None else max_pf_delta_pct),
                float(row["max_dd_delta_pct"] if row["max_dd_delta_pct"] is not None else max_dd_delta_pct),
                float(row["max_trades_delta_pct"] if row["max_trades_delta_pct"] is not None else max_trades_delta_pct),
                now,
            ),
        )
    if table == "candidate_final_tick" and status not in {"accepted", "pending_ohlc_trades"}:
        conn.execute(
            f"delete from candidate_final_tick_6m where candidate_id in ({_placeholders(len(ids))})",
            tuple(ids),
        )
    return len(rows)
