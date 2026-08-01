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

MT5 leaves ``trade_tick_value`` at 0 for instruments it cannot convert to the
deposit currency (every GBX-quoted UK share on AXI, plus whatever pair has no
conversion loaded at extraction time). Those symbols are reconstructed from
``price * contract_size * rate`` with the rate implied by the symbols MT5 *did*
convert, which is the same quantity by a different route. Anything still
unmeasurable keeps its previous factor instead of silently inheriting a group
number that was never measured for it.

The functions here are pure (no MT5 dependency) so they can be unit tested; the
CLI in ``tools/gen_axi_normalization.py`` feeds them live ``SymbolSpec`` rows.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, fields

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

# Currencies MT5 quotes in a fraction of a parent currency. GBX (pence) is the
# one that matters on AXI: every LSE share is quoted in it and none of them
# carries a converted tick value, so the rate has to come from GBP.
MINOR_CURRENCY_UNITS: dict[str, tuple[str, float]] = {
    "GBX": ("GBP", 100.0),
    "ZAC": ("ZAR", 100.0),
    "ILA": ("ILS", 100.0),
}

# Case-sensitive spellings of those minor units. Upper-casing blindly would turn
# "GBp" (pence) into "GBP" (pounds) and undervalue every LSE share by 100x.
_MINOR_CURRENCY_ALIASES = {"GBp": "GBX", "GBx": "GBX", "ZAc": "ZAC", "ILa": "ILA"}


def currency_key(raw: object) -> str:
    """Normalized currency code, keeping minor units distinct from their parent."""
    code = str(raw or "").strip()
    return _MINOR_CURRENCY_ALIASES.get(code, code.upper())


@dataclass(frozen=True)
class SymbolFactor:
    name: str
    factor: float
    lot_used: float
    notional_per_lot: float
    actual_notional: float
    price: float
    group: str = ""
    # "tick_value" when MT5 converted the tick itself, "contract_rate" when the
    # notional had to be rebuilt from contract size and an implied FX rate.
    source: str = "tick_value"


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


def specs_from_payload(data: object) -> list[SymbolSpec]:
    """Build ``SymbolSpec`` rows from any of the spec-dump shapes we have produced.

    Accepts a bare list of dicts (``dataclasses.asdict`` output), a
    ``{"symbols": [...]}`` envelope, or the ``{"symbols": {name: {...}}}`` map the
    terminal dump uses. Unknown keys are ignored so a richer dump (margin, group,
    leverage) still loads.
    """
    known = {field.name for field in fields(SymbolSpec)}

    def build(name: str, row: object) -> SymbolSpec | None:
        if not isinstance(row, dict):
            return None
        payload = {key: value for key, value in row.items() if key in known}
        payload["name"] = str(payload.get("name") or name or "").strip()
        if not payload["name"]:
            return None
        return SymbolSpec(**payload)

    if isinstance(data, dict):
        data = data.get("symbols", data)
    specs: list[SymbolSpec] = []
    if isinstance(data, dict):
        for name, row in data.items():
            spec = build(str(name), row)
            if spec is not None:
                specs.append(spec)
    elif isinstance(data, list):
        for row in data:
            spec = build(str(row.get("name", "")) if isinstance(row, dict) else "", row)
            if spec is not None:
                specs.append(spec)
    return specs


def implied_currency_rates(
    specs: list[SymbolSpec],
    *,
    account_currency: str = "",
) -> dict[str, float]:
    """Account-currency value of one unit of each symbol's profit currency.

    ``tick_value / (tick_size * contract_size)`` is exactly that rate: for a
    USD-quoted symbol on a USD account it comes out 1.0, for a EUR-quoted one it
    comes out EURUSD. Taking the median per currency keeps one bad symbol from
    poisoning the rate. Minor units (GBX) are derived from their parent because
    no GBX symbol ever carries a converted tick value.
    """
    samples: dict[str, list[float]] = {}
    for spec in specs:
        currency = currency_key(spec.currency_profit)
        tick_size = float(spec.tick_size or 0.0)
        tick_value = float(spec.tick_value or 0.0)
        contract_size = float(spec.contract_size or 0.0)
        if not currency or tick_size <= 0 or tick_value <= 0 or contract_size <= 0:
            continue
        rate = tick_value / (tick_size * contract_size)
        if rate > 0:
            samples.setdefault(currency, []).append(rate)
    rates = {currency: statistics.median(values) for currency, values in samples.items()}
    for minor, (parent, divisor) in MINOR_CURRENCY_UNITS.items():
        if minor not in rates and rates.get(parent):
            rates[minor] = rates[parent] / divisor
    account = currency_key(account_currency)
    if account:
        rates.setdefault(account, 1.0)
    return rates


def notional_per_lot(
    price: float,
    tick_value: float,
    tick_size: float,
    *,
    contract_size: float = 0.0,
    currency_rate: float = 0.0,
) -> float:
    """Account-currency notional value of one 1.0-lot position.

    ``tick_value / tick_size`` is the account-currency value of a 1.0 price move
    for one lot, so multiplying by price yields the position's notional exposure
    in account currency (currency conversion is already baked into tick_value).

    When MT5 reports no tick value the same quantity is rebuilt as
    ``price * contract_size * currency_rate``: algebraically identical, but it
    only needs a rate implied by the symbols MT5 did convert. Returns 0.0 when
    neither route has enough data.
    """
    price = float(price or 0.0)
    tick_value = float(tick_value or 0.0)
    tick_size = float(tick_size or 0.0)
    if price <= 0:
        return 0.0
    if tick_value > 0 and tick_size > 0:
        return price * tick_value / tick_size
    contract_size = float(contract_size or 0.0)
    currency_rate = float(currency_rate or 0.0)
    if contract_size > 0 and currency_rate > 0:
        return price * contract_size * currency_rate
    return 0.0


def min_lot_notional(spec: SymbolSpec, currency_rate: float) -> float:
    """Account-currency exposure of one position at the symbol's minimum volume."""
    per_lot = notional_per_lot(
        spec.price,
        spec.tick_value,
        spec.tick_size,
        contract_size=spec.contract_size,
        currency_rate=currency_rate,
    )
    volume_min = float(spec.volume_min or 0.0)
    if per_lot <= 0 or volume_min <= 0:
        return 0.0
    return per_lot * volume_min


def build_symbol_specs_payload(
    specs: list[SymbolSpec],
    *,
    account_currency: str = "",
    account_leverage: int | None = None,
    server: str = "",
    terminal: str = "",
    broker: str = "",
    generated_utc: str = "",
    group_by_symbol: dict[str, str] | None = None,
    missing_symbols: list[str] | None = None,
    previous: dict | None = None,
) -> dict:
    """Assemble the terminal spec dump the portfolio manager reads.

    Money fields are in the account currency, which is the whole point: a dump
    that multiplies the quoted price without converting reports a pence-quoted
    share at 74x its real exposure.

    Every field of a previous dump survives: values this extraction could not
    measure (``margin_min_lot`` above all, which the manager uses as its best
    margin source) are taken from ``previous`` instead of being written as 0, and
    symbols the extraction did not return at all are carried over untouched and
    listed in ``carried_symbols``. A snapshot taken while the terminal could not
    resolve a name must not delete that symbol's measurements.
    """
    rates = implied_currency_rates(specs, account_currency=account_currency)
    previous_symbols = {}
    if isinstance(previous, dict):
        raw = previous.get("symbols")
        if isinstance(raw, dict):
            previous_symbols = {str(name): row for name, row in raw.items() if isinstance(row, dict)}
    groups = {key.upper(): value for key, value in (group_by_symbol or {}).items()}

    symbols: dict[str, dict] = {}
    without_price: list[str] = []
    for spec in sorted(specs, key=lambda item: item.name.upper()):
        old = dict(previous_symbols.get(spec.name) or {})
        rate = rates.get(currency_key(spec.currency_profit), 0.0)
        margin = float(spec.margin_min_lot or 0.0)
        if margin <= 0:
            try:
                margin = float(old.get("margin_min_lot") or 0.0)
            except (TypeError, ValueError):
                margin = 0.0
        notional = min_lot_notional(spec, rate)
        row = dict(old)
        row.update(
            {
                "contract_size": float(spec.contract_size or 0.0),
                "currency_profit": str(spec.currency_profit or ""),
                "currency_rate": round(rate, 8) if rate > 0 else None,
                "digits": spec.digits,
                "margin_min_lot": round(margin, 2) if margin > 0 else None,
                "notional_min_lot": round(notional, 2) if notional > 0 else None,
                "observed_leverage": round(notional / margin, 6) if notional > 0 and margin > 0 else None,
                "price": float(spec.price or 0.0),
                "tick_size": float(spec.tick_size or 0.0),
                "tick_value": float(spec.tick_value or 0.0),
                "volume_max": float(spec.volume_max or 0.0),
                "volume_min": float(spec.volume_min or 0.0),
                "volume_step": float(spec.volume_step or 0.0),
            }
        )
        group = groups.get(spec.name.upper())
        if group:
            row["group"] = group
        symbols[spec.name] = row
        if float(spec.price or 0.0) <= 0:
            without_price.append(spec.name)

    carried = sorted(name for name in previous_symbols if name not in symbols)
    for name in carried:
        symbols[name] = dict(previous_symbols[name])

    payload = dict(previous or {})
    payload.update(
        {
            "broker": broker or str(payload.get("broker") or ""),
            "account_currency": account_currency,
            "account_leverage": account_leverage,
            "server": server,
            "terminal": terminal,
            "generated_utc": generated_utc,
            "symbol_count": len(symbols),
            "measured_symbol_count": len(symbols) - len(carried),
            "carried_symbols": carried,
            "missing_symbols": sorted(missing_symbols or []),
            "symbols_without_price": sorted(without_price),
            "notional_currency": account_currency,
            "notional_note": (
                "notional_min_lot and observed_leverage are expressed in the account currency; "
                "currency_rate is the account-currency value of one unit of currency_profit."
            ),
            "symbols": symbols,
        }
    )
    return payload


def symbol_factor(
    spec: SymbolSpec,
    *,
    reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
    requested_lot: float = DEFAULT_REQUESTED_LOT,
    group: str = "",
    min_notional: float = 0.0,
    currency_rate: float = 0.0,
) -> SymbolFactor | None:
    """Compute the notional-normalization factor for one symbol.

    Returns ``None`` when MT5 did not provide enough data (missing price or tick
    value); the caller then falls back to the group factor.

    ``min_notional`` (0 = off) floors the position notional before dividing, so a
    very cheap instrument cannot earn an enormous factor that would amplify noisy
    micro-profits past the net-profit gate (e.g. a $2.71 share priced at 1-share
    lots). It caps the factor at ``reference_notional / min_notional``.
    """
    per_lot = notional_per_lot(
        spec.price,
        spec.tick_value,
        spec.tick_size,
        contract_size=spec.contract_size,
        currency_rate=currency_rate,
    )
    if per_lot <= 0:
        return None
    source = (
        "tick_value"
        if float(spec.tick_value or 0.0) > 0 and float(spec.tick_size or 0.0) > 0
        else "contract_rate"
    )
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
        source=source,
    )


def compute_symbol_factors(
    specs: list[SymbolSpec],
    group_by_symbol: dict[str, str],
    *,
    reference_notional: float = DEFAULT_REFERENCE_NOTIONAL,
    requested_lot: float = DEFAULT_REQUESTED_LOT,
    min_notional: float = 0.0,
    currency_rates: dict[str, float] | None = None,
    account_currency: str = "",
) -> tuple[list[SymbolFactor], list[str]]:
    """Compute factors for every spec. Returns (factors, skipped_symbol_names).

    ``currency_rates`` defaults to the rates implied by ``specs`` themselves, so
    symbols MT5 could not convert are still measured from contract size.
    """
    rates = (
        dict(currency_rates)
        if currency_rates is not None
        else implied_currency_rates(specs, account_currency=account_currency)
    )
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
            currency_rate=rates.get(currency_key(spec.currency_profit), 0.0),
        )
        if result is None:
            skipped.append(spec.name)
        else:
            factors.append(result)
    return factors, skipped


def _group_fallback_factors(factors: list[SymbolFactor]) -> dict[str, float]:
    """Per-group fallback for symbols with no measurement of their own.

    Deliberately the *minimum* factor of the group, not the median. The median is
    what turned the unmeasured LSE shares into a 10.0 factor: that number is the
    median of hundreds of cheap US shares pinned at the amplification cap, and
    applying it to a GBP 29 share traded in 100-share lots inflated its net
    profit by up to 96x. With the minimum, an unmeasured symbol can only ever be
    understated, which costs a false reject instead of a false accept.
    """
    by_group: dict[str, list[float]] = {}
    for item in factors:
        if item.group:
            by_group.setdefault(item.group, []).append(item.factor)
    return {
        group: round(min(values), 6)
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
    previous_factors: dict[str, float] | None = None,
) -> dict:
    """Assemble a broker normalization JSON payload from computed factors.

    Per-symbol factors take precedence in ``net_profit_normalization``. Symbols
    this run could not measure keep the factor of the previous file
    (``carried_symbols``) rather than dropping to a group number, because a
    momentarily missing quote is not a reason to change how a symbol is scored.
    Only what has never been measured lands in ``skipped_symbols``; consumers
    should refuse to infer anything for those.

    The legacy ``group_suffix``/``symbol_suffix`` maps are cleared because a
    measured per-symbol factor supersedes those crude compensations.
    """
    symbol_factors = {
        item.name.upper(): item.factor
        for item in sorted(factors, key=lambda f: f.name.upper())
    }
    measured = set(symbol_factors)
    carried: list[str] = []
    for name, factor in sorted((previous_factors or {}).items()):
        key = str(name).upper()
        try:
            value = float(factor)
        except (TypeError, ValueError):
            continue
        if key in measured or value <= 0:
            continue
        symbol_factors[key] = value
        carried.append(key)

    still_skipped = sorted(
        name for name in (skipped_symbols or []) if str(name).upper() not in symbol_factors
    )
    reconstructed = sorted(
        item.name.upper() for item in factors if item.source == "contract_rate"
    )
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
        "measured_symbol_count": len(measured),
        "reconstructed_symbols": reconstructed,
        "carried_symbols": sorted(carried),
        "skipped_symbols": still_skipped,
        "group_factor_policy": "min_measured_factor",
        "default_net_profit_factor": 1.0,
        "group_net_profit_factors": _group_fallback_factors(factors),
        "group_suffix_net_profit_factors": {},
        "symbol_suffix_net_profit_factors": {},
        "symbol_net_profit_factors": dict(sorted(symbol_factors.items())),
    }
