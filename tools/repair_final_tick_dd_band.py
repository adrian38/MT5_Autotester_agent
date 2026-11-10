"""Re-aplica el veredicto Final Tick tras anadir la banda absoluta de DD.

La comparacion OHLC vs Real Tick no cambia de datos: las dos patas ya estan
medidas y guardadas en ``ohlc_metrics_json`` / ``real_tick_metrics_json``. Lo
unico que cambio es la regla del drawdown, asi que basta con volver a correr
``final_tick_similarity`` sobre lo guardado, con los umbrales que lleva cada
fila, y escribir el veredicto que sale. No abre MT5 ni reparsea reportes.

A diferencia del reparador de la pestana Universo, cubre tambien las dos tablas
de seeds, que son las que miran los paneles "Seeds Final Tick".

La banda solo puede ampliar la aceptacion, de modo que una fila ``accepted``
no puede volverse ``rejected`` por este cambio; aun asi se recalculan todas y
se avisa si alguna cambia en la otra direccion, que seria senal de que la
memoria trae otra divergencia encima.

    py tools/repair_final_tick_dd_band.py --broker ICTRADING --account-type STANDARD
    py tools/repair_final_tick_dd_band.py --broker ICTRADING --account-type STANDARD --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import sqlite3

from ubs.account import account_memory_path, normalize_account_type, normalize_broker
from ubs.db import connect_memory
from ubs.score import ScoreResult
from ubs_agent_config import LosslessControlGate
from ubs_agent_final_tick import final_tick_status_from_similarity
from ubs_agent_final_tick_similarity import DEFAULT_MAX_DD_GAP_PP, final_tick_similarity


STAGE_TABLES = (
    ("candidate_final_tick", "candidate_id", False),
    ("candidate_final_tick_6m", "candidate_id", True),
    ("seed_final_tick", "seed_id", False),
    ("seed_final_tick_6m", "seed_id", True),
)


def _ensure_gap_column(conn, table: str) -> bool:
    """True si la tabla existe; le anade la columna de la banda si le falta."""
    columns = {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}
    if not columns:
        return False
    if "max_dd_gap_pp" not in columns:
        conn.execute(f"alter table {table} add column max_dd_gap_pp real not null default 3.0")
    return True


def _stored_min_model_pf(raw_tick_metrics: str, six_month: bool) -> float | None:
    """Piso de PF que llevaba el 6M; fuera del 6M no hay piso.

    Mismo criterio y mismo respaldo que el reparador de la pestana Universo.
    """
    if not six_month:
        return None
    try:
        config = json.loads(str(raw_tick_metrics or "{}")).get("score_config") or {}
        return float(config.get("min_profit_factor", 1.20))
    except (AttributeError, TypeError, ValueError):
        return 1.20


def _recomputed_verdict(row, six_month: bool, max_dd_gap_pp: float):
    """Veredicto que da el codigo de hoy sobre las metricas ya guardadas."""
    try:
        ohlc = ScoreResult.from_json(row["ohlc_metrics_json"])
        tick = ScoreResult.from_json(row["real_tick_metrics_json"])
    except (TypeError, ValueError):
        return None
    stored_max_pf = float(row["max_pf_delta_pct"] or 35.0)
    similarity = final_tick_similarity(
        ohlc,
        tick,
        min_history_quality=float(row["min_history_quality"] or 80.0),
        max_net_delta_pct=float(row["max_net_delta_pct"] or 35.0),
        max_pf_delta_pct=min(stored_max_pf, 30.0) if six_month else stored_max_pf,
        max_dd_delta_pct=float(row["max_dd_delta_pct"] or 35.0),
        max_trades_delta_pct=float(row["max_trades_delta_pct"] or 35.0),
        max_dd_gap_pp=float(max_dd_gap_pp),
        min_model_profit_factor=_stored_min_model_pf(row["real_tick_metrics_json"], six_month),
        lossless_control_gate=LosslessControlGate() if six_month else None,
    )
    status = final_tick_status_from_similarity(similarity)
    if status is None:
        return None
    return status, similarity


def _table_changes(conn, table: str, key: str, six_month: bool, max_dd_gap_pp: float) -> list[dict]:
    """Filas de una tabla cuyo veredicto guardado ya no es el que sale hoy."""
    rows = conn.execute(
        f"""select {key} as row_id, status, similarity_json,
                   ohlc_metrics_json, real_tick_metrics_json, min_history_quality,
                   max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                   max_trades_delta_pct
            from {table}
            where coalesce(ohlc_metrics_json, '') != ''
              and coalesce(real_tick_metrics_json, '') != ''"""
    ).fetchall()
    changes: list[dict] = []
    for row in rows:
        verdict = _recomputed_verdict(row, six_month, max_dd_gap_pp)
        if verdict is None:
            continue
        status, similarity = verdict
        if status == str(row["status"] or ""):
            continue
        check = similarity["checks"].get("drawdown_pct", {})
        changes.append(
            {
                "table": table,
                "key": key,
                "row_id": row["row_id"],
                "stored_status": str(row["status"] or ""),
                "status": status,
                "similarity_json": json.dumps(similarity, ensure_ascii=True, sort_keys=True),
                "gap_pp": check.get("gap_pp"),
                "delta_pct": check.get("delta_pct"),
                "ohlc_dd": check.get("ohlc"),
                "tick_dd": check.get("real_tick"),
            }
        )
    return changes


def _seed_six_month_rows_to_queue(conn) -> list[dict]:
    """Seeds ya elegibles para el 6M que no tienen fila que lo cuente.

    Rechazar el tramo corto borra la fila 6M de la seed, asi que al
    desbloquearlo la etapa vuelve a recogerla (su filtro es
    ``probe.status in ('accepted','pending_ohlc_trades')`` y un 6M vacio cuenta
    como reintentable), pero el panel la pinta igual que a una seed que nunca
    fue elegible. Se le escribe una fila ``pending`` —estado retryable, o sea
    que la etapa la reprocesa igual— para que se lea como trabajo encolado.

    Del lado de los candidatos esto ya lo hace el boton de reparacion de la
    pestana Universo; las seeds no tenian equivalente.
    """
    try:
        rows = conn.execute(
            """
            select ss.id as seed_id, ss.seed_mtime, ss.seed_size
            from seed_scores ss
            join seed_final_tick ft on ft.seed_id = ss.id and ft.status='accepted'
             and ft.seed_mtime = ss.seed_mtime and ft.seed_size = ss.seed_size
            left join seed_final_tick_6m ft6 on ft6.seed_id = ss.id
             and ft6.seed_mtime = ss.seed_mtime and ft6.seed_size = ss.seed_size
            where ss.active=1 and ss.status='accepted' and ft6.seed_id is null
            order by ss.id
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "seed_id": int(row["seed_id"]),
            "seed_mtime": float(row["seed_mtime"]),
            "seed_size": int(row["seed_size"]),
        }
        for row in rows
    ]


def _queue_seed_six_month(conn, queued: list[dict]) -> None:
    evaluated_at = datetime.now().isoformat(timespec="seconds")
    for item in queued:
        conn.execute(
            """insert into seed_final_tick_6m
                   (seed_id, status, accepted, seed_mtime, seed_size, evaluated_at)
               values (?, 'pending', 0, ?, ?, ?)
               on conflict(seed_id) do nothing""",
            (item["seed_id"], item["seed_mtime"], item["seed_size"], evaluated_at),
        )
    conn.commit()


def _describe(change: dict) -> str:
    return (
        f"  {change['table']} #{change['row_id']}: "
        f"{change['stored_status']} -> {change['status']} "
        f"(DD {change['ohlc_dd']} vs {change['tick_dd']}; "
        f"{change['gap_pp']}pp / {change['delta_pct']}%)"
    )


def _apply(conn, changes: list[dict], max_dd_gap_pp: float) -> None:
    for change in changes:
        conn.execute(
            f"update {change['table']} set status=?, accepted=?, similarity_json=?, "
            f"max_dd_gap_pp=? where {change['key']}=?",
            (
                change["status"],
                int(change["status"] == "accepted"),
                change["similarity_json"],
                float(max_dd_gap_pp),
                change["row_id"],
            ),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", default="ICTRADING")
    parser.add_argument("--account-type", default="STANDARD")
    parser.add_argument("--memory", default="", help="Ruta explicita a la memoria SQLite.")
    parser.add_argument("--max-dd-gap-pp", type=float, default=DEFAULT_MAX_DD_GAP_PP)
    parser.add_argument("--apply", action="store_true", help="Escribe; sin esto solo informa.")
    args = parser.parse_args()

    broker = normalize_broker(args.broker)
    account = normalize_account_type(args.account_type, broker)
    db_path = Path(args.memory) if args.memory else account_memory_path(BASE_DIR, account, broker)
    if not db_path.is_file():
        print(f"ERROR: no existe la memoria {db_path}")
        return 1
    print(f"Memoria: {db_path}")
    print(f"Banda absoluta de DD: {float(args.max_dd_gap_pp):.2f}pp")

    conn = connect_memory(db_path)
    try:
        changes: list[dict] = []
        for table, key, six_month in STAGE_TABLES:
            if not _ensure_gap_column(conn, table):
                continue
            changes.extend(_table_changes(conn, table, key, six_month, args.max_dd_gap_pp))
        for change in changes:
            print(_describe(change))
        rescued = [item for item in changes if item["status"] == "accepted"]
        tightened = [item for item in changes if item["stored_status"] == "accepted"]
        print(
            f"Total {len(changes)} fila(s): {len(rescued)} pasan a accepted; "
            f"{len(tightened)} dejan de estarlo."
            if changes
            else "Ningun veredicto cambia con la banda."
        )
        if args.apply and changes:
            _apply(conn, changes, args.max_dd_gap_pp)
            print(f"Escritas {len(changes)} fila(s).")
        # El encolado se mide despues de escribir: un candidato recien
        # desbloqueado solo es elegible para 6M con su tramo corto ya accepted.
        queued = _seed_six_month_rows_to_queue(conn)
        print(f"Seeds elegibles para 6M sin fila que lo cuente: {len(queued)}")
        if not args.apply:
            print("Simulacion: nada escrito. Repite con --apply.")
            return 0
        if queued:
            _queue_seed_six_month(conn, queued)
            print(f"Encoladas {len(queued)} fila(s) de seeds 6M en estado pending.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
