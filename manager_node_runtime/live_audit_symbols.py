"""Simbolos de la auditoria, que cruza dos servidores del mismo broker.

La cuenta real y la del tester no tienen por que estar en el mismo servidor, y
cada servidor escribe los simbolos a su manera: ``XAUUSD`` en Axi-US50-Live y
``XAUUSD.sa`` en Axi-US51-Live.  El set se adapta a como los escribe el servidor
del tester —si no, MT5 aborta el backtest con «symbol ... not exist»— y las
comparaciones contra la cuenta real usan una clave comun a los dos servidores.
"""
from __future__ import annotations

import configparser
import re
from pathlib import Path

from run_tests_symbols import normalize_set_symbol


_UNSUFFIXED_AXI_SERVERS = frozenset({"axi-us50-live"})
_SET_SYMBOL_WITH_STANDARD_SUFFIX = re.compile(
    r"^(\s*\ufeff?(?:ForceSymbol|Symbol)\s*=\s*)([^\s|]+)\.sa(?=\s*(?:\|\||$))",
    flags=re.IGNORECASE | re.MULTILINE,
)
_SET_SYMBOL_PARAMETER = re.compile(
    r"^(\s*\ufeff?(?:ForceSymbol|Symbol)\s*=\s*)([^\s|]+)(?=\s*(?:\|\||$))",
    flags=re.IGNORECASE | re.MULTILINE,
)


def audit_symbol_key(symbol: object) -> str:
    """Clave de un simbolo estable entre los servidores de un mismo broker.

    `normalize_set_symbol` solo quita el sufijo de cuenta —minusculas y ajeno a
    los codigos de mercado—, asi que ``XAUUSD.sa`` y ``XAUUSD`` comparten clave
    mientras ``SIL.NYSE`` y ``SIL.US-24`` siguen siendo simbolos distintos.
    """
    return normalize_set_symbol(str(symbol or ""))


def broker_symbol_spellings(project: Path, broker: object) -> dict[str, str]:
    """Como escribe cada simbolo el servidor del broker, segun su universo."""
    path = Path(project) / "assets" / f"{str(broker or '').strip().lower()}_assets.ini"
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8-sig")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return {}
    spellings: dict[str, str] = {}
    ambiguous: set[str] = set()
    for section in parser.sections():
        if section == "CommonAliases":
            continue
        for item in parser[section].get("symbols", "").split(","):
            symbol = item.strip()
            key = audit_symbol_key(symbol)
            if not key:
                continue
            if spellings.setdefault(key, symbol) != symbol:
                ambiguous.add(key)
    for key in ambiguous:
        # Dos escrituras para la misma clave: no hay una correcta que elegir.
        spellings.pop(key, None)
    return spellings


def normalize_live_audit_set_symbols(
    text: str, tester_server: str, broker_symbols: dict[str, str] | None = None
) -> str:
    """Adapt symbol parameters in an audit copy to the selected MT5 server."""
    if tester_server.strip().casefold() in _UNSUFFIXED_AXI_SERVERS:
        return _SET_SYMBOL_WITH_STANDARD_SUFFIX.sub(r"\1\2", text)
    if not broker_symbols:
        return text

    def _spelling(match: re.Match[str]) -> str:
        symbol = match.group(2)
        return f"{match.group(1)}{broker_symbols.get(audit_symbol_key(symbol), symbol)}"

    return _SET_SYMBOL_PARAMETER.sub(_spelling, text)
