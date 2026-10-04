"""Rutas, constantes y overrides de mutacion del agente UBS."""
from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from run_tests import TIMEFRAME_ENUM, load_symbol_suffix_target_map, parse_symbol_map
from ubs.account import (
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_memory_path,
    default_symbol_map_for_broker,
)
from ubs.selection import DISCOVERY_SOURCE_MIX_FLOOR


BASE_DIR = Path(__file__).resolve().parent
MUTATION_OVERRIDES_FILE = BASE_DIR / "outputs" / "ubs_mutation_overrides.json"
GLOBAL_PARAMS_FILE = BASE_DIR / "outputs" / "ubs_global_params.json"
DEFAULT_SOURCE = BASE_DIR / "sets" / "ubs_ready"
DEFAULT_OUTPUT = BASE_DIR / "outputs" / "ubs_agent"
DEFAULT_MEMORY = account_memory_path(BASE_DIR, DEFAULT_ACCOUNT_TYPE, DEFAULT_BROKER)
DEFAULT_TEMPLATE = BASE_DIR / "tester_template.ini"
DEFAULT_ASSETS = BASE_DIR / "assets" / "roboforex_assets.ini"
DEFAULT_SYMBOL_MAP = default_symbol_map_for_broker(DEFAULT_BROKER)
DIAG_LOG_FILE = BASE_DIR / "logs" / "ubs_agent_diag.log"
FINAL_TICK_6M_MIN_DAYS = 180
TIMEFRAME_TO_ENUM = {period: value for value, period in TIMEFRAME_ENUM.items()}
LEGACY_TIMEFRAME_TO_ENUM = {
    "60": TIMEFRAME_TO_ENUM["H1"],
    "120": TIMEFRAME_TO_ENUM["H2"],
    "180": TIMEFRAME_TO_ENUM["H3"],
    "240": TIMEFRAME_TO_ENUM["H4"],
    "1440": TIMEFRAME_TO_ENUM["D1"],
    "10080": TIMEFRAME_TO_ENUM["W1"],
    "43200": TIMEFRAME_TO_ENUM["MN"],
}
BASE_TIMEFRAME_UNIVERSE = ("M1", "M5", "M15", "M30", "H1", "H2", "H3", "H4", "D1")


def diag_log(message: str) -> None:
    """Append a lightweight diagnostic line without changing run behaviour."""
    try:
        DIAG_LOG_FILE.parent.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with DIAG_LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] pid={os.getpid()} {message}\n")
    except OSError:
        pass


def diag_args_summary(args: argparse.Namespace) -> str:
    return (
        f"broker={getattr(args, 'broker', '')} account={getattr(args, 'account_type', '')} "
        f"generations={getattr(args, 'generations', '')} variants_per_seed={getattr(args, 'variants_per_seed', '')} "
        f"max_seeds={getattr(args, 'max_seeds', '')} execute_backtests={bool(getattr(args, 'execute_backtests', False))} "
        f"multi_terminal={bool(getattr(args, 'multi_terminal', False))} max_workers={getattr(args, 'max_workers', '')}"
    )
EXPERIMENTAL_LONG_TIMEFRAMES = ("W1", "MN")
TIMEFRAME_UNIVERSE = BASE_TIMEFRAME_UNIVERSE
GENERATION_MODES = ("production", "discovery")
SELECTION_FITNESS_MODE = "soft_weight"
SELECTION_FITNESS_APPLIED_SCALE = 0.15
ASSET_UNSEEDED_FORCE_PROB_BY_GENERATION = {1: 0.12, 2: 0.08}
ASSET_UNSEEDED_FORCE_PROB_LATE = 0.05
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
PRODUCTION_TARGET_SYMBOL_CAP_RATIO = 0.30
PRODUCTION_SEED_SYMBOL_CAP_RATIO = 0.25
PRODUCTION_CURRENT_SYMBOL_PROBABILITY = 0.85
PRODUCTION_NEXT_SEED_BACKFILL_MIN_RATIO = 0.60
PRODUCTION_DIVERSITY_REROLL_ATTEMPTS = 4
DISCOVERY_SEED_SYMBOL_RESERVE_RATIO = 0.40
DISCOVERY_EXPLOITABLE_SEED_MIN_RATIO = DISCOVERY_SOURCE_MIX_FLOOR
DISCOVERY_TARGET_SYMBOL_CAP_RATIO = 0.10
DEFAULT_TARGET_GROUP_CAP_RATIO = 0.40
TARGET_GROUP_CAP_RATIOS = {
    "Forex": 0.60,
    "Stocks": 0.60,
    "Metals": 0.40,
    "Indices": 0.35,
    "Energies": 0.25,
    "IndicesEnergies": 0.35,
    "Crypto": 0.25,
}
DIVERSITY_REROLL_ATTEMPTS = 24
DISCOVERY_GROUP_FEEDBACK_TEMPERATURE = 12.0
DISCOVERY_GROUP_FEEDBACK_EXP_LIMIT = 2.0
RANDOM_STREAM_VERSION = "generation-selection-mutation-v1"
# Cuando el Symbol no existe en el servidor del broker, MT5 no llega a abrir el
# Strategy Tester: cierra el terminal sin reporte ("shutdown with -1000012358").
# Sin un estado propio el candidato queda como no_report, que SI es retryable, y
# cada reparacion vuelve a encolarlo aunque reintentar no pueda cambiar nada.
# Este estado es terminal a proposito: no aparece en ningun set de retry.
SYMBOL_NOT_EXIST_STATUS = "symbol_not_exist"

FINAL_TICK_RETRYABLE_STATUSES = {
    "pending",
    "no_report",
    "parse_error",
    "report_mismatch",
}
FINAL_TICK_DATE_RETRYABLE_STATUSES = {
    "pending_history_quality",
}


def generation_random_stream(
    random_seed: int | None,
    generation: int,
    stream: str,
    *coordinates: object,
) -> random.Random:
    """Return a deterministic RNG isolated from every other generation stream.

    A mutation implementation may consume a different number of random values
    after a code change.  Keeping that consumption away from seed/target
    routing and from adjacent variants makes fixed-seed cohorts comparable and
    also lets a resumed generation reconstruct its streams without replaying
    earlier ones.
    """

    if random_seed is None:
        return random.Random()
    parts = (RANDOM_STREAM_VERSION, int(random_seed), int(generation), stream, *coordinates)
    return random.Random(":".join(str(part) for part in parts))

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
    "UseEveryTick",
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
    if key == "UseEveryTick":
        return False
    frozen_ov, mutable_ov = load_mutation_overrides()
    if key in frozen_ov:
        return False
    if key in mutable_ov:
        return True
    if key in FROZEN_KEYS or any(key.startswith(p) for p in FROZEN_PREFIXES):
        return False
    return key in ALLOWED_MUTATION_KEYS or any(key.startswith(p) for p in ALLOWED_MUTATION_PREFIXES)


def probability_argument(value: str) -> float:
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("debe ser un numero entre 0 y 1") from exc
    if not 0.0 <= probability <= 1.0:
        raise argparse.ArgumentTypeError("debe estar entre 0 y 1")
    return probability


@dataclass(frozen=True)
class LosslessControlGate:
    """Umbrales absolutos para cuando el control OHLC no tiene ni una perdida.

    Con la pata OHLC sin perdidas, PF (centinela 99 -> tope 10) y DD (0.0) dejan
    de ser comparables: exigirian PF de tick >=7.0 y DD de tick <=0.7pp, asi que
    el candidato se rechaza por el artefacto y no por su comportamiento. Cuando
    pasa eso dejamos de preguntar "se parecen?" y preguntamos "se sostiene la
    pata de tick por si sola?", que es la que de verdad se operaria.

    Los valores por defecto son el percentil 5 de la pata real-tick de las 786
    filas ``candidate_final_tick_6m`` aceptadas en memoria a 2026-09 (p95 para el
    drawdown). Es decir: un candidato con control degenerado tiene que caer
    dentro del territorio donde ya vive el 95% de lo que el pipeline acepta, ni
    una vara mas dura ni una barra libre.

    Solo aplica a la etapa de 6M: los umbrales estan calibrados sobre esa
    ventana y no significan nada sobre el probe de un mes.
    """

    min_trades: int = 25
    min_normalized_net_profit: float = 17.0
    min_profit_factor: float = 1.20
    max_drawdown_pct: float = 17.8
    min_recovery_factor: float = 0.75
    min_positive_month_ratio: float = 0.50


def paths_belong_to_workspace(*values: object) -> bool:
    workspace = BASE_DIR.resolve()
    for value in values:
        try:
            Path(str(value)).expanduser().resolve().relative_to(workspace)
        except (OSError, ValueError):
            return False
    return True


def augment_symbol_map_with_suffix_targets(symbol_map_text: str, args: argparse.Namespace) -> str:
    suffix_targets = load_symbol_suffix_target_map(
        Path(args.assets).expanduser(),
        getattr(args, "symbol_suffix", ""),
        getattr(args, "symbol_futures_suffix", ""),
        getattr(args, "symbol_shares_suffix", ""),
    )
    if not suffix_targets:
        return symbol_map_text
    try:
        existing = parse_symbol_map(symbol_map_text)
    except ValueError:
        return symbol_map_text
    additions = [
        f"{source}={target}"
        for source, target in sorted(suffix_targets.items())
        if source not in existing
    ]
    if not additions:
        return symbol_map_text
    base = symbol_map_text.strip()
    return ",".join([base, *additions]) if base else ",".join(additions)
