"""Informes del tester, journal y cache historica del modelo 4."""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from run_tests_base import (
    BASE_DIR,
    MT5_TESTER_ABORT_CODES,
    REPORT_DIR,
    HistoryCacheRotation,
)
from run_tests_symbols import normalize_set_symbol


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
        copied.append(source)
        logger.write(f"  Reporte ya estaba en reports: {source}")
    return copied

def filter_fresh_report_files(report_files: list[Path], started_at: float, logger: RunLogger | None) -> list[Path]:
    fresh: list[Path] = []
    cutoff = started_at - 1.0
    for path in report_files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            fresh.append(path)
        elif logger is not None:
            logger.write(f"  Reporte viejo ignorado: {path}")
    return fresh

def delete_existing_report_files(
    report_path: Path,
    terminal_data_dirs: list[Path],
    mt5_path: Path,
    logger: RunLogger,
    *,
    protected_set_name: str = "",
) -> None:
    suffixes = {".htm", ".html", ".xml", ".png", ".set", ".gif"}
    for path in find_report_files(report_path, terminal_data_dirs, mt5_path):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if protected_set_name and path.suffix.lower() == ".set" and path.name.lower() == protected_set_name.lower():
            logger.write(f"  Set activo conservado: {path}")
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

def tester_model_from_ini(ini_path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(ini_path, encoding="utf-8-sig")
    if not parser.has_section("Tester"):
        return ""
    return parser["Tester"].get("Model", "").strip()

def tester_symbol_from_ini(ini_path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(ini_path, encoding="utf-8-sig")
    if not parser.has_section("Tester"):
        return ""
    return parser["Tester"].get("Symbol", "").strip()

def signed_exit_code(exit_code: int | None) -> int | None:
    """Windows entrega los codigos de MT5 sin signo; el journal los escribe con signo."""
    if exit_code is None:
        return None
    code = int(exit_code)
    if code > 0x7FFFFFFF:
        code -= 0x100000000
    return code

def mt5_tester_abort(exit_code: int | None) -> tuple[str, bool] | None:
    """Motivo y reintentabilidad cuando MT5 no llego a arrancar el tester.

    Devuelve None para cualquier otro codigo (incluido 0), asi que el flujo
    normal no cambia."""
    return MT5_TESTER_ABORT_CODES.get(signed_exit_code(exit_code))

def load_universe_symbols(path: Path | None) -> set[str]:
    """Todos los simbolos que el broker ofrece, segun ``assets/<broker>_assets.ini``.

    Este fichero lo genera ``tools/sync_broker_universe.py`` desde el arbol de
    simbolos del servidor, asi que "no esta aqui" equivale a "el broker ya no lo
    ofrece".  Es una señal distinta de ``ubs_disabled_symbols_*.json``: ahi hay
    simbolos deshabilitados a mano o por veredicto ``no_history`` que SI existen
    y cuyos candidatos deben poder repararse."""
    if not path or not Path(path).exists():
        return set()
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8-sig")
    except (OSError, configparser.Error):
        return set()
    symbols: set[str] = set()
    for section in parser.sections():
        if section == "CommonAliases":
            continue
        for item in parser[section].get("symbols", "").split(","):
            value = item.strip()
            if value:
                symbols.add(value.upper())
    return symbols

def ini_symbol_missing_from_universe(ini_path: Path, universe_symbols: set[str]) -> str:
    """Devuelve el Symbol del .ini cuando el broker ya no lo ofrece, o "".

    Con el universo vacio (sin ``--symbol-universe`` o fichero ilegible) no se
    omite nada: es preferible gastar un arranque de MT5 que saltarse un backtest
    valido por un inventario ausente."""
    if not universe_symbols:
        return ""
    symbol = tester_symbol_from_ini(ini_path)
    if not symbol:
        return ""
    candidates = {symbol.upper(), normalize_set_symbol(symbol)}
    return "" if candidates & universe_symbols else symbol

def model4_history_cache_files(ini_path: Path, terminal_data_dirs: list[Path]) -> list[Path]:
    """Return all target-symbol HCC files whose refresh delays Model=4 safely.

    Some MT5 builds start the automatic tester about 400 ms after the first
    terminal synchronization, then briefly reconnect.  A Model=4 tick download
    started in that window is cancelled and MT5 writes a zero-bars/zero-ticks
    report.  M1 history synchronization survives the same reconnect.

    Rotating only the ToDate-year HCC is insufficient on terminals that can
    rebuild that single file from adjacent cached years in a few milliseconds.
    Rotate every HCC for the selected symbol so MT5 must complete the resilient
    M1 synchronization before it asks for real ticks.
    """
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(ini_path, encoding="utf-8-sig")
    if not parser.has_section("Tester"):
        return []
    tester = parser["Tester"]
    symbol = tester.get("Symbol", "").strip()
    if not symbol:
        return []

    candidates: set[Path] = set()
    for data_dir in terminal_data_dirs:
        bases_dir = data_dir / "bases"
        if not bases_dir.is_dir():
            continue
        try:
            server_dirs = [path for path in bases_dir.iterdir() if path.is_dir()]
        except OSError:
            continue
        for server_dir in server_dirs:
            symbol_history_dir = server_dir / "history" / symbol
            if not symbol_history_dir.is_dir():
                continue
            try:
                symbol_hcc_files = [
                    path
                    for path in symbol_history_dir.iterdir()
                    if path.is_file() and path.suffix.lower() == ".hcc"
                ]
            except OSError:
                continue
            candidates.update(symbol_hcc_files)
    return sorted(candidates)

def prepare_model4_history_preflight(
    ini_path: Path,
    terminal_data_dirs: list[Path],
    logger: RunLogger,
) -> list[HistoryCacheRotation]:
    """Temporarily rotate cached M1 history so MT5 reconnects before tick sync."""
    rotations: list[HistoryCacheRotation] = []
    candidates = model4_history_cache_files(ini_path, terminal_data_dirs)
    if not candidates:
        logger.write(
            "Preflight Model=4: no hay HCC del simbolo en cache; "
            "MT5 sincronizara M1 antes de descargar ticks."
        )
        return rotations

    for original in candidates:
        backup = original.with_name(
            f"{original.name}.model4-preflight-{os.getpid()}-{time.time_ns()}.bak"
        )
        try:
            original.replace(backup)
        except OSError as exc:
            logger.write(f"AVISO: no pude preparar la sincronizacion M1 de {original}: {exc}")
            continue
        rotations.append(HistoryCacheRotation(original=original, backup=backup))
        logger.write(
            f"Preflight Model=4: cache M1 rotada temporalmente para "
            f"{original.parent.name}/{original.stem}."
        )
    return rotations

def finish_model4_history_preflight(
    rotations: list[HistoryCacheRotation],
    logger: RunLogger,
) -> None:
    """Keep MT5's refreshed HCC, or restore the old cache if refresh failed."""
    for rotation in rotations:
        try:
            refreshed = rotation.original.is_file() and rotation.original.stat().st_size > 0
        except OSError:
            refreshed = False
        if refreshed:
            try:
                rotation.backup.unlink()
                logger.write(
                    f"Preflight Model=4 completado: cache M1 renovada "
                    f"({rotation.original.parent.name}/{rotation.original.stem})."
                )
            except OSError as exc:
                logger.write(f"AVISO: no pude borrar la copia temporal {rotation.backup}: {exc}")
            continue

        try:
            if rotation.original.exists():
                rotation.original.unlink()
            rotation.backup.replace(rotation.original)
            logger.write(f"Preflight Model=4: cache M1 anterior restaurada ({rotation.original}).")
        except OSError as exc:
            logger.write(
                f"ERROR: no pude restaurar la cache M1 {rotation.original} "
                f"desde {rotation.backup}: {exc}"
            )

def find_tester_journal_log(terminal_data_dirs: list[Path], min_mtime: float = 0.0) -> Path | None:
    """Return the most recently modified .log in <data_dir>/Tester/logs/ modified after min_mtime."""
    best: Path | None = None
    best_mtime = min_mtime
    for data_dir in terminal_data_dirs:
        logs_dir = data_dir / "Tester" / "logs"
        if not logs_dir.is_dir():
            continue
        for log_file in logs_dir.glob("*.log"):
            try:
                mtime = log_file.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best = log_file
            except OSError:
                continue
    return best

def read_tester_journal_tail(log_path: Path) -> tuple[str, int]:
    """Return (last_nonempty_line, file_byte_size). Reads only the last 4 KB."""
    try:
        size = log_path.stat().st_size
        if size == 0:
            return "", 0
        read_bytes = min(size, 4096)
        with log_path.open("rb") as f:
            f.seek(size - read_bytes)
            raw = f.read()
        # MT5 journal files are UTF-16 LE (with or without BOM) or UTF-8
        for enc in ("utf-16-le", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return (lines[-1] if lines else ""), size
    except OSError:
        return "", 0

def read_tester_journal_tail_text(log_path: Path, max_bytes: int = 65536) -> str:
    try:
        size = log_path.stat().st_size
        if size <= 0:
            return ""
        read_bytes = min(size, max(4096, int(max_bytes)))
        with log_path.open("rb") as f:
            f.seek(size - read_bytes)
            raw = f.read()
        for enc in ("utf-16-le", "utf-8"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, ValueError):
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""

def tester_journal_sidecar_path(report_path: Path) -> Path:
    return report_path.with_name(f"{report_path.stem}.mt5log.txt")

def write_tester_journal_sidecars(
    copied_reports: list[Path],
    terminal_data_dirs: list[Path],
    min_mtime: float,
    logger: RunLogger,
) -> None:
    journal = find_tester_journal_log(terminal_data_dirs, min_mtime=min_mtime)
    if journal is None:
        return
    text = read_tester_journal_tail_text(journal)
    if not text.strip():
        return
    payload = f"MT5 tester journal: {journal}\n\n{text}"
    for report in copied_reports:
        try:
            sidecar = tester_journal_sidecar_path(report)
            sidecar.write_text(payload, encoding="utf-8")
            logger.write(f"  Log tester guardado: {sidecar}")
        except OSError as exc:
            logger.write(f"  Aviso: no pude guardar log tester para {report.name}: {exc}")

def write_tester_journal_snapshot(
    report_path: Path,
    terminal_data_dirs: list[Path],
    min_mtime: float,
    logger: RunLogger,
    *,
    label: str,
) -> Path | None:
    """Persist watchdog evidence even when MT5 never produced a report."""
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label.strip()) or "watchdog"
    snapshot = report_path.with_name(f"{report_path.stem}.{safe_label}.mt5log.txt")
    journal = find_tester_journal_log(terminal_data_dirs, min_mtime=min_mtime)
    if journal is None:
        payload = "MT5 tester journal unavailable for this attempt.\n"
    else:
        text = read_tester_journal_tail_text(journal)
        payload = f"MT5 tester journal: {journal}\n\n{text}"
    try:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(payload, encoding="utf-8")
        logger.write(f"  Diagnostico watchdog guardado: {snapshot}")
        return snapshot
    except OSError as exc:
        logger.write(f"  Aviso: no pude guardar diagnostico watchdog {snapshot.name}: {exc}")
        return None

def _read_mt5_report_text(report: Path) -> str:
    try:
        raw = report.read_bytes()
    except OSError:
        return ""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

def _mt5_report_integer_metric(text: str, *labels: str) -> int | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})\s*:?\s*</td>\s*"
        rf"<td[^>]*>\s*(?:<b>)?\s*([0-9][0-9\s.,]*)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None

def model4_report_has_empty_tester_data(report_files: list[Path]) -> bool:
    """Return True when MT5 emitted a report shell without bars or ticks.

    MT5 can exit with code 0 after a transient connection loss while fetching
    real ticks.  The resulting report may even retain a plausible History
    Quality value, but Bars=0 and Ticks=0 means no test was executed.
    """
    main_report = next(
        (path for path in report_files if path.suffix.lower() in {".htm", ".html"}),
        None,
    )
    if main_report is None:
        return False
    text = _read_mt5_report_text(main_report)
    if not text:
        return False
    bars = _mt5_report_integer_metric(text, "Barras", "Bars")
    ticks = _mt5_report_integer_metric(text, "Ticks")
    return bars == 0 and ticks == 0

def fresh_report_signature(
    report_path: Path,
    terminal_data_dirs: list[Path],
    mt5_path: Path,
    min_mtime: float,
) -> tuple[tuple[str, int, int], ...]:
    """Describe fresh report artifacts once the main report exists."""
    artifacts: list[tuple[str, int, int]] = []
    has_main_report = False
    for path in find_report_files(report_path, terminal_data_dirs, mt5_path):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime + 1.0 < min_mtime or stat.st_size <= 0:
            continue
        if path.suffix.lower() in {".htm", ".html", ".xml"}:
            has_main_report = True
        artifacts.append((str(path), stat.st_size, stat.st_mtime_ns))
    if not has_main_report:
        return ()
    return tuple(sorted(artifacts))
