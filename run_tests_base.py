"""Rutas, constantes y tipos compartidos por el lanzador de backtests."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
CONFIG_DIR = BASE_DIR / "configs"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"
EXPERTS_FILE = BASE_DIR / "experts_list.txt"
EXPERTS_ROOT_FILE = BASE_DIR / "experts_root.txt"
TEMPLATE_FILE = BASE_DIR / "tester_template.ini"
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RUNNING_TERMINAL_EXIT_CODE = 3
MODEL4_NO_HISTORY_EXIT_CODE = 4
MODEL4_NO_HISTORY_RETRY_DELAY_SECONDS = 5
SKIPPED_SYMBOL_EXIT_CODE = 5

# MT5 aborta el arranque del Strategy Tester con codigos propios y sin generar
# reporte.  El journal los escribe con signo ("shutdown with -1000012358") pero
# Popen los entrega sin signo en Windows (3294954938), asi que se normalizan.
# transient=False significa que reintentar el mismo .ini no puede cambiar el
# resultado: el reintento solo gasta un arranque de terminal.
MT5_TESTER_ABORT_CODES: dict[int, tuple[str, bool]] = {
    -1000012358: ("el Symbol del tester no existe en el servidor del broker", False),
    -1000012362: ("el terminal no estaba sincronizado con el servidor de trading", True),
}
RUN_LOG_QUEUE_MAX_BATCHES = 20_000
WATCHDOG_TERMINATE_MIN_INTERVAL_SECONDS = 0.75
WATCHDOG_RESTART_MIN_INTERVAL_SECONDS = 3.0
GENERATED_SET_ROOT_PREFIXES = ("accepted_gen_", "mismatch_gen_")

# Lines in the MT5 tester journal that indicate MT5 is stuck waiting for tick download.
# If the journal's last line matches one of these AND the file hasn't grown for two
# consecutive 10-second checks, the process is killed and retried.
TESTER_STUCK_MARKERS = (
    "preliminary downloading of history ticks started",
)
REPORT_SAVE_STALL_SECONDS = 30
REPORT_SAVE_CHECK_INTERVAL = 2
DEFAULT_TESTER_STALL_AFTER_SECONDS = 300
DEFAULT_TESTER_MAX_RUNTIME_SECONDS = 1800
GENERATED_SET_ROOT_NAMES = {"retry_mismatch", "robustness", "final_tick"}

DEFAULT_MT5_PATHS = (
    Path(r"C:\Program Files\RoboForex MT5 Terminal\terminal64.exe"),
    Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
    Path(r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe"),
)


@dataclass(frozen=True)
class TesterSettings:
    mt5_path: Path
    delay_seconds: int
    portable: bool
    data_dir: Path | None
    tester_kick_after_seconds: int = 0
    terminal_cooldown_seconds: int = 0
    tester_stall_after_seconds: int = DEFAULT_TESTER_STALL_AFTER_SECONDS
    tester_max_runtime_seconds: int = DEFAULT_TESTER_MAX_RUNTIME_SECONDS


@dataclass(frozen=True)
class TerminalProfile:
    name: str
    mt5_path: Path
    data_dir: Path | None
    experts_root: Path
    ubs_ex5_file: Path | None
    portable: bool


@dataclass(frozen=True)
class BacktestJob:
    index: int
    expert: str
    set_file: Path | None


@dataclass(frozen=True)
class HistoryCacheRotation:
    original: Path
    backup: Path
