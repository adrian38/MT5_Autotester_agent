from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


from run_tests import KNOWN_TIMEFRAMES
from ubs.selection_common import (  # noqa: F401  fachada de ubs.selection
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
    _PRODUCTION_ASSET_POLICY_PREFIX,
    _UNSEEDED_ASSET_POLICIES,
    FITNESS_TARGET_FINAL_TICK_6M,
    FITNESS_TIMEFRAMES,
    _row_get,
)
from ubs.selection_mixes import DiscoverySourceMix, DiscoveryTargetPolicyMix
from ubs.selection_fitness import (  # noqa: F401  fachada de ubs.selection
    SelectionFitnessModel,
    SelectionPrediction,
    descendant_fitness_predictions,
    finalized_generation_six_month_label,
    finalized_six_month_label,
)
from ubs.weights import FeedbackSignal, probability_feedback_signals


def estimate_discovery_target_policy_mix(
    rows: Iterable[object],
    *,
    recent_run_limit: int = DISCOVERY_SOURCE_MIX_RECENT_RUNS,
    minimum_trials: int = DISCOVERY_TARGET_POLICY_MIN_TRIALS,
    minimum_benchmark_trials: int = DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS,
) -> DiscoveryTargetPolicyMix:
    materialized = list(rows)
    run_ids = sorted(
        {
            int(_row_get(row, "run_id", 0) or 0)
            for row in materialized
            if int(_row_get(row, "run_id", 0) or 0) > 0
        },
        reverse=True,
    )[: max(int(recent_run_limit), 0)]
    allowed_runs = set(run_ids)
    buckets = {
        "unseeded": [0, 0],
        "benchmark": [0, 0],
        "universe_feedback": [0, 0],
        "universe_explore": [0, 0],
        "current_target": [0, 0],
        "cross_target": [0, 0],
    }
    lifecycle_groups: dict[str, dict[object, list[object]]] = {
        "CURRENT": defaultdict(list),
        "CROSS": defaultdict(list),
    }
    global_lifecycle_groups: dict[object, list[object]] = defaultdict(list)
    allocation_lifecycle_groups: dict[str, dict[object, list[object]]] = {
        "UNSEEDED": defaultdict(list),
        "BENCHMARK": defaultdict(list),
    }
    global_allocation_groups: dict[object, list[object]] = defaultdict(list)
    timeframe_lifecycle_groups: dict[str, dict[object, list[object]]] = {
        "CURRENT_TF": defaultdict(list),
        "CHANGED_TF": defaultdict(list),
    }
    global_timeframe_groups: dict[object, list[object]] = defaultdict(list)
    universe_lifecycle_groups: dict[str, dict[object, list[object]]] = {
        "UNIVERSE_FEEDBACK": defaultdict(list),
        "UNIVERSE_EXPLORE": defaultdict(list),
    }
    global_universe_groups: dict[object, list[object]] = defaultdict(list)
    for row_index, row in enumerate(materialized):
        if int(_row_get(row, "run_id", 0) or 0) not in allowed_runs:
            continue
        status = str(_row_get(row, "status", "")).lower()
        if status not in _SOURCE_FINAL_STATUSES:
            continue
        policy = str(_row_get(row, "policy", "")).split("+", 1)[0]
        if not policy or policy.startswith(_PRODUCTION_ASSET_POLICY_PREFIX):
            continue
        success = int(status == "accepted")
        primary = "unseeded" if policy in _UNSEEDED_ASSET_POLICIES else "benchmark"
        buckets[primary][0] += 1
        buckets[primary][1] += success
        target_bucket = "current_target" if policy == "exploit" else "cross_target"
        buckets[target_bucket][0] += 1
        buckets[target_bucket][1] += success
        route = "CURRENT" if policy == "exploit" else "CROSS"
        seed_path = str(_row_get(row, "seed_path", "") or "")
        generation = int(_row_get(row, "generation", 0) or 0)
        group = (
            int(_row_get(row, "run_id", 0) or 0),
            generation,
            seed_path or f"row:{row_index}",
        )
        allocation_route = "UNSEEDED" if primary == "unseeded" else "BENCHMARK"
        allocation_lifecycle_groups[allocation_route][group].append(row)
        global_allocation_groups[(allocation_route, *group)].append(row)
        lifecycle_groups[route][group].append(row)
        global_lifecycle_groups[(route, *group)].append(row)
        source_period = str(_row_get(row, "source_period", "") or "").upper()
        target_period = str(_row_get(row, "target_period", "") or "").upper()
        if source_period and target_period:
            timeframe_route = "CURRENT_TF" if source_period == target_period else "CHANGED_TF"
            timeframe_lifecycle_groups[timeframe_route][group].append(row)
            global_timeframe_groups[(timeframe_route, *group)].append(row)
        if policy in {"asset_universe_feedback", "asset_universe_explore"}:
            buckets[policy.removeprefix("asset_")][0] += 1
            buckets[policy.removeprefix("asset_")][1] += success
            universe_route = (
                "UNIVERSE_FEEDBACK"
                if policy == "asset_universe_feedback"
                else "UNIVERSE_EXPLORE"
            )
            universe_lifecycle_groups[universe_route][group].append(row)
            global_universe_groups[(universe_route, *group)].append(row)

    def rate(bucket: str) -> float:
        trials, successes = buckets[bucket]
        return (successes + DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS) / (
            trials + DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS + DISCOVERY_SOURCE_MIX_PRIOR_FAILURE
        )

    unseeded_rate = rate("unseeded")
    benchmark_rate = rate("benchmark")
    feedback_rate = rate("universe_feedback")
    explore_rate = rate("universe_explore")
    current_target_rate = rate("current_target")
    cross_target_rate = rate("cross_target")
    empty_signal = FeedbackSignal(0.0, 0.0, 0.0, 0, 0.0, {})
    allocation_signals = probability_feedback_signals(
        allocation_lifecycle_groups,
        global_allocation_groups,
        normalize_keys=False,
        terminal_stage="six_month",
    )
    unseeded_signal = allocation_signals.get("UNSEEDED", empty_signal)
    benchmark_signal = allocation_signals.get("BENCHMARK", empty_signal)
    adaptive_unseeded = (
        buckets["unseeded"][0] >= minimum_trials
        and buckets["benchmark"][0] >= minimum_benchmark_trials
        and unseeded_signal.probability + benchmark_signal.probability > 0.0
    )
    if adaptive_unseeded and benchmark_signal.probability > 0.0:
        unseeded_multiplier = min(
            max(
                unseeded_signal.probability / benchmark_signal.probability,
                DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR,
            ),
            1.0,
        )
    else:
        unseeded_multiplier = 1.0
    universe_signals = probability_feedback_signals(
        universe_lifecycle_groups,
        global_universe_groups,
        normalize_keys=False,
        terminal_stage="six_month",
    )
    universe_feedback_signal = universe_signals.get("UNIVERSE_FEEDBACK", empty_signal)
    universe_explore_signal = universe_signals.get("UNIVERSE_EXPLORE", empty_signal)
    adaptive_feedback_lifecycle = (
        universe_feedback_signal.final_trials >= DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS
        and universe_explore_signal.final_trials >= DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS
        and (
            universe_feedback_signal.final_trials + universe_explore_signal.final_trials
            >= DISCOVERY_UNIVERSE_FEEDBACK_MIN_TOTAL_FINAL_TRIALS
        )
    )
    if (
        adaptive_feedback_lifecycle
        and universe_feedback_signal.probability + universe_explore_signal.probability > 0.0
    ):
        feedback_probability = min(
            max(
                universe_feedback_signal.probability
                / (
                    universe_feedback_signal.probability
                    + universe_explore_signal.probability
                ),
                DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
            ),
            DISCOVERY_UNIVERSE_FEEDBACK_CEILING,
        )
    else:
        feedback_probability = DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT
    lifecycle_signals = probability_feedback_signals(
        lifecycle_groups,
        global_lifecycle_groups,
        normalize_keys=False,
        terminal_stage="six_month",
    )
    current_signal = lifecycle_signals.get("CURRENT", empty_signal)
    cross_signal = lifecycle_signals.get("CROSS", empty_signal)
    adaptive_current_target = (
        current_signal.final_trials >= DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS
        and cross_signal.final_trials >= DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS
    )
    if adaptive_current_target and current_signal.probability + cross_signal.probability > 0.0:
        current_target_probability = min(
            max(
                current_signal.probability
                / (current_signal.probability + cross_signal.probability),
                DISCOVERY_CURRENT_TARGET_FLOOR,
            ),
            DISCOVERY_CURRENT_TARGET_CEILING,
        )
    else:
        current_target_probability = DISCOVERY_CURRENT_TARGET_DEFAULT
    timeframe_signals = probability_feedback_signals(
        timeframe_lifecycle_groups,
        global_timeframe_groups,
        normalize_keys=False,
        terminal_stage="six_month",
    )
    current_timeframe_signal = timeframe_signals.get("CURRENT_TF", empty_signal)
    changed_timeframe_signal = timeframe_signals.get("CHANGED_TF", empty_signal)
    adaptive_current_timeframe = (
        current_timeframe_signal.final_trials >= DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS
        and changed_timeframe_signal.final_trials >= DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS
    )
    if (
        adaptive_current_timeframe
        and current_timeframe_signal.probability + changed_timeframe_signal.probability > 0.0
    ):
        current_timeframe_probability = min(
            max(
                current_timeframe_signal.probability
                / (
                    current_timeframe_signal.probability
                    + changed_timeframe_signal.probability
                ),
                DISCOVERY_CURRENT_TIMEFRAME_FLOOR,
            ),
            DISCOVERY_CURRENT_TIMEFRAME_CEILING,
        )
    else:
        current_timeframe_probability = DISCOVERY_CURRENT_TIMEFRAME_DEFAULT
    return DiscoveryTargetPolicyMix(
        unseeded_multiplier=round(unseeded_multiplier, 6),
        universe_feedback_probability=round(feedback_probability, 6),
        current_target_probability=round(current_target_probability, 6),
        unseeded_trials=buckets["unseeded"][0],
        unseeded_successes=buckets["unseeded"][1],
        unseeded_rate=round(unseeded_rate, 6),
        benchmark_trials=buckets["benchmark"][0],
        benchmark_successes=buckets["benchmark"][1],
        benchmark_rate=round(benchmark_rate, 6),
        unseeded_lifecycle_probability=unseeded_signal.probability,
        benchmark_lifecycle_probability=benchmark_signal.probability,
        unseeded_lifecycle_confidence=unseeded_signal.confidence,
        benchmark_lifecycle_confidence=benchmark_signal.confidence,
        unseeded_final_trials=unseeded_signal.final_trials,
        benchmark_final_trials=benchmark_signal.final_trials,
        universe_feedback_trials=buckets["universe_feedback"][0],
        universe_feedback_successes=buckets["universe_feedback"][1],
        universe_feedback_rate=round(feedback_rate, 6),
        universe_explore_trials=buckets["universe_explore"][0],
        universe_explore_successes=buckets["universe_explore"][1],
        universe_explore_rate=round(explore_rate, 6),
        universe_feedback_lifecycle_probability=universe_feedback_signal.probability,
        universe_explore_lifecycle_probability=universe_explore_signal.probability,
        universe_feedback_lifecycle_confidence=universe_feedback_signal.confidence,
        universe_explore_lifecycle_confidence=universe_explore_signal.confidence,
        universe_feedback_final_trials=universe_feedback_signal.final_trials,
        universe_explore_final_trials=universe_explore_signal.final_trials,
        universe_feedback_lifecycle_adaptive=adaptive_feedback_lifecycle,
        current_target_trials=buckets["current_target"][0],
        current_target_successes=buckets["current_target"][1],
        current_target_rate=round(current_target_rate, 6),
        cross_target_trials=buckets["cross_target"][0],
        cross_target_successes=buckets["cross_target"][1],
        cross_target_rate=round(cross_target_rate, 6),
        current_target_lifecycle_probability=current_signal.probability,
        cross_target_lifecycle_probability=cross_signal.probability,
        current_target_lifecycle_confidence=current_signal.confidence,
        cross_target_lifecycle_confidence=cross_signal.confidence,
        current_target_final_trials=current_signal.final_trials,
        cross_target_final_trials=cross_signal.final_trials,
        current_timeframe_probability=round(current_timeframe_probability, 6),
        current_timeframe_lifecycle_probability=current_timeframe_signal.probability,
        changed_timeframe_lifecycle_probability=changed_timeframe_signal.probability,
        current_timeframe_lifecycle_confidence=current_timeframe_signal.confidence,
        changed_timeframe_lifecycle_confidence=changed_timeframe_signal.confidence,
        current_timeframe_final_trials=current_timeframe_signal.final_trials,
        changed_timeframe_final_trials=changed_timeframe_signal.final_trials,
        recent_runs=tuple(run_ids),
        adaptive_unseeded=adaptive_unseeded,
        adaptive_universe_feedback=adaptive_feedback_lifecycle,
        adaptive_current_target=adaptive_current_target,
        adaptive_current_timeframe=adaptive_current_timeframe,
    )


def _discovery_source_groups(
    rows: Iterable[object], recent_run_limit: int,
) -> tuple[list[int], dict[tuple[int, int, str, bool], list[int]]]:
    materialized = list(rows)
    run_ids = sorted(
        {
            int(_row_get(row, "run_id", 0) or 0)
            for row in materialized
            if int(_row_get(row, "run_id", 0) or 0) > 0
        },
        reverse=True,
    )[: max(int(recent_run_limit), 0)]
    allowed_runs = set(run_ids)
    grouped: dict[tuple[int, int, str, bool], list[int]] = {}
    for row in materialized:
        run_id = int(_row_get(row, "run_id", 0) or 0)
        if run_id not in allowed_runs:
            continue
        outcome = finalized_generation_six_month_label(row)
        if outcome is None:
            continue
        key = (
            run_id,
            int(_row_get(row, "generation", 0) or 0),
            str(_row_get(row, "seed_path", "")),
            bool(_row_get(row, "exploitable", False)),
        )
        grouped.setdefault(key, []).append(outcome)
    return run_ids, grouped


def estimate_discovery_source_mix(
    rows: Iterable[object],
    *,
    recent_run_limit: int = DISCOVERY_SOURCE_MIX_RECENT_RUNS,
    minimum_trials: int = DISCOVERY_SOURCE_MIX_MIN_TRIALS,
    floor: float = DISCOVERY_SOURCE_MIX_FLOOR,
    ceiling: float = DISCOVERY_SOURCE_MIX_CEILING,
) -> DiscoverySourceMix:
    """Allocate discovery sources from broker-local, source-level outcomes.

    Three variants from one selected source are correlated, so they count as a
    single trial. A source succeeds only when any child reaches accepted FT 6M.
    Valid failures at an earlier funnel stage count as failures; unresolved
    technical outcomes do not turn into negative evidence.
    """

    run_ids, grouped = _discovery_source_groups(rows, recent_run_limit)

    trials = {True: 0, False: 0}
    successes = {True: 0, False: 0}
    for (_run_id, _generation, _seed_path, exploitable), outcomes in grouped.items():
        trials[exploitable] += 1
        successes[exploitable] += int(1 in outcomes)

    def smoothed_rate(bucket: bool) -> float:
        numerator = successes[bucket] + DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS
        denominator = (
            trials[bucket]
            + DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS
            + DISCOVERY_SOURCE_MIX_PRIOR_FAILURE
        )
        return numerator / denominator

    exploitable_rate = smoothed_rate(True)
    cross_asset_rate = smoothed_rate(False)
    enough_evidence = trials[True] >= minimum_trials and trials[False] >= minimum_trials
    if enough_evidence and exploitable_rate + cross_asset_rate > 0.0:
        raw_ratio = exploitable_rate / (exploitable_rate + cross_asset_rate)
        ratio = min(max(raw_ratio, floor), ceiling)
        reason = "adaptive"
    else:
        ratio = floor
        reason = "insufficient_evidence"
    return DiscoverySourceMix(
        exploitable_ratio=round(ratio, 6),
        exploitable_trials=trials[True],
        exploitable_successes=successes[True],
        exploitable_rate=round(exploitable_rate, 6),
        cross_asset_trials=trials[False],
        cross_asset_successes=successes[False],
        cross_asset_rate=round(cross_asset_rate, 6),
        recent_runs=tuple(run_ids),
        adaptive=enough_evidence,
        reason=reason,
    )
