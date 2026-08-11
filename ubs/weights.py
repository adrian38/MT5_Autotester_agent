from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import json
import math

from ubs.regression_rules import REGRESSION_FAILURE_STATUSES


DEFAULT_ROBUST_POSITIVE_BONUS = 70.0
DEFAULT_ROBUST_NEGATIVE_BONUS = -70.0
DEFAULT_FINAL_TICK_ACCEPTED_BONUS = 120.0
DEFAULT_FINAL_TICK_REJECTED_PENALTY = -160.0

ASSET_ACCEPTED_BONUS = 20.0
TIMEFRAME_ACCEPTED_BONUS = 15.0
MUTATION_ACCEPTED_BONUS = 15.0

REJECTED_BASE_PENALTY = 50.0
NO_TRADES_WEIGHT = -40.0
WEIGHT_SHRINKAGE_K = 20.0
SEED_WEIGHT_SCALE = 1.0

# Probability feedback replaces the old unbounded additive score when the
# agent chooses assets, timeframes and mutation keys.  The legacy row utility
# below is intentionally kept for audit/backwards compatibility.
STAGE_PRIOR_STRENGTH = 20.0
RELATIVE_SCORE_SCALE = 10.0
RELATIVE_SCORE_LIMIT = 40.0
MUTATION_SCORE_FULL_STRENGTH = 5.0
DEFAULT_STAGE_PRIORS = {
    "base": 0.50,
    "robust": 0.50,
    "probe": 0.50,
    "six_month": 0.05,
    # Neutral until the first backward-validation results exist. Multiplying
    # by 1.0 preserves every pre-regression probability and relative weight.
    "regression": 1.0,
}

TIMEFRAME_PATCH_KEYS = frozenset({
    "ST1_Timeframe",
    "VolTimeframe",
    "Entry_Timing",
    "ATR_Timeframe",
})

REJECTED_REASON_PENALTIES = {
    "net_profit": 40.0,
    "profit_factor": 25.0,
    "trades": 30.0,
    "drawdown_pct": 35.0,
    "recovery_factor": 25.0,
    "positive_month_ratio": 15.0,
}

ROBUST_REASON_PENALTIES = {
    "net_profit": 50.0,
    "profit_factor": 35.0,
    "trades": 35.0,
    "drawdown_pct": 45.0,
    "recovery_factor": 35.0,
    "positive_month_ratio": 20.0,
    "degradation_net": 30.0,
    "degradation_profit_factor": 25.0,
    "degradation_recovery": 20.0,
    "degradation_drawdown": 30.0,
    "degradation_trade_rate": 20.0,
    "generalization_residual_profit": 55.0,
    "generalization_month_breadth": 35.0,
    "generalization_stability": 30.0,
    "generalization_stability_retention": 25.0,
    "generalization_bootstrap_net": 50.0,
    "generalization_bootstrap_pf": 50.0,
}

FINAL_TICK_REASON_PENALTIES = {
    "profit_factor_floor": 55.0,
    "profit_factor": 45.0,
    "drawdown_pct": 55.0,
    "trades": 45.0,
    "ohlc_trades": 45.0,
    "history_quality": 60.0,
}


@dataclass(frozen=True)
class StageEvidence:
    successes: float = 0.0
    trials: float = 0.0


@dataclass(frozen=True)
class FeedbackSignal:
    """Smoothed end-to-end probability and its relative selection score."""

    score: float
    probability: float
    confidence: float
    groups: int
    final_trials: float
    stage_probabilities: dict[str, float]
    regression_trials: float = 0.0

    @property
    def effective_score(self) -> float:
        """Selection score discounted by the amount of supporting evidence."""

        confidence = min(max(float(self.confidence), 0.0), 1.0)
        return round(float(self.score) * confidence, 6)


def row_get(row: object, key: str, default: object = None) -> object:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return default


def row_text(row: object, key: str, default: str = "") -> str:
    value = row_get(row, key, default)
    return str(value if value is not None else default).strip()


def row_float(row: object, key: str, default: float = 0.0) -> float:
    value = row_get(row, key, default)
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def metric_reasons(metrics_json: object) -> tuple[str, ...]:
    try:
        data = json.loads(str(metrics_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    reasons = data.get("reasons") if isinstance(data, dict) else ()
    if isinstance(reasons, str):
        return (reasons,)
    if isinstance(reasons, Iterable):
        return tuple(str(reason) for reason in reasons if str(reason))
    return ()


def row_stage_outcome(row: object, stage: str) -> tuple[bool, float]:
    """Return ``(is_trial, success)`` for one lifecycle stage.

    Missing reports and retryable technical states do not become statistical
    failures.  ``pending_ohlc_trades`` is probe-eligible by design because the
    longer six-month window supplies the missing sample.
    """

    if stage == "base":
        status = row_text(row, "status").lower()
        if status in {"accepted", "rejected", "no_trades"}:
            return True, 1.0 if status == "accepted" else 0.0
        return False, 0.0
    if stage == "robust":
        status = row_text(row, "robust_status").lower()
        if status in {"accepted", "rejected", "no_trades"}:
            return True, 1.0 if status == "accepted" else 0.0
        return False, 0.0
    if stage == "probe":
        status = row_text(row, "final_tick_status").lower()
        if status in {"accepted", "rejected", "pending_ohlc_trades"}:
            return True, 1.0 if status in {"accepted", "pending_ohlc_trades"} else 0.0
        return False, 0.0
    if stage == "six_month":
        status = row_text(row, "final_tick_6m_status").lower()
        if status in {"accepted", "rejected"}:
            return True, 1.0 if status == "accepted" else 0.0
        return False, 0.0
    if stage == "regression":
        status = row_text(row, "regression_status").lower()
        if status == "accepted":
            return True, 1.0
        if status in REGRESSION_FAILURE_STATUSES:
            return True, 0.0
        return False, 0.0
    raise ValueError(f"Etapa de feedback desconocida: {stage}")


def grouped_stage_evidence(grouped_rows: Mapping[object, Iterable[object]]) -> dict[str, StageEvidence]:
    """Give every correlated source group at most one trial per stage."""

    totals = {stage: [0.0, 0.0] for stage in DEFAULT_STAGE_PRIORS}
    for rows in grouped_rows.values():
        items = list(rows)
        for stage in totals:
            outcomes = [success for row in items for trial, success in [row_stage_outcome(row, stage)] if trial]
            if not outcomes:
                continue
            totals[stage][0] += sum(outcomes) / len(outcomes)
            totals[stage][1] += 1.0
    return {
        stage: StageEvidence(successes=values[0], trials=values[1])
        for stage, values in totals.items()
    }


def _posterior_probability(evidence: StageEvidence, prior: float, strength: float) -> float:
    return (evidence.successes + prior * strength) / (evidence.trials + strength)


def _logit(value: float) -> float:
    bounded = min(max(float(value), 1e-9), 1.0 - 1e-9)
    return math.log(bounded / (1.0 - bounded))


def probability_feedback_signals(
    grouped_by_key: Mapping[str, Mapping[object, Iterable[object]]],
    global_groups: Mapping[object, Iterable[object]],
    *,
    prior_strength: float = STAGE_PRIOR_STRENGTH,
    normalize_keys: bool = True,
) -> dict[str, FeedbackSignal]:
    """Build bounded, neutral-centred feedback from lifecycle probabilities."""

    global_evidence = grouped_stage_evidence(global_groups)
    priors: dict[str, float] = {}
    for stage, fallback in DEFAULT_STAGE_PRIORS.items():
        evidence = global_evidence[stage]
        observed = evidence.successes / evidence.trials if evidence.trials else fallback
        if stage == "regression" and evidence.trials:
            observed = min(0.95, max(0.05, observed))
        priors[stage] = observed
    global_probability = math.prod(priors.values())

    result: dict[str, FeedbackSignal] = {}
    for raw_key, groups in grouped_by_key.items():
        key = str(raw_key).strip()
        if normalize_keys:
            key = key.upper()
        if not key or not groups:
            continue
        evidence = grouped_stage_evidence(groups)
        stage_probabilities = {
            stage: _posterior_probability(evidence[stage], priors[stage], prior_strength)
            for stage in priors
        }
        probability = math.prod(stage_probabilities.values())
        score = RELATIVE_SCORE_SCALE * (_logit(probability) - _logit(global_probability))
        score = max(-RELATIVE_SCORE_LIMIT, min(RELATIVE_SCORE_LIMIT, score))
        weighted_trials = (
            evidence["base"].trials
            + 2.0 * evidence["robust"].trials
            + 3.0 * evidence["probe"].trials
            + 4.0 * evidence["six_month"].trials
            + 5.0 * evidence["regression"].trials
        )
        confidence_stages = 5.0 if global_evidence["regression"].trials else 4.0
        confidence = weighted_trials / (weighted_trials + prior_strength * confidence_stages)
        result[key] = FeedbackSignal(
            score=round(score, 6),
            probability=round(probability, 8),
            confidence=round(confidence, 6),
            groups=len(groups),
            final_trials=evidence["six_month"].trials,
            stage_probabilities={stage: round(value, 8) for stage, value in stage_probabilities.items()},
            regression_trials=evidence["regression"].trials,
        )
    return result


def percentile_multipliers(
    feedback: Mapping[str, float],
    keys: Iterable[str],
    *,
    minimum: float = 0.5,
    maximum: float = 1.5,
) -> dict[str, float]:
    """Map relative ordering to bounded mutation multipliers.

    Missing feedback is neutral. Ties receive the same averaged percentile, so
    an all-equal history produces multiplier 1.0 instead of an arbitrary rank.
    """

    requested = list(dict.fromkeys(str(key) for key in keys))
    known = [(key, float(feedback[key])) for key in requested if key in feedback]
    if not known:
        return {key: 1.0 for key in requested}
    ordered = sorted(known, key=lambda item: (item[1], item[0]))
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + end - 1) / 2.0
        percentile = 0.5 if len(ordered) == 1 else average_rank / (len(ordered) - 1)
        multiplier = minimum + percentile * (maximum - minimum)
        for key, _value in ordered[index:end]:
            ranks[key] = multiplier
        index = end
    return {key: ranks.get(key, 1.0) for key in requested}


def score_aware_percentile_multipliers(
    feedback: Mapping[str, float],
    keys: Iterable[str],
    *,
    minimum: float = 0.5,
    maximum: float = 1.5,
    full_strength: float = MUTATION_SCORE_FULL_STRENGTH,
) -> dict[str, float]:
    """Rank mutation keys without amplifying an effectively neutral signal.

    ``FeedbackSignal.effective_score`` already discounts sparse evidence.  A
    pure percentile would nevertheless turn a score close to zero into the
    strongest possible reward or penalty.  Blend that rank back towards 1.0
    until the effective score is large enough to justify the full effect.
    """

    requested = list(dict.fromkeys(str(key) for key in keys))
    ranked = percentile_multipliers(feedback, requested, minimum=minimum, maximum=maximum)
    scale = max(float(full_strength), 1e-9)
    result: dict[str, float] = {}
    for key in requested:
        try:
            score = abs(float(feedback[key]))
        except (KeyError, TypeError, ValueError):
            result[key] = 1.0
            continue
        strength = min(1.0, score / scale)
        result[key] = 1.0 + (ranked[key] - 1.0) * strength
    return result


def reason_penalty(reasons: Iterable[str], penalties: Mapping[str, float]) -> float:
    return sum(float(penalties.get(str(reason), 15.0)) for reason in reasons)


def robust_bonus(row: object) -> float:
    status = row_text(row, "robust_status").lower()
    if status == "accepted":
        return row_float(row, "robust_positive_bonus", DEFAULT_ROBUST_POSITIVE_BONUS)
    if status == "rejected":
        return row_float(row, "robust_negative_bonus", DEFAULT_ROBUST_NEGATIVE_BONUS)
    return 0.0


def final_tick_bonus(row: object) -> float:
    value = 0.0
    status = row_text(row, "final_tick_status").lower()
    if status == "rejected":
        reasons = metric_reasons(row_get(row, "final_tick_similarity_json"))
        value += DEFAULT_FINAL_TICK_REJECTED_PENALTY - reason_penalty(reasons, FINAL_TICK_REASON_PENALTIES)

    status_6m = row_text(row, "final_tick_6m_status").lower()
    if status_6m == "accepted":
        value += DEFAULT_FINAL_TICK_ACCEPTED_BONUS
    elif status_6m == "rejected":
        reasons = metric_reasons(row_get(row, "final_tick_6m_similarity_json"))
        value += DEFAULT_FINAL_TICK_REJECTED_PENALTY - reason_penalty(reasons, FINAL_TICK_REASON_PENALTIES)
    return value


def final_tick_rejected_ceiling(row: object) -> float | None:
    ceilings: list[float] = []
    status = row_text(row, "final_tick_status").lower()
    if status == "rejected":
        reasons = metric_reasons(row_get(row, "final_tick_similarity_json"))
        ceilings.append(DEFAULT_FINAL_TICK_REJECTED_PENALTY - reason_penalty(reasons, FINAL_TICK_REASON_PENALTIES))

    status_6m = row_text(row, "final_tick_6m_status").lower()
    if status_6m == "rejected":
        reasons = metric_reasons(row_get(row, "final_tick_6m_similarity_json"))
        ceilings.append(DEFAULT_FINAL_TICK_REJECTED_PENALTY - reason_penalty(reasons, FINAL_TICK_REASON_PENALTIES))
    return min(ceilings) if ceilings else None


def regression_adjustment(row: object) -> float:
    status = row_text(row, "regression_status").lower()
    if status == "accepted" or status in REGRESSION_FAILURE_STATUSES:
        return row_float(row, "regression_points_applied", 0.0)
    return 0.0


def feedback_weight(row: object, *, accepted_bonus: float) -> float | None:
    status = row_text(row, "status").lower()
    if status == "no_trades":
        # Solo aporta peso un no_trades con reporte real verificado;
        # filas manuales o huerfanas sin report_path no penalizan.
        if not row_text(row, "report_path"):
            return None
        return NO_TRADES_WEIGHT
    if status not in {"accepted", "rejected"}:
        return None
    if row_get(row, "score") in (None, ""):
        return None

    score = row_float(row, "score", 0.0)
    if status == "accepted":
        value = score + accepted_bonus
    else:
        reasons = metric_reasons(row_get(row, "metrics_json"))
        reasons_penalty = reason_penalty(reasons, REJECTED_REASON_PENALTIES)
        value = score - REJECTED_BASE_PENALTY - reasons_penalty
        max_rejected_weight = -reasons_penalty if reasons else -REJECTED_BASE_PENALTY
        value = min(value, max_rejected_weight)

    robust_status = row_text(row, "robust_status").lower()
    if robust_status == "accepted":
        value += robust_bonus(row)
    elif robust_status == "rejected":
        reasons = metric_reasons(row_get(row, "robust_metrics_json"))
        value += robust_bonus(row) - reason_penalty(reasons, ROBUST_REASON_PENALTIES)
    value += final_tick_bonus(row)
    final_tick_ceiling = final_tick_rejected_ceiling(row)
    if final_tick_ceiling is not None:
        value = min(value, final_tick_ceiling)
    regression_value = regression_adjustment(row)
    value += regression_value
    if regression_value < 0:
        value = min(value, regression_value)
    return value


def shrunk_mean(values: Iterable[float], *, k: float = WEIGHT_SHRINKAGE_K, prior: float = 0.0) -> float | None:
    items = [float(value) for value in values]
    if not items:
        return None
    mean = sum(items) / len(items)
    weight = len(items) / (len(items) + k)
    return mean * weight + prior * (1.0 - weight)


def grouped_shrunk_mean(
    grouped_values: Mapping[object, Iterable[float]],
    *,
    k: float = WEIGHT_SHRINKAGE_K,
    prior: float = 0.0,
) -> float | None:
    group_means = []
    for values in grouped_values.values():
        items = [float(value) for value in values]
        if items:
            group_means.append(sum(items) / len(items))
    return shrunk_mean(group_means, k=k, prior=prior)


def aggregate_feedback(
    rows: Iterable[object],
    *,
    key_fn: Callable[[object], str],
    group_fn: Callable[[object], object],
    accepted_bonus: float,
    k: float = WEIGHT_SHRINKAGE_K,
) -> dict[str, float]:
    grouped: dict[str, dict[object, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = key_fn(row).strip().upper()
        if not key:
            continue
        value = feedback_weight(row, accepted_bonus=accepted_bonus)
        if value is None:
            continue
        grouped[key][group_fn(row)].append(value)
    result: dict[str, float] = {}
    for key, groups in grouped.items():
        value = grouped_shrunk_mean(groups, k=k)
        if value is not None:
            result[key] = value
    return result


def candidate_group_key(row: object, *extra: object) -> tuple[object, ...]:
    return (
        row_get(row, "run_id", "candidate"),
        row_text(row, "family"),
        row_text(row, "seed_path"),
        row_text(row, "target_symbol") or row_text(row, "symbol"),
        row_text(row, "period"),
        *extra,
    )


def seed_group_key(row: object, *extra: object) -> tuple[object, ...]:
    return ("seed", row_text(row, "seed_path") or row_text(row, "symbol"), row_text(row, "period"), *extra)
