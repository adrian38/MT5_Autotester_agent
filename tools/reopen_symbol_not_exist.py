"""Reabre las filas marcadas ``symbol_not_exist`` que el broker si ofrece.

``symbol_not_exist`` es terminal y sin reintento: nada lo reencola. Es el
estado correcto para un simbolo retirado, pero tambien lo heredan las filas que
se marcaron cuando al broker le faltaba el alias o el mapa de simbolos. Una vez
corregido el mapa, esas filas se quedarian ahi para siempre.

Solo reabre lo que HOY resuelve contra el universo del broker; una fila cuyo
simbolo sigue sin resolver conserva su estado terminal. Sin ``--write`` se
limita a enumerar.

    py tools/reopen_symbol_not_exist.py --broker VANTAGE --account-type RAW
    py tools/reopen_symbol_not_exist.py --broker VANTAGE --account-type RAW --write
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backtest.symbols import parse_symbol_map
from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_memory_path,
    default_symbol_map_for_broker,
    normalize_account_type,
    normalize_broker,
)
from ubs.agent.generation.universe import broker_universe_symbols, symbol_not_offered
from ubs.backup import backup_memory


# El estado retryable con el que vuelven: ni acepta ni rechaza nada, solo deja
# que la etapa las vuelva a elegir y las ejecute de verdad.
REOPENED_STATUS = "no_report"
# (tabla, columna del simbolo). Las dos guardan estados de etapa.
TABLES = (("seed_scores", "symbol"), ("candidates", "target_symbol"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER)
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE)
    parser.add_argument("--memory", default="", help="Ruta SQLite. Si se omite, usa la memoria de la cuenta.")
    parser.add_argument("--assets", default="", help="Universo del broker. Si se omite, assets/<broker>_assets.ini.")
    parser.add_argument("--write", action="store_true", help="Aplica los cambios. Sin esto solo enumera.")
    return parser.parse_args()


def resolvable_symbols(connection: sqlite3.Connection, args: argparse.Namespace) -> dict[str, list[str]]:
    """Simbolos ``symbol_not_exist`` que el universo actual si resuelve."""
    universe = broker_universe_symbols(args)
    if not universe:
        raise SystemExit(f"Universo vacio o ilegible: {args.assets}")
    symbol_map = parse_symbol_map(default_symbol_map_for_broker(args.broker))
    found: dict[str, list[str]] = {}
    for table, column in TABLES:
        rows = connection.execute(
            f"select {column}, count(*) from {table} "
            "where status = 'symbol_not_exist' group by 1 order by 1"
        ).fetchall()
        for symbol, count in rows:
            if symbol_not_offered(symbol or "", universe, symbol_map):
                print(f"  [-] {table}: {symbol} x{count} sigue sin resolver; se queda terminal.")
                continue
            print(f"  [+] {table}: {symbol} x{count}")
            found.setdefault(table, []).append(symbol)
    return found


def main() -> int:
    args = parse_args()
    args.broker = normalize_broker(args.broker)
    account = normalize_account_type(args.account_type, args.broker)
    memory = Path(args.memory).expanduser() if args.memory else account_memory_path(BASE_DIR, account, args.broker)
    if not args.assets:
        args.assets = str(BASE_DIR / "assets" / f"{args.broker.lower()}_assets.ini")
    if not memory.exists():
        raise SystemExit(f"Memoria inexistente: {memory}")

    print(f"Memoria: {memory}")
    print(f"Universo: {args.assets}")
    connection = sqlite3.connect(memory)
    try:
        found = resolvable_symbols(connection, args)
        total = 0
        for table, column in TABLES:
            symbols = found.get(table, [])
            if not symbols:
                continue
            placeholders = ",".join("?" for _ in symbols)
            query = f"{table} set status = ? where status = 'symbol_not_exist' and {column} in ({placeholders})"
            if not args.write:
                total += connection.execute(
                    f"select count(*) from {table} where status = 'symbol_not_exist' and {column} in ({placeholders})",
                    symbols,
                ).fetchone()[0]
                continue
            total += connection.execute(f"update {query}", [REOPENED_STATUS, *symbols]).rowcount
        if not args.write:
            print(f"\n{total} fila(s) se reabririan como '{REOPENED_STATUS}'. Repite con --write.")
            return 0
        # El backup se toma solo cuando hay algo que escribir y antes de cerrar
        # la transaccion: si algo falla, la memoria sigue siendo la de antes.
        backup_memory(BASE_DIR, account, broker=args.broker, source_path=memory)
        connection.commit()
        print(f"\n{total} fila(s) reabiertas como '{REOPENED_STATUS}'.")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
