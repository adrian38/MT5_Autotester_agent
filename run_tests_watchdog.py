"""Vigilancia del proceso del tester: atascos, reinicios y cierre."""
from __future__ import annotations

from dataclasses import dataclass, field
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

_FAILED_ATTEMPT_MESSAGE = "MT5 no cerro tras taskkill/terminate; se continua marcando el intento como fallido."


@dataclass
class _WatchdogLimits:
    """Umbrales y rutas con los que se vigila una ejecucion del tester."""

    kick_after_seconds: int = 0
    stall_after_seconds: int = 0
    max_runtime_seconds: int = 0
    tester_model: str = ""
    tester_log_dirs: list[Path] = field(default_factory=list)
    log_check_min_mtime: float = 0.0
    report_path: Path | None = None
    mt5_path: Path | None = None
    report_stable_seconds: int = REPORT_SAVE_STALL_SECONDS

    @property
    def use_model4_log_check(self) -> bool:
        """Detector de marcadores y descarga de ticks propio de Model=4."""
        return self.kick_after_seconds > 0 and bool(self.tester_log_dirs)

    @property
    def use_generic_stall_check(self) -> bool:
        """Deteccion de atasco por falta de progreso, valida para todo modelo."""
        return self.stall_after_seconds > 0 and bool(self.tester_log_dirs)

    @property
    def use_log_check(self) -> bool:
        """Si alguno de los dos detectores basados en log esta activo."""
        return self.use_model4_log_check or self.use_generic_stall_check

    @property
    def model_label(self) -> str:
        """Modelo del tester tal y como se nombra en los avisos."""
        return self.tester_model or "desconocido"


class _Mt5Watchdog:
    """Estado de la vigilancia de un proceso MT5 hasta que termina o se mata."""

    def __init__(self, process: subprocess.Popen, logger: RunLogger, limits: _WatchdogLimits) -> None:
        self.process = process
        self.logger = logger
        self.limits = limits
        self.started = time.time()
        self.next_alive_log = 30.0
        self.next_log_check = float(_LOG_CHECK_INTERVAL)
        self.last_log_size: int = -1
        self.last_log_path: Path | None = None
        self.last_log_line = ""
        self.prev_was_marker_stuck = False
        self.generic_stall_checks = 0
        self.last_progress_at = 0.0
        self.last_bases_signature: tuple[int, int, float] | None = None
        self.next_report_check = 0.0
        self.last_report_signature: tuple[tuple[str, int, int], ...] = ()
        self.report_stable_since: float | None = None

    def _terminate(self, timeout_message: str = _FAILED_ATTEMPT_MESSAGE) -> None:
        """Cierra el arbol de procesos y avisa si MT5 no se deja cerrar."""
        terminate_process_tree(self.process, self.logger)
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.logger.write(timeout_message)

    def _restart_result(self, elapsed: float) -> tuple[int, bool, float]:
        """Resultado de un intento que hubo que reiniciar."""
        code = self.process.returncode if self.process.returncode is not None else 1
        return code, True, elapsed

    def _check_report_stall(self, elapsed: float) -> tuple[int, bool, float] | None:
        """Cierra el terminal cuando el informe ya esta completo y no cambia."""
        limits = self.limits
        if not (
            limits.report_path is not None
            and limits.mt5_path is not None
            and limits.report_stable_seconds > 0
            and elapsed >= self.next_report_check
        ):
            return None
        report_signature = fresh_report_signature(
            limits.report_path,
            limits.tester_log_dirs or [],
            limits.mt5_path,
            limits.log_check_min_mtime,
        )
        now = time.time()
        if report_signature and report_signature == self.last_report_signature:
            if self.report_stable_since is None:
                self.report_stable_since = now
            stable_for = now - self.report_stable_since
            if stable_for >= limits.report_stable_seconds:
                self.logger.write(
                    f"MT5 no cerro, pero el informe lleva {stable_for:.0f}s completo y sin cambios; "
                    "se cierra el terminal y se conserva el resultado."
                )
                self._terminate("MT5 no cerro tras taskkill/terminate; se intentara conservar el informe.")
                return 0, False, elapsed
        else:
            self.report_stable_since = now if report_signature else None
            if report_signature:
                self.last_progress_at = elapsed
                self.generic_stall_checks = 0
            self.last_report_signature = report_signature
        self.next_report_check = elapsed + REPORT_SAVE_CHECK_INTERVAL
        return None

    def _bases_state(self) -> tuple[tuple[int, int, float], bool, bool]:
        """Progreso real de la descarga de ticks mirando los .tkc de bases/.

        MT5 bufferea el journal del tester mientras descarga, asi que el log
        puede no existir ni crecer durante minutos aunque la descarga avance.
        """
        if not self.limits.use_model4_log_check:
            return (0, 0, 0.0), False, True
        bases_signature = _tick_bases_progress_signature(self.limits.tester_log_dirs)
        bases_progress = self.last_bases_signature is not None and bases_signature != self.last_bases_signature
        first_bases_check = self.last_bases_signature is None
        self.last_bases_signature = bases_signature
        return bases_signature, bases_progress, first_bases_check

    def _journal_state(self, journal: Path, bases_progress: bool) -> tuple[bool, bool, str]:
        """Lee la cola del journal y decide si el tester esta atascado."""
        last_line, current_size = read_tester_journal_tail(journal)
        log_unchanged = (
            journal == self.last_log_path
            and current_size == self.last_log_size
            and last_line == self.last_log_line
        )
        marker_stuck_now = (
            self.limits.use_model4_log_check
            and _tester_log_is_stuck(last_line)
            and log_unchanged
            and not bases_progress
        )
        self.last_log_size = current_size
        self.last_log_path = journal
        self.last_log_line = last_line
        return not log_unchanged, marker_stuck_now, f"ultima linea log: {last_line!r}"

    def _no_journal_state(
        self, elapsed: float, bases_signature: tuple[int, int, float], bases_progress: bool, first_bases_check: bool
    ) -> tuple[bool, bool, str]:
        """Sin journal en disco: solo los .tkc dicen si MT5 sigue avanzando."""
        marker_stuck_now = (
            self.limits.use_model4_log_check
            and not bases_progress
            and not first_bases_check
            and elapsed >= self.limits.kick_after_seconds
        )
        if self.limits.use_model4_log_check and not marker_stuck_now:
            self.logger.write(
                f"MT5 activo ({elapsed:.0f}s): journal sin volcar; "
                f"ticks {'descargando' if bases_progress else 'sin datos aun'} "
                f"({bases_signature[0]} .tkc / {bases_signature[1] / 1048576:.1f} MB)."
            )
        return False, marker_stuck_now, "journal del tester sin volcar a disco"

    def _apply_marker_stuck(self, elapsed: float, marker_stuck_now: bool, stuck_detail: str) -> tuple[int, bool, float] | None:
        """Reinicia MT5 si el detector de Model=4 confirma el atasco dos veces."""
        if marker_stuck_now and self.prev_was_marker_stuck:
            self.logger.write(
                f"MT5 sigue activo tras {elapsed:.0f}s sin progreso de journal ni de descarga de ticks "
                f"(2 checks sin cambio); se reinicia para destrabar."
            )
            self.logger.write(f"  {stuck_detail}")
            self._terminate()
            return self._restart_result(elapsed)
        if marker_stuck_now:
            self.logger.write(
                f"MT5 sin progreso ({elapsed:.0f}s): {stuck_detail} — esperando {_LOG_CHECK_INTERVAL}s mas."
            )
            self.prev_was_marker_stuck = True
        else:
            if self.prev_was_marker_stuck:
                self.logger.write("MT5 volvio a avanzar (journal o descarga de ticks), reset detector stuck.")
            self.prev_was_marker_stuck = False
        return None

    def _apply_generic_stall(
        self, elapsed: float, journal_progress: bool, bases_progress: bool, stuck_detail: str
    ) -> tuple[int, bool, float] | None:
        """Reinicia MT5 cuando ningun modelo progresa durante el margen dado."""
        stalled_for = elapsed - self.last_progress_at
        generic_stalled = (
            self.limits.use_generic_stall_check
            and not journal_progress
            and not bases_progress
            and stalled_for >= self.limits.stall_after_seconds
        )
        if generic_stalled:
            self.generic_stall_checks += 1
            if self.generic_stall_checks >= 2:
                self.logger.write(
                    f"MT5 Model={self.limits.model_label} lleva {stalled_for:.0f}s sin progreso de journal "
                    f"ni de reporte (2 checks); se reinicia el proceso."
                )
                self.logger.write(f"  {stuck_detail}")
                self._terminate()
                return self._restart_result(elapsed)
            self.logger.write(
                f"MT5 Model={self.limits.model_label} sin progreso durante {stalled_for:.0f}s; "
                f"se confirmara en {_LOG_CHECK_INTERVAL}s."
            )
        elif journal_progress or bases_progress:
            self.generic_stall_checks = 0
        return None

    def _check_log(self, elapsed: float) -> tuple[int, bool, float] | None:
        """Pasada completa de los detectores basados en journal y en ticks."""
        bases_signature, bases_progress, first_bases_check = self._bases_state()
        journal = find_tester_journal_log(
            self.limits.tester_log_dirs, min_mtime=self.limits.log_check_min_mtime
        )
        if journal is not None:
            journal_progress, marker_stuck_now, stuck_detail = self._journal_state(journal, bases_progress)
        else:
            journal_progress, marker_stuck_now, stuck_detail = self._no_journal_state(
                elapsed, bases_signature, bases_progress, first_bases_check
            )
        if journal_progress or bases_progress:
            self.last_progress_at = elapsed
            self.generic_stall_checks = 0
        result = self._apply_marker_stuck(elapsed, marker_stuck_now, stuck_detail)
        if result is not None:
            return result
        result = self._apply_generic_stall(elapsed, journal_progress, bases_progress, stuck_detail)
        if result is not None:
            return result
        self.next_log_check = elapsed + _LOG_CHECK_INTERVAL
        return None

    def _check_fixed_timeout(self, elapsed: float) -> tuple[int, bool, float] | None:
        """Comportamiento original de timeout fijo cuando no hay logs que mirar."""
        if not (
            not self.limits.use_model4_log_check
            and self.limits.kick_after_seconds > 0
            and elapsed >= self.limits.kick_after_seconds
        ):
            return None
        self.logger.write(
            f"MT5 sigue activo tras {elapsed:.0f}s en Model=4; "
            "se reinicia el proceso lanzado para destrabar descarga de ticks."
        )
        self._terminate()
        return self._restart_result(elapsed)

    def _check_max_runtime(self, elapsed: float) -> tuple[int, bool, float] | None:
        """Failsafe absoluto por trabajo, independiente de cualquier detector."""
        if not (self.limits.max_runtime_seconds > 0 and elapsed >= self.limits.max_runtime_seconds):
            return None
        self.logger.write(
            f"MT5 Model={self.limits.model_label} supero el limite absoluto "
            f"de {self.limits.max_runtime_seconds}s; se termina el proceso."
        )
        self._terminate()
        return self._restart_result(elapsed)

    def run(self) -> tuple[int, bool, float]:
        """Espera a que MT5 termine, lo reinicia o lo cierra segun los detectores."""
        while True:
            exit_code = self.process.poll()
            elapsed = time.time() - self.started
            if exit_code is not None:
                return exit_code, False, elapsed

            result = self._check_report_stall(elapsed)
            if result is not None:
                return result

            if self.limits.use_log_check and elapsed >= self.next_log_check:
                result = self._check_log(elapsed)
            else:
                result = self._check_fixed_timeout(elapsed)
            if result is not None:
                return result

            result = self._check_max_runtime(elapsed)
            if result is not None:
                return result

            if elapsed >= self.next_alive_log:
                self.logger.write(f"MT5 sigue activo: {int(elapsed)}s esperando resultado...")
                self.next_alive_log += 30.0
            time.sleep(1)


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
    limits = _WatchdogLimits(
        kick_after_seconds=kick_after_seconds,
        stall_after_seconds=stall_after_seconds,
        max_runtime_seconds=max_runtime_seconds,
        tester_model=tester_model,
        tester_log_dirs=list(tester_log_dirs or []),
        log_check_min_mtime=log_check_min_mtime,
        report_path=report_path,
        mt5_path=mt5_path,
        report_stable_seconds=report_stable_seconds,
    )
    return _Mt5Watchdog(process, logger, limits).run()
