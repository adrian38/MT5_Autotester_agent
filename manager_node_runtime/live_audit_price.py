"""Familias y pisos absolutos de tolerancia de precio para auditoría viva."""
from __future__ import annotations

import re

ADAPTIVE_PRICE_TOLERANCE_FLOORS = {
    "indices": 10.5,
    "nasdaq": 5.0,
    "crypto_btc": 10.0,
    "gold": 2.05,
    "silver": 0.02,
    "jpy_fx": 0.05,
    "fx": 0.0005,
}
_INDEX_SYMBOL_PREFIXES = ("US30", "DE40", "USTEC", "USTECH")
_FX_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"})


def adaptive_price_tolerance_floor(symbol: str) -> tuple[float | None, str]:
    """Devuelve el piso absoluto validado para la familia del instrumento."""
    root = re.split(r"[^A-Z0-9]", str(symbol or "").upper(), maxsplit=1)[0]
    if root.startswith(("NAS100", "US100")):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["nasdaq"], "adaptive_nasdaq"
    if root.startswith("BTC"):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["crypto_btc"], "adaptive_crypto_btc"
    if root.startswith(_INDEX_SYMBOL_PREFIXES):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["indices"], "adaptive_indices"
    if root.startswith("XAU"):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["gold"], "adaptive_gold"
    if root.startswith("XAG"):
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS["silver"], "adaptive_silver"
    if len(root) >= 6 and root[:3] in _FX_CURRENCIES and root[3:6] in _FX_CURRENCIES:
        family = "jpy_fx" if root[3:6] == "JPY" else "fx"
        return ADAPTIVE_PRICE_TOLERANCE_FLOORS[family], f"adaptive_{family}"
    return None, "configured_points"
