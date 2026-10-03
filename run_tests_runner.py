"""Lanzamiento, vigilancia y paralelizacion de los backtests de MT5."""
from __future__ import annotations

import os
import re
import sys
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from run_tests_parallel import run_parallel_jobs

from run_tests_base import (
    MODEL4_NO_HISTORY_EXIT_CODE,
    MODEL4_NO_HISTORY_RETRY_DELAY_SECONDS,
    NO_WINDOW,
    REPORT_SAVE_CHECK_INTERVAL,
    REPORT_SAVE_STALL_SECONDS,
    RUNNING_TERMINAL_EXIT_CODE,
    SKIPPED_SYMBOL_EXIT_CODE,
    TESTER_STUCK_MARKERS,
    BacktestJob,
    TerminalProfile,
    TesterSettings,
)
from run_tests_experts import (
    copy_set_file_to_tester_profiles,
    create_ini,
    job_uses_profile_ubs_expert,
    profile_expert_for_job,
)
from run_tests_logging import (
    _WATCHDOG_RESTART_LIMITER,
    _WATCHDOG_TERMINATE_LIMITER,
)
from run_tests_reports import (
    copy_reports_to_project,
    delete_test_artifacts,
    delete_existing_report_files,
    filter_fresh_report_files,
    find_report_files,
    find_tester_journal_log,
    finish_model4_history_preflight,
    fresh_report_signature,
    ini_symbol_missing_from_universe,
    install_dir_reports,
    load_universe_symbols,
    log_ini_content,
    model4_report_has_empty_tester_data,
    mt5_tester_abort,
    prepare_model4_history_preflight,
    read_tester_journal_tail,
    read_tester_journal_tail_text,
    signed_exit_code,
    tester_model_from_ini,
    tester_symbol_from_ini,
    write_tester_journal_sidecars,
    write_tester_journal_snapshot,
)
from run_tests_watchdog import wait_for_mt5_process
from run_tests_terminals import (
    TerminalStillRunningError,
    log_runner_diagnostics,
    settings_from_profile,
    terminal_data_dirs_for_profile,
    wait_for_terminal_release,
)


def quote_command(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


@dataclass
class _AttemptOutcome:
    """Resultado de una pasada: codigo final, o reintento con su etiqueta."""

    exit_code: int | None = None
    retry_label: str = ""


class _TestRunner:
    """Lanza MT5 sobre un trabajo y recoge su reporte, con un reintento."""

    # MT5 can exit without producing a fresh report for every tester model.  It
    # happens independently of the watchdog (for example when a terminal
    # silently ignores /config after a previous job).  Reserve one retry for
    # that false-success path as well as for Model=4 empty history and watchdog
    # restarts.
    MAX_ATTEMPTS = 2

    def __init__(
        self, ini_path: Path, report_path: Path, settings: TesterSettings, logger: RunLogger,
        terminal_data_dirs: list[Path], protected_set_name: str,
    ) -> None:
        self.ini_path = ini_path
        self.report_path = report_path
        self.settings = settings
        self.logger = logger
        self.terminal_data_dirs = terminal_data_dirs
        self.protected_set_name = protected_set_name
        self.command = [str(settings.mt5_path)]
        if settings.portable:
            self.command.append("/portable")
        self.command.append(f"/config:{ini_path}")
        self.tester_model = ""
        self.real_tick_model = False
        self.before = 0.0
        self.restarted = False

    @property
    def model_label(self) -> str:
        """Modelo del tester tal y como se nombra en los avisos."""
        return self.tester_model or "desconocido"

    def _launch(self, attempt: int) -> tuple[int, bool, float]:
        """Arranca MT5, lo vigila y espera a que libere el terminal."""
        settings = self.settings
        logger = self.logger
        kick_after_seconds = settings.tester_kick_after_seconds if self.real_tick_model else 0
        self.before = time.time()
        logger.write(f"DIAG MT5_POPEN_BEFORE mt5={settings.mt5_path} attempt={attempt}/{self.MAX_ATTEMPTS}")
        process = subprocess.Popen(self.command, creationflags=NO_WINDOW)
        logger.write(
            f"DIAG MT5_POPEN_AFTER pid={process.pid} mt5={settings.mt5_path} "
            f"attempt={attempt}/{self.MAX_ATTEMPTS}"
        )
        attempt_kick_after = (
            kick_after_seconds if attempt < self.MAX_ATTEMPTS else max(0, kick_after_seconds * 2)
        )
        exit_code, restarted, elapsed = wait_for_mt5_process(
            process,
            logger,
            kick_after_seconds=attempt_kick_after,
            stall_after_seconds=settings.tester_stall_after_seconds,
            max_runtime_seconds=settings.tester_max_runtime_seconds,
            tester_model=self.tester_model,
            tester_log_dirs=self.terminal_data_dirs,
            log_check_min_mtime=self.before,
            report_path=self.report_path,
            mt5_path=settings.mt5_path,
        )
        # Popen.wait/poll only observes the original PID, not an updater's
        # successor. Do not restore history, retry or finish this job yet.
        try:
            wait_for_terminal_release(
                settings.mt5_path, logger,
                timeout=max(120, settings.tester_max_runtime_seconds),
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            raise TerminalStillRunningError(str(exc)) from exc
        return exit_code, restarted, elapsed

    def _run_mt5(self, attempt: int) -> tuple[int, bool, float]:
        """Prepara el historico de Model=4, lanza MT5 y lo deja como estaba."""
        delete_existing_report_files(
            self.report_path,
            self.terminal_data_dirs,
            self.settings.mt5_path,
            self.logger,
            protected_set_name=self.protected_set_name,
        )
        history_rotations = (
            prepare_model4_history_preflight(self.ini_path, self.terminal_data_dirs, self.logger)
            if self.real_tick_model
            else []
        )
        try:
            return self._launch(attempt)
        finally:
            finish_model4_history_preflight(history_rotations, self.logger)

    def _snapshot(self, attempt: int, label: str) -> None:
        """Guarda el journal del tester como evidencia de esta pasada."""
        write_tester_journal_snapshot(
            self.report_path,
            self.terminal_data_dirs,
            self.before,
            self.logger,
            label=f"{label}_attempt_{attempt}",
        )

    def _tester_abort(self, exit_code: int, attempt: int) -> int | None:
        """Traduce un cierre de MT5 sin tester; 1 si no admite reintento.

        MT5 puede cerrarse sin reporte porque ni siquiera arranco el tester.  El
        motivo esta en su codigo de salida y en el journal; sin traducirlo el
        fallo se confunde con "no se genero reporte" y se reintenta a ciegas.
        """
        abort = mt5_tester_abort(exit_code)
        if abort is None:
            return None
        reason, transient = abort
        symbol = tester_symbol_from_ini(self.ini_path)
        self.logger.write(
            f"MT5 no arranco el tester: {reason} "
            f"(Symbol={symbol or '(sin dato)'}, codigo {signed_exit_code(exit_code)})."
        )
        self._snapshot(attempt, "tester_abort")
        if not transient:
            self.logger.write(
                f"DIAG TESTER_ABORT symbol={symbol} code={signed_exit_code(exit_code)} retry=no"
            )
            self.logger.write(
                "ERROR: backtest abortado por MT5 sin reintento: "
                f"{reason}. Revisa el universo del broker para {symbol or 'este simbolo'}."
            )
            return 1
        self.logger.write(
            f"DIAG TESTER_ABORT symbol={symbol} code={signed_exit_code(exit_code)} retry=si"
        )
        return None

    def _after_watchdog(self, attempt: int) -> _AttemptOutcome | None:
        """Decide si un reinicio del watchdog admite otra pasada."""
        if not self.restarted:
            return None
        if attempt < self.MAX_ATTEMPTS:
            return _AttemptOutcome(retry_label="Reinicio watchdog")
        self.logger.write(
            f"ERROR: MT5 Model={self.model_label} volvio a bloquearse "
            "tras el reintento automatico."
        )
        return _AttemptOutcome(exit_code=1)

    def _copy_reports(self, report_files: list[Path], exit_code: int, empty_tester_data: bool) -> int:
        """Copia los reportes al proyecto y resuelve el codigo de salida."""
        self.logger.write("Reportes encontrados:")
        for path in report_files:
            self.logger.write(f"  {path} ({path.stat().st_size} bytes)")
        copied_reports = copy_reports_to_project(report_files, self.logger)
        if not copied_reports:
            self.logger.write("ERROR: No quedo ningun reporte nuevo copiado a reports.")
            return 1
        write_tester_journal_sidecars(copied_reports, self.terminal_data_dirs, self.before, self.logger)
        if empty_tester_data:
            self.logger.write(
                "ERROR: MT5 Model=4 volvio a generar 0 barras / 0 ticks; "
                "el reporte se conserva como evidencia y el trabajo queda reintentable."
            )
            return MODEL4_NO_HISTORY_EXIT_CODE
        return exit_code

    def _no_report(self, attempt: int) -> _AttemptOutcome:
        """Sin reporte fresco: un reintento y, si tampoco, error explicado."""
        if attempt < self.MAX_ATTEMPTS:
            self.logger.write(
                f"No se encontro reporte en Model={self.model_label}; "
                "se reintentara una vez."
            )
            return _AttemptOutcome(retry_label="Reintento sin reporte")
        self.logger.write("ERROR: No se encontro ningun reporte generado para este backtest.")
        self.logger.write(
            "Revisa que el EA exista dentro de la carpeta MQL5 del terminal "
            f"({self.settings.mt5_path}) y que el simbolo/fechas tengan datos."
        )
        return _AttemptOutcome(exit_code=1)

    def _collect_reports(self, attempt: int, exit_code: int) -> _AttemptOutcome:
        """Busca el reporte generado por esta pasada y lo conserva."""
        report_files = filter_fresh_report_files(
            find_report_files(self.report_path, self.terminal_data_dirs, self.settings.mt5_path),
            self.before,
            self.logger,
        )
        if not report_files:
            return self._no_report(attempt)
        empty_tester_data = (
            self.real_tick_model and model4_report_has_empty_tester_data(report_files)
        )
        if empty_tester_data and attempt < self.MAX_ATTEMPTS:
            self.logger.write(
                "MT5 Model=4 genero un reporte vacio (0 barras / 0 ticks); "
                "se considera un fallo tecnico de historico y se reintentara."
            )
            time.sleep(MODEL4_NO_HISTORY_RETRY_DELAY_SECONDS)
            return _AttemptOutcome()
        return _AttemptOutcome(
            exit_code=self._copy_reports(report_files, exit_code, empty_tester_data)
        )

    def _attempt(self, attempt: int) -> _AttemptOutcome:
        """Una pasada completa de MT5 sobre este trabajo."""
        exit_code, self.restarted, elapsed = self._run_mt5(attempt)
        self.logger.write(f"MT5 termino con codigo: {exit_code}")
        self.logger.write(f"Duracion: {elapsed:.1f} segundos")
        aborted = self._tester_abort(exit_code, attempt)
        if aborted is not None:
            return _AttemptOutcome(exit_code=aborted)
        if self.restarted:
            self._snapshot(attempt, "watchdog")
        if self.settings.terminal_cooldown_seconds > 0:
            self.logger.write(f"Cooldown MT5: {self.settings.terminal_cooldown_seconds}s")
            time.sleep(self.settings.terminal_cooldown_seconds)
        after_watchdog = self._after_watchdog(attempt)
        if after_watchdog is not None:
            return after_watchdog
        time.sleep(self.settings.delay_seconds)
        return self._collect_reports(attempt, exit_code)

    def run(self, dry_run: bool) -> int:
        """Ejecuta el backtest con su reintento y devuelve el codigo final."""
        log_ini_content(self.ini_path, self.logger, [
            "",
            f"Config: {self.ini_path}",
            f"Reporte esperado: {self.report_path}.*",
            f"Comando: {quote_command(self.command)}",
        ])
        if dry_run:
            return 0
        self.tester_model = tester_model_from_ini(self.ini_path)
        self.real_tick_model = self.tester_model == "4"
        retry_label = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            if attempt > 1:
                self.logger.write(
                    f"Reintentando MT5 Model={self.model_label} ({attempt}/{self.MAX_ATTEMPTS})."
                )
                if retry_label:
                    _WATCHDOG_RESTART_LIMITER.wait_for_turn(self.logger, retry_label)
            outcome = self._attempt(attempt)
            if outcome.exit_code is not None:
                return outcome.exit_code
            retry_label = outcome.retry_label
        return 1


def run_test(
    ini_path: Path,
    report_path: Path,
    settings: TesterSettings,
    dry_run: bool,
    logger: RunLogger,
    terminal_data_dirs: list[Path],
    protected_set_name: str = "",
) -> int:
    """Lanza un backtest en MT5 y devuelve el codigo de salida del trabajo."""
    runner = _TestRunner(
        ini_path, report_path, settings, logger, terminal_data_dirs, protected_set_name
    )
    return runner.run(dry_run)


def _log_backtest_job(
    job: BacktestJob, profile: TerminalProfile, expert: str, logger: RunLogger
) -> None:
    logger.write("")
    logger.write(
        f"[{profile.name}] Job #{job.index}: "
        f"{Path(expert).name if expert else '(perfil UBS)'}"
        + (f" | set={job.set_file.name}" if job.set_file else "")
    )


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
    _log_backtest_job(job, profile, expert, logger)
    try:
        ini_path, report_path = create_ini(
            expert,
            job.index,
            template,
            job.set_file,
            args.symbol_suffix,
            args.symbol_futures_suffix,
            args.symbol_shares_suffix,
            getattr(args, "symbol_suffix_universe", {}),
            symbol_map,
            args.infer_tester_from_set,
            args.prefer_set_path_timeframe,
            args.model,
            logger,
        )
    except ValueError as exc:
        logger.write(f"[{profile.name}] ERROR: {exc}")
        return 1
    retired_symbol = ini_symbol_missing_from_universe(
        ini_path, getattr(args, "universe_symbols", set())
    )
    if retired_symbol:
        logger.write(
            f"[{profile.name}] OMITIDO: Symbol={retired_symbol} ya no esta en el universo "
            "del broker; no se abre MT5."
        )
        delete_test_artifacts(ini_path, report_path, logger)
        return SKIPPED_SYMBOL_EXIT_CODE
    if job.set_file and not args.dry_run:
        copy_set_file_to_tester_profiles(
            job.set_file,
            terminal_data_dirs,
            logger,
            symbol_map,
            args.symbol_suffix,
            args.symbol_futures_suffix,
            args.symbol_shares_suffix,
            getattr(args, "symbol_suffix_universe", {}),
        )
    protected_set_name = job.set_file.name if job.set_file else ""
    return run_test(ini_path, report_path, settings, args.dry_run, logger, terminal_data_dirs, protected_set_name)

def run_jobs_parallel(
    jobs: list[BacktestJob], profiles: list[TerminalProfile],
    template: configparser.ConfigParser, args: argparse.Namespace,
    symbol_map: dict[str, str], logger: RunLogger, *, set_mode: bool,
) -> int:
    def prepare_profile(profile: TerminalProfile) -> TesterSettings:
        return settings_from_profile(
            profile, args.delay, args.tester_kick_after_seconds,
            args.tester_stall_after_seconds, args.tester_max_runtime_seconds,
            args.terminal_cooldown_seconds,
        )

    def execute(job: BacktestJob, profile: TerminalProfile, settings: TesterSettings) -> int:
        return run_backtest_job(
            job, profile, settings, template, args, symbol_map, logger, set_mode=set_mode,
        )

    log_runner_diagnostics(logger, "PARALLEL_BEFORE", profiles)
    result = run_parallel_jobs(
        jobs, profiles, prepare_profile, execute, logger,
        success_exit_codes=(0, SKIPPED_SYMBOL_EXIT_CODE),
        fatal_exception=TerminalStillRunningError,
        retry_exit_code=MODEL4_NO_HISTORY_EXIT_CODE,
    )
    log_runner_diagnostics(logger, "PARALLEL_AFTER", profiles)
    return result
