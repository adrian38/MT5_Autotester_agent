"""Reintentos de candidatos, semillas, generaciones y runs."""
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
from ubs_agent_retry_seed import retry_seed  # noqa: F401
from ubs_agent_variants import (
    run_backtests,
)


def retry_candidate(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    candidate_ids = [int(value) for value in (args.retry_candidate_id or [])]
    if not candidate_ids:
        print("ERROR: falta --retry-candidate-id")
        return 1
    if not args.expert and not args.multi_terminal:
        print("ERROR: retry requiere --expert")
        return 1

    exit_code = 0
    for candidate_id in candidate_ids:
        code = _retry_single_candidate(candidate_id, args, memory, score_config)
        if code == RUNNING_TERMINAL_EXIT_CODE:
            print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
            return 1
        if code != 0:
            exit_code = code
    return exit_code


def _evaluate_retried_candidate(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    variant, retry_set: Path, run_dir: Path, generation: int, batch_started_at: float,
) -> None:
    status, result = evaluate_variant(
        memory,
        variant,
        score_config,
        parse_symbol_map(args.symbol_map),
        args.broker,
        min_report_mtime=batch_started_at - 1.0,
        min_trades_w1=args.min_trades_w1,
        min_trades_mn=args.min_trades_mn,
        symbol_suffix=args.symbol_suffix,
        universe_symbols=broker_universe_symbols(args),
    )
    if status == "accepted" and result is not None:
        copied = copy_accepted(
            [(replace(variant, path=retry_set), result)],
            run_dir / f"accepted_gen_{generation:03d}",
        )
        print(f"Retry aceptado; copias accepted: {len(copied)}")
    else:
        print(f"Retry terminado con estado: {status}")


def _retry_single_candidate(
    candidate_id: int,
    args: argparse.Namespace,
    memory: AgentMemory,
    score_config: ScoreConfig,
) -> int:
    row = memory.candidate_by_id(candidate_id)
    if row is None:
        print(f"ERROR: no existe candidate id {candidate_id}")
        return 1

    set_path = resolve_workspace_path(row["set_path"])
    if not set_path.exists():
        print(f"ERROR: no existe el set del candidato: {set_path}")
        return 1

    run = memory.run_by_id(int(row["run_id"]))
    run_dir = resolve_workspace_path(run["output_dir"]) if run else DEFAULT_OUTPUT
    generation = int(row["generation"] or 0)
    retry_dir = recreate_work_dir(run_dir / "retry_mismatch" / f"candidate_{candidate_id}")
    retry_set = retry_dir / set_path.name
    variant = variant_from_candidate_row(row)
    exact_symbol = write_retry_set(set_path, retry_set, False, args, variant.target_symbol)
    variant = replace(variant, target_symbol=exact_symbol)
    if not args.dry_run:
        remove_report_artifacts(set_path)
        remove_candidate_copies(run_dir, generation, set_path.name)

    print(f"Retry candidate #{candidate_id}")
    print(f"Set original: {set_path}")
    print(f"Set retry: {retry_set}")
    batch_started_at = time.time()
    code = run_backtests(args, retry_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        return code
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0

    _evaluate_retried_candidate(
        args, memory, score_config, variant, retry_set, run_dir, generation, batch_started_at
    )
    return 0

def _prepare_mismatch_retry(
    args: argparse.Namespace, rows_with_paths: list[tuple], retry_dir: Path,
    run_dir: Path, cleanup_generation: int | None,
) -> tuple[list, dict[int, Path], list[Variant]] | None:
    rows = [row for row, _set_path in rows_with_paths]
    seen_names: set[str] = set()
    retry_sets_by_id: dict[int, Path] = {}
    variants: list[Variant] = []
    for row, set_path in rows_with_paths:
        if set_path.name in seen_names:
            print(f"ERROR: nombre de set duplicado en retry: {set_path.name}")
            return None
        seen_names.add(set_path.name)
        retry_set = retry_dir / set_path.name
        retry_sets_by_id[int(row["id"])] = retry_set
        variant = variant_from_candidate_row(row)
        exact_symbol = write_retry_set(set_path, retry_set, False, args, variant.target_symbol)
        variants.append(replace(variant, target_symbol=exact_symbol))
        if not args.dry_run:
            generation = (
                cleanup_generation if cleanup_generation is not None
                else int(row["generation"] or 0)
            )
            remove_report_artifacts(set_path)
            remove_candidate_copies(run_dir, generation, set_path.name)
    return rows, retry_sets_by_id, variants


def _run_mismatch_retry_batch(args: argparse.Namespace, retry_dir: Path) -> tuple[int | None, float]:
    batch_started_at = time.time()
    code = run_backtests(args, retry_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
        return 1, batch_started_at
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return code, batch_started_at
    if args.dry_run:
        return 0, batch_started_at
    return None, batch_started_at


def _evaluate_mismatch_variants(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    rows: list, variants: list[Variant], retry_sets_by_id: dict[int, Path],
    batch_started_at: float, fixed_generation: int | None,
) -> tuple[dict[str, int], dict[int, list[tuple[Variant, ScoreResult]]]]:
    accepted_by_generation: dict[int, list[tuple[Variant, ScoreResult]]] = {}
    status_counts: dict[str, int] = {}
    symbol_map = parse_symbol_map(args.symbol_map)
    for row, variant in zip(rows, variants):
        status, result = evaluate_variant(
            memory, variant, score_config, symbol_map, args.broker,
            min_report_mtime=batch_started_at - 1.0,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
            symbol_suffix=args.symbol_suffix,
            universe_symbols=broker_universe_symbols(args),
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "accepted" and result is not None:
            generation = fixed_generation if fixed_generation is not None else int(row["generation"] or 0)
            accepted_by_generation.setdefault(generation, []).append(
                (replace(variant, path=retry_sets_by_id[int(row["id"])]), result)
            )
    return status_counts, accepted_by_generation


def retry_generation_mismatches(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.retry_mismatch_generation:
        print("ERROR: falta --retry-mismatch-generation")
        return 1
    if not args.expert and not args.multi_terminal:
        print("ERROR: retry por generacion requiere --expert")
        return 1

    if args.retry_run_id:
        run = memory.run_by_id(args.retry_run_id)
    else:
        run = memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para retry por generacion")
        return 1

    run_id = int(run["id"])
    generation = int(args.retry_mismatch_generation)
    rows_with_paths = [
        (row, resolve_workspace_path(row["set_path"]))
        for row in memory.mismatch_candidates_for_generation(run_id, generation)
    ]
    rows_with_paths = [(row, set_path) for row, set_path in rows_with_paths if set_path.exists()]
    if not rows_with_paths:
        print(f"ERROR: run #{run_id} gen {generation} no tiene problemas reintentables con .set existente")
        return 1

    run_dir = resolve_workspace_path(run["output_dir"])
    retry_dir = recreate_work_dir(run_dir / "retry_mismatch" / f"run_{run_id}_gen_{generation:03d}")
    print(f"Retry problemas tecnicos run #{run_id} gen {generation}: {len(rows_with_paths)} candidato(s)")
    prepared = _prepare_mismatch_retry(args, rows_with_paths, retry_dir, run_dir, generation)
    if prepared is None:
        return 1
    rows, retry_sets_by_id, variants = prepared
    early_return, batch_started_at = _run_mismatch_retry_batch(args, retry_dir)
    if early_return is not None:
        return early_return

    status_counts, accepted_by_generation = _evaluate_mismatch_variants(
        args, memory, score_config, rows, variants, retry_sets_by_id,
        batch_started_at, generation,
    )
    accepted = accepted_by_generation.get(generation, [])
    copied = copy_accepted(accepted, run_dir / f"accepted_gen_{generation:03d}")
    print(
        "Retry gen terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; accepted/copied={len(copied)}"
    )
    return 0


def retry_run_mismatches(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.expert and not args.multi_terminal:
        print("ERROR: retry por run requiere --expert")
        return 1

    run = memory.run_by_id(args.retry_run_id) if args.retry_run_id else memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para retry por run")
        return 1

    run_id = int(run["id"])
    rows_with_paths = [
        (row, resolve_workspace_path(row["set_path"]))
        for row in memory.mismatch_candidates_for_run(run_id)
    ]
    rows_with_paths = [(row, set_path) for row, set_path in rows_with_paths if set_path.exists()]
    if not rows_with_paths:
        print(f"ERROR: run #{run_id} no tiene problemas reintentables con .set existente")
        return 1

    run_dir = resolve_workspace_path(run["output_dir"])
    retry_dir = recreate_work_dir(run_dir / "retry_mismatch" / f"run_{run_id}_all")
    print(f"Retry problemas tecnicos run #{run_id}: {len(rows_with_paths)} candidato(s)")
    prepared = _prepare_mismatch_retry(args, rows_with_paths, retry_dir, run_dir, None)
    if prepared is None:
        return 1
    rows, retry_sets_by_id, variants = prepared
    early_return, batch_started_at = _run_mismatch_retry_batch(args, retry_dir)
    if early_return is not None:
        return early_return

    status_counts, accepted_by_generation = _evaluate_mismatch_variants(
        args, memory, score_config, rows, variants, retry_sets_by_id,
        batch_started_at, None,
    )
    copied = 0
    for generation, accepted in accepted_by_generation.items():
        copied += len(copy_accepted(accepted, run_dir / f"accepted_gen_{generation:03d}"))
    print(
        "Retry run terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; accepted/copied={copied}"
    )
    return 0


def _retry_full_rows(args: argparse.Namespace, memory: AgentMemory, run_id: int):
    """Candidatos del run con .set existente, filtrados por los marcados."""
    rows_with_paths = [
        (row, resolve_workspace_path(row["set_path"]))
        for row in memory.candidates_for_run(run_id)
    ]
    rows_with_paths = [(row, set_path) for row, set_path in rows_with_paths if set_path.exists()]
    requested_ids = {int(value) for value in (args.retry_candidate_id or [])}
    if requested_ids:
        rows_with_paths = [
            (row, set_path) for row, set_path in rows_with_paths
            if int(row["id"]) in requested_ids
        ]
        found_ids = {int(row["id"]) for row, _set_path in rows_with_paths}
        missing_ids = sorted(requested_ids - found_ids)
        if missing_ids:
            print(
                "ERROR: candidatos marcados no pertenecen al run o no tienen .set existente: "
                + ", ".join(str(candidate_id) for candidate_id in missing_ids)
            )
            return None, requested_ids
    if not rows_with_paths:
        print(f"ERROR: run #{run_id} no tiene candidatos con .set existente")
        return None, requested_ids
    return rows_with_paths, requested_ids


def _retry_full_stage_sets(
    args: argparse.Namespace, rows_with_paths: list, retry_dir: Path, run_dir: Path
):
    """Copia los sets al area de reprueba y prepara sus variantes."""
    seen_names: set[str] = set()
    retry_sets_by_id: dict[int, Path] = {}
    variants: list[Variant] = []
    for row, set_path in rows_with_paths:
        if set_path.name in seen_names:
            print(f"ERROR: nombre de set duplicado en reprobar run: {set_path.name}")
            return None, None
        seen_names.add(set_path.name)
        retry_set = retry_dir / set_path.name
        retry_sets_by_id[int(row["id"])] = retry_set
        variant = variant_from_candidate_row(row)
        exact_symbol = write_retry_set(set_path, retry_set, False, args, variant.target_symbol)
        variants.append(replace(variant, target_symbol=exact_symbol))
        if not args.dry_run:
            remove_report_artifacts(set_path)
            remove_candidate_copies(run_dir, int(row["generation"] or 0), set_path.name)
    return retry_sets_by_id, variants


def _retry_full_evaluate(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig,
    rows: list, variants: list[Variant], retry_sets_by_id: dict[int, Path],
    batch_started_at: float,
):
    """Puntua cada candidato reprobado y agrupa los aceptados por generacion."""
    accepted_by_generation: dict[int, list[tuple[Variant, ScoreResult]]] = {}
    status_counts: dict[str, int] = {}
    symbol_map = parse_symbol_map(args.symbol_map)
    for row, variant in zip(rows, variants):
        status, result = evaluate_variant(
            memory,
            variant,
            score_config,
            symbol_map,
            args.broker,
            min_report_mtime=batch_started_at - 1.0,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
            symbol_suffix=args.symbol_suffix,
            universe_symbols=broker_universe_symbols(args),
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "accepted" and result is not None:
            accepted_by_generation.setdefault(int(row["generation"] or 0), []).append(
                (replace(variant, path=retry_sets_by_id[int(row["id"])]), result)
            )
    return status_counts, accepted_by_generation


def retry_full_run(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.expert and not args.multi_terminal:
        print("ERROR: reprobar run completo requiere --expert o --multi-terminal")
        return 1
    run = memory.run_by_id(args.retry_run_id) if args.retry_run_id else memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para reprobar completo")
        return 1
    run_id = int(run["id"])
    rows_with_paths, requested_ids = _retry_full_rows(args, memory, run_id)
    if rows_with_paths is None:
        return 1
    run_dir = resolve_workspace_path(run["output_dir"])
    suffix = "selected" if requested_ids else "all"
    retry_dir = recreate_work_dir(run_dir / "retry_full" / f"run_{run_id}_{suffix}")
    rows = [row for row, _set_path in rows_with_paths]
    print(f"Reprobar run completo #{run_id}: {len(rows)} candidato(s)")
    if requested_ids:
        print("Modo seleccionado: " + ", ".join(str(row["id"]) for row in rows))
    retry_sets_by_id, variants = _retry_full_stage_sets(args, rows_with_paths, retry_dir, run_dir)
    if retry_sets_by_id is None:
        return 1
    batch_started_at = time.time()
    code = run_backtests(args, retry_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
        return 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0
    status_counts, accepted_by_generation = _retry_full_evaluate(
        args, memory, score_config, rows, variants, retry_sets_by_id, batch_started_at
    )
    copied = 0
    for generation, accepted in accepted_by_generation.items():
        copied += len(copy_accepted(accepted, run_dir / f"accepted_gen_{generation:03d}"))
    print(
        "Reprobar run completo terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; accepted/copied={copied}"
    )
    return 0
