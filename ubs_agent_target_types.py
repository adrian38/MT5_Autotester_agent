"""Tipos internos usados por la seleccion de objetivos del agente UBS."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiverseTargetOptions:
    timeframe_universe: tuple[str, ...]
    symbol_map: dict[str, str] | None
    disabled_symbols: set[str] | None
    force_unseeded_universe: bool
    unseeded_universe_symbols: tuple[str, ...]
    unseeded_timeframes: tuple[str, ...]
    asset_unseeded_probability: float
    timeframe_unseeded_probability: float
    production_mode: bool
    group_by_symbol: dict[str, str] | None
    asset_group_feedback: dict[str, float] | None
    universe_feedback_probability: float
    current_target_probability: float
    current_timeframe_probability: float
