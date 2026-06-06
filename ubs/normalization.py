from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ubs.universe import canonical_symbol, load_asset_universe


DEFAULT_BASIS = "raw_net_profit"


def net_profit_normalization(symbol: str, *, base_dir: Path | None = None) -> tuple[float, str, str]:
    """Return multiplier, group and basis for scoring net profit.

    The factor is intentionally limited to scoring. Backtests still run with the
    lot defined by the generated .set file, currently forced to 0.01.
    """
    root = base_dir or Path(__file__).resolve().parent.parent
    config = _load_config(root / "assets" / "roboforex_normalization.json")
    group_by_symbol, aliases = _asset_group_index(root / "assets" / "roboforex_assets.ini")

    canonical = canonical_symbol(str(symbol or ""), aliases)
    factor = float(config.get("default_net_profit_factor") or 1.0)
    group = group_by_symbol.get(canonical.upper(), "")

    group_factors = config.get("group_net_profit_factors")
    if isinstance(group_factors, dict) and group:
        factor = _safe_float(group_factors.get(group), factor)

    symbol_factors = config.get("symbol_net_profit_factors")
    if isinstance(symbol_factors, dict):
        factor = _safe_float(symbol_factors.get(canonical.upper()), factor)

    if factor <= 0:
        factor = 1.0
    basis = str(config.get("basis") or DEFAULT_BASIS)
    return factor, group, basis


@lru_cache(maxsize=8)
def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=8)
def _asset_group_index(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    groups, aliases = load_asset_universe(path, include_disabled=True)
    group_by_symbol: dict[str, str] = {}
    for group, symbols in groups.items():
        for symbol in symbols:
            group_by_symbol[str(symbol).upper()] = group
    return group_by_symbol, aliases


def _safe_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
