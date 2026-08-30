"""Sincroniza assets/<broker>_assets.ini con los simbolos reales del servidor MT5.

Equivalente por linea de comandos del boton "Extraer simbolos MT5" de la UI, para
poder refrescar el inventario desde un job o una sesion sin interfaz. El
inventario estatico es la causa raiz de los abortos ``tester symbol does not
exist``: cuando el broker retira un ticker, el universo sigue generando .set
contra el y MT5 cierra el terminal sin arrancar el tester.

Uso tipico (leer sin escribir):

    py tools/sync_broker_universe.py --broker ICTRADING --dry-run

Escribir el universo y deshabilitar lo que desaparecio:

    py tools/sync_broker_universe.py --broker ICTRADING --disable-removed

Sin --login se adjunta a la sesion del terminal (lo arranca si hace falta y usa
la cuenta guardada), asi que no hay que pasar credenciales.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from ubs.account import (  # noqa: E402
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_disabled_symbols_path,
    broker_asset_universe_path,
)
from ubs.mt5_symbol_extract import (  # noqa: E402
    MT5SymbolExtractionError,
    _load_existing_asset_universe,
    extract_symbols_from_mt5,
    sync_asset_universe_groups,
    write_asset_universe_from_symbols,
)
from ubs.universe import load_disabled_symbols, save_disabled_symbols  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER)
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE)
    parser.add_argument("--terminal", default="", help="Ruta a terminal64.exe. Vacio usa el terminal por defecto de MT5.")
    parser.add_argument("--login", type=int, help="Cuenta MT5. Omitir para usar la sesion guardada del terminal.")
    parser.add_argument("--server", default="", help="Servidor MT5, solo con --login.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo informa del diff (agregados/eliminados); no escribe nada.",
    )
    parser.add_argument(
        "--preserve-groups",
        action="store_true",
        help="Mantiene cada simbolo existente en su seccion actual en vez de reclasificar todo.",
    )
    parser.add_argument(
        "--disable-removed",
        action="store_true",
        help="Agrega los simbolos desaparecidos a ubs_disabled_symbols_<BROKER>_<CUENTA>.json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    terminal_path = Path(args.terminal).expanduser() if args.terminal.strip() else None
    if terminal_path is not None and not terminal_path.exists():
        print(f"ERROR: no existe el terminal {terminal_path}")
        return 1

    universe_path = broker_asset_universe_path(BASE_DIR, args.broker)
    try:
        extraction = extract_symbols_from_mt5(
            terminal_path=terminal_path,
            login=args.login,
            server=args.server,
        )
    except MT5SymbolExtractionError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(
        f"Cuenta: {extraction.account_login or '(sesion actual)'} | "
        f"Servidor: {extraction.server or '(sin dato)'} | "
        f"Simbolos en servidor: {len(extraction.symbols)}"
    )
    print(f"Universo: {universe_path}")

    if args.dry_run:
        existing_groups, _aliases = _load_existing_asset_universe(universe_path)
        groups, added, removed = sync_asset_universe_groups(
            existing_groups,
            extraction.symbols,
            preserve_existing_groups=args.preserve_groups,
        )
        total = sum(len(values) for values in groups.values())
        print(f"DRY-RUN total={total} agregados={len(added)} eliminados={len(removed)}")
        print(f"  grupos: {({group: len(values) for group, values in groups.items()})}")
        print(f"  eliminados: {', '.join(removed) if removed else '(ninguno)'}")
        return 0

    result = write_asset_universe_from_symbols(
        universe_path,
        extraction.symbols,
        preserve_existing_groups=args.preserve_groups,
    )
    total = sum(result.counts.values())
    print(f"Escrito: total={total} agregados={len(result.added_symbols)} eliminados={len(result.removed_symbols)}")
    print(f"  backup: {result.backup_path}")
    print(f"  grupos: {result.counts}")
    print(f"  eliminados: {', '.join(result.removed_symbols) if result.removed_symbols else '(ninguno)'}")

    if args.disable_removed and result.removed_symbols:
        disabled_path = account_disabled_symbols_path(BASE_DIR, args.account_type, args.broker)
        before = load_disabled_symbols(disabled_path)
        if disabled_path.exists():
            backup = disabled_path.with_suffix(
                disabled_path.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}"
            )
            shutil.copy2(disabled_path, backup)
            print(f"  backup deshabilitados: {backup}")
        after = before | {symbol.upper() for symbol in result.removed_symbols}
        save_disabled_symbols(disabled_path, after)
        print(f"Deshabilitados: {disabled_path} ({len(before)} -> {len(after)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
