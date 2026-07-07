from __future__ import annotations

import configparser
import importlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


GROUP_ORDER = (
    "Forex",
    "Metals",
    "Indices",
    "Energies",
    "Crypto",
    "Stocks",
    "Commodities",
    "Bonds",
    "Futures",
    "Other",
)

FOREX_BASES = (
    "AED",
    "AUD",
    "CAD",
    "CHF",
    "CNH",
    "CZK",
    "EUR",
    "GBP",
    "GBX",
    "HKD",
    "HUF",
    "ILS",
    "INR",
    "JPY",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "RON",
    "SEK",
    "SGD",
    "THB",
    "USD",
    "ZAR",
)
METAL_PREFIXES = ("XAU", "XAG", "XPT", "XPD", "GC", "SI")
CRYPTO_HINTS = (
    "BTC",
    "ETH",
    "XRP",
    "LTC",
    "BCH",
    "ADA",
    "DOGE",
    "SOL",
    "DOT",
    "BNB",
    "TRX",
    "USDT",
    "USD",
)
ENERGY_HINTS = ("OIL", "WTI", "BRENT", "XTI", "XBR", "XNG", "GAS")
INDEX_HINTS = (
    "US30",
    "US500",
    "USTEC",
    "NAS100",
    "NASDAQ",
    "DAX",
    "DE40",
    "JP225",
    "UK100",
    "AUS200",
    "STOXX",
    "VIX",
    "DXY",
)
COMMODITY_HINTS = ("COCOA", "COFFEE", "CORN", "COTTON", "SUGAR", "WHEAT", "SBEAN", "OJ_")
BOND_HINTS = ("BOND", "BND", "B10Y", "BUND")


@dataclass(frozen=True)
class ExtractedSymbol:
    name: str
    path: str = ""
    visible: bool = False
    trade_mode: int | None = None


@dataclass(frozen=True)
class SymbolExtractionResult:
    symbols: tuple[ExtractedSymbol, ...]
    terminal_path: Path | None
    account_login: int | None
    server: str


@dataclass(frozen=True)
class AssetUniverseSyncResult:
    backup_path: Path | None
    counts: dict[str, int]
    added_symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]


class MT5SymbolExtractionError(RuntimeError):
    pass


def _mt5_module():
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise MT5SymbolExtractionError(
            "Falta el paquete opcional MetaTrader5. Instala con: pip install MetaTrader5"
        ) from exc


def extract_symbols_from_mt5(
    *,
    terminal_path: Path | None = None,
    login: int | None = None,
    password: str = "",
    server: str = "",
    timeout_ms: int = 60000,
) -> SymbolExtractionResult:
    mt5 = _mt5_module()
    init_kwargs: dict[str, object] = {"timeout": timeout_ms}
    if terminal_path:
        init_kwargs["path"] = str(terminal_path)

    if not mt5.initialize(**init_kwargs):
        code, message = mt5.last_error()
        raise MT5SymbolExtractionError(f"No se pudo inicializar MT5: {code} {message}")

    try:
        if login is not None:
            login_kwargs: dict[str, object] = {"login": login}
            if password:
                login_kwargs["password"] = password
            if server:
                login_kwargs["server"] = server
            if not mt5.login(**login_kwargs):
                code, message = mt5.last_error()
                raise MT5SymbolExtractionError(f"No se pudo iniciar sesion MT5: {code} {message}")

        account = mt5.account_info()
        raw_symbols = mt5.symbols_get()
        if raw_symbols is None:
            code, message = mt5.last_error()
            raise MT5SymbolExtractionError(f"No se pudieron leer simbolos MT5: {code} {message}")

        extracted: list[ExtractedSymbol] = []
        for item in raw_symbols:
            name = str(getattr(item, "name", "") or "").strip()
            if not name:
                continue
            extracted.append(
                ExtractedSymbol(
                    name=name,
                    path=str(getattr(item, "path", "") or ""),
                    visible=bool(getattr(item, "visible", False)),
                    trade_mode=getattr(item, "trade_mode", None),
                )
            )
        extracted.sort(key=lambda symbol: symbol.name.upper())
        return SymbolExtractionResult(
            symbols=tuple(extracted),
            terminal_path=terminal_path,
            account_login=int(account.login) if account is not None and getattr(account, "login", None) else None,
            server=str(getattr(account, "server", "") or server or ""),
        )
    finally:
        mt5.shutdown()


def classify_symbol_group(symbol: ExtractedSymbol) -> str:
    name = symbol.name.upper()
    base_name = _classification_symbol_base(name)
    path = symbol.path.upper()
    combined = f"{path}\\{name}"

    if path.startswith("AXISELECT_STANDARD_FX\\"):
        return "Forex"
    if path.startswith("AXISELECT_STANDARD_METALS\\"):
        return "Metals"
    if path.startswith("AXISELECT_CASH\\CASH_INDICES"):
        return "Indices"
    if path.startswith("AXISELECT_CASH\\CASH_OIL"):
        return "Energies"
    if path.startswith("AXISELECT_CRYPTO\\"):
        return "Crypto"
    if path.startswith("FUTURES\\FUT_INDICES"):
        return "Indices"
    if path.startswith("FUTURES\\FUT_COMMODITY"):
        return "Energies" if any(token in base_name for token in ENERGY_HINTS) else "Commodities"
    if path.startswith("SHARES_COMMFREE\\"):
        return "Stocks"

    if any(token in combined for token in ("METAL", "PRECIOUS")) or name.startswith(METAL_PREFIXES):
        return "Metals"
    if "FOREX" in path or _looks_like_forex_pair(base_name):
        return "Forex"
    if "CRYPTO" in path or ("USD" in base_name and any(token in base_name for token in CRYPTO_HINTS if token != "USD")):
        return "Crypto"
    if any(token in combined for token in ("INDEX", "INDICES")) or any(token in name for token in INDEX_HINTS):
        return "Indices"
    if any(token in combined for token in ("ENERG", "OIL")) or any(token in name for token in ENERGY_HINTS):
        return "Energies"
    if any(token in combined for token in ("COMMOD", "AGRICULT")) or any(token in name for token in COMMODITY_HINTS):
        return "Commodities"
    if any(token in combined for token in ("BOND", "TREASUR")) or any(token in name for token in BOND_HINTS):
        return "Bonds"
    if any(token in combined for token in ("FUTURE", "FUTURES")):
        return "Futures"
    if any(token in combined for token in ("STOCK", "SHARE", "ETF", "EQUIT")):
        return "Stocks"
    if re.search(r"\.(NAS|NYSE|US|TSE|AMS|ETR|MAD|PAR|LSE|SWX|IT|IE)(?:-24)?$", name):
        return "Stocks"
    return "Other"


def _classification_symbol_base(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    for suffix in (".SA", ".FS", "+"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _looks_like_forex_pair(symbol: str) -> bool:
    if len(symbol) != 6:
        return False
    return symbol[:3] in FOREX_BASES and symbol[3:] in FOREX_BASES


def group_symbols_for_universe(symbols: Iterable[ExtractedSymbol]) -> dict[str, list[str]]:
    grouped = {group: [] for group in GROUP_ORDER}
    seen: set[str] = set()
    for symbol in symbols:
        name = symbol.name.strip()
        key = name.upper()
        if not name or key in seen:
            continue
        seen.add(key)
        grouped.setdefault(classify_symbol_group(symbol), []).append(name)
    return {group: sorted(values, key=str.upper) for group, values in grouped.items() if values}


def _symbol_key(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _load_existing_asset_universe(path: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    if not path.exists():
        return {}, {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8-sig")
    groups: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    for section in parser.sections():
        if section == "CommonAliases":
            aliases = {key: value for key, value in parser[section].items() if str(value).strip()}
            continue
        groups[section] = [
            item.strip()
            for item in parser[section].get("symbols", "").split(",")
            if item.strip()
        ]
    return groups, aliases


def _dedupe_sorted(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for symbol in symbols:
        key = _symbol_key(symbol)
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(str(symbol).strip())
    return sorted(values, key=str.upper)


def sync_asset_universe_groups(
    existing_groups: dict[str, list[str]],
    symbols: Iterable[ExtractedSymbol],
    *,
    preserve_existing_groups: bool = True,
) -> tuple[dict[str, list[str]], tuple[str, ...], tuple[str, ...]]:
    extracted_by_key: dict[str, ExtractedSymbol] = {}
    for symbol in symbols:
        key = _symbol_key(symbol.name)
        if key and key not in extracted_by_key:
            extracted_by_key[key] = symbol

    existing_by_key: dict[str, str] = {}
    for current_symbols in existing_groups.values():
        for current in current_symbols:
            key = _symbol_key(current)
            if key and key not in existing_by_key:
                existing_by_key[key] = current

    if not existing_groups:
        groups = group_symbols_for_universe(extracted_by_key.values())
        added = tuple(sorted((symbol.name for symbol in extracted_by_key.values()), key=str.upper))
        return groups, added, ()

    if not preserve_existing_groups:
        groups = group_symbols_for_universe(extracted_by_key.values())
        added = tuple(
            sorted(
                (symbol.name for key, symbol in extracted_by_key.items() if key not in existing_by_key),
                key=str.upper,
            )
        )
        removed = tuple(
            sorted(
                (symbol for key, symbol in existing_by_key.items() if key not in extracted_by_key),
                key=str.upper,
            )
        )
        return groups, added, removed

    synced_groups: dict[str, list[str]] = {group: [] for group in existing_groups}
    kept_keys: set[str] = set()
    removed: list[str] = []

    for group, current_symbols in existing_groups.items():
        for current in current_symbols:
            key = _symbol_key(current)
            extracted = extracted_by_key.get(key)
            if extracted is None:
                removed.append(current)
                continue
            synced_groups.setdefault(group, []).append(extracted.name)
            kept_keys.add(key)

    added: list[str] = []
    for key, symbol in extracted_by_key.items():
        if key in kept_keys:
            continue
        group = classify_symbol_group(symbol)
        synced_groups.setdefault(group, []).append(symbol.name)
        added.append(symbol.name)

    synced_groups = {
        group: _dedupe_sorted(values)
        for group, values in synced_groups.items()
        if values
    }
    return (
        synced_groups,
        tuple(sorted(added, key=str.upper)),
        tuple(sorted(removed, key=str.upper)),
    )


def write_asset_universe_from_symbols(
    path: Path,
    symbols: Iterable[ExtractedSymbol],
    *,
    backup: bool = True,
    preserve_existing_groups: bool = True,
) -> AssetUniverseSyncResult:
    existing_groups, aliases = _load_existing_asset_universe(path)
    groups, added_symbols, removed_symbols = sync_asset_universe_groups(
        existing_groups,
        symbols,
        preserve_existing_groups=preserve_existing_groups,
    )
    backup_path: Path | None = None
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
        backup_path.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    ordered_groups = list(GROUP_ORDER)
    ordered_groups.extend(sorted(group for group in groups if group not in set(GROUP_ORDER)))
    for group in ordered_groups:
        values = groups.get(group)
        if not values:
            continue
        parser[group] = {"symbols": ",".join(values)}
    if aliases:
        parser["CommonAliases"] = aliases

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        parser.write(file, space_around_delimiters=False)

    counts = {group: len(values) for group, values in groups.items()}
    return AssetUniverseSyncResult(
        backup_path=backup_path,
        counts=counts,
        added_symbols=added_symbols,
        removed_symbols=removed_symbols,
    )
