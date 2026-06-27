from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from run_tests import (
    RUNNING_TERMINAL_EXIT_CODE,
    TIMEFRAME_ENUM,
    apply_symbol_map,
    load_set_params,
    normalize_set_symbol,
    parse_symbol_map,
)
from ubs_generate_sets import format_like, parse_numeric
from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_disabled_symbols_path,
    account_memory_path,
    account_output_dir,
    account_seed_dir,
    broker_asset_universe_path_with_fallback,
    load_account_timeframe_universe,
    migrate_legacy_account_storage,
    normalize_account_type,
    normalize_broker,
)
from ubs.memory import AgentMemory, final_tick_table_for_stage, variant_from_candidate_row
from ubs.models import Seed, Variant
from ubs.score import ScoreConfig, ScoreResult, score_report_file
from ubs.seeds import file_digest, load_seeds, seed_eval_filename, seed_from_path
from ubs.set_utils import compact_safe_part, force_fixed_lot_text, read_set_with_encoding, safe_part, write_set_text
from ubs.universe import (
    canonical_symbol,
    load_asset_universe,
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
    seed_symbol_disabled,
)
from ubs.weights import (
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    percentile_multipliers,
)


BASE_DIR = Path(__file__).resolve().parent
MUTATION_OVERRIDES_FILE = BASE_DIR / "outputs" / "ubs_mutation_overrides.json"
GLOBAL_PARAMS_FILE = BASE_DIR / "outputs" / "ubs_global_params.json"
DEFAULT_SOURCE = BASE_DIR / "sets" / "ubs_ready"
DEFAULT_OUTPUT = BASE_DIR / "outputs" / "ubs_agent"
DEFAULT_MEMORY = account_memory_path(BASE_DIR, DEFAULT_ACCOUNT_TYPE, DEFAULT_BROKER)
DEFAULT_TEMPLATE = BASE_DIR / "tester_template.ini"
DEFAULT_ASSETS = BASE_DIR / "assets" / "roboforex_assets.ini"
DEFAULT_DISABLED_SYMBOLS = account_disabled_symbols_path(BASE_DIR, DEFAULT_ACCOUNT_TYPE, DEFAULT_BROKER)
DEFAULT_SYMBOL_MAP = "CRUDEOIL=WTI,XTIUSD=WTI,USTEC=.USTECHCash,US100=.USTECHCash,US30=.US30Cash,US500=.US500Cash,DAX=.DE40Cash,DE40=.DE40Cash"
FINAL_TICK_6M_MIN_DAYS = 180
TIMEFRAME_TO_ENUM = {period: value for value, period in TIMEFRAME_ENUM.items()}
BASE_TIMEFRAME_UNIVERSE = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
EXPERIMENTAL_LONG_TIMEFRAMES = ("W1", "MN")
TIMEFRAME_UNIVERSE = BASE_TIMEFRAME_UNIVERSE
GENERATION_MODES = ("production", "discovery")
SELECTION_FITNESS_MODE = "observe_only"
SELECTION_FITNESS_APPLIED_SCALE = 0.0
ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION = {1: 0.35, 2: 0.25}
ASSET_UNSEEDED_FORCE_PROB_LATE = 0.15
TF_UNSEEDED_FORCE_PROB_BY_GENERATION = {1: 0.20, 2: 0.12}
TF_UNSEEDED_FORCE_PROB_LATE = 0.08
FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS = {
    "M1": 0.02,
    "M5": 0.02,
    "M15": 0.03,
    "M30": 0.05,
}
TARGET_PAIR_CAP_RATIO = 0.30
TARGET_SYMBOL_CAP_RATIO = 0.45
TARGET_TIMEFRAME_CAP_RATIO = 0.60
DEFAULT_TARGET_GROUP_CAP_RATIO = 0.40
TARGET_GROUP_CAP_RATIOS = {
    "Forex": 0.60,
    "Stocks": 0.60,
    "Metals": 0.40,
    "IndicesEnergies": 0.35,
    "Crypto": 0.25,
}
DIVERSITY_REROLL_ATTEMPTS = 24
FINAL_TICK_RETRYABLE_STATUSES = {
    "pending",
    "no_report",
    "parse_error",
    "report_mismatch",
}
FINAL_TICK_DATE_RETRYABLE_STATUSES = {
    "pending_history_quality",
}

CORE_MUTATION_KEYS = {
    "1": (
        "Exit_stop",
        "Exit_limit",
        "Exit_BE_start",
        "Exit_BE_extra_pips",
        "Exit_TrailSL_size",
        "Exit_TrailSL_Start",
        "Exit_TrailSL_step",
        "ST1_MinDist_to_HL",
        "ST1_countback",
        "ST1_HL_strength_L",
        "ST1_HL_strength_R",
        "MinDist_orders",
        "ST1_Expiration_hours",
    ),
    "2": (
        "Exit_stop",
        "Exit_limit",
        "Exit_BE_start",
        "Exit_BE_extra_pips",
        "Exit_TrailSL_size",
        "Exit_TrailSL_Start",
        "Exit_TrailSL_step",
        "VolCandles",
        "minSize",
        "AtrPeriod",
        "DevFactor",
        "VolMaxTrades",
    ),
    "": (
        "Exit_stop",
        "Exit_limit",
        "Exit_BE_start",
        "Exit_BE_extra_pips",
        "Exit_TrailSL_size",
        "Exit_TrailSL_Start",
        "Exit_TrailSL_step",
    ),
}
FROZEN_KEYS = {
    "PrintLogs",
    "PrintSetLoadingInfo",
    "ShowInfoPanel",
    "UpdateInfoTesting",
    "InfoPanelSizeAdjust",
    "SetFontSize",
    "EP",
    "RF",
    "TR",
    "MTR",
    "AdjustLotsizeToVariableValues",
    "Risk",
    "StartLots",
    "Lic_key",
    "URL",
    "LICURL",
    "LICURLB",
    "Sets_Folder",
    "UseAutoLoader",
    "UseCommonFolder",
    "Run_Strategy",
    "EA_MagicNumber",
    "ST1_MagicNumber",
    "ST2_MagicNumber",
    "EA_Comment",
    "ST1_Comment",
    "ST2_Comment",
}
FROZEN_PREFIXES = ("Grid", "PropFirm", "CloseAt", "Broker_GMT", "AutoGMT", "UseMQL5Calendar", "NFP_", "Manual", "MaxRisk")
ALLOWED_MUTATION_PREFIXES = ("ST1_", "Vol", "Exit_")
ALLOWED_MUTATION_KEYS = {
    "SpreadFilter",
    "MaxSpread",
    "DistForSpreadFilter",
    "MinDist_orders",
    "MaxTrades",
    "DevFactor",
    "AtrPeriod",
    "minSize",
    "ATRDefault",
    "ATR_Period",
    "DefaultValue",
}


_overrides_cache: tuple[set[str], set[str]] | None = None
_overrides_mtime: float = -1.0


def load_mutation_overrides() -> tuple[dict[str, str], set[str]]:
    """Return (frozen_override, mutable_override) from the user-editable JSON file.

    frozen_override: {key: forced_value} — key is frozen and the agent injects this value.
    mutable_override: {key} — normally frozen key that the user has made mutable.
    Results are cached until the file changes on disk.
    """
    global _overrides_cache, _overrides_mtime
    try:
        mtime = MUTATION_OVERRIDES_FILE.stat().st_mtime if MUTATION_OVERRIDES_FILE.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _overrides_cache is not None and mtime == _overrides_mtime:
        return _overrides_cache  # type: ignore[return-value]
    _overrides_mtime = mtime
    if mtime == 0.0:
        _overrides_cache = ({}, set())
        return _overrides_cache  # type: ignore[return-value]
    try:
        data = json.loads(MUTATION_OVERRIDES_FILE.read_text(encoding="utf-8"))
        raw_frozen = data.get("frozen_override", {})
        # Support legacy list format (no values)
        if isinstance(raw_frozen, list):
            raw_frozen = {k: "" for k in raw_frozen}
        _overrides_cache = (
            {str(k): str(v) for k, v in raw_frozen.items()},
            set(data.get("mutable_override", [])),
        )
    except Exception:
        _overrides_cache = ({}, set())
    return _overrides_cache  # type: ignore[return-value]


def save_mutation_overrides(frozen_override: dict[str, str], mutable_override: set[str]) -> None:
    """Write user mutation overrides to disk and invalidate the cache."""
    global _overrides_cache, _overrides_mtime
    MUTATION_OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MUTATION_OVERRIDES_FILE.write_text(
        json.dumps(
            {
                "frozen_override": {k: frozen_override[k] for k in sorted(frozen_override)},
                "mutable_override": sorted(mutable_override),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _overrides_cache = None
    _overrides_mtime = -1.0


def load_global_params() -> dict[str, str]:
    """Load the global parameter values from ubs_global_params.json."""
    if not GLOBAL_PARAMS_FILE.exists():
        return {}
    try:
        return {str(k): str(v) for k, v in json.loads(GLOBAL_PARAMS_FILE.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def save_global_params(params: dict[str, str]) -> None:
    """Save all global parameter values to ubs_global_params.json."""
    GLOBAL_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_PARAMS_FILE.write_text(
        json.dumps({k: params[k] for k in sorted(params)}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_agent_mutable_key(key: str) -> bool:
    """Return True if the agent is allowed to mutate this key."""
    frozen_ov, mutable_ov = load_mutation_overrides()
    if key in frozen_ov:
        return False
    if key in mutable_ov:
        return True
    if key in FROZEN_KEYS or any(key.startswith(p) for p in FROZEN_PREFIXES):
        return False
    return key in ALLOWED_MUTATION_KEYS or any(key.startswith(p) for p in ALLOWED_MUTATION_PREFIXES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agente UBS con seleccion de assets, mutacion guiada y memoria.")
    score_defaults = ScoreConfig()
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER, help="Broker UBS: ROBOFOREX, ICTRADING o AXI.")
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE, help="Tipo de cuenta UBS del broker seleccionado.")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--memory", default=str(DEFAULT_MEMORY))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--assets", default=str(DEFAULT_ASSETS))
    parser.add_argument("--expert", help="Ruta .ex5 UBS para ejecutar backtests.")
    parser.add_argument("--mt5-path", help="Ruta terminal64.exe.")
    parser.add_argument("--data-dir", help="Carpeta de datos MT5.")
    parser.add_argument("--terminals-config", help="Archivo .ini con perfiles multiterminal.")
    parser.add_argument("--multi-terminal", action="store_true", help="Ejecuta backtests UBS repartidos entre terminales configuradas.")
    parser.add_argument("--max-workers", type=int, default=1, help="Maximo de terminales simultaneas con --multi-terminal.")
    parser.add_argument("--symbol-map", default=DEFAULT_SYMBOL_MAP)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--variants-per-seed", type=int, default=3)
    parser.add_argument("--max-seeds", type=int, default=30)
    parser.add_argument("--mutations-per-variant", type=int, default=6)
    parser.add_argument("--top-percent", type=float, default=20.0)
    parser.add_argument(
        "--generation-mode",
        choices=GENERATION_MODES,
        default="production",
        help="production prioriza rendimiento conocido; discovery activa exploracion del universo sin seed.",
    )
    parser.add_argument(
        "--force-unseeded-universe",
        action="store_true",
        help="Alias legacy de --generation-mode discovery.",
    )
    parser.add_argument(
        "--experimental-long-timeframes",
        action="store_true",
        help="Incluye W1/MN como targets experimentales de generacion.",
    )
    parser.add_argument("--continue-last-run", action="store_true", help="Usa la ultima generacion registrada como seeds.")
    parser.add_argument(
        "--backtest-pending-only",
        action="store_true",
        help="Con --continue-last-run, ejecuta solo candidatos generated pendientes y no crea generaciones nuevas.",
    )
    parser.add_argument("--evaluate-seeds", action="store_true", help="Backtestea y puntua las semillas UBS nuevas o modificadas.")
    parser.add_argument("--evaluate-robustness", action="store_true", help="Backtestea candidatos accepted de un run en ventana OOS/robustez.")
    parser.add_argument("--robust-run-id", type=int, help="Run SQLite cuyos accepted se enviaran al test de robustez.")
    parser.add_argument(
        "--robust-candidate-id",
        type=int,
        action="append",
        help="Limita robustez a un candidate id concreto. Puede repetirse para varios candidatos.",
    )
    parser.add_argument("--robust-pending-only", action="store_true", help="Con --evaluate-robustness, testea solo accepted sin robustez registrada.")
    parser.add_argument("--robust-positive-bonus", type=float, default=DEFAULT_ROBUST_POSITIVE_BONUS, help="Bonus de peso si el candidato pasa robustez.")
    parser.add_argument("--robust-negative-bonus", type=float, default=DEFAULT_ROBUST_NEGATIVE_BONUS, help="Bonus de peso si el candidato falla robustez.")
    parser.add_argument("--evaluate-final-tick", action="store_true", help="Compara OHLC vs Every tick based on real ticks para robustez accepted.")
    parser.add_argument("--final-tick-run-id", type=int, help="Run SQLite cuyos robust accepted se enviaran al test Final Tick.")
    parser.add_argument(
        "--final-tick-stage",
        choices=("probe", "six_month"),
        default="probe",
        help="Etapa Final Tick: probe=filtro corto; six_month=validacion 6M para uso en portafolio.",
    )
    parser.add_argument("--final-tick-pending-only", action="store_true", help="Con --evaluate-final-tick, testea solo robust accepted sin Final Tick.")
    parser.add_argument("--final-tick-retry-pending-quality", action="store_true", help="Con --final-tick-pending-only, incluye filas pending_history_quality aunque las fechas no hayan cambiado.")
    parser.add_argument("--final-tick-reconcile-only", action="store_true", help="Con --evaluate-final-tick, concilia reportes OHLC/Every Tick ya existentes en disco sin abrir MT5.")
    parser.add_argument("--final-tick-skip-ohlc", action="store_true", help="Salta el backtest OHLC y reutiliza ohlc_metrics_json guardado en DB; solo ejecuta Every Tick.")
    parser.add_argument("--final-tick-min-history-quality", type=float, default=80.0, help="Calidad minima History Quality del reporte real tick.")
    parser.add_argument("--final-tick-min-ohlc-trades", type=int, default=5, help="Operaciones OHLC minimas para pasar a Every Tick.")
    parser.add_argument("--final-tick-min-trades-w1", type=int, default=2, help="Operaciones minimas Final Tick para W1.")
    parser.add_argument("--final-tick-min-trades-mn", type=int, default=1, help="Operaciones minimas Final Tick para MN.")
    parser.add_argument("--final-tick-ohlc-from-date", default="", help="Fecha alternativa para reintentar pendientes por pocas operaciones OHLC.")
    parser.add_argument("--final-tick-ohlc-to-date", default="", help="Fecha alternativa final para reintentar pendientes por pocas operaciones OHLC.")
    parser.add_argument("--final-tick-max-net-delta-pct", type=float, default=35.0, help="Diferencia maxima de net normalizado vs OHLC.")
    parser.add_argument("--final-tick-max-pf-delta-pct", type=float, default=35.0, help="Diferencia maxima de PF vs OHLC.")
    parser.add_argument("--final-tick-max-dd-delta-pct", type=float, default=35.0, help="Diferencia maxima de DD pct vs OHLC.")
    parser.add_argument("--final-tick-max-trades-delta-pct", type=float, default=35.0, help="Diferencia maxima de trades vs OHLC.")
    parser.add_argument("--rescore-seeds-only", action="store_true", help="Recalcula accepted/rejected de seeds existentes sin abrir MT5.")
    parser.add_argument("--rescore-candidates-only", action="store_true", help="Recalcula candidatos existentes con reporte sin abrir MT5.")
    parser.add_argument("--rescore-robustness-only", action="store_true", help="Recalcula resultados OOS existentes con reporte sin abrir MT5.")
    parser.add_argument("--rescore-final-tick-only", action="store_true", help="Recalcula Final Tick existente desde reportes guardados sin abrir MT5.")
    parser.add_argument(
        "--reconcile-seed-eval-only",
        action="store_true",
        help="Con --evaluate-seeds, clasifica reportes de evaluaciones seed incompletas sin abrir MT5.",
    )
    parser.add_argument("--reevaluate-seeds", action="store_true", help="Con --evaluate-seeds, vuelve a testear todas las semillas activas.")
    parser.add_argument(
        "--retry-candidate-id",
        type=int,
        action="append",
        help="Relanza un candidato concreto y actualiza su estado en memoria. Puede repetirse para varios candidatos.",
    )
    parser.add_argument("--retry-seed-path", action="append", help="Relanza una semilla concreta y actualiza seed_scores. Puede repetirse.")
    parser.add_argument("--retry-run-id", type=int, help="Run SQLite para retry de mismatches. Si se omite usa el ultimo run.")
    parser.add_argument("--retry-mismatch-run", action="store_true", help="Relanza todos los report_mismatch de un run.")
    parser.add_argument("--retry-full-run", action="store_true", help="Relanza todos los candidatos de un run y reemplaza sus resultados.")
    parser.add_argument("--retry-mismatch-generation", type=int, help="Relanza todos los report_mismatch de una generacion.")
    parser.add_argument("--min-net-profit", type=float, default=score_defaults.min_net_profit)
    parser.add_argument("--min-profit-factor", type=float, default=score_defaults.min_profit_factor)
    parser.add_argument("--min-trades", type=int, default=score_defaults.min_trades)
    parser.add_argument("--min-trades-w1", type=int, default=12, help="Trades minimos para W1 en score base/robustez.")
    parser.add_argument("--min-trades-mn", type=int, default=4, help="Trades minimos para MN en score base/robustez.")
    parser.add_argument("--max-drawdown-pct", type=float, default=score_defaults.max_drawdown_pct)
    parser.add_argument("--min-recovery-factor", type=float, default=score_defaults.min_recovery_factor)
    parser.add_argument("--min-positive-month-ratio", type=float, default=score_defaults.min_positive_month_ratio)
    parser.add_argument("--delay", type=int, default=1)
    parser.add_argument("--from-date", default="", help="Fecha inicio YYYY.MM.DD. Sobreescribe FromDate del template.")
    parser.add_argument("--to-date", default="", help="Fecha fin YYYY.MM.DD. Sobreescribe ToDate del template.")
    parser.add_argument("--execute-backtests", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No abre MT5; pasa --dry-run a run_tests.")
    parser.add_argument("--random-seed", type=int)
    args = parser.parse_args()
    if args.force_unseeded_universe:
        args.generation_mode = "discovery"
    args.force_unseeded_universe = args.generation_mode == "discovery"
    args.broker = normalize_broker(args.broker)
    args.account_type = normalize_account_type(args.account_type, args.broker)
    migrate_legacy_account_storage(BASE_DIR, args.account_type, args.broker)
    if Path(args.source_dir).expanduser() == DEFAULT_SOURCE:
        args.source_dir = str(account_seed_dir(BASE_DIR, args.account_type, args.broker))
    if Path(args.output_dir).expanduser() == DEFAULT_OUTPUT:
        args.output_dir = str(account_output_dir(BASE_DIR, args.account_type, args.broker))
    legacy_memory = BASE_DIR / "outputs" / "ubs_memory.sqlite"
    if Path(args.memory).expanduser() in {DEFAULT_MEMORY, legacy_memory}:
        args.memory = str(account_memory_path(BASE_DIR, args.account_type, args.broker))
    if Path(args.assets).expanduser() == DEFAULT_ASSETS:
        args.assets = str(broker_asset_universe_path_with_fallback(BASE_DIR, args.broker))
    return args


def seeds_from_variants(variants: list[Variant]) -> list[Seed]:
    return [variant_as_next_seed(variant) for variant in variants]


def seeds_from_survivors(survivors: list[tuple[Variant, ScoreResult]]) -> list[Seed]:
    return [variant_as_next_seed(variant) for variant, _ in survivors]


def variant_as_next_seed(variant: Variant) -> Seed:
    return Seed(
        path=variant.path,
        symbol=variant.target_symbol,
        period=variant.target_period,
        family=variant.seed.family,
        run_strategy=variant.seed.run_strategy,
    )


def ranked_seed_selection(
    seeds: list[Seed],
    max_seeds: int,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    aliases: dict[str, str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    fitness_feedback: dict[str, float] | None = None,
) -> list[tuple[float, Seed, float, float, float]]:
    aliases = aliases or {}
    fitness_feedback = fitness_feedback or {}
    valid = [seed for seed in seeds if seed.symbol != "UNKNOWN" and seed.period != "UNKNOWN"]
    if not valid:
        valid = seeds
    scored: list[tuple[float, Seed, float, float, float]] = []
    for seed in valid:
        asset_key = canonical_symbol(seed.symbol, aliases).upper()
        asset_weight = asset_feedback.get(asset_key, 0.0)
        timeframe_weight = timeframe_feedback.get(seed.period.upper(), 0.0) * 0.50
        diversity = rng.random() * 5.0
        fitness_weight = (
            fitness_feedback.get(str(seed.path), 0.0)
            * SELECTION_FITNESS_APPLIED_SCALE
        )
        scored.append((asset_weight + timeframe_weight + fitness_weight + diversity, seed, asset_weight, timeframe_weight, diversity))
    scored.sort(key=lambda item: item[0], reverse=True)
    limit = len(scored) if max_seeds <= 0 else min(max_seeds, len(scored))
    if limit <= 0:
        return []
    limiter = TargetDiversityLimiter(limit, aliases, group_by_symbol=group_by_symbol)
    selected: list[tuple[float, Seed, float, float, float]] = []
    overflow: list[tuple[float, Seed, float, float, float]] = []
    for item in scored:
        seed = item[1]
        if limiter.allows(seed.symbol, seed.period):
            selected.append(item)
            limiter.record(seed.symbol, seed.period)
            if len(selected) >= limit:
                return selected
        else:
            overflow.append(item)
    for item in overflow:
        if len(selected) >= limit:
            break
        selected.append(item)
    return selected


def choose_seeds(
    seeds: list[Seed],
    max_seeds: int,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    aliases: dict[str, str] | None = None,
    group_by_symbol: dict[str, str] | None = None,
    fitness_feedback: dict[str, float] | None = None,
) -> list[Seed]:
    return [
        seed
        for _, seed, _, _, _ in ranked_seed_selection(
            seeds, max_seeds, asset_feedback, timeframe_feedback, rng, aliases, group_by_symbol, fitness_feedback
        )
    ]


def unseeded_asset_force_probability(generation: int, unseeded_count: int) -> float:
    if unseeded_count <= 0:
        return 0.0
    return ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION.get(generation, ASSET_UNSEEDED_FORCE_PROB_LATE)


def unseeded_timeframe_force_probability(generation: int, unseeded_count: int) -> float:
    if unseeded_count <= 0:
        return 0.0
    return TF_UNSEEDED_FORCE_PROB_BY_GENERATION.get(generation, TF_UNSEEDED_FORCE_PROB_LATE)


def reserved_timeframe_plan(
    selected_seeds: list[Seed],
    timeframe_universe: tuple[str, ...],
    capacity: int,
    min_ratios: dict[str, float] | None = None,
) -> list[str]:
    if capacity <= 0:
        return []
    allowed = {str(tf).upper() for tf in timeframe_universe}
    ratios = min_ratios or FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS
    plan: list[str] = []
    for timeframe, ratio in ratios.items():
        key = str(timeframe).upper()
        if key in allowed:
            plan.extend([key] * capped_count(capacity, float(ratio)))
    represented = {str(seed.period or "").upper() for seed in selected_seeds}
    planned = set(plan)
    missing = [
        str(tf).upper()
        for tf in timeframe_universe
        if str(tf).upper() not in represented and str(tf).upper() not in planned
    ]
    plan.extend(missing)
    return plan[:capacity]


def timeframe_plan_summary(plan: list[str]) -> str:
    counts = Counter(str(tf).upper() for tf in plan)
    return ", ".join(f"{tf}x{counts[tf]}" for tf in sorted(counts))


def apply_reserved_timeframe(
    *,
    reserved_timeframes: list[str],
    target_symbol: str,
    target_period: str,
    policy: str,
    seed: Seed,
    target_limiter: "TargetDiversityLimiter",
    universe_symbols: tuple[str, ...],
    disabled_symbols: set[str],
    aliases: dict[str, str],
    rng: random.Random,
) -> tuple[str, str, str]:
    if not reserved_timeframes:
        return target_symbol, target_period, policy
    reserved_period = reserved_timeframes[0]
    candidates = [target_symbol, seed.symbol]
    shuffled = list(universe_symbols)
    rng.shuffle(shuffled)
    candidates.extend(shuffled)
    seen: set[str] = set()
    for symbol in candidates:
        canonical = canonical_symbol(symbol, aliases).upper()
        if not canonical or canonical in seen or canonical in disabled_symbols:
            continue
        seen.add(canonical)
        if target_limiter.allows(symbol, reserved_period):
            reserved_timeframes.pop(0)
            suffix = "tf_reserved"
            next_policy = f"{policy}+{suffix}" if policy else suffix
            return symbol, reserved_period, next_policy
    return target_symbol, target_period, policy


def capped_count(total: int, ratio: float) -> int:
    if total <= 0:
        return 0
    return max(1, int(total * ratio + 0.999999))


def asset_group_map(asset_groups: dict[str, list[str]], aliases: dict[str, str]) -> dict[str, str]:
    group_by_symbol: dict[str, str] = {}
    for group, symbols in asset_groups.items():
        for symbol in symbols:
            group_by_symbol[canonical_symbol(symbol, aliases).upper()] = str(group)
    return group_by_symbol


def format_group_cap_summary(limiter: "TargetDiversityLimiter") -> str:
    groups = sorted(set(limiter.group_by_symbol.values()) | set(limiter.group_cap_ratios))
    if not groups:
        return f"default<={limiter.default_group_cap}"
    return ", ".join(f"{group}<={limiter.group_cap_for(group)}" for group in groups)


class TargetDiversityLimiter:
    def __init__(
        self,
        planned_variants: int,
        aliases: dict[str, str] | None = None,
        *,
        group_by_symbol: dict[str, str] | None = None,
        pair_cap_ratio: float = TARGET_PAIR_CAP_RATIO,
        symbol_cap_ratio: float = TARGET_SYMBOL_CAP_RATIO,
        timeframe_cap_ratio: float = TARGET_TIMEFRAME_CAP_RATIO,
        group_cap_ratios: dict[str, float] | None = None,
        default_group_cap_ratio: float = DEFAULT_TARGET_GROUP_CAP_RATIO,
    ) -> None:
        self.aliases = aliases or {}
        self.group_by_symbol = {str(k).upper(): str(v) for k, v in (group_by_symbol or {}).items()}
        self.group_cap_ratios = {
            str(group): float(ratio)
            for group, ratio in (group_cap_ratios or TARGET_GROUP_CAP_RATIOS).items()
        }
        self.pair_cap = capped_count(planned_variants, pair_cap_ratio)
        self.symbol_cap = capped_count(planned_variants, symbol_cap_ratio)
        self.timeframe_cap = capped_count(planned_variants, timeframe_cap_ratio)
        self.default_group_cap = capped_count(planned_variants, default_group_cap_ratio)
        self.group_caps = {
            group: capped_count(planned_variants, ratio)
            for group, ratio in self.group_cap_ratios.items()
        }
        self.pair_counts: Counter[tuple[str, str]] = Counter()
        self.symbol_counts: Counter[str] = Counter()
        self.timeframe_counts: Counter[str] = Counter()
        self.group_counts: Counter[str] = Counter()

    def symbol_key(self, symbol: str) -> str:
        return canonical_symbol(symbol, self.aliases).upper()

    def group_key(self, symbol: str) -> str:
        return self.group_by_symbol.get(self.symbol_key(symbol), "")

    def group_cap_for(self, group: str) -> int:
        return self.group_caps.get(str(group), self.default_group_cap)

    def pair_key(self, symbol: str, period: str) -> tuple[str, str]:
        return self.symbol_key(symbol), str(period or "").upper()

    def timeframe_key(self, period: str) -> str:
        return str(period or "").upper()

    def allows(self, symbol: str, period: str) -> bool:
        if self.pair_cap and self.pair_counts[self.pair_key(symbol, period)] >= self.pair_cap:
            return False
        if self.symbol_cap and self.symbol_counts[self.symbol_key(symbol)] >= self.symbol_cap:
            return False
        if self.timeframe_cap and self.timeframe_counts[self.timeframe_key(period)] >= self.timeframe_cap:
            return False
        group = self.group_key(symbol)
        group_cap = self.group_cap_for(group) if group else 0
        if group and group_cap and self.group_counts[group] >= group_cap:
            return False
        return True

    def record(self, symbol: str, period: str) -> None:
        self.pair_counts[self.pair_key(symbol, period)] += 1
        self.symbol_counts[self.symbol_key(symbol)] += 1
        self.timeframe_counts[self.timeframe_key(period)] += 1
        group = self.group_key(symbol)
        if group:
            self.group_counts[group] += 1


def generation_source_seeds(
    seeds: list[Seed],
    symbol_map: dict[str, str],
    disabled_symbols: set[str],
    seed_enabled_when_disabled: set[str],
) -> tuple[list[Seed], int]:
    allowed = [
        seed
        for seed in seeds
        if not seed_symbol_disabled(seed, disabled_symbols, symbol_map, seed_enabled_when_disabled)
    ]
    return allowed, len(seeds) - len(allowed)


def disabled_symbols_file_for_account(account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    return account_disabled_symbols_path(BASE_DIR, account_type, broker)


def target_timeframe_universe(
    include_experimental_long: bool = False,
    *,
    base_dir: Path | None = None,
    broker: object = DEFAULT_BROKER,
    account_type: object = DEFAULT_ACCOUNT_TYPE,
) -> tuple[str, ...]:
    if base_dir is not None:
        return load_account_timeframe_universe(
            base_dir,
            account_type,
            broker,
            include_experimental_long=include_experimental_long,
            default_timeframes=TIMEFRAME_UNIVERSE,
            experimental_timeframes=EXPERIMENTAL_LONG_TIMEFRAMES,
        )
    if include_experimental_long:
        return tuple(dict.fromkeys((*TIMEFRAME_UNIVERSE, *EXPERIMENTAL_LONG_TIMEFRAMES)))
    return TIMEFRAME_UNIVERSE


def min_trades_for_period(period: str, default_min_trades: int, w1_min_trades: int, mn_min_trades: int) -> int:
    period = str(period or "").upper()
    if period == "W1":
        return max(0, int(w1_min_trades))
    if period == "MN":
        return max(0, int(mn_min_trades))
    return max(0, int(default_min_trades))


def score_config_for_period(
    score_config: ScoreConfig,
    period: str,
    *,
    min_trades_w1: int,
    min_trades_mn: int,
) -> ScoreConfig:
    min_trades = min_trades_for_period(period, score_config.min_trades, min_trades_w1, min_trades_mn)
    if min_trades == score_config.min_trades:
        return score_config
    return replace(score_config, min_trades=min_trades)


def score_config_for_variant(
    score_config: ScoreConfig,
    variant: Variant,
    *,
    min_trades_w1: int,
    min_trades_mn: int,
) -> ScoreConfig:
    return score_config_for_period(
        score_config,
        variant.target_period,
        min_trades_w1=min_trades_w1,
        min_trades_mn=min_trades_mn,
    )


def unseeded_universe_targets(
    seeds: list[Seed],
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    aliases = aliases or {}
    seed_symbols = {
        canonical_symbol(seed.symbol, aliases).upper()
        for seed in seeds
        if seed.symbol and seed.symbol != "UNKNOWN"
    }
    seed_timeframes = {
        seed.period.upper()
        for seed in seeds
        if seed.period and seed.period != "UNKNOWN"
    }
    unseeded_symbols = tuple(
        symbol
        for symbol in dict.fromkeys(universe_symbols)
        if canonical_symbol(symbol, aliases).upper() not in seed_symbols
    )
    unseeded_timeframes = tuple(period for period in timeframe_universe if period.upper() not in seed_timeframes)
    return unseeded_symbols, unseeded_timeframes


def related_assets(symbol: str) -> tuple[str, ...]:
    symbol = symbol.upper()
    if symbol in {"XAUUSD", "XAGUSD", "XAUEUR"}:
        return ("XAUUSD", "XAGUSD", "XAUEUR")
    if symbol in {"US30", ".US30CASH", "US500", ".US500CASH", "USTEC", "US100", ".USTECHCASH", "DAX", "DE40", ".DE40CASH"}:
        return ("US30", "US500", "USTEC", "DAX")
    if symbol in {"BTCUSD", "ETHUSD", "XRPUSD", "ADAUSD", "DOGEUSD"}:
        return ("BTCUSD", "ETHUSD")
    if symbol in {"XTIUSD", "WTI", "BRENT", "CRUDEOIL"}:
        return ("XTIUSD", "BRENT")
    if len(symbol) == 6:
        base = symbol[:3]
        quote = symbol[3:]
        major = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
        related = [item for item in major if base in item or quote in item]
        return tuple(dict.fromkeys([symbol, *related]))
    return (symbol,)


def target_symbol_disabled(
    symbol: str,
    universe_symbols: tuple[str, ...] = (),
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
) -> bool:
    aliases = aliases or {}
    symbol_map = symbol_map or {}
    policy_symbols = load_disabled_symbols(DEFAULT_DISABLED_SYMBOLS) if disabled_symbols is None else disabled_symbols
    disabled = {symbol.upper() for symbol in policy_symbols}
    exact_by_key = {symbol.upper(): symbol for symbol in universe_symbols}
    for alias, target in aliases.items():
        exact_by_key[str(alias).upper()] = target
    normalized = normalize_set_symbol(symbol)
    resolved = exact_by_key.get(normalized, symbol)
    mapped = normalize_set_symbol(apply_symbol_map(symbol, symbol_map))
    return normalized in disabled or resolved.upper() in disabled or mapped in disabled


def choose_target_symbol(
    seed: Seed,
    asset_feedback: dict[str, float],
    rng: random.Random,
    universe_symbols: tuple[str, ...] = (),
    aliases: dict[str, str] | None = None,
    *,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    force_unseeded_universe: bool = False,
    unseeded_universe_symbols: tuple[str, ...] = (),
    force_unseeded_probability: float = 0.65,
) -> tuple[str, str]:
    aliases = aliases or {}
    symbol_map = symbol_map or {}
    current = seed.symbol or "UNKNOWN"
    exact_by_key = {symbol.upper(): symbol for symbol in universe_symbols}
    for alias, target in aliases.items():
        exact_by_key[str(alias).upper()] = target

    def target_disabled(symbol: str) -> bool:
        return target_symbol_disabled(
            symbol,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
        )

    current_disabled = target_disabled(current)

    related = tuple(
        symbol for symbol in dict.fromkeys(exact_by_key.get(symbol.upper(), symbol) for symbol in related_assets(current))
        if not target_disabled(symbol)
    )
    universe_choices = tuple(
        symbol for symbol in dict.fromkeys(universe_symbols)
        if symbol.upper() != current.upper() and not target_disabled(symbol)
    )
    unseeded_choices = tuple(
        symbol for symbol in dict.fromkeys(unseeded_universe_symbols)
        if symbol.upper() != current.upper() and not target_disabled(symbol)
    )
    if force_unseeded_universe and unseeded_choices and rng.random() < force_unseeded_probability:
        unseen = [symbol for symbol in unseeded_choices if symbol.upper() not in asset_feedback]
        return rng.choice(unseen or list(unseeded_choices)), "asset_unseeded_force"

    if not current_disabled and rng.random() < 0.70:
        return current, "exploit"
    if universe_choices and rng.random() < 0.65:
        ranked = sorted(universe_choices, key=lambda item: asset_feedback.get(item.upper(), -999999.0), reverse=True)
        ranked_with_feedback = [symbol for symbol in ranked if symbol.upper() in asset_feedback]
        if ranked_with_feedback and rng.random() < 0.55:
            return ranked_with_feedback[0], "asset_universe_feedback"
        return rng.choice(universe_choices), "asset_universe_explore"

    choices = tuple(symbol for symbol in related if symbol.upper() != current.upper())
    if not choices:
        if universe_choices:
            return rng.choice(universe_choices), "asset_universe_fallback"
        return seed.symbol, "exploit"
    ranked = sorted(choices, key=lambda item: asset_feedback.get(item.upper(), 0.0), reverse=True)
    if ranked and rng.random() < 0.50:
        return ranked[0], "asset_feedback"
    return rng.choice(choices), "asset_explore"


def filter_timeframe_universe(periods: tuple[str, ...], timeframe_universe: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {period.upper() for period in timeframe_universe}
    return tuple(period for period in dict.fromkeys(periods) if period.upper() in allowed)


def related_timeframes(
    period: str,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
) -> tuple[str, ...]:
    period = period.upper()
    related: tuple[str, ...]
    if period == "M15":
        related = ("M5", "M15", "M30", "H1")
    elif period == "M30":
        related = ("M15", "M30", "H1", "H4")
    elif period == "H1":
        related = ("M30", "H1", "H4", "D1")
    elif period == "H4":
        related = ("H1", "H4", "D1")
    elif period == "D1":
        related = ("H4", "D1", "W1", "MN")
    elif period == "M1":
        related = ("M1", "M5", "M15")
    elif period == "M5":
        related = ("M1", "M5", "M15", "M30")
    elif period == "W1":
        related = ("D1", "W1", "MN")
    elif period == "MN":
        related = ("D1", "W1", "MN")
    else:
        related = timeframe_universe
    return filter_timeframe_universe(related, timeframe_universe)


def choose_target_period(
    seed: Seed,
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    *,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
    force_unseeded_timeframes: bool = False,
    unseeded_timeframes: tuple[str, ...] = (),
    force_unseeded_probability: float = 0.50,
) -> tuple[str, str]:
    current = seed.period.upper()
    choices = tuple(dict.fromkeys(related_timeframes(current, timeframe_universe)))
    if not choices:
        return current, "tf_exploit"
    forced_choices = tuple(period for period in choices if period.upper() in {tf.upper() for tf in unseeded_timeframes})
    if force_unseeded_timeframes and forced_choices and rng.random() < force_unseeded_probability:
        unseen = [period for period in forced_choices if period.upper() not in timeframe_feedback]
        return rng.choice(unseen or list(forced_choices)), "tf_unseeded_force"
    if current in choices and rng.random() < 0.60:
        return current, "tf_exploit"
    ranked = sorted(choices, key=lambda item: timeframe_feedback.get(item.upper(), -999999.0), reverse=True)
    ranked_with_feedback = [period for period in ranked if period.upper() in timeframe_feedback]
    if ranked_with_feedback and rng.random() < 0.45:
        return ranked_with_feedback[0], "tf_feedback"
    unexplored = [period for period in choices if period.upper() not in timeframe_feedback]
    if unexplored and rng.random() < 0.70:
        return rng.choice(unexplored), "tf_explore_new"
    return rng.choice(choices), "tf_explore"


def diverse_target_fallback(
    seed: Seed,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    limiter: TargetDiversityLimiter,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
) -> tuple[str, str] | None:
    aliases = aliases or {}
    symbol_candidates = tuple(dict.fromkeys((seed.symbol, *related_assets(seed.symbol), *universe_symbols)))
    period_candidates = tuple(dict.fromkeys((*related_timeframes(seed.period, timeframe_universe), *timeframe_universe)))
    scored: list[tuple[float, str, str]] = []
    for symbol in symbol_candidates:
        if not symbol or symbol == "UNKNOWN":
            continue
        if target_symbol_disabled(
            symbol,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
        ):
            continue
        asset_key = canonical_symbol(symbol, aliases).upper()
        asset_weight = asset_feedback.get(asset_key, 0.0)
        for period in period_candidates:
            if not period or period == "UNKNOWN":
                continue
            if not limiter.allows(symbol, period):
                continue
            timeframe_weight = timeframe_feedback.get(period.upper(), 0.0) * 0.50
            scored.append((asset_weight + timeframe_weight + rng.random() * 5.0, symbol, period))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    _, symbol, period = scored[0]
    return symbol, period


def choose_diverse_target(
    seed: Seed,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
    limiter: TargetDiversityLimiter,
    universe_symbols: tuple[str, ...],
    aliases: dict[str, str] | None = None,
    *,
    timeframe_universe: tuple[str, ...] = TIMEFRAME_UNIVERSE,
    symbol_map: dict[str, str] | None = None,
    disabled_symbols: set[str] | None = None,
    force_unseeded_universe: bool = False,
    unseeded_universe_symbols: tuple[str, ...] = (),
    unseeded_timeframes: tuple[str, ...] = (),
    asset_unseeded_probability: float = 0.0,
    timeframe_unseeded_probability: float = 0.0,
) -> tuple[str, str, str]:
    last_symbol = seed.symbol
    last_period = seed.period
    last_policy = "exploit+tf_exploit"
    for attempt in range(DIVERSITY_REROLL_ATTEMPTS + 1):
        target_symbol, policy = choose_target_symbol(
            seed,
            asset_feedback,
            rng,
            universe_symbols,
            aliases,
            symbol_map=symbol_map,
            disabled_symbols=disabled_symbols,
            force_unseeded_universe=force_unseeded_universe,
            unseeded_universe_symbols=unseeded_universe_symbols,
            force_unseeded_probability=asset_unseeded_probability,
        )
        target_period, period_policy = choose_target_period(
            seed,
            timeframe_feedback,
            rng,
            timeframe_universe=timeframe_universe,
            force_unseeded_timeframes=force_unseeded_universe,
            unseeded_timeframes=unseeded_timeframes,
            force_unseeded_probability=timeframe_unseeded_probability,
        )
        full_policy = f"{policy}+{period_policy}"
        last_symbol = target_symbol
        last_period = target_period
        last_policy = full_policy
        if limiter.allows(target_symbol, target_period):
            if attempt:
                full_policy = f"{full_policy}+diversity_reroll"
            return target_symbol, target_period, full_policy

    fallback = diverse_target_fallback(
        seed,
        asset_feedback,
        timeframe_feedback,
        rng,
        limiter,
        universe_symbols,
        aliases,
        timeframe_universe=timeframe_universe,
        symbol_map=symbol_map,
        disabled_symbols=disabled_symbols,
    )
    if fallback is not None:
        target_symbol, target_period = fallback
        return target_symbol, target_period, "diversity_fallback"
    return last_symbol, last_period, f"{last_policy}+diversity_overflow"


def line_candidates(
    text: str,
    run_strategy: str,
    mutation_feedback: dict[str, float],
    *,
    excluded_keys: Iterable[str] = (),
) -> dict[str, tuple[int, list[str], float]]:
    lines = text.splitlines()
    preferred = set(CORE_MUTATION_KEYS.get(run_strategy, CORE_MUTATION_KEYS[""]))
    excluded = {str(key) for key in excluded_keys}
    candidates: dict[str, tuple[int, list[str], float]] = {}
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key in excluded:
            continue
        if not is_agent_mutable_key(key):
            continue
        if "||" not in raw_value:
            continue
        parts = raw_value.split("||")
        if len(parts) < 5:
            continue
        current = parse_numeric(parts[0])
        start = parse_numeric(parts[1])
        step = parse_numeric(parts[2])
        stop = parse_numeric(parts[3])
        if current is None or start is None or step is None or stop is None:
            continue
        if step <= 0 or stop <= start:
            continue
        base_weight = 4.0 if key in preferred else 1.0
        candidates[key] = (index, parts, base_weight)
    multipliers = percentile_multipliers(mutation_feedback, candidates)
    for key, (index, parts, base_weight) in tuple(candidates.items()):
        candidates[key] = (index, parts, base_weight * multipliers[key])
    return candidates


def weighted_sample(items: dict[str, tuple[int, list[str], float]], count: int, rng: random.Random) -> list[str]:
    selected: list[str] = []
    pool = dict(items)
    for _ in range(min(count, len(pool))):
        total = sum(value[2] for value in pool.values())
        cursor = rng.random() * total
        upto = 0.0
        chosen = next(iter(pool))
        for key, (_, _, weight) in pool.items():
            upto += weight
            if upto >= cursor:
                chosen = key
                break
        selected.append(chosen)
        pool.pop(chosen, None)
    return selected


def replace_existing_plain_key(lines: list[str], key: str, value: str) -> bool:
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        lhs, _ = line.split("=", 1)
        if lhs.strip() == key:
            lines[index] = f"{lhs}={value}"
            return True
    return False


def replace_or_add_plain_key(lines: list[str], key: str, value: str) -> None:
    if replace_existing_plain_key(lines, key, value):
        return
    insert_at = 0
    while insert_at < len(lines) and (not lines[insert_at].strip() or lines[insert_at].lstrip().startswith(";")):
        insert_at += 1
    lines.insert(insert_at, f"{key}={value}")


def replace_existing_current_value(lines: list[str], key: str, value: str) -> bool:
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        lhs, raw_value = line.split("=", 1)
        if lhs.strip() != key:
            continue
        if "||" in raw_value:
            parts = raw_value.split("||")
            parts[0] = value
            lines[index] = f"{lhs}={'||'.join(parts)}"
        else:
            lines[index] = f"{lhs}={value}"
        return True
    return False


def replace_timeframe_keys(lines: list[str], run_strategy: str, target_period: str) -> list[str]:
    enum_value = TIMEFRAME_TO_ENUM.get(target_period.upper())
    if not enum_value:
        return []
    keys: list[str] = []
    if run_strategy == "1":
        keys.append("ST1_Timeframe")
    elif run_strategy == "2":
        keys.append("VolTimeframe")
    keys.extend(["Entry_Timing", "ATR_Timeframe"])
    changed: list[str] = []
    for key in dict.fromkeys(keys):
        if replace_existing_current_value(lines, key, enum_value):
            changed.append(key)
    return changed


def write_set_force_symbol(source: Path, destination: Path, symbol: str) -> bool:
    text, encoding = read_set_with_encoding(source)
    lines = text.splitlines()
    normalized_symbol = str(symbol or "").strip().upper()
    changed = not replace_existing_current_value(lines, "ForceSymbol", normalized_symbol)
    if changed:
        replace_or_add_plain_key(lines, "ForceSymbol", normalized_symbol)
    write_set_text(destination, "\n".join(lines), encoding)
    return changed


def validate_seed_backtest_set(seed: Seed) -> list[str]:
    try:
        params = load_set_params(seed.path)
    except OSError as exc:
        return [f"no se pudo leer .set: {exc}"]
    if not params:
        return ["set vacio o no parseable"]

    issues: list[str] = []
    force_symbol = normalize_set_symbol(params.get("ForceSymbol", ""))
    expected_symbol = normalize_set_symbol(seed.symbol)
    strategy_keys = {"ST1_Timeframe", "VolTimeframe", "Entry_Timing", "ATR_Timeframe"}
    has_strategy_keys = any(key in params for key in strategy_keys)
    if has_strategy_keys and not force_symbol:
        issues.append("sin ForceSymbol")
    elif force_symbol and expected_symbol and force_symbol != expected_symbol:
        issues.append(f"ForceSymbol={force_symbol} != {expected_symbol}")

    run_strategy = str(params.get("Run_Strategy") or seed.run_strategy or "").strip()
    if has_strategy_keys and run_strategy not in {"1", "2"}:
        issues.append("sin Run_Strategy valido")

    valid_timeframes = set(TIMEFRAME_ENUM)
    for key in ("ST1_Timeframe", "VolTimeframe", "Entry_Timing", "ATR_Timeframe"):
        raw = str(params.get(key, "")).strip()
        if raw and raw != "0" and raw not in valid_timeframes:
            issues.append(f"{key}={raw} no es timeframe MT5 valido")
    return issues


def record_invalid_seed(memory: AgentMemory, seed: Seed, reasons: list[str]) -> None:
    memory.record_seed_score(seed, None, "invalid_seed", None)
    memory.conn.execute(
        "update seed_scores set metrics_json=? where seed_path=?",
        (json.dumps({"reasons": reasons}, ensure_ascii=False), str(seed.path)),
    )
    memory.conn.commit()


def create_variant(
    seed: Seed,
    target_symbol: str,
    target_period: str,
    output_dir: Path,
    generation: int,
    seed_index: int,
    variant_index: int,
    mutations_per_variant: int,
    mutation_feedback: dict[str, float],
    mutation_direction_feedback: dict[str, float],
    policy: str,
    rng: random.Random,
) -> Variant:
    text, encoding = read_set_with_encoding(seed.path)
    lines = text.splitlines()
    replace_or_add_plain_key(lines, "ForceSymbol", target_symbol)
    timeframe_keys = replace_timeframe_keys(lines, seed.run_strategy, target_period)
    # Apply user-defined frozen override values from the global params config
    frozen_ov, _ = load_mutation_overrides()
    if frozen_ov:
        global_params = load_global_params()
        for fkey in frozen_ov:
            fvalue = global_params.get(fkey, frozen_ov.get(fkey, ""))
            if fvalue:
                replace_existing_current_value(lines, fkey, fvalue)
    text = "\n".join(lines)
    candidates = line_candidates(text, seed.run_strategy, mutation_feedback, excluded_keys=timeframe_keys)
    selected = weighted_sample(candidates, mutations_per_variant, rng)
    lines = text.splitlines()
    changed: list[str] = []
    mutation_details: list[dict[str, object]] = []
    for key in selected:
        line_index, parts, _ = candidates[key]
        current = float(parts[0])
        start = float(parts[1])
        step = float(parts[2])
        stop = float(parts[3])
        direction_bias = mutation_direction_feedback.get(key, 0.0)
        if direction_bias > 0 and rng.random() < 0.70:
            direction = rng.choice([1, 2])
        elif direction_bias < 0 and rng.random() < 0.70:
            direction = rng.choice([-2, -1])
        else:
            direction = rng.choice([-2, -1, 1, 2])
        value = current + direction * step
        wrapped = False
        if value < start or value > stop:
            slots = int((stop - start) / step)
            value = start + rng.randint(0, max(0, slots)) * step
            wrapped = True
        new_value = max(start, min(stop, value))
        parts[0] = format_like(parts[0], new_value)
        lhs = lines[line_index].split("=", 1)[0]
        lines[line_index] = f"{lhs}={'||'.join(parts)}"
        changed.append(key)
        mutation_details.append(
            {
                "key": key,
                "old": current,
                "new": new_value,
                "delta": new_value - current,
                "step": step,
                "direction": direction,
                "direction_bias": round(float(direction_bias), 4),
                "wrapped": wrapped,
            }
        )

    normalized, _, missing = force_fixed_lot_text("\n".join(lines))
    seed_label = compact_safe_part(seed.path.stem, 24)
    family_label = compact_safe_part(seed.family, 24)
    filename = (
        f"{safe_part(target_symbol)}_{safe_part(target_period)}_{family_label}_{seed_label}_"
        f"g{generation:03d}_s{seed_index:03d}_v{variant_index:03d}.set"
    )
    target = output_dir / safe_part(target_symbol) / safe_part(target_period) / filename
    write_set_text(target, normalized, encoding)
    return Variant(
        target,
        seed,
        target_symbol,
        target_period,
        tuple(changed),
        tuple(sorted(missing)),
        policy,
        tuple(timeframe_keys),
        tuple(mutation_details),
    )


def run_backtests(args: argparse.Namespace, set_dir: Path, *, model: str = "") -> int:
    if not args.expert and not args.multi_terminal:
        print("AVISO: --expert no indicado; se omiten backtests.")
        return 0
    command = [
        sys.executable,
        str(BASE_DIR / "run_tests.py"),
        "--template",
        str(Path(args.template).expanduser()),
        "--set-dir",
        str(set_dir),
        "--recursive",
        "--infer-tester-from-set",
        "--prefer-set-path-timeframe",
        "--delay",
        str(args.delay),
    ]
    if args.expert:
        command.extend(["--expert", args.expert])
    if args.mt5_path:
        command.extend(["--mt5-path", args.mt5_path])
    if args.data_dir:
        command.extend(["--data-dir", args.data_dir])
    if args.multi_terminal:
        command.append("--multi-terminal")
        command.extend(["--max-workers", str(args.max_workers)])
        if args.terminals_config:
            command.extend(["--terminals-config", args.terminals_config])
    if args.symbol_map:
        command.extend(["--symbol-map", args.symbol_map])
    if args.dry_run:
        command.append("--dry-run")
    if getattr(args, "from_date", ""):
        command.extend(["--from-date", args.from_date])
    if getattr(args, "to_date", ""):
        command.extend(["--to-date", args.to_date])
    if model:
        command.extend(["--model", str(model)])
    print("Ejecutando:", " ".join(f'"{part}"' if " " in part else part for part in command))
    process = subprocess.run(command, cwd=BASE_DIR, text=True)
    return process.returncode


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


def reconcile_seed_eval_reports(
    memory: AgentMemory,
    pending: list[Seed],
    output_root: Path,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
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
    for eval_dir in eval_dirs:
        eval_started = _parse_eval_dir_timestamp(eval_dir)
        if eval_started is None:
            continue
        for copied_set in sorted(eval_dir.glob("*.set")):
            report = find_report_for_set(copied_set, min_mtime=eval_started.timestamp() - 1.0)
            if not report:
                continue
            copied_digest = file_digest(copied_set)
            if not copied_digest:
                continue
            candidates = pending_by_hash.get(copied_digest, [])
            seed = next((candidate for candidate in candidates if str(candidate.path) not in processed_paths), None)
            if seed is None:
                continue
            override_updated_at = _seed_override_updated_at(memory, seed.path)
            if override_updated_at is not None and eval_started <= override_updated_at:
                # Override guardado despues de la evaluacion: el reporte solo es
                # reutilizable si coincide con el target efectivo actual (caso
                # tipico: override que no cambia symbol/TF). Si no coincide, se
                # deja pendiente para re-ejecutar en MT5.
                if seed.symbol == "UNKNOWN" or seed.period == "UNKNOWN":
                    continue
                try:
                    probe = score_report_file(report, config=score_config)
                except Exception:
                    continue
                probe_variant = Variant(
                    path=Path(copied_set.name),
                    seed=seed,
                    target_symbol=seed.symbol,
                    target_period=seed.period,
                    mutated_keys=(),
                    missing_lot_keys=(),
                    policy="seed_eval",
                )
                matches, _ = report_matches_variant(probe_variant, probe, symbol_map)
                if not matches:
                    continue
            seed_path = str(seed.path)
            status, _ = evaluate_seed_report(memory, seed, report, score_config, symbol_map, label=copied_set.name)
            status_counts[status] = status_counts.get(status, 0) + 1
            processed_paths.add(seed_path)
            if len(processed_paths) >= len(pending):
                return status_counts, processed_paths
    return status_counts, processed_paths


def rescore_existing_seed_scores(
    memory: AgentMemory,
    seeds: list[Seed],
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    *,
    exclude_paths: set[str],
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
        status, _ = evaluate_seed_report(memory, seed, report, score_config, symbol_map)
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


def evaluate_seed_scores(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    source_dir = Path(args.source_dir).expanduser()
    output_root = Path(args.output_dir).expanduser()
    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    if not args.expert and not args.multi_terminal and not args.reconcile_seed_eval_only:
        print("ERROR: evaluar semillas requiere --expert o --multi-terminal.")
        return 1

    symbol_map = parse_symbol_map(args.symbol_map)
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
    print(f"Semillas detectadas: {len(seeds)}")
    print(f"Backtests de semillas pendientes: {len(pending)}")
    if invalid_pending:
        print(f"Semillas bloqueadas por symbol/timeframe no inferible: {len(invalid_pending)}")
    if invalid_set_reasons:
        print(f"Semillas bloqueadas por .set invalido: {len(invalid_set_reasons)}")
    if disabled_pending:
        print(
            "Semillas omitidas por symbol deshabilitado en Universo del broker: "
            f"{len(disabled_pending)} ({format_disabled_seed_counts(disabled_pending, symbol_map)})"
        )
        print("Estas seeds no abren MT5 ni aportan pesos; activa SEEDS para usarlas sin habilitar generacion.")
    if seed_enabled_when_disabled:
        print(f"Symbols deshabilitados con SEEDS activo: {len(seed_enabled_when_disabled)}")
    print(f"Semillas ya evaluadas sin cambios: {unchanged_count}")
    reconciled_counts, reconciled_paths = reconcile_seed_eval_reports(
        memory,
        pending,
        output_root,
        score_config,
        symbol_map,
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
    if not pending:
        rescored_counts = rescore_existing_seed_scores(
            memory,
            seeds,
            score_config,
            symbol_map,
            exclude_paths=original_pending_paths | invalid_paths | disabled_paths | set(invalid_set_reasons),
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
                print("No hay backtests pendientes validos. Las restantes estan deshabilitadas en Universo del broker.")
        else:
            print("Evaluacion de semillas al dia. No hay backtests pendientes.")
        return 0

    eval_dir = output_root / "seed_eval" / datetime.now().strftime("eval_%Y%m%d_%H%M%S")
    eval_dir.mkdir(parents=True, exist_ok=True)
    copied: list[tuple[Seed, Path]] = []
    used_names: set[str] = set()
    for index, seed in enumerate(pending, start=1):
        destination = eval_dir / seed_eval_filename(index, seed, used_names)
        shutil.copy2(seed.path, destination)
        copied.append((seed, destination))

    print(f"Backtests semillas: {len(copied)}")
    print(f"Directorio evaluacion: {eval_dir}")
    batch_started_at = time.time()
    code = run_backtests(args, eval_dir)
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
    for seed, copied_set in copied:
        report = find_report_for_set(copied_set, min_mtime=batch_started_at - 1.0)
        if not report:
            memory.record_seed_score(seed, None, "no_report", None)
            status_counts["no_report"] = status_counts.get("no_report", 0) + 1
            handled_issues += 1
            continue
        status, result = evaluate_seed_report(memory, seed, report, score_config, symbol_map, label=copied_set.name)
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"accepted", "rejected"} and result is not None:
            scored += 1
        else:
            handled_issues += 1

    rescored_counts = rescore_existing_seed_scores(
        memory,
        seeds,
        score_config,
        symbol_map,
        exclude_paths=original_pending_paths | invalid_paths | disabled_paths | set(invalid_set_reasons),
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
    return 0


def rescore_seed_scores_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    source_dir = Path(args.source_dir).expanduser()
    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    status_counts = rescore_existing_seed_scores(
        memory,
        seeds,
        score_config,
        parse_symbol_map(args.symbol_map),
        exclude_paths=set(),
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


def _stored_or_discovered_report(row: sqlite3.Row) -> Path | None:
    report_raw = str(row["report_path"] or "").strip()
    if report_raw:
        report = Path(report_raw)
        if report.exists():
            return report
    set_path = Path(str(row["set_path"] or ""))
    if set_path.exists():
        return find_report_for_set(set_path)
    return None


def rescore_candidate_scores_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = memory.conn.execute(
        """
        select *
        from candidates
        where status in (
            'accepted', 'rejected', 'no_trades', 'report_mismatch',
            'parse_error', 'no_report', 'generated'
        )
        order by run_id, generation, id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    skipped_no_report = 0
    for row in rows:
        report = _stored_or_discovered_report(row)
        if report is None:
            skipped_no_report += 1
            continue
        variant = variant_from_candidate_row(row)
        status, _ = evaluate_variant_report(
            memory,
            variant,
            report,
            score_config,
            symbol_map,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    total = sum(status_counts.values())
    if status_counts:
        print(
            "Candidatos repuntuados con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay candidatos con reporte disponible para repuntuar.")
    if skipped_no_report:
        print(f"Candidatos sin reporte local omitidos: {skipped_no_report}")
    print(f"Memoria: {memory.path}")
    return 0


def rescore_robustness_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = memory.conn.execute(
        """
        select
            c.*,
            cr.status as robust_status,
            cr.report_path as robust_report_path,
            cr.from_date as robust_from_date,
            cr.to_date as robust_to_date,
            cr.positive_bonus as robust_positive_bonus,
            cr.negative_bonus as robust_negative_bonus
        from candidate_robustness cr
        join candidates c on c.id = cr.candidate_id
        order by cr.run_id, c.generation, c.id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    skipped_no_report = 0
    for row in rows:
        report_raw = str(row["robust_report_path"] or "").strip()
        report = Path(report_raw) if report_raw else None
        if report is None or not report.exists():
            skipped_no_report += 1
            continue
        candidate_id = int(row["id"])
        run_id = int(row["run_id"])
        variant = variant_from_candidate_row(row)
        try:
            period_score_config = score_config_for_variant(
                score_config,
                variant,
                min_trades_w1=args.min_trades_w1,
                min_trades_mn=args.min_trades_mn,
            )
            result = score_report_file(report, config=period_score_config)
        except Exception as exc:
            print(f"AVISO: no pude parsear robustez candidate #{candidate_id}: {exc}")
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                None,
                "parse_error",
                report,
                str(row["robust_from_date"] or ""),
                str(row["robust_to_date"] or ""),
                float(row["robust_positive_bonus"] or DEFAULT_ROBUST_POSITIVE_BONUS),
                float(row["robust_negative_bonus"] or DEFAULT_ROBUST_NEGATIVE_BONUS),
            )
            status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
            continue
        matches, mismatch_reason = report_matches_variant(variant, result, symbol_map)
        if not matches:
            print(f"AVISO: reporte robustez no coincide para candidate #{candidate_id}: {mismatch_reason}")
            status = "report_mismatch"
        elif result.trades <= 0:
            status = "no_trades"
        else:
            status = "accepted" if result.accepted else "rejected"
        memory.record_candidate_robustness(
            candidate_id,
            run_id,
            result,
            status,
            report,
            str(row["robust_from_date"] or ""),
            str(row["robust_to_date"] or ""),
            float(row["robust_positive_bonus"] or DEFAULT_ROBUST_POSITIVE_BONUS),
            float(row["robust_negative_bonus"] or DEFAULT_ROBUST_NEGATIVE_BONUS),
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    total = sum(status_counts.values())
    if status_counts:
        print(
            "Robustez repuntuada con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay resultados de robustez con reporte disponible para repuntuar.")
    if skipped_no_report:
        print(f"Robustez sin reporte local omitida: {skipped_no_report}")
    print(f"Memoria: {memory.path}")
    return 0


def _report_is_fresh(path: Path, min_mtime: float | None) -> bool:
    if min_mtime is None:
        return True
    try:
        return path.stat().st_mtime >= min_mtime
    except OSError:
        return False


def find_report_for_set(set_path: Path, *, min_mtime: float | None = None) -> Path | None:
    for suffix in (".htm", ".html", ".xml"):
        candidate = BASE_DIR / "reports" / f"{set_path.stem}{suffix}"
        if candidate.exists() and _report_is_fresh(candidate, min_mtime):
            return candidate
    candidates = sorted(
        path for path in (BASE_DIR / "reports").glob(f"{set_path.stem}.*")
        if _report_is_fresh(path, min_mtime)
    )
    return candidates[0] if candidates else None


def report_matches_variant(variant: Variant, result: ScoreResult, symbol_map: dict[str, str]) -> tuple[bool, str]:
    report_symbol = normalize_set_symbol(result.symbol)
    target_symbol = normalize_set_symbol(apply_symbol_map(variant.target_symbol, symbol_map))
    report_timeframe = str(result.timeframe or "").upper()
    target_timeframe = str(variant.target_period or "").upper()
    issues: list[str] = []
    if report_symbol != target_symbol:
        issues.append(f"symbol reporte={report_symbol or '(vacio)'} objetivo={target_symbol or '(vacio)'}")
    if report_timeframe != target_timeframe:
        issues.append(f"tf reporte={report_timeframe or '(vacio)'} objetivo={target_timeframe or '(vacio)'}")
    return not issues, "; ".join(issues)


def evaluate_seed_report(
    memory: AgentMemory,
    seed: Seed,
    report: Path,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    *,
    label: str | None = None,
) -> tuple[str, ScoreResult | None]:
    display_name = label or seed.path.name
    try:
        result = score_report_file(report, config=score_config)
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

    expected_symbol = seed.symbol if seed.symbol and seed.symbol != "UNKNOWN" else str(result.symbol or "UNKNOWN")
    expected_period = seed.period if seed.period and seed.period != "UNKNOWN" else str(result.timeframe or "UNKNOWN").upper()
    evaluated_seed = Seed(
        path=seed.path,
        symbol=expected_symbol,
        period=expected_period,
        family=seed.family,
        run_strategy=seed.run_strategy,
    )
    variant = Variant(
        path=Path(display_name),
        seed=evaluated_seed,
        target_symbol=expected_symbol,
        target_period=expected_period,
        mutated_keys=(),
        missing_lot_keys=(),
        policy="seed_eval",
    )
    matches, mismatch_reason = report_matches_variant(variant, result, symbol_map)
    if not matches:
        print(f"AVISO: reporte seed no coincide para {seed.path.name}: {mismatch_reason}")
        memory.record_seed_score(evaluated_seed, result, "report_mismatch", report)
        return "report_mismatch", result

    if result.trades <= 0:
        print(f"AVISO: reporte seed sin operaciones para {seed.path.name}; marcado como no_trades.")
        memory.record_seed_score(evaluated_seed, result, "no_trades", report)
        return "no_trades", result

    status = "accepted" if result.accepted else "rejected"
    memory.record_seed_score(evaluated_seed, result, status, report)
    return status, result


def evaluate_variants(
    memory: AgentMemory,
    variants: list[Variant],
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    *,
    min_report_mtime: float | None = None,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
) -> list[tuple[Variant, ScoreResult]]:
    scored: list[tuple[Variant, ScoreResult]] = []
    for variant in variants:
        status, result = evaluate_variant(
            memory,
            variant,
            score_config,
            symbol_map,
            min_report_mtime=min_report_mtime,
            min_trades_w1=min_trades_w1,
            min_trades_mn=min_trades_mn,
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
    *,
    min_report_mtime: float | None = None,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
) -> tuple[str, ScoreResult | None]:
    report = find_report_for_set(variant.path, min_mtime=min_report_mtime)
    if not report:
        memory.record_score(variant.path, None, "no_report", None)
        return "no_report", None
    return evaluate_variant_report(
        memory,
        variant,
        report,
        score_config,
        symbol_map,
        min_trades_w1=min_trades_w1,
        min_trades_mn=min_trades_mn,
    )


def evaluate_variant_report(
    memory: AgentMemory,
    variant: Variant,
    report: Path,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    *,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
) -> tuple[str, ScoreResult | None]:
    period_score_config = score_config_for_variant(
        score_config,
        variant,
        min_trades_w1=min_trades_w1,
        min_trades_mn=min_trades_mn,
    )
    try:
        result = score_report_file(report, config=period_score_config)
    except Exception as exc:
        print(f"AVISO: no pude parsear {report}: {exc}")
        memory.record_score(variant.path, None, "parse_error", report)
        return "parse_error", None
    matches, mismatch_reason = report_matches_variant(variant, result, symbol_map)
    if not matches:
        print(f"AVISO: reporte no coincide para {variant.path.name}: {mismatch_reason}")
        memory.record_score(variant.path, result, "report_mismatch", report)
        return "report_mismatch", result
    if result.trades <= 0:
        memory.record_score(variant.path, result, "no_trades", report)
        return "no_trades", result
    status = "accepted" if result.accepted else "rejected"
    memory.record_score(variant.path, result, status, report)
    return status, result


def select_survivors(scored: list[tuple[Variant, ScoreResult]], top_percent: float) -> list[tuple[Variant, ScoreResult]]:
    if not scored:
        return []
    scored = sorted(scored, key=lambda item: item[1].score, reverse=True)
    accepted = [item for item in scored if item[1].accepted]
    if not accepted:
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
) -> list[tuple[Variant, ScoreResult]]:
    survivors = select_survivors(scored, top_percent)
    if not survivors:
        return []
    if fitness_feedback and SELECTION_FITNESS_APPLIED_SCALE:
        survivors.sort(
            key=lambda item: (
                fitness_feedback.get(str(item[0].path), 0.0)
                * SELECTION_FITNESS_APPLIED_SCALE,
                item[1].score,
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
) -> list[tuple[Variant, ScoreResult]]:
    accepted = select_survivors(scored, top_percent)
    predictions = memory.seed_selection_predictions(
        seeds_from_survivors(accepted),
        exclude_run_id=run_id,
    )
    fitness_feedback = {path: prediction.weight for path, prediction in predictions.items()}
    return select_next_seed_survivors(
        scored,
        top_percent,
        max_seeds,
        aliases,
        group_by_symbol,
        fitness_feedback,
    )


def copy_accepted(survivors: list[tuple[Variant, ScoreResult]], accepted_dir: Path) -> list[Path]:
    if not survivors:
        return []
    accepted_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for variant, result in survivors:
        for previous in accepted_dir.glob(f"*__{variant.path.name}"):
            if previous.is_file():
                previous.unlink()
        destination = accepted_dir / f"score_{result.score:07.2f}__{variant.path.name}"
        shutil.copy2(variant.path, destination)
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
    *,
    min_trades_w1: int = 12,
    min_trades_mn: int = 4,
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
            )
        except Exception:
            continue
        matches, _ = report_matches_variant(variant, result, symbol_map)
        if matches:
            valid += 1
    return valid


def prepare_final_tick_exec_dir(path: Path, variants: list[Variant]) -> Path:
    exec_dir = recreate_work_dir(path)
    for variant in variants:
        shutil.copy2(variant.path, exec_dir / variant.path.name)
    return exec_dir


ROBUST_RETRYABLE_STATUSES = {"pending", "no_report", "parse_error", "report_mismatch", "no_trades"}


def robust_status_pending_for_retry(status: object) -> bool:
    value = str(status or "").strip()
    return not value or value in ROBUST_RETRYABLE_STATUSES


def evaluate_candidate_robustness(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    if not args.expert and not args.multi_terminal and not args.dry_run:
        print("ERROR: robustez requiere --expert o --multi-terminal")
        return 1

    run = memory.run_by_id(args.robust_run_id) if args.robust_run_id else memory.latest_run()
    if run is None:
        print("ERROR: no hay run SQLite disponible para robustez")
        return 1

    run_id = int(run["id"])
    run_dir = Path(run["output_dir"])
    rows = [
        row
        for row in memory.accepted_candidates_for_robustness(run_id)
        if Path(row["set_path"]).exists()
    ]
    robust_candidate_ids = {int(value) for value in (args.robust_candidate_id or [])}
    if robust_candidate_ids:
        rows = [row for row in rows if int(row["id"]) in robust_candidate_ids]
    if args.robust_pending_only:
        rows = [
            row for row in rows
            if robust_status_pending_for_retry(row["robust_status"])
        ]
    if not rows:
        if args.robust_pending_only:
            print(f"Robustez run #{run_id}: no hay candidatos accepted pendientes ni retryables de OOS.")
        else:
            print(f"Robustez run #{run_id}: no hay candidatos accepted con .set existente.")
        return 0

    robust_mode = "pending" if args.robust_pending_only else "all"
    robust_dir = recreate_work_dir(run_dir / "robustness" / f"run_{run_id}_{robust_mode}")
    copied: list[tuple[sqlite3.Row, Variant]] = []
    used_names: set[str] = set()
    for row in rows:
        source_set = Path(row["set_path"])
        name = f"robust_{int(row['id']):06d}_{source_set.name}"
        if name in used_names:
            print(f"ERROR: nombre duplicado en robustez: {name}")
            return 1
        used_names.add(name)
        retry_set = robust_dir / name
        shutil.copy2(source_set, retry_set)
        if not args.dry_run:
            remove_report_artifacts(retry_set)
        original_variant = variant_from_candidate_row(row)
        copied.append(
            (
                row,
                Variant(
                    path=retry_set,
                    seed=original_variant.seed,
                    target_symbol=original_variant.target_symbol,
                    target_period=original_variant.target_period,
                    mutated_keys=original_variant.mutated_keys,
                    missing_lot_keys=original_variant.missing_lot_keys,
                    policy=f"{original_variant.policy}+robustness",
                ),
            )
        )

    mode_label = "pendientes sin OOS" if args.robust_pending_only else "todos los accepted"
    print(f"Robustez run #{run_id}: modo={mode_label}; candidatos accepted={len(copied)}")
    print(f"Directorio robustez: {robust_dir}")
    print(f"Fechas robustez: {args.from_date or '(template)'} -> {args.to_date or '(template)'}")
    print(
        "Bonus robustez: "
        f"accepted={args.robust_positive_bonus:+.2f}, rejected={args.robust_negative_bonus:+.2f}"
    )
    batch_started_at = time.time()
    code = run_backtests(args, robust_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza robustez.")
        return 1
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            return code
    if args.dry_run:
        return 0

    symbol_map = parse_symbol_map(args.symbol_map)
    status_counts: dict[str, int] = {}
    for row, variant in copied:
        candidate_id = int(row["id"])
        report = find_report_for_set(variant.path, min_mtime=batch_started_at - 1.0)
        if not report:
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                None,
                "no_report",
                None,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts["no_report"] = status_counts.get("no_report", 0) + 1
            continue
        period_score_config = score_config_for_variant(
            score_config,
            variant,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        try:
            result = score_report_file(report, config=period_score_config)
        except Exception as exc:
            print(f"AVISO: no pude parsear robustez {report}: {exc}")
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                None,
                "parse_error",
                report,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
            continue
        matches, mismatch_reason = report_matches_variant(variant, result, symbol_map)
        if not matches:
            print(f"AVISO: reporte robustez no coincide para candidate #{candidate_id}: {mismatch_reason}")
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                result,
                "report_mismatch",
                report,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
            continue
        if result.trades <= 0:
            memory.record_candidate_robustness(
                candidate_id,
                run_id,
                result,
                "no_trades",
                report,
                args.from_date,
                args.to_date,
                args.robust_positive_bonus,
                args.robust_negative_bonus,
            )
            status_counts["no_trades"] = status_counts.get("no_trades", 0) + 1
            continue
        status = "accepted" if result.accepted else "rejected"
        memory.record_candidate_robustness(
            candidate_id,
            run_id,
            result,
            status,
            report,
            args.from_date,
            args.to_date,
            args.robust_positive_bonus,
            args.robust_negative_bonus,
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    print(
        "Robustez terminada: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0


def _relative_delta_pct(reference: float, observed: float, *, floor: float = 1.0) -> float:
    denominator = max(abs(reference), floor)
    return abs(observed - reference) / denominator * 100.0


def _bounded_profit_factor(value: float) -> float:
    return min(max(float(value), 0.0), 10.0)


def final_tick_similarity(
    ohlc_result: ScoreResult,
    real_tick_result: ScoreResult,
    *,
    min_history_quality: float,
    max_net_delta_pct: float,
    max_pf_delta_pct: float,
    max_dd_delta_pct: float,
    max_trades_delta_pct: float,
    min_model_profit_factor: float | None = None,
) -> dict[str, object]:
    """Decide si OHLC y real-tick son suficientemente parecidos.

    Es la MISMA estrategia ejecutada con dos modelos de datos distintos.
    La pregunta es: ¿dan resultados similares? No cuál es mejor.

    Criterios activos (contribuyen a accepted/rejected):
      - profit_factor  : diferencia relativa simétrica; cap [0,10]; piso 1.0
      - drawdown_pct   : diferencia relativa simétrica; piso 2pp evita falsos fallos en DDs pequeños
      - trades         : diferencia relativa simétrica

    net_profit se guarda como informacional pero NO bloquea la aceptación.
    La escala de normalized_net_profit depende del grupo de normalización y produce
    falsos fallos cuando los valores son pequeños en comparación absoluta.
    """
    reasons: list[str] = []
    checks: dict[str, dict[str, object]] = {}

    # 1. History quality
    history_quality = real_tick_result.history_quality
    quality_ok = history_quality is not None and history_quality >= min_history_quality
    if not quality_ok:
        reasons.append("history_quality")

    # 2. Net profit — informacional, no bloquea aceptación
    ohlc_net = float(ohlc_result.normalized_net_profit)
    tick_net  = float(real_tick_result.normalized_net_profit)
    net_denom = max(abs(ohlc_net), abs(tick_net), 1.0)
    net_delta = abs(tick_net - ohlc_net) / net_denom * 100.0
    checks["net_profit"] = {
        "ohlc": round(ohlc_net, 4),
        "real_tick": round(tick_net, 4),
        "delta_pct": round(net_delta, 4),
        "max_delta_pct": round(float(max_net_delta_pct), 4),
        "accepted": True,   # informacional: no bloquea
        "checked": False,
    }

    # 3. Profit factor — simétrico, cap [0, 10], piso 1.0
    ohlc_pf   = _bounded_profit_factor(ohlc_result.profit_factor)
    tick_pf   = _bounded_profit_factor(real_tick_result.profit_factor)
    if min_model_profit_factor is not None:
        min_pf = float(min_model_profit_factor)
        pf_floor_accepted = ohlc_pf >= min_pf and tick_pf >= min_pf
        if not pf_floor_accepted:
            reasons.append("profit_factor_floor")
        checks["profit_factor_floor"] = {
            "ohlc": round(ohlc_pf, 4),
            "real_tick": round(tick_pf, 4),
            "min_profit_factor": round(min_pf, 4),
            "accepted": pf_floor_accepted,
            "checked": True,
        }
    pf_delta  = _relative_delta_pct(ohlc_pf, tick_pf, floor=1.0)
    pf_accepted = pf_delta <= max_pf_delta_pct
    if not pf_accepted:
        reasons.append("profit_factor")
    checks["profit_factor"] = {
        "ohlc": round(ohlc_pf, 4),
        "real_tick": round(tick_pf, 4),
        "delta_pct": round(pf_delta, 4),
        "max_delta_pct": round(float(max_pf_delta_pct), 4),
        "accepted": pf_accepted,
        "checked": True,
    }

    # 4. Drawdown — simétrico, piso 2pp
    ohlc_dd   = float(ohlc_result.drawdown_pct)
    tick_dd   = float(real_tick_result.drawdown_pct)
    dd_floor  = max(ohlc_dd, tick_dd, 2.0)
    dd_delta  = abs(tick_dd - ohlc_dd) / dd_floor * 100.0
    dd_accepted = dd_delta <= max_dd_delta_pct
    if not dd_accepted:
        reasons.append("drawdown_pct")
    checks["drawdown_pct"] = {
        "ohlc": round(ohlc_dd, 4),
        "real_tick": round(tick_dd, 4),
        "delta_pct": round(dd_delta, 4),
        "max_delta_pct": round(float(max_dd_delta_pct), 4),
        "accepted": dd_accepted,
        "checked": True,
    }

    # 5. Trades — simétrico
    ohlc_trades   = float(ohlc_result.trades)
    tick_trades   = float(real_tick_result.trades)
    trades_delta  = _relative_delta_pct(ohlc_trades, tick_trades, floor=1.0)
    trades_accepted = trades_delta <= max_trades_delta_pct
    if not trades_accepted:
        reasons.append("trades")
    checks["trades"] = {
        "ohlc": round(ohlc_trades, 4),
        "real_tick": round(tick_trades, 4),
        "delta_pct": round(trades_delta, 4),
        "max_delta_pct": round(float(max_trades_delta_pct), 4),
        "accepted": trades_accepted,
        "checked": True,
    }

    return {
        "accepted": not reasons,
        "reasons": reasons,
        "history_quality": history_quality,
        "min_history_quality": float(min_history_quality),
        "checks": checks,
    }


def final_tick_ohlc_trades_pending_payload(ohlc_result: ScoreResult, min_ohlc_trades: int) -> dict[str, object]:
    return {
        "accepted": False,
        "pending": True,
        "reasons": ["ohlc_trades"],
        "checks": {
            "ohlc_trades": {
                "ohlc": int(ohlc_result.trades),
                "min_trades": int(min_ohlc_trades),
                "accepted": False,
            }
        },
    }


def _read_ohlc_report_cfg_dates(report_path: Path) -> tuple[str, str]:
    """Read the configured FromDate/ToDate from an MT5 Strategy Tester HTML report.

    Parses the Period cell which looks like 'H4 (2026.05.01 - 2026.05.31)'.
    Returns empty strings if the file cannot be read or the pattern is not found.
    """
    for encoding in ("utf-16-le", "utf-8", "utf-16"):
        try:
            content = report_path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, UnicodeError, OSError):
            continue
    else:
        return "", ""
    match = re.search(r"\((\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})\)", content)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def final_tick_dates_match(row: sqlite3.Row, from_date: str, to_date: str) -> bool:
    stored_from = str(row["final_tick_from_date"] or "").strip()
    stored_to = str(row["final_tick_to_date"] or "").strip()
    if not stored_from and not stored_to:
        return False
    return stored_from == str(from_date or "").strip() and stored_to == str(to_date or "").strip()


def final_tick_row_pending_for_dates(
    row: sqlite3.Row,
    from_date: str,
    to_date: str,
    *,
    final_tick_stage: str = "probe",
    force_quality_retry: bool = False,
) -> bool:
    stage = normalize_final_tick_stage(final_tick_stage)
    status = str(row["final_tick_status"] or "").strip()
    if not status:
        return True
    if status in FINAL_TICK_RETRYABLE_STATUSES:
        return True
    if force_quality_retry and status == "pending_history_quality":
        return True  # retry regardless of stored dates
    if stage == "six_month" and status == "pending_ohlc_trades":
        return not final_tick_dates_match(row, from_date, to_date)
    if status in FINAL_TICK_DATE_RETRYABLE_STATUSES:
        return stage == "six_month" and not final_tick_dates_match(row, from_date, to_date)
    return False


def final_tick_ohlc_retry_needed_for_dates(
    rows: Iterable[sqlite3.Row],
    from_date: str,
    to_date: str,
    *,
    final_tick_stage: str = "probe",
    force_quality_retry: bool = False,
) -> bool:
    if normalize_final_tick_stage(final_tick_stage) != "six_month":
        return False
    for row in rows:
        if str(row["final_tick_status"] or "").strip() != "pending_ohlc_trades":
            continue
        if final_tick_row_pending_for_dates(
            row,
            from_date,
            to_date,
            final_tick_stage=final_tick_stage,
            force_quality_retry=force_quality_retry,
        ):
            return True
    return False


def final_tick_ohlc_retry_exhausted_for_dates(row: sqlite3.Row, from_date: str, to_date: str) -> bool:
    if str(row["final_tick_status"] or "").strip() != "pending_ohlc_trades":
        return False
    return final_tick_dates_match(row, from_date, to_date)


def normalize_final_tick_stage(value: object) -> str:
    text = str(value or "probe").strip().lower().replace("-", "_")
    if text in {"6m", "sixmonth", "six_month"}:
        return "six_month"
    return "probe"


def final_tick_stage_label(stage: str) -> str:
    return "Final Tick 6M" if stage == "six_month" else "Final Tick"


def final_tick_stage_dir_name(stage: str) -> str:
    return "final_tick_6m" if stage == "six_month" else "final_tick"


def final_tick_stage_prefixes(stage: str, *, ohlc_retry: bool = False) -> tuple[str, str]:
    if stage == "six_month":
        return ("ohlc6m_retry" if ohlc_retry else "ohlc6m", "tick6m")
    return ("ohlc", "tick")


def validate_final_tick_stage_dates(stage: str, from_date: str, to_date: str) -> str | None:
    if stage != "six_month":
        return None
    from_text = from_date.strip()
    to_text = to_date.strip()
    try:
        start = datetime.strptime(from_text, "%Y.%m.%d")
        end = datetime.strptime(to_text, "%Y.%m.%d")
    except ValueError:
        return "Final Tick 6M requiere fechas en formato YYYY.MM.DD."
    days = (end - start).days
    if days < FINAL_TICK_6M_MIN_DAYS:
        min_to_date = (start + timedelta(days=FINAL_TICK_6M_MIN_DAYS)).strftime("%Y.%m.%d")
        return (
            f"Final Tick 6M requiere un rango minimo de {FINAL_TICK_6M_MIN_DAYS} dias; "
            f"rango actual {days} dias ({from_text} -> {to_text}). "
            f"Usa Hasta >= {min_to_date}."
        )
    return None


def evaluate_candidate_final_tick(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    final_tick_stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    memory.active_final_tick_stage = final_tick_stage
    final_tick_label = final_tick_stage_label(final_tick_stage)
    if getattr(args, "final_tick_reconcile_only", False):
        run = memory.run_by_id(args.final_tick_run_id) if args.final_tick_run_id else memory.latest_run()
        if run is None:
            print(f"ERROR: no hay run SQLite disponible para {final_tick_label}")
            return 1
        counts = reconcile_final_tick_reports(
            memory,
            int(run["id"]),
            score_config,
            parse_symbol_map(args.symbol_map),
            final_tick_stage=final_tick_stage,
            min_history_quality=args.final_tick_min_history_quality,
            min_ohlc_trades=args.final_tick_min_ohlc_trades,
            max_net_delta_pct=args.final_tick_max_net_delta_pct,
            max_pf_delta_pct=args.final_tick_max_pf_delta_pct,
            max_dd_delta_pct=args.final_tick_max_dd_delta_pct,
            max_trades_delta_pct=args.final_tick_max_trades_delta_pct,
        )
        if counts:
            print(
                f"{final_tick_label} reconciliado desde disco: "
                + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            )
        else:
            print(f"{final_tick_label} reconcile: no hay reportes en disco utilizables para filas pendientes.")
        print(f"Memoria: {memory.path}")
        return 0
    if not args.expert and not args.multi_terminal and not args.dry_run:
        print(f"ERROR: {final_tick_label} requiere --expert o --multi-terminal")
        return 1
    if not str(args.from_date or "").strip() or not str(args.to_date or "").strip():
        print(f"ERROR: {final_tick_label} requiere --from-date y --to-date para comparar el mismo tramo OHLC vs real tick.")
        return 1
    date_error = validate_final_tick_stage_dates(final_tick_stage, str(args.from_date or ""), str(args.to_date or ""))
    if date_error:
        print(f"ERROR: {date_error}")
        return 1

    run = memory.run_by_id(args.final_tick_run_id) if args.final_tick_run_id else memory.latest_run()
    if run is None:
        print(f"ERROR: no hay run SQLite disponible para {final_tick_label}")
        return 1

    run_id = int(run["id"])
    run_dir = Path(run["output_dir"])
    rows = [
        row
        for row in memory.accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
        if Path(row["set_path"]).exists()
    ]
    main_from_date = str(args.from_date or "").strip()
    main_to_date = str(args.to_date or "").strip()
    ohlc_retry_from = str(getattr(args, "final_tick_ohlc_from_date", "") or "").strip()
    ohlc_retry_to = str(getattr(args, "final_tick_ohlc_to_date", "") or "").strip()
    def _row_uses_retry_dates(row: sqlite3.Row) -> bool:
        # Una fila que ya quedo registrada con el rango retry OHLC (p. ej. un
        # mismatch ocurrido durante un retry) debe re-ejecutarse con ese mismo
        # rango, no con las fechas principales.
        if not ohlc_retry_from or not ohlc_retry_to:
            return False
        return (
            str(row["final_tick_from_date"] or "").strip() == ohlc_retry_from
            and str(row["final_tick_to_date"] or "").strip() == ohlc_retry_to
        )

    def _row_in_retry_scope(row: sqlite3.Row) -> bool:
        return (
            str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades"
            or _row_uses_retry_dates(row)
        )

    has_ohlc_trades_pending = False
    if ohlc_retry_from and ohlc_retry_to:
        has_ohlc_trades_pending = final_tick_ohlc_retry_needed_for_dates(
            rows,
            ohlc_retry_from,
            ohlc_retry_to,
            final_tick_stage=final_tick_stage,
            force_quality_retry=bool(getattr(args, "final_tick_retry_pending_quality", False)),
        )
    else:
        has_ohlc_trades_pending = any(
            str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades"
            for row in rows
        )
    using_ohlc_retry_dates = False
    if final_tick_stage == "six_month" and args.final_tick_pending_only and has_ohlc_trades_pending and (ohlc_retry_from or ohlc_retry_to):
        if not ohlc_retry_from or not ohlc_retry_to:
            print(f"ERROR: {final_tick_label} OHLC retry requiere ambas fechas alternativas Desde y Hasta.")
            return 1
        args.from_date = ohlc_retry_from
        args.to_date = ohlc_retry_to
        date_error = validate_final_tick_stage_dates(final_tick_stage, str(args.from_date or ""), str(args.to_date or ""))
        if date_error:
            print(f"ERROR: {date_error}")
            return 1
        using_ohlc_retry_dates = True
        print(f"{final_tick_label} OHLC retry: usando fechas alternativas {args.from_date} -> {args.to_date}.")
    retry_pending_quality = bool(getattr(args, "final_tick_retry_pending_quality", False))
    if args.final_tick_pending_only:
        if using_ohlc_retry_dates:
            deferred_main_rows = [
                row for row in rows
                if not _row_in_retry_scope(row)
                and final_tick_row_pending_for_dates(
                    row,
                    main_from_date,
                    main_to_date,
                    final_tick_stage=final_tick_stage,
                    force_quality_retry=retry_pending_quality,
                )
            ]
            rows = [
                row for row in rows
                if _row_in_retry_scope(row)
                and final_tick_row_pending_for_dates(
                    row,
                    args.from_date,
                    args.to_date,
                    final_tick_stage=final_tick_stage,
                    force_quality_retry=retry_pending_quality,
                )
            ]
            if deferred_main_rows:
                print(
                    f"{final_tick_label} OHLC retry: "
                    f"{len(deferred_main_rows)} fila(s) pendientes de fechas principales "
                    f"{main_from_date} -> {main_to_date} se dejan para la siguiente continuacion."
                )
        else:
            rows = [
                row for row in rows
                if final_tick_row_pending_for_dates(
                    row,
                    args.from_date,
                    args.to_date,
                    final_tick_stage=final_tick_stage,
                    force_quality_retry=retry_pending_quality,
                )
                and not (
                    final_tick_stage == "six_month"
                    and final_tick_ohlc_retry_exhausted_for_dates(row, ohlc_retry_from, ohlc_retry_to)
                )
            ]
    if not rows:
        if args.final_tick_pending_only:
            print(f"{final_tick_label} run #{run_id}: no hay candidatos pendientes.")
        else:
            print(f"{final_tick_label} run #{run_id}: no hay candidatos elegibles con .set existente.")
        return 0
    stored_dates_match = all(
        not str(row["final_tick_status"] or "").strip()
        or final_tick_dates_match(row, args.from_date, args.to_date)
        for row in rows
    )

    mode = "pending" if args.final_tick_pending_only else "all"
    final_dir = run_dir / final_tick_stage_dir_name(final_tick_stage) / f"run_{run_id}_{mode}"
    resume_pending_dir = args.final_tick_pending_only and final_dir.exists()
    if resume_pending_dir:
        if not final_dir.is_dir():
            raise NotADirectoryError(final_dir)
        final_dir.mkdir(parents=True, exist_ok=True)
    else:
        final_dir = recreate_work_dir(final_dir)
    ohlc_dir = final_dir / "ohlc_sets"
    real_tick_dir = final_dir / "real_tick_sets"
    ohlc_dir.mkdir(parents=True, exist_ok=True)
    real_tick_dir.mkdir(parents=True, exist_ok=True)

    copied: list[tuple[sqlite3.Row, Variant, Variant]] = []
    used_names: set[str] = set()
    ohlc_sets_unchanged = True
    ohlc_prefix, tick_prefix = final_tick_stage_prefixes(final_tick_stage, ohlc_retry=using_ohlc_retry_dates)
    for row in rows:
        candidate_id = int(row["id"])
        source_set = Path(row["set_path"])
        ohlc_name = f"{ohlc_prefix}_{candidate_id:06d}_{source_set.name}"
        real_tick_name = f"{tick_prefix}_{candidate_id:06d}_{source_set.name}"
        if ohlc_name in used_names or real_tick_name in used_names:
            print(f"ERROR: nombre duplicado en {final_tick_label} para candidate #{candidate_id}")
            return 1
        used_names.update({ohlc_name, real_tick_name})

        ohlc_set = ohlc_dir / ohlc_name
        real_tick_set = real_tick_dir / real_tick_name
        if resume_pending_dir:
            source_digest = file_digest(source_set)
            ohlc_digest = file_digest(ohlc_set) if ohlc_set.exists() else None
            if not source_digest or ohlc_digest != source_digest:
                ohlc_sets_unchanged = False
        shutil.copy2(source_set, ohlc_set)
        shutil.copy2(source_set, real_tick_set)
        if not args.dry_run:
            remove_report_artifacts(real_tick_set)
            if not resume_pending_dir:
                remove_report_artifacts(ohlc_set)

        original_variant = variant_from_candidate_row(row)
        ohlc_variant = Variant(
            path=ohlc_set,
            seed=original_variant.seed,
            target_symbol=original_variant.target_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+{final_tick_stage_dir_name(final_tick_stage)}_ohlc",
        )
        real_tick_variant = Variant(
            path=real_tick_set,
            seed=original_variant.seed,
            target_symbol=original_variant.target_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+{final_tick_stage_dir_name(final_tick_stage)}_real",
        )
        copied.append((row, ohlc_variant, real_tick_variant))

    print(f"{final_tick_label} run #{run_id}: modo={mode}; candidatos={len(copied)}")
    print(f"Directorio {final_tick_label}: {final_dir}")
    print(f"Fechas {final_tick_label}: {args.from_date or '(template)'} -> {args.to_date or '(template)'}")
    print(
        f"Criterios {final_tick_label}: "
        f"History Quality>={args.final_tick_min_history_quality:.2f}% | "
        f"Net delta<={args.final_tick_max_net_delta_pct:.2f}% | "
        f"PF delta<={args.final_tick_max_pf_delta_pct:.2f}% | "
        f"DD delta<={args.final_tick_max_dd_delta_pct:.2f}% | "
        f"Trades delta<={args.final_tick_max_trades_delta_pct:.2f}%"
    )

    symbol_map = parse_symbol_map(args.symbol_map)
    ohlc_variants = [ohlc_variant for _, ohlc_variant, _ in copied]
    real_tick_variants = [real_tick_variant for _, _, real_tick_variant in copied]
    skip_ohlc = False
    ohlc_min_report_mtime: float | None = None
    skip_ohlc_flag = bool(getattr(args, "final_tick_skip_ohlc", False))
    if skip_ohlc_flag:
        missing_metrics = [int(row["id"]) for row, _, _ in copied if not row["ft_ohlc_metrics_json"]]
        if missing_metrics:
            ids = ", ".join(f"#{i}" for i in missing_metrics)
            print(f"ERROR: --final-tick-skip-ohlc pero faltan ohlc_metrics_json para candidates {ids}.")
            return 1
        # Read the configured dates directly from the OHLC report file (ground truth).
        # The DB's from_date/to_date can be stale if a previous tick retry used a
        # different UI date and overwrote the stored value.
        first_ohlc_path_str = str(copied[0][0]["ft_ohlc_report_path"] or "")
        if first_ohlc_path_str:
            ohlc_cfg_from, ohlc_cfg_to = _read_ohlc_report_cfg_dates(Path(first_ohlc_path_str))
        else:
            ohlc_cfg_from, ohlc_cfg_to = "", ""
        if ohlc_cfg_from and ohlc_cfg_to:
            if ohlc_cfg_from != str(args.from_date or "").strip() or ohlc_cfg_to != str(args.to_date or "").strip():
                print(
                    f"Final Tick skip-ohlc: fechas leidas del reporte OHLC "
                    f"{ohlc_cfg_from} -> {ohlc_cfg_to} "
                    f"(UI: {args.from_date} -> {args.to_date}); se usan las del reporte."
                )
            args.from_date = ohlc_cfg_from
            args.to_date = ohlc_cfg_to
        else:
            print(
                f"AVISO: no se pudo leer fecha del reporte OHLC '{first_ohlc_path_str}'; "
                f"se usa fecha del UI ({args.from_date} -> {args.to_date})."
            )
        skip_ohlc = True
        print(
            f"Final Tick skip-ohlc: usando OHLC guardado en DB; "
            f"solo se ejecuta Every Tick ({len(copied)} candidatos)."
        )
    elif resume_pending_dir and ohlc_sets_unchanged and stored_dates_match:
        valid_ohlc_reports = count_valid_existing_reports(
            ohlc_variants,
            score_config,
            symbol_map,
            min_trades_w1=args.final_tick_min_trades_w1,
            min_trades_mn=args.final_tick_min_trades_mn,
        )
        if valid_ohlc_reports == len(ohlc_variants):
            # Verify the OHLC reports on disk have the expected dates.
            # A previous interrupted run may have overwritten the files with different dates
            # without updating the DB, so stored_dates_match=True is insufficient.
            first_rep = find_report_for_set(ohlc_variants[0].path, min_mtime=None) if ohlc_variants else None
            ohlc_disk_from, ohlc_disk_to = _read_ohlc_report_cfg_dates(first_rep) if first_rep else ("", "")
            if ohlc_disk_from and ohlc_disk_from != str(args.from_date or "").strip():
                print(
                    f"Final Tick resume: reporte OHLC en disco tiene fechas "
                    f"{ohlc_disk_from} -> {ohlc_disk_to} pero UI={args.from_date} -> {args.to_date}; "
                    f"se recalcula OHLC."
                )
                if not args.dry_run:
                    for variant in ohlc_variants:
                        remove_report_artifacts(variant.path)
            else:
                skip_ohlc = True
                print(
                    "Final Tick resume: OHLC existente completo; "
                    "se salta OHLC y se continua con Every Tick."
                )
        else:
            print(
                "Final Tick resume: OHLC incompleto "
                f"({valid_ohlc_reports}/{len(ohlc_variants)} reportes validos); se recalcula OHLC."
            )
            if not args.dry_run:
                for variant in ohlc_variants:
                    remove_report_artifacts(variant.path)
    elif resume_pending_dir:
        if not stored_dates_match:
            print("Final Tick resume: las fechas cambiaron; se recalcula OHLC.")
        else:
            print("Final Tick resume: los .set OHLC faltan o cambiaron; se recalcula OHLC.")
        if not args.dry_run:
            for variant in ohlc_variants:
                remove_report_artifacts(variant.path)

    if not skip_ohlc:
        ohlc_backtest_dir = ohlc_dir
        if args.final_tick_pending_only:
            ohlc_backtest_dir = prepare_final_tick_exec_dir(final_dir / "_pending_ohlc_sets", ohlc_variants)
            print(f"Final Tick pending: OHLC en cola={len(ohlc_variants)} set(s).")
        ohlc_started_at = time.time()
        ohlc_min_report_mtime = ohlc_started_at - 1.0
        ohlc_code = run_backtests(args, ohlc_backtest_dir, model="1")
        if ohlc_code == RUNNING_TERMINAL_EXIT_CODE:
            print("ERROR: run_tests.py no ejecuto OHLC Final Tick porque hay una terminal MT5 abierta.")
            return 1
        if ohlc_code != 0:
            print(f"AVISO: OHLC Final Tick termino con codigo {ohlc_code}; se evaluaran reportes disponibles")
            if args.dry_run:
                return ohlc_code

    if args.dry_run:
        return 0

    status_counts: dict[str, int] = {}
    ready_for_tick: list[tuple[sqlite3.Row, Variant, Variant]] = []
    ohlc_results: dict[int, tuple[Path, ScoreResult]] = {}
    default_min_ohlc_trades = max(0, int(args.final_tick_min_ohlc_trades))

    if skip_ohlc_flag:
        for row, ohlc_variant, real_tick_variant in copied:
            candidate_id = int(row["id"])
            try:
                data = json.loads(str(row["ft_ohlc_metrics_json"]))
                data["reasons"] = tuple(data.get("reasons", []))
                ohlc_result = ScoreResult(**data)
            except Exception as exc:
                print(f"AVISO: no pude reconstruir OHLC metrics para candidate #{candidate_id}: {exc}")
                memory.record_candidate_final_tick(
                    candidate_id, run_id, "parse_error", None, None,
                    None, None, None, None,
                    args.final_tick_min_history_quality, args.from_date, args.to_date,
                    args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
                )
                status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
                continue
            stored_path = row["ft_ohlc_report_path"]
            ohlc_report = Path(stored_path) if stored_path else ohlc_variant.path
            ready_for_tick.append((row, ohlc_variant, real_tick_variant))
            ohlc_results[candidate_id] = (ohlc_report, ohlc_result)
    else:
        for row, ohlc_variant, real_tick_variant in copied:
            candidate_id = int(row["id"])
            ohlc_report = find_report_for_set(ohlc_variant.path, min_mtime=ohlc_min_report_mtime)
            if not ohlc_report:
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "no_report",
                    None,
                    None,
                    ohlc_report,
                    None,
                    None,
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["no_report"] = status_counts.get("no_report", 0) + 1
                continue

            ohlc_score_config = score_config_for_variant(
                score_config,
                ohlc_variant,
                min_trades_w1=args.final_tick_min_trades_w1,
                min_trades_mn=args.final_tick_min_trades_mn,
            )
            try:
                ohlc_result = score_report_file(ohlc_report, config=ohlc_score_config)
            except Exception as exc:
                print(f"AVISO: no pude parsear OHLC Final Tick candidate #{candidate_id}: {exc}")
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "parse_error",
                    None,
                    None,
                    ohlc_report,
                    None,
                    None,
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
                continue

            ohlc_matches, ohlc_mismatch = report_matches_variant(ohlc_variant, ohlc_result, symbol_map)
            if not ohlc_matches:
                print(f"AVISO: reporte OHLC Final Tick no coincide para candidate #{candidate_id}: {ohlc_mismatch}")
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "report_mismatch",
                    ohlc_result,
                    None,
                    ohlc_report,
                    None,
                    None,
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
                continue

            min_ohlc_trades = min_trades_for_period(
                ohlc_variant.target_period,
                default_min_ohlc_trades,
                args.final_tick_min_trades_w1,
                args.final_tick_min_trades_mn,
            )
            if ohlc_result.trades < min_ohlc_trades:
                payload = final_tick_ohlc_trades_pending_payload(ohlc_result, min_ohlc_trades)
                memory.record_candidate_final_tick(
                    candidate_id,
                    run_id,
                    "pending_ohlc_trades",
                    ohlc_result,
                    None,
                    ohlc_report,
                    None,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                    None,
                    args.final_tick_min_history_quality,
                    args.from_date,
                    args.to_date,
                    args.final_tick_max_net_delta_pct,
                    args.final_tick_max_pf_delta_pct,
                    args.final_tick_max_dd_delta_pct,
                    args.final_tick_max_trades_delta_pct,
                )
                status_counts["pending_ohlc_trades"] = status_counts.get("pending_ohlc_trades", 0) + 1
                continue

            ready_for_tick.append((row, ohlc_variant, real_tick_variant))
            ohlc_results[candidate_id] = (ohlc_report, ohlc_result)

    reconciled_tick = 0
    if resume_pending_dir and ready_for_tick:
        # Reconciliar reportes Real Tick ya existentes en disco (p. ej. de un
        # proceso interrumpido manualmente) antes de relanzar MT5. Solo se
        # reutiliza un reporte si su rango de fechas coincide con el del OHLC
        # de control y el symbol/TF es el esperado.
        still_pending = []
        for row, ohlc_variant, real_tick_variant in ready_for_tick:
            candidate_id = int(row["id"])
            ohlc_report, ohlc_result = ohlc_results[candidate_id]
            existing_tick = find_report_for_set(real_tick_variant.path)
            reused = False
            if existing_tick is not None:
                tick_dates = _read_ohlc_report_cfg_dates(existing_tick)
                ohlc_dates = _read_ohlc_report_cfg_dates(Path(ohlc_report))
                if tick_dates[0] and tick_dates == ohlc_dates:
                    reused = _evaluate_final_tick_tick_report(
                        memory, args, score_config, symbol_map, run_id,
                        candidate_id, real_tick_variant, ohlc_report, ohlc_result,
                        existing_tick, status_counts, reconcile=True,
                    )
            if reused:
                reconciled_tick += 1
                print(f"Final Tick resume: reporte Real Tick existente reutilizado para candidate #{candidate_id}.")
            else:
                still_pending.append((row, ohlc_variant, real_tick_variant))
        ready_for_tick = still_pending

    if not ready_for_tick:
        if reconciled_tick:
            print(f"Final Tick: {reconciled_tick} reporte(s) Real Tick reconciliados desde disco; no hay nada que ejecutar.")
        else:
            print("Final Tick: ningun OHLC cumple el minimo de operaciones; no se lanza Every Tick.")
        print(
            "Final Tick terminado: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; memoria={memory.path}"
        )
        return 0

    ready_real_tick_variants = [real_tick_variant for _, _, real_tick_variant in ready_for_tick]
    real_tick_backtest_dir = real_tick_dir
    if args.final_tick_pending_only:
        real_tick_backtest_dir = prepare_final_tick_exec_dir(final_dir / "_pending_real_tick_sets", ready_real_tick_variants)
        print(f"Final Tick pending: Every Tick en cola={len(ready_real_tick_variants)} set(s).")
    real_tick_started_at = time.time()
    real_tick_min_report_mtime = real_tick_started_at - 1.0
    real_tick_code = run_backtests(args, real_tick_backtest_dir, model="4")
    if real_tick_code == RUNNING_TERMINAL_EXIT_CODE:
        print("ERROR: run_tests.py no ejecuto Real Tick Final Tick porque hay una terminal MT5 abierta.")
        return 1
    if real_tick_code != 0:
        print(f"AVISO: Real Tick Final Tick termino con codigo {real_tick_code}; se evaluaran reportes disponibles")

    for row, ohlc_variant, real_tick_variant in ready_for_tick:
        candidate_id = int(row["id"])
        ohlc_report, ohlc_result = ohlc_results[candidate_id]
        real_tick_report = find_report_for_set(real_tick_variant.path, min_mtime=real_tick_min_report_mtime)
        if not real_tick_report:
            memory.record_candidate_final_tick(
                candidate_id,
                run_id,
                "no_report",
                ohlc_result,
                None,
                ohlc_report,
                None,
                None,
                None,
                args.final_tick_min_history_quality,
                args.from_date,
                args.to_date,
                args.final_tick_max_net_delta_pct,
                args.final_tick_max_pf_delta_pct,
                args.final_tick_max_dd_delta_pct,
                args.final_tick_max_trades_delta_pct,
            )
            status_counts["no_report"] = status_counts.get("no_report", 0) + 1
            continue

        _evaluate_final_tick_tick_report(
            memory,
            args,
            score_config,
            symbol_map,
            run_id,
            candidate_id,
            real_tick_variant,
            ohlc_report,
            ohlc_result,
            real_tick_report,
            status_counts,
        )

    print(
        "Final Tick terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; memoria={memory.path}"
    )
    return 0


def _evaluate_final_tick_tick_report(
    memory: AgentMemory,
    args: argparse.Namespace,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    run_id: int,
    candidate_id: int,
    real_tick_variant: Variant,
    ohlc_report: Path,
    ohlc_result: ScoreResult,
    real_tick_report: Path,
    status_counts: dict[str, int],
    *,
    reconcile: bool = False,
) -> bool:
    """Evalua un reporte Real Tick contra su OHLC y registra el resultado.

    Con reconcile=True solo registra cuando el reporte es utilizable
    (parseable, symbol/TF correctos); si no lo es devuelve False sin tocar
    memoria, para que el candidato vuelva a la cola de ejecucion.
    """
    tick_score_config = score_config_for_variant(
        score_config,
        real_tick_variant,
        min_trades_w1=args.final_tick_min_trades_w1,
        min_trades_mn=args.final_tick_min_trades_mn,
    )
    try:
        real_tick_result = score_report_file(real_tick_report, config=tick_score_config)
    except Exception as exc:
        if reconcile:
            return False
        print(f"AVISO: no pude parsear Real Tick Final Tick candidate #{candidate_id}: {exc}")
        memory.record_candidate_final_tick(
            candidate_id, run_id, "parse_error", ohlc_result, None,
            ohlc_report, real_tick_report, None, None,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
        return True

    real_matches, real_mismatch = report_matches_variant(real_tick_variant, real_tick_result, symbol_map)
    if not real_matches:
        if reconcile:
            return False
        print(f"AVISO: reporte Real Tick Final Tick no coincide para candidate #{candidate_id}: {real_mismatch}")
        memory.record_candidate_final_tick(
            candidate_id, run_id, "report_mismatch", ohlc_result, real_tick_result,
            ohlc_report, real_tick_report, None, real_tick_result.history_quality,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
        return True

    if real_tick_result.trades <= 0:
        memory.record_candidate_final_tick(
            candidate_id, run_id, "no_trades", ohlc_result, real_tick_result,
            ohlc_report, real_tick_report, None, real_tick_result.history_quality,
            args.final_tick_min_history_quality, args.from_date, args.to_date,
            args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
            args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
        )
        status_counts["no_trades"] = status_counts.get("no_trades", 0) + 1
        return True

    is_six_month = memory.active_final_tick_stage == "six_month"
    similarity = final_tick_similarity(
        ohlc_result,
        real_tick_result,
        min_history_quality=args.final_tick_min_history_quality,
        max_net_delta_pct=args.final_tick_max_net_delta_pct,
        max_pf_delta_pct=min(float(args.final_tick_max_pf_delta_pct), 30.0) if is_six_month else args.final_tick_max_pf_delta_pct,
        max_dd_delta_pct=args.final_tick_max_dd_delta_pct,
        max_trades_delta_pct=args.final_tick_max_trades_delta_pct,
        min_model_profit_factor=float(score_config.min_profit_factor) if is_six_month else None,
    )
    reasons = set(str(reason) for reason in (similarity.get("reasons") or []))
    if "history_quality" in reasons:
        status = "pending_history_quality"
    else:
        status = "accepted" if bool(similarity.get("accepted")) else "rejected"
    memory.record_candidate_final_tick(
        candidate_id, run_id, status, ohlc_result, real_tick_result,
        ohlc_report, real_tick_report,
        json.dumps(similarity, ensure_ascii=True, sort_keys=True),
        real_tick_result.history_quality,
        args.final_tick_min_history_quality, args.from_date, args.to_date,
        args.final_tick_max_net_delta_pct, args.final_tick_max_pf_delta_pct,
        args.final_tick_max_dd_delta_pct, args.final_tick_max_trades_delta_pct,
    )
    status_counts[status] = status_counts.get(status, 0) + 1
    return True


def reconcile_final_tick_reports(
    memory: AgentMemory,
    run_id: int,
    score_config: ScoreConfig,
    symbol_map: dict[str, str],
    *,
    final_tick_stage: str = "probe",
    min_history_quality: float = 80.0,
    min_ohlc_trades: int = 5,
    min_trades_w1: int = 2,
    min_trades_mn: int = 1,
    max_net_delta_pct: float = 35.0,
    max_pf_delta_pct: float = 35.0,
    max_dd_delta_pct: float = 35.0,
    max_trades_delta_pct: float = 35.0,
) -> dict[str, int]:
    """Concilia desde disco los reportes Final Tick ya generados, sin abrir MT5.

    Para cada candidato robust-accepted sin estado final, busca su par de
    reportes `ohlc_*`/`tick_*` en `reports/`. El par solo se registra cuando el
    OHLC coincide con el target del candidato y ambos reportes comparten el
    mismo rango de fechas (verdad de disco, no la DB ni la UI). Permite
    recuperar el trabajo completado de un proceso interrumpido manualmente.
    """
    rows = [
        row
        for row in memory.accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
        if Path(row["set_path"]).exists()
    ]
    memory.active_final_tick_stage = normalize_final_tick_stage(final_tick_stage)
    ohlc_prefix, tick_prefix = final_tick_stage_prefixes(memory.active_final_tick_stage)
    policy_prefix = final_tick_stage_dir_name(memory.active_final_tick_stage)
    status_counts: dict[str, int] = {}
    for row in rows:
        if str(row["final_tick_status"] or "").strip() in {"accepted", "rejected"}:
            continue
        candidate_id = int(row["id"])
        source_set = Path(row["set_path"])
        ohlc_set_name = f"{ohlc_prefix}_{candidate_id:06d}_{source_set.name}"
        tick_set_name = f"{tick_prefix}_{candidate_id:06d}_{source_set.name}"
        ohlc_report = find_report_for_set(Path(ohlc_set_name))
        if ohlc_report is None:
            continue
        ohlc_dates = _read_ohlc_report_cfg_dates(ohlc_report)
        if not ohlc_dates[0] or not ohlc_dates[1]:
            continue
        original_variant = variant_from_candidate_row(row)
        ohlc_variant = Variant(
            path=Path(ohlc_set_name),
            seed=original_variant.seed,
            target_symbol=original_variant.target_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+{policy_prefix}_ohlc",
        )
        try:
            ohlc_result = score_report_file(
                ohlc_report,
                config=score_config_for_variant(
                    score_config,
                    ohlc_variant,
                    min_trades_w1=min_trades_w1,
                    min_trades_mn=min_trades_mn,
                ),
            )
        except Exception:
            continue
        ohlc_matches, _ = report_matches_variant(ohlc_variant, ohlc_result, symbol_map)
        if not ohlc_matches:
            continue
        thresholds = argparse.Namespace(
            final_tick_min_history_quality=float(min_history_quality),
            from_date=ohlc_dates[0],
            to_date=ohlc_dates[1],
            final_tick_max_net_delta_pct=float(max_net_delta_pct),
            final_tick_max_pf_delta_pct=float(max_pf_delta_pct),
            final_tick_max_dd_delta_pct=float(max_dd_delta_pct),
            final_tick_max_trades_delta_pct=float(max_trades_delta_pct),
            final_tick_min_trades_w1=int(min_trades_w1),
            final_tick_min_trades_mn=int(min_trades_mn),
        )
        period_min_ohlc_trades = min_trades_for_period(
            ohlc_variant.target_period,
            int(min_ohlc_trades),
            int(min_trades_w1),
            int(min_trades_mn),
        )
        if ohlc_result.trades < period_min_ohlc_trades:
            payload = final_tick_ohlc_trades_pending_payload(ohlc_result, period_min_ohlc_trades)
            memory.record_candidate_final_tick(
                candidate_id, run_id, "pending_ohlc_trades", ohlc_result, None,
                ohlc_report, None,
                json.dumps(payload, ensure_ascii=True, sort_keys=True), None,
                thresholds.final_tick_min_history_quality,
                thresholds.from_date, thresholds.to_date,
                float(max_net_delta_pct), float(max_pf_delta_pct),
                float(max_dd_delta_pct), float(max_trades_delta_pct),
            )
            status_counts["pending_ohlc_trades"] = status_counts.get("pending_ohlc_trades", 0) + 1
            continue
        tick_report = find_report_for_set(Path(tick_set_name))
        if tick_report is None:
            continue
        if _read_ohlc_report_cfg_dates(tick_report) != ohlc_dates:
            continue
        real_tick_variant = Variant(
            path=Path(tick_set_name),
            seed=original_variant.seed,
            target_symbol=original_variant.target_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+{policy_prefix}_real",
        )
        if _evaluate_final_tick_tick_report(
            memory, thresholds, score_config, symbol_map, run_id,
            candidate_id, real_tick_variant, ohlc_report, ohlc_result,
            tick_report, status_counts, reconcile=True,
        ):
            print(f"Final Tick reconcile: candidate #{candidate_id} registrado desde reportes en disco.")
    return status_counts


def rescore_final_tick_only(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    final_tick_stage = normalize_final_tick_stage(getattr(args, "final_tick_stage", "probe"))
    memory.active_final_tick_stage = final_tick_stage
    final_tick_table = final_tick_table_for_stage(final_tick_stage)
    symbol_map = parse_symbol_map(args.symbol_map)
    rows = memory.conn.execute(
        f"""
        select
            c.*,
            ft.run_id as ft_run_id,
            ft.ohlc_report_path as ft_ohlc_report_path,
            ft.real_tick_report_path as ft_real_tick_report_path,
            ft.from_date as ft_from_date,
            ft.to_date as ft_to_date
        from {final_tick_table} ft
        join candidates c on c.id = ft.candidate_id
        order by ft.run_id, c.generation, c.id
        """
    ).fetchall()
    status_counts: dict[str, int] = {}
    skipped_missing = 0
    for row in rows:
        candidate_id = int(row["id"])
        run_id = int(row["ft_run_id"] or row["run_id"])
        ohlc_report_raw = str(row["ft_ohlc_report_path"] or "").strip()
        real_tick_report_raw = str(row["ft_real_tick_report_path"] or "").strip()
        ohlc_report = Path(ohlc_report_raw) if ohlc_report_raw else None
        real_tick_report = Path(real_tick_report_raw) if real_tick_report_raw else None
        if ohlc_report is None or not ohlc_report.exists():
            skipped_missing += 1
            continue
        from_date, to_date = _read_ohlc_report_cfg_dates(ohlc_report)
        from_date = from_date or str(row["ft_from_date"] or args.from_date or "")
        to_date = to_date or str(row["ft_to_date"] or args.to_date or "")
        original_variant = variant_from_candidate_row(row)
        ohlc_variant = Variant(
            path=ohlc_report,
            seed=original_variant.seed,
            target_symbol=original_variant.target_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+final_tick_ohlc_rescore",
        )
        thresholds = argparse.Namespace(
            final_tick_min_history_quality=float(args.final_tick_min_history_quality),
            from_date=from_date,
            to_date=to_date,
            final_tick_max_net_delta_pct=float(args.final_tick_max_net_delta_pct),
            final_tick_max_pf_delta_pct=float(args.final_tick_max_pf_delta_pct),
            final_tick_max_dd_delta_pct=float(args.final_tick_max_dd_delta_pct),
            final_tick_max_trades_delta_pct=float(args.final_tick_max_trades_delta_pct),
            final_tick_min_trades_w1=int(args.final_tick_min_trades_w1),
            final_tick_min_trades_mn=int(args.final_tick_min_trades_mn),
        )
        try:
            ohlc_result = score_report_file(
                ohlc_report,
                config=score_config_for_variant(
                    score_config,
                    ohlc_variant,
                    min_trades_w1=args.final_tick_min_trades_w1,
                    min_trades_mn=args.final_tick_min_trades_mn,
                ),
            )
        except Exception as exc:
            print(f"AVISO: no pude parsear OHLC Final Tick candidate #{candidate_id}: {exc}")
            memory.record_candidate_final_tick(
                candidate_id, run_id, "parse_error", None, None,
                ohlc_report, real_tick_report,
                None, None,
                thresholds.final_tick_min_history_quality, thresholds.from_date, thresholds.to_date,
                thresholds.final_tick_max_net_delta_pct, thresholds.final_tick_max_pf_delta_pct,
                thresholds.final_tick_max_dd_delta_pct, thresholds.final_tick_max_trades_delta_pct,
            )
            status_counts["parse_error"] = status_counts.get("parse_error", 0) + 1
            continue
        ohlc_matches, ohlc_mismatch = report_matches_variant(ohlc_variant, ohlc_result, symbol_map)
        if not ohlc_matches:
            print(f"AVISO: reporte OHLC Final Tick no coincide para candidate #{candidate_id}: {ohlc_mismatch}")
            memory.record_candidate_final_tick(
                candidate_id, run_id, "report_mismatch", ohlc_result, None,
                ohlc_report, real_tick_report,
                None, ohlc_result.history_quality,
                thresholds.final_tick_min_history_quality, thresholds.from_date, thresholds.to_date,
                thresholds.final_tick_max_net_delta_pct, thresholds.final_tick_max_pf_delta_pct,
                thresholds.final_tick_max_dd_delta_pct, thresholds.final_tick_max_trades_delta_pct,
            )
            status_counts["report_mismatch"] = status_counts.get("report_mismatch", 0) + 1
            continue
        period_min_ohlc_trades = min_trades_for_period(
            ohlc_variant.target_period,
            int(args.final_tick_min_ohlc_trades),
            int(args.final_tick_min_trades_w1),
            int(args.final_tick_min_trades_mn),
        )
        if ohlc_result.trades < period_min_ohlc_trades:
            payload = final_tick_ohlc_trades_pending_payload(ohlc_result, period_min_ohlc_trades)
            memory.record_candidate_final_tick(
                candidate_id, run_id, "pending_ohlc_trades", ohlc_result, None,
                ohlc_report, real_tick_report,
                json.dumps(payload, ensure_ascii=True, sort_keys=True), None,
                thresholds.final_tick_min_history_quality, thresholds.from_date, thresholds.to_date,
                thresholds.final_tick_max_net_delta_pct, thresholds.final_tick_max_pf_delta_pct,
                thresholds.final_tick_max_dd_delta_pct, thresholds.final_tick_max_trades_delta_pct,
            )
            status_counts["pending_ohlc_trades"] = status_counts.get("pending_ohlc_trades", 0) + 1
            continue
        if real_tick_report is None or not real_tick_report.exists():
            memory.record_candidate_final_tick(
                candidate_id, run_id, "no_report", ohlc_result, None,
                ohlc_report, real_tick_report,
                None, None,
                thresholds.final_tick_min_history_quality, thresholds.from_date, thresholds.to_date,
                thresholds.final_tick_max_net_delta_pct, thresholds.final_tick_max_pf_delta_pct,
                thresholds.final_tick_max_dd_delta_pct, thresholds.final_tick_max_trades_delta_pct,
            )
            status_counts["no_report"] = status_counts.get("no_report", 0) + 1
            continue
        real_tick_variant = Variant(
            path=real_tick_report,
            seed=original_variant.seed,
            target_symbol=original_variant.target_symbol,
            target_period=original_variant.target_period,
            mutated_keys=original_variant.mutated_keys,
            missing_lot_keys=original_variant.missing_lot_keys,
            policy=f"{original_variant.policy}+final_tick_real_rescore",
        )
        _evaluate_final_tick_tick_report(
            memory,
            thresholds,
            score_config,
            symbol_map,
            run_id,
            candidate_id,
            real_tick_variant,
            ohlc_report,
            ohlc_result,
            real_tick_report,
            status_counts,
        )

    total = sum(status_counts.values())
    if status_counts:
        print(
            "Final Tick repuntuado con criterios actuales: "
            + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            + f"; total={total}"
        )
    else:
        print("No hay Final Tick con reportes guardados para repuntuar.")
    if skipped_missing:
        print(f"Final Tick sin OHLC local omitidos: {skipped_missing}")
    print(f"Memoria: {memory.path}")
    return 0


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

    set_path = Path(row["set_path"])
    if not set_path.exists():
        print(f"ERROR: no existe el set del candidato: {set_path}")
        return 1

    run = memory.run_by_id(int(row["run_id"]))
    run_dir = Path(run["output_dir"]) if run else DEFAULT_OUTPUT
    generation = int(row["generation"] or 0)
    retry_dir = recreate_work_dir(run_dir / "retry_mismatch" / f"candidate_{candidate_id}")
    retry_set = retry_dir / set_path.name
    shutil.copy2(set_path, retry_set)

    variant = variant_from_candidate_row(row)
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

    status, result = evaluate_variant(
        memory,
        variant,
        score_config,
        parse_symbol_map(args.symbol_map),
        min_report_mtime=batch_started_at - 1.0,
        min_trades_w1=args.min_trades_w1,
        min_trades_mn=args.min_trades_mn,
    )
    if status == "accepted" and result is not None:
        copied = copy_accepted([(variant, result)], run_dir / f"accepted_gen_{generation:03d}")
        print(f"Retry aceptado; copias accepted: {len(copied)}")
    else:
        print(f"Retry terminado con estado: {status}")
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

        retry_dir = Path(args.output_dir).expanduser() / "seed_retry" / datetime.now().strftime("retry_%Y%m%d_%H%M%S")
        retry_dir.mkdir(parents=True, exist_ok=True)
        copied: list[tuple[Seed, Path]] = []
        used_names: set[str] = set()
        for index, seed in enumerate(seeds, start=1):
            retry_set = retry_dir / seed_eval_filename(index, seed, used_names)
            shutil.copy2(seed.path, retry_set)
            copied.append((seed, retry_set))
        print(f"Retry seeds: {len(copied)}")
        print(f"Directorio retry: {retry_dir}")
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

        statuses: dict[str, int] = {}
        for seed, retry_set in copied:
            report = find_report_for_set(retry_set, min_mtime=batch_started_at - 1.0)
            if not report:
                memory.record_seed_score(seed, None, "no_report", None)
                statuses["no_report"] = statuses.get("no_report", 0) + 1
                continue
            status, _ = evaluate_seed_report(
                memory,
                seed,
                report,
                score_config,
                parse_symbol_map(args.symbol_map),
                label=retry_set.name,
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

    output_root = Path(args.output_dir).expanduser()
    retry_dir = output_root / "seed_retry" / datetime.now().strftime("retry_%Y%m%d_%H%M%S")
    retry_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    retry_set = retry_dir / seed_eval_filename(1, seed, used_names)
    shutil.copy2(source_seed, retry_set)

    print(f"Retry seed: {source_seed}")
    print(f"Set retry: {retry_set}")
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

    report = find_report_for_set(retry_set, min_mtime=batch_started_at - 1.0)
    if not report:
        memory.record_seed_score(seed, None, "no_report", None)
        print("Retry seed terminado sin reporte fresco.")
        return 1
    status, result = evaluate_seed_report(
        memory,
        seed,
        report,
        score_config,
        parse_symbol_map(args.symbol_map),
        label=retry_set.name,
    )
    print(f"Retry seed estado={status}; score={result.score if result else 'n/a'}")
    if status in {"accepted", "rejected", "no_trades", "report_mismatch", "parse_error"}:
        return 0
    return 1


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
    rows = memory.mismatch_candidates_for_generation(run_id, generation)
    rows = [row for row in rows if Path(row["set_path"]).exists()]
    if not rows:
        print(f"ERROR: run #{run_id} gen {generation} no tiene report_mismatch/no_report con .set existente")
        return 1

    run_dir = Path(run["output_dir"])
    retry_dir = recreate_work_dir(run_dir / "retry_mismatch" / f"run_{run_id}_gen_{generation:03d}")
    variants = [variant_from_candidate_row(row) for row in rows]

    print(f"Retry report_mismatch/no_report run #{run_id} gen {generation}: {len(rows)} candidato(s)")
    seen_names: set[str] = set()
    for row in rows:
        set_path = Path(row["set_path"])
        if set_path.name in seen_names:
            print(f"ERROR: nombre de set duplicado en retry: {set_path.name}")
            return 1
        seen_names.add(set_path.name)
        shutil.copy2(set_path, retry_dir / set_path.name)
        if not args.dry_run:
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

    accepted: list[tuple[Variant, ScoreResult]] = []
    status_counts: dict[str, int] = {}
    symbol_map = parse_symbol_map(args.symbol_map)
    for variant in variants:
        status, result = evaluate_variant(
            memory,
            variant,
            score_config,
            symbol_map,
            min_report_mtime=batch_started_at - 1.0,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "accepted" and result is not None:
            accepted.append((variant, result))

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
    rows = memory.mismatch_candidates_for_run(run_id)
    rows = [row for row in rows if Path(row["set_path"]).exists()]
    if not rows:
        print(f"ERROR: run #{run_id} no tiene report_mismatch/no_report con .set existente")
        return 1

    run_dir = Path(run["output_dir"])
    retry_dir = recreate_work_dir(run_dir / "retry_mismatch" / f"run_{run_id}_all")
    variants = [variant_from_candidate_row(row) for row in rows]

    print(f"Retry report_mismatch/no_report run #{run_id}: {len(rows)} candidato(s)")
    seen_names: set[str] = set()
    for row in rows:
        set_path = Path(row["set_path"])
        if set_path.name in seen_names:
            print(f"ERROR: nombre de set duplicado en retry: {set_path.name}")
            return 1
        seen_names.add(set_path.name)
        shutil.copy2(set_path, retry_dir / set_path.name)
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
            min_report_mtime=batch_started_at - 1.0,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "accepted" and result is not None:
            generation = int(row["generation"] or 0)
            accepted_by_generation.setdefault(generation, []).append((variant, result))

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
    rows = [row for row in memory.candidates_for_run(run_id) if Path(row["set_path"]).exists()]
    requested_ids = {int(value) for value in (args.retry_candidate_id or [])}
    if requested_ids:
        rows = [row for row in rows if int(row["id"]) in requested_ids]
        found_ids = {int(row["id"]) for row in rows}
        missing_ids = sorted(requested_ids - found_ids)
        if missing_ids:
            print(
                "ERROR: candidatos marcados no pertenecen al run o no tienen .set existente: "
                + ", ".join(str(candidate_id) for candidate_id in missing_ids)
            )
            return 1
    if not rows:
        print(f"ERROR: run #{run_id} no tiene candidatos con .set existente")
        return 1

    run_dir = Path(run["output_dir"])
    suffix = "selected" if requested_ids else "all"
    retry_dir = recreate_work_dir(run_dir / "retry_full" / f"run_{run_id}_{suffix}")
    variants = [variant_from_candidate_row(row) for row in rows]

    print(f"Reprobar run completo #{run_id}: {len(rows)} candidato(s)")
    if requested_ids:
        print("Modo seleccionado: " + ", ".join(str(row["id"]) for row in rows))
    seen_names: set[str] = set()
    for row in rows:
        set_path = Path(row["set_path"])
        if set_path.name in seen_names:
            print(f"ERROR: nombre de set duplicado en reprobar run: {set_path.name}")
            return 1
        seen_names.add(set_path.name)
        shutil.copy2(set_path, retry_dir / set_path.name)
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
            min_report_mtime=batch_started_at - 1.0,
            min_trades_w1=args.min_trades_w1,
            min_trades_mn=args.min_trades_mn,
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "accepted" and result is not None:
            generation = int(row["generation"] or 0)
            accepted_by_generation.setdefault(generation, []).append((variant, result))

    copied = 0
    for generation, accepted in accepted_by_generation.items():
        copied += len(copy_accepted(accepted, run_dir / f"accepted_gen_{generation:03d}"))
    print(
        "Reprobar run completo terminado: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        + f"; accepted/copied={copied}"
    )
    return 0


def evaluate_generation(
    args: argparse.Namespace,
    memory: AgentMemory,
    run_dir: Path,
    generation: int,
    variants: list[Variant],
    score_config: ScoreConfig,
) -> list[tuple[Variant, ScoreResult]]:
    scored: list[tuple[Variant, ScoreResult]] = []
    if not (args.execute_backtests or args.dry_run):
        return scored
    generation_dir = run_dir / f"gen_{generation:03d}"
    batch_started_at = time.time()
    code = run_backtests(args, generation_dir)
    if code == RUNNING_TERMINAL_EXIT_CODE:
        raise RuntimeError("run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta")
    partial_failure = code != 0
    if code != 0:
        print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
        if args.dry_run:
            raise RuntimeError(f"run_tests.py termino con codigo {code}")
    if args.dry_run:
        return scored
    scored = evaluate_variants(
        memory,
        variants,
        score_config,
        parse_symbol_map(args.symbol_map),
        min_report_mtime=batch_started_at - 1.0,
        min_trades_w1=args.min_trades_w1,
        min_trades_mn=args.min_trades_mn,
    )
    survivors = select_survivors(scored, args.top_percent)
    copied = copy_accepted(survivors, run_dir / f"accepted_gen_{generation:03d}")
    print(f"Reportes puntuados gen {generation}: {len(scored)}; accepted/copied: {len(copied)}")
    if partial_failure and not scored:
        raise RuntimeError(f"run_tests.py termino con codigo {code} y no produjo reportes puntuables")
    return scored


def json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def build_run_config(
    args: argparse.Namespace,
    score_config: ScoreConfig,
    *,
    source_dir: Path,
    output_root: Path,
    run_dir: Path,
    universe_symbol_count: int,
    universe_alias_count: int,
    disabled_symbol_count: int,
    seed_enabled_disabled_symbol_count: int,
) -> dict[str, object]:
    timeframe_universe = target_timeframe_universe(
        bool(args.experimental_long_timeframes),
        base_dir=BASE_DIR,
        broker=args.broker,
        account_type=args.account_type,
    )
    return {
        "schema_version": 2,
        "created_by": "ubs_agent.py",
        "broker": normalize_broker(args.broker),
        "account_type": normalize_account_type(args.account_type, args.broker),
        "paths": {
            "source_dir": str(source_dir),
            "output_root": str(output_root),
            "run_dir": str(run_dir),
            "memory": str(Path(args.memory).expanduser()),
            "template": str(Path(args.template).expanduser()),
            "assets": str(Path(args.assets).expanduser()),
        },
        "generation": {
            "mode": str(args.generation_mode),
            "generations": int(args.generations),
            "variants_per_seed": int(args.variants_per_seed),
            "max_seeds": int(args.max_seeds),
            "mutations_per_variant": int(args.mutations_per_variant),
            "top_percent": float(args.top_percent),
            "force_unseeded_universe": bool(args.force_unseeded_universe),
            "experimental_long_timeframes": bool(args.experimental_long_timeframes),
            "timeframe_universe": list(timeframe_universe),
            "long_timeframe_min_trades": {
                "W1": int(args.min_trades_w1),
                "MN": int(args.min_trades_mn),
            },
            "asset_unseeded_force_probability": {
                "generation_1": ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[1],
                "generation_2": ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION[2],
                "late": ASSET_UNSEEDED_FORCE_PROB_LATE,
            },
            "timeframe_unseeded_force_probability": {
                "generation_1": TF_UNSEEDED_FORCE_PROB_BY_GENERATION[1],
                "generation_2": TF_UNSEEDED_FORCE_PROB_BY_GENERATION[2],
                "late": TF_UNSEEDED_FORCE_PROB_LATE,
            },
            "force_unseeded_timeframe_min_ratios": FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS,
            "target_diversity_caps": {
                "default_group_ratio": DEFAULT_TARGET_GROUP_CAP_RATIO,
                "group_ratios": TARGET_GROUP_CAP_RATIOS,
                "symbol_ratio": TARGET_SYMBOL_CAP_RATIO,
                "timeframe_ratio": TARGET_TIMEFRAME_CAP_RATIO,
                "symbol_timeframe_ratio": TARGET_PAIR_CAP_RATIO,
                "reroll_attempts": DIVERSITY_REROLL_ATTEMPTS,
            },
            "seed_selection_diversity_caps": {
                "default_group_ratio": DEFAULT_TARGET_GROUP_CAP_RATIO,
                "group_ratios": TARGET_GROUP_CAP_RATIOS,
                "symbol_ratio": TARGET_SYMBOL_CAP_RATIO,
                "timeframe_ratio": TARGET_TIMEFRAME_CAP_RATIO,
                "symbol_timeframe_ratio": TARGET_PAIR_CAP_RATIO,
            },
            "next_seed_diversity_caps": {
                "default_group_ratio": DEFAULT_TARGET_GROUP_CAP_RATIO,
                "group_ratios": TARGET_GROUP_CAP_RATIOS,
                "symbol_ratio": TARGET_SYMBOL_CAP_RATIO,
                "timeframe_ratio": TARGET_TIMEFRAME_CAP_RATIO,
                "symbol_timeframe_ratio": TARGET_PAIR_CAP_RATIO,
            },
            "selection_fitness": {
                "model": "regularized_logistic_final_tick_6m_v1",
                "target": "final_tick_6m_accepted",
                "exclude_current_run": True,
                "mode": SELECTION_FITNESS_MODE,
                "applied_weight_scale": SELECTION_FITNESS_APPLIED_SCALE,
            },
            "feedback_model": {
                "model": "smoothed_stage_probability_v1",
                "stages": ["base", "robust", "probe", "six_month"],
                "mutation_sampling": "percentile_multiplier_0.5_1.5",
            },
        },
        "execution": {
            "execute_backtests": bool(args.execute_backtests),
            "dry_run": bool(args.dry_run),
            "multi_terminal": bool(args.multi_terminal),
            "max_workers": int(args.max_workers),
            "delay": int(args.delay),
            "random_seed": args.random_seed,
            "from_date": str(args.from_date or ""),
            "to_date": str(args.to_date or ""),
            "symbol_map": str(args.symbol_map or ""),
        },
        "universe": {
            "symbols": int(universe_symbol_count),
            "aliases": int(universe_alias_count),
            "disabled_symbols": int(disabled_symbol_count),
            "seed_enabled_disabled_symbols": int(seed_enabled_disabled_symbol_count),
        },
        "score": score_config.to_dict(),
        "robustness_defaults": {
            "positive_bonus": float(args.robust_positive_bonus),
            "negative_bonus": float(args.robust_negative_bonus),
            "robust_run_id": args.robust_run_id,
        },
        "final_tick_defaults": {
            "min_history_quality": float(args.final_tick_min_history_quality),
            "min_ohlc_trades": int(args.final_tick_min_ohlc_trades),
            "min_trades_w1": int(args.final_tick_min_trades_w1),
            "min_trades_mn": int(args.final_tick_min_trades_mn),
            "max_net_delta_pct": float(args.final_tick_max_net_delta_pct),
            "max_pf_delta_pct": float(args.final_tick_max_pf_delta_pct),
            "max_dd_delta_pct": float(args.final_tick_max_dd_delta_pct),
            "max_trades_delta_pct": float(args.final_tick_max_trades_delta_pct),
        },
        "args": {key: json_safe(value) for key, value in sorted(vars(args).items())},
    }


def resume_last_run(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    run = memory.latest_run()
    if run is None:
        print("ERROR: no hay runs guardados para continuar")
        return 1
    run_id = int(run["id"])
    run_dir = Path(run["output_dir"])
    planned_generations = int(run["generations"])
    args.variants_per_seed = int(run["variants_per_seed"])
    args.max_seeds = int(run["max_seeds"])
    args.execute_backtests = bool(run["execute_backtests"])
    args.dry_run = bool(run["dry_run"]) or args.dry_run
    if args.execute_backtests and not args.expert and not args.multi_terminal:
        print("ERROR: el run pendiente requiere backtests; indica --expert o activa --multi-terminal para continuar")
        return 1

    max_generation = memory.max_generation(run_id)
    pending_generation = memory.pending_generated_generation(run_id) if args.execute_backtests else 0
    current_seeds: list[Seed] = []
    did_work = False
    disabled_policy_path = disabled_symbols_file_for_account(args.account_type, args.broker)
    disabled_symbols = load_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled = load_seed_enabled_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled &= disabled_symbols
    symbol_map = parse_symbol_map(args.symbol_map)
    asset_groups, aliases = load_asset_universe(
        Path(args.assets).expanduser(),
        disabled_symbols=disabled_symbols,
    )
    group_by_symbol = asset_group_map(asset_groups, aliases)
    universe_symbols = tuple(symbol for symbols in asset_groups.values() for symbol in symbols)
    if not universe_symbols:
        print("ERROR: no hay simbolos activos en Universo para generar targets")
        return 1
    print(f"Universo {args.broker} cargado: {len(universe_symbols)} simbolos, {len(aliases)} aliases")

    print(f"Continuando run #{run_id}: plan={planned_generations}, ultima_gen={max_generation}")

    if pending_generation:
        variants = memory.variants_for_generation(run_id, pending_generation, status="generated")
        if not variants:
            print(f"ERROR: gen {pending_generation} marcada pendiente, pero no hay .set disponibles")
            return 1
        print(f"Reanudando gen {pending_generation}: backtests pendientes={len(variants)}")
        try:
            scored = evaluate_generation(args, memory, run_dir, pending_generation, variants, score_config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        did_work = True
        if args.execute_backtests and not args.dry_run and not scored:
            print(f"ERROR: gen {pending_generation} no produjo reportes puntuables; no se genera la siguiente generacion")
            return 1
        if scored:
            current_seeds = seeds_from_survivors(
                select_next_generation_survivors(
                    memory, run_id, scored, args.top_percent, args.max_seeds, aliases, group_by_symbol
                )
            )
        else:
            current_seeds = seeds_from_variants(variants)
        if args.backtest_pending_only:
            print(f"Backtests pendientes completados en gen {pending_generation}; no se generan nuevas generaciones.")
            print(f"Run dir: {run_dir}")
            print(f"Memoria: {memory.path}")
            return 0
        next_generation = pending_generation + 1
    else:
        if args.backtest_pending_only:
            print(f"Run #{run_id} no tiene candidatos generated pendientes para backtest.")
            return 0
        if max_generation >= planned_generations:
            print(f"Run #{run_id} ya esta completo: {max_generation}/{planned_generations}")
            return 0
        seed_limit = args.max_seeds if args.max_seeds > 0 else 0
        _, latest_generation, current_seeds = memory.continuation_seeds(seed_limit)
        next_generation = latest_generation + 1

    current_seeds, blocked_source_count = generation_source_seeds(
        current_seeds,
        symbol_map,
        disabled_symbols,
        seed_enabled_when_disabled,
    )
    if blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {blocked_source_count}")
    if not current_seeds:
        print("ERROR: no hay seeds disponibles para generar con la politica actual del Universo")
        return 1

    all_generated = 0
    rng = random.Random(args.random_seed)
    timeframe_universe = target_timeframe_universe(
        bool(args.experimental_long_timeframes),
        base_dir=BASE_DIR,
        broker=args.broker,
        account_type=args.account_type,
    )
    print(f"Universo TF target: {', '.join(timeframe_universe)}")
    for generation in range(next_generation, planned_generations + 1):
        generation_dir = run_dir / f"gen_{generation:03d}"
        mutation_feedback = memory.mutation_feedback()
        mutation_direction_feedback = memory.mutation_direction_feedback()
        asset_feedback = memory.asset_feedback(aliases)
        timeframe_feedback = memory.timeframe_feedback()
        fitness_predictions = memory.seed_selection_predictions(current_seeds, exclude_run_id=run_id)
        fitness_feedback = {path: prediction.weight for path, prediction in fitness_predictions.items()}
        selected_seed_rankings = ranked_seed_selection(
            current_seeds,
            args.max_seeds,
            asset_feedback,
            timeframe_feedback,
            rng,
            aliases,
            group_by_symbol,
            fitness_feedback,
        )
        selected_seeds = [seed for _, seed, _, _, _ in selected_seed_rankings]
        memory.record_seed_selection(run_id, generation, selected_seed_rankings, fitness_predictions)
        unseeded_symbols, unseeded_timeframes = unseeded_universe_targets(
            current_seeds,
            universe_symbols,
            aliases,
            timeframe_universe,
        )
        asset_unseeded_probability = unseeded_asset_force_probability(generation, len(unseeded_symbols))
        tf_unseeded_probability = unseeded_timeframe_force_probability(generation, len(unseeded_timeframes))
        target_limiter = TargetDiversityLimiter(
            len(selected_seeds) * args.variants_per_seed,
            aliases,
            group_by_symbol=group_by_symbol,
        )
        variants: list[Variant] = []
        reserved_timeframes = (
            reserved_timeframe_plan(selected_seeds, timeframe_universe, len(selected_seeds) * args.variants_per_seed)
            if args.force_unseeded_universe
            else []
        )
        print(f"Generacion {generation}: seeds={len(selected_seeds)}")
        print(
            "Caps diversidad target: "
            f"grupos[{format_group_cap_summary(target_limiter)}], "
            f"simbolo<={target_limiter.symbol_cap}, TF<={target_limiter.timeframe_cap}, "
            f"simbolo+TF<={target_limiter.pair_cap}"
        )
        if args.force_unseeded_universe:
            print(
                f"Exploracion forzada sin seed: activos={len(unseeded_symbols)}, "
                f"TF={len(unseeded_timeframes)} | "
                f"prob_activo={asset_unseeded_probability:.0%}, prob_TF={tf_unseeded_probability:.0%}"
            )
            if reserved_timeframes:
                print(f"Cuota/reserva TF exploratoria: {timeframe_plan_summary(reserved_timeframes)}")
        for seed_index, seed in enumerate(selected_seeds, start=1):
            for variant_index in range(1, args.variants_per_seed + 1):
                target_symbol, target_period, policy = choose_diverse_target(
                    seed,
                    asset_feedback,
                    timeframe_feedback,
                    rng,
                    target_limiter,
                    universe_symbols,
                    aliases,
                    timeframe_universe=timeframe_universe,
                    symbol_map=symbol_map,
                    disabled_symbols=disabled_symbols,
                    force_unseeded_universe=args.force_unseeded_universe,
                    unseeded_universe_symbols=unseeded_symbols,
                    unseeded_timeframes=unseeded_timeframes,
                    asset_unseeded_probability=asset_unseeded_probability,
                    timeframe_unseeded_probability=tf_unseeded_probability,
                )
                target_symbol, target_period, policy = apply_reserved_timeframe(
                    reserved_timeframes=reserved_timeframes,
                    target_symbol=target_symbol,
                    target_period=target_period,
                    policy=policy,
                    seed=seed,
                    target_limiter=target_limiter,
                    universe_symbols=universe_symbols,
                    disabled_symbols=disabled_symbols,
                    aliases=aliases,
                    rng=rng,
                )
                variant = create_variant(
                    seed,
                    target_symbol,
                    target_period,
                    generation_dir,
                    generation,
                    seed_index,
                    variant_index,
                    args.mutations_per_variant,
                    mutation_feedback,
                    mutation_direction_feedback,
                    policy,
                    rng,
                )
                memory.record_variant(run_id, generation, variant)
                target_limiter.record(target_symbol, target_period)
                variants.append(variant)
        all_generated += len(variants)
        did_work = True
        print(f"Generados: {len(variants)} en {generation_dir}")
        try:
            scored = evaluate_generation(args, memory, run_dir, generation, variants, score_config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        if args.execute_backtests and not args.dry_run and not scored:
            print(f"ERROR: gen {generation} no produjo reportes puntuables; se detiene la continuacion")
            return 1
        if scored:
            current_seeds = seeds_from_survivors(
                select_next_generation_survivors(
                    memory, run_id, scored, args.top_percent, args.max_seeds, aliases, group_by_symbol
                )
            )
        else:
            current_seeds = seeds_from_variants(variants)

    print(f"Run dir: {run_dir}")
    print(f"Memoria: {memory.path}")
    print(f"Sets nuevos generados: {all_generated}")
    return 0 if did_work else 1


def run_agent(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser()
    output_root = Path(args.output_dir).expanduser()
    run_dir = output_root / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    memory = AgentMemory(Path(args.memory).expanduser())
    rng = random.Random(args.random_seed)
    score_config = ScoreConfig(
        min_net_profit=args.min_net_profit,
        min_profit_factor=args.min_profit_factor,
        min_trades=args.min_trades,
        max_drawdown_pct=args.max_drawdown_pct,
        min_recovery_factor=args.min_recovery_factor,
        min_positive_month_ratio=args.min_positive_month_ratio,
    )

    if args.evaluate_seeds:
        try:
            return evaluate_seed_scores(args, memory, score_config)
        finally:
            memory.close()
    if args.evaluate_robustness:
        try:
            return evaluate_candidate_robustness(args, memory, score_config)
        finally:
            memory.close()
    if args.evaluate_final_tick:
        try:
            return evaluate_candidate_final_tick(args, memory, score_config)
        finally:
            memory.close()
    if args.rescore_seeds_only:
        try:
            return rescore_seed_scores_only(args, memory, score_config)
        finally:
            memory.close()
    if args.rescore_candidates_only:
        try:
            return rescore_candidate_scores_only(args, memory, score_config)
        finally:
            memory.close()
    if args.rescore_robustness_only:
        try:
            return rescore_robustness_only(args, memory, score_config)
        finally:
            memory.close()
    if args.rescore_final_tick_only:
        try:
            return rescore_final_tick_only(args, memory, score_config)
        finally:
            memory.close()
    if args.continue_last_run:
        try:
            return resume_last_run(args, memory, score_config)
        finally:
            memory.close()
    if args.retry_candidate_id:
        try:
            return retry_candidate(args, memory, score_config)
        finally:
            memory.close()
    if args.retry_seed_path:
        try:
            return retry_seed(args, memory, score_config)
        finally:
            memory.close()
    if args.retry_full_run:
        try:
            return retry_full_run(args, memory, score_config)
        finally:
            memory.close()
    if args.retry_mismatch_run:
        try:
            return retry_run_mismatches(args, memory, score_config)
        finally:
            memory.close()
    if args.retry_mismatch_generation:
        try:
            return retry_generation_mismatches(args, memory, score_config)
        finally:
            memory.close()

    seed_source = str(source_dir)
    seeds = memory.apply_seed_overrides(load_seeds(source_dir, base_dir=BASE_DIR))
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    disabled_policy_path = disabled_symbols_file_for_account(args.account_type, args.broker)
    disabled_symbols = load_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled = load_seed_enabled_disabled_symbols(disabled_policy_path)
    seed_enabled_when_disabled &= disabled_symbols
    symbol_map = parse_symbol_map(args.symbol_map)
    source_seeds, blocked_source_count = generation_source_seeds(
        seeds,
        symbol_map,
        disabled_symbols,
        seed_enabled_when_disabled,
    )
    if not source_seeds:
        print("ERROR: no hay seeds disponibles como fuente con la politica actual del Universo")
        return 1
    asset_groups, aliases = load_asset_universe(
        Path(args.assets).expanduser(),
        disabled_symbols=disabled_symbols,
    )
    universe_symbols = tuple(symbol for symbols in asset_groups.values() for symbol in symbols)
    if not universe_symbols:
        print("ERROR: no hay simbolos activos en Universo para generar targets")
        return 1
    group_by_symbol = asset_group_map(asset_groups, aliases)
    timeframe_universe = target_timeframe_universe(
        bool(args.experimental_long_timeframes),
        base_dir=BASE_DIR,
        broker=args.broker,
        account_type=args.account_type,
    )
    print(f"Seeds disponibles: {len(seeds)} ({seed_source})")
    if blocked_source_count:
        print(f"Seeds bloqueadas como fuente por GEN=no y SEEDS=no: {blocked_source_count}")
    if seed_enabled_when_disabled:
        print(f"Symbols deshabilitados con SEEDS activo: {len(seed_enabled_when_disabled)}")
    print(f"Universo {args.broker} cargado: {len(universe_symbols)} simbolos, {len(aliases)} aliases")
    print(f"Universo TF target: {', '.join(timeframe_universe)}")
    monthly_pass = (
        f"meses+>={score_config.min_positive_month_ratio}"
        if score_config.min_positive_month_ratio > 0
        else "estabilidad mensual solo score"
    )
    print(
        "Pass config: "
        f"net>{score_config.min_net_profit}, "
        f"pf>={score_config.min_profit_factor}, "
        f"trades>={score_config.min_trades}, "
        f"dd%<={score_config.max_drawdown_pct}, "
        f"recovery>={score_config.min_recovery_factor}, "
        f"{monthly_pass}"
    )
    run_id = memory.create_run(
        source_dir,
        run_dir,
        args.generations,
        args.variants_per_seed,
        args.max_seeds,
        args.execute_backtests,
        args.dry_run,
        config=build_run_config(
            args,
            score_config,
            source_dir=source_dir,
            output_root=output_root,
            run_dir=run_dir,
            universe_symbol_count=len(universe_symbols),
            universe_alias_count=len(aliases),
            disabled_symbol_count=len(disabled_symbols),
            seed_enabled_disabled_symbol_count=len(seed_enabled_when_disabled),
        ),
    )

    current_seeds = source_seeds
    all_generated = 0
    try:
        for generation in range(1, args.generations + 1):
            generation_dir = run_dir / f"gen_{generation:03d}"
            accepted_dir = run_dir / f"accepted_gen_{generation:03d}"
            mutation_feedback = memory.mutation_feedback()
            mutation_direction_feedback = memory.mutation_direction_feedback()
            asset_feedback = memory.asset_feedback(aliases)
            timeframe_feedback = memory.timeframe_feedback()
            fitness_predictions = memory.seed_selection_predictions(current_seeds, exclude_run_id=run_id)
            fitness_feedback = {path: prediction.weight for path, prediction in fitness_predictions.items()}
            selected_seed_rankings = ranked_seed_selection(
                current_seeds,
                args.max_seeds,
                asset_feedback,
                timeframe_feedback,
                rng,
                aliases,
                group_by_symbol,
                fitness_feedback,
            )
            selected_seeds = [seed for _, seed, _, _, _ in selected_seed_rankings]
            memory.record_seed_selection(run_id, generation, selected_seed_rankings, fitness_predictions)
            unseeded_symbols, unseeded_timeframes = unseeded_universe_targets(
                current_seeds,
                universe_symbols,
                aliases,
                timeframe_universe,
            )
            asset_unseeded_probability = unseeded_asset_force_probability(generation, len(unseeded_symbols))
            tf_unseeded_probability = unseeded_timeframe_force_probability(generation, len(unseeded_timeframes))
            target_limiter = TargetDiversityLimiter(
                len(selected_seeds) * args.variants_per_seed,
                aliases,
                group_by_symbol=group_by_symbol,
            )
            variants: list[Variant] = []
            reserved_timeframes = (
                reserved_timeframe_plan(selected_seeds, timeframe_universe, len(selected_seeds) * args.variants_per_seed)
                if args.force_unseeded_universe
                else []
            )
            print(f"Generacion {generation}: seeds={len(selected_seeds)}")
            print(
                "Caps diversidad target: "
                f"grupos[{format_group_cap_summary(target_limiter)}], "
                f"simbolo<={target_limiter.symbol_cap}, TF<={target_limiter.timeframe_cap}, "
                f"simbolo+TF<={target_limiter.pair_cap}"
            )
            if args.force_unseeded_universe:
                print(
                    f"Exploracion forzada sin seed: activos={len(unseeded_symbols)}, "
                    f"TF={len(unseeded_timeframes)} | "
                    f"prob_activo={asset_unseeded_probability:.0%}, prob_TF={tf_unseeded_probability:.0%}"
                )
                if reserved_timeframes:
                    print(f"Cuota/reserva TF exploratoria: {timeframe_plan_summary(reserved_timeframes)}")
            for seed_index, seed in enumerate(selected_seeds, start=1):
                for variant_index in range(1, args.variants_per_seed + 1):
                    target_symbol, target_period, policy = choose_diverse_target(
                        seed,
                        asset_feedback,
                        timeframe_feedback,
                        rng,
                        target_limiter,
                        universe_symbols,
                        aliases,
                        timeframe_universe=timeframe_universe,
                        symbol_map=symbol_map,
                        disabled_symbols=disabled_symbols,
                        force_unseeded_universe=args.force_unseeded_universe,
                        unseeded_universe_symbols=unseeded_symbols,
                        unseeded_timeframes=unseeded_timeframes,
                        asset_unseeded_probability=asset_unseeded_probability,
                        timeframe_unseeded_probability=tf_unseeded_probability,
                    )
                    target_symbol, target_period, policy = apply_reserved_timeframe(
                        reserved_timeframes=reserved_timeframes,
                        target_symbol=target_symbol,
                        target_period=target_period,
                        policy=policy,
                        seed=seed,
                        target_limiter=target_limiter,
                        universe_symbols=universe_symbols,
                        disabled_symbols=disabled_symbols,
                        aliases=aliases,
                        rng=rng,
                    )
                    variant = create_variant(
                        seed,
                        target_symbol,
                        target_period,
                        generation_dir,
                        generation,
                        seed_index,
                        variant_index,
                        args.mutations_per_variant,
                        mutation_feedback,
                        mutation_direction_feedback,
                        policy,
                        rng,
                    )
                    memory.record_variant(run_id, generation, variant)
                    target_limiter.record(target_symbol, target_period)
                    variants.append(variant)
            all_generated += len(variants)
            print(f"Generados: {len(variants)} en {generation_dir}")

            scored: list[tuple[Variant, ScoreResult]] = []
            if args.execute_backtests or args.dry_run:
                batch_started_at = time.time()
                code = run_backtests(args, generation_dir)
                if code == RUNNING_TERMINAL_EXIT_CODE:
                    print("ERROR: run_tests.py no ejecuto backtests porque hay una terminal MT5 abierta. No se actualiza memoria.")
                    return code
                partial_failure = code != 0
                if code != 0:
                    print(f"AVISO: run_tests.py termino con codigo {code}; se evaluaran los reportes disponibles")
                    if args.dry_run:
                        return code
                if not args.dry_run:
                    scored = evaluate_variants(
                        memory,
                        variants,
                        score_config,
                        parse_symbol_map(args.symbol_map),
                        min_report_mtime=batch_started_at - 1.0,
                        min_trades_w1=args.min_trades_w1,
                        min_trades_mn=args.min_trades_mn,
                    )
                    survivors = select_survivors(scored, args.top_percent)
                    copied = copy_accepted(survivors, accepted_dir)
                    print(f"Reportes puntuados: {len(scored)}; accepted/copied: {len(copied)}")
                    if partial_failure and not scored:
                        print(f"ERROR: run_tests.py termino con codigo {code} y no produjo reportes puntuables")
                        return code

            if scored:
                current_seeds = seeds_from_survivors(
                    select_next_generation_survivors(
                        memory, run_id, scored, args.top_percent, args.max_seeds, aliases, group_by_symbol
                    )
                )
            else:
                current_seeds = [variant_as_next_seed(variant) for variant in variants]

        print(f"Run dir: {run_dir}")
        print(f"Memoria: {memory.path}")
        print(f"Sets generados: {all_generated}")
        return 0 if all_generated else 1
    finally:
        memory.close()


def main() -> int:
    args = parse_args()
    if args.generations <= 0 or args.variants_per_seed <= 0:
        print("ERROR: generations y variants-per-seed deben ser mayores que 0")
        return 1
    if args.min_trades < 0:
        print("ERROR: --min-trades no puede ser negativo")
        return 1
    if args.min_trades_w1 < 0 or args.min_trades_mn < 0:
        print("ERROR: --min-trades-w1 y --min-trades-mn no pueden ser negativos")
        return 1
    if args.final_tick_min_ohlc_trades < 0 or args.final_tick_min_trades_w1 < 0 or args.final_tick_min_trades_mn < 0:
        print("ERROR: minimos de operaciones Final Tick no pueden ser negativos")
        return 1
    if args.min_profit_factor < 0 or args.max_drawdown_pct < 0:
        print("ERROR: profit factor y drawdown deben ser mayores o iguales a 0")
        return 1
    if not 0 <= args.min_positive_month_ratio <= 1:
        print("ERROR: --min-positive-month-ratio debe estar entre 0 y 1")
        return 1
    return run_agent(args)


if __name__ == "__main__":
    raise SystemExit(main())
