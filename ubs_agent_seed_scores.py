"""Puntuacion y reconciliacion de las semillas del run."""
from __future__ import annotations

import argparse
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from run_tests import (
    RUNNING_TERMINAL_EXIT_CODE,
    apply_symbol_map,
    normalize_set_symbol,
    parse_symbol_map,
)
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, score_report_file
from ubs.seeds import file_digest, load_seeds, seed_eval_filename
from ubs.universe import (
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
    seed_symbol_disabled,
)
from ubs_agent_config import (
    BASE_DIR,
)
from ubs_agent_evaluate import (
    evaluate_seed_report,
)
from ubs_agent_reports import (
    find_report_for_set,
    report_has_empty_tester_context,
    report_matches_variant,
)
from ubs_agent_seeds_plan import (
    disabled_symbols_file_for_account,
)
from ubs_agent_sets import (
    copy_seed_for_backtest,
    record_invalid_seed,
    validate_seed_backtest_set,
)
from ubs_agent_universe import (
    missing_report_status,
)
from ubs_agent_variants import (
    run_backtests,
)


def _parse_eval_dir_timestamp(eval_dir: Path) -> datetime | None:
    try:
        return datetime.strptime(eval_dir.name, "eval_%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _seed_override_updated_at(memory: AgentMemory, seed_path: Path) -> datetime | None:
    try:
        row = memory.conn.execute(
            "select updated_at from seed_overrides where seed_path=?",
            (str(seed_path),),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return datetime.fromisoformat(str(row["updated_at"] or ""))
    except (TypeError, ValueError):
        return None


def _reconcile_seed_eval_copy(
    memory, score_config, symbol_map, broker, symbol_suffix, pending, pending_by_hash,
    processed_paths, status_counts, eval_started, copied_set,
) -> None:
    """Reconcilia un .set copiado con el informe que dejo en disco."""
    report = find_report_for_set(copied_set, min_mtime=eval_started.timestamp() - 1.0)
    if not report:
        return
    copied_digest = file_digest(copied_set)
    if not copied_digest:
        return
    candidates = pending_by_hash.get(copied_digest, [])
    seed = next((candidate for candidate in candidates if str(candidate.path) not in processed_paths), None)
    if seed is None:
        return
    try:
        parsed_result = score_report_file(report, config=score_config, broker=broker)
    except Exception:
        # A broken historical artifact is not a completed evaluation.
        # Keep the seed pending so this batch launches MT5 again.
        return
    if report_has_empty_tester_context(parsed_result):
        print(
            f"AVISO: reporte previo sin contexto tester para {copied_set.name}; "
            "se ignora y la seed permanece pendiente para un backtest nuevo."
        )
        return
    override_updated_at = _seed_override_updated_at(memory, seed.path)
    if override_updated_at is not None and eval_started <= override_updated_at:
        # Override guardado despues de la evaluacion: el reporte solo es
        # reutilizable si coincide con el target efectivo actual (caso
        # tipico: override que no cambia symbol/TF). Si no coincide, se
        # deja pendiente para re-ejecutar en MT5.
        if seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN":
            return
        probe_variant = Variant(
            path=Path(copied_set.name),
            seed=seed,
            target_symbol=seed.symbol,
            target_period=seed.period,
            mutated_keys=(),
            missing_lot_keys=(),
            policy="seed_eval",
        )
        matches, _ = report_matches_variant(
            probe_variant,
            parsed_result,
            symbol_map,
            symbol_suffix,
            broker,
        )
        if not matches:
            return
    seed_path = str(seed.path)
    status, _ = evaluate_seed_report(
        memory,
        seed,
        report,
        score_config,
        symbol_map,
        broker,
        label=copied_set.name,
        symbol_suffix=symbol_suffix,
        parsed_result=parsed_result,
    )
    status_counts[status] = status_counts.get(status, 0) + 1
    processed_paths.add(seed_path)
    if len(processed_paths) >= len(pending):
        return status_counts, processed_paths


def _reconcile_seed_eval_set(
    memory, score_config, symbol_map, broker, symbol_suffix, pending, pending_by_hash,
    processed_paths, status_counts, eval_dir, eval_started,
):
    """Reconcilia los .set copiados de una carpeta de evaluacion."""
    for copied_set in sorted(eval_dir.glob("*.set")):
        _reconcile_seed_eval_copy(
            memory, score_config, symbol_map, broker, symbol_suffix, pending, pending_by_hash,
            processed_paths, status_counts, eval_started, copied_set,
        )

def _reconcile_seed_eval_reports_in(
    memory, score_config, symbol_map, broker, symbol_suffix, pending, pending_by_hash,
    processed_paths, status_counts, eval_dir,
) -> None:
    """Reconcilia los informes de una carpeta de evaluacion concreta."""
    eval_started = _parse_eval_dir_timestamp(eval_dir)
    if eval_started is None:
        return
    _reconcile_seed_eval_set(
        memory, score_config, symbol_map, broker, symbol_suffix, pending, pending_by_hash,
        processed_paths, status_counts, eval_dir, eval_started,
    )


def _reconcile_seed_eval_dir(memory, pending, score_config, symbol_map, broker, symbol_suffix, eval_dirs, pending_by_hash, processed_paths, status_counts):
    """Reconcilia los informes de una carpeta de evaluacion de semillas."""
    for eval_dir in eval_dirs:
        _reconcile_seed_eval_reports_in(
            memory, score_config, symbol_map, broker, symbol_suffix, pending, pending_by_hash,
            processed_paths, status_counts, eval_dir,
        )

def reconcile_seed_eval_reports(
    memory: AgentMemory,
    pending: list[Seed],
    output_root: Path,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    symbol_suffix: str = "",
) -> tuple[dict[str, int], set[str]]:
    seed_eval_root = output_root / "seed_eval"
    if not pending or not seed_eval_root.exists():
        return {}, set()

    pending_by_hash: dict[str, list[Seed]] = {}
    for seed in pending:
        row = memory.seed_score_row(seed.path)
        if row is not None and str(row["status"] or "") == "no_trades":
            continue
        digest = file_digest(seed.path)
        if digest:
            pending_by_hash.setdefault(digest, []).append(seed)
    eval_dirs = sorted(
        (path for path in seed_eval_root.glob("eval_*") if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    status_counts: dict[str, int] = {}
    processed_paths: set[str] = set()
    _reconcile_seed_eval_dir(memory, pending, score_config, symbol_map, broker, symbol_suffix, eval_dirs, pending_by_hash, processed_paths, status_counts)
    return status_counts, processed_paths


def rescore_existing_seed_scores(
    memory: AgentMemory,
    seeds: list[Seed],
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    exclude_paths: set[str],
    symbol_suffix: str = "",
) -> dict[str, int]:
    status_counts: dict[str, int] = {}
    for seed in seeds:
        if str(seed.path) in exclude_paths:
            continue
        row = memory.seed_score_row(seed.path)
        if row is None or str(row["status"] or "") not in {"accepted", "rejected", "no_trades", "report_mismatch", "parse_error"}:
            continue
        report_raw = str(row["report_path"] or "").strip()
        if not report_raw:
            continue
        report = Path(report_raw)
        if not report.exists():
            continue
        status, _ = evaluate_seed_report(
            memory,
            seed,
            report,
            score_config,
            symbol_map,
            broker,
            symbol_suffix=symbol_suffix,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    return status_counts


def format_disabled_seed_counts(seeds: list[Seed], symbol_map: dict[str, str]) -> str:
    counts: Counter[tuple[str, str]] = Counter()
    for seed in seeds:
        raw = normalize_set_symbol(seed.symbol)
        mapped = normalize_set_symbol(apply_symbol_map(seed.symbol, symbol_map))
        counts[(raw or seed.symbol, mapped or raw or seed.symbol)] += 1
    parts = []
    shown_total = 0
    for (raw, mapped), count in counts.most_common(5):
        shown_total += count
        label = raw if raw == mapped else f"{raw} -> {mapped}"
        parts.append(f"{label}: {count}")
    remaining = sum(counts.values()) - shown_total
    if remaining > 0:
        parts.append(f"otros: {remaining}")
    return ", ".join(parts)


def _score_seed_reports(args, memory, score_config, batch_started_at, copied, status_counts, symbol_map):
    """Puntua el informe de cada semilla evaluada y guarda su estado."""
    for seed, copied_set in copied:
        report = find_report_for_set(copied_set, min_mtime=batch_started_at - 1.0)
        if not report:
            status = missing_report_status(seed.symbol, args, symbol_map)
            memory.record_seed_score(seed, None, status, None)
            status_counts[status] = status_counts.get(status, 0) + 1
            handled_issues += 1
            continue
        status, result = evaluate_seed_report(
            memory,
            seed,
            report,
            score_config,
            symbol_map,
            args.broker,
            label=copied_set.name,
            symbol_suffix=args.symbol_suffix,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"accepted", "rejected"} and result is not None:
            scored += 1
        else:
            handled_issues += 1
    return handled_issues, scored, status

def _prepare_seed_eval_dir(args, output_root, pending, symbol_map):
    """Prepara el directorio de evaluacion y copia las semillas pendientes."""
    eval_dir = output_root / "seed_eval" / datetime.now().strftime("eval_%Y%m%d_%H%M%S")
    eval_dir.mkdir(parents=True, exist_ok=True)
    copied: list[tuple[Seed, Path]] = []
    used_names: set[str] = set()
    for index, seed in enumerate(pending, start=1):
        destination = eval_dir / seed_eval_filename(index, seed, used_names)
        copy_seed_for_backtest(seed, destination, symbol_map, args.symbol_suffix)
        copied.append((seed, destination))

    print(f"Backtests semillas: {len(copied)}")
    print(f"Directorio evaluacion: {eval_dir}")
    return copied, eval_dir

def _print_seed_evaluation_summary(disabled_pending, invalid_pending, invalid_set_reasons, pending, seed_enabled_when_disabled, seeds, symbol_map, unchanged_count):
    """Resumen de semillas detectadas, pendientes y bloqueadas."""
    print(f"Semillas detectadas: {len(seeds)}")
    print(f"Backtests de semillas pendientes: {len(pending)}")
    if invalid_pending:
        print(f"Semillas bloqueadas por symbol/timeframe no inferible: {len(invalid_pending)}")
    if invalid_set_reasons:
        print(f"Semillas bloqueadas por .set invalido: {len(invalid_set_reasons)}")
    if disabled_pending:
        print(
            "Semillas omitidas por symbol deshabilitado en politica GEN/SEEDS de la cuenta: "
            f"{len(disabled_pending)} ({format_disabled_seed_counts(disabled_pending, symbol_map)})"
        )
        print("Estas seeds no abren MT5 ni aportan pesos; activa SEEDS para usarlas sin habilitar generacion.")
    if seed_enabled_when_disabled:
        print(f"Symbols deshabilitados con SEEDS activo: {len(seed_enabled_when_disabled)}")
    print(f"Semillas ya evaluadas sin cambios: {unchanged_count}")

def _filter_disabled_seeds(args, memory, symbol_map):
    """Aparta las semillas cuyo simbolo esta desactivado en la politica."""
    disabled_policy_path = disabled_symbols_file_for_account(args.account_type, args.broker)
    disabled_symbols = load_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled = load_seed_enabled_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled &= disabled_symbols
    disabled_pending = [
        seed for seed in pending
        if seed_symbol_disabled(seed, disabled_symbols, symbol_map, seed_enabled_when_disabled)
    ]
    disabled_paths = {str(seed.path) for seed in disabled_pending}
    for seed in disabled_pending:
        raw_symbol = normalize_set_symbol(seed.symbol)
        mapped_symbol = normalize_set_symbol(apply_symbol_map(seed.symbol, symbol_map))
        symbol_detail = raw_symbol if raw_symbol == mapped_symbol else f"{raw_symbol} -> {mapped_symbol}"
        print(
            f"AVISO: seed omitida por symbol deshabilitado: {seed.path.name} "
            f"({symbol_detail}); marcada como disabled_symbol sin abrir MT5."
        )
        memory.record_seed_score(seed, None, "disabled_symbol", None)
    pending = [seed for seed in pending if str(seed.path) not in disabled_paths]
    blocked_count += len(disabled_pending)
    return blocked_count, disabled_paths, disabled_pending, pending, seed, seed_enabled_when_disabled

def _filter_invalid_seeds(args, memory, seeds):
    """Aparta las semillas sin simbolo o sin timeframe utilizable."""
    pending = memory.prepare_seed_evaluation(seeds, force=args.reevaluate_seeds)
    original_pending_count = len(pending)
    original_pending_paths = {str(seed.path) for seed in pending}
    invalid_pending = [
        seed
        for seed in pending
        if not seed.symbol or not seed.period or seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN"
    ]
    invalid_paths = {str(seed.path) for seed in invalid_pending}
    for seed in invalid_pending:
        print(
            f"AVISO: seed sin symbol/timeframe inferible: {seed.path.name}; "
            "marcada como report_mismatch sin ejecutar backtest."
        )
        memory.record_seed_score(seed, None, "report_mismatch", None)
    pending = [seed for seed in pending if seed not in invalid_pending]
    unchanged_count = len(seeds) - original_pending_count
    blocked_count = len(invalid_pending)
    return blocked_count, invalid_paths, invalid_pending, original_pending_paths, pending, seed, unchanged_count

def _report_no_pending_seeds(
    args, memory, score_config, blocked_count, disabled_paths, disabled_pending,
    invalid_paths, invalid_pending, invalid_set_reasons, original_pending_paths,
    seeds, symbol_map,
) -> int:
    """Informe cuando no queda ninguna semilla pendiente de evaluar."""
    rescored_counts = rescore_existing_seed_scores(
        memory,
        seeds,
        score_config,
        symbol_map,
        args.broker,
        exclude_paths=original_pending_paths | invalid_paths | disabled_paths | set(invalid_set_reasons),
        symbol_suffix=args.symbol_suffix,
    )
    if rescored_counts:
        print(
            "Semillas repuntuadas con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(rescored_counts.items()))
        )
    if blocked_count:
        if invalid_pending:
            print("No hay backtests pendientes validos. Corrige Symbol/TF de las semillas sin inferencia.")
        elif disabled_pending:
            print("No hay backtests pendientes validos. Las restantes estan deshabilitadas en la politica GEN/SEEDS de la cuenta.")
    else:
        print("Evaluacion de semillas al dia. No hay backtests pendientes.")
    return 0

def _print_seed_evaluation_result(args, memory, score_config, code, copied, disabled_paths, handled_issues, invalid_paths, invalid_set_reasons, original_pending_paths, scored, seeds, status_counts, symbol_map):
    """Resultado final de la evaluacion y de las repuntuaciones."""
    rescored_counts = rescore_existing_seed_scores(
        memory,
        seeds,
        score_config,
        symbol_map,
        args.broker,
        exclude_paths=original_pending_paths | invalid_paths | disabled_paths | set(invalid_set_reasons),
        symbol_suffix=args.symbol_suffix,
    )
    rescored_total = sum(rescored_counts.values())

    print(
        "Evaluacion semillas terminada: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; puntuadas={scored}/{len(copied)}"
    )
    if rescored_counts:
        print(
            "Semillas repuntuadas con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(rescored_counts.items()))
            + f"; repuntuadas={rescored_total}"
        )
    print(f"Memoria: {memory.path}")
    if code != 0 and scored == 0 and handled_issues == 0:
        return 1

def _check_seed_sets(memory, disabled_pending, invalid_pending, seed_enabled_when_disabled, seeds, symbol_map, unchanged_count):
    """Revisa los .set de las semillas pendientes y avisa de los invalidos."""
    invalid_set_reasons: dict[str, list[str]] = {}
    for seed in pending:
        reasons = validate_seed_backtest_set(seed)
        if reasons:
            invalid_set_reasons[str(seed.path)] = reasons
            print(
                f"AVISO: seed con .set invalido para MT5: {seed.path.name}; "
                + " | ".join(reasons)
                + ". Marcada como invalid_seed sin abrir MT5."
            )
            record_invalid_seed(memory, seed, reasons)
    if invalid_set_reasons:
        pending = [seed for seed in pending if str(seed.path) not in invalid_set_reasons]
        blocked_count += len(invalid_set_reasons)
    _print_seed_evaluation_summary(disabled_pending, invalid_pending, invalid_set_reasons, pending, seed_enabled_when_disabled, seeds, symbol_map, unchanged_count)
    return blocked_count, invalid_set_reasons, pending, seed

def _reconcile_pending_seeds(args, memory, score_config, output_root, symbol_map):
    """Aprovecha los informes de semillas ya presentes en disco."""
    reconciled_counts, reconciled_paths = reconcile_seed_eval_reports(
        memory,
        pending,
        output_root,
        score_config,
        symbol_map,
        args.broker,
        args.symbol_suffix,
    )
    if reconciled_counts:
        pending = [seed for seed in pending if str(seed.path) not in reconciled_paths]
        print(
            "Semillas reconciliadas desde evaluaciones incompletas: "
            + ", ".join(f"{status}={count}" for status, count in sorted(reconciled_counts.items()))
        )
        print(f"Semillas pendientes tras reconciliar: {len(pending)}")
    if args.reconcile_seed_eval_only:
        print(f"Memoria: {memory.path}")
        return 0
    return pending

def evaluate_seed_scores(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    source_dir = resolve_workspace_path(args.source_dir)
    output_root = resolve_workspace_path(args.output_dir)
    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    if not args.expert and not args.multi_terminal and not args.reconcile_seed_eval_only:
        print("ERROR: evaluar semillas requiere --expert o --multi-terminal.")
        return 1

    symbol_map = parse_symbol_map(args.symbol_map)
    blocked_count, invalid_paths, invalid_pending, original_pending_paths, pending, seed, unchanged_count = _filter_invalid_seeds(args, memory, seeds)
    blocked_count, disabled_paths, disabled_pending, pending, seed, seed_enabled_when_disabled = _filter_disabled_seeds(args, memory, symbol_map)
    blocked_count, invalid_set_reasons, pending, seed = _check_seed_sets(memory, disabled_pending, invalid_pending, seed_enabled_when_disabled, seeds, symbol_map, unchanged_count)
    pending = _reconcile_pending_seeds(args, memory, score_config, output_root, symbol_map)
    if not pending:
        return _report_no_pending_seeds(
            args, memory, score_config, blocked_count, disabled_paths, disabled_pending,
            invalid_paths, invalid_pending, invalid_set_reasons, original_pending_paths,
            seeds, symbol_map,
        )

    copied, eval_dir = _prepare_seed_eval_dir(args, output_root, pending, symbol_map)
    batch_started_at = time.time()
    code = run_backtests(args, eval_dir, model="1")
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
        return 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se puntuaran los reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0

    scored = 0
    handled_issues = blocked_count
    status_counts: dict[str, int] = {}
    handled_issues, scored, status = _score_seed_reports(args, memory, score_config, batch_started_at, copied, status_counts, symbol_map)

    _print_seed_evaluation_result(args, memory, score_config, code, copied, disabled_paths, handled_issues, invalid_paths, invalid_set_reasons, original_pending_paths, scored, seeds, status_counts, symbol_map)
    return 0


def rescore_seed_scores_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    source_dir = resolve_workspace_path(args.source_dir)
    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    status_counts = rescore_existing_seed_scores(
        memory,
        seeds,
        score_config,
        parse_symbol_map(args.symbol_map),
        args.broker,
        exclude_paths=set(),
        symbol_suffix=args.symbol_suffix,
    )
    total = sum(status_counts.values())
    if status_counts:
        print(
            "Semillas repuntuadas con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay seeds accepted/rejected con reporte guardado para repuntuar.")
    print(f"Memoria: {memory.path}")
    return 0
