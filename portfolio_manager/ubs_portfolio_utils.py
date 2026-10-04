"""Utilidades puras: simbolos, grupos, fechas y lotes del portafolio."""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
from typing import Iterable, Sequence

from .mt5_report import StrategyReport
from .ubs_portfolio import (
    ClosedTrade,
    DEFAULT_GROUP_LIMITS,
    PORTFOLIO_GROUP_BY_SYMBOL,
    PeriodReport,
    PortfolioEvaluation,
    PortfolioGroupLimits,
    PortfolioType,
    RobustStrategySet,
    UnusedSetInfo,
    _ascii_text,
    _portfolio_universe_group_by_symbol,
    _portfolio_universe_group_maps,
    portfolio_symbol_key,
)
from .ubs_portfolio_curves import (
    curve_increment_correlation,
)


def set_current_value(text: str, key: str, value: object) -> tuple[str, bool]:
    out: list[str] = []
    found = False
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(";"):
            lhs, rhs = line.split("=", 1)
            if lhs.strip() == key:
                if "||" in rhs:
                    parts = rhs.split("||")
                    parts[0] = str(value)
                    rhs = "||".join(parts)
                else:
                    rhs = str(value)
                line = f"{lhs}={rhs}"
                found = True
        out.append(line)
    return "\n".join(out), found


def apply_portfolio_lot_text(text: str, lot_size_step: float) -> tuple[str, int, bool]:
    step_int = max(1, int(math.ceil(lot_size_step)))
    text, _ = set_current_value(text, "Risk", 2)
    text, found_step = set_current_value(text, "LotPerBalance_step", step_int)
    return text, step_int, found_step


def execution_units_from_step(capital: float, lot_size_step: float | int | None) -> int:
    if lot_size_step is None:
        return 0
    step_int = max(1, int(math.ceil(float(lot_size_step))))
    return int(math.floor(capital / step_int)) if capital > 0 else 0


def _curve_points_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[tuple[datetime, float]]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    total = 0.0
    points: list[tuple[datetime, float]] = []
    for trade in ordered:
        total += trade.net_profit
        points.append((trade.close_time, total))
    return points


def _merge_curve_points(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> list[tuple[datetime, float]]:
    if not report_2020_2024.pnl_points_001 and not report_2025_2026.pnl_points_001:
        return []
    last_value = report_2020_2024.pnl_curve_001[-1] if report_2020_2024.pnl_curve_001 else 0.0
    points = list(report_2020_2024.pnl_points_001)
    points.extend((timestamp, last_value + value) for timestamp, value in report_2025_2026.pnl_points_001)
    return sorted(points, key=lambda item: item[0])


def _evaluate_portfolio_on_time_axis(
    active_sets: list[RobustStrategySet],
    allocations: dict[str, int],
) -> list[float]:
    events: list[tuple[datetime, str, int, float]] = []
    for strategy in active_sets:
        previous_value = 0.0
        for index, (timestamp, value) in enumerate(strategy.curve_points_2020_2026_001):
            events.append((timestamp, strategy.set_id, index, (value - previous_value) * allocations[strategy.set_id]))
            previous_value = value

    if not events:
        return [0.0]
    curve = [0.0]
    total = 0.0
    for _timestamp, _set_id, _index, change in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        total += change
        curve.append(total)
    return curve


def _metric_amount(report: StrategyReport, *keys: str) -> float | None:
    value = _first_metric(report, *keys)
    if value == "":
        return None
    return _to_float(value)


def _first_metric(report: StrategyReport, *keys: str) -> str:
    normalized = {_ascii_text(key): value for key, value in report.metrics.items()}
    for key in keys:
        value = report.metrics.get(key)
        if value:
            return value
        value = normalized.get(_ascii_text(key))
        if value:
            return value
    return ""


def _validate_curve_against_net(curve: list[float], html_net_profit: float) -> None:
    curve_net_profit = curve[-1] if curve else 0.0
    difference = abs(curve_net_profit - html_net_profit)
    tolerance = max(1.0, abs(html_net_profit) * 0.01)
    if difference > tolerance:
        raise ValueError("Parsed trade curve net profit differs from HTML net profit")


def _period_years(report: StrategyReport, period_name: str) -> tuple[int, int]:
    dates = [_parse_report_date(report.period_start), _parse_report_date(report.period_end)]
    if dates[0] and dates[1]:
        return dates[0].year, dates[1].year
    match = re.search(r"(\d{4})[_-](\d{4})", period_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    year = dates[0].year if dates[0] else 0
    return year, year


def _validate_period_order(report_2020_2024: PeriodReport, report_2025_2026: PeriodReport) -> None:
    first_end = _parse_report_date(report_2020_2024.end_date)
    second_start = _parse_report_date(report_2025_2026.start_date)
    if first_end and second_start and first_end >= second_start:
        raise ValueError("First report period must end before second report period starts")


def _parse_report_date(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_month_end(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value:
        return _parse_report_date(str(value))
    return None


def _latest_month_from_monthly(monthly: dict[int, dict[int, float]]) -> datetime | None:
    pairs = [
        (int(year), int(month))
        for year, months in monthly.items()
        for month in months
    ]
    if not pairs:
        return None
    year, month = max(pairs)
    return datetime(year, month, 1)


def _month_window(end_year: int, end_month: int, window_months: int) -> list[tuple[int, int]]:
    end_index = end_year * 12 + end_month - 1
    start_index = end_index - window_months + 1
    result: list[tuple[int, int]] = []
    for month_index in range(start_index, end_index + 1):
        year = month_index // 12
        month = month_index % 12 + 1
        result.append((year, month))
    return result


def _first_existing_report_path(row: object, *keys: str) -> Path | None:
    for key in keys:
        value = str(_row_value(row, key, default="") or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return path
    return None


def _to_float(value: str) -> float:
    text = str(value or "").split("(")[0].strip()
    text = text.replace(" ", "").replace("%", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def portfolio_display_symbol(symbol: str) -> str:
    return str(symbol or "").strip() or portfolio_symbol_key(symbol)


def portfolio_group_key(
    symbol: str,
    *,
    universe_files: Iterable[str | Path] | None = None,
) -> str:
    raw_symbol = str(symbol or "").strip().upper()
    exact_group = _portfolio_universe_group_maps(universe_files)[1].get(raw_symbol)
    if exact_group:
        return exact_group
    symbol_key = portfolio_symbol_key(symbol)
    universe_group = _portfolio_universe_group_by_symbol(universe_files).get(symbol_key)
    if universe_group:
        return universe_group
    if symbol_key in PORTFOLIO_GROUP_BY_SYMBOL:
        return PORTFOLIO_GROUP_BY_SYMBOL[symbol_key]
    if _looks_like_forex_pair(symbol_key):
        return "Forex"
    return "Other"


def group_limits_for_portfolio_type(portfolio_type: PortfolioType) -> PortfolioGroupLimits:
    return DEFAULT_GROUP_LIMITS.get(portfolio_type, DEFAULT_GROUP_LIMITS[PortfolioType.BALANCED])


def _looks_like_forex_pair(symbol: str) -> bool:
    currencies = {"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"}
    return len(symbol) == 6 and symbol[:3] in currencies and symbol[3:] in currencies


def _logical_stem(set_path: str) -> str:
    stem = Path(set_path).stem
    return re.sub(r"^robust_\d{6}_", "", stem)


def _norm_path(value: str) -> str:
    try:
        return str(Path(value)).casefold()
    except (TypeError, ValueError):
        return str(value or "").casefold()


def _row_value(row: object, *keys: str, default: object = "") -> object:
    row_keys: set[str] | None = None
    try:
        row_keys = {str(key) for key in row.keys()}  # type: ignore[attr-defined]
    except Exception:
        row_keys = None
    for key in keys:
        if row_keys is not None and key not in row_keys:
            continue
        try:
            return row[key]  # type: ignore[index]
        except Exception:
            pass
        try:
            return getattr(row, key)
        except Exception:
            pass
    return default


def _row_int(row: object, *keys: str) -> int:
    try:
        return int(_row_value(row, *keys, default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _lot_size_step(capital: float, units: int) -> float | None:
    if units <= 0:
        return None
    return float(_step_for_max_units(capital, units))


def _execution_plan_allocations(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    capital: float,
) -> tuple[dict[str, int], dict[str, int]]:
    executable = allocations.copy()
    steps: dict[str, int] = {}
    for strategy in sets:
        units = allocations.get(strategy.set_id, 0)
        if units <= 0:
            executable[strategy.set_id] = 0
            continue
        step = _step_for_max_units(capital, units)
        executable[strategy.set_id] = execution_units_from_step(capital, step)
        steps[strategy.set_id] = step
    return executable, steps


def _step_for_max_units(capital: float, units: int) -> int:
    if capital <= 0 or units <= 0:
        return 1
    return max(1, int(math.floor(capital / (units + 1))) + 1)


def _build_unused_sets(
    raw_sets: list[RobustStrategySet],
    eligible: list[RobustStrategySet],
    selected: list[RobustStrategySet],
    allocations: dict[str, int],
    min_trades_2020_2026: int,
) -> list[UnusedSetInfo]:
    eligible_ids = {strategy.set_id for strategy in eligible}
    selected_ids = {strategy.set_id for strategy in selected}
    unused: list[UnusedSetInfo] = []
    for strategy in raw_sets:
        reason = ""
        if strategy.robustness_status != "accepted":
            reason = "not_accepted"
        elif strategy.already_used:
            reason = "already_used"
        elif strategy.trades_2020_2026 < min_trades_2020_2026:
            reason = "below_min_trades"
        elif strategy.net_profit_2020_2026_001 <= 0:
            reason = "non_positive_net_profit"
        elif strategy.set_id not in eligible_ids:
            reason = "not_eligible"
        elif strategy.set_id not in selected_ids:
            reason = "not_selected_top_k"
        elif allocations.get(strategy.set_id, 0) <= 0:
            reason = "received_zero_units"
        if reason:
            unused.append(
                UnusedSetInfo(
                    set_id=strategy.set_id,
                    symbol=strategy.symbol,
                    score=score_set_for_portfolio(strategy, min_trades_2020_2026),
                    reason=reason,
                )
            )
    return sorted(unused, key=lambda item: (item.reason, -item.score, item.symbol))


def score_set_for_portfolio(
    strategy: RobustStrategySet,
    min_trades_2020_2026: int = 100,
) -> float:
    profit_score = max(strategy.net_profit_2020_2026_001, 0.0)
    pf_score = min(max(strategy.profit_factor_2020_2026, 1.0), 3.0)
    return_dd_score = max(strategy.return_dd_2020_2026, 0.1)
    trades_confidence = min(1.0, strategy.trades_2020_2026 / max(min_trades_2020_2026, 1))
    dd_penalty = max(strategy.valley_dd_2020_2026_001, 1.0)
    return float((profit_score * pf_score * return_dd_score * trades_confidence) / dd_penalty)


def _active_unit_allocations(allocations: dict[str, int]) -> dict[str, int]:
    return {str(set_id): int(units) for set_id, units in allocations.items() if int(units) > 0}


def _portfolio_active_count(allocations: dict[str, int]) -> int:
    return sum(1 for units in allocations.values() if int(units) > 0)


def _portfolio_corr_allowed(
    evaluation: PortfolioEvaluation,
    existing_portfolio_curves: Sequence[Sequence[float]] | None,
    max_portfolio_corr: float | None,
) -> bool:
    if max_portfolio_corr is None:
        return True
    curves = list(existing_portfolio_curves or [])
    if not curves:
        return True
    worst_corr = max(
        curve_increment_correlation(evaluation.equity_curve_2020_2026, curve)
        for curve in curves
    )
    return worst_corr <= max_portfolio_corr + 1e-9
