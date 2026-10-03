from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from typing import Any

from ubs.memory import AgentMemory
from ubs.models import Variant
from ubs.regression_rules import regression_points_breakdown
from ubs.score import ScoreConfig, ScoreResult


def _score_config_for_period(config: ScoreConfig, period: str, args: Any) -> ScoreConfig:
    normalized = str(period or "").strip().upper()
    if normalized == "W1":
        return replace(config, min_trades=int(args.regression_min_trades_w1))
    if normalized in {"MN", "MN1"}:
        return replace(config, min_trades=int(args.regression_min_trades_mn))
    return config


def _details_payload(
    status: str,
    result: ScoreResult | None,
    args: Any,
    *,
    reasons: tuple[str, ...] = (),
    actual_dates: tuple[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> tuple[str, float]:
    reason_items = reasons or (tuple(result.reasons) if result is not None else ())
    points = regression_points_breakdown(
        status,
        reason_items,
        positive_points=float(args.regression_positive_points),
        negative_points=float(args.regression_negative_points),
    )
    payload: dict[str, object] = {
        "accepted": status == "accepted",
        "reasons": list(reason_items),
        "model": "1_minute_ohlc",
        "expected_from_date": str(args.regression_from_date).strip(),
        "expected_to_date": str(args.regression_to_date).strip(),
        "actual_from_date": actual_dates[0] if actual_dates else "",
        "actual_to_date": actual_dates[1] if actual_dates else "",
        "points": points,
    }
    if metadata:
        payload.update(metadata)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True), float(points["applied"])


def _record_technical(
    memory: AgentMemory,
    args: Any,
    *,
    candidate_id: int,
    run_id: int,
    status: str,
    report: Path | None,
    result: ScoreResult | None = None,
    reasons: tuple[str, ...] = (),
    actual_dates: tuple[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> str:
    details_json, points_applied = _details_payload(
        status,
        result,
        args,
        reasons=reasons,
        actual_dates=actual_dates,
        metadata=metadata,
    )
    memory.record_candidate_regression(
        candidate_id,
        run_id,
        status,
        result,
        report,
        details_json,
        args.regression_from_date,
        args.regression_to_date,
        args.regression_positive_points,
        args.regression_negative_points,
        points_applied,
    )
    return status


def _base_metrics_from_row(row: sqlite3.Row | None) -> dict[str, object] | None:
    """Parse the candidate's base-window metrics for degradation comparison."""

    if row is None:
        return None
    try:
        raw = row["metrics_json"]
    except (KeyError, IndexError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _watchdog_snapshot_metadata(snapshot: Path, variant: Variant) -> dict[str, object]:
    metadata: dict[str, object] = {"watchdog_snapshot": str(snapshot)}
    try:
        text = snapshot.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        metadata["watchdog_snapshot_error"] = str(exc)
        return metadata

    symbol = str(variant.target_symbol or "").strip().lower()
    lines = text.splitlines()
    old_tick_lines = sum(
        1
        for line in lines
        if "old tick" in line.lower() and (not symbol or symbol in line.lower())
    )
    gmt_url_error_lines = sum(
        1 for line in lines if "error when reading gmt url" in line.lower()
    )
    if old_tick_lines:
        metadata["history_signal"] = "old_tick_seen"
        metadata["old_tick_lines"] = old_tick_lines
    if gmt_url_error_lines:
        metadata["gmt_url_error_lines"] = gmt_url_error_lines
    return metadata
