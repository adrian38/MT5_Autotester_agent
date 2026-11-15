"""Como se lee y se compara un valor guardado en una fila de etapa.

Puro: no sabe de etapas ni abre SQLite, solo contesta sobre un valor suelto
-si es el mismo que el de ahora, como se escribe para la pantalla, si una
fecha no es una fecha-. Salio de `stage_rules_audit`, que lo tenia mezclado
con las consultas y no paraba de crecer.

`stage_rules_audit` reexporta lo publico (`missing_window_date`, los dos
margenes), que es por donde lo pide el resto.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Mapping

from ubs.stage_specs import RuleSpec


# Margen con el que se comparan dos umbrales numericos. SQLite guarda reales y
# la interfaz escribe texto: sin margen, "35" y 35.0 se verian distintos.
NUMBER_TOLERANCE = 1e-6

# Ano hasta el que una fecha guardada se lee como el cero de MT5 y no como una
# fecha que alguien puso.
EPOCH_YEAR = 1970


def _score_config(raw: object) -> Mapping[str, object] | None:
    try:
        payload = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    stored = payload.get("score_config")
    return stored if isinstance(stored, Mapping) else None


def _format_number(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


def _same_value(stored: object, current: object, kind: str) -> bool:
    if kind == "date":
        return str(stored or "").strip() == str(current or "").strip()
    try:
        return abs(float(stored) - float(current)) <= NUMBER_TOLERANCE  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(stored or "").strip() == str(current or "").strip()


def missing_window_date(value: object) -> bool:
    """Una fecha vacia o el cero de MT5 no es una regla: es la falta de una.

    Cuando el reporte OHLC no trae periodo -un backtest que no produjo nada-
    MT5 escribe 1970.01.01, y `_read_ohlc_report_cfg_dates()` lo copia tal cual
    a la fila. Compararlo con la ventana de hoy no dice que la regla se haya
    movido: dice que esa fila no se juzgo con ninguna. Lo mismo vale para la
    fecha vacia de una fila que aun no ha corrido.
    """
    text = str(value or "").strip()
    if not text:
        return True
    year = text[:4]
    return year.isdigit() and int(year) <= EPOCH_YEAR


def _undated_reason(value: object) -> str:
    """Por que la fila no tiene ventana, que es lo unico que importa de ella.

    El valor guardado no dice nada por si solo -1970.01.01 es el cero que MT5
    escribe en un reporte sin periodo- y ensenarlo obligaba a saberse esa
    equivalencia de memoria para leer la celda.
    """
    return "no ha corrido" if not str(value or "").strip() else "reporte sin periodo"


def _display(value: object, kind: str) -> str:
    if kind == "date":
        return str(value or "").strip() or "(vacio)"
    return _format_number(value)


def _stored_rule_value(
    row: sqlite3.Row,
    rule: RuleSpec,
    *,
    columns: set[str],
    score_config: Mapping[str, object] | None,
) -> tuple[object, bool]:
    """Valor con el que se juzgo la fila, y si la fila no lo guarda."""
    if rule.column:
        if rule.column not in columns:
            return None, False
        return row[rule.column], True
    if rule.score_field:
        if score_config is None or rule.score_field not in score_config:
            return None, False
        return score_config[rule.score_field], True
    return None, False


def _by_count(values: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    """Valores con su cuenta, el mas repetido primero y a igualdad por nombre."""
    return tuple(sorted(values.items(), key=lambda item: (-item[1], item[0])))
