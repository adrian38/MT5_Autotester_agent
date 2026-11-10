"""Contrato de estrategias disponible en el EA UBS V6 actual."""

from __future__ import annotations


STRATEGY_LABELS: dict[str, str] = {
    "1": "Breakout Support/Resistance",
    "2": "Volatility Breakout",
}
VALID_RUN_STRATEGIES = frozenset(STRATEGY_LABELS)
STRATEGY_TIMEFRAME_KEYS: dict[str, tuple[str, ...]] = {
    "1": ("Entry_Timing",),
    "2": ("VolTimeframe",),
}
STRATEGY_MARKER_KEYS: dict[str, tuple[str, ...]] = {
    "1": ("ST1_Timeframe", "Entry_Timing"),
    "2": ("VolTimeframe",),
}
GENERAL_TIMEFRAME_KEYS = ("Entry_Timing", "ATR_Timeframe")


def strategy_label(value: object) -> str:
    """Etiqueta el enum V6 o deja visible que el valor no es compatible."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return STRATEGY_LABELS.get(normalized, f"Incompatible V6 (Run_Strategy={normalized})")
