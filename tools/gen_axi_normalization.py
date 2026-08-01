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

Offline compute from previously dumped specs (no MT5 needed). Any dump shape we
have produced is accepted, including the terminal dump in
``assets/<broker>_symbol_specs.json``:

    py tools/gen_axi_normalization.py --specs-json assets/axi_symbol_specs.json --write

Symbols this run cannot measure keep the factor of the file being replaced, so a
snapshot taken while a quote was missing never silently drops a symbol to a group
number. Pass --no-carry to opt out.
"""

from __future__ import annotations

import argparse
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
    build_symbol_specs_payload,
    compute_symbol_factors,
    implied_currency_rates,
    specs_from_payload,
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


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _specs_from_json(path: Path) -> tuple[list[SymbolSpec], str, str]:
    """Load a spec dump in any shape we have produced. Returns (specs, currency, server)."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    envelope = data if isinstance(data, dict) else {}
    return (
        specs_from_payload(data),
        str(envelope.get("account_currency") or ""),
        str(envelope.get("server") or ""),
    )


def _previous_factors(path: Path) -> dict[str, float]:
    """Per-symbol factors of the file we are about to replace."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    raw = data.get("symbol_net_profit_factors") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    factors: dict[str, float] = {}
    for name, value in raw.items():
        try:
            factor = float(value)
        except (TypeError, ValueError):
            continue
        if factor > 0:
            factors[str(name).upper()] = factor
    return factors


def _extract_specs_from_mt5(args: argparse.Namespace, symbols: list[str]):
    from ubs.mt5_symbol_extract import extract_symbol_specs_from_mt5

    terminal_path = Path(args.terminal_path).expanduser() if args.terminal_path else None
    return extract_symbol_specs_from_mt5(
        symbols,
        terminal_path=terminal_path,
        login=args.login,
        password=args.password or "",
        server=args.server or "",
    )


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
    parser.add_argument(
        "--dump-specs",
        default="",
        help="Write the live MT5 read to this JSON file (the shape the portfolio manager reads: "
        "margin per minimum position, minimum lot, contract size, account leverage). Merges with "
        "the existing file, so a partial read never drops measurements it could not take.",
    )
    parser.add_argument("--out", default="", help="Output path (default assets/<broker>_normalization.json).")
    parser.add_argument("--write", action="store_true", help="Write the file (default is dry-run).")
    parser.add_argument(
        "--previous",
        default="",
        help="File whose factors are carried for symbols this run cannot measure "
        "(default: the broker file currently in effect, regardless of --out).",
    )
    parser.add_argument(
        "--no-carry",
        action="store_true",
        help="Do not keep the previous factor of symbols this run could not measure "
        "(they then fall back to the conservative group factor).",
    )
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

    default_path = BASE_DIR / "assets" / f"{broker.lower()}_normalization.json"
    out_path = Path(args.out) if args.out else default_path
    # The file to carry from is the one in effect, not the output path: writing a
    # staging copy must not lose the factors of symbols measured last time.
    previous_path = Path(args.previous) if args.previous else default_path
    previous = {} if args.no_carry else _previous_factors(previous_path)

    account_currency = ""
    server = ""
    terminal = ""
    account_leverage: int | None = None
    missing: list[str] = []
    if args.specs_json:
        specs, account_currency, server = _specs_from_json(Path(args.specs_json))
        print(f"Loaded {len(specs)} specs from {args.specs_json} | account={account_currency} server={server}")
    else:
        try:
            extraction = _extract_specs_from_mt5(args, symbols)
        except Exception as exc:  # noqa: BLE001 - surface any MT5 error clearly
            print(f"ERROR reading MT5 specs: {exc}", file=sys.stderr)
            print("Open the AXI terminal (logged in) and retry, or pass --specs-json.", file=sys.stderr)
            return 2
        specs = list(extraction.specs)
        account_currency = extraction.account_currency
        server = extraction.server
        account_leverage = extraction.account_leverage
        terminal = str(extraction.terminal_path or "")
        missing = list(extraction.missing_symbols)
        measured_margin = sum(1 for spec in specs if spec.margin_min_lot > 0)
        print(
            f"MT5 specs read: {len(specs)} symbols | account={account_currency} leverage={account_leverage} "
            f"server={server} | margin measured for {measured_margin} | missing={len(missing)}"
        )

    if args.dump_specs:
        dump_path = Path(args.dump_specs)
        if args.specs_json:
            print(
                "ERROR: --dump-specs needs a live MT5 read; with --specs-json there is nothing new to dump.",
                file=sys.stderr,
            )
            return 2
        previous_dump = _load_json(dump_path)
        payload = build_symbol_specs_payload(
            specs,
            account_currency=account_currency,
            account_leverage=account_leverage,
            server=server,
            terminal=terminal,
            broker=broker,
            generated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            group_by_symbol=group_by_symbol,
            missing_symbols=missing,
            previous=previous_dump,
        )
        measured_now = {spec.name for spec in specs if spec.margin_min_lot > 0}
        kept_margins = sum(
            1
            for name, row in payload["symbols"].items()
            if row.get("margin_min_lot") and name not in measured_now
        )
        summary = (
            f"{payload['symbol_count']} symbols, {payload['measured_symbol_count']} read now, "
            f"{len(payload['carried_symbols'])} symbols and {kept_margins} margins carried from the previous dump"
        )
        if not args.write:
            print(f"[DRY-RUN] Would write {dump_path} ({summary}).")
        else:
            if dump_path.exists():
                backup = dump_path.with_suffix(dump_path.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
                backup.write_text(dump_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
                print(f"Backup -> {backup.name}")
            dump_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            print(f"Dumped specs -> {dump_path} ({summary})")

    rates = implied_currency_rates(specs, account_currency=account_currency)
    print(
        "Implied FX rates (1 unit of quote currency in account currency): "
        + ", ".join(f"{currency}={rate:g}" for currency, rate in sorted(rates.items()))
    )

    factors, skipped = compute_symbol_factors(
        specs,
        group_by_symbol,
        reference_notional=args.reference_notional,
        requested_lot=args.requested_lot,
        min_notional=min_notional,
        currency_rates=rates,
    )
    skipped = sorted(set(skipped) | set(missing))
    rebuilt = [item for item in factors if item.source == "contract_rate"]
    print(
        f"Computed {len(factors)} factors "
        f"({len(factors) - len(rebuilt)} from tick value, {len(rebuilt)} rebuilt from contract size) "
        f"| unmeasured {len(skipped)}"
    )
    if rebuilt:
        preview = ", ".join(f"{item.name}={item.factor:g}" for item in rebuilt[:6])
        print(f"  rebuilt e.g.: {preview}")

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
        previous_factors=previous,
    )
    if config["carried_symbols"]:
        print(
            f"Carried {len(config['carried_symbols'])} factors from the previous file "
            f"(unmeasurable now): {', '.join(config['carried_symbols'][:8])}"
        )
    if config["skipped_symbols"]:
        print(
            f"No factor at all for {len(config['skipped_symbols'])} symbols "
            f"(they use the conservative group minimum): {', '.join(config['skipped_symbols'][:8])}"
        )

    # --- comparison against the current file (old effective factor per symbol) ---
    print("\nPer-group median of the measured factors: OLD-effective -> NEW")
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
    print("Next: re-apply the new factors to the stored results (no MT5 needed):")
    print(f"  py tools/fast_rescore_from_metrics.py --broker {broker} --account-type {args.account_type} --dry-run")
    print(f"  py tools/fast_rescore_from_metrics.py --broker {broker} --account-type {args.account_type}")
    print(
        "  (ubs_agent.py --rescore-*-only recomputes gates but KEEPS the stored factor by design, "
        "so it will not pick a normalization change up)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
