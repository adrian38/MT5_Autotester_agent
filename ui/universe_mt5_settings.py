"""Persistencia de la conexion MT5 usada por la pantalla Universo."""
from __future__ import annotations


def _variable_value(owner, name: str, default=""):
    variable = getattr(owner, name, None)
    return variable.get() if hasattr(variable, "get") else default


def universe_mt5_settings(owner) -> dict[str, str]:
    """Serializa la cuenta opcional sin acoplar Settings al mixin de Universo."""
    return {
        "ubs_universe_mt5_path": str(_variable_value(owner, "ubs_universe_mt5_path")).strip(),
        "ubs_universe_mt5_use_account": (
            "1" if bool(_variable_value(owner, "ubs_universe_mt5_use_account", False)) else "0"
        ),
        "ubs_universe_mt5_login": str(_variable_value(owner, "ubs_universe_mt5_login")).strip(),
        "ubs_universe_mt5_server": str(_variable_value(owner, "ubs_universe_mt5_server")).strip(),
        "ubs_universe_mt5_password": str(_variable_value(owner, "ubs_universe_mt5_password")),
    }
