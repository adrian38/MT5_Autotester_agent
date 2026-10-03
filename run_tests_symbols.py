"""Simbolos, timeframes y lectura de los .set que alimentan al tester."""
from __future__ import annotations

import configparser
import re
from pathlib import Path

from ubs.set_utils import read_set_with_encoding


def _symbol_endswith(value: str, suffix: str) -> bool:
    return bool(suffix) and value.upper().endswith(suffix.upper())

def _symbol_has_explicit_suffix(symbol: str, suffixes: tuple[str, ...]) -> bool:
    if any(_symbol_endswith(symbol, suffix) for suffix in suffixes if suffix):
        return True
    if symbol.endswith("+"):
        return True
    return bool(re.search(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9-]+$", symbol))

class SymbolSuffixUniverse(dict[str, str]):
    """Suffix lookup with broker-exact symbol spellings for MT5 execution."""

    def __init__(self) -> None:
        super().__init__()
        self.exact_targets: dict[str, str] = {}

def load_symbol_suffix_universe(
    path: Path | None,
    symbol_suffix: str = "",
    futures_suffix: str = "",
    shares_suffix: str = "",
) -> dict[str, str]:
    suffixes = tuple(
        suffix.strip()
        for suffix in (shares_suffix, futures_suffix, symbol_suffix)
        if suffix and suffix.strip()
    )
    # Existing callers consume the dict values as suffixes.  Keep that contract
    # and attach the broker's complete spelling separately so MT5 execution can
    # also repair casing drift such as AXI ``APPLE+`` -> ``Apple+``.
    universe = SymbolSuffixUniverse()
    for source, target in load_symbol_suffix_target_map(path, symbol_suffix, futures_suffix, shares_suffix).items():
        for suffix in suffixes:
            if _symbol_endswith(target, suffix):
                universe[source] = target[-len(suffix):]
                universe.exact_targets[source] = target
                universe.exact_targets.setdefault(normalize_set_symbol(target), target)
                break
    return universe

def load_symbol_suffix_target_map(
    path: Path | None,
    symbol_suffix: str = "",
    futures_suffix: str = "",
    shares_suffix: str = "",
) -> dict[str, str]:
    suffixes = tuple(
        suffix.strip()
        for suffix in (shares_suffix, futures_suffix, symbol_suffix)
        if suffix and suffix.strip()
    )
    if not path or not suffixes or not path.exists():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8-sig")
    except configparser.Error:
        return {}
    mapping: dict[str, str] = {}
    for section in parser.sections():
        if section == "CommonAliases":
            continue
        raw_symbols = parser[section].get("symbols", "")
        for item in raw_symbols.split(","):
            listed = item.strip()
            if not listed:
                continue
            for suffix in suffixes:
                if _symbol_endswith(listed, suffix):
                    base = listed[: -len(suffix)]
                    if base:
                        mapping.setdefault(normalize_set_symbol(base), listed)
                    break
    return mapping

def apply_symbol_suffix(
    symbol: str,
    suffix: str,
    futures_suffix: str = "",
    shares_suffix: str = "",
    suffix_universe: dict[str, str] | None = None,
) -> str:
    symbol = symbol.strip()
    suffix = suffix.strip()
    futures_suffix = futures_suffix.strip()
    shares_suffix = shares_suffix.strip()
    suffixes = (suffix, futures_suffix, shares_suffix)
    if not symbol:
        return symbol
    universe = suffix_universe or {}
    exact_targets = getattr(suffix_universe, "exact_targets", {})
    universe_target = exact_targets.get(normalize_set_symbol(symbol), "")
    if not universe_target:
        for configured_suffix in suffixes:
            if _symbol_endswith(symbol, configured_suffix):
                base_symbol = symbol[: -len(configured_suffix)]
                universe_target = exact_targets.get(normalize_set_symbol(base_symbol), "")
                if universe_target:
                    break
    if universe_target:
        return universe_target
    if any(_symbol_endswith(symbol, configured_suffix) for configured_suffix in suffixes if configured_suffix):
        return symbol
    universe_suffix = universe.get(normalize_set_symbol(symbol), "")
    if universe_suffix:
        return f"{symbol}{universe_suffix}"
    if _symbol_has_explicit_suffix(symbol, suffixes):
        return symbol
    selected_suffix = suffix
    if not selected_suffix:
        return symbol
    return f"{symbol}{selected_suffix}"

def parse_symbol_map(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in re.split(r"[\n,;]+", value):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Correspondencia de simbolo invalida: {item}. Usa formato ORIGEN=DESTINO.")
        source, target = item.split("=", 1)
        source = normalize_set_symbol(source)
        target = target.strip()
        if not source or not target.strip():
            raise ValueError(f"Correspondencia de simbolo invalida: {item}. Usa formato ORIGEN=DESTINO.")
        mapping[source] = target
    return mapping

def apply_symbol_map(symbol: str, symbol_map: dict[str, str]) -> str:
    if _symbol_has_explicit_suffix(symbol.strip(), ()):
        return symbol.strip()
    base_symbol = normalize_set_symbol(symbol)
    return symbol_map.get(base_symbol, symbol.strip())

FOREX_SYMBOLS = {
    a + b
    for a in ("AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD")
    for b in ("AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD")
    if a != b
}

SYMBOL_ALIASES = (
    (".JP225Cash", re.compile(r"\.?JP225CASH|(?:^|[^A-Z0-9])JP225(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("XAUUSD", re.compile(r"XAUUSD|GOLD|GOLDTRADE|GOLDREAPER|GOLDBOT|PHANTOM", re.IGNORECASE)),
    ("XAGUSD", re.compile(r"XAGUSD|SILVER", re.IGNORECASE)),
    ("XAUEUR", re.compile(r"XAUEUR", re.IGNORECASE)),
    ("XTIUSD", re.compile(r"XTIUSD", re.IGNORECASE)),
    ("BTCUSD", re.compile(r"BTCUSD|BTC|BITCOIN", re.IGNORECASE)),
    ("US100", re.compile(r"US100|USTEC|NAS100|NASDAQ|NAS_", re.IGNORECASE)),
    ("US30", re.compile(r"US30|DOW", re.IGNORECASE)),
    ("US500", re.compile(r"US500|SP500|SPX", re.IGNORECASE)),
    ("DAX", re.compile(r"(?:^|[^A-Z0-9])DAX(?:[^A-Z0-9]|$)|GER40|GER30", re.IGNORECASE)),
    ("CRUDEOIL", re.compile(r"CRUDEOIL|CRUDE|USOIL|WTI|OIL", re.IGNORECASE)),
)

EXPLICIT_SYMBOLS = FOREX_SYMBOLS | {
    symbol for symbol, _pattern in SYMBOL_ALIASES
} | {
    "USTEC",
    "US100",
    "US500",
    "US30",
    "DE40",
    "DAX",
    "BRENT",
    "BTCUSD",
    "ETHUSD",
    "XRPUSD",
    "ADAUSD",
    "DOGEUSD",
}

TIMEFRAME_PATTERNS = (
    ("M1", re.compile(r"(?:^|[^A-Z0-9])M1(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("M5", re.compile(r"(?:^|[^A-Z0-9])M5(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("M15", re.compile(r"(?:^|[^A-Z0-9])M15(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("M30", re.compile(r"(?:^|[^A-Z0-9])M30(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("H1", re.compile(r"(?:^|[^A-Z0-9])H1(?:[^A-Z0-9]|$)|SCALPH1", re.IGNORECASE)),
    ("H2", re.compile(r"(?:^|[^A-Z0-9])H2(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("H3", re.compile(r"(?:^|[^A-Z0-9])H3(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("H4", re.compile(r"(?:^|[^A-Z0-9])H4(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("D1", re.compile(r"(?:^|[^A-Z0-9])D1(?:[^A-Z0-9]|$)|\(D\)|\(DAILY\)|DAILY|DAYTRADE|LONGTERM", re.IGNORECASE)),
    ("W1",  re.compile(r"(?:^|[^A-Z0-9])W1(?:[^A-Z0-9]|$)|WEEKLY", re.IGNORECASE)),
    ("MN", re.compile(r"(?:^|[^A-Z0-9])MN?(?:[^A-Z0-9]|$)|MONTHLY", re.IGNORECASE)),
)

# Valores de ENUM_TIMEFRAMES de MT5 que el proyecto sabe decodificar.
# MT5 codifica las horas como 16384 + horas (H1=16385 ... D1=16408).
# Declarado en orden de duracion: KNOWN_TIMEFRAMES hereda ese orden y es la
# lista canonica que consumen la UI, la seleccion y las validaciones. Anadir un
# timeframe aqui lo habilita en toda la aplicacion; NO lo mete por si solo en el
# universo de generacion (ver DEFAULT_TIMEFRAME_UNIVERSE / ubs_timeframes.json).
TIMEFRAME_ENUM = {
    "1": "M1",
    "5": "M5",
    "15": "M15",
    "30": "M30",
    "16385": "H1",
    "16386": "H2",
    "16387": "H3",
    "16388": "H4",
    "16408": "D1",
    "32769": "W1",
    "49153": "MN",
}

KNOWN_TIMEFRAMES = tuple(dict.fromkeys(TIMEFRAME_ENUM.values()))

def read_set_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

def load_set_params(path: Path) -> dict[str, str]:
    params: dict[str, str] = {}
    for line in read_set_text(path).splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.strip()] = value.split("||", 1)[0].strip()
    return params

EXCHANGE_SYMBOL_SUFFIXES = {
    "AMS",
    "ETR",
    "IE",
    "IT",
    "LSE",
    "MAD",
    "NAS",
    "NYSE",
    "PAR",
    "SWX",
    "TSE",
    "US",
}

def normalize_set_symbol(symbol: str) -> str:
    value = symbol.strip()
    match = re.search(r"(?<=[A-Za-z0-9])\.([A-Za-z0-9]+)$", value)
    if match:
        suffix = match.group(1)
        if suffix.upper() not in EXCHANGE_SYMBOL_SUFFIXES and suffix.islower():
            value = value[: match.start()]
    return value.upper()

def infer_symbol_from_set(set_file: Path, params: dict[str, str]) -> str:
    force_symbol = params.get("ForceSymbol", "").strip()
    if force_symbol:
        return force_symbol

    haystack = str(set_file).upper().replace("+", "_")
    for token in re.split(r"[^A-Z0-9]+", haystack):
        if token in EXPLICIT_SYMBOLS:
            return token
        match = re.search(r"(?:AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD){2}", token)
        if match and match.group(0) in FOREX_SYMBOLS:
            return match.group(0)

    for symbol, pattern in SYMBOL_ALIASES:
        if pattern.search(str(set_file)):
            return symbol
    return ""

def infer_period_from_set(set_file: Path, params: dict[str, str]) -> str:
    run_strategy = params.get("Run_Strategy", "").strip()
    strategy_timeframe_key = ""
    if run_strategy == "1":
        strategy_timeframe_key = "ST1_Timeframe"
    elif run_strategy == "2":
        strategy_timeframe_key = "VolTimeframe"
    elif run_strategy == "3":
        strategy_timeframe_key = "RNG_Timeframe"

    if strategy_timeframe_key:
        period = TIMEFRAME_ENUM.get(params.get(strategy_timeframe_key, ""))
        if period:
            return period

    text = str(set_file)
    for period, pattern in TIMEFRAME_PATTERNS:
        if pattern.search(text):
            return period

    comment = params.get("EA_Comment", "")
    for period, pattern in TIMEFRAME_PATTERNS:
        if comment and pattern.search(comment):
            return period

    # Low-confidence fallback, used only when names/comments give no clue.
    for key in (
        "ST1_Timeframe",
        "VolTimeframe",
        "RNG_Timeframe",
        "Entry_Timing",
        "ATR_Timeframe",
    ):
        value = params.get(key, "")
        period = TIMEFRAME_ENUM.get(value)
        if period:
            return period
    return ""

def infer_period_from_path(set_file: Path) -> str:
    periods = {period.upper(): period for period in TIMEFRAME_ENUM.values()}
    for part in (set_file.parent.name, set_file.stem):
        for token in re.split(r"[^A-Z0-9]+", part.upper()):
            period = periods.get(token)
            if period:
                return period
    text = str(set_file)
    for period, pattern in TIMEFRAME_PATTERNS:
        if pattern.search(text):
            return period
    return ""

def infer_tester_fields_from_set(set_file: Path | None) -> dict[str, str]:
    if not set_file:
        return {}
    params = load_set_params(set_file)
    inferred: dict[str, str] = {}
    symbol = infer_symbol_from_set(set_file, params)
    period = infer_period_from_set(set_file, params)
    if symbol:
        inferred["Symbol"] = symbol
    if period:
        inferred["Period"] = period
    return inferred

def validate_set_symbol(
    config: configparser.ConfigParser,
    set_file: Path | None,
    inferred_fields: dict[str, str],
    symbol_map: dict[str, str],
    symbol_suffix: str,
    futures_suffix: str = "",
    shares_suffix: str = "",
    suffix_universe: dict[str, str] | None = None,
) -> None:
    if not set_file:
        return

    inferred_symbol = inferred_fields.get("Symbol", "").strip()
    if not inferred_symbol:
        if config["Tester"].get("Symbol", "").strip():
            return
        raise ValueError(
            f"No pude inferir el Symbol desde el set {set_file.name} "
            "y el template no tiene Symbol."
        )

    expected_symbol = apply_symbol_suffix(
        apply_symbol_map(inferred_symbol, symbol_map),
        symbol_suffix,
        futures_suffix,
        shares_suffix,
        suffix_universe,
    )
    actual_symbol = config["Tester"].get("Symbol", "").strip()
    if actual_symbol.upper() != expected_symbol.upper():
        raise ValueError(
            f"Symbol no coincide para {set_file.name}: esperado {expected_symbol}, "
            f"pero el tester.ini quedo con {actual_symbol or '(vacio)'}."
        )
