"""Descubrimiento de terminales MT5, perfiles y ajustes del runner."""
from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from mt5_env import MT5_TERMINAL_ENV, terminal_path_from_env
from run_tests_base import (
    DEFAULT_MT5_PATHS,
    DEFAULT_TESTER_MAX_RUNTIME_SECONDS,
    DEFAULT_TESTER_STALL_AFTER_SECONDS,
    NO_WINDOW,
    TerminalProfile,
    TesterSettings,
)


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
    result = subprocess.run(command, capture_output=True, text=True, check=False, creationflags=NO_WINDOW, timeout=10)
    if result.returncode != 0:
        raise RuntimeError("No se pudo comprobar el cierre de MT5: fallo al consultar procesos")
    if not result.stdout.strip():
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

def log_runner_diagnostics(
    logger: RunLogger,
    label: str,
    profiles: list[TerminalProfile] | None = None,
) -> None:
    logger.write(
        f"DIAG {label}: runner_pid={os.getpid()} ppid={os.getppid()} "
        f"python_threads={threading.active_count()} logical_cpus={os.cpu_count()}"
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"Get-Process -Id {os.getpid()} | "
                    "Select-Object Id,CPU,PriorityClass,ProcessorAffinity,"
                    "@{Name='Threads';Expression={$_.Threads.Count}},Path | "
                    "ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            creationflags=NO_WINDOW,
        )
        if result.stdout.strip():
            logger.write(f"DIAG {label}: runner_process={result.stdout.strip()}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.write(f"DIAG {label}: runner_process_snapshot_error={exc}")

    if profiles:
        for profile in profiles:
            logger.write(
                f"DIAG {label}: profile={profile.name} mt5={profile.mt5_path} "
                f"data_dir={profile.data_dir} portable={'si' if profile.portable else 'no'}"
            )

    running = get_running_terminal_processes()
    logger.write(f"DIAG {label}: running_terminal64_count={len(running)}")
    for process in running[:30]:
        logger.write(
            f"DIAG {label}: terminal pid={process.get('pid', '')} "
            f"path={process.get('path', '')} cmd={process.get('command', '')}"
        )

def find_matching_running_terminals(mt5_path: Path) -> list[dict[str, str]]:
    target = str(mt5_path).lower()
    matches = []
    for process in get_running_terminal_processes():
        process_path = process["path"].lower()
        process_command = process["command"].lower()
        # LiveUpdate hands the job to another PID, whose executable is outside
        # the installation. Its /path identifies the terminal it will relaunch.
        update_target = re.search(r'/path:(?:"([^"]+)"|(\S+))', process_command)
        update_dir = (update_target.group(1) or update_target.group(2)) if update_target else ""
        if process_path == target or target in process_command or (
            update_dir and update_dir.rstrip("\\/") == str(mt5_path.parent).lower().rstrip("\\/")
        ):
            matches.append(process)
    return matches

class TerminalStillRunningError(RuntimeError):
    """The worker must not reuse a terminal whose previous job still owns it."""

def wait_for_terminal_release(mt5_path: Path, logger: RunLogger, timeout: float = 120) -> None:
    """Confirm exit across MT5 LiveUpdate PID handoffs without killing terminals."""
    deadline = time.monotonic() + timeout
    clear_checks = 0
    last_pids = None
    while True:
        running = find_matching_running_terminals(mt5_path)
        pids = tuple(item["pid"] for item in running)
        if running:
            clear_checks = 0
            if pids != last_pids:
                logger.write(f"Esperando cierre real MT5: {mt5_path}; PIDs={','.join(pids)}")
        else:
            clear_checks += 1
            # A quiet interval prevents a brief updater/relaunch gap from
            # releasing the profile to another candidate or pipeline stage.
            if clear_checks >= 2:
                return
        last_pids = pids
        if time.monotonic() >= deadline:
            raise TerminalStillRunningError(
                f"MT5 no libero el perfil en {timeout:g}s: {mt5_path}; PIDs={','.join(pids)}. "
                "Se detiene este worker sin reutilizar el terminal."
            )
        time.sleep(1)

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

def normalize_terminal_broker(value: object) -> str:
    text = str(value or "ROBOFOREX").strip().upper().replace(" ", "")
    aliases = {
        "ROBO": "ROBOFOREX",
        "ROBOFOREX": "ROBOFOREX",
        "IC": "ICTRADING",
        "ICTRADING": "ICTRADING",
        "ICMARKETS": "ICTRADING",
        "AXI": "AXI",
    }
    return aliases.get(text, "ROBOFOREX")

def parse_non_negative_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(0, parsed)

def load_runner_tuning(
    config_path: Path,
    *,
    tester_kick_after: int | None,
    tester_stall_after: int | None,
    tester_max_runtime: int | None,
    terminal_cooldown: int | None,
) -> tuple[int, int, int, int]:
    saved_kick_after = 30
    saved_stall_after = DEFAULT_TESTER_STALL_AFTER_SECONDS
    saved_max_runtime = DEFAULT_TESTER_MAX_RUNTIME_SECONDS
    saved_cooldown = 1
    if config_path.exists():
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(config_path, encoding="utf-8-sig")
        if parser.has_section("Multiterminal"):
            saved_kick_after = parse_non_negative_int(
                parser["Multiterminal"].get("tester_kick_after"),
                saved_kick_after,
            )
            saved_stall_after = parse_non_negative_int(
                parser["Multiterminal"].get("tester_stall_after"),
                saved_stall_after,
            )
            saved_max_runtime = parse_non_negative_int(
                parser["Multiterminal"].get("tester_max_runtime"),
                saved_max_runtime,
            )
            saved_cooldown = parse_non_negative_int(
                parser["Multiterminal"].get("terminal_cooldown"),
                saved_cooldown,
            )
    kick_after_value = saved_kick_after if tester_kick_after is None else parse_non_negative_int(tester_kick_after, 0)
    stall_after_value = (
        saved_stall_after
        if tester_stall_after is None
        else parse_non_negative_int(tester_stall_after, 0)
    )
    max_runtime_value = (
        saved_max_runtime
        if tester_max_runtime is None
        else parse_non_negative_int(tester_max_runtime, 0)
    )
    cooldown_value = saved_cooldown if terminal_cooldown is None else parse_non_negative_int(terminal_cooldown, 0)
    return kick_after_value, stall_after_value, max_runtime_value, cooldown_value

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
    target_broker = "ROBOFOREX"
    if parser.has_section("Multiterminal"):
        target_broker = normalize_terminal_broker(parser["Multiterminal"].get("broker", target_broker))
    profiles: list[TerminalProfile] = []
    sections = sorted(
        (section for section in parser.sections() if section.lower().startswith("terminal.")),
        key=terminal_section_sort_key,
    )
    for index, section in enumerate(sections, start=1):
        values = parser[section]
        if normalize_terminal_broker(values.get("broker", target_broker)) != target_broker:
            continue
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

def settings_from_profile(
    profile: TerminalProfile,
    delay_seconds: int,
    tester_kick_after_seconds: int,
    tester_stall_after_seconds: int,
    tester_max_runtime_seconds: int,
    terminal_cooldown_seconds: int,
) -> TesterSettings:
    return TesterSettings(
        mt5_path=profile.mt5_path,
        delay_seconds=delay_seconds,
        portable=profile.portable,
        data_dir=profile_data_dir(profile),
        tester_kick_after_seconds=tester_kick_after_seconds,
        tester_stall_after_seconds=tester_stall_after_seconds,
        tester_max_runtime_seconds=tester_max_runtime_seconds,
        terminal_cooldown_seconds=terminal_cooldown_seconds,
    )


def terminal_data_dirs_for_profile(profile: TerminalProfile, settings: TesterSettings) -> list[Path]:
    dirs: list[Path] = []
    if settings.data_dir:
        dirs.append(settings.data_dir)
    fallback = terminal_data_dir_from_experts_dir(profile.experts_root)
    if fallback:
        dirs.append(fallback)
    return sorted(set(dirs))
