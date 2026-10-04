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


_EMPTY_FEEDBACK_SIGNAL = FeedbackSignal(0.0, 0.0, 0.0, 0, 0.0, {})


class _RouteGroups:
    """Grupos de ciclo de vida de una decision, por ruta y en global."""

    def __init__(self, routes: tuple[str, ...]) -> None:
        self.by_route: dict[str, dict[object, list[object]]] = {
            route: defaultdict(list) for route in routes
        }
        self.global_groups: dict[object, list[object]] = defaultdict(list)

    def add(self, route: str, group: tuple, row: object) -> None:
        """Anota una fila en su ruta y en el conjunto global."""
        self.by_route[route][group].append(row)
        self.global_groups[(route, *group)].append(row)

    def pair(self, first: str, second: str) -> tuple[FeedbackSignal, FeedbackSignal]:
        """Senales de probabilidad de las dos rutas que se comparan."""
        signals = probability_feedback_signals(
            self.by_route,
            self.global_groups,
            normalize_keys=False,
            terminal_stage="six_month",
        )
        return (
            signals.get(first, _EMPTY_FEEDBACK_SIGNAL),
            signals.get(second, _EMPTY_FEEDBACK_SIGNAL),
        )


@dataclass
class _PolicyMixTally:
    """Recuento por politica y grupos de ciclo de vida de cada decision."""

    buckets: dict[str, list[int]]
    allocation: _RouteGroups
    lifecycle: _RouteGroups
    timeframe: _RouteGroups
    universe: _RouteGroups

    @classmethod
    def empty(cls) -> "_PolicyMixTally":
        """Contadores a cero para las seis cestas y las cuatro decisiones."""
        return cls(
            buckets={
                "unseeded": [0, 0],
                "benchmark": [0, 0],
                "universe_feedback": [0, 0],
                "universe_explore": [0, 0],
                "current_target": [0, 0],
                "cross_target": [0, 0],
            },
            allocation=_RouteGroups(("UNSEEDED", "BENCHMARK")),
            lifecycle=_RouteGroups(("CURRENT", "CROSS")),
            timeframe=_RouteGroups(("CURRENT_TF", "CHANGED_TF")),
            universe=_RouteGroups(("UNIVERSE_FEEDBACK", "UNIVERSE_EXPLORE")),
        )

    def count(self, bucket: str, success: int) -> None:
        """Suma un intento y su resultado a una cesta."""
        self.buckets[bucket][0] += 1
        self.buckets[bucket][1] += success

    def rate(self, bucket: str) -> float:
        """Tasa de exito de la cesta con el prior de la mezcla de fuentes."""
        trials, successes = self.buckets[bucket]
        return (successes + DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS) / (
            trials + DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS + DISCOVERY_SOURCE_MIX_PRIOR_FAILURE
        )


def _recent_policy_run_ids(materialized: list[object], recent_run_limit: int) -> list[int]:
    """Los ultimos runs que entran en la estimacion, de mas nuevo a mas viejo."""
    return sorted(
        {
            int(_row_get(row, "run_id", 0) or 0)
            for row in materialized
            if int(_row_get(row, "run_id", 0) or 0) > 0
        },
        reverse=True,
    )[: max(int(recent_run_limit), 0)]


def _tally_policy_row(tally: _PolicyMixTally, row: object, row_index: int) -> None:
    """Reparte una fila final en sus cestas y grupos de ciclo de vida."""
    status = str(_row_get(row, "status", "")).lower()
    if status not in _SOURCE_FINAL_STATUSES:
        return
    policy = str(_row_get(row, "policy", "")).split("+", 1)[0]
    if not policy or policy.startswith(_PRODUCTION_ASSET_POLICY_PREFIX):
        return
    success = int(status == "accepted")
    primary = "unseeded" if policy in _UNSEEDED_ASSET_POLICIES else "benchmark"
    tally.count(primary, success)
    tally.count("current_target" if policy == "exploit" else "cross_target", success)
    group = (
        int(_row_get(row, "run_id", 0) or 0),
        int(_row_get(row, "generation", 0) or 0),
        str(_row_get(row, "seed_path", "") or "") or f"row:{row_index}",
    )
    tally.allocation.add("UNSEEDED" if primary == "unseeded" else "BENCHMARK", group, row)
    tally.lifecycle.add("CURRENT" if policy == "exploit" else "CROSS", group, row)
    source_period = str(_row_get(row, "source_period", "") or "").upper()
    target_period = str(_row_get(row, "target_period", "") or "").upper()
    if source_period and target_period:
        tally.timeframe.add(
            "CURRENT_TF" if source_period == target_period else "CHANGED_TF", group, row
        )
    if policy in {"asset_universe_feedback", "asset_universe_explore"}:
        tally.count(policy.removeprefix("asset_"), success)
        tally.universe.add(
            "UNIVERSE_FEEDBACK" if policy == "asset_universe_feedback" else "UNIVERSE_EXPLORE",
            group,
            row,
        )


def _tally_policy_rows(materialized: list[object], allowed_runs: set[int]) -> _PolicyMixTally:
    """Recorre las filas de los runs recientes y acumula la evidencia."""
    tally = _PolicyMixTally.empty()
    for row_index, row in enumerate(materialized):
        if int(_row_get(row, "run_id", 0) or 0) not in allowed_runs:
            continue
        _tally_policy_row(tally, row, row_index)
    return tally


def _bounded_share(
    first: FeedbackSignal,
    second: FeedbackSignal,
    *,
    adaptive: bool,
    floor: float,
    ceiling: float,
    default: float,
) -> float:
    """Reparto entre dos rutas acotado entre suelo y techo, o el valor por defecto."""
    total = first.probability + second.probability
    if adaptive and total > 0.0:
        return min(max(first.probability / total, floor), ceiling)
    return default


def _unseeded_multiplier_decision(
    tally: _PolicyMixTally, minimum_trials: int, minimum_benchmark_trials: int
) -> tuple[float, bool, FeedbackSignal, FeedbackSignal]:
    """Cuanto se penaliza la busqueda sin semilla frente a la de referencia."""
    unseeded_signal, benchmark_signal = tally.allocation.pair("UNSEEDED", "BENCHMARK")
    adaptive = (
        tally.buckets["unseeded"][0] >= minimum_trials
        and tally.buckets["benchmark"][0] >= minimum_benchmark_trials
        and unseeded_signal.probability + benchmark_signal.probability > 0.0
    )
    if adaptive and benchmark_signal.probability > 0.0:
        multiplier = min(
            max(
                unseeded_signal.probability / benchmark_signal.probability,
                DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR,
            ),
            1.0,
        )
    else:
        multiplier = 1.0
    return multiplier, adaptive, unseeded_signal, benchmark_signal


def _universe_feedback_decision(
    tally: _PolicyMixTally,
) -> tuple[float, bool, FeedbackSignal, FeedbackSignal]:
    """Probabilidad de volver a un activo ya conocido frente a explorar."""
    feedback_signal, explore_signal = tally.universe.pair("UNIVERSE_FEEDBACK", "UNIVERSE_EXPLORE")
    adaptive = (
        feedback_signal.final_trials >= DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS
        and explore_signal.final_trials >= DISCOVERY_UNIVERSE_FEEDBACK_MIN_FINAL_TRIALS
        and (
            feedback_signal.final_trials + explore_signal.final_trials
            >= DISCOVERY_UNIVERSE_FEEDBACK_MIN_TOTAL_FINAL_TRIALS
        )
    )
    probability = _bounded_share(
        feedback_signal,
        explore_signal,
        adaptive=adaptive,
        floor=DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
        ceiling=DISCOVERY_UNIVERSE_FEEDBACK_CEILING,
        default=DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT,
    )
    return probability, adaptive, feedback_signal, explore_signal


def _current_target_decision(
    tally: _PolicyMixTally,
) -> tuple[float, bool, FeedbackSignal, FeedbackSignal]:
    """Probabilidad de quedarse en el simbolo actual en vez de cruzar."""
    current_signal, cross_signal = tally.lifecycle.pair("CURRENT", "CROSS")
    adaptive = (
        current_signal.final_trials >= DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS
        and cross_signal.final_trials >= DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS
    )
    probability = _bounded_share(
        current_signal,
        cross_signal,
        adaptive=adaptive,
        floor=DISCOVERY_CURRENT_TARGET_FLOOR,
        ceiling=DISCOVERY_CURRENT_TARGET_CEILING,
        default=DISCOVERY_CURRENT_TARGET_DEFAULT,
    )
    return probability, adaptive, current_signal, cross_signal


def _current_timeframe_decision(
    tally: _PolicyMixTally,
) -> tuple[float, bool, FeedbackSignal, FeedbackSignal]:
    """Probabilidad de mantener el marco temporal de origen."""
    current_signal, changed_signal = tally.timeframe.pair("CURRENT_TF", "CHANGED_TF")
    adaptive = (
        current_signal.final_trials >= DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS
        and changed_signal.final_trials >= DISCOVERY_CURRENT_TIMEFRAME_MIN_FINAL_TRIALS
    )
    probability = _bounded_share(
        current_signal,
        changed_signal,
        adaptive=adaptive,
        floor=DISCOVERY_CURRENT_TIMEFRAME_FLOOR,
        ceiling=DISCOVERY_CURRENT_TIMEFRAME_CEILING,
        default=DISCOVERY_CURRENT_TIMEFRAME_DEFAULT,
    )
    return probability, adaptive, current_signal, changed_signal


def _policy_mix_counts(tally: _PolicyMixTally) -> dict[str, object]:
    """Intentos, aciertos y tasa de cada cesta, tal y como los pide la mezcla."""
    counts: dict[str, object] = {}
    for bucket in ("unseeded", "benchmark", "universe_feedback", "universe_explore",
                   "current_target", "cross_target"):
        counts[f"{bucket}_trials"] = tally.buckets[bucket][0]
        counts[f"{bucket}_successes"] = tally.buckets[bucket][1]
        counts[f"{bucket}_rate"] = round(tally.rate(bucket), 6)
    return counts


def _policy_mix_signal_fields(
    first_name: str, first: FeedbackSignal, second_name: str, second: FeedbackSignal
) -> dict[str, object]:
    """Probabilidad, confianza y ensayos finales de las dos rutas de una decision."""
    return {
        f"{first_name}_lifecycle_probability": first.probability,
        f"{second_name}_lifecycle_probability": second.probability,
        f"{first_name}_lifecycle_confidence": first.confidence,
        f"{second_name}_lifecycle_confidence": second.confidence,
        f"{first_name}_final_trials": first.final_trials,
        f"{second_name}_final_trials": second.final_trials,
    }


def estimate_discovery_target_policy_mix(
    rows: Iterable[object],
    *,
    recent_run_limit: int = DISCOVERY_SOURCE_MIX_RECENT_RUNS,
    minimum_trials: int = DISCOVERY_TARGET_POLICY_MIN_TRIALS,
    minimum_benchmark_trials: int = DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS,
) -> DiscoveryTargetPolicyMix:
    materialized = list(rows)
    run_ids = _recent_policy_run_ids(materialized, recent_run_limit)
    tally = _tally_policy_rows(materialized, set(run_ids))
    unseeded_multiplier, adaptive_unseeded, unseeded_signal, benchmark_signal = (
        _unseeded_multiplier_decision(tally, minimum_trials, minimum_benchmark_trials)
    )
    feedback_probability, adaptive_feedback, feedback_signal, explore_signal = (
        _universe_feedback_decision(tally)
    )
    current_target_probability, adaptive_current_target, current_signal, cross_signal = (
        _current_target_decision(tally)
    )
    current_timeframe_probability, adaptive_current_timeframe, current_tf_signal, changed_tf_signal = (
        _current_timeframe_decision(tally)
    )
    return DiscoveryTargetPolicyMix(
        unseeded_multiplier=round(unseeded_multiplier, 6),
        universe_feedback_probability=round(feedback_probability, 6),
        current_target_probability=round(current_target_probability, 6),
        current_timeframe_probability=round(current_timeframe_probability, 6),
        **_policy_mix_counts(tally),
        **_policy_mix_signal_fields("unseeded", unseeded_signal, "benchmark", benchmark_signal),
        **_policy_mix_signal_fields(
            "universe_feedback", feedback_signal, "universe_explore", explore_signal
        ),
        **_policy_mix_signal_fields("current_target", current_signal, "cross_target", cross_signal),
        **_policy_mix_signal_fields(
            "current_timeframe", current_tf_signal, "changed_timeframe", changed_tf_signal
        ),
        universe_feedback_lifecycle_adaptive=adaptive_feedback,
        recent_runs=tuple(run_ids),
        adaptive_unseeded=adaptive_unseeded,
        adaptive_universe_feedback=adaptive_feedback,
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
