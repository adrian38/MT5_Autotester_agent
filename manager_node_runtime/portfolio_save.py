from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from portfolio_manager.ubs_portfolio import (
    BootstrapDrawdownAnalysis,
    OptimizationDecision,
    PortfolioResult,
    StrategyAllocation,
    UnusedSetInfo,
)
from ubs.db import connect_memory
from ubs.account import account_memory_path
from ubs.manual_status import mark_candidate_final_tick, mark_candidate_robustness
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin


class _PortfolioPersistence(UBSPortfolioLogicMixin):
    """Reuse the desktop portfolio persistence without constructing the UI."""


# --- Motivo de exclusion (copia bifurcada de mt5_manager/candidate_verdict.py) ---
#
# El manager envia `reason_code` con la exclusion. Cuando no es `manual`, la
# estrategia no se retira del portafolio: se declara que FALLO, y este proceso
# escribe el veredicto que habria escrito el pipeline. Es lo mismo que hace el
# FAIL manual de la aplicacion, asi que se llama a `ubs.manual_status`, que es la
# autoridad de este lado; los pesos no se guardan en ninguna tabla y salen de
# estos estados (`ubs/weights.py::feedback_weight`).
#
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
    raw_key = str(payload.get("quarantine_id") or "").strip()
    if not raw_key:
        raise ValueError("Falta la estrategia excluida que se quiere reclasificar")
    requested = str(payload.get("reason_code") or "pool").strip().lower()
    target = "pool" if requested == "pool" else normalize_reason_code(requested)

    # La clave de cuarentena lleva la etiqueta de la memoria que guarda la fila.
    if "|" in raw_key:
        account_label, _separator, raw_id = raw_key.rpartition("|")
        quarantine_memory = _requalify_memory(project, broker, active_memory, account_label)
    else:
        raw_id = raw_key
        quarantine_memory = active_memory
    quarantine_id = int(raw_id) if raw_id.strip().isdigit() else 0
    if quarantine_id < 1:
        raise ValueError("Identificador de cuarentena inválido")

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
        current = normalize_reason_code(row["reason_code"])
        candidate_account = row["account_type"]
        candidate_id = row["candidate_id"]
        previous_restore = row["restore_json"]
        origin = origin_of_reason(row["reason"])
        conn.commit()
    finally:
        conn.close()

    if target == current:
        return {
            "requalified": True,
            "quarantine_id": raw_key,
            "reason_code": current,
            "previous_reason_code": current,
        }

    candidate_memory = _requalify_memory(project, broker, quarantine_memory, candidate_account)
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

    return {
        "requalified": True,
        "quarantine_id": raw_key,
        "reason_code": target,
        "previous_reason_code": current,
    }


def _supported_dataclass_values(dataclass_type: type, raw: dict[str, Any]) -> dict[str, Any]:
    """Ignorar los campos que el manager ya envia y esta copia todavia no tiene.

    REGLA DUPLICADA: es el mismo filtro que
    `mt5_manager/portfolio_service.py::_supported_dataclass_values`. El manager
    manda la tanda de riesgo por equity (`max_balance_dd_001`, `max_equity_dd_001`,
    el DD flotante, el rendimiento reciente y las rutas de informe) y los campos
    de auditoria del resultado; el `portfolio_manager/ubs_portfolio.py` de este
    agente es de antes y no los declara. Sin este filtro, construir la dataclass
    con el diccionario crudo moria con `unexpected keyword argument` y el nodo
    devolvia un 500 con su traza en la consola en **cada** guardado: el manager
    reintentaba con
    `legacy_compatible_portfolio_save_payload` y el 201 llegaba en el segundo
    POST. Descartarlos aqui deja exactamente los mismos datos guardados, en un
    solo POST y sin traza.
    """
    supported = {item.name for item in fields(dataclass_type)}
    return {key: value for key, value in raw.items() if key in supported}


def _deserialize_proposals(payload: object, scope: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("No se recibieron propuestas para guardar")
    proposals: list[dict[str, Any]] = []
    for raw_proposal in payload:
        if not isinstance(raw_proposal, dict):
            raise ValueError("Propuesta remota invalida")
        raw_result = raw_proposal.get("result")
        raw_inputs = raw_proposal.get("inputs")
        if not isinstance(raw_result, dict) or not isinstance(raw_inputs, dict):
            raise ValueError("La propuesta remota no contiene inputs y resultado")
        result_values = dict(raw_result)
        result_values["allocations"] = [
            StrategyAllocation(**_supported_dataclass_values(StrategyAllocation, item))
            for item in result_values.get("allocations") or []
            if isinstance(item, dict)
        ]
        result_values["decision_log"] = [
            OptimizationDecision(**_supported_dataclass_values(OptimizationDecision, item))
            for item in result_values.get("decision_log") or []
            if isinstance(item, dict)
        ]
        result_values["unused_sets"] = [
            UnusedSetInfo(**_supported_dataclass_values(UnusedSetInfo, item))
            for item in result_values.get("unused_sets") or []
            if isinstance(item, dict)
        ]
        stress = result_values.get("stress_bootstrap")
        result_values["stress_bootstrap"] = (
            BootstrapDrawdownAnalysis(
                **_supported_dataclass_values(BootstrapDrawdownAnalysis, stress)
            )
            if isinstance(stress, dict) else None
        )
        try:
            result = PortfolioResult(
                **_supported_dataclass_values(PortfolioResult, result_values)
            )
        except TypeError as exc:
            raise ValueError(f"Resultado de propuesta incompatible: {exc}") from exc
        inputs = dict(raw_inputs)
        inputs["portfolio_scope"] = scope
        proposals.append(
            {
                "key": str(raw_proposal.get("key") or ""),
                "label": str(raw_proposal.get("label") or ""),
                "reserve_pct": float(raw_proposal.get("reserve_pct") or 0),
                "inputs": inputs,
                "result": result,
            }
        )
    return proposals


def _saved_request_portfolio_id(
    conn: sqlite3.Connection, request_id: str, scope: str
) -> int | None:
    rows = conn.execute(
        "select id,metrics_json from portfolios "
        "where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
        (scope,),
    ).fetchall()
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        inputs = metrics.get("inputs") if isinstance(metrics, dict) else None
        if isinstance(inputs, dict) and inputs.get("_manager_save_request_id") == request_id:
            return int(row["id"])
    return None


def _selected_proposal(
    proposals: list[dict[str, Any]], selected_key: str, scope: str
) -> dict[str, Any]:
    selected = next(
        (proposal for proposal in proposals if str(proposal.get("key") or "") == selected_key),
        None,
    )
    if selected is None:
        raise ValueError("La propuesta seleccionada ya no esta disponible")
    result: PortfolioResult = selected["result"]
    inputs: dict[str, Any] = selected["inputs"]
    if not result.allocations:
        raise ValueError("La propuesta no tiene asignaciones")
    if (
        scope == "monthly"
        and inputs.get("strict_yearly_month_validation")
        and not result.seasonal_validation.get("passed")
    ):
        raise ValueError("La propuesta mensual no paso la validacion estricta")
    return selected


def _insert_proposal(
    helper: _PortfolioPersistence,
    conn: sqlite3.Connection,
    proposals: list[dict[str, Any]],
    selected: dict[str, Any],
    scope: str,
) -> int:
    result: PortfolioResult = selected["result"]
    if scope == "full_history":
        return helper._insert_portfolio_bundle(conn, proposals, result, commit=False)
    return helper._insert_portfolio(conn, selected["inputs"], result, commit=False)


def _replace_from_temporary(
    helper: _PortfolioPersistence,
    conn: sqlite3.Connection,
    proposals: list[dict[str, Any]],
    selected: dict[str, Any],
    scope: str,
    portfolio_id: int,
    reason: str,
) -> int:
    current = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
    if current is None:
        raise ValueError("El portafolio ya no existe")
    current_scope = str(current["portfolio_scope"] or "full_history")
    if current_scope != scope:
        raise ValueError("El portafolio no pertenece al ambito seleccionado")
    helper._save_portfolio_version(conn, portfolio_id, reason)
    temporary_id = _insert_proposal(helper, conn, proposals, selected, scope)
    temporary = conn.execute("select * from portfolios where id=?", (temporary_id,)).fetchone()
    if temporary is None:
        raise ValueError("No se pudo preparar el reemplazo del portafolio")

    columns = [
        str(row["name"])
        for row in conn.execute("pragma table_info(portfolios)").fetchall()
        if str(row["name"]) not in {"id", "created_at", "target_strategies"}
    ]
    target_strategies = max(
        int(current["target_strategies"] or 0),
        int(temporary["active_strategies"] or 0),
    )
    conn.execute(
        "update portfolios set "
        + ",".join(f"{column}=?" for column in columns)
        + ",target_strategies=? where id=?",
        [temporary[column] for column in columns] + [target_strategies, portfolio_id],
    )
    for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members"):
        conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
        conn.execute(
            f"update {table} set portfolio_id=? where portfolio_id=?",
            (portfolio_id, temporary_id),
        )
    conn.execute("delete from portfolios where id=?", (temporary_id,))
    return portfolio_id


def save_portfolio_payload(memory_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist an authenticated manager proposal in the node's local SQLite."""
    scope = "monthly" if str(payload.get("scope")) == "monthly" else "full_history"
    request_id = str(payload.get("request_id") or "").strip()
    selected_key = str(payload.get("selected_key") or "").strip()
    operation = str(payload.get("operation") or "generate")
    if not request_id or not selected_key:
        raise ValueError("Solicitud de guardado incompleta")
    if operation not in {"generate", "reoptimize", "complete"}:
        raise ValueError("Operacion de guardado desconocida")

    helper = _PortfolioPersistence()
    try:
        conn = connect_memory(memory_path, timeout=10.0)
    except sqlite3.OperationalError as exc:
        raise ValueError(f"No se pudo abrir la memoria UBS local: {exc}") from exc
    try:
        helper._ensure_portfolio_schema(conn)
        existing_id = _saved_request_portfolio_id(conn, request_id, scope)
        if existing_id is not None:
            row = conn.execute(
                "select total_net_profit,total_lot,active_strategies from portfolios where id=?",
                (existing_id,),
            ).fetchone()
            return {
                "portfolio_id": existing_id,
                "request_id": request_id,
                "deduplicated": True,
                "total_net_profit": float(row["total_net_profit"] or 0) if row else 0.0,
                "total_lot": float(row["total_lot"] or 0) if row else 0.0,
                "active_strategies": int(row["active_strategies"] or 0) if row else 0,
            }

        proposals = _deserialize_proposals(payload.get("proposals"), scope)
        selected = _selected_proposal(proposals, selected_key, scope)
        try:
            if operation in {"reoptimize", "complete"}:
                portfolio_id = int(payload.get("portfolio_id") or 0)
                if portfolio_id <= 0:
                    raise ValueError("Falta el portafolio que se quiere actualizar")
                saved_id = _replace_from_temporary(
                    helper,
                    conn,
                    proposals,
                    selected,
                    scope,
                    portfolio_id,
                    "Antes de reoptimizar"
                    if operation == "reoptimize"
                    else "Antes de completar portafolio",
                )
            else:
                saved_id = _insert_proposal(helper, conn, proposals, selected, scope)
            allocation_count = int(
                conn.execute(
                    "select count(*) from portfolio_allocations where portfolio_id=?",
                    (saved_id,),
                ).fetchone()[0]
            )
            if allocation_count <= 0:
                raise ValueError(f"El portafolio #{saved_id} se escribio sin estrategias")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute(
            "select total_net_profit,total_lot,active_strategies from portfolios where id=?",
            (saved_id,),
        ).fetchone()
        return {
            "portfolio_id": saved_id,
            "request_id": request_id,
            "deduplicated": False,
            "total_net_profit": float(row["total_net_profit"] or 0) if row else 0.0,
            "total_lot": float(row["total_lot"] or 0) if row else 0.0,
            "active_strategies": int(row["active_strategies"] or 0) if row else 0,
        }
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise ValueError(
                "No se pudo guardar porque la memoria UBS esta bloqueada por otro proceso. "
                "La propuesta sigue disponible; intentalo de nuevo cuando termine."
            ) from exc
        raise
    finally:
        conn.close()


def exclude_portfolio_members_payload(
    project_dir: str | Path,
    broker: str,
    memory_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Quarantine selected bundle members locally, then delete the bundle."""
    project = Path(project_dir).expanduser().resolve()
    active_memory = Path(memory_path).expanduser().resolve()
    portfolio_id = int(payload.get("portfolio_id") or 0)
    scope = "monthly" if str(payload.get("scope") or "") == "monthly" else "full_history"
    raw_paths = payload.get("set_paths")
    single_path = payload.get("set_path") or payload.get("set_id")
    if portfolio_id <= 0:
        raise ValueError("Falta el portafolio que contiene las estrategias")
    multiple = isinstance(raw_paths, list) and bool(raw_paths)
    if not multiple and not single_path:
        raise ValueError("Selecciona al menos una estrategia")

    def path_key(value: object) -> str:
        return str(Path(str(value or "")).expanduser()).replace("/", "\\").casefold()

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
    selected: list[dict[str, Any]] = []
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

    reason_code = normalize_reason_code(payload.get("reason_code"))
    reason = reason_with_verdict(
        "Excluida manualmente desde un portafolio A/M/C guardado" if is_bundle
        else "Excluida manualmente desde un Portafolio UBS mensual guardado" if scope == "monthly"
        else "Retirada manualmente de un portafolio guardado",
        reason_code,
    )

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

    quarantine_ids: list[int] = []
    verdict_applied = reason_code != MANUAL_REASON
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
                        portfolio_id,
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

    # EL PORTAFOLIO GUARDADO NO SE TOCA. Antes se borraba entero (bundle A/M/C,
    # mes, o cualquier exclusion multiple) o se le quitaba la asignacion y se
    # recalculaban sus metricas. Las dos cosas destruian un resultado guardado
    # como efecto colateral de una decision sobre el pool. La exclusion afecta
    # ahora a lo que decide: el pool y, si hay veredicto, los estados del agente.
    # Misma regla en el manager: `PortfolioSource._quarantine_member`.
    # `verdict_applied` es la confirmacion que exige el manager cuando el motivo
    # no es manual: sin ella avisa en vez de dar por escrito un veredicto que
    # este nodo no habria aplicado.
    if multiple:
        return {
            "quarantine_ids": quarantine_ids,
            "deleted": False,
            "portfolio_id": portfolio_id,
            "scope": scope,
            "reason_code": reason_code,
            "verdict_applied": verdict_applied,
        }
    return {
        "quarantine_id": quarantine_ids[0] if quarantine_ids else 0,
        "deleted": False,
        "portfolio_id": portfolio_id,
        "scope": scope,
        "reason_code": reason_code,
        "verdict_applied": verdict_applied,
    }
