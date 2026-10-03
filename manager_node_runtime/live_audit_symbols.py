from __future__ import annotations

import re


_UNSUFFIXED_AXI_SERVERS = frozenset({"axi-us50-live"})
_SET_SYMBOL_WITH_STANDARD_SUFFIX = re.compile(
    r"^(\s*\ufeff?(?:ForceSymbol|Symbol)\s*=\s*)([^\s|]+)\.sa(?=\s*(?:\|\||$))",
    flags=re.IGNORECASE | re.MULTILINE,
)


def normalize_live_audit_set_symbols(text: str, tester_server: str) -> str:
    """Adapt symbol parameters in an audit copy to the selected MT5 server."""
    if tester_server.strip().casefold() not in _UNSUFFIXED_AXI_SERVERS:
        return text
    return _SET_SYMBOL_WITH_STANDARD_SUFFIX.sub(r"\1\2", text)
