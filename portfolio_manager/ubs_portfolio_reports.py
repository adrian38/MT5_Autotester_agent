"""Lectura de informes MT5 y construccion de los sets robustos."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Sequence

from .mt5_report import StrategyReport, parse_report
from ubs.path_utils import resolve_workspace_path
from .ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    RobustStrategySet,
    _normalize_symbol,
)
from .ubs_portfolio_curves import (
    calc_point_dd,
    calc_valley_dd,
    merge_accumulated_curves,
)
from .ubs_portfolio_utils import (
    _curve_points_from_closed_trades,
    _merge_curve_points,
    _metric_amount,
    _period_years,
    _row_value,
    _validate_curve_against_net,
    _validate_period_order,
)


def extract_period_info(text: str) -> tuple[str, str, str]:
    match = re.search(
        r"([A-Z0-9]+)\s+\((\d{4}\.\d{2}\.\d{2})\s+-\s+(\d{4}\.\d{2}\.\d{2})\)",
        text,
    )
    if not match:
        raise ValueError("Period info not found")
    return match.group(1), match.group(2), match.group(3)


def build_equity_curve_from_closed_trades(closed_trades: list[ClosedTrade]) -> list[float]:
    ordered = sorted(closed_trades, key=lambda trade: trade.close_time)
    curve = [0.0]
    total = 0.0
    for trade in ordered:
        total += trade.net_profit
        curve.append(total)
    return curve


def parse_mt5_html_report(html_path: str | Path, period_name: str) -> PeriodReport:
    report = parse_report(Path(html_path))
    return period_report_from_strategy_report(report, period_name)


def period_report_from_strategy_report(report: StrategyReport, period_name: str) -> PeriodReport:
    closed_trades = [
        ClosedTrade(
            open_time=trade.open_time,
            close_time=trade.close_time,
            symbol=report.symbol,
            volume=trade.size,
            profit=trade.profit_loss,
            open_price=trade.open_price,
            close_price=trade.close_price,
        )
        for trade in report.trades
    ]
    curve = build_equity_curve_from_closed_trades(closed_trades)
    pnl_points = _curve_points_from_closed_trades(closed_trades)
    metric_net = _metric_amount(report, "Total Net Profit", "Beneficio Neto")
    net_profit = curve[-1] if metric_net is None else metric_net
    _validate_curve_against_net(curve, net_profit)

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    gross_profit = _metric_amount(report, "Gross Profit", "Beneficio Bruto")
    gross_loss_amount = _metric_amount(report, "Gross Loss", "Perdidas Brutas", "Perdidas Brutas")
    if gross_profit is None or gross_loss_amount is None:
        profits = [trade.net_profit for trade in closed_trades]
        gross_profit = sum(value for value in profits if value > 0)
        gross_loss = sum(value for value in profits if value < 0)
    else:
        gross_loss = -abs(gross_loss_amount)
    profit_factor = _metric_amount(report, "Profit Factor", "Factor de Beneficio")
    if profit_factor is None:
        profit_factor = gross_profit / abs(gross_loss) if gross_loss else (float("inf") if gross_profit else 0.0)

    start_year, end_year = _period_years(report, period_name)
    return PeriodReport(
        period_name=period_name,
        start_year=start_year,
        end_year=end_year,
        symbol=report.symbol,
        timeframe=report.timeframe,
        pnl_curve_001=curve,
        net_profit_001=net_profit,
        valley_dd_001=valley_dd,
        point_dd_001=point_dd,
        profit_factor=float(profit_factor),
        return_dd_ratio=net_profit / max(valley_dd, 1.0),
        trades=len(closed_trades),
        gross_profit=float(gross_profit) if gross_profit is not None else None,
        gross_loss=float(gross_loss) if gross_loss is not None else None,
        closed_trades=closed_trades,
        pnl_points_001=pnl_points,
        source_path=str(report.path),
        start_date=report.period_start,
        end_date=report.period_end,
    )


def calc_combined_profit_factor(
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
) -> float:
    if (
        report_2020_2024.gross_profit is not None
        and report_2020_2024.gross_loss is not None
        and report_2025_2026.gross_profit is not None
        and report_2025_2026.gross_loss is not None
    ):
        gross_profit = report_2020_2024.gross_profit + report_2025_2026.gross_profit
        gross_loss = report_2020_2024.gross_loss + report_2025_2026.gross_loss
        if gross_loss == 0:
            return float("inf")
        return gross_profit / abs(gross_loss)
    return min(report_2020_2024.profit_factor, report_2025_2026.profit_factor)


def build_robust_strategy_set(
    set_id: str,
    candidate_id: str,
    symbol: str,
    timeframe: str | None,
    strategy_family: str | None,
    robustness_status: str,
    already_used: bool,
    report_2020_2024: PeriodReport,
    report_2025_2026: PeriodReport,
    *,
    set_path: str = "",
    is_report_path: str = "",
    oos_report_path: str = "",
) -> RobustStrategySet:
    if _normalize_symbol(report_2020_2024.symbol) != _normalize_symbol(report_2025_2026.symbol):
        raise ValueError("Cannot merge reports with different symbols")
    _validate_period_order(report_2020_2024, report_2025_2026)

    curve_2020_2026_001 = merge_accumulated_curves(
        report_2020_2024.pnl_curve_001,
        report_2025_2026.pnl_curve_001,
    )
    curve_points = _merge_curve_points(report_2020_2024, report_2025_2026)
    if curve_points:
        curve_2020_2026_001 = [0.0] + [value for _time, value in curve_points]

    net_profit_2020_2026_001 = curve_2020_2026_001[-1]
    valley_dd_2020_2026_001 = calc_valley_dd(curve_2020_2026_001)
    point_dd_2020_2026_001 = calc_point_dd(curve_2020_2026_001)
    return_dd_2020_2026 = net_profit_2020_2026_001 / max(valley_dd_2020_2026_001, 1.0)
    trades_2020_2026 = report_2020_2024.trades + report_2025_2026.trades
    profit_factor_2020_2026 = calc_combined_profit_factor(report_2020_2024, report_2025_2026)

    return RobustStrategySet(
        set_id=str(set_id),
        candidate_id=str(candidate_id),
        symbol=_normalize_symbol(symbol or report_2020_2024.symbol),
        timeframe=timeframe or report_2020_2024.timeframe,
        strategy_family=strategy_family,
        robustness_status=robustness_status,
        already_used=already_used,
        report_2020_2024=report_2020_2024,
        report_2025_2026=report_2025_2026,
        curve_2020_2026_001=curve_2020_2026_001,
        net_profit_2020_2026_001=net_profit_2020_2026_001,
        valley_dd_2020_2026_001=valley_dd_2020_2026_001,
        point_dd_2020_2026_001=point_dd_2020_2026_001,
        profit_factor_2020_2026=profit_factor_2020_2026,
        return_dd_2020_2026=return_dd_2020_2026,
        trades_2020_2026=trades_2020_2026,
        set_path=set_path,
        is_report_path=is_report_path,
        oos_report_path=oos_report_path,
        curve_points_2020_2026_001=curve_points,
    )


def slice_strategy_set_to_month(
    strategy: RobustStrategySet,
    target_month: int,
) -> RobustStrategySet:
    """Return the strategy curve restricted to one calendar month across all years.

    The source points are accumulated trade P/L values.  We first recover each
    closed-trade increment, then keep only trades whose close timestamp belongs
    to ``target_month``.  Concatenating those increments chronologically gives a
    seasonal history such as every January available in the base + OOS reports.
    """
    if not 1 <= int(target_month) <= 12:
        raise ValueError("target_month must be between 1 and 12")
    if not strategy.curve_points_2020_2026_001:
        raise ValueError("Strategy has no timestamped trade curve")

    selected: list[tuple[datetime, float]] = []
    previous_value = 0.0
    for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
        increment = float(accumulated_value) - previous_value
        previous_value = float(accumulated_value)
        if timestamp.month == int(target_month):
            selected.append((timestamp, increment))

    total = 0.0
    curve = [0.0]
    points: list[tuple[datetime, float]] = []
    pnl_by_year: dict[int, float] = {}
    gross_profit = 0.0
    gross_loss = 0.0
    for timestamp, increment in selected:
        total += increment
        curve.append(total)
        points.append((timestamp, total))
        pnl_by_year[timestamp.year] = pnl_by_year.get(timestamp.year, 0.0) + increment
        if increment >= 0:
            gross_profit += increment
        else:
            gross_loss += increment

    valley_dd = calc_valley_dd(curve)
    point_dd = calc_point_dd(curve)
    if gross_loss < 0:
        profit_factor = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    years = tuple(sorted(pnl_by_year))
    positive_years = tuple(year for year in years if pnl_by_year[year] > 0)

    return RobustStrategySet(
        set_id=strategy.set_id,
        candidate_id=strategy.candidate_id,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        strategy_family=strategy.strategy_family,
        robustness_status=strategy.robustness_status,
        already_used=strategy.already_used,
        report_2020_2024=strategy.report_2020_2024,
        report_2025_2026=strategy.report_2025_2026,
        curve_2020_2026_001=curve,
        net_profit_2020_2026_001=total,
        valley_dd_2020_2026_001=valley_dd,
        point_dd_2020_2026_001=point_dd,
        profit_factor_2020_2026=profit_factor,
        return_dd_2020_2026=total / max(valley_dd, 1.0),
        trades_2020_2026=len(selected),
        set_path=strategy.set_path,
        is_report_path=strategy.is_report_path,
        oos_report_path=strategy.oos_report_path,
        curve_points_2020_2026_001=points,
        target_month=int(target_month),
        month_years=years,
        positive_month_years=positive_years,
    )


def slice_strategy_sets_to_month(
    strategies: Sequence[RobustStrategySet],
    target_month: int,
) -> tuple[list[RobustStrategySet], list[str]]:
    """Build seasonal curves and report candidates without timestamped history."""
    sliced: list[RobustStrategySet] = []
    skipped = 0
    for strategy in strategies:
        try:
            sliced.append(slice_strategy_set_to_month(strategy, target_month))
        except ValueError:
            skipped += 1
    warnings = []
    if skipped:
        warnings.append(
            f"{skipped} candidato(s) omitido(s): no tienen curva historica con fechas para el mes objetivo."
        )
    return sliced, warnings


def set_file_has_enabled_grid(set_path: str | Path) -> bool:
    """Return True only when a .set file explicitly has EnableGrid=true."""
    path = resolve_workspace_path(set_path)
    if not path.is_file():
        return False
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() != "enablegrid":
            continue
        first_value = value.split("||", 1)[0].strip().lower()
        return first_value in {"true", "1", "yes", "y", "si", "sí"}
    return False


def filter_rows_grid_off(rows: Sequence[object]) -> tuple[list[object], list[str]]:
    """Remove candidate rows whose .set explicitly enables grid trading."""
    filtered: list[object] = []
    skipped_grid = 0
    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if set_path and set_file_has_enabled_grid(set_path):
            skipped_grid += 1
            continue
        filtered.append(row)
    warnings = []
    if skipped_grid:
        warnings.append(f"Grid OFF: {skipped_grid} candidato(s) omitido(s) por EnableGrid=true.")
    return filtered, warnings
