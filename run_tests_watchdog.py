"""Vigilancia del proceso del tester: atascos, reinicios y cierre."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from run_tests_base import (
    NO_WINDOW,
    REPORT_SAVE_CHECK_INTERVAL,
    REPORT_SAVE_STALL_SECONDS,
    TESTER_STUCK_MARKERS,
)
from run_tests_logging import _WATCHDOG_TERMINATE_LIMITER
from run_tests_reports import (
    find_tester_journal_log,
    fresh_report_signature,
    read_tester_journal_tail,
    write_tester_journal_snapshot,
)


def _tester_log_is_stuck(last_line: str) -> bool:
    lower = last_line.lower()
    return any(marker.lower() in lower for marker in TESTER_STUCK_MARKERS)

def _tick_bases_progress_signature(terminal_data_dirs: list[Path]) -> tuple[int, int, float]:
    """Firma de progreso de la descarga de ticks: (n_archivos, bytes, mtime max)
    de los .tkc bajo <data>/bases/<server>/ticks/<symbol>/.

    MT5 bufferea el journal del tester durante la descarga de ticks: el log
    puede quedarse sin volcar a disco minutos enteros mientras la descarga
    avanza. Los .tkc en cambio crecen en tiempo real: si esta firma cambia
    entre chequeos hay progreso aunque el journal calle."""
    count = 0
    total = 0
    newest = 0.0
    for data_dir in terminal_data_dirs:
        bases = data_dir / "bases"
        if not bases.is_dir():
            continue
        for tkc in bases.glob("*/ticks/*/*.tkc"):
            try:
                st = tkc.stat()
            except OSError:
                continue
            count += 1
            total += int(st.st_size)
            if st.st_mtime > newest:
                newest = st.st_mtime
    return count, total, newest

def terminate_process_tree(process: subprocess.Popen, logger: RunLogger) -> None:
    if process.poll() is not None:
        return
    _WATCHDOG_TERMINATE_LIMITER.wait_for_turn(logger, "Cierre MT5")
    if sys.platform.startswith("win"):
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=NO_WINDOW,
        )
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            logger.write(f"taskkill no pudo cerrar MT5: {detail}")
    try:
        process.terminate()
    except OSError as exc:
        logger.write(f"No se pudo terminar MT5: {exc}")

_LOG_CHECK_INTERVAL = 10  # seconds between tester journal polls

def wait_for_mt5_process(
    process: subprocess.Popen,
    logger: RunLogger,
    *,
    kick_after_seconds: int = 0,
    stall_after_seconds: int = 0,
    max_runtime_seconds: int = 0,
    tester_model: str = "",
    tester_log_dirs: list[Path] | None = None,
    log_check_min_mtime: float = 0.0,
    report_path: Path | None = None,
    mt5_path: Path | None = None,
    report_stable_seconds: int = REPORT_SAVE_STALL_SECONDS,
) -> tuple[int, bool, float]:
    """Wait for MT5 to exit.

    Model=4 keeps its marker/tick-download detector through kick_after_seconds.
    Independently, stall_after_seconds applies to every model: if neither the
    tester journal nor a fresh report progresses for two checks after that idle
    window, MT5 is killed. max_runtime_seconds is an absolute per-job failsafe.

    If tester_log_dirs is empty/None, falls back to the original fixed-timeout
    behaviour (kill after kick_after_seconds of no exit).

    A fresh report that remains unchanged while MT5 is still alive identifies
    the terminal's occasional final "Save report" hang. The terminal is closed
    after the grace period and the already completed report is preserved.
    """
    started = time.time()
    next_alive_log = 30.0

    use_model4_log_check = kick_after_seconds > 0 and bool(tester_log_dirs)
    use_generic_stall_check = stall_after_seconds > 0 and bool(tester_log_dirs)
    use_log_check = use_model4_log_check or use_generic_stall_check
    next_log_check = float(_LOG_CHECK_INTERVAL)
    last_log_size: int = -1
    last_log_path: Path | None = None
    last_log_line = ""
    prev_was_marker_stuck = False
    generic_stall_checks = 0
    last_progress_at = 0.0
    last_bases_signature: tuple[int, int, float] | None = None
    next_report_check = 0.0
    last_report_signature: tuple[tuple[str, int, int], ...] = ()
    report_stable_since: float | None = None

    while True:
        exit_code = process.poll()
        elapsed = time.time() - started
        if exit_code is not None:
            return exit_code, False, elapsed

        if (
            report_path is not None
            and mt5_path is not None
            and report_stable_seconds > 0
            and elapsed >= next_report_check
        ):
            report_signature = fresh_report_signature(
                report_path,
                tester_log_dirs or [],
                mt5_path,
                log_check_min_mtime,
            )
            now = time.time()
            if report_signature and report_signature == last_report_signature:
                if report_stable_since is None:
                    report_stable_since = now
                stable_for = now - report_stable_since
                if stable_for >= report_stable_seconds:
                    logger.write(
                        f"MT5 no cerro, pero el informe lleva {stable_for:.0f}s completo y sin cambios; "
                        "se cierra el terminal y se conserva el resultado."
                    )
                    terminate_process_tree(process, logger)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        logger.write("MT5 no cerro tras taskkill/terminate; se intentara conservar el informe.")
                    return 0, False, elapsed
            else:
                report_stable_since = now if report_signature else None
                if report_signature:
                    last_progress_at = elapsed
                    generic_stall_checks = 0
                last_report_signature = report_signature
            next_report_check = elapsed + REPORT_SAVE_CHECK_INTERVAL

        if use_log_check and elapsed >= next_log_check:
            # Progreso real de descarga de ticks: MT5 bufferea el journal del
            # tester mientras descarga, asi que el log puede no existir o no
            # crecer durante minutos aunque la descarga avance. Los .tkc de
            # bases/ si crecen en tiempo real.
            if use_model4_log_check:
                bases_signature = _tick_bases_progress_signature(tester_log_dirs)
                bases_progress = last_bases_signature is not None and bases_signature != last_bases_signature
                first_bases_check = last_bases_signature is None
                last_bases_signature = bases_signature
            else:
                bases_signature = (0, 0, 0.0)
                bases_progress = False
                first_bases_check = True

            journal = find_tester_journal_log(tester_log_dirs, min_mtime=log_check_min_mtime)
            if journal is not None:
                last_line, current_size = read_tester_journal_tail(journal)
                is_stuck_line = _tester_log_is_stuck(last_line)
                log_unchanged = (
                    journal == last_log_path
                    and current_size == last_log_size
                    and last_line == last_log_line
                )
                journal_progress = not log_unchanged
                marker_stuck_now = (
                    use_model4_log_check
                    and is_stuck_line
                    and log_unchanged
                    and not bases_progress
                )
                stuck_detail = f"ultima linea log: {last_line!r}"
                last_log_size = current_size
                last_log_path = journal
                last_log_line = last_line
            else:
                # Sin journal volcado a disco: si los .tkc tampoco avanzan tras
                # el primer chequeo, MT5 esta colgado sin escribir nada.
                journal_progress = False
                marker_stuck_now = (
                    use_model4_log_check
                    and not bases_progress
                    and not first_bases_check
                    and elapsed >= kick_after_seconds
                )
                stuck_detail = "journal del tester sin volcar a disco"
                if use_model4_log_check and not marker_stuck_now:
                    logger.write(
                        f"MT5 activo ({elapsed:.0f}s): journal sin volcar; "
                        f"ticks {'descargando' if bases_progress else 'sin datos aun'} "
                        f"({bases_signature[0]} .tkc / {bases_signature[1] / 1048576:.1f} MB)."
                    )

            if journal_progress or bases_progress:
                last_progress_at = elapsed
                generic_stall_checks = 0

            if marker_stuck_now and prev_was_marker_stuck:
                logger.write(
                    f"MT5 sigue activo tras {elapsed:.0f}s sin progreso de journal ni de descarga de ticks "
                    f"(2 checks sin cambio); se reinicia para destrabar."
                )
                logger.write(f"  {stuck_detail}")
                terminate_process_tree(process, logger)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    logger.write("MT5 no cerro tras taskkill/terminate; se continua marcando el intento como fallido.")
                return process.returncode if process.returncode is not None else 1, True, elapsed

            if marker_stuck_now:
                logger.write(
                    f"MT5 sin progreso ({elapsed:.0f}s): {stuck_detail} — esperando {_LOG_CHECK_INTERVAL}s mas."
                )
                prev_was_marker_stuck = True
            else:
                if prev_was_marker_stuck:
                    logger.write("MT5 volvio a avanzar (journal o descarga de ticks), reset detector stuck.")
                prev_was_marker_stuck = False

            stalled_for = elapsed - last_progress_at
            generic_stalled = (
                use_generic_stall_check
                and not journal_progress
                and not bases_progress
                and stalled_for >= stall_after_seconds
            )
            if generic_stalled:
                generic_stall_checks += 1
                if generic_stall_checks >= 2:
                    model_label = tester_model or "desconocido"
                    logger.write(
                        f"MT5 Model={model_label} lleva {stalled_for:.0f}s sin progreso de journal "
                        f"ni de reporte (2 checks); se reinicia el proceso."
                    )
                    logger.write(f"  {stuck_detail}")
                    terminate_process_tree(process, logger)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        logger.write(
                            "MT5 no cerro tras taskkill/terminate; se continua marcando el intento como fallido."
                        )
                    return process.returncode if process.returncode is not None else 1, True, elapsed
                logger.write(
                    f"MT5 Model={tester_model or 'desconocido'} sin progreso durante {stalled_for:.0f}s; "
                    f"se confirmara en {_LOG_CHECK_INTERVAL}s."
                )
            elif journal_progress or bases_progress:
                generic_stall_checks = 0

            next_log_check = elapsed + _LOG_CHECK_INTERVAL

        elif not use_model4_log_check and kick_after_seconds > 0 and elapsed >= kick_after_seconds:
            # Fallback: original fixed-timeout when no log dirs available
            logger.write(
                f"MT5 sigue activo tras {elapsed:.0f}s en Model=4; "
                "se reinicia el proceso lanzado para destrabar descarga de ticks."
            )
            terminate_process_tree(process, logger)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.write("MT5 no cerro tras taskkill/terminate; se continua marcando el intento como fallido.")
            return process.returncode if process.returncode is not None else 1, True, elapsed

        if max_runtime_seconds > 0 and elapsed >= max_runtime_seconds:
            logger.write(
                f"MT5 Model={tester_model or 'desconocido'} supero el limite absoluto "
                f"de {max_runtime_seconds}s; se termina el proceso."
            )
            terminate_process_tree(process, logger)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                logger.write("MT5 no cerro tras taskkill/terminate; se continua marcando el intento como fallido.")
            return process.returncode if process.returncode is not None else 1, True, elapsed

        if elapsed >= next_alive_log:
            logger.write(f"MT5 sigue activo: {int(elapsed)}s esperando resultado...")
            next_alive_log += 30.0
        time.sleep(1)
