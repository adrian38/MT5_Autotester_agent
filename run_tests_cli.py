"""Lectura de la linea de ordenes del lanzador de backtests."""
from __future__ import annotations

import argparse
from pathlib import Path

from run_tests_base import TEMPLATE_FILE, UI_SETTINGS_FILE


def _add_terminal_arguments(parser: argparse.ArgumentParser) -> None:
    """Terminal MT5, plantilla y sufijos de simbolo."""
    parser.add_argument("--mt5-path", help="Ruta completa a terminal64.exe.")
    parser.add_argument(
        "--data-dir",
        help="Carpeta de datos del terminal MT5, por ejemplo ...\\MetaQuotes\\Terminal\\HASH.",
    )
    parser.add_argument("--template", default=str(TEMPLATE_FILE), help="Archivo .ini general.")
    parser.add_argument(
        "--symbol-suffix",
        default="",
        help="Sufijo a agregar al Symbol del template, por ejemplo .a. Vacio no modifica el simbolo.",
    )
    parser.add_argument(
        "--symbol-futures-suffix",
        default="",
        help="Sufijo para futuros/CFDs especiales cuando el universo lo indique, por ejemplo .fs.",
    )
    parser.add_argument(
        "--symbol-shares-suffix",
        default="",
        help="Sufijo para shares/ETFs cuando el universo lo indique, por ejemplo +.",
    )
    parser.add_argument(
        "--symbol-universe",
        default="",
        help="Archivo assets.ini usado para elegir entre sufijo general, futuros y shares.",
    )
    parser.add_argument(
        "--symbol-map",
        default="",
        help="Correspondencias de simbolos del broker, por ejemplo XTIUSD=USOIL,GER40=DAX.",
    )


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    """Expertos, ficheros .set y como inferir el contexto del tester."""
    parser.add_argument(
        "--experts-dir",
        help="Carpeta donde buscar .ex5. Si se usa, no lee experts_list.txt.",
    )
    parser.add_argument("--expert", help="Nombre o ruta de un Expert Advisor concreto .ex5/.mq5.")
    parser.add_argument(
        "--set-dir",
        help="Carpeta con archivos .set. Si se usa junto a --expert, ejecuta ese EA una vez por cada .set.",
    )
    parser.add_argument(
        "--set-file",
        action="append",
        help="Archivo .set concreto. Puede repetirse; requiere --expert.",
    )
    parser.add_argument(
        "--infer-tester-from-set",
        action="store_true",
        help="Rellena Symbol y Period del tester desde cada .set cuando sea posible.",
    )
    parser.add_argument(
        "--prefer-set-path-timeframe",
        action="store_true",
        help="Con --infer-tester-from-set, prefiere el timeframe del path/nombre del .set sobre parametros internos.",
    )
    parser.add_argument("--delay", type=int, default=5, help="Pausa en segundos entre tests.")
    parser.add_argument("--recursive", action="store_true", help="Procesar todos los .ex5 de la carpeta indicada.")
    parser.add_argument(
        "--skip-running-check",
        action="store_true",
        help="No comprobar si MT5 ya esta abierto antes de lanzar los backtests.",
    )


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    """Portabilidad, multiterminal, fechas y modelo del tester."""
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Arranca MT5 con /portable. Util para terminales copiados fuera de Program Files.",
    )
    parser.add_argument(
        "--terminals-config",
        default=str(UI_SETTINGS_FILE),
        help="Archivo .ini con secciones [Multiterminal] y [Terminal.N].",
    )
    parser.add_argument(
        "--multi-terminal",
        action="store_true",
        help="Reparte la cola entre terminales MT5 configuradas.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximo de terminales simultaneas cuando --multi-terminal esta activo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Genera los .ini y muestra los comandos, pero no abre MT5.",
    )
    parser.add_argument(
        "--from-date",
        default="",
        help="Fecha inicio backtest en formato YYYY.MM.DD. Sobreescribe FromDate del template.",
    )
    parser.add_argument(
        "--to-date",
        default="",
        help="Fecha fin backtest en formato YYYY.MM.DD. Sobreescribe ToDate del template.",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "Modo de modelado MT5. 0=Every tick, 1=1 minute OHLC, "
            "2=Open price only, 3=Math calculations, 4=Every tick based on real ticks. "
            "Vacio usa el template."
        ),
    )


def _add_watchdog_arguments(parser: argparse.ArgumentParser) -> None:
    """Tiempos del watchdog y enfriamiento del terminal."""
    parser.add_argument(
        "--tester-kick-after",
        type=int,
        default=None,
        help=(
            "Solo para Model=4: si MT5 sigue activo tras N segundos, mata el proceso "
            "lanzado y reintenta una vez. Por defecto lee [Multiterminal] tester_kick_after."
        ),
    )
    parser.add_argument(
        "--tester-stall-after",
        type=int,
        default=None,
        help=(
            "Para cualquier Model: si no hay progreso de journal/reporte durante N segundos, "
            "mata el proceso y reintenta una vez. Por defecto lee [Multiterminal] tester_stall_after."
        ),
    )
    parser.add_argument(
        "--tester-max-runtime",
        type=int,
        default=None,
        help=(
            "Para cualquier Model: limite absoluto por backtest en segundos. "
            "Por defecto lee [Multiterminal] tester_max_runtime. 0 lo desactiva."
        ),
    )
    parser.add_argument(
        "--terminal-cooldown",
        type=int,
        default=None,
        help=(
            "Pausa en segundos despues de terminar/matar MT5 antes del siguiente intento. "
            "Por defecto lee [Multiterminal] terminal_cooldown."
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta backtests de MT5 en serie para los EA listados en experts_list.txt."
    )
    _add_terminal_arguments(parser)
    _add_input_arguments(parser)
    _add_execution_arguments(parser)
    _add_watchdog_arguments(parser)
    return parser.parse_args()
