"""Creacion local de un portafolio a partir de ficheros ``.set`` subidos."""
from __future__ import annotations

import base64
import binascii
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from portfolio_manager.ubs_portfolio import PortfolioResult, StrategyAllocation
from run_tests_symbols import infer_tester_fields_from_set, load_set_params
from ubs.db import connect_memory
from ubs.set_rules import normalize_generated_set_text
from ubs.set_utils import read_set_with_encoding, set_use_every_tick_text, write_set_text

from .portfolio_save import _PortfolioPersistence


MODE_LABELS = {
    "aggressive": "Agresivo",
    "balanced": "Moderado",
    "conservative": "Conservador",
}
TIMEFRAME_LABELS = {
    "1": "M1", "2": "M2", "3": "M3", "4": "M4", "5": "M5",
    "6": "M6", "10": "M10", "12": "M12", "15": "M15", "20": "M20",
    "30": "M30", "60": "H1", "120": "H2", "180": "H3", "240": "H4",
    "360": "H6", "480": "H8", "720": "H12", "1440": "D1",
    "10080": "W1", "43200": "MN1",
}


def normalize_manual_set(path: Path) -> None:
    """Aplica al set manual las mismas invariantes que al incorporarlo al agente."""
    text, encoding = read_set_with_encoding(path)
    normalized, _found, _missing = normalize_generated_set_text(text)
    normalized = set_use_every_tick_text(normalized, False)
    if normalized != text:
        write_set_text(path, normalized, encoding)


def _member(path: Path, index: int) -> StrategyAllocation:
    values = load_set_params(path)
    fields = infer_tester_fields_from_set(path)
    symbol = str(fields.get("Symbol") or values.get("ForceSymbol") or values.get("Symbol") or "")
    raw_timeframe = str(fields.get("Period") or values.get("TimeFrame") or values.get("Period") or "")
    timeframe = TIMEFRAME_LABELS.get(raw_timeframe, raw_timeframe.upper())
    if not symbol or not timeframe:
        raise ValueError(
            f"{path.name}: no se pudieron inferir el simbolo y el timeframe para auditarlo"
        )
    try:
        lot = float(values.get("StartLots") or "0.01")
    except ValueError as exc:
        raise ValueError(f"{path.name}: StartLots no es numerico") from exc
    if lot <= 0:
        raise ValueError(f"{path.name}: StartLots debe ser mayor que cero")
    return StrategyAllocation(
        set_id=path.name, candidate_id=f"manual:{index}", symbol=symbol,
        timeframe=timeframe, units=1, lot=lot, set_path=str(path),
        net_profit_contribution=0.0, standalone_valley_dd=0.0,
        standalone_point_dd=0.0, floating_dd_source="Pendiente de auditoria",
    )


def _write_sets(runtime_dir: Path, raw_files: object) -> tuple[Path, list[Path]]:
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("Selecciona una carpeta que contenga al menos un fichero .set")
    target = runtime_dir / "manual_portfolios" / uuid.uuid4().hex / "sets"
    target.mkdir(parents=True, exist_ok=False)
    paths: list[Path] = []
    names: set[str] = set()
    total = 0
    try:
        for item in raw_files:
            if not isinstance(item, dict):
                raise ValueError("La carpeta contiene una entrada no valida")
            name = Path(str(item.get("name") or "").replace("\\", "/")).name
            if not name.lower().endswith(".set") or not name:
                continue
            if name.casefold() in names:
                raise ValueError(f"Hay dos ficheros .set con el mismo nombre: {name}")
            try:
                content = base64.b64decode(str(item.get("content") or ""), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"{name}: contenido base64 no valido") from exc
            total += len(content)
            if total > 35_000_000:
                raise ValueError("La carpeta de sets supera el limite de 35 MB")
            path = target / name
            path.write_bytes(content)
            normalize_manual_set(path)
            paths.append(path)
            names.add(name.casefold())
        if not paths:
            raise ValueError("La carpeta seleccionada no contiene ficheros .set")
        return target.parent, paths
    except Exception:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise


def _result(paths: list[Path]) -> PortfolioResult:
    allocations = [_member(path, index) for index, path in enumerate(paths, 1)]
    return PortfolioResult(
        allocations=allocations, equity_curve_2020_2026=[], total_net_profit=0.0,
        actual_valley_dd=0.0, actual_point_dd=0.0, target_valley_dd=0.0,
        target_point_dd=0.0, valley_usage_pct=0.0, point_usage_pct=0.0,
        total_lot=sum(item.lot for item in allocations), total_units=len(allocations),
        active_strategies=len(allocations),
        stop_reason="Portafolio manual: historico pendiente de auditoria",
        warnings=["Creado desde sets subidos; las metricas se completan mediante auditorias."],
        decision_log=[], enforce_point_dd=False,
    )


def create_manual_portfolio(
    memory_path: str | Path, runtime_dir: Path, payload: dict[str, Any],
) -> dict[str, Any]:
    """Guarda los sets en el nodo y registra su composicion para auditorias."""
    name = " ".join(str(payload.get("name") or "").split())
    mode = str(payload.get("portfolio_type") or "balanced").strip().lower()
    if not name or len(name) > 80:
        raise ValueError("Indica un nombre de portafolio de hasta 80 caracteres")
    if mode not in MODE_LABELS:
        raise ValueError("El modo debe ser Agresivo, Moderado o Conservador")
    folder, paths = _write_sets(runtime_dir, payload.get("files"))
    try:
        result = _result(paths)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    inputs = {
        "portfolio_type": mode, "optimization_profile": mode,
        "optimization_profile_label": MODE_LABELS[mode], "portfolio_scope": "full_history",
        "capital": 0.0, "valley_dd_pct": 0.0, "point_dd_pct": 0.0,
        "manual_portfolio": True, "portfolio_alias": name,
    }
    helper = _PortfolioPersistence()
    conn: sqlite3.Connection | None = None
    try:
        conn = connect_memory(memory_path, timeout=10.0)
        helper._ensure_portfolio_schema(conn)
        portfolio_id = helper._insert_portfolio(conn, inputs, result, commit=False)
        conn.execute("update portfolios set name=? where id=?", (name, portfolio_id))
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        if conn is not None:
            conn.close()
    return {
        "portfolio_id": portfolio_id, "name": name, "portfolio_type": mode,
        "strategies": len(paths), "sets_folder": str(folder / "sets"),
    }
