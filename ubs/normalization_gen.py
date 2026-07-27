"""Compute broker net-profit normalization factors from live MT5 symbol specs.

The UBS scorer runs every backtest at a forced ``StartLots=0.01`` but the broker
clamps each order up to the symbol's minimum volume. On AXI that means forex runs
at 0.01 lot (~1000 units of notional) while share CFDs run at 1.0 lot on a tiny
per-share notional. A flat per-group factor cannot make those comparable because
stock prices span ~20x. This module derives a *per-symbol* factor from the real
contract value so ``normalized_net_profit`` represents net profit at a common
notional exposure, independent of the broker's minimum-lot quirk.

    factor = reference_notional / (lot_used * notional_per_lot)
    notional_per_lot = price * tick_value / tick_size      (account currency)
    lot_used         = the lot MT5 actually runs (0.01 clamped to volume_min/step)

The functions here are pure (no MT5 dependency) so they can be unit tested; the
CLI in ``tools/gen_axi_normalization.py`` feeds them live ``SymbolSpec`` rows.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from ubs.mt5_symbol_extract import SymbolSpec


# A 0.01-lot forex position is ~1000 units of the base currency, which is the
# historical anchor the flat forex factor (1.0) implicitly used. Anchoring the
# reference notional here keeps forex factors ~1.0 while correcting every other
# group onto the same notional basis.
DEFAULT_REFERENCE_NOTIONAL = 1000.0
DEFAULT_REQUESTED_LOT = 0.01

# Defensive bounds so a stale/zero price snapshot cannot emit an absurd factor.
_MIN_FACTOR = 1e-4
_MAX_FACTOR = 1e5


@dataclass(frozen=True)
class SymbolFactor:
    name: str
    factor: float
    lot_used: float
    notional_per_lot: float
    actual_notional: float
    price: float
    group: str = ""


def clamp_lot(
    requested_lot: float,
    volume_min: float,
    volume_step: float,
    volume_max: float = 0.0,
) -> float:
    """Return the lot MT5 would actually trade for ``requested_lot``.

    MT5 snaps volume to ``volume_step`` and enforces ``[volume_min, volume_max]``.
    A 0.01 request on a min-1.0 symbol therefore executes at 1.0.
    """
    lot = float(requested_lot or 0.0)
    step = float(volume_step or 0.0)
    vmin = float(volume_min or 0.0)
    vmax = float(volume_max or 0.0)
    if step > 0:
        lot = round(lot / step) * step
        if lot < step:
            lot = step
    if vmin > 0 and lot < vmin:
        lot = vmin
    if vmax > 0 and lot > vmax:
        lot = vmax
    if lot <= 0:
        lot = vmin if vmin > 0 else float(requested_lot or 0.0)
    return lot


def notional_per_lot(price: float, tick_value: float, tick_size: float) -> float:
    """Account-currency notional value of one 1.0-lot position.

    ``tick_value / tick_size`` is the account-currency value of a 1.0 price move
    for one lot, so multiplying by price yields the position's notional exposure
    in account currency (currency conversion is already baked into tick_value).
    Returns 0.0 when the inputs are insufficient.
    """
    price = float(price or 0.0)
    tick_value = float(tick_value or 0.0)
    tick_size = float(tick_size or 0.0)
    if price <= 0 or tick_value <= 0 or tick_size <= 0:
        return 0.0
    return price * tick_value / tick_size


def symbol_factor(
    spec: SymbolSpec,
    *,
    reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
    requested_lot: float = DEFAULT_REQUESTED_LOT,
    group: str = "",
    min_notional: float = 0.0,
) -> SymbolFactor | None:
    """Compute the notional-normalization factor for one symbol.

    Returns ``None`` when MT5 did not provide enough data (missing price or tick
    value); the caller then falls back to the group factor.

    ``min_notional`` (0 = off) floors the position notional before dividing, so a
    very cheap instrument cannot earn an enormous factor that would amplify noisy
    micro-profits past the net-profit gate (e.g. a $2.71 share priced at 1-share
    lots). It caps the factor at ``reference_notional / min_notional``.
    """
    per_lot = notional_per_lot(spec.price, spec.tick_value, spec.tick_size)
    if per_lot <= 0:
        return None
    lot_used = clamp_lot(requested_lot, spec.volume_min, spec.volume_step, spec.volume_max)
    actual_notional = lot_used * per_lot
    if actual_notional <= 0:
        return None
    effective_notional = max(actual_notional, float(min_notional or 0.0))
    factor = reference_notional / effective_notional
    factor = max(_MIN_FACTOR, min(_MAX_FACTOR, factor))
    return SymbolFactor(
        name=spec.name,
        factor=round(factor, 6),
        lot_used=lot_used,
        notional_per_lot=round(per_lot, 4),
        actual_notional=round(actual_notional, 4),
        price=float(spec.price or 0.0),
        group=group,
    )


def compute_symbol_factors(
    specs: list[SymbolSpec],
    group_by_symbol: dict[str, str],
    *,
    reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
    requested_lot: float = DEFAULT_REQUESTED_LOT,
    min_notional: float = 0.0,
) -> tuple[list[SymbolFactor], list[str]]:
    """Compute factors for every spec. Returns (factors, skipped_symbol_names)."""
    factors: list[SymbolFactor] = []
    skipped: list[str] = []
    for spec in specs:
        group = group_by_symbol.get(spec.name.upper(), "")
        result = symbol_factor(
            spec,
            reference_notional=reference_notional,
            requested_lot=requested_lot,
            group=group,
            min_notional=min_notional,
        )
        if result is None:
            skipped.append(spec.name)
        else:
            factors.append(result)
    return factors, skipped


def _group_median_factors(factors: list[SymbolFactor]) -> dict[str, float]:
    by_group: dict[str, list[float]] = {}
    for item in factors:
        if item.group:
            by_group.setdefault(item.group, []).append(item.factor)
    return {
        group: round(statistics.median(values), 6)
        for group, values in sorted(by_group.items())
        if values
    }


def build_normalization_config(
    factors: list[SymbolFactor],
    *,
    broker: str = "AXI",
    reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
    requested_lot: float = DEFAULT_REQUESTED_LOT,
    min_notional: float = 0.0,
    account_currency: str = "",
    server: str = "",
    generated_utc: str = "",
    skipped_symbols: list[str] | None = None,
) -> dict:
    """Assemble a broker normalization JSON payload from computed factors.

    Per-symbol factors take precedence in ``net_profit_normalization``; the
    per-group median is written as a fallback for symbols MT5 could not measure.
    The legacy ``group_suffix``/``symbol_suffix`` maps are cleared because a
    measured per-symbol factor supersedes those crude compensations.
    """
    symbol_factors = {
        item.name.upper(): item.factor
        for item in sorted(factors, key=lambda f: f.name.upper())
    }
    group_factors = _group_median_factors(factors)
    return {
        "broker": broker,
        "basis": f"{broker.lower()}_notional_normalization_ref{int(round(reference_notional))}",
        "generated_utc": generated_utc,
        "reference_notional": reference_notional,
        "requested_lot": requested_lot,
        "min_notional": min_notional,
        "account_currency": account_currency,
        "server": server,
        "symbol_count": len(symbol_factors),
        "skipped_symbols": sorted(skipped_symbols or []),
        "default_net_profit_factor": 1.0,
        "group_net_profit_factors": group_factors,
        "group_suffix_net_profit_factors": {},
        "symbol_suffix_net_profit_factors": {},
        "symbol_net_profit_factors": symbol_factors,
    }
