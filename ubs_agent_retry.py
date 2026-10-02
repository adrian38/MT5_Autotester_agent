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


def retry_seed(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.retry_seed_path:
        print("ERROR: falta --retry-seed-path")
        return 1
    if not args.expert and not args.multi_terminal:
        print("ERROR: retry seed requiere --expert o --multi-terminal")
        return 1
    source_paths = []
    for value in args.retry_seed_path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        source_paths.append(path.resolve())
    if len(source_paths) > 1:
        seeds: list[Seed] = []
        for source_path in source_paths:
            if not source_path.exists():
                print(f"ERROR: no existe seed {source_path}")
                return 1
            seed = memory.apply_seed_overrides([seed_from_path(source_path)])[0]
            if not seed.symbol or not seed.period or seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN":
                print(f"AVISO: seed sin symbol/timeframe inferible: {source_path.name}; marcada como report_mismatch.")
                if not args.dry_run:
                    memory.prepare_single_seed_evaluation(seed, force=True)
                    memory.record_seed_score(seed, None, "report_mismatch", None)
                continue
            invalid_reasons = validate_seed_backtest_set(seed)
            if invalid_reasons:
                print(
                    f"AVISO: seed con .set invalido para MT5: {source_path.name}; "
                    + " | ".join(invalid_reasons)
                    + ". Marcada como invalid_seed."
                )
                if not args.dry_run:
                    memory.prepare_single_seed_evaluation(seed, force=True)
                    record_invalid_seed(memory, seed, invalid_reasons)
                continue
            if not args.dry_run:
                memory.prepare_single_seed_evaluation(seed, force=True)
            seeds.append(seed)
        if not seeds:
            return 1

        retry_dir = resolve_workspace_path(args.output_dir) / "seed_retry" / datetime.now().strftime("retry_%Y%m%d_%H%M%S")
        retry_dir.mkdir(parents=True, exist_ok=True)
        copied: list[tuple[Seed, Path]] = []
        used_names: set[str] = set()
        for index, seed in enumerate(seeds, start=1):
            retry_set = retry_dir / seed_eval_filename(index, seed, used_names)
            copy_seed_for_backtest(seed, retry_set, parse_symbol_map(args.symbol_map), args.symbol_suffix)
            copied.append((seed, retry_set))
        print(f"Retry seeds: {len(copied)}")
        print(f"Directorio retry: {retry_dir}")
        batch_started_at = time.time()
        code = run_backtests(args, retry_dir, model="1")
        if code == RUNNING_TERMINAL_EXIT_CODE:
            print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
            return 1
        if code != 0:
            print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
            if args.dry_run:
                return code
        if args.dry_run:
            return 0

        statuses: dict[str, int] = {}
        for seed, retry_set in copied:
            report = find_report_for_set(retry_set, min_mtime=batch_started_at - 1.0)
            if not report:
                status = missing_report_status(seed.symbol, args)
                memory.record_seed_score(seed, None, status, None)
                statuses[status] = statuses.get(status, 0) + 1
                continue
            status, _ = evaluate_seed_report(
                memory,
                seed,
                report,
                score_config,
                parse_symbol_map(args.symbol_map),
                args.broker,
                label=retry_set.name,
                symbol_suffix=args.symbol_suffix,
            )
            statuses[status] = statuses.get(status, 0) + 1
        print(
            "Retry seeds terminado: "
            + ", ".join(f"{status}={count}" for status, count in sorted(statuses.items()))
        )
        return 0

    source_seed = source_paths[0]
    if not source_seed.exists():
        print(f"ERROR: no existe seed {source_seed}")
        return 1

    seed = memory.apply_seed_overrides([seed_from_path(source_seed)])[0]
    if not args.dry_run and not memory.prepare_single_seed_evaluation(seed, force=True):
        print(f"ERROR: no se pudo preparar seed {source_seed}")
        return 1
    if not seed.symbol or not seed.period or seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN":
        print(f"AVISO: seed sin symbol/timeframe inferible: {source_seed.name}; marcada como report_mismatch.")
        if not args.dry_run:
            memory.record_seed_score(seed, None, "report_mismatch", None)
        return 1
    invalid_reasons = validate_seed_backtest_set(seed)
    if invalid_reasons:
        print(
            f"AVISO: seed con .set invalido para MT5: {source_seed.name}; "
            + " | ".join(invalid_reasons)
            + ". Marcada como invalid_seed."
        )
        if not args.dry_run:
            record_invalid_seed(memory, seed, invalid_reasons)
        return 1

    output_root = resolve_workspace_path(args.output_dir)
    retry_dir = output_root / "seed_retry" / datetime.now().strftime("retry_%Y%m%d_%H%M%S")
    retry_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    retry_set = retry_dir / seed_eval_filename(1, seed, used_names)
    copy_seed_for_backtest(seed, retry_set, parse_symbol_map(args.symbol_map), args.symbol_suffix)

    print(f"Retry seed: {source_seed}")
    print(f"Set retry: {retry_set}")
    batch_started_at = time.time()
    code = run_backtests(args, retry_dir, model="1")
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
        return 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0

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


def retry_full_run(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.expert and not args.multi_terminal:
        print("ERROR: reprobar run completo requiere --expert o --multi-terminal")
        return 1

    run = memory.run_by_id(args.retry_run_id) if args.retry_run_id else memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para reprobar completo")
        return 1

    run_id = int(run["id"])
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
        rows = [row for row, _set_path in rows_with_paths]
        found_ids = {int(row["id"]) for row in rows}
        missing_ids = sorted(requested_ids - found_ids)
        if missing_ids:
            print(
                "ERROR: candidatos marcados no pertenecen al run o no tienen .set existente: "
                + ", ".join(str(candidate_id) for candidate_id in missing_ids)
            )
            return 1
    if not rows_with_paths:
        print(f"ERROR: run #{run_id} no tiene candidatos con .set existente")
        return 1

    run_dir = resolve_workspace_path(run["output_dir"])
    suffix = "selected" if requested_ids else "all"
    retry_dir = recreate_work_dir(run_dir / "retry_full" / f"run_{run_id}_{suffix}")
    rows = [row for row, _set_path in rows_with_paths]
    print(f"Reprobar run completo #{run_id}: {len(rows)} candidato(s)")
    if requested_ids:
        print("Modo seleccionado: " + ", ".join(str(row["id"]) for row in rows))
    seen_names: set[str] = set()
    retry_sets_by_id: dict[int, Path] = {}
    variants: list[Variant] = []
    for row, set_path in rows_with_paths:
        if set_path.name in seen_names:
            print(f"ERROR: nombre de set duplicado en reprobar run: {set_path.name}")
            return 1
        seen_names.add(set_path.name)
        retry_set = retry_dir / set_path.name
        retry_sets_by_id[int(row["id"])] = retry_set
        variant = variant_from_candidate_row(row)
        exact_symbol = write_retry_set(set_path, retry_set, False, args, variant.target_symbol)
        variants.append(replace(variant, target_symbol=exact_symbol))
        if not args.dry_run:
            generation = int(row["generation"] or 0)
            remove_report_artifacts(set_path)
            remove_candidate_copies(run_dir, generation, set_path.name)

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
            generation = int(row["generation"] or 0)
            accepted_by_generation.setdefault(generation, []).append(
                (replace(variant, path=retry_sets_by_id[int(row["id"])]), result)
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
