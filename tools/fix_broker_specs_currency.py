"""Express a broker spec dump (and its leverage caps) in the account currency.

``assets/<broker>_symbol_specs.json`` is a raw terminal dump. Two of its derived
fields are computed from the quoted price without converting the symbol's profit
currency, so every non-USD instrument comes out wrong on a USD account:

    notional_min_lot   price * contract_size * volume_min   <- in the QUOTE currency
    observed_leverage  notional_min_lot / margin_min_lot    <- inherits the error

3iGroup+ is quoted in pence, so its dump says a 100-share position is worth
290,593 when it is 3,898 USD (a 74x error); AirArabia+ (AED) reports 1:73.88 of
leverage where the product cap is 1:20. The measured fields the manager actually
reads for margin (``margin_min_lot``, ``volume_min``, ``contract_size``) are
already in the account currency and are left untouched.

``assets/<broker>_max_product_leverage.json`` matters more, because the manager
does consume it: entries whose ``origin`` starts with ``terminal`` were derived
from that same uncorrected ``observed_leverage``. This tool recomputes them and
leaves the ``schedule:*`` entries (taken from the broker's published product
schedule) exactly as they are.

Dry-run by default; ``--write`` backs both files up first. Idempotent: re-run it
after every fresh dump.

    py tools/fix_broker_specs_currency.py --broker AXI
    py tools/fix_broker_specs_currency.py --broker AXI --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import normalize_broker
from ubs.normalization_gen import currency_key, implied_currency_rates, specs_from_payload

# Rounding used by the dump/leverage files.
_MONEY_DIGITS = 2
_RATE_DIGITS = 8


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, payload: str) -> None:
    """Replace the file in one step: the manager may read it at any moment."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def _backup(path: Path) -> Path:
    backup = path.with_suffix(path.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    return backup


def _number(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fix_specs(data: dict, rates: dict[str, float]) -> tuple[dict, list[tuple[str, float, float]]]:
    """Rewrite the derived fields of a spec dump in the account currency."""
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        return data, []
    changes: list[tuple[str, float, float]] = []
    for name, spec in symbols.items():
        if not isinstance(spec, dict):
            continue
        rate = rates.get(currency_key(spec.get("currency_profit")), 0.0)
        price = _number(spec.get("price"))
        contract = _number(spec.get("contract_size"))
        volume_min = _number(spec.get("volume_min"))
        if rate <= 0 or price <= 0 or contract <= 0 or volume_min <= 0:
            # Nothing measurable: drop the derived fields instead of leaving the
            # uncorrected ones behind for someone to trust. margin_min_lot is a
            # real measurement and stays.
            spec["currency_rate"] = None
            spec["notional_min_lot"] = None
            spec["observed_leverage"] = None
            continue
        previous = _number(spec.get("notional_min_lot"))
        notional = round(price * contract * volume_min * rate, _MONEY_DIGITS)
        spec["currency_rate"] = round(rate, _RATE_DIGITS)
        spec["notional_min_lot"] = notional
        margin = _number(spec.get("margin_min_lot"))
        spec["observed_leverage"] = round(notional / margin, 6) if margin > 0 else None
        if previous > 0 and abs(previous - notional) > 0.01:
            changes.append((str(name), previous, notional))
    data["symbols"] = symbols
    data["notional_currency"] = str(data.get("account_currency") or "")
    data["notional_note"] = (
        "notional_min_lot and observed_leverage are expressed in the account currency; "
        "currency_rate is the account-currency value of one unit of currency_profit."
    )
    data["currency_corrected_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data, changes


def fix_leverage(
    data: dict,
    specs: dict,
    *,
    account_leverage: float = 0.0,
) -> tuple[dict, list[tuple[str, float, float]], int, int]:
    """Recompute the terminal-measured caps from the corrected notional.

    A measured cap can never exceed the leverage the account had when the margin
    was taken (the margin is `notional / min(account, cap)`), so anything above it
    is snapshot noise -- price and margin were not read at the same instant. It is
    clamped, because a cap that is too high asks for less margin than the broker
    will.
    """
    caps = data.get("max_product_leverage")
    origins = data.get("origin")
    if not isinstance(caps, dict) or not isinstance(origins, dict):
        return data, [], 0, 0
    by_key = {str(name).upper(): spec for name, spec in specs.items() if isinstance(spec, dict)}
    changes: list[tuple[str, float, float]] = []
    kept = 0
    clamped = 0
    for name in list(caps):
        key = str(name).upper()
        origin = str(origins.get(name) or origins.get(key) or "")
        if not origin.startswith("terminal"):
            kept += 1
            continue
        spec = by_key.get(key)
        if not spec:
            continue
        margin = _number(spec.get("margin_min_lot"))
        notional = _number(spec.get("notional_min_lot"))
        if margin <= 0 or notional <= 0:
            continue
        previous = _number(caps.get(name))
        corrected = round(notional / margin, 2)
        if corrected <= 0:
            continue
        if account_leverage > 0 and corrected > account_leverage:
            corrected = round(account_leverage, 2)
            clamped += 1
        caps[name] = corrected
        for origin_key in (name, key):
            if origin_key in origins:
                origins[origin_key] = "terminal:observado_cuenta"
        if previous > 0 and abs(previous - corrected) > 0.01:
            changes.append((str(name), previous, corrected))
    data["max_product_leverage"] = caps
    data["origin"] = origins
    data["currency_corrected_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data, changes, kept, clamped


def main() -> int:
    parser = argparse.ArgumentParser(description="Express a broker spec dump in the account currency.")
    parser.add_argument("--broker", default="AXI")
    parser.add_argument("--specs", default="")
    parser.add_argument("--leverage", default="")
    parser.add_argument("--write", action="store_true", help="Write the files (default is dry-run).")
    args = parser.parse_args()

    broker = normalize_broker(args.broker)
    specs_path = Path(args.specs) if args.specs else BASE_DIR / "assets" / f"{broker.lower()}_symbol_specs.json"
    leverage_path = (
        Path(args.leverage) if args.leverage else BASE_DIR / "assets" / f"{broker.lower()}_max_product_leverage.json"
    )
    if not specs_path.is_file():
        print(f"ERROR: no spec dump at {specs_path}", file=sys.stderr)
        return 2

    specs_doc = _load(specs_path)
    account_currency = str(specs_doc.get("account_currency") or "")
    rates = implied_currency_rates(specs_from_payload(specs_doc), account_currency=account_currency)
    print(f"{specs_path.name}: {len(specs_doc.get('symbols') or {})} symbols | account={account_currency or '?'}")
    print("Rates: " + ", ".join(f"{currency}={rate:g}" for currency, rate in sorted(rates.items())))

    specs_doc, spec_changes = fix_specs(specs_doc, rates)
    print(f"\nnotional_min_lot corrected for {len(spec_changes)} symbols")
    for name, old, new in sorted(spec_changes, key=lambda row: -abs(row[1] / max(row[2], 1e-9)))[:10]:
        print(f"  {name:18s} {old:14.2f} -> {new:12.2f}  ({old / new:6.2f}x)")

    leverage_changes: list[tuple[str, float, float]] = []
    if leverage_path.is_file():
        leverage_doc = _load(leverage_path)
        leverage_doc, leverage_changes, kept, clamped = fix_leverage(
            leverage_doc,
            specs_doc.get("symbols") or {},
            account_leverage=_number(specs_doc.get("account_leverage")),
        )
        raised = sum(1 for _n, old, new in leverage_changes if new > old)
        lowered = len(leverage_changes) - raised
        print(
            f"\n{leverage_path.name}: {len(leverage_changes)} measured caps corrected "
            f"({lowered} lowered = more margin, {raised} raised), {kept} schedule entries untouched, "
            f"{clamped} clamped to the account leverage"
        )
        for name, old, new in sorted(leverage_changes, key=lambda row: -abs(row[1] - row[2]))[:10]:
            print(f"  {name:18s} 1:{old:<8.2f} -> 1:{new:<8.2f}")
    else:
        leverage_doc = None
        print(f"\n{leverage_path.name}: not found, skipping")

    if not args.write:
        print("\n[DRY-RUN] nothing written. Re-run with --write.")
        return 0

    print(f"\nBackup -> {_backup(specs_path).name}")
    _write_atomic(specs_path, json.dumps(specs_doc, ensure_ascii=True, indent=2))
    print(f"Wrote {specs_path}")
    if leverage_doc is not None:
        print(f"Backup -> {_backup(leverage_path).name}")
        _write_atomic(leverage_path, json.dumps(leverage_doc, ensure_ascii=True, indent=2))
        print(f"Wrote {leverage_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
