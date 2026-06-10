from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
import json


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
}

FINAL_TICK_REASON_PENALTIES = {
    "profit_factor": 45.0,
    "drawdown_pct": 55.0,
    "trades": 45.0,
    "history_quality": 60.0,
}


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
    status = row_text(row, "final_tick_status").lower()
    if status == "accepted":
        return DEFAULT_FINAL_TICK_ACCEPTED_BONUS
    if status == "rejected":
        reasons = metric_reasons(row_get(row, "final_tick_similarity_json"))
        return DEFAULT_FINAL_TICK_REJECTED_PENALTY - reason_penalty(reasons, FINAL_TICK_REASON_PENALTIES)
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
