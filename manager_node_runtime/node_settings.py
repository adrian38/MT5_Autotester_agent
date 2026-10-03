"""Ajustes, universo y memoria que el nodo lee antes de lanzar nada."""
from __future__ import annotations

import configparser
import contextlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


CLEANUP_STAGE_SCRIPTS = {
    "cleanup_tester": "cleanOldTest.ps1",
    "cleanup_data": "cleanOlddata.ps1",
}
CLEANUP_STAGES = (*CLEANUP_STAGE_SCRIPTS, "cleanup_verify")

def read_settings(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def setting(parser: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    return parser.get(section, key, fallback=default).strip()


def setting_bool(parser: configparser.ConfigParser, section: str, key: str, default: bool = False) -> bool:
    try:
        return parser.getboolean(section, key, fallback=default)
    except ValueError:
        return default


def historical_cleanup_scripts(config: dict[str, Any], *, required: bool = True) -> dict[str, Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    candidate_dirs = [project / "scripts", project]
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        candidate_dirs.insert(0, Path(str(bundled_root)) / "scripts")
    for directory in candidate_dirs:
        scripts = {
            stage: directory / filename
            for stage, filename in CLEANUP_STAGE_SCRIPTS.items()
        }
        if all(path.is_file() for path in scripts.values()):
            return scripts
    if required:
        names = " / ".join(CLEANUP_STAGE_SCRIPTS.values())
        raise ValueError(f"No se encontraron {names} en la carpeta scripts del nodo")
    return {}


def cleanup_after_run_enabled(config: dict[str, Any], payload: dict[str, Any]) -> bool:
    available = bool(historical_cleanup_scripts(config, required=False))
    enabled = bool(payload.get("cleanup_after_run", available))
    if enabled:
        historical_cleanup_scripts(config)
    return enabled


def build_historical_cleanup_command(
    config: dict[str, Any], stage: str,
) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    if stage in CLEANUP_STAGE_SCRIPTS:
        script = historical_cleanup_scripts(config)[stage]
        return (
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script),
            ],
            project,
        )
    if stage != "cleanup_verify":
        raise ValueError(f"Etapa de limpieza desconocida: {stage}")
    verification = r"""
import os
from pathlib import Path

appdata = str(os.environ.get("APPDATA") or "").strip()
if not appdata:
    print("ERROR: APPDATA no esta disponible para verificar la limpieza.", flush=True)
    raise SystemExit(1)
metaquotes = Path(appdata) / "MetaQuotes"
paths = [metaquotes / "Tester"]
terminal_root = metaquotes / "Terminal"
if terminal_root.is_dir():
    for terminal in terminal_root.iterdir():
        if terminal.is_dir():
            paths.extend(terminal / name for name in ("tester", "Tester", "bases", "history"))
leftovers = []
for path in paths:
    if not path.is_dir():
        continue
    try:
        count = sum(1 for item in path.rglob("*") if item.is_file())
    except OSError:
        count = 1
    if count:
        leftovers.append((path, count))
if leftovers:
    print("ERROR: quedan datos historicos despues de limpiar:", flush=True)
    for path, count in leftovers[:20]:
        print(f" - {path} | {count} archivo(s)", flush=True)
    raise SystemExit(1)
print("Verificacion completada: no quedan datos historicos de MT5.", flush=True)
"""
    return [sys.executable, "-c", verification], project


def _universe_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    broker = str(config.get("broker") or "ROBOFOREX").strip().upper()
    account = str(config.get("account_type") or "ECN").strip().upper()
    return (project / "assets" / f"{broker.lower()}_assets.ini", project / "outputs" / f"ubs_disabled_symbols_{broker}_{account}.json")


def _load_universe_rows(config: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    assets_path, policy_path = _universe_paths(config)
    if not assets_path.is_file():
        raise ValueError(f"No existe el universo de activos: {assets_path}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(assets_path, encoding="utf-8-sig")
    aliases = {
        str(alias).strip().upper(): str(target).strip()
        for alias, target in (
            parser["CommonAliases"].items()
            if parser.has_section("CommonAliases")
            else []
        )
        if str(alias).strip() and str(target).strip()
    }
    reverse_aliases: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        reverse_aliases.setdefault(target.upper(), []).append(alias)
    policy: dict[str, Any] = {}
    if policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
            policy = loaded if isinstance(loaded, dict) else {"disabled": loaded if isinstance(loaded, list) else []}
        except (OSError, json.JSONDecodeError):
            policy = {}
    disabled = {str(value).strip().upper() for value in policy.get("disabled") or [] if str(value).strip()}
    seed_enabled = {str(value).strip().upper() for value in policy.get("seed_enabled_when_disabled") or [] if str(value).strip()} & disabled
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in parser.sections():
        if section == "CommonAliases":
            continue
        for raw in parser[section].get("symbols", "").split(","):
            symbol = raw.strip()
            canonical = aliases.get(symbol.upper(), symbol)
            canonical_key = canonical.upper()
            if not canonical or canonical_key in seen:
                continue
            seen.add(canonical_key)
            generation_enabled = canonical_key not in disabled
            rows.append({
                "symbol": canonical,
                "group": section,
                "aliases": sorted(reverse_aliases.get(canonical_key, [])),
                "generation_enabled": generation_enabled,
                "seeds_enabled": generation_enabled or canonical_key in seed_enabled,
            })
    rows.sort(key=lambda item: (str(item["group"]).casefold(), str(item["symbol"]).casefold()))
    return rows, disabled, seed_enabled


def declared_cli_options(script: Path) -> set[str] | None:
    """Opciones que `ubs_agent.py` declara, o None cuando no declara ninguna.

    None significa «no se puede saber», nunca «no soporta nada»: desde que el
    parser vive en `ubs_agent_cli.py` la fachada no contiene ni un literal
    `--opcion`, y exigirle uno daba por no soportada hasta la memoria por
    broker. El centinela es `--generations`, que declara toda rama con parser
    propio. Detalle en
    `ai_context/ubs_agent_cli_split_breaks_option_sniffing.md` del manager.
    """
    try:
        source = script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    options = set(re.findall(r"[\"'](--[a-z0-9-]+)[\"']", source, flags=re.IGNORECASE))
    return options if "--generations" in options else None


def memory_path(config: dict[str, Any], parser: configparser.ConfigParser) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    explicit = str(config.get("memory_path") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_absolute() else project / path
    broker = str(config.get("broker") or setting(parser, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(parser, "General", "ubs_account_type", "ECN")).upper()
    scoped = project / "outputs" / f"ubs_memory_{broker}_{account}.sqlite"
    legacy = project / "outputs" / "ubs_memory.sqlite"
    declared = declared_cli_options(project / "ubs_agent.py")
    return scoped if declared is None or "--broker" in declared else legacy


def resolve_generation_mode(
    config: dict[str, Any],
    payload: dict[str, Any],
    parser: configparser.ConfigParser | None = None,
) -> str:
    if parser is None:
        project = Path(str(config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        parser = read_settings(settings_path)
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    raw_mode = payload.get(
        "generation_mode",
        defaults.get("generation_mode", setting(parser, "General", "ubs_generation_mode", "production")),
    )
    mode = str(raw_mode).strip().lower()
    if mode not in {"production", "discovery"}:
        raise ValueError("generation_mode debe ser production o discovery")
    return mode


def stored_run_generation_mode(config: dict[str, Any], run_id: int) -> str | None:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    db_path = memory_path(config, read_settings(settings_path))
    if not db_path.is_file():
        return None
    try:
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=2)) as conn:
            if not _table_exists(conn, "runs"):
                return None
            row = conn.execute("select config_json from runs where id=?", (run_id,)).fetchone()
        if row is None:
            return None
        run_config = json.loads(str(row[0] or "{}"))
    except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(run_config, dict):
        return None
    generation = run_config.get("generation") if isinstance(run_config.get("generation"), dict) else {}
    run_args = run_config.get("args") if isinstance(run_config.get("args"), dict) else {}
    mode = str(generation.get("mode") or run_args.get("generation_mode") or "").strip().lower()
    return mode if mode in {"production", "discovery"} else None

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone()
    return row is not None
