"""Busqueda de reportes en disco y lectura de los journals."""
from __future__ import annotations

import bisect
import json
import os
import re
from pathlib import Path

from run_tests import (
    apply_symbol_map,
    apply_symbol_suffix,
    normalize_set_symbol,
    tester_journal_sidecar_path,
)
from ubs.account import DEFAULT_BROKER, strip_broker_identity_suffix
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.score import ScoreResult
from ubs.tester_diagnostics import execution_failure_metadata, journal_symbol_missing
from ubs_agent_config import (
    BASE_DIR,
)


REPORT_SUFFIXES = (".htm", ".html", ".xml")
WATCHDOG_SUFFIX = ".mt5log.txt"

# `reports/` acumula cientos de miles de ficheros (los .png y .mt5log.txt de
# cada backtest) y nunca se purga. Un `glob()` ahi enumera el directorio entero:
# medido, 1.85 s por llamada con 470k ficheros, y la reconciliacion de seeds la
# invoca una vez por cada .set copiado. Se indexan los nombres una sola vez y se
# reindexan cuando cambia el mtime del directorio (Windows lo actualiza al
# crear/borrar entradas), asi que los reportes recien generados se ven igual.
_REPORTS_NAME_INDEX: dict[str, object] = {"signature": None, "reports": (), "watchdog": ()}


def _reports_name_index(reports_dir: Path) -> tuple[tuple, tuple]:
    """`(nombres de reporte, nombres de log watchdog)` como `(minuscula, real)`."""
    try:
        signature = reports_dir.stat().st_mtime_ns
    except OSError:
        return (), ()
    if _REPORTS_NAME_INDEX["signature"] == signature:
        return _REPORTS_NAME_INDEX["reports"], _REPORTS_NAME_INDEX["watchdog"]

    reports: list[tuple[str, str]] = []
    watchdog: list[tuple[str, str]] = []
    try:
        with os.scandir(reports_dir) as entries:
            for entry in entries:
                lowered = entry.name.lower()
                if lowered.endswith(REPORT_SUFFIXES):
                    bucket = reports
                elif lowered.endswith(WATCHDOG_SUFFIX):
                    bucket = watchdog
                else:
                    continue
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                bucket.append((lowered, entry.name))
    except OSError:
        return (), ()

    reports.sort()
    watchdog.sort()
    _REPORTS_NAME_INDEX["signature"] = signature
    _REPORTS_NAME_INDEX["reports"] = tuple(reports)
    _REPORTS_NAME_INDEX["watchdog"] = tuple(watchdog)
    return _REPORTS_NAME_INDEX["reports"], _REPORTS_NAME_INDEX["watchdog"]


def _indexed_names_with_prefix(index: tuple, prefix: str) -> list[str]:
    """Nombres reales cuyo nombre en minuscula empieza por `prefix` (ya minuscula)."""
    start = bisect.bisect_left(index, prefix, key=lambda item: item[0])
    matches: list[str] = []
    for lowered, name in index[start:]:
        if not lowered.startswith(prefix):
            break
        matches.append(name)
    return matches


def find_report_for_set(set_path: Path, *, min_mtime: float | None = None) -> Path | None:
    reports_dir = BASE_DIR / "reports"
    for suffix in REPORT_SUFFIXES:
        candidate = reports_dir / f"{set_path.stem}{suffix}"
        if candidate.is_file() and _report_is_fresh(candidate, min_mtime):
            return candidate
    reports_index, _watchdog_index = _reports_name_index(reports_dir)
    candidates = sorted(
        path
        for path in (
            reports_dir / name
            for name in _indexed_names_with_prefix(
                reports_index, f"{set_path.stem.lower()}."
            )
        )
        if _report_is_fresh(path, min_mtime)
    )
    return candidates[0] if candidates else None


def find_watchdog_snapshot_for_set(
    set_path: Path,
    *,
    min_mtime: float | None = None,
) -> Path | None:
    reports_dir = BASE_DIR / "reports"
    _reports_index, watchdog_index = _reports_name_index(reports_dir)
    candidates = [
        path
        for path in (
            reports_dir / name
            for name in _indexed_names_with_prefix(
                watchdog_index, f"{set_path.stem.lower()}.watchdog_attempt_"
            )
        )
        if _report_is_fresh(path, min_mtime)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def set_symbol_missing_in_terminal(
    set_path: Path,
    symbol: str,
    *,
    min_mtime: float | None = None,
) -> bool:
    """True si algun journal guardado del .set dice que el simbolo no existe.

    Cuando MT5 no llega a abrir el tester no hay reporte, pero el runner si deja
    el journal (`.tester_abort_attempt_N` o `.watchdog_attempt_N`). Es la unica
    evidencia de primera mano: el fichero de activos se mantiene a mano y sigue
    listando futuros ya vencidos.
    """
    if not str(symbol or "").strip():
        return False
    reports_dir = BASE_DIR / "reports"
    _reports_index, watchdog_index = _reports_name_index(reports_dir)
    for name in _indexed_names_with_prefix(watchdog_index, f"{set_path.stem.lower()}."):
        journal = reports_dir / name
        if not _report_is_fresh(journal, min_mtime):
            continue
        try:
            text = journal.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if journal_symbol_missing(text, symbol):
            return True
    return False


def report_matches_variant(
    variant: Variant,
    result: ScoreResult,
    symbol_map: dict[str, str],
    symbol_suffix: str = "",
    broker: object = DEFAULT_BROKER,
) -> tuple[bool, str]:
    report_symbol = normalize_set_symbol(strip_broker_identity_suffix(result.symbol, broker))
    target_symbol = normalize_set_symbol(
        strip_broker_identity_suffix(
            apply_symbol_suffix(apply_symbol_map(variant.target_symbol, symbol_map), symbol_suffix),
            broker,
        )
    )
    report_timeframe = str(result.timeframe or "").upper()
    target_timeframe = str(variant.target_period or "").upper()
    issues: list[str] = []
    if report_symbol != target_symbol:
        issues.append(f"symbol reporte={report_symbol or '(vacio)'} objetivo={target_symbol or '(vacio)'}")
    if report_timeframe != target_timeframe:
        issues.append(f"tf reporte={report_timeframe or '(vacio)'} objetivo={target_timeframe or '(vacio)'}")
    return not issues, "; ".join(issues)


def report_has_empty_tester_context(result: ScoreResult) -> bool:
    return result.trades <= 0 and (
        not str(result.symbol or "").strip()
        or str(result.timeframe or "").strip().upper() in {"", "M0"}
    )


def _no_history_metadata_from_matches(found_matches, missing_matches, tick_download_failed):
    """Arma los metadatos de falta de historico con lo hallado en el journal."""
    found = found_matches[-1] if found_matches else None
    missing = missing_matches[-1] if missing_matches else None
    recommendation = "desactivar simbolo y revisar historico del broker"
    if tick_download_failed:
        recommendation = (
            "reintentar tras estabilizar la conexion MT5; "
            "no asumir ausencia de historico del broker"
        )
    return found, missing, recommendation

def _trade_server_sync_only(
    found_matches, last_failure_position, missing_matches, text,
    tick_download_failed, trade_server_sync_pattern,
) -> bool:
    """El journal solo culpa a la sincronizacion con el servidor de trading."""
    trade_server_sync_matches = [
        match
        for match in trade_server_sync_pattern.finditer(text)
        if match.start() < last_failure_position
    ]
    if trade_server_sync_matches and not tick_download_failed:
        latest_trade_server_sync = trade_server_sync_matches[-1].start()
        sync_warning_in_current_attempt = (
            last_failure_position - latest_trade_server_sync <= 20_000
        )
        explicit_history_positions = [
            *(match.start() for match in found_matches),
            *(match.start() for match in missing_matches),
        ]
        if sync_warning_in_current_attempt and not any(
            position > latest_trade_server_sync for position in explicit_history_positions
        ):
            return True
    return False

def _tick_download_failure(escaped, failure_positions, text):
    """Detecta la descarga de ticks interrumpida y si hubo fallo posterior."""
    tick_download_failed = False
    download_matches = list(
        re.finditer(
            rf"{escaped}:\s+preliminary downloading of history ticks started",
            text,
            re.IGNORECASE,
        )
    )
    if download_matches:
        # Model=4 can fail before MT5 prints the requested date range. Associate
        # the generic stop message with the latest symbol-specific tick download.
        latest_download = download_matches[-1]
        download_tail = text[latest_download.start(): latest_download.start() + 2000]
        generic_failure = re.search(
            r"no history data,\s*stop testing",
            download_tail,
            re.IGNORECASE,
        )
        if generic_failure:
            failure_positions.append(latest_download.start() + generic_failure.start())
            tick_download_failed = True
    return tick_download_failed

def _dependent_symbol_failures(escaped, symbols, text):
    """Localiza los simbolos de conversion que impidieron el backtest."""
    attempt_pattern = re.compile(
        rf"{escaped},[^\r\n]*testing of Experts",
        re.IGNORECASE,
    )
    attempt_matches = list(attempt_pattern.finditer(text))
    dependent_failures: list[tuple[int, str]] = []
    if attempt_matches:
        attempt_start = attempt_matches[-1].start()
        attempt_text = text[attempt_start:]
        dependency_patterns = (
            re.compile(
                r"symbol\s+(?P<symbol>[A-Za-z0-9][A-Za-z0-9&+_.-]*)\s+history synchronization error",
                re.IGNORECASE,
            ),
            re.compile(
                r"cannot get history\s+(?P<symbol>[A-Za-z0-9][A-Za-z0-9&+_.-]*),[^\s]+",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?P<symbol>[A-Za-z0-9][A-Za-z0-9&+_.-]*):\s+no data synchronized",
                re.IGNORECASE,
            ),
            re.compile(
                r"no prices for symbol\s+(?P<symbol>[A-Za-z0-9][A-Za-z0-9&+_.-]*)",
                re.IGNORECASE,
            ),
        )
        target_symbols = {symbol.casefold() for symbol in symbols}
        for pattern in dependency_patterns:
            for match in pattern.finditer(attempt_text):
                failed_symbol = match.group("symbol")
                if failed_symbol.casefold() not in target_symbols:
                    dependent_failures.append(
                        (attempt_start + match.start(), failed_symbol)
                    )
    return dependent_failures

def _no_history_metadata(dependent_failures, found, missing, recommendation, sidecar, tick_download_failed):
    """Metadatos finales de falta de historico, con sus avisos y recomendacion."""
    metadata = {
        "reasons": ["no_history_data"],
        "no_score": True,
        "recommendation": recommendation,
        "log_source": str(sidecar),
        "history_available_from": found.group(1).strip() if found else "",
        "history_available_to": found.group(2).strip() if found else "",
        "history_requested_from": missing.group(1).strip() if missing else "",
        "history_requested_to": missing.group(2).strip().rstrip(".") if missing else "",
    }
    if dependent_failures:
        failed_history_symbols = sorted(
            {symbol for _position, symbol in dependent_failures},
            key=str.casefold,
        )
        metadata["reasons"] = ["no_history_data", "dependent_symbol_history"]
        metadata["failed_history_symbols"] = failed_history_symbols
        metadata["failure_type"] = "dependent_symbol_history"
        metadata["recommendation"] = (
            "revisar o descargar el historico del simbolo de conversion: "
            + ", ".join(failed_history_symbols)
        )
    if tick_download_failed:
        metadata["tick_download_failed"] = True
        metadata["retryable"] = True
        metadata["failure_type"] = "tick_history_sync"
    return metadata

def _no_history_patterns(escaped):
    """Expresiones del journal que delatan la falta de historico."""
    found_pattern = re.compile(
        rf"{escaped}:\s+found history data from\s+(.+?)\s+to\s+(.+?),\s+specified period is out of this range",
        re.IGNORECASE,
    )
    missing_pattern = re.compile(
        rf"{escaped}:\s+no history data from\s+(.+?)\s+to\s+(.+?)(?:\r?\n|$)",
        re.IGNORECASE,
    )
    cannot_get_pattern = re.compile(
        rf"cannot get history\s+{escaped},[^\s]+",
        re.IGNORECASE,
    )
    no_sync_pattern = re.compile(
        rf"{escaped}:\s+no data synchronized",
        re.IGNORECASE,
    )
    trade_server_sync_pattern = re.compile(
        r"not synchronized with trade server",
        re.IGNORECASE,
    )
    success_pattern = re.compile(
        rf"{escaped},[^:]+:.*\btest passed\b",
        re.IGNORECASE,
    )
    return cannot_get_pattern, found_pattern, missing_pattern, no_sync_pattern, success_pattern, trade_server_sync_pattern

def _journal_symbol_pattern(variant, symbol_map, symbol_suffix):
    """Alternativa de expresiones con las ortografias del simbolo objetivo."""
    raw_symbol = str(variant.target_symbol or "").strip()
    if not raw_symbol:
        return None
    broker_symbol = apply_symbol_suffix(
        apply_symbol_map(raw_symbol, symbol_map or {}),
        symbol_suffix,
    )
    symbols = sorted(
        {symbol for symbol in (raw_symbol, broker_symbol) if symbol},
        key=len,
        reverse=True,
    )
    escaped = "(?:" + "|".join(re.escape(symbol) for symbol in symbols) + ")"
    return escaped, symbols

def tester_log_no_history_metadata(
    report: Path,
    variant: Variant,
    symbol_map: dict[str, str] | None = None,
    symbol_suffix: str = "",
) -> dict[str, object] | None:
    sidecar = tester_journal_sidecar_path(report)
    if not sidecar.exists():
        return None
    try:
        text = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    escaped, symbols = _journal_symbol_pattern(variant, symbol_map, symbol_suffix)
    cannot_get_pattern, found_pattern, missing_pattern, no_sync_pattern, success_pattern, trade_server_sync_pattern = _no_history_patterns(escaped)
    # Shares quoted in a sub-unit (for example AXI's NationGrid+ in GBX) can
    # require a separate conversion symbol.  MT5 reports the failure against
    # that dependency, not against the tested symbol, so limiting detection to
    # ``variant.target_symbol`` incorrectly turns an empty technical report
    # into a scored ``no_trades`` result.
    dependent_failures = _dependent_symbol_failures(escaped, symbols, text)
    found_matches = list(found_pattern.finditer(text))
    missing_matches = list(missing_pattern.finditer(text))
    failure_positions = [
        *(match.start() for match in found_matches),
        *(match.start() for match in missing_matches),
        *(match.start() for match in cannot_get_pattern.finditer(text)),
        *(match.start() for match in no_sync_pattern.finditer(text)),
        *(position for position, _symbol in dependent_failures),
    ]
    tick_download_failed = _tick_download_failure(escaped, failure_positions, text)
    if not failure_positions:
        return None
    last_failure_position = max(failure_positions)
    success_matches = list(success_pattern.finditer(text))
    if success_matches and success_matches[-1].start() > last_failure_position:
        return None
    # ``no data synchronized`` / ``cannot get history`` are also emitted when
    # the terminal itself has lost synchronization with the trade server.  In
    # that case they are weak transport evidence, not proof that the broker has
    # no history for the symbol.  Keep explicit date-range evidence authoritative
    # and let empty-report handling classify the transport failure as a retryable
    # ``pending_tester_context``.
    if _trade_server_sync_only(
        found_matches, last_failure_position, missing_matches, text,
        tick_download_failed, trade_server_sync_pattern,
    ):
        return None
    found, missing, recommendation = _no_history_metadata_from_matches(found_matches, missing_matches, tick_download_failed)
    metadata = _no_history_metadata(dependent_failures, found, missing, recommendation, sidecar, tick_download_failed)
    return metadata


def tester_log_invalid_stops_metadata(
    report: Path,
    variant: Variant,
) -> dict[str, object] | None:
    """Describe rejected orders using the shared, truncated-journal detector."""
    return execution_failure_metadata(report, variant.target_symbol, variant.target_period)


def classify_zero_trade_robustness(
    report: Path,
    variant: Variant,
) -> tuple[str, dict[str, object]]:
    invalid_stops = tester_log_invalid_stops_metadata(report, variant)
    if invalid_stops:
        return "rejected", invalid_stops
    return "no_trades", {}


def record_score_with_metadata(
    memory: AgentMemory,
    set_path: Path,
    result: ScoreResult,
    status: str,
    report_path: Path,
    metadata: dict[str, object],
) -> None:
    memory.record_score(set_path, result, status, report_path)
    try:
        payload = json.loads(result.to_json())
    except (TypeError, ValueError):
        payload = {}
    payload.update(metadata)
    payload["score"] = None
    payload["accepted"] = False
    memory.conn.execute(
        "update candidates set metrics_json=?, score=null, accepted=null where set_path=?",
        (json.dumps(payload, ensure_ascii=True, sort_keys=True), str(set_path)),
    )
    memory.conn.commit()


def record_seed_score_with_metadata(
    memory: AgentMemory,
    seed: Seed,
    result: ScoreResult,
    status: str,
    report_path: Path,
    metadata: dict[str, object],
) -> None:
    memory.record_seed_score(seed, result, status, report_path)
    try:
        payload = json.loads(result.to_json())
    except (TypeError, ValueError):
        payload = {}
    payload.update(metadata)
    payload["score"] = None
    payload["accepted"] = False
    memory.conn.execute(
        "update seed_scores set metrics_json=?, score=null, accepted=null where seed_path=?",
        (json.dumps(payload, ensure_ascii=True, sort_keys=True), str(seed.path)),
    )
    memory.conn.commit()


def record_history_probe_status(
    memory: AgentMemory,
    variant: Variant,
    result: ScoreResult | None,
    status: str,
    report_path: Path | None,
    metadata: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "symbol": variant.target_symbol,
        "timeframe": variant.target_period,
        "history_probe": True,
        "reasons": [],
    }
    if result is not None:
        try:
            stored = json.loads(result.to_json())
            if isinstance(stored, dict):
                payload.update(stored)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    if metadata:
        payload.update(metadata)
    payload["score"] = None
    payload["accepted"] = False
    memory.conn.execute(
        """
        update candidates
        set report_path=?, score=null, accepted=null, metrics_json=?, status=?
        where set_path=?
        """,
        (
            str(report_path) if report_path else None,
            json.dumps(payload, ensure_ascii=True, sort_keys=True),
            status,
            str(variant.path),
        ),
    )
    memory.conn.commit()


def _report_is_fresh(path: Path, min_mtime: float | None) -> bool:
    if min_mtime is None:
        return True
    try:
        return path.stat().st_mtime >= min_mtime
    except OSError:
        return False
