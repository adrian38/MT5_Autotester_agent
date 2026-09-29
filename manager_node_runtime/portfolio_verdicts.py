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


def _select_excluded_members(active_memory, multiple, path_key, pool_exclusion, pool_member, portfolio_id, raw_paths, scope, selected, single_path):
    """Resuelve que miembros se excluyen, sean del pool o de un portafolio."""
    is_bundle = False
    member = None
    if pool_exclusion:
        selected.append({
            "set_path": str(pool_member.get("set_path") or single_path or ""),
            "set_id": str(pool_member.get("set_path") or single_path or ""),
            "candidate_id": str(pool_member.get("candidate_id") or ""),
            "symbol": str(pool_member.get("symbol") or ""),
            "timeframe": str(pool_member.get("timeframe") or ""),
        })
    else:
        conn = connect_memory(active_memory, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            portfolio = conn.execute(
                "select portfolio_type,type,metrics_json from portfolios "
                "where id=? and coalesce(nullif(portfolio_scope,''),'full_history')=?",
                (portfolio_id, scope),
            ).fetchone()
            if portfolio is None:
                raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
            try:
                metrics = json.loads(portfolio["metrics_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            portfolio_type = str(portfolio["portfolio_type"] or portfolio["type"] or "").lower()
            is_bundle = portfolio_type == "bundle" or bool(metrics.get("portfolio_bundle"))
            # Multiple exclusion is allowed where the manager offers the checkboxes:
            # A/M/C bundles and any saved month. Ya no hay ninguna asimetria de
            # borrado detras: ningun ambito borra ni modifica el portafolio guardado.
            if multiple and not (is_bundle or scope == "monthly"):
                raise ValueError("La exclusión múltiple solo está disponible para portafolios A/M/C y mensuales")
            rows = [dict(row) for row in conn.execute(
                "select set_path,set_id,candidate_id,symbol,timeframe from portfolio_allocations "
                "where portfolio_id=?",
                (portfolio_id,),
            ).fetchall()]
        finally:
            conn.close()

        members_by_path = {
            path_key(row.get("set_path") or row.get("set_id")): row for row in rows
        }
        seen: set[str] = set()
        for raw_path in (raw_paths if multiple else [single_path]):
            key = path_key(raw_path)
            if key in seen:
                continue
            member = members_by_path.get(key)
            if member is None:
                raise ValueError(
                    "Una estrategia seleccionada ya no pertenece al portafolio"
                    if multiple else "No se encontró la estrategia dentro del portafolio"
                )
            seen.add(key)
            selected.append(member)
    return is_bundle, member


def _group_members_by_memory(broker, active_memory, project, selected):
    """Agrupa los miembros elegidos por la memoria donde viven."""
    grouped: dict[Path, list[tuple[str, int | None, dict[str, Any]]]] = {}
    for member in selected:
        candidate_text = str(member.get("candidate_id") or "")
        account_label, separator, raw_candidate_id = candidate_text.rpartition(":")
        account_type = account_label.rsplit("/", 1)[-1] if separator else ""
        candidate_id = int(raw_candidate_id) if separator and raw_candidate_id.isdigit() else None
        source_memory = account_memory_path(project, account_type, broker) if account_type else active_memory
        if not source_memory.is_file():
            source_memory = active_memory
        grouped.setdefault(source_memory.resolve(), []).append((account_label, candidate_id, member))
    return grouped


def _quarantine_grouped_members(grouped, portfolio_id, quarantine_ids, reason, reason_code):
    """Pone en cuarentena los miembros elegidos en cada memoria de origen."""
    for source_memory, members in grouped.items():
        source_conn = connect_memory(source_memory, timeout=10.0)
        try:
            source_conn.row_factory = sqlite3.Row
            source_conn.execute("begin immediate")
            source_conn.execute(
                """create table if not exists portfolio_quarantine (
                    id integer primary key autoincrement, account_type text not null,
                    candidate_id, set_path text not null unique, symbol text, timeframe text,
                    reason text not null default '', source_portfolio_id integer,
                    quarantined_at text not null
                )"""
            )
            ensure_quarantine_reason_columns(source_conn)
            for account_label, candidate_id, member in members:
                set_path = str(member.get("set_path") or member.get("set_id") or "")
                # El respaldo se lee ANTES del veredicto y viaja con la fila de
                # cuarentena: es lo unico que permite reintegrar despues.
                restore_json = (
                    snapshot_candidate_stages(source_conn, candidate_id)
                    if reason_code != MANUAL_REASON else None
                )
                source_conn.execute(
                    """insert into portfolio_quarantine(
                        account_type,candidate_id,set_path,symbol,timeframe,reason,
                        source_portfolio_id,quarantined_at,reason_code,restore_json
                    ) values(?,?,?,?,?,?,?,?,?,?) on conflict(set_path) do update set
                        account_type=excluded.account_type,candidate_id=excluded.candidate_id,
                        symbol=excluded.symbol,timeframe=excluded.timeframe,
                        reason=excluded.reason,source_portfolio_id=excluded.source_portfolio_id,
                        quarantined_at=excluded.quarantined_at,
                        reason_code=excluded.reason_code,restore_json=excluded.restore_json""",
                    (
                        account_label,
                        candidate_id,
                        set_path,
                        str(member.get("symbol") or ""),
                        str(member.get("timeframe") or ""),
                        reason,
                        portfolio_id or None,
                        datetime.now().isoformat(timespec="seconds"),
                        reason_code,
                        restore_json,
                    ),
                )
                saved = source_conn.execute(
                    "select id from portfolio_quarantine where set_path=?", (set_path,)
                ).fetchone()
                quarantine_ids.append(int(saved[0]))
                apply_candidate_verdict(source_conn, candidate_id, reason_code)
            source_conn.commit()
        except Exception:
            source_conn.rollback()
            raise
        finally:
            source_conn.close()


def _exclusion_response_message(multiple, portfolio_id, quarantine_ids, reason_code, scope, verdict_applied):
    """Mensaje de respuesta de la exclusion segun cuantos miembros toca."""
    if multiple:
        return {
            "quarantine_ids": quarantine_ids,
            "deleted": False,
            "portfolio_id": portfolio_id,
            "scope": scope,
            "reason_code": reason_code,
            "verdict_applied": verdict_applied,
        }
