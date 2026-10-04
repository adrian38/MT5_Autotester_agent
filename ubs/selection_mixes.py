"""Mezclas de descubrimiento: fuente y politica de objetivo."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ubs.selection_common import (
    FITNESS_TIMEFRAMES,
    DISCOVERY_CURRENT_TARGET_CEILING,
    DISCOVERY_CURRENT_TARGET_DEFAULT,
    DISCOVERY_CURRENT_TARGET_FLOOR,
    DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS,
    DISCOVERY_CURRENT_TIMEFRAME_CEILING,
    DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
    DISCOVERY_CURRENT_TIMEFRAME_FLOOR,
    DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS,
    DISCOVERY_SOURCE_MIX_CEILING,
    DISCOVERY_SOURCE_MIX_FLOOR,
    DISCOVERY_SOURCE_MIX_MIN_TRIALS,
    DISCOVERY_SOURCE_MIX_MODEL,
    DISCOVERY_SOURCE_MIX_PRIOR_FAILURE,
    DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS,
    DISCOVERY_SOURCE_MIX_RECENT_RUNS,
    DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS,
    DISCOVERY_TARGET_POLICY_MIN_TRIALS,
    DISCOVERY_TARGET_POLICY_MODEL,
    DISCOVERY_UNIVERSE_FEEDBACK_CEILING,
    DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
    DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS,
    DISCOVERY_UNIVERSE_FEEDBACK_MIN_TOTAL_FINAL_TRIALS,
    DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR,
    _SOURCE_FINAL_STATUSES,
    _UNSEEDED_ASSET_POLICIES,
)


@dataclass(frozen=True)
class DiscoverySourceMix:
    exploitable_ratio: float
    exploitable_trials: int
    exploitable_successes: int
    exploitable_rate: float
    cross_asset_trials: int
    cross_asset_successes: int
    cross_asset_rate: float
    recent_runs: tuple[int, ...]
    adaptive: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "model": DISCOVERY_SOURCE_MIX_MODEL,
            "unit": "selected_source_any_descendant_final_tick_6m_accept",
            "exploitable_ratio": self.exploitable_ratio,
            "cross_asset_ratio": round(1.0 - self.exploitable_ratio, 6),
            "floor": DISCOVERY_SOURCE_MIX_FLOOR,
            "ceiling": DISCOVERY_SOURCE_MIX_CEILING,
            "minimum_trials_per_bucket": DISCOVERY_SOURCE_MIX_MIN_TRIALS,
            "prior": {
                "success": DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS,
                "failure": DISCOVERY_SOURCE_MIX_PRIOR_FAILURE,
            },
            "recent_run_limit": DISCOVERY_SOURCE_MIX_RECENT_RUNS,
            "recent_runs": list(self.recent_runs),
            "adaptive": self.adaptive,
            "reason": self.reason,
            "exploitable": {
                "trials": self.exploitable_trials,
                "successes": self.exploitable_successes,
                "smoothed_rate": self.exploitable_rate,
            },
            "cross_asset": {
                "trials": self.cross_asset_trials,
                "successes": self.cross_asset_successes,
                "smoothed_rate": self.cross_asset_rate,
            },
        }


@dataclass(frozen=True)
class DiscoveryTargetPolicyMix:
    unseeded_multiplier: float
    universe_feedback_probability: float
    current_target_probability: float
    unseeded_trials: int
    unseeded_successes: int
    unseeded_rate: float
    benchmark_trials: int
    benchmark_successes: int
    benchmark_rate: float
    unseeded_lifecycle_probability: float
    benchmark_lifecycle_probability: float
    unseeded_lifecycle_confidence: float
    benchmark_lifecycle_confidence: float
    unseeded_final_trials: float
    benchmark_final_trials: float
    universe_feedback_trials: int
    universe_feedback_successes: int
    universe_feedback_rate: float
    universe_explore_trials: int
    universe_explore_successes: int
    universe_explore_rate: float
    universe_feedback_lifecycle_probability: float
    universe_explore_lifecycle_probability: float
    universe_feedback_lifecycle_confidence: float
    universe_explore_lifecycle_confidence: float
    universe_feedback_final_trials: float
    universe_explore_final_trials: float
    universe_feedback_lifecycle_adaptive: bool
    current_target_trials: int
    current_target_successes: int
    current_target_rate: float
    cross_target_trials: int
    cross_target_successes: int
    cross_target_rate: float
    current_target_lifecycle_probability: float
    cross_target_lifecycle_probability: float
    current_target_lifecycle_confidence: float
    cross_target_lifecycle_confidence: float
    current_target_final_trials: float
    cross_target_final_trials: float
    current_timeframe_probability: float
    current_timeframe_lifecycle_probability: float
    changed_timeframe_lifecycle_probability: float
    current_timeframe_lifecycle_confidence: float
    changed_timeframe_lifecycle_confidence: float
    current_timeframe_final_trials: float
    changed_timeframe_final_trials: float
    recent_runs: tuple[int, ...]
    adaptive_unseeded: bool
    adaptive_universe_feedback: bool
    adaptive_current_target: bool
    adaptive_current_timeframe: bool

    def _unseeded_dict(self) -> dict[str, object]:
        return {
            "multiplier": self.unseeded_multiplier,
            "multiplier_floor": DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR,
            "adaptive": self.adaptive_unseeded,
            "trials": self.unseeded_trials,
            "successes": self.unseeded_successes,
            "smoothed_rate": self.unseeded_rate,
            "benchmark_trials": self.benchmark_trials,
            "benchmark_successes": self.benchmark_successes,
            "benchmark_smoothed_rate": self.benchmark_rate,
            "routing_basis": "lifecycle_through_six_month",
            "unseeded_lifecycle": {
                "probability": self.unseeded_lifecycle_probability,
                "confidence": self.unseeded_lifecycle_confidence,
                "final_trials": self.unseeded_final_trials,
            },
            "benchmark_lifecycle": {
                "probability": self.benchmark_lifecycle_probability,
                "confidence": self.benchmark_lifecycle_confidence,
                "final_trials": self.benchmark_final_trials,
            },
        }

    def _universe_feedback_dict(self) -> dict[str, object]:
        return {
            "probability": self.universe_feedback_probability,
            "floor": DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
            "ceiling": DISCOVERY_UNIVERSE_FEEDBACK_CEILING,
            "adaptive": self.adaptive_universe_feedback,
            "feedback_trials": self.universe_feedback_trials,
            "feedback_successes": self.universe_feedback_successes,
            "feedback_smoothed_rate": self.universe_feedback_rate,
            "explore_trials": self.universe_explore_trials,
            "explore_successes": self.universe_explore_successes,
            "explore_smoothed_rate": self.universe_explore_rate,
            "routing_basis": (
                "lifecycle"
                if self.universe_feedback_lifecycle_adaptive
                else "default"
            ),
            "minimum_final_trials_per_bucket": DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS,
            "minimum_total_final_trials": DISCOVERY_UNIVERSE_FEEDBACK_MIN_TOTAL_FINAL_TRIALS,
            "feedback_lifecycle": {
                "probability": self.universe_feedback_lifecycle_probability,
                "confidence": self.universe_feedback_lifecycle_confidence,
                "final_trials": self.universe_feedback_final_trials,
            },
            "explore_lifecycle": {
                "probability": self.universe_explore_lifecycle_probability,
                "confidence": self.universe_explore_lifecycle_confidence,
                "final_trials": self.universe_explore_final_trials,
            },
        }

    def _current_target_dict(self) -> dict[str, object]:
        return {
            "probability": self.current_target_probability,
            "default": DISCOVERY_CURRENT_TARGET_DEFAULT,
            "floor": DISCOVERY_CURRENT_TARGET_FLOOR,
            "ceiling": DISCOVERY_CURRENT_TARGET_CEILING,
            "adaptive": self.adaptive_current_target,
            "minimum_final_trials_per_bucket": DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS,
            "current": {
                "base_trials": self.current_target_trials,
                "base_successes": self.current_target_successes,
                "base_smoothed_rate": self.current_target_rate,
                "lifecycle_probability": self.current_target_lifecycle_probability,
                "lifecycle_confidence": self.current_target_lifecycle_confidence,
                "final_trials": self.current_target_final_trials,
            },
            "cross": {
                "base_trials": self.cross_target_trials,
                "base_successes": self.cross_target_successes,
                "base_smoothed_rate": self.cross_target_rate,
                "lifecycle_probability": self.cross_target_lifecycle_probability,
                "lifecycle_confidence": self.cross_target_lifecycle_confidence,
                "final_trials": self.cross_target_final_trials,
            },
        }

    def _current_timeframe_dict(self) -> dict[str, object]:
        return {
            "probability": self.current_timeframe_probability,
            "default": DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
            "floor": DISCOVERY_CURRENT_TIMEFRAME_FLOOR,
            "ceiling": DISCOVERY_CURRENT_TIMEFRAME_CEILING,
            "adaptive": self.adaptive_current_timeframe,
            "minimum_final_trials_per_bucket": DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS,
            "current": {
                "lifecycle_probability": self.current_timeframe_lifecycle_probability,
                "lifecycle_confidence": self.current_timeframe_lifecycle_confidence,
                "final_trials": self.current_timeframe_final_trials,
            },
            "changed": {
                "lifecycle_probability": self.changed_timeframe_lifecycle_probability,
                "lifecycle_confidence": self.changed_timeframe_lifecycle_confidence,
                "final_trials": self.changed_timeframe_final_trials,
            },
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "model": DISCOVERY_TARGET_POLICY_MODEL,
            "unit": "finalized_base_candidate",
            "recent_run_limit": DISCOVERY_SOURCE_MIX_RECENT_RUNS,
            "recent_runs": list(self.recent_runs),
            "unseeded": self._unseeded_dict(),
            "universe_feedback": self._universe_feedback_dict(),
            "current_target": self._current_target_dict(),
            "current_timeframe": self._current_timeframe_dict(),
            "minimum_trials": DISCOVERY_TARGET_POLICY_MIN_TRIALS,
            "minimum_benchmark_trials": DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS,
            "prior": {
                "success": DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS,
                "failure": DISCOVERY_SOURCE_MIX_PRIOR_FAILURE,
            },
        }
