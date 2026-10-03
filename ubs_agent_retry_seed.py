"""Reintento de seeds sueltas, en tanda o de una en una."""
from __future__ import annotations

import argparse
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult
from ubs.seeds import seed_eval_filename, seed_from_path
from ubs_agent_config import (
    BASE_DIR,
    DEFAULT_OUTPUT,
)
from ubs_agent_evaluate import (
    copy_accepted,
    evaluate_seed_report,
    evaluate_variant,
    recreate_work_dir,
    remove_candidate_copies,
    remove_report_artifacts,
)
from ubs_agent_reports import (
    find_report_for_set,
)
from ubs_agent_sets import (
    copy_seed_for_backtest,
    record_invalid_seed,
    validate_seed_backtest_set,
    write_retry_set,
)
from ubs_agent_universe import (
    broker_universe_symbols,
    missing_report_status,
)
from ubs_agent_variants import (
    run_backtests,
)


def _retry_seed_paths(args: argparse.Namespace) -> list[Path]:
    """Rutas absolutas de las seeds que se piden reintentar."""
    source_paths = []
    for value in args.retry_seed_path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        source_paths.append(path.resolve())
    return source_paths


def _retry_seed_rejected(
    args: argparse.Namespace, memory: AgentMemory, seed: Seed, name: str
) -> bool:
    """Marca la seed si no tiene symbol/TF o su .set no sirve para MT5."""
    if not seed.symbol or not seed.period or seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN":
        print(f"AVISO: seed sin symbol/timeframe inferible: {name}; marcada como report_mismatch.")
        if not args.dry_run:
            memory.record_seed_score(seed, None, "report_mismatch", None)
        return True
    invalid_reasons = validate_seed_backtest_set(seed)
    if invalid_reasons:
        print(
            f"AVISO: seed con .set invalido para MT5: {name}; "
            + " | ".join(invalid_reasons)
            + ". Marcada como invalid_seed."
        )
        if not args.dry_run:
            record_invalid_seed(memory, seed, invalid_reasons)
        return True
    return False


def _retry_seed_dir(args: argparse.Namespace) -> Path:
    """Carpeta de trabajo de este reintento de seeds."""
    retry_dir = (
        resolve_workspace_path(args.output_dir)
        / "seed_retry"
        / datetime.now().strftime("retry_%Y%m%d_%H%M%S")
    )
    retry_dir.mkdir(parents=True, exist_ok=True)
    return retry_dir


def _copy_retry_seeds(
    args: argparse.Namespace, seeds: list[Seed], retry_dir: Path
) -> list[tuple[Seed, Path]]:
    """Copia cada seed al area de reintento con su nombre de evaluacion."""
    copied: list[tuple[Seed, Path]] = []
    used_names: set[str] = set()
    for index, seed in enumerate(seeds, start=1):
        retry_set = retry_dir / seed_eval_filename(index, seed, used_names)
        copy_seed_for_backtest(seed, retry_set, parse_symbol_map(args.symbol_map), args.symbol_suffix)
        copied.append((seed, retry_set))
    return copied


def _run_retry_backtests(
    args: argparse.Namespace, retry_dir: Path
) -> tuple[float, int | None]:
    """Lanza los backtests del reintento; devuelve cuando empezaron y el corte."""
    batch_started_at = time.time()
    code = run_backtests(args, retry_dir, model="1")
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
        return batch_started_at, 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return batch_started_at, code
    if args.dry_run:
        return batch_started_at, 0
    return batch_started_at, None


def _evaluate_retry_seed(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    seed: Seed, retry_set: Path, batch_started_at: float,
):
    """Puntua el reporte de una seed reintentada."""
    report = find_report_for_set(retry_set, min_mtime=batch_started_at - 1.0)
    if not report:
        status = missing_report_status(seed.symbol, args)
        memory.record_seed_score(seed, None, status, None)
        return status, None
    return evaluate_seed_report(
        memory,
        seed,
        report,
        score_config,
        parse_symbol_map(args.symbol_map),
        args.broker,
        label=retry_set.name,
        symbol_suffix=args.symbol_suffix,
    )


def _retry_seed_batch(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    source_paths: list[Path],
) -> int:
    """Reintenta varias seeds en una sola tanda de backtests."""
    seeds: list[Seed] = []
    for source_path in source_paths:
        if not source_path.exists():
            print(f"ERROR: no existe seed {source_path}")
            return 1
        seed = memory.apply_seed_overrides([seed_from_path(source_path)])[0]
        if not args.dry_run:
            memory.prepare_single_seed_evaluation(seed, force=True)
        if not _retry_seed_rejected(args, memory, seed, source_path.name):
            seeds.append(seed)
    if not seeds:
        return 1
    retry_dir = _retry_seed_dir(args)
    copied = _copy_retry_seeds(args, seeds, retry_dir)
    print(f"Retry seeds: {len(copied)}")
    print(f"Directorio retry: {retry_dir}")
    batch_started_at, early_exit = _run_retry_backtests(args, retry_dir)
    if early_exit is not None:
        return early_exit
    statuses: dict[str, int] = {}
    for seed, retry_set in copied:
        status, _result = _evaluate_retry_seed(
            args, memory, score_config, seed, retry_set, batch_started_at
        )
        statuses[status] = statuses.get(status, 0) + 1
    print(
        "Retry seeds terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(statuses.items()))
    )
    return 0


def _retry_single_seed(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig, source_seed: Path
) -> int:
    """Reintenta una sola seed y devuelve el codigo de salida del comando."""
    if not source_seed.exists():
        print(f"ERROR: no existe seed {source_seed}")
        return 1
    seed = memory.apply_seed_overrides([seed_from_path(source_seed)])[0]
    if not args.dry_run and not memory.prepare_single_seed_evaluation(seed, force=True):
        print(f"ERROR: no se pudo preparar seed {source_seed}")
        return 1
    if _retry_seed_rejected(args, memory, seed, source_seed.name):
        return 1
    retry_dir = _retry_seed_dir(args)
    retry_set = retry_dir / seed_eval_filename(1, seed, set())
    copy_seed_for_backtest(seed, retry_set, parse_symbol_map(args.symbol_map), args.symbol_suffix)
    print(f"Retry seed: {source_seed}")
    print(f"Set retry: {retry_set}")
    batch_started_at, early_exit = _run_retry_backtests(args, retry_dir)
    if early_exit is not None:
        return early_exit
    report = find_report_for_set(retry_set, min_mtime=batch_started_at - 1.0)
    if not report:
        memory.record_seed_score(seed, None, missing_report_status(seed.symbol, args), None)
        print("Retry seed terminado sin reporte fresco.")
        return 1
    status, result = evaluate_seed_report(
        memory,
        seed,
        report,
        score_config,
        parse_symbol_map(args.symbol_map),
        args.broker,
        label=retry_set.name,
        symbol_suffix=args.symbol_suffix,
    )
    print(f"Retry seed estado={status}; score={result.score if result else 'n/a'}")
    if status in {"accepted", "rejected", "no_trades", "report_mismatch", "parse_error"}:
        return 0
    return 1


def retry_seed(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.retry_seed_path:
        print("ERROR: falta --retry-seed-path")
        return 1
    if not args.expert and not args.multi_terminal:
        print("ERROR: retry seed requiere --expert o --multi-terminal")
        return 1
    source_paths = _retry_seed_paths(args)
    if len(source_paths) > 1:
        return _retry_seed_batch(args, memory, score_config, source_paths)
    return _retry_single_seed(args, memory, score_config, source_paths[0])
