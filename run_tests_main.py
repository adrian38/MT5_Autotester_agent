"""Pasos de la ejecucion por linea de comandos de los backtests."""
from __future__ import annotations

import argparse
from pathlib import Path

from mt5_env import MT5_TERMINAL_ENV
from run_tests_base import (
    BASE_DIR,
    EXPERTS_FILE,
    RUNNING_TERMINAL_EXIT_CODE,
    BacktestJob,
    TerminalProfile,
    TesterSettings,
)
from run_tests_logging import RunLogger
from run_tests_experts import (
    copy_set_file_to_tester_profiles,
    create_ini,
    expert_from_cli_value,
    load_experts,
    load_experts_from_dir,
    load_experts_root,
    load_set_files,
    load_template,
    missing_experts_in_terminal_data_dirs,
    validate_terminal_profiles,
)
from run_tests_reports import (
    delete_test_artifacts,
    ini_symbol_missing_from_universe,
    load_universe_symbols,
)
from run_tests_runner import run_jobs_parallel, run_test
from run_tests_symbols import load_symbol_suffix_universe, parse_symbol_map
from run_tests_terminals import (
    discover_terminal_data_dirs,
    find_matching_running_terminals,
    find_mt5_path,
    load_runner_tuning,
    load_terminal_profiles,
    log_runner_diagnostics,
    portable_terminal_data_dir,
    should_use_portable,
    terminal_data_dir_from_cli,
    terminal_data_dir_from_experts_dir,
    terminal_data_dir_from_origin,
)


def _apply_symbol_universe(args: argparse.Namespace) -> dict:
    """Carga el universo de simbolos y sus sufijos en los argumentos."""
    symbol_universe_path = (
        Path(args.symbol_universe).expanduser() if args.symbol_universe.strip() else None
    )
    args.symbol_suffix_universe = load_symbol_suffix_universe(
        symbol_universe_path,
        args.symbol_suffix,
        args.symbol_futures_suffix,
        args.symbol_shares_suffix,
    )
    args.universe_symbols = load_universe_symbols(symbol_universe_path)
    return args.symbol_suffix_universe


def _apply_runner_tuning(args: argparse.Namespace) -> tuple[int, int, int, int]:
    """Resuelve los tiempos del watchdog y los deja en los argumentos."""
    tuning = load_runner_tuning(
        Path(args.terminals_config).expanduser(),
        tester_kick_after=args.tester_kick_after,
        tester_stall_after=args.tester_stall_after,
        tester_max_runtime=args.tester_max_runtime,
        terminal_cooldown=args.terminal_cooldown,
    )
    (
        args.tester_kick_after_seconds,
        args.tester_stall_after_seconds,
        args.tester_max_runtime_seconds,
        args.terminal_cooldown_seconds,
    ) = tuning
    return tuning


def prepare_settings(args: argparse.Namespace) -> tuple | int:
    """Resuelve terminal, universo y tiempos; codigo de error si algo falla."""
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
    _apply_symbol_universe(args)
    kick_after, stall_after, max_runtime, cooldown = _apply_runner_tuning(args)
    data_dir = explicit_data_dir or (
        portable_terminal_data_dir(mt5_path) if portable else terminal_data_dir_from_origin(mt5_path)
    )
    settings = TesterSettings(
        mt5_path=mt5_path,
        delay_seconds=args.delay,
        portable=portable,
        data_dir=data_dir,
        tester_kick_after_seconds=kick_after,
        tester_stall_after_seconds=stall_after,
        tester_max_runtime_seconds=max_runtime,
        terminal_cooldown_seconds=cooldown,
    )
    return settings, symbol_map, template_path


def terminal_profiles_for_run(
    args: argparse.Namespace, logger: RunLogger
) -> list[TerminalProfile] | int:
    """Terminales habilitadas para el modo multiterminal."""
    if not args.multi_terminal:
        return []
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
    log_runner_diagnostics(logger, "AFTER_PROFILE_LOAD", terminal_profiles)
    return terminal_profiles


def _log_missing_experts(args: argparse.Namespace, experts_dir: Path | None, logger: RunLogger) -> None:
    """Explica donde se buscaron los EAs cuando no aparece ninguno."""
    source = experts_dir if experts_dir else EXPERTS_FILE
    logger.write("")
    logger.write("ERROR: no se encontraron Expert Advisors para backtestear.")
    logger.write(f"  Origen consultado: {source}")
    logger.write(f"  Modo recursivo: {'si' if args.recursive else 'no'}")
    if not experts_dir:
        logger.write(f"  Revisa que {EXPERTS_FILE.name} liste rutas validas.")
        return
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


def _load_experts(
    args: argparse.Namespace, experts_dir: Path | None, set_files: list[Path]
) -> list[str]:
    """EAs a backtestear segun los argumentos y el origen configurado."""
    if args.expert:
        return [expert_from_cli_value(args.expert, experts_dir)]
    if set_files and args.multi_terminal:
        return [""]
    if experts_dir:
        return load_experts_from_dir(
            experts_dir, recursive=args.recursive, allow_sources=args.dry_run
        )
    return load_experts()


def expert_sources(
    args: argparse.Namespace, terminal_profiles: list[TerminalProfile], logger: RunLogger
) -> tuple | int:
    """Sets y EAs del trabajo; codigo de error si falta alguno."""
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
    experts = _load_experts(args, experts_dir, set_files)
    if not experts:
        _log_missing_experts(args, experts_dir, logger)
        return 1
    return experts, experts_dir, set_dir, set_files


def terminal_guard(
    args: argparse.Namespace, settings: TesterSettings, logger: RunLogger
) -> int | None:
    """Comprueba que MT5 exista y que no haya una instancia ya abierta."""
    if not args.multi_terminal and not settings.mt5_path.exists() and not args.dry_run:
        logger.write(f"No encuentro MT5 en: {settings.mt5_path}")
        logger.write(
            f"Indica la ruta con --mt5-path o define {MT5_TERMINAL_ENV[0]} en el entorno o en .env"
        )
        return 1
    if args.multi_terminal or args.dry_run or args.skip_running_check:
        return None
    running = find_matching_running_terminals(settings.mt5_path)
    if not running:
        return None
    logger.write("ERROR: RoboForex MT5 ya esta abierto.")
    for process in running:
        logger.write(f"  PID {process['pid']}: {process['path']}")
    logger.write("Cierra MT5 completamente y vuelve a ejecutar el script.")
    logger.write("MT5 puede ignorar /config si ya existe una instancia abierta con la misma carpeta de datos.")
    return RUNNING_TERMINAL_EXIT_CODE


def load_run_template(args: argparse.Namespace, template_path: Path) -> dict:
    """Plantilla del tester con las fechas pedidas por linea de comandos."""
    template = load_template(template_path)
    if args.from_date.strip():
        template.setdefault("Tester", {})
        template["Tester"]["FromDate"] = args.from_date.strip()
    if args.to_date.strip():
        template.setdefault("Tester", {})
        template["Tester"]["ToDate"] = args.to_date.strip()
    return template


def terminal_data_dirs_for_run(
    args: argparse.Namespace, settings: TesterSettings, experts: list[str], experts_dir: Path | None
) -> list[Path]:
    """Carpetas de datos de MT5 que el trabajo va a tocar."""
    if args.multi_terminal:
        return []
    if settings.data_dir:
        return [settings.data_dir]
    terminal_data_dirs = discover_terminal_data_dirs(experts)
    if experts_dir:
        fallback_data_dir = terminal_data_dir_from_experts_dir(experts_dir)
        if fallback_data_dir:
            terminal_data_dirs = sorted(set([fallback_data_dir] + terminal_data_dirs))
    return terminal_data_dirs


def _log_terminal_header(args: argparse.Namespace, settings: TesterSettings, logger: RunLogger) -> None:
    """Terminal, modo portable y carpeta de datos elegidos."""
    if args.multi_terminal:
        logger.write("MT5: perfiles multiterminal")
        return
    logger.write(f"MT5: {settings.mt5_path}")
    logger.write(f"Portable: {'si' if settings.portable else 'no'}")
    if settings.data_dir:
        logger.write(f"Carpeta de datos MT5 seleccionada: {settings.data_dir}")
    else:
        logger.write("Aviso: no pude detectar la carpeta de datos exacta del terminal seleccionado.")


def _log_watchdog_header(settings: TesterSettings, logger: RunLogger) -> None:
    """Tiempos del watchdog con los que se vigilara cada backtest."""
    if settings.tester_kick_after_seconds > 0:
        logger.write(
            f"Model=4 auto-restart: kick_after={settings.tester_kick_after_seconds}s, "
            f"cooldown={settings.terminal_cooldown_seconds}s"
        )
    else:
        logger.write("Model=4 auto-restart: desactivado")
    logger.write(
        "Watchdog general: "
        f"stall_after={settings.tester_stall_after_seconds}s, "
        f"max_runtime={settings.tester_max_runtime_seconds}s"
    )


def log_run_header(
    args: argparse.Namespace, settings: TesterSettings, logger: RunLogger, template_path: Path,
    experts: list[str], experts_dir: Path | None, set_dir: Path | None, set_files: list[Path],
    terminal_data_dirs: list[Path],
) -> None:
    """Deja en el log todo lo que define esta ejecucion."""
    logger.write(f"Log: {logger.log_path}")
    logger.write(f"Ultimo log: {logger.last_log_path}")
    logger.write(f"Modo: {'DRY-RUN' if args.dry_run else 'REAL'}")
    logger.write(f"Proyecto: {BASE_DIR}")
    _log_terminal_header(args, settings, logger)
    logger.write(f"INI general: {template_path}")
    _log_watchdog_header(settings, logger)
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


def build_jobs(
    args: argparse.Namespace, experts: list[str], set_files: list[Path], logger: RunLogger
) -> list[BacktestJob]:
    """Cola de backtests: un set por trabajo, o un EA por trabajo."""
    raw_jobs: list[tuple[str, Path | None]] = (
        [(experts[0], set_file) for set_file in set_files]
        if set_files
        else [(expert, None) for expert in experts]
    )
    jobs = [
        BacktestJob(index, expert, set_file)
        for index, (expert, set_file) in enumerate(raw_jobs, start=1)
    ]
    logger.write(f"DIAG JOBS_READY jobs={len(jobs)} multi_terminal={'si' if args.multi_terminal else 'no'}")
    return jobs


def _validate_multiterminal(
    args: argparse.Namespace, terminal_profiles: list[TerminalProfile], jobs: list[BacktestJob],
    set_files: list[Path], logger: RunLogger,
) -> int | None:
    """Valida los perfiles y que ninguna terminal este ya abierta."""
    log_runner_diagnostics(logger, "BEFORE_MULTITERMINAL_VALIDATE", terminal_profiles)
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
    if args.dry_run or args.skip_running_check:
        return None
    for profile in terminal_profiles:
        running = find_matching_running_terminals(profile.mt5_path)
        if running:
            logger.write(f"ERROR: {profile.name} ya esta abierta.")
            for process in running:
                logger.write(f"  PID {process['pid']}: {process['path']}")
            logger.write("Cierra esas terminales y vuelve a ejecutar.")
            return RUNNING_TERMINAL_EXIT_CODE
    return None


def _validate_single_terminal(
    args: argparse.Namespace, settings: TesterSettings, experts: list[str],
    terminal_data_dirs: list[Path], logger: RunLogger,
) -> int | None:
    """Comprueba que los EAs esten en la carpeta de datos que usara MT5."""
    missing_experts = missing_experts_in_terminal_data_dirs(experts, terminal_data_dirs)
    if not missing_experts or args.dry_run:
        return None
    logger.write("ERROR: estos EAs no estan en la carpeta de datos que usara MT5:")
    for expert in missing_experts[:20]:
        logger.write(f"  {expert}")
    if len(missing_experts) > 20:
        logger.write(f"  ... y {len(missing_experts) - 20} mas")
    logger.write("Compila/copialos dentro de MQL5\\Experts del terminal seleccionado.")
    if settings.portable:
        logger.write(f"Terminal portable esperado: {settings.mt5_path.parent / 'MQL5' / 'Experts'}")
    return 1


def validate_jobs(
    args: argparse.Namespace, settings: TesterSettings, logger: RunLogger,
    terminal_profiles: list[TerminalProfile], jobs: list[BacktestJob], experts: list[str],
    set_files: list[Path], terminal_data_dirs: list[Path],
) -> int | None:
    """Comprobaciones previas a abrir MT5, segun el modo de ejecucion."""
    if args.multi_terminal:
        return _validate_multiterminal(args, terminal_profiles, jobs, set_files, logger)
    return _validate_single_terminal(args, settings, experts, terminal_data_dirs, logger)


def log_terminal_pool(
    args: argparse.Namespace, logger: RunLogger, terminal_profiles: list[TerminalProfile],
    jobs: list[BacktestJob],
) -> None:
    """Lista las terminales del pool y el tamano de la cola."""
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


def _run_single_job(
    job: BacktestJob, args: argparse.Namespace, settings: TesterSettings, template: dict,
    symbol_map: dict, terminal_data_dirs: list[Path], logger: RunLogger,
) -> int:
    """Prepara el .ini del trabajo y lanza su backtest; 1 si falla."""
    try:
        ini_path, report_path = create_ini(
            job.expert,
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
        logger.write("")
        logger.write(f"ERROR: {exc}")
        return 1
    retired_symbol = ini_symbol_missing_from_universe(
        ini_path, getattr(args, "universe_symbols", set())
    )
    if retired_symbol:
        logger.write("")
        logger.write(
            f"OMITIDO: Symbol={retired_symbol} ya no esta en el universo del "
            "broker; no se abre MT5."
        )
        delete_test_artifacts(ini_path, report_path, logger)
        return 0
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
    exit_code = run_test(
        ini_path,
        report_path,
        settings,
        args.dry_run,
        logger,
        terminal_data_dirs,
        job.set_file.name if job.set_file else "",
    )
    return 1 if exit_code != 0 else 0


def run_jobs(
    args: argparse.Namespace, settings: TesterSettings, symbol_map: dict, logger: RunLogger,
    template: dict, terminal_profiles: list[TerminalProfile], jobs: list[BacktestJob],
    set_files: list[Path], terminal_data_dirs: list[Path],
) -> int:
    """Ejecuta la cola de backtests y devuelve cuantos fallaron."""
    if args.multi_terminal:
        return run_jobs_parallel(
            jobs,
            terminal_profiles,
            template,
            args,
            symbol_map,
            logger,
            set_mode=bool(set_files),
        )
    return sum(
        _run_single_job(job, args, settings, template, symbol_map, terminal_data_dirs, logger)
        for job in jobs
    )


def log_run_summary(args: argparse.Namespace, failures: int, logger: RunLogger) -> None:
    """Cierre del log con el resultado de la ejecucion."""
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
