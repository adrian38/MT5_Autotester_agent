"""Universo del broker, simbolos retirados y configuracion por periodo."""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import replace
from pathlib import Path

from run_tests import apply_symbol_map, normalize_set_symbol, parse_symbol_map
from ubs.account import DEFAULT_ACCOUNT_TYPE, DEFAULT_BROKER, load_account_timeframe_universe
from ubs.memory import AgentMemory
from ubs.models import Variant
from ubs.score import ScoreConfig
from ubs.universe import load_asset_universe
from ubs_agent_reports import set_symbol_missing_in_terminal
from ubs_agent_config import (
    EXPERIMENTAL_LONG_TIMEFRAMES,
    SYMBOL_NOT_EXIST_STATUS,
    TIMEFRAME_UNIVERSE,
)


_BROKER_UNIVERSE_SYMBOLS_CACHE: dict[str, tuple[float, frozenset[str]]] = {}


def broker_universe_symbols(args: argparse.Namespace) -> set[str]:
    """Simbolos que el broker ofrece, leidos de ``assets/<broker>_assets.ini``.

    NO se usa aqui la politica ``ubs_disabled_symbols_*.json``: ahi conviven
    simbolos deshabilitados a mano o por veredicto ``no_history``, que existen
    en el broker y cuyos candidatos deben poder repararse. Solo la ausencia del
    universo (que ``tools/sync_broker_universe.py`` sincroniza con el servidor)
    significa "retirado".

    Incluye las claves de ``[CommonAliases]`` ademas de los simbolos del broker.
    Un candidato puede llevar el alias como ``target_symbol`` (US100, CRUDEOIL,
    DAX...) mientras el .ini se genera con el nombre canonico, y el mapa de
    simbolos puede estar vacio para la cuenta activa: sin los alias, esos
    objetivos parecerian retirados y acabarian con un estado terminal
    irreversible. Fallar hacia el lado retryable es preferible.

    Se cachea por ruta y mtime porque las llamadas estan dentro de bucles de
    evaluacion; si el universo cambia en disco, el mtime invalida la entrada."""
    raw_path = str(getattr(args, "assets", "") or "").strip()
    if not raw_path:
        return set()
    path = Path(raw_path).expanduser()
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return set()
    cached = _BROKER_UNIVERSE_SYMBOLS_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return set(cached[1])
    groups, aliases = load_asset_universe(path, include_disabled=True)
    symbols = {
        str(symbol).strip().upper()
        for group_symbols in groups.values()
        for symbol in group_symbols
        if str(symbol).strip()
    }
    symbols |= {str(alias).strip().upper() for alias in aliases if str(alias).strip()}
    _BROKER_UNIVERSE_SYMBOLS_CACHE[key] = (mtime, frozenset(symbols))
    return symbols


def current_set_symbol(lines: list[str]) -> str:
    """Return the active ForceSymbol value of a .set, ignoring ``||`` metadata."""
    for line in lines:
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        lhs, raw_value = line.split("=", 1)
        if lhs.strip() != "ForceSymbol":
            continue
        return raw_value.split("||", 1)[0].strip()
    return ""


def symbol_not_offered(
    symbol: str,
    universe_symbols: set[str] | None,
    symbol_map: dict[str, str] | None = None,
) -> bool:
    """True cuando el broker ya no ofrece ``symbol``.

    Con universo vacio devuelve False: sin inventario no se puede afirmar que un
    simbolo este retirado, y el estado que deriva de esto es irreversible."""
    if not universe_symbols:
        return False
    target = str(symbol or "")
    if not target.strip():
        return False
    mapped = apply_symbol_map(target, symbol_map or {})
    keys = {
        target.strip().upper(),
        normalize_set_symbol(target),
        str(mapped).strip().upper(),
        normalize_set_symbol(mapped),
    }
    return keys.isdisjoint({str(value).strip().upper() for value in universe_symbols})


def variant_symbol_not_offered(
    variant: Variant,
    universe_symbols: set[str] | None,
    symbol_map: dict[str, str],
) -> bool:
    """True cuando el simbolo objetivo del candidato ya no esta en el universo."""
    return symbol_not_offered(getattr(variant, "target_symbol", ""), universe_symbols, symbol_map)


def variant_missing_report_status(
    memory: AgentMemory,
    variant: Variant,
    symbol_map: dict[str, str],
    universe_symbols: set[str] | None,
    min_report_mtime: float | None,
) -> str:
    """Estado de un candidato sin reporte: fallo tecnico o simbolo retirado.

    Sin reporte hay dos causas distintas: un fallo tecnico, que admite
    reintento, o que el broker ya no ofrezca el simbolo, en cuyo caso MT5 ni
    abre el tester y reintentar no puede cambiar nada. Un simbolo deshabilitado
    a mano no entra aqui: sigue en el universo y su candidato se repara con
    normalidad.
    """
    if variant_symbol_not_offered(variant, universe_symbols, symbol_map):
        print(
            f"AVISO: {variant.target_symbol} no esta en el universo del broker; "
            f"marcado como {SYMBOL_NOT_EXIST_STATUS} sin reintento."
        )
        memory.record_score(variant.path, None, SYMBOL_NOT_EXIST_STATUS, None)
        return SYMBOL_NOT_EXIST_STATUS
    # El universo se configura a mano y se queda viejo: un futuro vencido sigue
    # listado y su candidato caia en no_report, que es retryable, asi que cada
    # reparacion lo reencolaba para que MT5 volviera a no abrir el tester. El
    # journal del propio terminal lo dice sin ambiguedad.
    if set_symbol_missing_in_terminal(
        variant.path, variant.target_symbol, min_mtime=min_report_mtime,
    ):
        print(
            f"AVISO: MT5 no encuentra {variant.target_symbol} en el servidor del "
            f"broker; marcado como {SYMBOL_NOT_EXIST_STATUS} sin reintento."
        )
        memory.record_score(variant.path, None, SYMBOL_NOT_EXIST_STATUS, None)
        return SYMBOL_NOT_EXIST_STATUS
    memory.record_score(variant.path, None, "no_report", None)
    return "no_report"


def _row_target_symbol(row: sqlite3.Row) -> str:
    try:
        return str(row["target_symbol"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def split_retired_symbols(
    items: list,
    args: argparse.Namespace,
    *,
    row_of=None,
    symbol_map: dict[str, str] | None = None,
) -> tuple[list, list]:
    """Aparta los elementos cuyo ``target_symbol`` ya no ofrece el broker.

    Ahorra copiar el .set y lanzar run_tests para un backtest cuyo resultado ya
    se conoce: MT5 no abriria el tester. El llamador DEBE grabar el estado
    terminal de su etapa para los apartados, porque la seleccion de etapa es
    "sin fila O estado retryable": sin fila volverian a elegirse en cada pasada.

    ``items`` pueden ser filas o tuplas ``(fila, path)``; en ese caso pasa
    ``row_of=lambda item: item[0]``. Devuelve ``(los que siguen, las filas
    apartadas)`` y con universo vacio no aparta nada."""
    universe = broker_universe_symbols(args)
    if not universe:
        return items, []
    if symbol_map is None:
        try:
            symbol_map = parse_symbol_map(getattr(args, "symbol_map", "") or "")
        except ValueError:
            symbol_map = {}
    extract = row_of or (lambda item: item)
    kept: list = []
    retired: list = []
    for item in items:
        row = extract(item)
        if symbol_not_offered(_row_target_symbol(row), universe, symbol_map):
            retired.append(row)
        else:
            kept.append(item)
    return kept, retired


def format_retired_symbol_rows(retired: list) -> str:
    symbols: dict[str, int] = {}
    for row in retired:
        symbol = _row_target_symbol(row).strip() or "(sin dato)"
        symbols[symbol] = symbols.get(symbol, 0) + 1
    return ", ".join(f"{symbol} x{count}" for symbol, count in sorted(symbols.items()))


def missing_report_status(
    symbol: str,
    args: argparse.Namespace,
    symbol_map: dict[str, str] | None = None,
    *,
    set_path: Path | None = None,
    min_mtime: float | None = None,
) -> str:
    """Estado a grabar cuando una etapa no encuentra reporte.

    ``no_report`` es retryable y lo reencolan tanto las rutas de retry como el
    manager; para un simbolo que el broker retiro eso es un bucle infinito,
    porque MT5 no llega a abrir el tester. Devuelve el estado terminal solo en
    ese caso, asi que un fallo tecnico transitorio sigue siendo reparable.

    Con ``set_path`` se consulta ademas el journal que dejo el terminal. El
    universo se mantiene a mano y se queda viejo —un futuro vencido sigue
    listado—, y entonces solo MT5 sabe que el simbolo ya no esta."""
    if symbol_map is None:
        try:
            symbol_map = parse_symbol_map(getattr(args, "symbol_map", "") or "")
        except ValueError:
            symbol_map = {}
    if symbol_not_offered(symbol, broker_universe_symbols(args), symbol_map):
        return SYMBOL_NOT_EXIST_STATUS
    if set_path is not None and set_symbol_missing_in_terminal(
        set_path, symbol, min_mtime=min_mtime,
    ):
        return SYMBOL_NOT_EXIST_STATUS
    return "no_report"


def target_timeframe_universe(
    include_experimental_long: bool = False,
    *,
    base_dir: Path | None = None,
    broker: object = DEFAULT_BROKER,
    account_type: object = DEFAULT_ACCOUNT_TYPE,
) -> tuple[str, ...]:
    if base_dir is not None:
        return load_account_timeframe_universe(
            base_dir,
            account_type,
            broker,
            include_experimental_long=include_experimental_long,
            default_timeframes=TIMEFRAME_UNIVERSE,
            experimental_timeframes=EXPERIMENTAL_LONG_TIMEFRAMES,
        )
    if include_experimental_long:
        return tuple(dict.fromkeys((*TIMEFRAME_UNIVERSE, *EXPERIMENTAL_LONG_TIMEFRAMES)))
    return TIMEFRAME_UNIVERSE


def min_trades_for_period(period: str, default_min_trades: int, w1_min_trades: int, mn_min_trades: int) -> int:
    period = str(period or "").upper()
    if period == "W1":
        return max(0, int(w1_min_trades))
    if period == "MN":
        return max(0, int(mn_min_trades))
    return max(0, int(default_min_trades))


def score_config_for_period(
    score_config: ScoreConfig,
    period: str,
    *,
    min_trades_w1: int,
    min_trades_mn: int,
) -> ScoreConfig:
    min_trades = min_trades_for_period(period, score_config.min_trades, min_trades_w1, min_trades_mn)
    if min_trades == score_config.min_trades:
        return score_config
    return replace(score_config, min_trades=min_trades)


def score_config_for_variant(
    score_config: ScoreConfig,
    variant: Variant,
    *,
    min_trades_w1: int,
    min_trades_mn: int,
) -> ScoreConfig:
    return score_config_for_period(
        score_config,
        variant.target_period,
        min_trades_w1=min_trades_w1,
        min_trades_mn=min_trades_mn,
    )


def regression_score_config(args: argparse.Namespace) -> ScoreConfig:
    """Build the independent thresholds used by the backward OHLC holdout."""

    return ScoreConfig(
        min_net_profit=float(args.regression_min_net_profit),
        min_profit_factor=float(args.regression_min_profit_factor),
        min_trades=int(args.regression_min_trades),
        max_drawdown_pct=float(args.regression_max_drawdown_pct),
        min_recovery_factor=float(args.regression_min_recovery_factor),
        min_positive_month_ratio=float(args.regression_min_positive_month_ratio),
    )


