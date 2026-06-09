import argparse
import configparser
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mt5_env import MT5_TERMINAL_ENV, terminal_path_from_env


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
GENERATED_SET_ROOT_PREFIXES = ("accepted_gen_", "mismatch_gen_")
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


class RunLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self._lock = threading.Lock()
        self.last_log_path = LOG_DIR / "last_run.log"
        self.last_log_path.write_text("", encoding="utf-8")

    def write(self, message: str = "") -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}" if message else ""
        with self._lock:
            print(message)
            with self.log_path.open("a", encoding="utf-8") as file:
                file.write(f"{line}\n")
            with self.last_log_path.open("a", encoding="utf-8") as file:
                file.write(f"{line}\n")

    def write_many(self, messages: list[str]) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as log_file, self.last_log_path.open("a", encoding="utf-8") as last_file:
                for message in messages:
                    line = f"[{timestamp}] {message}" if message else ""
                    print(message)
                    log_file.write(f"{line}\n")
                    last_file.write(f"{line}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta backtests de MT5 en serie para los EA listados en experts_list.txt."
    )
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
        "--symbol-map",
        default="",
        help="Correspondencias de simbolos del broker, por ejemplo XTIUSD=USOIL,GER40=DAX.",
    )
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
    return parser.parse_args()


def find_mt5_path(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()

    env_path = terminal_path_from_env()
    if env_path:
        return env_path

    for candidate in DEFAULT_MT5_PATHS:
        if candidate.exists():
            return candidate

    from_path = shutil.which("terminal64.exe")
    if from_path:
        return Path(from_path)

    return DEFAULT_MT5_PATHS[0]


def should_use_portable(mt5_path: Path, cli_portable: bool) -> bool:
    if cli_portable:
        return True
    install_dir = mt5_path.parent
    return (install_dir / "MQL5" / "Experts").exists()


def get_running_terminal_processes() -> list[dict[str, str]]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process -Filter \"name='terminal64.exe'\" | "
            "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        ),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, creationflags=NO_WINDOW)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    import json

    data = json.loads(result.stdout)
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "pid": str(item.get("ProcessId", "")),
            "path": str(item.get("ExecutablePath", "")),
            "command": str(item.get("CommandLine", "")),
        }
        for item in data
    ]


def find_matching_running_terminals(mt5_path: Path) -> list[dict[str, str]]:
    target = str(mt5_path).lower()
    matches = []
    for process in get_running_terminal_processes():
        process_path = process["path"].lower()
        process_command = process["command"].lower()
        if process_path == target or target in process_command:
            matches.append(process)
    return matches


def discover_terminal_data_dirs(expert_names: list[str]) -> list[Path]:
    terminal_root = Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal"
    if not terminal_root.exists():
        return []

    data_dirs: list[Path] = []
    for directory in terminal_root.iterdir():
        experts_dir = directory / "MQL5" / "Experts"
        if not experts_dir.exists():
            continue
        for expert in expert_names:
            expert_file = experts_dir / Path(expert.replace("/", "\\")).name
            if expert_file.exists():
                data_dirs.append(directory)
                break
    return sorted(set(data_dirs))


def terminal_data_dir_from_experts_dir(experts_dir: Path) -> Path | None:
    parts = [part.lower() for part in experts_dir.parts]
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ["mql5", "experts"]:
            return Path(*experts_dir.parts[:index])
    return None


def normalized_path(path: Path) -> str:
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    return str(path).rstrip("\\/").lower()


def read_origin_path(path: Path) -> Path | None:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = path.read_text(encoding=encoding).strip()
            if text:
                return Path(text).expanduser()
        except UnicodeError:
            continue
        except OSError:
            return None
    return None


def terminal_data_dir_from_origin(mt5_path: Path) -> Path | None:
    terminal_root = Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal"
    install_dir = normalized_path(mt5_path.parent)
    if not terminal_root.exists():
        return None

    for origin_file in terminal_root.glob("*/origin.txt"):
        origin_path = read_origin_path(origin_file)
        if origin_path and normalized_path(origin_path) == install_dir:
            return origin_file.parent
    return None


def portable_terminal_data_dir(mt5_path: Path) -> Path | None:
    data_dir = mt5_path.parent
    if (data_dir / "MQL5" / "Experts").exists():
        return data_dir
    return None


def terminal_data_dir_from_cli(cli_value: str | None) -> Path | None:
    if not cli_value:
        return None
    data_dir = Path(cli_value).expanduser()
    if not data_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta de datos MT5: {data_dir}")
    if not data_dir.is_dir():
        raise NotADirectoryError(f"No es una carpeta de datos MT5: {data_dir}")
    return data_dir


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def terminal_section_sort_key(section: str) -> tuple[int, str]:
    suffix = section.split(".", 1)[1] if "." in section else section
    try:
        return (int(suffix), section)
    except ValueError:
        return (999999, section)


def load_terminal_profiles(config_path: Path, *, ignore_enabled: bool = False) -> list[TerminalProfile]:
    if not config_path.exists():
        raise FileNotFoundError(f"No existe la configuracion multiterminal: {config_path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(config_path, encoding="utf-8-sig")
    profiles: list[TerminalProfile] = []
    sections = sorted(
        (section for section in parser.sections() if section.lower().startswith("terminal.")),
        key=terminal_section_sort_key,
    )
    for index, section in enumerate(sections, start=1):
        values = parser[section]
        if not ignore_enabled and not parse_bool(values.get("enabled"), True):
            continue
        mt5_raw = values.get("mt5_path", "").strip()
        experts_raw = values.get("experts_root", "").strip()
        if not mt5_raw:
            raise ValueError(f"{section}: falta mt5_path.")
        if not experts_raw:
            raise ValueError(f"{section}: falta experts_root.")
        mt5_path = Path(mt5_raw).expanduser()
        experts_root = Path(experts_raw).expanduser()
        data_raw = values.get("data_dir", "").strip()
        ubs_raw = values.get("ubs_ex5_file", "").strip()
        ubs_ex5_file = Path(ubs_raw).expanduser() if ubs_raw else None
        if ubs_ex5_file and not ubs_ex5_file.is_absolute():
            ubs_ex5_file = experts_root / ubs_ex5_file
        portable = parse_bool(values.get("portable"), should_use_portable(mt5_path, False))
        profiles.append(
            TerminalProfile(
                name=values.get("name", "").strip() or f"Terminal {index}",
                mt5_path=mt5_path,
                data_dir=Path(data_raw).expanduser() if data_raw else None,
                experts_root=experts_root,
                ubs_ex5_file=ubs_ex5_file,
                portable=portable,
            )
        )
    return profiles


def profile_data_dir(profile: TerminalProfile) -> Path | None:
    return profile.data_dir or (portable_terminal_data_dir(profile.mt5_path) if profile.portable else terminal_data_dir_from_origin(profile.mt5_path))


def settings_from_profile(profile: TerminalProfile, delay_seconds: int) -> TesterSettings:
    return TesterSettings(
        mt5_path=profile.mt5_path,
        delay_seconds=delay_seconds,
        portable=profile.portable,
        data_dir=profile_data_dir(profile),
    )


def load_experts() -> list[str]:
    if not EXPERTS_FILE.exists():
        raise FileNotFoundError(f"No existe {EXPERTS_FILE}")

    experts: list[str] = []
    for line in EXPERTS_FILE.read_text(encoding="utf-8-sig").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        experts.append(item)
    return experts


def load_experts_from_dir(experts_dir: Path, recursive: bool = False, allow_sources: bool = False) -> list[str]:
    if not experts_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta de Expert Advisors: {experts_dir}")
    if not experts_dir.is_dir():
        raise NotADirectoryError(f"No es una carpeta: {experts_dir}")

    data_dir = terminal_data_dir_from_experts_dir(experts_dir)
    experts_root = data_dir / "MQL5" / "Experts" if data_dir else experts_dir
    experts = []
    ex5_iter = experts_dir.glob("*.ex5")
    for file_path in sorted(ex5_iter):
        try:
            experts.append(str(file_path.relative_to(experts_root)))
        except ValueError:
            experts.append(file_path.name)
    if allow_sources and not experts:
        mq5_iter = experts_dir.glob("*.mq5")
        for file_path in sorted(mq5_iter):
            expert_path = file_path.with_suffix(".ex5")
            try:
                experts.append(str(expert_path.relative_to(experts_root)))
            except ValueError:
                experts.append(expert_path.name)
    return experts


def missing_experts_in_terminal_data_dirs(experts: list[str], terminal_data_dirs: list[Path]) -> list[str]:
    missing = []
    for expert in experts:
        expert_file = Path(expert.replace("/", "\\"))
        if not any((data_dir / "MQL5" / "Experts" / expert_file).exists() for data_dir in terminal_data_dirs):
            missing.append(str(expert_file))
    return missing


def expert_from_value(value: str) -> str:
    path = Path(value.strip().replace("/", "\\"))
    if path.suffix.lower() in (".mq5", ".ex5"):
        path = path.with_suffix(".ex5")
    elif not path.suffix:
        path = path.with_suffix(".ex5")
    parts_lower = [part.lower() for part in path.parts]
    if "experts" in parts_lower:
        index = parts_lower.index("experts")
        relative_parts = path.parts[index + 1 :]
        if relative_parts:
            return str(Path(*relative_parts))
    return str(path)


def expert_from_cli_value(value: str, experts_dir: Path | None) -> str:
    path = Path(value.strip().replace("/", "\\"))
    if path.suffix.lower() in (".mq5", ".ex5"):
        path = path.with_suffix(".ex5")
    elif not path.suffix:
        path = path.with_suffix(".ex5")

    if experts_dir and not path.is_absolute():
        candidate = experts_dir / path
        data_dir = terminal_data_dir_from_experts_dir(candidate.parent)
        if data_dir:
            experts_root = data_dir / "MQL5" / "Experts"
            try:
                return str(candidate.relative_to(experts_root))
            except ValueError:
                pass

    return expert_from_value(str(path))


def expert_file_path(expert: str, experts_root: Path) -> Path:
    path = Path(expert.strip().replace("/", "\\"))
    if path.is_absolute():
        return path.with_suffix(".ex5") if path.suffix.lower() in {".mq5", ".ex5"} else path
    if path.suffix.lower() in {".mq5", ".ex5"}:
        path = path.with_suffix(".ex5")
    elif not path.suffix:
        path = path.with_suffix(".ex5")
    return experts_root / path


def looks_like_ubs_expert_file(path: str | Path) -> bool:
    stem = Path(str(path)).stem.lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", stem) if token]
    compact = "".join(tokens)
    return (
        "ubs" in tokens
        or compact.startswith("ubs")
        or ("ultimate" in tokens and "breakout" in tokens)
    )


def profile_expert_for_job(profile: TerminalProfile, job: BacktestJob, set_mode: bool) -> str:
    if set_mode and profile.ubs_ex5_file:
        return expert_from_cli_value(str(profile.ubs_ex5_file), profile.experts_root)
    return job.expert


def validate_terminal_profiles(
    profiles: list[TerminalProfile],
    jobs: list[BacktestJob],
    *,
    set_mode: bool,
    dry_run: bool,
) -> list[str]:
    errors: list[str] = []
    for profile in profiles:
        prefix = profile.name
        if not dry_run and not profile.mt5_path.exists():
            errors.append(f"{prefix}: no existe terminal64.exe: {profile.mt5_path}")
        if profile.data_dir and (not profile.data_dir.exists() or not profile.data_dir.is_dir()):
            errors.append(f"{prefix}: carpeta de datos MT5 invalida: {profile.data_dir}")
        if not dry_run and (not profile.experts_root.exists() or not profile.experts_root.is_dir()):
            errors.append(f"{prefix}: carpeta Experts invalida: {profile.experts_root}")
        if set_mode:
            if not profile.ubs_ex5_file:
                errors.append(f"{prefix}: falta ubs_ex5_file para Tester/Agente UBS.")
            else:
                if not looks_like_ubs_expert_file(profile.ubs_ex5_file):
                    errors.append(f"{prefix}: UBS .ex5 no parece Ultimate Breakout System: {profile.ubs_ex5_file}")
                if not dry_run and not profile.ubs_ex5_file.exists():
                    errors.append(f"{prefix}: no existe UBS .ex5: {profile.ubs_ex5_file}")
            continue
        if dry_run:
            continue
        for job in jobs:
            candidate = expert_file_path(job.expert, profile.experts_root)
            if not candidate.exists():
                errors.append(f"{prefix}: falta EA {job.expert} en {profile.experts_root}")
                break
    return errors


def load_experts_root() -> Path | None:
    if not EXPERTS_ROOT_FILE.exists():
        return None

    for line in EXPERTS_ROOT_FILE.read_text(encoding="utf-8-sig").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        return Path(item).expanduser()
    return None


def safe_name(expert_path: str) -> str:
    path = Path(expert_path)
    name = path.stem if path.suffix.lower() in {".ex5", ".mq5", ".set", ".ini", ".htm", ".html"} else path.name
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in name)


def load_set_files(set_dir: Path | None, set_files: list[str] | None, recursive: bool = False) -> list[Path]:
    files: list[Path] = []
    if set_dir:
        if not set_dir.exists():
            raise FileNotFoundError(f"No existe la carpeta de set files: {set_dir}")
        if not set_dir.is_dir():
            raise NotADirectoryError(f"No es una carpeta: {set_dir}")
        iterator = set_dir.rglob("*.set") if recursive else set_dir.glob("*.set")
        files.extend(sorted(path for path in iterator if path.is_file() and not _is_auxiliary_generated_set(set_dir, path)))

    for value in set_files or []:
        path = Path(value).expanduser()
        if not path.is_absolute() and set_dir:
            path = set_dir / path
        if path.suffix.lower() != ".set":
            raise ValueError(f"El set file debe terminar en .set: {path}")
        if not path.exists():
            raise FileNotFoundError(f"No existe el set file: {path}")
        files.append(path)

    return sorted(set(files))


def _is_auxiliary_generated_set(set_dir: Path, path: Path) -> bool:
    try:
        relative_parts = path.relative_to(set_dir).parts
    except ValueError:
        return False
    if len(relative_parts) < 2:
        return False
    root = relative_parts[0]
    return root in GENERATED_SET_ROOT_NAMES or any(root.startswith(prefix) for prefix in GENERATED_SET_ROOT_PREFIXES)


def mapped_set_text_for_tester(set_file: Path, symbol_map: dict[str, str]) -> tuple[str | None, list[str]]:
    if not symbol_map:
        return None, []
    text = read_set_text(set_file)
    lines = text.splitlines()
    changes: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip().lstrip("\ufeff")
        if not stripped or stripped.startswith(";") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if key not in {"ForceSymbol", "Symbol"}:
            continue
        current = raw_value.split("||", 1)[0].strip()
        mapped = apply_symbol_map(current, symbol_map).strip()
        if not current or mapped.upper() == current.upper():
            continue
        lhs = line.split("=", 1)[0]
        if "||" in raw_value:
            parts = raw_value.split("||")
            parts[0] = mapped
            lines[index] = f"{lhs}={'||'.join(parts)}"
        else:
            lines[index] = f"{lhs}={mapped}"
        changes.append(f"{key}: {current} -> {mapped}")
    if not changes:
        return None, []
    return "\n".join(lines) + ("\n" if text.endswith(("\n", "\r\n")) else ""), changes


def copy_set_file_to_tester_profiles(
    set_file: Path,
    terminal_data_dirs: list[Path],
    logger: RunLogger,
    symbol_map: dict[str, str] | None = None,
) -> None:
    mapped_text, changes = mapped_set_text_for_tester(set_file, symbol_map or {})
    copied_to: list[Path] = []
    for data_dir in terminal_data_dirs:
        for target_dir in (data_dir / "MQL5" / "Profiles" / "Tester", data_dir / "tester"):
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                destination = target_dir / set_file.name
                if mapped_text is None:
                    shutil.copy2(set_file, destination)
                else:
                    destination.write_text(mapped_text, encoding="utf-8", newline="\n")
                copied_to.append(destination)
            except OSError as exc:
                logger.write(f"AVISO: no pude copiar {set_file.name} a {target_dir}: {exc}")
    if copied_to:
        logger.write(f"Set file preparado: {set_file.name}")
        for change in changes:
            logger.write(f"  Symbol map aplicado al .set: {change}")
        for destination in copied_to:
            logger.write(f"  {destination}")


def normalize_expert_for_tester(expert_path: str) -> str:
    expert = expert_path.strip().replace("/", "\\")
    prefix = "Experts\\"
    if expert.lower().startswith(prefix.lower()):
        expert = expert[len(prefix):]
    if expert.lower().endswith((".ex5", ".mq5")):
        expert = expert[:-4]
    return expert


def load_template(template_path: Path) -> configparser.ConfigParser:
    if not template_path.exists():
        raise FileNotFoundError(f"No existe el .ini general: {template_path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(template_path, encoding="utf-8-sig")

    if "Tester" not in parser:
        raise ValueError(f"El .ini general debe tener una seccion [Tester]: {template_path}")

    return parser


def apply_symbol_suffix(symbol: str, suffix: str) -> str:
    symbol = symbol.strip()
    suffix = suffix.strip()
    if not symbol or not suffix or symbol.endswith(suffix):
        return symbol
    return f"{symbol}{suffix}"


def parse_symbol_map(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in re.split(r"[\n,;]+", value):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Correspondencia de simbolo invalida: {item}. Usa formato ORIGEN=DESTINO.")
        source, target = item.split("=", 1)
        source = normalize_set_symbol(source)
        target = target.strip()
        if not source or not target.strip():
            raise ValueError(f"Correspondencia de simbolo invalida: {item}. Usa formato ORIGEN=DESTINO.")
        mapping[source] = target
    return mapping


def apply_symbol_map(symbol: str, symbol_map: dict[str, str]) -> str:
    base_symbol = normalize_set_symbol(symbol)
    return symbol_map.get(base_symbol, symbol.strip())


FOREX_SYMBOLS = {
    a + b
    for a in ("AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD")
    for b in ("AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD")
    if a != b
}

SYMBOL_ALIASES = (
    (".JP225Cash", re.compile(r"\.?JP225CASH|(?:^|[^A-Z0-9])JP225(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("XAUUSD", re.compile(r"XAUUSD|GOLD|GOLDTRADE|GOLDREAPER|GOLDBOT|PHANTOM", re.IGNORECASE)),
    ("XAGUSD", re.compile(r"XAGUSD|SILVER", re.IGNORECASE)),
    ("XAUEUR", re.compile(r"XAUEUR", re.IGNORECASE)),
    ("XTIUSD", re.compile(r"XTIUSD", re.IGNORECASE)),
    ("BTCUSD", re.compile(r"BTCUSD|BTC|BITCOIN", re.IGNORECASE)),
    ("US100", re.compile(r"US100|USTEC|NAS100|NASDAQ|NAS_", re.IGNORECASE)),
    ("US30", re.compile(r"US30|DOW", re.IGNORECASE)),
    ("US500", re.compile(r"US500|SP500|SPX", re.IGNORECASE)),
    ("DAX", re.compile(r"(?:^|[^A-Z0-9])DAX(?:[^A-Z0-9]|$)|GER40|GER30", re.IGNORECASE)),
    ("CRUDEOIL", re.compile(r"CRUDEOIL|CRUDE|USOIL|WTI|OIL", re.IGNORECASE)),
)

EXPLICIT_SYMBOLS = FOREX_SYMBOLS | {
    symbol for symbol, _pattern in SYMBOL_ALIASES
} | {
    "USTEC",
    "US100",
    "US500",
    "US30",
    "DE40",
    "DAX",
    "BRENT",
    "BTCUSD",
    "ETHUSD",
    "XRPUSD",
    "ADAUSD",
    "DOGEUSD",
}

TIMEFRAME_PATTERNS = (
    ("M1", re.compile(r"(?:^|[^A-Z0-9])M1(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("M5", re.compile(r"(?:^|[^A-Z0-9])M5(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("M15", re.compile(r"(?:^|[^A-Z0-9])M15(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("M30", re.compile(r"(?:^|[^A-Z0-9])M30(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("H1", re.compile(r"(?:^|[^A-Z0-9])H1(?:[^A-Z0-9]|$)|SCALPH1", re.IGNORECASE)),
    ("H4", re.compile(r"(?:^|[^A-Z0-9])H4(?:[^A-Z0-9]|$)", re.IGNORECASE)),
    ("D1", re.compile(r"(?:^|[^A-Z0-9])D1(?:[^A-Z0-9]|$)|\(D\)|\(DAILY\)|DAILY|DAYTRADE|LONGTERM", re.IGNORECASE)),
    ("W1",  re.compile(r"(?:^|[^A-Z0-9])W1(?:[^A-Z0-9]|$)|WEEKLY", re.IGNORECASE)),
    ("MN", re.compile(r"(?:^|[^A-Z0-9])MN?(?:[^A-Z0-9]|$)|MONTHLY", re.IGNORECASE)),
)

TIMEFRAME_ENUM = {
    "1": "M1",
    "5": "M5",
    "15": "M15",
    "30": "M30",
    "16385": "H1",
    "16388": "H4",
    "16408": "D1",
    "32769": "W1",
    "49153": "MN",
}


def read_set_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_set_params(path: Path) -> dict[str, str]:
    params: dict[str, str] = {}
    for line in read_set_text(path).splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.strip()] = value.split("||", 1)[0].strip()
    return params


def normalize_set_symbol(symbol: str) -> str:
    return re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", symbol.strip()).upper()


def infer_symbol_from_set(set_file: Path, params: dict[str, str]) -> str:
    force_symbol = params.get("ForceSymbol", "").strip()
    if force_symbol:
        return force_symbol

    haystack = str(set_file).upper().replace("+", "_")
    for token in re.split(r"[^A-Z0-9]+", haystack):
        if token in EXPLICIT_SYMBOLS:
            return token
        match = re.search(r"(?:AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD){2}", token)
        if match and match.group(0) in FOREX_SYMBOLS:
            return match.group(0)

    for symbol, pattern in SYMBOL_ALIASES:
        if pattern.search(str(set_file)):
            return symbol
    return ""


def infer_period_from_set(set_file: Path, params: dict[str, str]) -> str:
    run_strategy = params.get("Run_Strategy", "").strip()
    strategy_timeframe_key = ""
    if run_strategy == "1":
        strategy_timeframe_key = "ST1_Timeframe"
    elif run_strategy == "2":
        strategy_timeframe_key = "VolTimeframe"

    if strategy_timeframe_key:
        period = TIMEFRAME_ENUM.get(params.get(strategy_timeframe_key, ""))
        if period:
            return period

    text = str(set_file)
    for period, pattern in TIMEFRAME_PATTERNS:
        if pattern.search(text):
            return period

    comment = params.get("EA_Comment", "")
    for period, pattern in TIMEFRAME_PATTERNS:
        if comment and pattern.search(comment):
            return period

    # Low-confidence fallback, used only when names/comments give no clue.
    for key in ("ST1_Timeframe", "VolTimeframe", "Entry_Timing", "ATR_Timeframe"):
        value = params.get(key, "")
        period = TIMEFRAME_ENUM.get(value)
        if period:
            return period
    return ""


def infer_period_from_path(set_file: Path) -> str:
    periods = {period.upper(): period for period in TIMEFRAME_ENUM.values()}
    for part in (set_file.parent.name, set_file.stem):
        for token in re.split(r"[^A-Z0-9]+", part.upper()):
            period = periods.get(token)
            if period:
                return period
    text = str(set_file)
    for period, pattern in TIMEFRAME_PATTERNS:
        if pattern.search(text):
            return period
    return ""


def infer_tester_fields_from_set(set_file: Path | None) -> dict[str, str]:
    if not set_file:
        return {}
    params = load_set_params(set_file)
    inferred: dict[str, str] = {}
    symbol = infer_symbol_from_set(set_file, params)
    period = infer_period_from_set(set_file, params)
    if symbol:
        inferred["Symbol"] = symbol
    if period:
        inferred["Period"] = period
    return inferred


def delete_test_artifacts(ini_path: Path, report_path: Path, logger: RunLogger) -> None:
    candidates = [ini_path]
    candidates.extend(REPORT_DIR.glob(f"{report_path.name}*"))
    for path in sorted(set(candidates)):
        try:
            if path.exists() and path.is_file():
                path.unlink()
                logger.write(f"  Borrado por validacion: {path}")
        except OSError as exc:
            logger.write(f"  Aviso: no pude borrar {path}: {exc}")


def validate_set_symbol(
    config: configparser.ConfigParser,
    set_file: Path | None,
    inferred_fields: dict[str, str],
    symbol_map: dict[str, str],
    symbol_suffix: str,
) -> None:
    if not set_file:
        return

    inferred_symbol = inferred_fields.get("Symbol", "").strip()
    if not inferred_symbol:
        if config["Tester"].get("Symbol", "").strip():
            return
        raise ValueError(
            f"No pude inferir el Symbol desde el set {set_file.name} "
            "y el template no tiene Symbol."
        )

    expected_symbol = apply_symbol_suffix(apply_symbol_map(inferred_symbol, symbol_map), symbol_suffix)
    actual_symbol = config["Tester"].get("Symbol", "").strip()
    if actual_symbol.upper() != expected_symbol.upper():
        raise ValueError(
            f"Symbol no coincide para {set_file.name}: esperado {expected_symbol}, "
            f"pero el tester.ini quedo con {actual_symbol or '(vacio)'}."
        )


def create_ini(
    expert_path: str,
    index: int,
    template: configparser.ConfigParser,
    set_file: Path | None = None,
    symbol_suffix: str = "",
    symbol_map: dict[str, str] | None = None,
    infer_tester_from_set: bool = False,
    prefer_set_path_timeframe: bool = False,
    tester_model: str = "",
    logger: RunLogger | None = None,
) -> tuple[Path, Path]:
    symbol_map = symbol_map or {}
    ea_name = safe_name(expert_path)
    if set_file:
        report_name = safe_name(set_file.stem)
    else:
        report_name = f"{index:03d}_{ea_name}"
    report_path = REPORT_DIR / report_name
    ini_path = CONFIG_DIR / f"{report_name}.ini"

    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_dict({section: dict(template[section]) for section in template.sections()})
    config["Tester"]["Expert"] = normalize_expert_for_tester(expert_path)
    inferred_fields = infer_tester_fields_from_set(set_file) if infer_tester_from_set else {}
    if set_file and infer_tester_from_set and prefer_set_path_timeframe:
        path_period = infer_period_from_path(set_file)
        if path_period:
            inferred_fields["Period"] = path_period
    use_template_tester_fields = bool(set_file and infer_tester_from_set and "Symbol" not in inferred_fields)
    if use_template_tester_fields:
        inferred_fields = {}
    for field, value in inferred_fields.items():
        config["Tester"][field] = value
    if "Symbol" in config["Tester"]:
        config["Tester"]["Symbol"] = apply_symbol_suffix(
            apply_symbol_map(config["Tester"]["Symbol"], symbol_map),
            symbol_suffix,
        )
    if set_file:
        config["Tester"]["ExpertParameters"] = set_file.name
    config["Tester"]["Report"] = report_name
    if tester_model.strip():
        config["Tester"]["Model"] = tester_model.strip()

    if infer_tester_from_set:
        if use_template_tester_fields and logger:
            template_symbol = config["Tester"].get("Symbol", "").strip() or "(vacio)"
            logger.write(
                f"AVISO: No pude inferir el Symbol desde {set_file.name}. "
                f"Se usara el template como esta: Symbol={template_symbol}, "
                f"Period={config['Tester'].get('Period', '').strip() or '(vacio)'}"
            )
        try:
            validate_set_symbol(config, set_file, inferred_fields, symbol_map, symbol_suffix)
        except ValueError:
            if logger:
                delete_test_artifacts(ini_path, report_path, logger)
            raise

    with ini_path.open("w", encoding="utf-8", newline="\n") as file:
        config.write(file, space_around_delimiters=False)
    return ini_path, report_path


def quote_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def find_report_files(report_path: Path, terminal_data_dirs: list[Path], mt5_path: Path) -> list[Path]:
    report_files = list(REPORT_DIR.glob(f"{report_path.name}*"))
    report_files.extend(BASE_DIR.glob(f"{report_path.name}*"))
    for directory in terminal_data_dirs:
        report_files.extend(directory.glob(f"{report_path.name}*"))
        report_files.extend((directory / "Reports").glob(f"{report_path.name}*"))
        report_files.extend((directory / "tester").glob(f"{report_path.name}*"))
        report_files.extend((directory / "MQL5" / "Files").glob(f"{report_path.name}*"))
    report_files.extend(install_dir_reports(report_path.name, mt5_path))
    return sorted(set(report_files))


def install_dir_reports(report_name: str, mt5_path: Path) -> list[Path]:
    candidates = []
    install_dir = mt5_path.parent
    for base in (
        install_dir,
        install_dir / "Reports",
        install_dir / "tester",
        install_dir / "MQL5" / "Files",
        Path(r"C:\Program Files\RoboForex MT5 Terminal"),
        Path(r"C:\Program Files\RoboForex MT5 Terminal\Reports"),
    ):
        if base.exists():
            candidates.extend(base.glob(f"{report_name}*"))
    return candidates


def copy_reports_to_project(report_files: list[Path], logger: RunLogger) -> list[Path]:
    copied: list[Path] = []
    local_sources: list[Path] = []
    external_sources: list[Path] = []
    for source in report_files:
        destination = REPORT_DIR / source.name
        if source.resolve() == destination.resolve():
            local_sources.append(source)
        else:
            external_sources.append(source)
    for source in external_sources:
        destination = REPORT_DIR / source.name
        shutil.copy2(source, destination)
        copied.append(destination)
        logger.write(f"  Copiado a reports: {destination}")
        try:
            source.unlink()
            logger.write(f"  Reporte origen eliminado: {source}")
        except OSError as exc:
            logger.write(f"  Aviso: no pude eliminar reporte origen {source}: {exc}")
    copied_destinations = {path.resolve() for path in copied}
    for source in local_sources:
        if source.resolve() in copied_destinations:
            continue
        try:
            source.unlink()
            logger.write(f"  Reporte local previo eliminado: {source}")
        except OSError as exc:
            logger.write(f"  Aviso: no pude eliminar reporte local previo {source}: {exc}")
    return copied


def filter_fresh_report_files(report_files: list[Path], started_at: float, logger: RunLogger) -> list[Path]:
    fresh: list[Path] = []
    cutoff = started_at - 1.0
    for path in report_files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            fresh.append(path)
        else:
            logger.write(f"  Reporte viejo ignorado: {path}")
    return fresh


def delete_existing_report_files(report_path: Path, terminal_data_dirs: list[Path], mt5_path: Path, logger: RunLogger) -> None:
    suffixes = {".htm", ".html", ".xml", ".png", ".set"}
    for path in find_report_files(report_path, terminal_data_dirs, mt5_path):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        try:
            path.unlink()
            logger.write(f"  Reporte previo borrado: {path}")
        except OSError as exc:
            logger.write(f"  Aviso: no pude borrar reporte previo {path}: {exc}")


def log_ini_content(ini_path: Path, logger: RunLogger, prefix: list[str] | None = None) -> None:
    messages = list(prefix or [])
    messages.append("Contenido del .ini generado:")
    messages.extend(f"  {line}" for line in ini_path.read_text(encoding="utf-8-sig").splitlines())
    logger.write_many(messages)


def run_test(
    ini_path: Path,
    report_path: Path,
    settings: TesterSettings,
    dry_run: bool,
    logger: RunLogger,
    terminal_data_dirs: list[Path],
) -> int:
    command = [str(settings.mt5_path)]
    if settings.portable:
        command.append("/portable")
    command.append(f"/config:{ini_path}")
    log_ini_content(ini_path, logger, [
        "",
        f"Config: {ini_path}",
        f"Reporte esperado: {report_path}.*",
        f"Comando: {quote_command(command)}",
    ])

    if dry_run:
        return 0

    delete_existing_report_files(report_path, terminal_data_dirs, settings.mt5_path, logger)

    before = time.time()
    process = subprocess.Popen(command, creationflags=NO_WINDOW)
    exit_code = process.wait()
    elapsed = time.time() - before
    logger.write(f"MT5 termino con codigo: {exit_code}")
    logger.write(f"Duracion: {elapsed:.1f} segundos")

    time.sleep(settings.delay_seconds)

    report_files = filter_fresh_report_files(
        find_report_files(report_path, terminal_data_dirs, settings.mt5_path),
        before,
        logger,
    )
    if report_files:
        logger.write("Reportes encontrados:")
        for path in report_files:
            logger.write(f"  {path} ({path.stat().st_size} bytes)")
        copied_reports = copy_reports_to_project(report_files, logger)
        if not copied_reports:
            logger.write("ERROR: No quedo ningun reporte nuevo copiado a reports.")
            return 1
    else:
        logger.write("ERROR: No se encontro ningun reporte generado para este backtest.")
        logger.write("Revisa que el EA exista dentro de la carpeta MQL5 del terminal RoboForex y que el simbolo/fechas tengan datos.")
        return 1

    return exit_code


def terminal_data_dirs_for_profile(profile: TerminalProfile, settings: TesterSettings) -> list[Path]:
    dirs: list[Path] = []
    if settings.data_dir:
        dirs.append(settings.data_dir)
    fallback = terminal_data_dir_from_experts_dir(profile.experts_root)
    if fallback:
        dirs.append(fallback)
    return sorted(set(dirs))


def run_backtest_job(
    job: BacktestJob,
    profile: TerminalProfile,
    settings: TesterSettings,
    template: configparser.ConfigParser,
    args: argparse.Namespace,
    symbol_map: dict[str, str],
    logger: RunLogger,
    *,
    set_mode: bool,
) -> int:
    terminal_data_dirs = terminal_data_dirs_for_profile(profile, settings)
    expert = profile_expert_for_job(profile, job, set_mode)
    logger.write("")
    logger.write(
        f"[{profile.name}] Job #{job.index}: "
        f"{Path(expert).name if expert else '(perfil UBS)'}"
        + (f" | set={job.set_file.name}" if job.set_file else "")
    )
    try:
        ini_path, report_path = create_ini(
            expert,
            job.index,
            template,
            job.set_file,
            args.symbol_suffix,
            symbol_map,
            args.infer_tester_from_set,
            args.prefer_set_path_timeframe,
            args.model,
            logger,
        )
    except ValueError as exc:
        logger.write(f"[{profile.name}] ERROR: {exc}")
        return 1
    if job.set_file and not args.dry_run:
        copy_set_file_to_tester_profiles(job.set_file, terminal_data_dirs, logger, symbol_map)
    return run_test(ini_path, report_path, settings, args.dry_run, logger, terminal_data_dirs)


def run_jobs_parallel(
    jobs: list[BacktestJob],
    profiles: list[TerminalProfile],
    template: configparser.ConfigParser,
    args: argparse.Namespace,
    symbol_map: dict[str, str],
    logger: RunLogger,
    *,
    set_mode: bool,
) -> int:
    job_queue: queue.Queue[BacktestJob] = queue.Queue()
    for job in jobs:
        job_queue.put(job)

    def worker(profile: TerminalProfile) -> int:
        failures = 0
        settings = settings_from_profile(profile, args.delay)
        while True:
            try:
                job = job_queue.get_nowait()
            except queue.Empty:
                break
            try:
                exit_code = run_backtest_job(
                    job,
                    profile,
                    settings,
                    template,
                    args,
                    symbol_map,
                    logger,
                    set_mode=set_mode,
                )
            except Exception as exc:
                logger.write(f"[{profile.name}] ERROR inesperado: {exc}")
                exit_code = 1
            if exit_code != 0:
                failures += 1
            job_queue.task_done()
        return failures

    with ThreadPoolExecutor(max_workers=len(profiles)) as executor:
        return sum(future.result() for future in [executor.submit(worker, profile) for profile in profiles])


def ensure_directories() -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    REPORT_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def create_logger() -> RunLogger:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_{stamp}.log"
    return RunLogger(log_path)


def main() -> int:
    args = parse_args()
    template_path = Path(args.template).expanduser()
    mt5_path = find_mt5_path(args.mt5_path)
    portable = should_use_portable(mt5_path, args.portable)
    try:
        explicit_data_dir = terminal_data_dir_from_cli(args.data_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"ERROR: {exc}")
        return 1
    try:
        symbol_map = parse_symbol_map(args.symbol_map)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    data_dir = explicit_data_dir or (portable_terminal_data_dir(mt5_path) if portable else terminal_data_dir_from_origin(mt5_path))
    settings = TesterSettings(
        mt5_path=mt5_path,
        delay_seconds=args.delay,
        portable=portable,
        data_dir=data_dir,
    )

    ensure_directories()
    logger = create_logger()
    terminal_profiles: list[TerminalProfile] = []
    if args.multi_terminal:
        try:
            configured_profiles = load_terminal_profiles(
            Path(args.terminals_config).expanduser(),
            ignore_enabled=args.max_workers > 1,
        )
        except (OSError, ValueError) as exc:
            logger.write(f"ERROR: {exc}")
            return 1
        if not configured_profiles:
            logger.write("ERROR: no hay terminales habilitadas en la configuracion multiterminal.")
            return 1
        worker_limit = args.max_workers if args.max_workers > 0 else len(configured_profiles)
        terminal_profiles = configured_profiles[: max(1, min(worker_limit, len(configured_profiles)))]

    experts_dir = (
        Path(args.experts_dir).expanduser()
        if args.experts_dir
        else (terminal_profiles[0].experts_root if terminal_profiles else load_experts_root())
    )
    set_dir = Path(args.set_dir).expanduser() if args.set_dir else None
    set_files = load_set_files(set_dir, args.set_file, recursive=args.recursive)
    if (set_dir or args.set_file) and not set_files:
        logger.write("ERROR: no se encontraron set files para testear.")
        logger.write(f"  Origen consultado: {set_dir if set_dir else 'argumentos --set-file'}")
        logger.write(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
        return 1
    if set_files and not args.expert and not args.multi_terminal:
        logger.write("ERROR: para testear multiples set files debes indicar un EA con --expert.")
        return 1
    if args.expert:
        experts = [expert_from_cli_value(args.expert, experts_dir)]
    elif set_files and args.multi_terminal:
        experts = [""]
    else:
        experts = (
            load_experts_from_dir(experts_dir, recursive=args.recursive, allow_sources=args.dry_run)
            if experts_dir
            else load_experts()
        )
    if not experts:
        source = experts_dir if experts_dir else EXPERTS_FILE
        logger.write("")
        logger.write("ERROR: no se encontraron Expert Advisors para backtestear.")
        logger.write(f"  Origen consultado: {source}")
        logger.write(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
        if experts_dir:
            logger.write("  Revisa que la ruta exista y contenga archivos .ex5.")
            try:
                if experts_dir.exists() and experts_dir.is_dir():
                    subdirs = [d for d in experts_dir.iterdir() if d.is_dir()][:10]
                    if subdirs:
                        logger.write(f"  Subcarpetas detectadas ({len(subdirs)}):")
                        for d in subdirs:
                            logger.write(f"    - {d.name}")
            except OSError:
                pass
        else:
            logger.write(f"  Revisa que {EXPERTS_FILE.name} liste rutas validas.")
        return 1

    if not args.multi_terminal and not settings.mt5_path.exists() and not args.dry_run:
        logger.write(f"No encuentro MT5 en: {settings.mt5_path}")
        logger.write(
            f"Indica la ruta con --mt5-path o define {MT5_TERMINAL_ENV[0]} en el entorno o en .env"
        )
        return 1

    if not args.multi_terminal and not args.dry_run and not args.skip_running_check:
        running = find_matching_running_terminals(settings.mt5_path)
        if running:
            logger.write("ERROR: RoboForex MT5 ya esta abierto.")
            for process in running:
                logger.write(f"  PID {process['pid']}: {process['path']}")
            logger.write("Cierra MT5 completamente y vuelve a ejecutar el script.")
            logger.write("MT5 puede ignorar /config si ya existe una instancia abierta con la misma carpeta de datos.")
            return RUNNING_TERMINAL_EXIT_CODE

    template = load_template(template_path)
    if args.from_date.strip():
        template.setdefault("Tester", {})
        template["Tester"]["FromDate"] = args.from_date.strip()
    if args.to_date.strip():
        template.setdefault("Tester", {})
        template["Tester"]["ToDate"] = args.to_date.strip()
    terminal_data_dirs = [] if args.multi_terminal else ([settings.data_dir] if settings.data_dir else [])
    if not args.multi_terminal and not terminal_data_dirs:
        terminal_data_dirs = discover_terminal_data_dirs(experts)
        if experts_dir:
            fallback_data_dir = terminal_data_dir_from_experts_dir(experts_dir)
            if fallback_data_dir:
                terminal_data_dirs = sorted(set([fallback_data_dir] + terminal_data_dirs))

    logger.write(f"Log: {logger.log_path}")
    logger.write(f"Ultimo log: {logger.last_log_path}")
    logger.write(f"Modo: {'DRY-RUN' if args.dry_run else 'REAL'}")
    logger.write(f"Proyecto: {BASE_DIR}")
    if args.multi_terminal:
        logger.write("MT5: perfiles multiterminal")
    else:
        logger.write(f"MT5: {settings.mt5_path}")
        logger.write(f"Portable: {'si' if settings.portable else 'no'}")
        if settings.data_dir:
            logger.write(f"Carpeta de datos MT5 seleccionada: {settings.data_dir}")
        else:
            logger.write("Aviso: no pude detectar la carpeta de datos exacta del terminal seleccionado.")
    logger.write(f"INI general: {template_path}")
    logger.write(f"Origen EAs: {experts_dir if experts_dir else EXPERTS_FILE}")
    logger.write(f"Expert Advisors: {len(experts)}")
    if set_files:
        logger.write(f"Set files: {len(set_files)}")
        logger.write(f"Origen sets: {set_dir if set_dir else 'argumentos --set-file'}")
    if not args.multi_terminal and terminal_data_dirs:
        logger.write("Carpetas de datos MT5 detectadas:")
        for directory in terminal_data_dirs:
            logger.write(f"  {directory}")
    elif not args.multi_terminal:
        logger.write("Aviso: no se detecto automaticamente la carpeta de datos MT5 con esos EAs.")

    raw_jobs: list[tuple[str, Path | None]] = (
        [(experts[0], set_file) for set_file in set_files]
        if set_files
        else [(expert, None) for expert in experts]
    )
    jobs = [BacktestJob(index, expert, set_file) for index, (expert, set_file) in enumerate(raw_jobs, start=1)]

    if args.multi_terminal:
        profile_errors = validate_terminal_profiles(
            terminal_profiles,
            jobs,
            set_mode=bool(set_files),
            dry_run=args.dry_run,
        )
        if profile_errors:
            logger.write("ERROR: configuracion multiterminal invalida.")
            for error in profile_errors[:30]:
                logger.write(f"  {error}")
            if len(profile_errors) > 30:
                logger.write(f"  ... y {len(profile_errors) - 30} error(es) mas")
            return 1
        if not args.dry_run and not args.skip_running_check:
            for profile in terminal_profiles:
                running = find_matching_running_terminals(profile.mt5_path)
                if running:
                    logger.write(f"ERROR: {profile.name} ya esta abierta.")
                    for process in running:
                        logger.write(f"  PID {process['pid']}: {process['path']}")
                    logger.write("Cierra esas terminales y vuelve a ejecutar.")
                    return RUNNING_TERMINAL_EXIT_CODE
    else:
        missing_experts = missing_experts_in_terminal_data_dirs(experts, terminal_data_dirs)
        if missing_experts and not args.dry_run:
            logger.write("ERROR: estos EAs no estan en la carpeta de datos que usara MT5:")
            for expert in missing_experts[:20]:
                logger.write(f"  {expert}")
            if len(missing_experts) > 20:
                logger.write(f"  ... y {len(missing_experts) - 20} mas")
            logger.write("Compila/copialos dentro de MQL5\\Experts del terminal seleccionado.")
            if settings.portable:
                logger.write(f"Terminal portable esperado: {settings.mt5_path.parent / 'MQL5' / 'Experts'}")
            return 1

    if args.multi_terminal:
        logger.write("Multiterminal: si")
        logger.write(f"Terminales habilitadas: {len(terminal_profiles)}")
        logger.write(f"Workers: {len(terminal_profiles)}")
        for profile in terminal_profiles:
            logger.write(f"  {profile.name}: {profile.mt5_path}")
            logger.write(f"    Experts: {profile.experts_root}")
            if profile.data_dir:
                logger.write(f"    Data: {profile.data_dir}")
            if profile.ubs_ex5_file:
                logger.write(f"    UBS EX5: {profile.ubs_ex5_file}")
    logger.write(f"Backtests en cola: {len(jobs)}")
    if args.multi_terminal:
        failures = run_jobs_parallel(
            jobs,
            terminal_profiles,
            template,
            args,
            symbol_map,
            logger,
            set_mode=bool(set_files),
        )
    else:
        failures = 0
        for job in jobs:
            try:
                ini_path, report_path = create_ini(
                    job.expert,
                    job.index,
                    template,
                    job.set_file,
                    args.symbol_suffix,
                    symbol_map,
                    args.infer_tester_from_set,
                    args.prefer_set_path_timeframe,
                    args.model,
                    logger,
                )
            except ValueError as exc:
                logger.write("")
                logger.write(f"ERROR: {exc}")
                failures += 1
                continue
            if job.set_file and not args.dry_run:
                copy_set_file_to_tester_profiles(job.set_file, terminal_data_dirs, logger, symbol_map)
            exit_code = run_test(ini_path, report_path, settings, args.dry_run, logger, terminal_data_dirs)
            if exit_code != 0:
                failures += 1

    if args.dry_run:
        logger.write("")
        if failures:
            logger.write(f"Dry-run terminado con {failures} test(s) fallido(s). No se abrio MT5.")
        else:
            logger.write("Dry-run terminado. Se generaron los .ini, no se abrio MT5.")
    elif failures:
        logger.write("")
        logger.write(f"Terminado con {failures} test(s) fallido(s).")
    else:
        logger.write("")
        logger.write("Todos los backtests han terminado.")

    logger.write(f"Log guardado en: {logger.log_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
