from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ubs.account import account_memory_path, normalize_account_type, normalize_broker
from ubs.db import configure_sqlite_connection


BASE_DIR = Path(__file__).resolve().parent.parent


def load_run_snapshot(base_dir: Path, broker: object, account_type: object, run_id: int) -> dict[str, Any]:
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    memory_path = account_memory_path(base_dir, account, broker_key)
    return load_run_snapshot_from_path(memory_path, broker_key, account, run_id)


def load_run_snapshot_from_path(
    memory_path: str | Path,
    broker: object,
    account_type: object,
    run_id: int,
) -> dict[str, Any]:
    path = Path(memory_path)
    if not path.exists():
        raise FileNotFoundError(f"UBS memory not found: {path}")
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    conn = _connect_readonly(path)
    try:
        run = conn.execute("select * from runs where id=?", (int(run_id),)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} not found in {path}")
        snapshot = {
            "broker": broker_key,
            "account_type": account,
            "run_id": int(run_id),
            "memory_path": str(path),
            "run": dict(run),
            "counts": {},
            "reasons": {},
            "concentration": {},
            "top_mutated_keys": [],
            "samples": {},
        }
        snapshot["counts"]["base_status"] = _counts(
            conn,
            "select status,count(*) from candidates where run_id=? group by status",
            (run_id,),
        )
        snapshot["counts"]["robustness_status"] = _counts(
            conn,
            """
            select cr.status,count(*)
            from candidate_robustness cr join candidates c on c.id=cr.candidate_id
            where c.run_id=? group by cr.status
            """,
            (run_id,),
        )
        snapshot["counts"]["final_tick_status"] = _counts(
            conn,
            """
            select ft.status,count(*)
            from candidate_final_tick ft join candidates c on c.id=ft.candidate_id
            where c.run_id=? group by ft.status
            """,
            (run_id,),
        )
        snapshot["counts"]["final_tick_6m_status"] = _counts(
            conn,
            """
            select ft6.status,count(*)
            from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id
            where c.run_id=? group by ft6.status
            """,
            (run_id,),
        )
        snapshot["counts"]["missing"] = _missing_counts(conn, int(run_id))
        snapshot["counts"]["stale"] = _stale_counts(conn, int(run_id))
        snapshot["reasons"]["base"] = _metric_reason_counts(
            conn,
            "select metrics_json from candidates where run_id=? and status in ('rejected','no_trades','parse_error')",
            (run_id,),
        )
        snapshot["reasons"]["robustness"] = _metric_reason_counts(
            conn,
            """
            select cr.metrics_json from candidate_robustness cr join candidates c on c.id=cr.candidate_id
            where c.run_id=? and cr.status in ('rejected','no_trades','parse_error')
            """,
            (run_id,),
        )
        snapshot["reasons"]["final_tick"] = _similarity_reason_counts(
            conn,
            """
            select ft.similarity_json from candidate_final_tick ft join candidates c on c.id=ft.candidate_id
            where c.run_id=? and ft.status in ('rejected','pending_history_quality','pending_ohlc_trades')
            """,
            (run_id,),
        )
        snapshot["reasons"]["final_tick_6m"] = _similarity_reason_counts(
            conn,
            """
            select ft6.similarity_json from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id
            where c.run_id=? and ft6.status in ('rejected','pending_history_quality','pending_ohlc_trades')
            """,
            (run_id,),
        )
        snapshot["concentration"]["target_symbol"] = _top_counts(
            conn,
            "select coalesce(nullif(target_symbol,''),symbol) as key,count(*) from candidates where run_id=? group by key order by count(*) desc,key limit 12",
            (run_id,),
        )
        snapshot["concentration"]["period"] = _top_counts(
            conn,
            "select period as key,count(*) from candidates where run_id=? group by period order by count(*) desc,period limit 12",
            (run_id,),
        )
        snapshot["top_mutated_keys"] = _top_mutated_keys(conn, int(run_id))
        snapshot["samples"]["problem_candidates"] = _sample_problem_candidates(conn, int(run_id))
        return snapshot
    finally:
        conn.close()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn, enable_wal=False)
    return conn


def _counts(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> dict[str, int]:
    return {str(row[0] or ""): int(row[1] or 0) for row in conn.execute(sql, params)}


def _top_counts(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> list[dict[str, Any]]:
    return [{"key": str(row["key"] or ""), "count": int(row[1] or 0)} for row in conn.execute(sql, params)]


def _one(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def _missing_counts(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    return {
        "robustness": _one(
            conn,
            """
            select count(*) from candidates c left join candidate_robustness cr on cr.candidate_id=c.id
            where c.run_id=? and c.status='accepted' and cr.candidate_id is null
            """,
            (run_id,),
        ),
        "final_tick": _one(
            conn,
            """
            select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id
            left join candidate_final_tick ft on ft.candidate_id=c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft.candidate_id is null
            """,
            (run_id,),
        ),
        "final_tick_6m": _one(
            conn,
            """
            select count(*) from candidates c
            join candidate_robustness cr on cr.candidate_id=c.id
            join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades')
            left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.candidate_id is null
            """,
            (run_id,),
        ),
    }


def _stale_counts(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    return {
        "robustness": _one(
            conn,
            """
            select count(*) from candidate_robustness cr join candidates c on c.id=cr.candidate_id
            where c.run_id=? and c.status<>'accepted'
            """,
            (run_id,),
        ),
        "final_tick": _one(
            conn,
            """
            select count(*) from candidate_final_tick ft join candidates c on c.id=ft.candidate_id
            left join candidate_robustness cr on cr.candidate_id=c.id
            where c.run_id=? and not (c.status='accepted' and cr.status='accepted')
            """,
            (run_id,),
        ),
        "final_tick_6m": _one(
            conn,
            """
            select count(*) from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id
            left join candidate_robustness cr on cr.candidate_id=c.id
            left join candidate_final_tick ft on ft.candidate_id=c.id
            where c.run_id=? and not (c.status='accepted' and cr.status='accepted' and ft.status in ('accepted','pending_ohlc_trades'))
            """,
            (run_id,),
        ),
    }


def _parse_json(value: object) -> dict[str, Any]:
    try:
        data = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _metric_reason_counts(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in conn.execute(sql, params):
        data = _parse_json(row[0])
        reasons = data.get("reasons") or data.get("reason") or ()
        if isinstance(reasons, str):
            reasons = [reasons]
        for reason in reasons or ("sin_reason",):
            key = str(reason or "sin_reason")
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _similarity_reason_counts(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in conn.execute(sql, params):
        data = _parse_json(row[0])
        reasons = data.get("reasons") or ()
        if isinstance(reasons, str):
            reasons = [reasons]
        for reason in reasons or ("sin_reason",):
            key = str(reason or "sin_reason")
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_mutated_keys(conn: sqlite3.Connection, run_id: int, *, limit: int = 30) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in conn.execute("select mutated_keys from candidates where run_id=?", (run_id,)):
        text = str(row["mutated_keys"] or "")
        for key in [part.strip() for part in text.replace(";", ",").split(",")]:
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _sample_problem_candidates(conn: sqlite3.Connection, run_id: int, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select id,status,target_symbol,period,score,mutated_keys,set_path,report_path
        from candidates
        where run_id=? and status not in ('accepted','generated')
        order by id
        limit ?
        """,
        (run_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]
