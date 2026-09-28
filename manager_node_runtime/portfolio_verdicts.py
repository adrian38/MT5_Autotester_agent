"""Motivos de exclusion, veredictos de etapa y recualificacion de miembros."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ubs.account import account_memory_path
from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_final_tick, mark_candidate_robustness


# REGLA DUPLICADA: el criterio vive tambien en el manager
# (`mt5_manager/candidate_verdict.py`). El manager exige `verdict_applied` en la
# respuesta: un nodo sin portar devolvia 200 sin escribir nada y el usuario daba
# por hechos unos cambios que no existian.
MANUAL_REASON = "manual"
DEGRADATION_REASON = "degradation"
OHLC_MISMATCH_REASON = "ohlc_mismatch"
REASON_CODES = (MANUAL_REASON, DEGRADATION_REASON, OHLC_MISMATCH_REASON)

REASON_TEXTS = {
    MANUAL_REASON: "Excluida manualmente desde el manager",
    DEGRADATION_REASON: "Excluida por degradación: rechazada en el test de robustez",
    OHLC_MISMATCH_REASON: "Excluida porque el OHLC no se parece al every tick: rechazada en Final Tick 6M",
}

STAGE_TABLES = (
    "candidate_robustness",
    "candidate_final_tick",
    "candidate_final_tick_6m",
    "candidate_regression",
)


def normalize_reason_code(value: object) -> str:
    """Lo desconocido es `manual`: un motivo inventado nunca borra etapas."""
    code = str(value or "").strip().lower().replace("-", "_")
    return code if code in REASON_CODES else MANUAL_REASON


def reason_with_verdict(text: str, reason_code: str) -> str:
    code = normalize_reason_code(reason_code)
    if code == MANUAL_REASON:
        return text
    return f"{text} — {REASON_TEXTS[code]}"


def _quarantine_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def ensure_quarantine_reason_columns(conn: sqlite3.Connection) -> None:
    """Migracion idempotente: las memorias en produccion no tienen estas columnas."""
    columns = {str(row[1]) for row in conn.execute("pragma table_info(portfolio_quarantine)")}
    if "reason_code" not in columns:
        conn.execute(
            f"alter table portfolio_quarantine add column reason_code text not null default '{MANUAL_REASON}'"
        )
    if "restore_json" not in columns:
        conn.execute("alter table portfolio_quarantine add column restore_json text")


def snapshot_candidate_stages(conn: sqlite3.Connection, candidate_id: object) -> str | None:
    """Copia literal de las etapas antes del veredicto, para poder reintegrar.

    El rechazo por degradacion BORRA Final Tick, Final Tick 6M y regresion, igual
    que el del agente. Sin este respaldo, reintegrar la estrategia la dejaria
    fuera del pool para siempre: el manager exige las cuatro etapas aceptadas.
    """
    try:
        identifier = int(candidate_id)
    except (TypeError, ValueError):
        return None
    if identifier < 1:
        return None
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for table in STAGE_TABLES:
        if not _quarantine_table_exists(conn, table):
            continue
        cursor = conn.execute(f"select * from {table} where candidate_id=?", (identifier,))
        names = [str(column[0]) for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        if rows:
            snapshot[table] = rows
    if not snapshot:
        return None
    return json.dumps(snapshot, ensure_ascii=True, sort_keys=True, default=str)


def apply_candidate_verdict(conn: sqlite3.Connection, candidate_id: object, reason_code: str) -> bool:
    """Marca la etapa que corresponde al motivo, con la primitiva del agente."""
    code = normalize_reason_code(reason_code)
    if code == MANUAL_REASON:
        return False
    try:
        identifier = int(candidate_id)
    except (TypeError, ValueError):
        return False
    if identifier < 1:
        return False
    if code == DEGRADATION_REASON:
        return bool(mark_candidate_robustness(conn, [identifier], "rejected"))
    return bool(
        mark_candidate_final_tick(conn, [identifier], "rejected", final_tick_stage="six_month")
    )


def origin_of_reason(reason: object) -> str:
    """Devuelve el origen de la exclusion, sin el veredicto que se le anadio.

    Reclasificar cambia el veredicto pero no de donde salio la exclusion. Sin
    quitar el sufijo anterior, mover una fila entre tablas iria acumulando
    veredictos en el mismo texto. Copia de `candidate_verdict.origin_text`.
    """
    text = str(reason or "").strip()
    for verdict in REASON_TEXTS.values():
        suffix = f" — {verdict}"
        if text.endswith(suffix):
            return text[: -len(suffix)].strip()
        if text == verdict:
            return ""
    return text


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}


def restore_candidate_stages(conn: sqlite3.Connection, snapshot: object) -> int:
    """Devuelve las filas de etapa tal y como estaban antes del veredicto.

    Copia de `mt5_manager/candidate_verdict.py::restore_candidate_stages`. Se
    restaura por nombre de columna, nunca por posicion: dos memorias pueden tener
    columnas distintas y restaurar por posicion escribiria el valor equivocado
    sin fallar.
    """
    data = snapshot
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return 0
    if not isinstance(data, dict) or not data:
        return 0
    restored = 0
    for table in STAGE_TABLES:
        rows = data.get(table)
        if not isinstance(rows, list) or not rows:
            continue
        if not _quarantine_table_exists(conn, table):
            continue
        available = _table_columns(conn, table)
        for row in rows:
            if not isinstance(row, dict):
                continue
            columns = [name for name in row if name in available]
            if not columns:
                continue
            identifier = row.get("candidate_id")
            if identifier is not None:
                conn.execute(f"delete from {table} where candidate_id=?", (identifier,))
            placeholders = ",".join("?" for _ in columns)
            conn.execute(
                f"insert into {table} ({','.join(columns)}) values ({placeholders})",
                tuple(row[name] for name in columns),
            )
            restored += 1
    return restored


def _requalify_memory(
    project: Path, broker: str, active_memory: Path, account_label: object
) -> Path:
    """Memoria del broker que corresponde a una etiqueta `BROKER/CUENTA`."""
    account_type = str(account_label or "").rsplit("/", 1)[-1].strip()
    if not account_type:
        return active_memory
    candidate = account_memory_path(project, account_type, broker)
    return candidate if candidate.is_file() else active_memory


def _read_quarantine_row(quarantine_memory: Path, quarantine_id: int):
    """Motivo vigente, cuenta, candidato, respaldo y origen de la fila excluida."""
    conn = connect_memory(quarantine_memory, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        if not _quarantine_table_exists(conn, "portfolio_quarantine"):
            raise ValueError("No existe la cuarentena")
        ensure_quarantine_reason_columns(conn)
        row = conn.execute(
            "select account_type,candidate_id,reason,reason_code,restore_json "
            "from portfolio_quarantine where id=?",
            (quarantine_id,),
        ).fetchone()
        if row is None:
            raise ValueError("La estrategia excluida ya no existe")
        result = (
            normalize_reason_code(row["reason_code"]),
            row["account_type"],
            row["candidate_id"],
            row["restore_json"],
            origin_of_reason(row["reason"]),
        )
        conn.commit()
    finally:
        conn.close()
    return result


def _reapply_candidate_verdict(
    candidate_memory: Path, candidate_id: object, previous_restore: object, target: str,
) -> str | None:
    """Deshace el veredicto vigente, fotografia el estado y aplica el nuevo."""
    restore_json: str | None = None
    candidate_conn = connect_memory(candidate_memory, timeout=10.0)
    try:
        candidate_conn.row_factory = sqlite3.Row
        candidate_conn.execute("begin immediate")
        restore_candidate_stages(candidate_conn, previous_restore)
        snapshot = snapshot_candidate_stages(candidate_conn, candidate_id)
        if target not in (MANUAL_REASON, "pool"):
            if not snapshot:
                raise ValueError(
                    "El candidato ya no tiene etapas en la memoria del agente: "
                    "no se puede aplicar el veredicto"
                )
            restore_json = snapshot
            apply_candidate_verdict(candidate_conn, candidate_id, target)
        candidate_conn.commit()
    except Exception:
        candidate_conn.rollback()
        raise
    finally:
        candidate_conn.close()
    return restore_json


def _write_quarantine_row(
    quarantine_memory: Path, quarantine_id: int, target: str, origin: str, restore_json: str | None,
) -> None:
    conn = connect_memory(quarantine_memory, timeout=10.0)
    try:
        conn.execute("begin immediate")
        if target == "pool":
            conn.execute("delete from portfolio_quarantine where id=?", (quarantine_id,))
        else:
            conn.execute(
                "update portfolio_quarantine set reason_code=?,reason=?,restore_json=?,"
                "quarantined_at=? where id=?",
                (
                    target,
                    reason_with_verdict(origin, target) if origin else REASON_TEXTS[target],
                    restore_json,
                    datetime.now().isoformat(timespec="seconds"),
                    quarantine_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _requalify_request(payload: dict[str, Any]) -> tuple[str, str]:
    raw_key = str(payload.get("quarantine_id") or "").strip()
    if not raw_key:
        raise ValueError("Falta la estrategia excluida que se quiere reclasificar")
    requested = str(payload.get("reason_code") or "pool").strip().lower()
    target = "pool" if requested == "pool" else normalize_reason_code(requested)
    return raw_key, target


def _requalify_quarantine_location(
    project: Path, broker: str, active_memory: Path, raw_key: str,
) -> tuple[Path, int]:
    """La clave de cuarentena lleva la etiqueta de la memoria que guarda la fila."""
    if "|" in raw_key:
        account_label, _separator, raw_id = raw_key.rpartition("|")
        quarantine_memory = _requalify_memory(project, broker, active_memory, account_label)
    else:
        raw_id = raw_key
        quarantine_memory = active_memory
    quarantine_id = int(raw_id) if raw_id.strip().isdigit() else 0
    if quarantine_id < 1:
        raise ValueError("Identificador de cuarentena inválido")
    return quarantine_memory, quarantine_id


def requalify_portfolio_member_payload(
    project_dir: str | Path,
    broker: str,
    memory_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Mueve una estrategia excluida entre los tres motivos y el pool.

    ESTO CORRE EN EL NODO A PROPOSITO. El manager solo lee esta memoria por una
    copia de lectura: sobre CIFS o sobre un bind mount de Docker, abrirla para
    escribir falla con "disk I/O error" porque el modo WAL necesita un `-shm` que
    esos sistemas de ficheros no respaldan. Aqui la base es local.

    REGLA DUPLICADA: el mismo orden vive en el manager
    (`mt5_manager/portfolio_service.py::PortfolioSource.requalify_strategy`) y no
    puede divergir. Reclasificar es **deshacer el veredicto vigente y aplicar el
    nuevo**, nunca aplicar uno encima de otro:

    1. deshacer el veredicto vigente restaurando `restore_json`;
    2. fotografiar el estado ya restaurado, que es el respaldo de la proxima vez;
    3. aplicar el veredicto nuevo, o borrar la fila si el destino es el pool.

    Sin el paso 1, pasar de degradacion a OHLC guardaria como «estado anterior»
    una memoria a la que ya le faltan Final Tick y 6M, y la estrategia no volveria
    nunca al pool.
    """
    project = Path(project_dir).expanduser().resolve()
    active_memory = Path(memory_path).expanduser().resolve()
    raw_key, target = _requalify_request(payload)
    quarantine_memory, quarantine_id = _requalify_quarantine_location(
        project, broker, active_memory, raw_key,
    )

    current, candidate_account, candidate_id, previous_restore, origin = _read_quarantine_row(
        quarantine_memory, quarantine_id,
    )

    if target == current:
        return {
            "requalified": True,
            "quarantine_id": raw_key,
            "reason_code": current,
            "previous_reason_code": current,
        }

    candidate_memory = _requalify_memory(project, broker, quarantine_memory, candidate_account)
    restore_json = _reapply_candidate_verdict(
        candidate_memory, candidate_id, previous_restore, target,
    )
    _write_quarantine_row(quarantine_memory, quarantine_id, target, origin, restore_json)

    return {
        "requalified": True,
        "quarantine_id": raw_key,
        "reason_code": target,
        "previous_reason_code": current,
    }
