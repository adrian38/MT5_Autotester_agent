"""Memorias de pega para las pruebas de auditoria de etapas.

Las tres pruebas de auditoria -reglas movidas, pendientes y que arreglo le
toca a cada etapa- parten de la misma memoria vacia y de las mismas filas de
pega. Vivian con la primera, que se paso de las 600 lineas en cuanto las otras
dos crecieron.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

from ubs.memory_schema import SCHEMA_DDL
from ubs.stage_rules_audit import STAGE_SPEC_BY_KEY, stage_spec

FINAL_STATUSES = ("accepted", "rejected")

CURRENT_FINAL_TICK = {
    "min_history_quality": 80.0,
    "max_net_delta_pct": 35.0,
    "max_pf_delta_pct": 30.0,
    "max_dd_delta_pct": 35.0,
    "max_trades_delta_pct": 35.0,
    "max_dd_gap_pp": 3.0,
    "from_date": "2026.05.01",
    "to_date": "2026.05.31",
}

CURRENT_SCORE = {
    "min_net_profit": 100.0,
    "min_profit_factor": 1.20,
    "min_trades": 60,
    "max_drawdown_pct": 25.0,
    "min_recovery_factor": 1.0,
}


def memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_DDL)
    return conn


def insert_candidate(
    conn,
    candidate_id: int,
    *,
    run_id: int,
    status: str,
    min_trades: object,
    policy: str = "",
) -> None:
    metrics = {}
    if min_trades is not None:
        metrics["score_config"] = {**CURRENT_SCORE, "min_trades": min_trades}
    conn.execute(
        """
        insert into candidates (
            id, run_id, generation, seed_path, set_path, symbol, target_symbol, period,
            family, run_strategy, mutated_keys, timeframe_keys, mutation_details_json,
            missing_lot_keys, policy, metrics_json, status, created_at
        ) values (?, ?, 1, '', '', 'EURUSD', 'EURUSD', 'H1', 'fam', 'run', '', '', '', '', ?, ?, ?, '')
        """,
        (candidate_id, run_id, policy, json.dumps(metrics), status),
    )


def insert_final_tick(conn, candidate_id: int, *, run_id: int, status: str, **overrides) -> None:
    values = {
        "min_history_quality": 80.0,
        "max_net_delta_pct": 35.0,
        "max_pf_delta_pct": 35.0,
        "max_dd_delta_pct": 35.0,
        "max_trades_delta_pct": 35.0,
        "max_dd_gap_pp": 3.0,
        "from_date": "2026.05.01",
        "to_date": "2026.05.31",
        # Sin sello la fila no se juzgo nunca, que es lo que quieren casi todas
        # estas pruebas: asi el cambio de regla declarado no las alcanza.
        "evaluated_at": "",
    }
    values.update(overrides)
    conn.execute(
        """
        insert into candidate_final_tick (
            candidate_id, run_id, status, min_history_quality, from_date, to_date,
            max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
            max_dd_gap_pp, evaluated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id, run_id, status, values["min_history_quality"],
            values["from_date"], values["to_date"], values["max_net_delta_pct"],
            values["max_pf_delta_pct"], values["max_dd_delta_pct"],
            values["max_trades_delta_pct"], values["max_dd_gap_pp"],
            values["evaluated_at"],
        ),
    )


# Un cambio de regla de mentira y los dos sellos que lo rodean. En produccion
# no hay ninguno declarado, ni deberia haberlo mientras nadie cambie el codigo
# que decide accepted/rejected, asi que la prueba se lo pone ella.
RULE_CHANGED_AT = "2026-06-01T12:00:00"
BEFORE_CHANGE = "2026-05-01T09:00:00"
AFTER_CHANGE = "2026-07-01T09:00:00"
RULE_CHANGE_NOTE = "Cambio declarado de prueba"


@contextmanager
def declared_rule_change(stage_key: str, *, fix: str = "rescore"):
    """Declara un cambio de regla en una etapa mientras dure la prueba.

    `STAGE_SPEC_BY_KEY` es el mismo objeto que lee `stage_spec()`, asi que
    cambiarlo aqui alcanza a quien pregunte por la etapa, venga de la auditoria
    o de la pantalla.
    """
    spec = replace(
        stage_spec(stage_key),
        rules_changed_at=RULE_CHANGED_AT,
        rules_change_note=RULE_CHANGE_NOTE,
        rules_change_fix=fix,
    )
    with patch.dict(STAGE_SPEC_BY_KEY, {stage_key: spec}):
        yield


def insert_robustness(conn, candidate_id: int, *, run_id: int, status: str) -> None:
    conn.execute(
        """
        insert into candidate_robustness (
            candidate_id, run_id, status, from_date, to_date,
            positive_bonus, negative_bonus, evaluated_at
        ) values (?, ?, ?, '2026.01.01', '2026.06.30', 70.0, -70.0, '')
        """,
        (candidate_id, run_id, status),
    )

