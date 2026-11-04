"""Traceability metadata and general-set recognition for UBS seeds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from ubs.seed_dedup import parse_set_params
from ubs.set_utils import read_set_with_encoding


STRATEGY_TYPES = {
    "1": "Soporte/Resistencia",
    "2": "Volatility Breakout",
    "3": "Range Breakout",
}
SOURCE_MARKERS = {
    "breakoutsupportresistance": STRATEGY_TYPES["1"],
    "volatilitybreakout": STRATEGY_TYPES["2"],
    "rangebreakout": STRATEGY_TYPES["3"],
    "ubsliveportfolio": "",
}
CLIENT_SETS_MARKER = "clientsets"


@dataclass(frozen=True)
class SeedTraceMetadata:
    client: str = ""
    strategy_type: str = ""
    family: str = ""


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _display(value: str) -> str:
    return value.replace("_", " ").strip()


def _marker(parts: tuple[str, ...]) -> tuple[int, str] | None:
    for index, part in enumerate(parts):
        compact = _compact(part)
        if compact == CLIENT_SETS_MARKER or compact in SOURCE_MARKERS:
            return index, compact
    return None


def _flattened_parts(path: Path) -> list[str]:
    return [_display(part) for part in path.stem.split("__") if part]


def seed_trace_from_path(
    path: Path,
    params: dict[str, str] | None = None,
) -> SeedTraceMetadata:
    params = params or {}
    parent_parts = tuple(path.parent.parts)
    found = _marker(parent_parts)
    strategy_type = STRATEGY_TYPES.get(str(params.get("Run_Strategy") or ""), "")
    if found is None:
        return SeedTraceMetadata(strategy_type=strategy_type)

    index, marker = found
    trailing = [_display(part) for part in parent_parts[index + 1:]]
    flattened = _flattened_parts(path)
    if marker == CLIENT_SETS_MARKER:
        client = trailing[0] if trailing else (flattened[0] if flattened else "")
        family_parts = trailing[1:] if trailing else flattened[1:-1]
        return SeedTraceMetadata(client, strategy_type, " / ".join(family_parts))

    strategy_type = strategy_type or SOURCE_MARKERS[marker]
    family_parts = trailing if trailing else flattened[:-1]
    if marker == "ubsliveportfolio" and family_parts == ["Sets"]:
        family_parts = []
    return SeedTraceMetadata("Profalgo", strategy_type, " / ".join(family_parts))


def seed_trace_from_file(path: Path) -> SeedTraceMetadata:
    try:
        text, _encoding = read_set_with_encoding(path)
        return seed_trace_from_path(path, parse_set_params(text))
    except (OSError, UnicodeError):
        return seed_trace_from_path(path)


def is_general_ubs_set(path: Path, params: dict[str, str]) -> bool:
    """Return true for OneChart/auto-loader aggregate settings, not real seeds."""
    if str(params.get("UseAutoLoader") or "").strip().lower() in {"1", "true", "yes"}:
        return True
    compact = _compact(path.stem)
    return "onechart" in compact and "main" in compact and "settings" in compact
