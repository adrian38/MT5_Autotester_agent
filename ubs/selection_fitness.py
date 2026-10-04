"""Etiquetas terminales y modelo de fitness de la seleccion UBS."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Iterable

from ubs.selection_common import (
    DESCENDANT_FITNESS_PRIOR_STRENGTH,
    FITNESS_TARGET_FINAL_TICK_6M,
    FITNESS_TARGET_ROBUSTNESS,
    FITNESS_TARGETS,
    FITNESS_TIMEFRAMES,
    FITNESS_WEIGHT_LIMIT,
    FITNESS_WEIGHT_SCALE,
    MIN_POSITIVE_ROWS,
    MIN_TRAINING_ROWS,
    _DENSE_FITNESS_FEATURES,
    _logit,
    _metrics,
    _row_get,
    _safe_float,
    _sigmoid,
)


def finalized_generation_six_month_label(row: object) -> int | None:
    """Label the whole generation funnel, including valid base-stage failures."""

    status = str(_row_get(row, "status", "")).lower()
    if status in {"rejected", "no_trades"}:
        return 0
    if status != "accepted":
        return None
    return finalized_six_month_label(row)


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


def finalized_robustness_label(row: object) -> int | None:
    """Return the statistical OOS label without poisoning it with technical failures."""

    if str(_row_get(row, "status", "")).lower() != "accepted":
        return None
    robust = str(_row_get(row, "robust_status", "")).lower()
    if robust == "accepted":
        return 1
    if robust in {"rejected", "no_trades"}:
        return 0
    return None


def finalized_fitness_label(row: object, target: str) -> int | None:
    if target == FITNESS_TARGET_ROBUSTNESS:
        return finalized_robustness_label(row)
    if target == FITNESS_TARGET_FINAL_TICK_6M:
        return finalized_six_month_label(row)
    raise ValueError(f"Objetivo de fitness desconocido: {target}")


def _group_outcomes_by_root_source(materialized: list) -> dict[tuple[int, str], list[int]]:
    """Agrupa los desenlaces 6M por (run, fuente raiz) siguiendo la cadena de semillas."""
    parent_paths = {
        (
            int(_row_get(row, "run_id", 0) or 0),
            str(_row_get(row, "set_path", "") or ""),
        ): str(_row_get(row, "seed_path", "") or "")
        for row in materialized
        if str(_row_get(row, "set_path", "") or "")
        and str(_row_get(row, "seed_path", "") or "")
    }

    def root_path(run_id: int, seed_path: str) -> str:
        current = seed_path
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            parent = parent_paths.get((run_id, current))
            if not parent:
                break
            current = parent
        return current

    grouped: dict[tuple[int, str], list[int]] = defaultdict(list)
    for row in materialized:
        outcome = finalized_generation_six_month_label(row)
        if outcome is None:
            continue
        run_id = int(_row_get(row, "run_id", 0) or 0)
        key = (
            run_id,
            root_path(run_id, str(_row_get(row, "seed_path", "") or "")),
        )
        if key[1]:
            grouped[key].append(outcome)
    return grouped


def descendant_fitness_predictions(
    rows: Iterable[object],
    paths: Iterable[str],
    *,
    prior_strength: float = DESCENDANT_FITNESS_PRIOR_STRENGTH,
) -> dict[str, SelectionPrediction]:
    """Estimate whether each root source produces an accepted FT 6M descendant.

    A later generation uses the previous candidate ``set_path`` as its
    ``seed_path``. Follow that chain back to the original source so a terminal
    success teaches the reusable seed instead of a run-local generated path.
    """

    materialized = list(rows)
    requested = list(dict.fromkeys(str(path) for path in paths))
    grouped = _group_outcomes_by_root_source(materialized)

    source_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    total_trials = 0
    total_successes = 0
    for (_run_id, path), outcomes in grouped.items():
        success = int(1 in outcomes)
        source_counts[path][0] += 1
        source_counts[path][1] += success
        total_trials += 1
        total_successes += success

    if total_trials <= 0:
        return {path: SelectionPrediction(0.0, 0.0, 0.0) for path in requested}
    prior = total_successes / total_trials
    strength = max(float(prior_strength), 1e-9)
    result: dict[str, SelectionPrediction] = {}
    for path in requested:
        trials, successes = source_counts.get(path, (0, 0))
        probability = (successes + prior * strength) / (trials + strength)
        evidence = trials / (trials + strength)
        weight = FITNESS_WEIGHT_SCALE * (_logit(probability) - _logit(prior)) * evidence
        weight = max(-FITNESS_WEIGHT_LIMIT, min(FITNESS_WEIGHT_LIMIT, weight))
        result[path] = SelectionPrediction(
            round(probability, 8),
            round(weight, 6),
            round(evidence, 6),
        )
    return result


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


def _training_samples(rows: Iterable[object], target: str) -> list[tuple[tuple[float, ...], int]]:
    samples: list[tuple[tuple[float, ...], int]] = []
    for row in rows:
        label = finalized_fitness_label(row, target)
        if label is None:
            continue
        features = fitness_features(
            _row_get(row, "score"),
            _row_get(row, "metrics_json"),
            _row_get(row, "period"),
        )
        if features is not None:
            samples.append((features, label))
    return samples


def _fit_coefficients(samples, sparse_features, means, scales, coefficients: list[float]) -> None:
    """Descenso de gradiente en sitio, con el mismo paso, L2 y parada que antes."""
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
    target: str

    @classmethod
    def train(
        cls,
        rows: Iterable[object],
        *,
        target: str = FITNESS_TARGET_FINAL_TICK_6M,
    ) -> SelectionFitnessModel | None:
        if target not in FITNESS_TARGETS:
            raise ValueError(f"Objetivo de fitness desconocido: {target}")
        samples = _training_samples(rows, target)
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

        _fit_coefficients(samples, sparse_features, means, scales, coefficients)

        return cls(
            means=tuple(means),
            scales=tuple(scales),
            coefficients=tuple(coefficients),
            prior_probability=prior,
            training_rows=len(samples),
            positive_rows=positives,
            target=target,
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
