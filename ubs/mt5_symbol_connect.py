from __future__ import annotations

import importlib
from pathlib import Path
from typing import Iterable

from ubs.mt5_symbol_types import (
    ExtractedSymbol,
    MT5SymbolExtractionError,
    SymbolExtractionResult,
    SymbolSpec,
    SymbolSpecExtractionResult,
)


def _mt5_module():
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise MT5SymbolExtractionError(
            "Falta el paquete opcional MetaTrader5. Instala con: pip install MetaTrader5"
        ) from exc


def _init_and_login(
    mt5,
    *,
    terminal_path: Path | None,
    login: int | None,
    password: str,
    server: str,
    timeout_ms: int,
) -> None:
    """Initialize MT5 and optionally log in. Raises MT5SymbolExtractionError on failure.

    When ``login`` is None and a terminal is already running and logged in, MT5
    attaches to that live session (no credentials needed).
    """
    init_kwargs: dict[str, object] = {"timeout": timeout_ms}
    if terminal_path:
        init_kwargs["path"] = str(terminal_path)

    if not mt5.initialize(**init_kwargs):
        code, message = mt5.last_error()
        raise MT5SymbolExtractionError(f"No se pudo inicializar MT5: {code} {message}")

    if login is not None:
        login_kwargs: dict[str, object] = {"login": login}
        if password:
            login_kwargs["password"] = password
        if server:
            login_kwargs["server"] = server
        if not mt5.login(**login_kwargs):
            code, message = mt5.last_error()
            mt5.shutdown()
            raise MT5SymbolExtractionError(f"No se pudo iniciar sesion MT5: {code} {message}")


def extract_symbols_from_mt5(
    *,
    terminal_path: Path | None = None,
    login: int | None = None,
    password: str = "",
    server: str = "",
    timeout_ms: int = 60000,
) -> SymbolExtractionResult:
    mt5 = _mt5_module()
    _init_and_login(
        mt5,
        terminal_path=terminal_path,
        login=login,
        password=password,
        server=server,
        timeout_ms=timeout_ms,
    )

    try:
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


def _first_positive(*values: object) -> float:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0.0


def _margin_for_min_lot(mt5, name: str, volume: float, price: float) -> float:
    """Margin MT5 requires for one position at ``volume``, in the deposit currency.

    ``order_calc_margin`` already applies the product's margin tier and the
    account leverage, so there is nothing to derive from it. Returns 0.0 when MT5
    declines to answer (symbol not tradable, no price, older build without the
    call): the caller keeps whatever the previous dump had.
    """
    if volume <= 0 or price <= 0:
        return 0.0
    order_type = getattr(mt5, "ORDER_TYPE_BUY", None)
    calc = getattr(mt5, "order_calc_margin", None)
    if order_type is None or calc is None:
        return 0.0
    try:
        margin = calc(order_type, name, volume, price)
    except Exception:
        return 0.0
    try:
        return max(0.0, float(margin))
    except (TypeError, ValueError):
        return 0.0


def _last_historical_close(mt5, name: str) -> float:
    """Last daily close from history, used as a price fallback when quotes are empty.

    Works when the market is closed (unlike live ticks). Tries D1 then H1 bars and
    returns 0.0 when the symbol has no accessible history.
    """
    for timeframe_attr in ("TIMEFRAME_D1", "TIMEFRAME_H1"):
        timeframe = getattr(mt5, timeframe_attr, None)
        if timeframe is None:
            continue
        try:
            rates = mt5.copy_rates_from_pos(name, timeframe, 0, 1)
        except Exception:
            rates = None
        if rates is None or len(rates) == 0:
            continue
        try:
            close = float(rates[-1]["close"])
        except (KeyError, IndexError, TypeError, ValueError):
            try:
                close = float(rates[-1][4])  # OHLC tuple order: time,open,high,low,close
            except (IndexError, TypeError, ValueError):
                close = 0.0
        if close > 0:
            return close
    return 0.0


def _symbol_price(mt5, info, name: str) -> float:
    tick = mt5.symbol_info_tick(name)
    price = _first_positive(
        getattr(tick, "ask", 0.0) if tick is not None else 0.0,
        getattr(tick, "bid", 0.0) if tick is not None else 0.0,
        getattr(tick, "last", 0.0) if tick is not None else 0.0,
        getattr(info, "ask", 0.0),
        getattr(info, "bid", 0.0),
        getattr(info, "last", 0.0),
        getattr(info, "session_close", 0.0),
        getattr(info, "session_open", 0.0),
    )
    if price <= 0:
        # Live quotes are empty when the market is closed; the last daily
        # close is available from history regardless of session state.
        price = _last_historical_close(mt5, name)
    return price


def _symbol_spec(mt5, info, name: str) -> SymbolSpec:
    price = _symbol_price(mt5, info, name)
    volume_min = float(getattr(info, "volume_min", 0.0) or 0.0)
    return SymbolSpec(
        name=str(getattr(info, "name", name) or name),
        path=str(getattr(info, "path", "") or ""),
        volume_min=volume_min,
        volume_step=float(getattr(info, "volume_step", 0.0) or 0.0),
        volume_max=float(getattr(info, "volume_max", 0.0) or 0.0),
        contract_size=float(getattr(info, "trade_contract_size", 0.0) or 0.0),
        tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
        tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        price=price,
        currency_profit=str(getattr(info, "currency_profit", "") or ""),
        currency_base=str(getattr(info, "currency_base", "") or ""),
        digits=getattr(info, "digits", None),
        margin_min_lot=_margin_for_min_lot(mt5, name, volume_min, price),
    )


def extract_symbol_specs_from_mt5(
    symbols: Iterable[str],
    *,
    terminal_path: Path | None = None,
    login: int | None = None,
    password: str = "",
    server: str = "",
    timeout_ms: int = 60000,
) -> SymbolSpecExtractionResult:
    """Read live trading specs for the requested symbols from a running MT5 terminal.

    Each symbol is selected into Market Watch before reading ``symbol_info`` so that
    contract size, tick value/size, minimum volume and current price are populated.
    Symbols MT5 cannot resolve are returned in ``missing_symbols`` instead of raising.
    """
    mt5 = _mt5_module()
    _init_and_login(
        mt5,
        terminal_path=terminal_path,
        login=login,
        password=password,
        server=server,
        timeout_ms=timeout_ms,
    )

    try:
        account = mt5.account_info()
        account_currency = str(getattr(account, "currency", "") or "")
        specs: list[SymbolSpec] = []
        missing: list[str] = []
        seen: set[str] = set()
        for raw_name in symbols:
            name = str(raw_name or "").strip()
            key = name.upper()
            if not name or key in seen:
                continue
            seen.add(key)

            # Selecting the symbol forces MT5 to populate quotes/specs for it.
            mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
            if info is None:
                missing.append(name)
                continue

            specs.append(_symbol_spec(mt5, info, name))
        specs.sort(key=lambda spec: spec.name.upper())
        leverage = getattr(account, "leverage", None) if account is not None else None
        return SymbolSpecExtractionResult(
            specs=tuple(specs),
            terminal_path=terminal_path,
            account_login=int(account.login) if account is not None and getattr(account, "login", None) else None,
            server=str(getattr(account, "server", "") or server or ""),
            account_currency=account_currency,
            missing_symbols=tuple(missing),
            account_leverage=int(leverage) if leverage else None,
        )
    finally:
        mt5.shutdown()
