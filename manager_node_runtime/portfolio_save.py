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
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin

from .portfolio_verdicts import (
    _exclusion_response_message,
    _group_members_by_memory,
    _quarantine_grouped_members,
    _select_excluded_members,
    MANUAL_REASON,
    apply_candidate_verdict,
    ensure_quarantine_reason_columns,
    normalize_reason_code,
    reason_with_verdict,
    requalify_portfolio_member_payload,  # noqa: F401  reexportado para el nodo
    snapshot_candidate_stages,
)


class _PortfolioPersistence(UBSPortfolioLogicMixin):
    """Reuse the desktop portfolio persistence without constructing the UI."""


def normalize_portfolio_alias(value: Any) -> str:
    """Normalize the optional human label without changing portfolio identity."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("El alias del portafolio debe ser texto")
    alias = " ".join(value.split())
    if len(alias) > 80:
        raise ValueError("El alias del portafolio no puede superar 80 caracteres")
    return alias


def set_portfolio_alias_payload(
    memory_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist the display alias in the node-owned portfolio memory."""
    portfolio_id = int(payload.get("portfolio_id") or 0)
    scope = "monthly" if str(payload.get("scope") or "") == "monthly" else "full_history"
    if portfolio_id <= 0:
        raise ValueError("Falta el portafolio cuyo alias se quiere cambiar")
    if scope != "full_history":
        raise ValueError("El alias solo está disponible en Portafolio UBS")
    alias = normalize_portfolio_alias(payload.get("alias"))
    conn = connect_memory(memory_path, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("begin immediate")
        row = conn.execute(
            "select metrics_json from portfolios where id=? and "
            "coalesce(nullif(portfolio_scope,''),'full_history')=?",
            (portfolio_id, scope),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
        try:
            parsed = json.loads(row["metrics_json"] or "{}")
            metrics = parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            metrics = {}
        inputs = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        metrics["inputs"] = inputs
        if alias:
            inputs["portfolio_alias"] = alias
        else:
            inputs.pop("portfolio_alias", None)
        conn.execute(
            "update portfolios set metrics_json=? where id=?",
            (json.dumps(metrics, ensure_ascii=True, separators=(",", ":")), portfolio_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"portfolio_id": portfolio_id, "scope": scope, "alias": alias}


# --- Motivo de exclusion (copia bifurcada de mt5_manager/candidate_verdict.py) ---
#
# El manager envia `reason_code` con la exclusion. Cuando no es `manual`, la
# estrategia no se retira del portafolio: se declara que FALLO, y este proceso
# escribe el veredicto que habria escrito el pipeline. Es lo mismo que hace el
# FAIL manual de la aplicacion, asi que se llama a `ubs.manual_status`, que es la
# autoridad de este lado; los pesos no se guardan en ninguna tabla y salen de


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
    source_id = int(selected["inputs"].get("improvement_source_portfolio_id") or 0)
    if scope == "full_history" and len(proposals) == 1 and source_id > 0:
        # A selected-mode improvement is a new standalone portfolio. Never
        # replace the source bundle or calculate its other variants here.
        portfolio_id = helper._insert_portfolio(conn, selected["inputs"], result, commit=False)
        labels = {"aggressive": "Agresivo", "balanced": "Moderado", "conservative": "Conservador"}
        mode = str(selected["inputs"]["portfolio_type"])
        label = str(selected["inputs"].get("improvement_label") or "").strip()
        conn.execute(
            "update portfolios set name=? where id=?",
            (label or f"Mejora de #{source_id} | {labels.get(mode, mode)}", portfolio_id),
        )
        return portfolio_id
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


def _saved_portfolio_response(conn, request_id, existing_id):
    """Respuesta para una peticion de guardado ya atendida antes."""
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

def _persist_portfolio_proposal(helper, conn, payload, scope, request_id,
                                selected_key, operation):
    """Guarda la propuesta seleccionada y devuelve el portafolio resultante."""
    helper._ensure_portfolio_schema(conn)
    existing_id = _saved_request_portfolio_id(conn, request_id, scope)
    if existing_id is not None:
        return _saved_portfolio_response(conn, request_id, existing_id)

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
        return _persist_portfolio_proposal(
            helper, conn, payload, scope, request_id, selected_key, operation,
        )
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
    multiple = isinstance(raw_paths, list) and bool(raw_paths)
    if not multiple and not single_path:
        raise ValueError("Selecciona al menos una estrategia")
    # Exclusion de pool: «Gestion por simbolo» excluye un set que no tiene por
    # que pertenecer a ningun portafolio, asi que no hay `portfolio_id` ni fila
    # en `portfolio_allocations` donde buscarlo. Resolverlo aqui es imposible:
    # la ruta que manda el manager no es la que guarda esta memoria
    # (`/data/axi/...` contra `F:\TRADING\...`) y quien sabe traducirla es
    # `_resolve_source_path`, que solo existe en el manager. Por eso el manager
    # resuelve el candidato y lo manda en `pool_member`
    # (`PortfolioSource.pool_member_payload`) y aqui solo se escribe.
    # Sin esta rama la ventana devolvia 400 y no habia forma de sacar del pool
    # un set que ningun portafolio usara.
    pool_member = payload.get("pool_member") if not multiple else None
    pool_exclusion = portfolio_id <= 0
    if pool_exclusion and not isinstance(pool_member, dict):
        raise ValueError("Falta el portafolio que contiene las estrategias")

    def path_key(value: object) -> str:
        return str(Path(str(value or "")).expanduser()).replace("/", "\\").casefold()

    is_bundle = False
    selected: list[dict[str, Any]] = []
    is_bundle, member = _select_excluded_members(active_memory, multiple, path_key, pool_exclusion, pool_member, portfolio_id, raw_paths, scope, selected, single_path)

    reason_code = normalize_reason_code(payload.get("reason_code"))
    reason = reason_with_verdict(
        "Excluida manualmente desde la gestión por símbolo" if pool_exclusion
        else "Excluida manualmente desde un portafolio A/M/C guardado" if is_bundle
        else "Excluida manualmente desde un Portafolio UBS mensual guardado" if scope == "monthly"
        else "Retirada manualmente de un portafolio guardado",
        reason_code,
    )

    grouped = _group_members_by_memory(broker, active_memory, project, selected)

    quarantine_ids: list[int] = []
    verdict_applied = reason_code != MANUAL_REASON
    _quarantine_grouped_members(grouped, portfolio_id, quarantine_ids, reason, reason_code)

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
        return _exclusion_response_message(
            multiple, portfolio_id, quarantine_ids, reason_code, scope, verdict_applied,
        )
    return {
        "quarantine_id": quarantine_ids[0] if quarantine_ids else 0,
        "deleted": False,
        "portfolio_id": portfolio_id or None,
        "scope": scope,
        "reason_code": reason_code,
        "verdict_applied": verdict_applied,
        "pool_exclusion": pool_exclusion,
    }
