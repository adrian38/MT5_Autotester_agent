"""Las filas de pega del auditor: un hallazgo, una etapa y una pendiente.

Las comparten las dos hojas de pruebas de la pantalla -la que mira lo que
hace la interfaz y la que mira lo que pinta-, que salieron de un mismo fichero
cuando se paso de las 600 lineas.
"""
from __future__ import annotations

from types import SimpleNamespace


def fake_finding(**overrides):
    """Un hallazgo de pega: una regla movida con sus filas y sus veredictos."""
    values = {
        "rule": "max_pf_delta_pct", "rule_label": "Delta PF %", "current": "30",
        "stored": (("35", 4),), "rows": 4, "verdicts": 4, "fix": "rescore",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_audit_row(**overrides):
    """Una etapa auditada de pega, con todo a cero salvo lo que pida la prueba."""
    values = {
        "stage": "final_tick",
        "stage_label": "Final Tick",
        "rows": 10,
        "affected": 0,
        "affected_verdicts": 0,
        "findings": (),
        "stale": 0,
        "stale_verdicts": 0,
        "undated": 0,
        "undated_verdicts": 0,
        "undated_stored": (),
        "offwindow": 0,
        "offwindow_verdicts": 0,
        "window_findings": (),
        "unknown": 0,
        "missing_table": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_pending_row(**overrides):
    values = {
        "stage": "final_tick",
        "stage_label": "Final Tick",
        "rows": 10,
        "pending": 0,
        "missing": 0,
        "by_status": (),
        "missing_table": False,
        "settled": 0,
        "settled_by_status": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)
