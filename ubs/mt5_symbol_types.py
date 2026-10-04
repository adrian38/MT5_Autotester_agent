from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path




@dataclass(frozen=True)
class ExtractedSymbol:
    name: str
    path: str = ""
    visible: bool = False
    trade_mode: int | None = None


@dataclass(frozen=True)
class SymbolSpec:
    """Live trading specification for one symbol, as reported by MT5.

    All monetary fields are expressed in the account's deposit currency, which is
    what MT5 uses for ``trade_tick_value``. This is the data needed to normalize
    backtest net profit onto a common notional basis regardless of the broker's
    per-symbol minimum lot.
    """

    name: str
    path: str = ""
    volume_min: float = 0.0
    volume_step: float = 0.0
    volume_max: float = 0.0
    contract_size: float = 0.0
    tick_value: float = 0.0
    tick_size: float = 0.0
    price: float = 0.0
    currency_profit: str = ""
    currency_base: str = ""
    digits: int | None = None
    # Margin MT5 requires for ONE position at volume_min, with the leverage the
    # account had when measured. The portfolio manager reads this from the spec
    # dump as its best margin source, so the dump must never be written without it.
    margin_min_lot: float = 0.0


@dataclass(frozen=True)
class SymbolExtractionResult:
    symbols: tuple[ExtractedSymbol, ...]
    terminal_path: Path | None
    account_login: int | None
    server: str


@dataclass(frozen=True)
class SymbolSpecExtractionResult:
    specs: tuple[SymbolSpec, ...]
    terminal_path: Path | None
    account_login: int | None
    server: str
    account_currency: str
    missing_symbols: tuple[str, ...]
    account_leverage: int | None = None


@dataclass(frozen=True)
class AssetUniverseSyncResult:
    backup_path: Path | None
    counts: dict[str, int]
    added_symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]


class MT5SymbolExtractionError(RuntimeError):
    pass
