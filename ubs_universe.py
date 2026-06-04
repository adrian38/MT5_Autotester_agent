from __future__ import annotations

import configparser
import json
from pathlib import Path

from run_tests import apply_symbol_map, normalize_set_symbol
from ubs_models import Seed


def disabled_symbols_path(base_dir: Path) -> Path:
    return base_dir / "outputs" / "ubs_disabled_symbols.json"


def load_disabled_symbols(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, dict):
        values = data.get("disabled") or []
    elif isinstance(data, list):
        values = data
    else:
        values = []
    return {str(value).strip().upper() for value in values if str(value).strip()}


def save_disabled_symbols(path: Path, symbols: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"disabled": sorted(symbols)}, indent=2), encoding="utf-8")


def load_asset_universe(
    path: Path,
    *,
    disabled_symbols: set[str] | None = None,
    include_disabled: bool = False,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if path.exists():
        parser.read(path, encoding="utf-8-sig")

    groups: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    disabled = {symbol.upper() for symbol in (disabled_symbols or set())}

    for section in parser.sections():
        if section == "CommonAliases":
            aliases = {key.upper(): value.strip() for key, value in parser[section].items()}
            continue
        symbols = [item.strip() for item in parser[section].get("symbols", "").split(",") if item.strip()]
        if not include_disabled:
            symbols = [symbol for symbol in symbols if symbol.upper() not in disabled]
        groups[section] = symbols
    return groups, aliases


def asset_rows_from_groups(groups: dict[str, list[str]], aliases: dict[str, str]) -> list[tuple[str, str, list[str]]]:
    reverse_aliases: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        reverse_aliases.setdefault(target.upper(), []).append(alias)

    rows: list[tuple[str, str, list[str]]] = []
    for group, symbols in groups.items():
        for symbol in symbols:
            rows.append((group, symbol, sorted(reverse_aliases.get(symbol.upper(), []))))
    return rows


def canonical_symbol(symbol: str, aliases: dict[str, str]) -> str:
    normalized = str(symbol or "").upper()
    return aliases.get(normalized, normalized).upper()


def seed_symbol_disabled(seed: Seed, disabled_symbols: set[str], symbol_map: dict[str, str]) -> bool:
    raw = normalize_set_symbol(seed.symbol)
    mapped = normalize_set_symbol(apply_symbol_map(seed.symbol, symbol_map))
    return raw in disabled_symbols or mapped in disabled_symbols
