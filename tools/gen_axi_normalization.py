"""Generate a broker net-profit normalization file from live MT5 contract specs.

Backtests run at a forced 0.01 lot, but each broker clamps orders up to the
symbol's minimum volume, so a flat per-group factor cannot fairly compare, say,
a 1.0-lot share CFD against a 0.01-lot forex pair. This tool reads the real
contract value of every symbol in the broker's asset universe from a running
MT5 terminal and writes per-symbol factors that normalize net profit onto a
common notional exposure (see ubs/normalization_gen.py).

Usage (dry-run, prints old-vs-new comparison, writes nothing):

    py tools/gen_axi_normalization.py --broker AXI --account-type STANDARD

Attach to an already-open+logged-in terminal by passing its path, or provide
credentials to log in:

    py tools/gen_axi_normalization.py --terminal-path "C:/.../terminal64.exe"
    py tools/gen_axi_normalization.py --login 123 --server AxiCorp-Live --password ***

Write the file (after reviewing the dry-run) and keep a timestamped backup:

    py tools/gen_axi_normalization.py --broker AXI --write

Offline compute from previously dumped specs (no MT5 needed):

    py tools/gen_axi_normalization.py --specs-json specs.json --write
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import broker_asset_universe_path, normalize_account_type, normalize_broker
from ubs.mt5_symbol_extract import SymbolSpec
from ubs.normalization import net_profit_normalization
from ubs.normalization_gen import (
    DEFAULT_REFERENCE_NOTIONAL,
    DEFAULT_REQUESTED_LOT,
    build_normalization_config,
    compute_symbol_factors,
)
from ubs.universe import load_asset_universe

WATCH_SYMBOLS = [
    "EURUSD.sa", "CHFJPY.sa", "XAUUSD.sa", "XAGUSD.sa", "WTI.fs", "S&P.fs", "NAS100.fs",
    "Costco+", "Meta+", "Exxon+", "Shopify+", "DocuSign+", "Aurora+", "CAT+", "Trip.com+", "Apple+",
]


def _load_universe(broker: str) -> tuple[list[str], dict[str, str]]:
    path = broker_asset_universe_path(BASE_DIR, broker)
    groups, _aliases = load_asset_universe(path, include_disabled=True)
    symbols: list[str] = []
    group_by_symbol: dict[str, str] = {}
    for group, group_symbols in groups.items():
        for symbol in group_symbols:
            symbols.append(symbol)
            group_by_symbol[symbol.upper()] = group
    return symbols, group_by_symbol


def _specs_from_json(path: Path) -> list[SymbolSpec]:
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = {f.name for f in dataclasses.fields(SymbolSpec)}
    return [SymbolSpec(**{k: v for k, v in row.items() if k in fields}) for row in data]


def _extract_specs_from_mt5(args: argparse.Namespace, symbols: list[str]) -> tuple[list[SymbolSpec], str, str, list[str]]:
    from ubs.mt5_symbol_extract import extract_symbol_specs_from_mt5

    terminal_path = Path(args.terminal_path).expanduser() if args.terminal_path else None
    result = extract_symbol_specs_from_mt5(
        symbols,
        terminal_path=terminal_path,
        login=args.login,
        password=args.password or "",
        server=args.server or "",
    )
    return list(result.specs), result.account_currency, result.server, list(result.missing_symbols)


def _fmt(value: float) -> str:
    return f"{value:.4f}" if value < 100 else f"{value:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate broker notional normalization factors from MT5.")
    parser.add_argument("--broker", default="AXI")
    parser.add_argument("--account-type", default="STANDARD")
    parser.add_argument("--terminal-path", default="")
    parser.add_argument("--login", type=int, default=None)
    parser.add_argument("--server", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--reference-notional", type=float, default=DEFAULT_REFERENCE_NOTIONAL)
    parser.add_argument("--requested-lot", type=float, default=DEFAULT_REQUESTED_LOT)
    parser.add_argument(
        "--min-notional-fraction",
        type=float,
        default=0.1,
        help="General amplification cap as a fraction of reference_notional (default 0.1). Floors "
        "any position notional at reference*fraction, so no symbol's net is scaled more than "
        "1/fraction (10x by default). Auto-scales across brokers/currencies. Set 0 to disable.",
    )
    parser.add_argument(
        "--min-notional",
        type=float,
        default=-1.0,
        help="Absolute notional floor override. When >=0 it overrides --min-notional-fraction.",
    )
    parser.add_argument("--specs-json", default="", help="Load specs from this JSON file instead of MT5.")
    parser.add_argument("--dump-specs", default="", help="Write extracted specs to this JSON file for reuse.")
    parser.add_argument("--out", default="", help="Output path (default assets/<broker>_normalization.json).")
    parser.add_argument("--write", action="store_true", help="Write the file (default is dry-run).")
    args = parser.parse_args()

    broker = normalize_broker(args.broker)
    normalize_account_type(args.account_type, broker)  # validate
    # General amplification cap: floor = reference * fraction unless an absolute override is given.
    min_notional = (
        args.min_notional
        if args.min_notional >= 0
        else max(0.0, args.reference_notional * args.min_notional_fraction)
    )
    symbols, group_by_symbol = _load_universe(broker)
    print(f"Universe [{broker}]: {len(symbols)} symbols across {len(set(group_by_symbol.values()))} groups")
    cap = f"{args.reference_notional / min_notional:g}x" if min_notional > 0 else "off"
    print(f"Reference notional={args.reference_notional:g} | min_notional floor={min_notional:g} (amplification cap {cap})")

    account_currency = ""
    server = ""
    missing: list[str] = []
    if args.specs_json:
        specs = _specs_from_json(Path(args.specs_json))
        print(f"Loaded {len(specs)} specs from {args.specs_json}")
    else:
        try:
            specs, account_currency, server, missing = _extract_specs_from_mt5(args, symbols)
        except Exception as exc:  # noqa: BLE001 - surface any MT5 error clearly
            print(f"ERROR reading MT5 specs: {exc}", file=sys.stderr)
            print("Open the AXI terminal (logged in) and retry, or pass --specs-json.", file=sys.stderr)
            return 2
        print(f"MT5 specs read: {len(specs)} symbols | account={account_currency} server={server} | missing={len(missing)}")

    if args.dump_specs:
        Path(args.dump_specs).write_text(
            json.dumps([dataclasses.asdict(s) for s in specs], ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        print(f"Dumped specs -> {args.dump_specs}")

    factors, skipped = compute_symbol_factors(
        specs,
        group_by_symbol,
        reference_notional=args.reference_notional,
        requested_lot=args.requested_lot,
        min_notional=min_notional,
    )
    skipped = sorted(set(skipped) | set(missing))
    print(f"Computed {len(factors)} factors | skipped/unmeasured {len(skipped)}")

    config = build_normalization_config(
        factors,
        broker=broker,
        reference_notional=args.reference_notional,
        requested_lot=args.requested_lot,
        min_notional=min_notional,
        account_currency=account_currency,
        server=server,
        generated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        skipped_symbols=skipped,
    )

    # --- comparison against the current file (old effective factor per symbol) ---
    print("\nPer-group factor (median): OLD-effective -> NEW")
    new_by_group: dict[str, list[float]] = {}
    old_by_group: dict[str, list[float]] = {}
    for item in factors:
        old = net_profit_normalization(item.name, broker=broker, base_dir=BASE_DIR)[0]
        new_by_group.setdefault(item.group or "?", []).append(item.factor)
        old_by_group.setdefault(item.group or "?", []).append(old)
    import statistics
    for group in sorted(new_by_group):
        oldm = statistics.median(old_by_group[group])
        newm = statistics.median(new_by_group[group])
        print(f"  {group:14s} old~{_fmt(oldm):>10s}  new~{_fmt(newm):>10s}  (n={len(new_by_group[group])})")

    print("\nWatch symbols: price   OLD factor -> NEW factor   (lot, notional)")
    by_name = {f.name.upper(): f for f in factors}
    for watch in WATCH_SYMBOLS:
        f = by_name.get(watch.upper())
        if not f:
            print(f"  {watch:14s} (not measured)")
            continue
        old = net_profit_normalization(f.name, broker=broker, base_dir=BASE_DIR)[0]
        print(
            f"  {f.name:14s} {f.price:9.2f}  {_fmt(old):>9s} -> {_fmt(f.factor):<9s}"
            f"  (lot={f.lot_used:g}, notional={f.actual_notional:.0f})"
        )

    out_path = Path(args.out) if args.out else (BASE_DIR / "assets" / f"{broker.lower()}_normalization.json")
    payload = json.dumps(config, ensure_ascii=True, indent=2)
    if not args.write:
        print(f"\n[DRY-RUN] Would write {out_path} ({config['symbol_count']} symbol factors). Re-run with --write.")
        return 0

    if out_path.exists():
        backup = out_path.with_suffix(out_path.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
        backup.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backup -> {backup}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    print(f"Wrote {out_path} ({config['symbol_count']} symbol factors)")
    print("Next: re-score existing results without MT5:")
    print(f"  py ubs_agent.py --broker {broker} --account-type {args.account_type} --rescore-candidates-only")
    print(f"  py ubs_agent.py --broker {broker} --account-type {args.account_type} --rescore-seeds-only")
    print(f"  py ubs_agent.py --broker {broker} --account-type {args.account_type} --rescore-robustness-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
