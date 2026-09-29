"""Punto de entrada del Final Tick: elige pasada unica o continuacion."""
from __future__ import annotations

import argparse
import sqlite3

from ubs.memory import AgentMemory
from ubs.score import ScoreConfig
from ubs_agent_final_tick import final_tick_stage_label, normalize_final_tick_stage

from ubs_agent_final_tick_pass import _evaluate_candidate_final_tick_pass


def evaluate_candidate_final_tick(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    """Ejecuta Final Tick y continua con las fechas principales si hizo falta.

    Una pasada solo puede servir un rango de fechas: ``args.from_date`` y
    ``args.to_date`` se mutan para toda la ejecucion. Con rango OHLC alternativo
    configurado, la pasada corre las filas en scope de retry y aparta el resto.
    El flujo de UI cuenta con que el usuario vuelva a pulsar el boton, pero el
    pipeline del manager avanza a ``final_tick_*_quality`` acto seguido, asi que
    la continuacion tiene que ocurrir aqui: si no, esas filas se quedan sin
    evaluar y la etapa se da por terminada con codigo 0.
    """
    final_tick_label = final_tick_stage_label(
        normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    )
    deferred: list[sqlite3.Row] = []
    main_from_date = getattr(args, "from_date", None)
    main_to_date = getattr(args, "to_date", None)
    code = _evaluate_candidate_final_tick_pass(
        args, memory, score_config, deferred_out=deferred
    )
    if code != 0 or not deferred:
        return code
    args.from_date = main_from_date
    args.to_date = main_to_date
    print(
        f"{final_tick_label} continuacion: {len(deferred)} fila(s) pendientes con "
        f"fechas principales {main_from_date} -> {main_to_date}."
    )
    # ``allow_ohlc_retry=False`` fuerza la rama de fechas principales sin borrar
    # el rango alternativo, que sigue haciendo falta para no reencolar las filas
    # cuyo retry OHLC ya se agoto.
    return _evaluate_candidate_final_tick_pass(
        args, memory, score_config, allow_ohlc_retry=False
    )
