"""Expertos, ficheros .set y construccion del .ini del tester."""
from __future__ import annotations

import configparser
import re
import shutil
from pathlib import Path

from run_tests_base import (
    CONFIG_DIR,
    REPORT_DIR,
    EXPERTS_FILE,
    EXPERTS_ROOT_FILE,
    GENERATED_SET_ROOT_NAMES,
    GENERATED_SET_ROOT_PREFIXES,
    TEMPLATE_FILE,
    BacktestJob,
    TerminalProfile,
)
from run_tests_symbols import (
    apply_symbol_map,
    apply_symbol_suffix,
    infer_period_from_path,
    infer_period_from_set,
    infer_symbol_from_set,
    infer_tester_fields_from_set,
    load_symbol_suffix_universe,
    read_set_text,
    validate_set_symbol,
)
from run_tests_reports import delete_test_artifacts
from run_tests_terminals import (
    profile_data_dir,
    terminal_data_dir_from_experts_dir,
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

def job_uses_profile_ubs_expert(job: BacktestJob) -> bool:
    return not str(job.expert or "").strip()

def profile_expert_for_job(profile: TerminalProfile, job: BacktestJob, set_mode: bool) -> str:
    if set_mode and job_uses_profile_ubs_expert(job) and profile.ubs_ex5_file:
        return expert_from_cli_value(str(profile.ubs_ex5_file), profile.experts_root)
    return job.expert

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

def mapped_set_text_for_tester(
    set_file: Path,
    symbol_map: dict[str, str],
    symbol_suffix: str = "",
    futures_suffix: str = "",
    shares_suffix: str = "",
    suffix_universe: dict[str, str] | None = None,
) -> tuple[str | None, list[str]]:
    if not symbol_map and not any(value.strip() for value in (symbol_suffix, futures_suffix, shares_suffix)):
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
        mapped = apply_symbol_suffix(
            apply_symbol_map(current, symbol_map),
            symbol_suffix,
            futures_suffix,
            shares_suffix,
            suffix_universe,
        ).strip()
        if not current or mapped == current:
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
    symbol_suffix: str = "",
    futures_suffix: str = "",
    shares_suffix: str = "",
    suffix_universe: dict[str, str] | None = None,
) -> None:
    mapped_text, changes = mapped_set_text_for_tester(
        set_file,
        symbol_map or {},
        symbol_suffix,
        futures_suffix,
        shares_suffix,
        suffix_universe,
    )
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
            logger.write(f"  Symbol ajustado en .set: {change}")
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

TESTER_DEFAULTS: dict[str, str] = {
    "Deposit": "1000",
    "Currency": "EUR",
    "Leverage": "1:500",
    "Optimization": "0",
    "Visual": "0",
    "ReplaceReport": "1",
    "ShutdownTerminal": "1",
}

def ensure_tester_defaults(config: configparser.ConfigParser) -> None:
    if "Tester" not in config:
        config["Tester"] = {}
    for key, value in TESTER_DEFAULTS.items():
        if not config["Tester"].get(key, "").strip():
            config["Tester"][key] = value

def _report_artifact_paths(
    expert_path: str, index: int, set_file: Path | None,
) -> tuple[str, Path, Path]:
    ea_name = safe_name(expert_path)
    report_name = safe_name(set_file.stem) if set_file else f"{index:03d}_{ea_name}"
    return report_name, REPORT_DIR / report_name, CONFIG_DIR / f"{report_name}.ini"


def _prepare_tester_config(
    template: configparser.ConfigParser,
    expert_path: str,
    set_file: Path | None,
    infer_tester_from_set: bool,
    prefer_set_path_timeframe: bool,
) -> tuple[configparser.ConfigParser, dict[str, str], bool]:
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_dict({section: dict(template[section]) for section in template.sections()})
    ensure_tester_defaults(config)
    config["Tester"]["Expert"] = normalize_expert_for_tester(expert_path)
    inferred_fields = infer_tester_fields_from_set(set_file) if infer_tester_from_set else {}
    if set_file and infer_tester_from_set and prefer_set_path_timeframe:
        path_period = infer_period_from_path(set_file)
        if path_period:
            inferred_fields["Period"] = path_period
    use_template_fields = bool(set_file and infer_tester_from_set and "Symbol" not in inferred_fields)
    if use_template_fields:
        inferred_fields = {}
    for field, value in inferred_fields.items():
        config["Tester"][field] = value
    return config, inferred_fields, use_template_fields


def create_ini(
    expert_path: str,
    index: int,
    template: configparser.ConfigParser,
    set_file: Path | None = None,
    symbol_suffix: str = "",
    futures_suffix: str = "",
    shares_suffix: str = "",
    suffix_universe: dict[str, str] | None = None,
    symbol_map: dict[str, str] | None = None,
    infer_tester_from_set: bool = False,
    prefer_set_path_timeframe: bool = False,
    tester_model: str = "",
    logger: RunLogger | None = None,
) -> tuple[Path, Path]:
    symbol_map = symbol_map or {}
    report_name, report_path, ini_path = _report_artifact_paths(expert_path, index, set_file)
    config, inferred_fields, use_template_tester_fields = _prepare_tester_config(
        template, expert_path, set_file, infer_tester_from_set, prefer_set_path_timeframe,
    )
    if "Symbol" in config["Tester"]:
        config["Tester"]["Symbol"] = apply_symbol_suffix(
            apply_symbol_map(config["Tester"]["Symbol"], symbol_map),
            symbol_suffix,
            futures_suffix,
            shares_suffix,
            suffix_universe,
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
            validate_set_symbol(
                config,
                set_file,
                inferred_fields,
                symbol_map,
                symbol_suffix,
                futures_suffix,
                shares_suffix,
                suffix_universe,
            )
        except ValueError:
            if logger:
                delete_test_artifacts(ini_path, report_path, logger)
            raise

    with ini_path.open("w", encoding="utf-8", newline="\n") as file:
        config.write(file, space_around_delimiters=False)
    return ini_path, report_path


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
            if not dry_run:
                for job in jobs:
                    effective_expert = profile_expert_for_job(profile, job, set_mode)
                    if not effective_expert:
                        errors.append(f"{prefix}: falta EA efectivo para el job #{job.index}.")
                        break
                    candidate = expert_file_path(effective_expert, profile.experts_root)
                    if not candidate.exists():
                        errors.append(f"{prefix}: falta EA del job {effective_expert} en {profile.experts_root}")
                        break
            continue
        if dry_run:
            continue
        for job in jobs:
            candidate = expert_file_path(job.expert, profile.experts_root)
            if not candidate.exists():
                errors.append(f"{prefix}: falta EA {job.expert} en {profile.experts_root}")
                break
    return errors
