from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
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
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin


class _PortfolioPersistence(UBSPortfolioLogicMixin):
    """Reuse the desktop portfolio persistence without constructing the UI."""


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
            StrategyAllocation(**item)
            for item in result_values.get("allocations") or []
            if isinstance(item, dict)
        ]
        result_values["decision_log"] = [
            OptimizationDecision(**item)
            for item in result_values.get("decision_log") or []
            if isinstance(item, dict)
        ]
        result_values["unused_sets"] = [
            UnusedSetInfo(**item)
            for item in result_values.get("unused_sets") or []
            if isinstance(item, dict)
        ]
        stress = result_values.get("stress_bootstrap")
        result_values["stress_bootstrap"] = (
            BootstrapDrawdownAnalysis(**stress) if isinstance(stress, dict) else None
        )
        try:
            result = PortfolioResult(**result_values)
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
