from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Iterable, Mapping


from run_tests import KNOWN_TIMEFRAMES

FITNESS_TIMEFRAMES = KNOWN_TIMEFRAMES
MIN_TRAINING_ROWS = 300
MIN_POSITIVE_ROWS = 30
FITNESS_WEIGHT_SCALE = 10.0
FITNESS_WEIGHT_LIMIT = 15.0


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
        standardized = [
            tuple((features[index] - means[index]) / scales[index] for index in range(feature_count))
            for features, _label in samples
        ]
        labels = [label for _features, label in samples]
        prior = positives / len(samples)
        coefficients = [_logit(prior), *([0.0] * feature_count)]

        learning_rate = 0.08
        l2 = 0.02
        for iteration in range(700):
            gradients = [0.0] * len(coefficients)
            for features, label in zip(standardized, labels):
                prediction = _sigmoid(coefficients[0] + sum(c * value for c, value in zip(coefficients[1:], features)))
                error = label - prediction
                gradients[0] += error
                for index, value in enumerate(features, start=1):
                    gradients[index] += error * value
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

