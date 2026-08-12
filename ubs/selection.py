from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from typing import Iterable, Mapping


from run_tests import KNOWN_TIMEFRAMES
from ubs.weights import FeedbackSignal, probability_feedback_signals

FITNESS_TIMEFRAMES = KNOWN_TIMEFRAMES
MIN_TRAINING_ROWS = 300
MIN_POSITIVE_ROWS = 30
FITNESS_WEIGHT_SCALE = 10.0
FITNESS_WEIGHT_LIMIT = 15.0
_DENSE_FITNESS_FEATURES = 8
DISCOVERY_SOURCE_MIX_MODEL = "beta_smoothed_source_success_v1"
DISCOVERY_SOURCE_MIX_RECENT_RUNS = 10
DISCOVERY_SOURCE_MIX_MIN_TRIALS = 20
DISCOVERY_SOURCE_MIX_FLOOR = 0.60
DISCOVERY_SOURCE_MIX_CEILING = 0.85
DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS = 2.0
DISCOVERY_SOURCE_MIX_PRIOR_FAILURE = 2.0
_SOURCE_FINAL_STATUSES = {"accepted", "rejected", "no_trades"}
DISCOVERY_TARGET_POLICY_MODEL = "lifecycle_smoothed_target_policy_v2"
DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR = 0.25
DISCOVERY_UNIVERSE_FEEDBACK_DEFAULT = 0.55
DISCOVERY_UNIVERSE_FEEDBACK_CEILING = 0.85
DISCOVERY_CURRENT_TARGET_DEFAULT = 0.70
DISCOVERY_CURRENT_TARGET_FLOOR = 0.55
DISCOVERY_CURRENT_TARGET_CEILING = 0.85
DISCOVERY_CURRENT_TARGET_MIN_FINAL_TRIALS = 3
DISCOVERY_TARGET_POLICY_MIN_TRIALS = 20
DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS = 100
_UNSEEDED_ASSET_POLICIES = {"asset_unseeded_force", "asset_unseeded_group_feedback"}
_PRODUCTION_ASSET_POLICY_PREFIX = "production_"


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
            "unit": "selected_source_any_base_accept",
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
    universe_feedback_trials: int
    universe_feedback_successes: int
    universe_feedback_rate: float
    universe_explore_trials: int
    universe_explore_successes: int
    universe_explore_rate: float
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
    recent_runs: tuple[int, ...]
    adaptive_unseeded: bool
    adaptive_universe_feedback: bool
    adaptive_current_target: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "model": DISCOVERY_TARGET_POLICY_MODEL,
            "unit": "finalized_base_candidate",
            "recent_run_limit": DISCOVERY_SOURCE_MIX_RECENT_RUNS,
            "recent_runs": list(self.recent_runs),
            "unseeded": {
                "multiplier": self.unseeded_multiplier,
                "multiplier_floor": DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR,
                "adaptive": self.adaptive_unseeded,
                "trials": self.unseeded_trials,
                "successes": self.unseeded_successes,
                "smoothed_rate": self.unseeded_rate,
                "benchmark_trials": self.benchmark_trials,
                "benchmark_successes": self.benchmark_successes,
                "benchmark_smoothed_rate": self.benchmark_rate,
            },
            "universe_feedback": {
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
            },
            "current_target": {
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
            },
            "minimum_trials": DISCOVERY_TARGET_POLICY_MIN_TRIALS,
            "minimum_benchmark_trials": DISCOVERY_TARGET_POLICY_MIN_BENCHMARK_TRIALS,
            "prior": {
                "success": DISCOVERY_SOURCE_MIX_PRIOR_SUCCESS,
                "failure": DISCOVERY_SOURCE_MIX_PRIOR_FAILURE,
            },
        }


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
        lifecycle_groups[route][group].append(row)
        global_lifecycle_groups[(route, *group)].append(row)
        if policy in {"asset_universe_feedback", "asset_universe_explore"}:
            buckets[policy.removeprefix("asset_")][0] += 1
            buckets[policy.removeprefix("asset_")][1] += success

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
    adaptive_unseeded = (
        buckets["unseeded"][0] >= minimum_trials
        and buckets["benchmark"][0] >= minimum_benchmark_trials
    )
    if adaptive_unseeded and benchmark_rate > 0.0:
        unseeded_multiplier = min(
            max(unseeded_rate / benchmark_rate, DISCOVERY_UNSEEDED_MULTIPLIER_FLOOR),
            1.0,
        )
    else:
        unseeded_multiplier = 1.0
    adaptive_feedback = (
        buckets["universe_feedback"][0] >= minimum_trials
        and buckets["universe_explore"][0] >= minimum_trials
    )
    if adaptive_feedback and feedback_rate + explore_rate > 0.0:
        feedback_probability = min(
            max(
                feedback_rate / (feedback_rate + explore_rate),
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
    )
    empty_signal = FeedbackSignal(0.0, 0.0, 0.0, 0, 0.0, {})
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
        universe_feedback_trials=buckets["universe_feedback"][0],
        universe_feedback_successes=buckets["universe_feedback"][1],
        universe_feedback_rate=round(feedback_rate, 6),
        universe_explore_trials=buckets["universe_explore"][0],
        universe_explore_successes=buckets["universe_explore"][1],
        universe_explore_rate=round(explore_rate, 6),
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
        recent_runs=tuple(run_ids),
        adaptive_unseeded=adaptive_unseeded,
        adaptive_universe_feedback=adaptive_feedback,
        adaptive_current_target=adaptive_current_target,
    )


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
    single trial.  A source succeeds when any finalized base variant is
    accepted. Technical outcomes do not turn into negative evidence.
    """

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
    grouped: dict[tuple[int, int, str, bool], set[str]] = {}
    for row in materialized:
        run_id = int(_row_get(row, "run_id", 0) or 0)
        if run_id not in allowed_runs:
            continue
        status = str(_row_get(row, "status", "")).lower()
        if status not in _SOURCE_FINAL_STATUSES:
            continue
        key = (
            run_id,
            int(_row_get(row, "generation", 0) or 0),
            str(_row_get(row, "seed_path", "")),
            bool(_row_get(row, "exploitable", False)),
        )
        grouped.setdefault(key, set()).add(status)

    trials = {True: 0, False: 0}
    successes = {True: 0, False: 0}
    for (_run_id, _generation, _seed_path, exploitable), statuses in grouped.items():
        trials[exploitable] += 1
        successes[exploitable] += int("accepted" in statuses)

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


def finalized_six_month_label(row: object) -> int | None:
    """Return the definitive pipeline label, excluding unresolved technical rows."""

    if str(_row_get(row, "status", "")).lower() != "accepted":
        return None
    robust = str(_row_get(row, "robust_status", "")).lower()
    if robust in {"rejected", "no_trades"}:
        return 0
    if robust != "accepted":
        return None
    probe = str(_row_get(row, "final_tick_status", "")).lower()
    if probe == "rejected":
        return 0
    if probe not in {"accepted", "pending_ohlc_trades"}:
        return None
    six_month = str(_row_get(row, "final_tick_6m_status", "")).lower()
    if six_month == "accepted":
        return 1
    if six_month == "rejected":
        return 0
    return None


def fitness_features(score: object, metrics_json: object, period: object) -> tuple[float, ...] | None:
    data = _metrics(metrics_json)
    if not data:
        return None
    profit_factor = max(_safe_float(data.get("profit_factor")), 0.0)
    recovery = max(_safe_float(data.get("recovery_factor")), 0.0)
    trades = max(_safe_float(data.get("trades")), 0.0)
    values = [
        max(-3.0, min(3.0, _safe_float(score) / 100.0)),
        math.log1p(min(profit_factor, 20.0)),
        math.log1p(min(recovery, 200.0)),
        min(max(_safe_float(data.get("drawdown_pct")), 0.0), 100.0) / 25.0,
        math.log1p(min(trades, 100000.0)),
        min(max(_safe_float(data.get("positive_month_ratio")), 0.0), 1.0),
        min(max(_safe_float(data.get("max_month_concentration")), 0.0), 1.0),
        max(-5.0, min(5.0, _safe_float(data.get("sqn")))),
    ]
    timeframe = str(period or "").upper()
    values.extend(1.0 if timeframe == item else 0.0 for item in FITNESS_TIMEFRAMES[1:])
    return tuple(values)


def _batch_logistic_gradients(
    samples: list[tuple[tuple[float, ...], int]],
    sparse_features: list[tuple[tuple[int, float], ...]],
    means: list[float],
    scales: list[float],
    coefficients: list[float],
) -> list[float]:
    """Compute the exact full-batch gradient without materializing z-scores.

    The first eight fitness features are dense metrics.  The remaining values
    are timeframe one-hot columns.  Moving the centering term into the
    intercept and accumulating gradients in raw-feature space avoids visiting
    every zero timeframe column for every sample and iteration.  Algebraically
    this is the same gradient used by the original standardized implementation.
    """
    feature_count = len(means)
    dense_count = min(_DENSE_FITNESS_FEATURES, feature_count)
    raw_coefficients = [
        coefficients[index + 1] / scales[index]
        for index in range(feature_count)
    ]
    raw_intercept = coefficients[0] - sum(
        raw_coefficients[index] * means[index]
        for index in range(feature_count)
    )
    intercept_gradient = 0.0
    raw_gradients = [0.0] * feature_count
    for (features, label), active_sparse in zip(samples, sparse_features):
        linear = raw_intercept
        for index in range(dense_count):
            linear += raw_coefficients[index] * features[index]
        for index, value in active_sparse:
            linear += raw_coefficients[index] * value
        error = label - _sigmoid(linear)
        intercept_gradient += error
        for index in range(dense_count):
            raw_gradients[index] += error * features[index]
        for index, value in active_sparse:
            raw_gradients[index] += error * value
    return [
        intercept_gradient,
        *(
            (raw_gradients[index] - means[index] * intercept_gradient) / scales[index]
            for index in range(feature_count)
        ),
    ]


@dataclass(frozen=True)
class SelectionPrediction:
    probability: float
    weight: float
    evidence: float


@dataclass(frozen=True)
class SelectionFitnessModel:
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    prior_probability: float
    training_rows: int
    positive_rows: int

    @classmethod
    def train(cls, rows: Iterable[object]) -> SelectionFitnessModel | None:
        samples: list[tuple[tuple[float, ...], int]] = []
        for row in rows:
            label = finalized_six_month_label(row)
            if label is None:
                continue
            features = fitness_features(
                _row_get(row, "score"),
                _row_get(row, "metrics_json"),
                _row_get(row, "period"),
            )
            if features is not None:
                samples.append((features, label))
        positives = sum(label for _features, label in samples)
        if len(samples) < MIN_TRAINING_ROWS or positives < MIN_POSITIVE_ROWS:
            return None

        feature_count = len(samples[0][0])
        means = [sum(features[index] for features, _label in samples) / len(samples) for index in range(feature_count)]
        scales = []
        for index, mean in enumerate(means):
            variance = sum((features[index] - mean) ** 2 for features, _label in samples) / len(samples)
            scales.append(max(math.sqrt(variance), 1e-6))
        sparse_features = [
            tuple(
                (index, features[index])
                for index in range(_DENSE_FITNESS_FEATURES, feature_count)
                if features[index] != 0.0
            )
            for features, _label in samples
        ]
        prior = positives / len(samples)
        coefficients = [_logit(prior), *([0.0] * feature_count)]

        learning_rate = 0.08
        l2 = 0.02
        for iteration in range(700):
            gradients = _batch_logistic_gradients(
                samples,
                sparse_features,
                means,
                scales,
                coefficients,
            )
            count = float(len(samples))
            max_step = 0.0
            for index in range(len(coefficients)):
                penalty = 0.0 if index == 0 else l2 * coefficients[index]
                step = learning_rate * (gradients[index] / count - penalty)
                coefficients[index] += step
                max_step = max(max_step, abs(step))
            if iteration > 100 and max_step < 1e-7:
                break

        return cls(
            means=tuple(means),
            scales=tuple(scales),
            coefficients=tuple(coefficients),
            prior_probability=prior,
            training_rows=len(samples),
            positive_rows=positives,
        )

    def predict(self, score: object, metrics_json: object, period: object) -> SelectionPrediction:
        features = fitness_features(score, metrics_json, period)
        evidence = self.training_rows / (self.training_rows + 500.0)
        if features is None:
            return SelectionPrediction(self.prior_probability, 0.0, evidence)
        standardized = tuple(
            (features[index] - self.means[index]) / self.scales[index]
            for index in range(len(features))
        )
        probability = _sigmoid(
            self.coefficients[0]
            + sum(coefficient * value for coefficient, value in zip(self.coefficients[1:], standardized))
        )
        weight = FITNESS_WEIGHT_SCALE * (_logit(probability) - _logit(self.prior_probability))
        weight = max(-FITNESS_WEIGHT_LIMIT, min(FITNESS_WEIGHT_LIMIT, weight))
        return SelectionPrediction(round(probability, 8), round(weight, 6), round(evidence, 6))
