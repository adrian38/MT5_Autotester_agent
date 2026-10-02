"""Evaluacion de sondas, semillas y variantes contra sus reportes."""
from __future__ import annotations

import shutil
from pathlib import Path

from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult, score_report_file
from ubs.selection import FITNESS_TARGET_FINAL_TICK_6M, SelectionPrediction
from ubs.set_utils import write_set_use_every_tick
from ubs.tester_diagnostics import TRADE_DISABLED_STATUS, trade_disabled_metadata, execution_failure_metadata, execution_failure_reason
from ubs_agent_config import (
    BASE_DIR,
    SELECTION_FITNESS_APPLIED_SCALE,
    SYMBOL_NOT_EXIST_STATUS,
)
from ubs_agent_reports import (
    find_report_for_set,
    record_history_probe_status,
    record_score_with_metadata,
    record_seed_score_with_metadata,
    report_has_empty_tester_context,
    report_matches_variant,
    tester_log_no_history_metadata,
)
from ubs_agent_seeds_plan import (
    TargetDiversityLimiter,
    seeds_from_survivors,
)
from ubs_agent_universe import (
    score_config_for_variant,
    variant_symbol_not_offered,
)


def evaluate_history_probe(
    memory: AgentMemory,
    variant: Variant,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    min_report_mtime: float | None = None,
    report_path: Path | None = None,
    symbol_suffix: str = "",
    universe_symbols: set[str] | None = None,
) -> tuple[str, ScoreResult | None]:
    report = report_path or find_report_for_set(variant.path, min_mtime=min_report_mtime)
    if not report:
        # Las filas del probe viven en candidates, asi que un no_report aqui
        # tambien entra en el pool de retry.
        status = (
            SYMBOL_NOT_EXIST_STATUS
            if variant_symbol_not_offered(variant, universe_symbols, symbol_map)
            else "no_report"
        )
        record_history_probe_status(
            memory,
            variant,
            None,
            status,
            None,
            {"reasons": [status], "history_probe": True},
        )
        return status, None
    try:
        result = score_report_file(report, config=score_config, broker=broker)
    except Exception as exc:
        print(f"AVISO: no pude parsear probe historico {report}: {exc}")
        record_history_probe_status(
            memory,
            variant,
            None,
            "parse_error",
            report,
            {"reasons": ["parse_error"], "error": str(exc), "history_probe": True},
        )
        return "parse_error", None
    no_history = tester_log_no_history_metadata(
        report,
        variant,
        symbol_map,
        symbol_suffix,
    )
    if no_history:
        print(
            f"AVISO: {variant.target_symbol} sin historico completo para el rango pedido; "
            "marcado como no_history."
        )
        no_history["history_probe"] = True
        record_score_with_metadata(memory, variant.path, result, "no_history", report, no_history)
        return "no_history", result
    if report_has_empty_tester_context(result):
        record_history_probe_status(
            memory,
            variant,
            result,
            "pending_tester_context",
            report,
            {"reasons": ["empty_tester_context"], "history_probe": True},
        )
        return "pending_tester_context", result

    matches, mismatch_reason = report_matches_variant(
        variant, result, symbol_map, symbol_suffix, broker
    )
    if not matches:
        print(f"AVISO: probe historico no coincide para {variant.path.name}: {mismatch_reason}")
        record_history_probe_status(
            memory,
            variant,
            result,
            "report_mismatch",
            report,
            {"reasons": ["report_mismatch"], "mismatch": mismatch_reason, "history_probe": True},
        )
        return "report_mismatch", result

    record_history_probe_status(
        memory,
        variant,
        result,
        "history_ok",
        report,
        {"reasons": [], "history_probe": True},
    )
    return "history_ok", result


def _seed_evaluation_target(seed: Seed, result: ScoreResult, display_name: str) -> tuple[Seed, Variant]:
    expected_symbol = seed.symbol if seed.symbol and seed.symbol != "UNKNOWN" else str(result.symbol or "UNKNOWN")
    expected_period = seed.period if seed.period and seed.period != "UNKNOWN" else str(result.timeframe or "UNKNOWN").upper()
    evaluated_seed = Seed(
        path=seed.path, symbol=expected_symbol, period=expected_period,
        family=seed.family, run_strategy=seed.run_strategy,
    )
    variant = Variant(
        path=Path(display_name), seed=evaluated_seed,
        target_symbol=expected_symbol, target_period=expected_period,
        mutated_keys=(), missing_lot_keys=(), policy="seed_eval",
    )
    return evaluated_seed, variant


def _record_zero_trade_seed(
    memory: AgentMemory, seed: Seed, evaluated_seed: Seed, report: Path,
    result: ScoreResult,
) -> tuple[str, ScoreResult]:
    trade_disabled = trade_disabled_metadata(report)
    if trade_disabled:
        print(
            f"AVISO: el broker no permite abrir posiciones en {evaluated_seed.symbol}; "
            f"marcado como {TRADE_DISABLED_STATUS}."
        )
        record_seed_score_with_metadata(
            memory, evaluated_seed, result, TRADE_DISABLED_STATUS, report, trade_disabled,
        )
        return TRADE_DISABLED_STATUS, result
    metadata = execution_failure_metadata(report, result.symbol, result.timeframe)
    status = "rejected" if metadata else "no_trades"
    reason = execution_failure_reason(metadata) if metadata else "sin operaciones"
    print(f"AVISO: reporte seed {reason} para {seed.path.name}; marcado como {status}.")
    memory.record_seed_score(evaluated_seed, result, status, report, metadata=metadata)
    return status, result


def evaluate_seed_report(
    memory: AgentMemory,
    seed: Seed,
    report: Path,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    label: str | None = None,
    symbol_suffix: str = "",
    parsed_result: ScoreResult | None = None,
) -> tuple[str, ScoreResult | None]:
    display_name = label or seed.path.name
    result = parsed_result
    if result is None:
        try:
            result = score_report_file(report, config=score_config, broker=broker)
        except Exception as exc:
            print(f"AVISO: no pude parsear seed {display_name}: {exc}")
            memory.record_seed_score(seed, None, "parse_error", report)
            return "parse_error", None

    if seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN":
        print(
            f"AVISO: seed sin symbol/timeframe confirmado para {seed.path.name}; "
            "queda como report_mismatch hasta guardar override."
        )
        memory.record_seed_score(seed, result, "report_mismatch", report)
        return "report_mismatch", result

    if report_has_empty_tester_context(result):
        print(
            f"AVISO: reporte seed sin contexto tester para {display_name}; "
            "queda pendiente para un backtest nuevo."
        )
        memory.record_seed_score(seed, result, "pending_tester_context", report)
        return "pending_tester_context", result

    evaluated_seed, variant = _seed_evaluation_target(seed, result, display_name)
    matches, mismatch_reason = report_matches_variant(
        variant, result, symbol_map, symbol_suffix, broker
    )
    if not matches:
        print(f"AVISO: reporte seed no coincide para {seed.path.name}: {mismatch_reason}")
        memory.record_seed_score(evaluated_seed, result, "report_mismatch", report)
        return "report_mismatch", result

    if result.trades <= 0:
        return _record_zero_trade_seed(memory, seed, evaluated_seed, report, result)

    status = "accepted" if result.accepted else "rejected"
    memory.record_seed_score(evaluated_seed, result, status, report)
    return status, result


def evaluate_variants(
    memory: AgentMemory,
    variants: list[Variant],
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    min_report_mtime: float | None = None,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
    symbol_suffix: str = "",
    universe_symbols: set[str] | None = None,
) -> list[tuple[Variant, ScoreResult]]:
    scored: list[tuple[Variant, ScoreResult]] = []
    for variant in variants:
        status, result = evaluate_variant(
            memory,
            variant,
            score_config,
            symbol_map,
            broker,
            min_report_mtime=min_report_mtime,
            min_trades_w1=min_trades_w1,
            min_trades_mn=min_trades_mn,
            symbol_suffix=symbol_suffix,
            universe_symbols=universe_symbols,
        )
        if status not in {"accepted", "rejected"} or result is None:
            continue
        scored.append((variant, result))
    return scored


def evaluate_variant(
    memory: AgentMemory,
    variant: Variant,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    min_report_mtime: float | None = None,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
    symbol_suffix: str = "",
    universe_symbols: set[str] | None = None,
) -> tuple[str, ScoreResult | None]:
    report = find_report_for_set(variant.path, min_mtime=min_report_mtime)
    if not report:
        # Sin reporte hay dos causas distintas: un fallo tecnico (retryable) o
        # que el broker ya no ofrezca el simbolo, en cuyo caso MT5 ni abre el
        # tester.  Un simbolo deshabilitado a mano no entra aqui: sigue en el
        # universo, asi que su candidato se repara con normalidad.
        if variant_symbol_not_offered(variant, universe_symbols, symbol_map):
            print(
                f"AVISO: {variant.target_symbol} no esta en el universo del broker; "
                f"marcado como {SYMBOL_NOT_EXIST_STATUS} sin reintento."
            )
            memory.record_score(variant.path, None, SYMBOL_NOT_EXIST_STATUS, None)
            return SYMBOL_NOT_EXIST_STATUS, None
        memory.record_score(variant.path, None, "no_report", None)
        return "no_report", None
    return evaluate_variant_report(
        memory,
        variant,
        report,
        score_config,
        symbol_map,
        broker,
        min_trades_w1=min_trades_w1,
        min_trades_mn=min_trades_mn,
        symbol_suffix=symbol_suffix,
    )


def _record_variant_no_history(
    memory: AgentMemory, variant: Variant, report: Path, result: ScoreResult,
    no_history: dict,
) -> tuple[str, ScoreResult]:
    failed_symbols = no_history.get("failed_history_symbols") or []
    dependency_detail = (
        f"; falta historial dependiente de {', '.join(str(value) for value in failed_symbols)}"
        if failed_symbols else ""
    )
    recommendation = str(
        no_history.get("recommendation")
        or "desactivar simbolo y revisar historico del broker"
    )
    print(
        f"AVISO: {variant.target_symbol} sin historico del broker para el rango pedido"
        f"{dependency_detail}; "
        f"marcado como no_history. Recomendacion: {recommendation}."
    )
    record_score_with_metadata(memory, variant.path, result, "no_history", report, no_history)
    return "no_history", result


def _record_zero_trade_variant(
    memory: AgentMemory, variant: Variant, report: Path, result: ScoreResult,
) -> tuple[str, ScoreResult]:
    trade_disabled = trade_disabled_metadata(report)
    if trade_disabled:
        print(
            f"AVISO: el broker no permite abrir posiciones en {variant.target_symbol}; "
            f"marcado como {TRADE_DISABLED_STATUS}."
        )
        record_score_with_metadata(
            memory, variant.path, result, TRADE_DISABLED_STATUS, report, trade_disabled,
        )
        return TRADE_DISABLED_STATUS, result
    metadata = execution_failure_metadata(report, result.symbol, result.timeframe)
    status = "rejected" if metadata else "no_trades"
    memory.record_score(variant.path, result, status, report, metadata=metadata)
    if metadata:
        print(f"AVISO: {variant.path.name}: {execution_failure_reason(metadata)}.")
    return status, result


def evaluate_variant_report(
    memory: AgentMemory,
    variant: Variant,
    report: Path,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
    symbol_suffix: str = "",
) -> tuple[str, ScoreResult | None]:
    period_score_config = score_config_for_variant(
        score_config,
        variant,
        min_trades_w1=min_trades_w1,
        min_trades_mn=min_trades_mn,
    )
    try:
        result = score_report_file(report, config=period_score_config, broker=broker)
    except Exception as exc:
        print(f"AVISO: no pude parsear {report}: {exc}")
        memory.record_score(variant.path, None, "parse_error", report)
        return "parse_error", None
    no_history = tester_log_no_history_metadata(
        report,
        variant,
        symbol_map,
        symbol_suffix,
    )
    if no_history:
        return _record_variant_no_history(memory, variant, report, result, no_history)
    if report_has_empty_tester_context(result):
        print(
            f"AVISO: reporte sin contexto tester para {variant.path.name}; "
            "queda pendiente para un backtest nuevo."
        )
        memory.record_score(variant.path, result, "pending_tester_context", report)
        return "pending_tester_context", result
    matches, mismatch_reason = report_matches_variant(
        variant, result, symbol_map, symbol_suffix, broker
    )
    if not matches:
        print(f"AVISO: reporte no coincide para {variant.path.name}: {mismatch_reason}")
        memory.record_score(variant.path, result, "report_mismatch", report)
        return "report_mismatch", result
    if result.trades <= 0:
        return _record_zero_trade_variant(memory, variant, report, result)
    status = "accepted" if result.accepted else "rejected"
    memory.record_score(variant.path, result, status, report)
    return status, result


def select_survivors(
    scored: list[tuple[Variant, ScoreResult]],
    top_percent: float,
    *,
    allow_rejected_fallback: bool = True,
) -> list[tuple[Variant, ScoreResult]]:
    if not scored:
        return []
    scored = sorted(scored, key=lambda item: item[1].score, reverse=True)
    accepted = [item for item in scored if item[1].accepted]
    if not accepted and allow_rejected_fallback:
        limit = max(1, int(len(scored) * max(top_percent, 1.0) / 100.0))
        accepted = scored[:limit]
    return accepted


def select_next_seed_survivors(
    scored: list[tuple[Variant, ScoreResult]],
    top_percent: float,
    max_seeds: int,
    aliases: dict[str, str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    fitness_feedback: dict[str, float] | None = None,
    allow_rejected_fallback: bool = True,
) -> list[tuple[Variant, ScoreResult]]:
    survivors = select_survivors(scored, top_percent, allow_rejected_fallback=allow_rejected_fallback)
    if not survivors:
        return []
    if fitness_feedback and SELECTION_FITNESS_APPLIED_SCALE:
        survivors.sort(
            key=lambda item: item[1].score + (
                fitness_feedback.get(str(item[0].path), 0.0)
                * SELECTION_FITNESS_APPLIED_SCALE
            ),
            reverse=True,
        )
    if max_seeds <= 0:
        limit = len(survivors)
    else:
        top_limit = max(1, int(len(scored) * max(top_percent, 1.0) / 100.0))
        limit = min(len(survivors), max(max_seeds, top_limit))
    limiter = TargetDiversityLimiter(limit, aliases, group_by_symbol=group_by_symbol)
    selected: list[tuple[Variant, ScoreResult]] = []
    overflow: list[tuple[Variant, ScoreResult]] = []
    for item in survivors:
        variant, _result = item
        if limiter.allows(variant.target_symbol, variant.target_period):
            selected.append(item)
            limiter.record(variant.target_symbol, variant.target_period)
            if len(selected) >= limit:
                return selected
        else:
            overflow.append(item)
    for item in overflow:
        if len(selected) >= limit:
            break
        selected.append(item)
    return selected


def select_next_generation_survivors(
    memory: AgentMemory,
    run_id: int,
    scored: list[tuple[Variant, ScoreResult]],
    top_percent: float,
    max_seeds: int,
    aliases: dict[str, str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    allow_rejected_fallback: bool = True,
    fitness_target: str = FITNESS_TARGET_FINAL_TICK_6M,
) -> list[tuple[Variant, ScoreResult]]:
    accepted = select_survivors(scored, top_percent, allow_rejected_fallback=allow_rejected_fallback)
    predictions = memory.seed_selection_predictions(
        seeds_from_survivors(accepted),
        exclude_run_id=run_id,
        target=fitness_target,
    )
    fitness_feedback = {path: prediction.weight for path, prediction in predictions.items()}
    return select_next_seed_survivors(
        scored,
        top_percent,
        max_seeds,
        aliases,
        group_by_symbol,
        fitness_feedback,
        allow_rejected_fallback,
    )


def generation_fitness_target(force_unseeded_universe: bool) -> str:
    """Keep both generation modes aligned with the usable-strategy objective."""

    return FITNESS_TARGET_FINAL_TICK_6M


def generation_feedback_terminal_stage(force_unseeded_universe: bool) -> str | None:
    """Keep Discovery feedback bounded at FT 6M; Production may use regression."""

    return "six_month" if force_unseeded_universe else None


def generation_seed_fitness_predictions(
    memory: AgentMemory,
    seeds: list[Seed],
    *,
    run_id: int,
    force_unseeded_universe: bool,
) -> dict[str, SelectionPrediction]:
    """Use child 6M yield for Discovery sources and metric fitness in Production."""

    if force_unseeded_universe:
        return memory.discovery_seed_descendant_predictions(
            seeds,
            exclude_run_id=run_id,
        )
    return memory.seed_selection_predictions(
        seeds,
        exclude_run_id=run_id,
        target=FITNESS_TARGET_FINAL_TICK_6M,
    )


def copy_accepted(survivors: list[tuple[Variant, ScoreResult]], accepted_dir: Path) -> list[Path]:
    if not survivors:
        return []
    accepted_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for variant, result in survivors:
        source_path = resolve_workspace_path(variant.path)
        for previous in accepted_dir.glob(f"*__{source_path.name}"):
            if previous.is_file():
                previous.unlink()
        destination = accepted_dir / f"score_{result.score:07.2f}__{source_path.name}"
        write_set_use_every_tick(source_path, destination, False)
        copied.append(destination)
    return copied


def recreate_work_dir(path: Path) -> Path:
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(path)
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_candidate_copies(run_dir: Path, generation: int, set_name: str) -> None:
    for prefix in ("accepted", "mismatch"):
        folder = run_dir / f"{prefix}_gen_{generation:03d}"
        if not folder.exists():
            continue
        for path in folder.glob(f"*__{set_name}"):
            if path.is_file():
                path.unlink()


def remove_report_artifacts(set_path: Path) -> None:
    for path in (BASE_DIR / "reports").glob(f"{set_path.stem}*"):
        if path.is_file() and path.suffix.lower() in {".htm", ".html", ".xml", ".png", ".set"}:
            path.unlink()


def count_valid_existing_reports(
    variants: list[Variant],
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    broker: object,
    *,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
    symbol_suffix: str = "",
) -> int:
    valid = 0
    for variant in variants:
        report = find_report_for_set(variant.path, min_mtime=None)
        if not report:
            continue
        try:
            result = score_report_file(
                report,
                config=score_config_for_variant(
                    score_config,
                    variant,
                    min_trades_w1=min_trades_w1,
                    min_trades_mn=min_trades_mn,
                ),
                broker=broker,
            )
        except Exception:
            continue
        matches, _ = report_matches_variant(
            variant, result, symbol_map, symbol_suffix, broker
        )
        if matches:
            valid += 1
    return valid


def prepare_final_tick_exec_dir(path: Path, variants: list[Variant]) -> Path:
    exec_dir = recreate_work_dir(path)
    for variant in variants:
        shutil.copy2(variant.path, exec_dir / variant.path.name)
    return exec_dir
