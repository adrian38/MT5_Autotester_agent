"""Etapas tick a tick de las seeds: corto (probe) y largo (6M).

Es el mismo contraste que el Final Tick de los candidatos -la misma estrategia
corrida con dos modelos de datos sobre el mismo tramo- pero aplicado a las
semillas, que no cuelgan de ningun run: cada fila se identifica por su
``seed_path``, igual que en ``seed_scores``.

La decision no se duplica: el veredicto sale de ``final_tick_similarity`` y de
``final_tick_status_from_similarity``, las mismas funciones que deciden el de
los candidatos. Lo propio de aqui es el recorrido de filas, donde se copian los
.set y que tabla se escribe.

Diferencia deliberada con los candidatos: no hay ventana OHLC alternativa para
las filas ``pending_ohlc_trades``. El estado se guarda igual -y por tanto 6M
sigue siendo elegible tras un probe con pocas operaciones- pero no existe un
segundo rango de fechas que reintentar; la seed se relanza con las fechas de la
etapa cuando el usuario las cambia.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from run_tests import RUNNING_TERMINAL_EXIT_CODE, parse_symbol_map
from ubs.account import DEFAULT_BROKER
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path
from ubs.score import ScoreConfig, ScoreResult, score_report_file
from ubs.set_utils import write_set_use_every_tick
from ubs_agent_evaluate import recreate_work_dir, remove_report_artifacts
from ubs_agent_final_tick import (
    final_tick_ohlc_trades_pending_payload,
    final_tick_row_pending_for_dates,
    final_tick_status_from_similarity,
    lossless_control_gate_from_args,
    normalize_final_tick_stage,
    validate_final_tick_stage_dates,
)
from ubs_agent_final_tick_similarity import final_tick_similarity
from ubs_agent_reports import (
    find_report_for_set,
    report_has_empty_tester_context,
    report_matches_variant,
    tester_log_no_history_metadata,
)
from ubs_agent_sets import copy_seed_for_backtest
from ubs_agent_universe import (
    min_trades_for_period,
    missing_report_status,
    score_config_for_variant,
)
from ubs_agent_variants import run_backtests


SEED_FINAL_TICK_STAGE_DIRS = {
    "probe": "seed_final_tick",
    "six_month": "seed_final_tick_6m",
}
SEED_FINAL_TICK_STAGE_LABELS = {
    "probe": "Seeds Tick",
    "six_month": "Seeds Tick 6M",
}


def seed_final_tick_stage_label(stage: str) -> str:
    return SEED_FINAL_TICK_STAGE_LABELS[normalize_final_tick_stage(stage)]


def _seed_from_row(row) -> Seed:
    return Seed(
        path=resolve_workspace_path(row["seed_path"]),
        symbol=str(row["symbol"] or "").strip().upper(),
        period=str(row["period"] or "").strip().upper(),
        family=str(row["family"] or ""),
        run_strategy=str(row["run_strategy"] or ""),
    )


def _seed_variant(seed: Seed, set_path: Path, policy: str) -> Variant:
    return Variant(
        path=set_path,
        seed=seed,
        target_symbol=seed.symbol,
        target_period=seed.period,
        mutated_keys=(),
        missing_lot_keys=(),
        policy=policy,
    )


def _seed_stage_score_config(
    score_config: ScoreConfig, variant: Variant, args: argparse.Namespace
) -> ScoreConfig:
    return score_config_for_variant(
        score_config,
        variant,
        min_trades_w1=args.final_tick_min_trades_w1,
        min_trades_mn=args.final_tick_min_trades_mn,
    )


class _SeedFinalTickRecord:
    """Escritura de una fila de etapa con los umbrales de la pasada."""

    def __init__(self, memory: AgentMemory, args: argparse.Namespace, seed: Seed, stage: str):
        self.memory = memory
        self.args = args
        self.seed = seed
        self.stage = stage
        self.ohlc_report: Path | None = None
        self.ohlc_result: ScoreResult | None = None
        self.real_tick_report: Path | None = None

    def write(
        self,
        status: str,
        real_tick_result: ScoreResult | None = None,
        *,
        similarity: str | None = None,
        history_quality: float | None = None,
    ) -> str:
        args = self.args
        self.memory.record_seed_final_tick(
            str(self.seed.path),
            self.seed.symbol,
            self.seed.period,
            status,
            self.ohlc_result,
            real_tick_result,
            self.ohlc_report,
            self.real_tick_report,
            similarity,
            history_quality,
            args.final_tick_min_history_quality,
            args.from_date,
            args.to_date,
            args.final_tick_max_net_delta_pct,
            args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct,
            args.final_tick_max_trades_delta_pct,
            final_tick_stage=self.stage,
        )
        return status


def _seed_final_tick_argument_errors(args, label: str) -> int | None:
    """Comprueba experto, terminal y fechas antes de tocar la memoria."""
    if not args.expert and not args.multi_terminal and not args.dry_run:
        print(f"ERROR: {label} requiere --expert o --multi-terminal")
        return 1
    if not str(args.from_date or "").strip() or not str(args.to_date or "").strip():
        print(
            f"ERROR: {label} requiere --from-date y --to-date para comparar el "
            "mismo tramo OHLC vs real tick."
        )
        return 1
    stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    date_error = validate_final_tick_stage_dates(stage, str(args.from_date), str(args.to_date))
    if date_error:
        print(f"ERROR: {date_error}")
        return 1
    return None


def _seed_final_tick_rows(args, memory: AgentMemory, stage: str) -> list:
    """Seeds elegibles con .set existente, filtradas por el modo pedido."""
    rows = [
        row
        for row in memory.accepted_seeds_for_final_tick(final_tick_stage=stage)
        if resolve_workspace_path(row["seed_path"]).exists()
    ]
    rows = [
        row
        for row in rows
        if str(row["symbol"] or "").strip().upper() not in {"", "UNKNOWN"}
        and str(row["period"] or "").strip().upper() not in {"", "UNKNOWN"}
    ]
    if not getattr(args, "seed_final_tick_pending_only", False):
        return rows
    return [
        row
        for row in rows
        if final_tick_row_pending_for_dates(
            row,
            str(args.from_date),
            str(args.to_date),
            final_tick_stage=stage,
            force_quality_retry=bool(
                getattr(args, "final_tick_retry_pending_quality", False)
            ),
        )
    ]


def _copy_seed_stage_sets(args, rows, ohlc_dir: Path, real_tick_dir: Path, symbol_map):
    """Copia cada seed a su .set OHLC y a su .set Every Tick."""
    copied: list[tuple[Seed, Variant, Variant]] = []
    for index, row in enumerate(rows, start=1):
        seed = _seed_from_row(row)
        stem = f"{index:05d}_{seed.path.stem}"
        ohlc_set = ohlc_dir / f"ohlc_{stem}.set"
        real_tick_set = real_tick_dir / f"tick_{stem}.set"
        # copy_seed_for_backtest aplica el mapa y el sufijo del broker al
        # ForceSymbol; sin eso MT5 cierra sin reporte en las cuentas con sufijo.
        copy_seed_for_backtest(seed, ohlc_set, symbol_map, args.symbol_suffix)
        copy_seed_for_backtest(seed, real_tick_set, symbol_map, args.symbol_suffix)
        write_set_use_every_tick(ohlc_set, ohlc_set, False)
        write_set_use_every_tick(real_tick_set, real_tick_set, True)
        if not args.dry_run:
            remove_report_artifacts(ohlc_set)
            remove_report_artifacts(real_tick_set)
        copied.append((
            seed,
            _seed_variant(seed, ohlc_set, "seed_final_tick_ohlc"),
            _seed_variant(seed, real_tick_set, "seed_final_tick_real"),
        ))
    return copied


def _print_seed_final_tick_header(args, label: str, copied, stage_dir: Path) -> None:
    mode = "pendientes" if getattr(args, "seed_final_tick_pending_only", False) else "todas"
    print(f"{label}: modo={mode}; seeds={len(copied)}")
    print(f"Directorio {label}: {stage_dir}")
    print(f"Fechas {label}: {args.from_date} -> {args.to_date}")
    print(
        f"Criterios {label}: "
        f"History Quality>={args.final_tick_min_history_quality:.2f}% | "
        f"Net delta<={args.final_tick_max_net_delta_pct:.2f}% | "
        f"PF delta<={args.final_tick_max_pf_delta_pct:.2f}% | "
        f"DD delta<={args.final_tick_max_dd_delta_pct:.2f}% | "
        f"Trades delta<={args.final_tick_max_trades_delta_pct:.2f}%"
    )


def _score_seed_ohlc_control(args, score_config, seed, ohlc_variant, record, report, symbol_map) -> str | None:
    """Puntua el OHLC de control; devuelve el estado si la seed no sigue."""
    try:
        result = score_report_file(
            report,
            config=_seed_stage_score_config(score_config, ohlc_variant, args),
            broker=args.broker,
        )
    except Exception as exc:
        print(f"AVISO: no pude parsear OHLC de {seed.path.name}: {exc}")
        record.ohlc_report = report
        return record.write("parse_error")
    record.ohlc_report = report
    record.ohlc_result = result
    matches, mismatch = report_matches_variant(
        ohlc_variant,
        result,
        symbol_map,
        getattr(args, "symbol_suffix", ""),
        getattr(args, "broker", DEFAULT_BROKER),
    )
    if not matches:
        print(f"AVISO: OHLC de {seed.path.name} no coincide: {mismatch}")
        return record.write("report_mismatch", history_quality=result.history_quality)
    minimum = min_trades_for_period(
        seed.period,
        int(args.final_tick_min_ohlc_trades),
        int(args.final_tick_min_trades_w1),
        int(args.final_tick_min_trades_mn),
    )
    if int(result.trades) < minimum:
        # Pocas operaciones no son una divergencia: la fila queda pendiente y
        # sigue siendo elegible para 6M, donde la ventana larga da muestra.
        return record.write(
            "pending_ohlc_trades",
            similarity=json.dumps(
                final_tick_ohlc_trades_pending_payload(result, minimum),
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
    return None


def _collect_seed_ohlc_results(
    args, memory, score_config, copied, stage, min_report_mtime, symbol_map, status_counts,
):
    """Deja listas para Every Tick las seeds cuyo OHLC de control es usable."""
    ready: list[tuple[Seed, Variant, _SeedFinalTickRecord]] = []
    for seed, ohlc_variant, real_tick_variant in copied:
        record = _SeedFinalTickRecord(memory, args, seed, stage)
        report = find_report_for_set(ohlc_variant.path, min_mtime=min_report_mtime)
        if not report:
            status = record.write(
                missing_report_status(
                    seed.symbol, args, symbol_map, set_path=ohlc_variant.path,
                )
            )
        else:
            status = _score_seed_ohlc_control(
                args, score_config, seed, ohlc_variant, record, report, symbol_map,
            )
        if status is None:
            ready.append((seed, real_tick_variant, record))
            continue
        status_counts[status] = status_counts.get(status, 0) + 1
    return ready


def _seed_tick_pending_history(
    args, symbol_map, seed, real_tick_variant, record, report, result,
) -> str:
    """Reporte Every Tick sin contexto de tester: pendiente de historico.

    Distingue el fallo tecnico de descarga -lo acredita el log del tester- de
    un reporte vacio sin mas, igual que la ruta de los candidatos.
    """
    history = tester_log_no_history_metadata(
        report, real_tick_variant, symbol_map, getattr(args, "symbol_suffix", ""),
    )
    pending = {
        "accepted": False,
        "reasons": ["real_tick_no_history" if history else "empty_tester_context"],
        "history_quality": result.history_quality,
        "min_history_quality": float(args.final_tick_min_history_quality),
        "technical_failure": bool(history),
        "checks": {},
        "history": history or {},
    }
    print(
        f"AVISO: reporte Every Tick sin contexto usable para {seed.path.name}; "
        "queda pendiente de historico."
    )
    return record.write(
        "pending_history_quality",
        similarity=json.dumps(pending, ensure_ascii=True, sort_keys=True),
        history_quality=result.history_quality,
    )


def _evaluate_seed_tick_report(
    args, memory, score_config, symbol_map, stage, seed, real_tick_variant, record, report,
) -> str:
    """Compara el informe Every Tick de una seed con su OHLC de control."""
    record.real_tick_report = report
    try:
        result = score_report_file(
            report,
            config=_seed_stage_score_config(score_config, real_tick_variant, args),
            broker=args.broker,
        )
    except Exception as exc:
        print(f"AVISO: no pude parsear Every Tick de {seed.path.name}: {exc}")
        return record.write("parse_error")

    if report_has_empty_tester_context(result):
        return _seed_tick_pending_history(
            args, symbol_map, seed, real_tick_variant, record, report, result,
        )

    matches, mismatch = report_matches_variant(
        real_tick_variant,
        result,
        symbol_map,
        getattr(args, "symbol_suffix", ""),
        getattr(args, "broker", DEFAULT_BROKER),
    )
    if not matches:
        print(f"AVISO: Every Tick de {seed.path.name} no coincide: {mismatch}")
        return record.write("report_mismatch", result, history_quality=result.history_quality)

    is_six_month = stage == "six_month"
    similarity = final_tick_similarity(
        record.ohlc_result,
        result,
        min_history_quality=args.final_tick_min_history_quality,
        max_net_delta_pct=args.final_tick_max_net_delta_pct,
        max_pf_delta_pct=(
            min(float(args.final_tick_max_pf_delta_pct), 30.0)
            if is_six_month
            else args.final_tick_max_pf_delta_pct
        ),
        max_dd_delta_pct=args.final_tick_max_dd_delta_pct,
        max_trades_delta_pct=args.final_tick_max_trades_delta_pct,
        min_model_profit_factor=float(score_config.min_profit_factor) if is_six_month else None,
        lossless_control_gate=lossless_control_gate_from_args(args) if is_six_month else None,
    )
    return record.write(
        final_tick_status_from_similarity(similarity) or "rejected",
        result,
        similarity=json.dumps(similarity, ensure_ascii=True, sort_keys=True),
        history_quality=result.history_quality,
    )


def _run_seed_tick_leg(
    args, memory, score_config, symbol_map, stage, ready, real_tick_dir, status_counts,
) -> int | None:
    """Lanza el tramo Every Tick y puntua lo que devuelva."""
    started_at = time.time()
    code = run_backtests(
        args, real_tick_dir, model="4", from_date=args.from_date, to_date=args.to_date,
    )
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto Every Tick porque hay una terminal MT5 abierta.")
        return 1
    if code != 0:
        print(f"AVISO: Every Tick termino con codigo {code}; se evaluaran los reportes disponibles")

    for seed, real_tick_variant, record in ready:
        report = find_report_for_set(real_tick_variant.path, min_mtime=started_at - 1.0)
        if not report:
            status = record.write(
                missing_report_status(
                    seed.symbol, args, symbol_map, set_path=real_tick_variant.path,
                )
            )
        else:
            status = _evaluate_seed_tick_report(
                args, memory, score_config, symbol_map, stage,
                seed, real_tick_variant, record, report,
            )
        status_counts[status] = status_counts.get(status, 0) + 1
    return None


def _seed_final_tick_dirs(args, stage: str) -> tuple[Path, Path, Path]:
    """Directorio nuevo de la pasada y sus dos subcarpetas de .set."""
    stage_dir = recreate_work_dir(
        resolve_workspace_path(args.output_dir)
        / SEED_FINAL_TICK_STAGE_DIRS[stage]
        / datetime.now().strftime("stage_%Y%m%d_%H%M%S")
    )
    ohlc_dir = stage_dir / "ohlc_sets"
    real_tick_dir = stage_dir / "real_tick_sets"
    ohlc_dir.mkdir(parents=True, exist_ok=True)
    real_tick_dir.mkdir(parents=True, exist_ok=True)
    return stage_dir, ohlc_dir, real_tick_dir


def evaluate_seed_final_tick(
    args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig
) -> int:
    """Corre una etapa tick a tick completa sobre las seeds elegibles."""
    stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    label = seed_final_tick_stage_label(stage)
    argument_error = _seed_final_tick_argument_errors(args, label)
    if argument_error is not None:
        return argument_error

    symbol_map = parse_symbol_map(args.symbol_map)
    rows = _seed_final_tick_rows(args, memory, stage)
    if not rows:
        print(f"{label}: no hay seeds elegibles pendientes; memoria={memory.path}")
        return 0

    stage_dir, ohlc_dir, real_tick_dir = _seed_final_tick_dirs(args, stage)
    copied = _copy_seed_stage_sets(args, rows, ohlc_dir, real_tick_dir, symbol_map)
    _print_seed_final_tick_header(args, label, copied, stage_dir)
    if args.dry_run:
        return 0

    ohlc_started_at = time.time()
    ohlc_code = run_backtests(
        args, ohlc_dir, model="1", from_date=args.from_date, to_date=args.to_date,
    )
    if ohlc_code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto el OHLC de control porque hay una terminal MT5 abierta.")
        return 1
    if ohlc_code != 0:
        print(f"AVISO: OHLC de control termino con codigo {ohlc_code}; se evaluaran los reportes disponibles")

    status_counts: dict[str, int] = {}
    ready = _collect_seed_ohlc_results(
        args, memory, score_config, copied, stage,
        ohlc_started_at - 1.0, symbol_map, status_counts,
    )
    if ready:
        tick_code = _run_seed_tick_leg(
            args, memory, score_config, symbol_map, stage, ready, real_tick_dir, status_counts,
        )
        if tick_code is not None:
            return tick_code
    else:
        print(f"{label}: ningun OHLC de control cumple el minimo de operaciones; no se lanza Every Tick.")

    print(
        f"{label} terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0
