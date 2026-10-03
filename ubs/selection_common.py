"""Constantes y conversiones compartidas de la seleccion UBS."""
from __future__ import annotations

import json
import math

from typing import Mapping

from run_tests import KNOWN_TIMEFRAMES


FITNESS_TIMEFRAMES = KNOWN_TIMEFRAMES
MIN_TRAINING_ROWS = 300
MIN_POSITIVE_ROWS = 30
FITNESS_WEIGHT_SCALE = 10.0
FITNESS_WEIGHT_LIMIT = 15.0
DESCENDANT_FITNESS_PRIOR_STRENGTH = 10.0
_DENSE_FITNESS_FEATURES = 8
FITNESS_TARGET_FINAL_TICK_6M = "final_tick_6m"
FITNESS_TARGET_ROBUSTNESS = "robustness"
FITNESS_TARGETS = {FITNESS_TARGET_FINAL_TICK_6M, FITNESS_TARGET_ROBUSTNESS}


def _row_get(row: object, key: str, default: object = None) -> object:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sigmoid(value: float) -> float:
    bounded = min(max(value, -35.0), 35.0)
    return 1.0 / (1.0 + math.exp(-bounded))


def _logit(value: float) -> float:
    bounded = min(max(value, 1e-9), 1.0 - 1e-9)
    return math.log(bounded / (1.0 - bounded))


def _metrics(metrics_json: object) -> dict[str, object]:
    try:
        data = json.loads(str(metrics_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}



DISCOVERY_SOURCE_MIX_MODEL = "beta_smoothed_source_descendant_6m_success_v3"
DISCOVERY_SOURCE_MIX_RECENT_RUNS = 10
DISCOVERY_SOURCE_MIX_MIN_TRIALS = 20
DISCOVERY_SOURCE_MIX_FLOOR = 0.60
DISCOVERY_SOURCE_MIX_CEILING = 0.85
DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS = 2.0
DISCOVERY_SOURCE_MIX_PRIOR_FAILURE = 2.0
_SOURCE_FINAL_STATUSES = {"accepted", "rejected", "no_trades"}
DISCOVERY_TARGET_POLICY_MODEL = "lifecycle_smoothed_target_policy_v3"
DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR = 0.25
DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT = 0.55
DISCOVERY_UNIVERSE_FEEDBACK_CEILING = 0.85
DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS = 1.0
DISCOVERY_UNIVERSE_FEEDBACK_MIN_TOTAL_FINAL_TRIALS = 4.0
DISCOVERY_CURRENT_TARGET_DEFAULT = 0.70
DISCOVERY_CURRENT_TARGET_FLOOR = 0.55
DISCOVERY_CURRENT_TARGET_CEILING = 0.85
DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS = 3
DISCOVERY_CURRENT_TIMEFRAME_DEFAULT = 0.60
DISCOVERY_CURRENT_TIMEFRAME_FLOOR = 0.45
DISCOVERY_CURRENT_TIMEFRAME_CEILING = 0.80
DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS = 3
DISCOVERY_TARGET_POLICY_MIN_TRIALS = 20
DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS = 100
_UNSEEDED_ASSET_POLICIES = {"asset_unseeded_force", "asset_unseeded_group_feedback"}
_PRODUCTION_ASSET_POLICY_PREFIX = "production_"
