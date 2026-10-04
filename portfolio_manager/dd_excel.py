from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from .dd_excel_sheets import (
    _safe_sheet_name,
    _window_stats,
    _write_dd_sheet,
    _write_portfolio_dd_sheet,
    _write_portfolio_valley_dd_sheet,
    _write_threshold_sheet,
    _write_top_portfolio_valleys_sheet,
)
from .dd_excel_types import (
    PORTFOLIO_ACCOUNT_BALANCE,
    DrawdownWindow,
    PortfolioDayDrawdown,
    PortfolioValleyContribution,
    PortfolioValleyDrawdown,
    StrategyDayDrawdown,
)
from .mt5_report import StrategyReport, Trade


def build_drawdown_workbook(reports: list[StrategyReport], output_path: Path) -> None:
    wb = Workbook()
    default = wb.active
    wb.remove(default)

    used_names: set[str] = set()
    for report in reports:
        ws = wb.create_sheet(_safe_sheet_name(report.name, used_names))
        window = max_drawdown_window(report)
        _write_dd_sheet(ws, report, window)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def build_portfolio_drawdown_workbook(reports: list[StrategyReport], output_path: Path) -> None:
    portfolio_dd = max_portfolio_drawdown_day(reports)
    wb = Workbook()
    ws = wb.active
    ws.title = "PORTFOLIO_DD"
    _write_portfolio_dd_sheet(ws, portfolio_dd)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def build_portfolio_valley_drawdown_workbook(reports: list[StrategyReport], output_path: Path) -> None:
    portfolio_dd = max_portfolio_valley_drawdown(reports)
    wb = Workbook()
    ws = wb.active
    ws.title = "PORTFOLIO_VALLEY_DD"
    _write_portfolio_valley_dd_sheet(ws, portfolio_dd)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def build_top_portfolio_valleys_workbook(reports: list[StrategyReport], output_path: Path, limit: int = 5) -> None:
    valleys = top_portfolio_valley_drawdowns(reports, limit)
    wb = Workbook()
    ws = wb.active
    ws.title = "TOP_VALLEYS"
    _write_top_portfolio_valleys_sheet(ws, valleys)
    for index, valley in enumerate(valleys, start=1):
        detail = wb.create_sheet(f"VALLE_{index}")
        _write_portfolio_valley_dd_sheet(detail, valley)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def build_dd_threshold_workbook(reports: list[StrategyReport], output_path: Path, threshold: float) -> None:
    rows = []
    for report in reports:
        window = max_drawdown_window(report)
        stats = _window_stats(report, window)
        rows.append(
            {
                "Strategy": report.name,
                "Symbol": report.symbol,
                "Timeframe": report.timeframe,
                "Cumple": "SI" if window.drawdown <= threshold else "NO",
                "Threshold": threshold,
                "Max daily DD": round(window.drawdown, 2),
                "Worst day P/L": stats["Total profit"],
                "Worst day": stats["Period start"],
                "# trades": stats["# of trades"],
                "Winning %": stats["Winning %"],
                "Profit factor": stats["Profit factor"],
                "Profit in pips": stats["Profit in pips"],
                "Report path": str(report.path),
            }
        )

    rows.sort(key=lambda row: (row["Cumple"] != "SI", row["Max daily DD"], row["Strategy"]))

    wb = Workbook()
    ws_ok = wb.active
    ws_ok.title = "CUMPLEN"
    _write_threshold_sheet(ws_ok, [row for row in rows if row["Cumple"] == "SI"], threshold)
    ws_all = wb.create_sheet("TODAS")
    _write_threshold_sheet(ws_all, rows, threshold)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def max_portfolio_drawdown_day(reports: list[StrategyReport]) -> PortfolioDayDrawdown:
    by_day: dict[object, list[StrategyDayDrawdown]] = {}

    for report in reports:
        for day, window in daily_close_windows(report).items():
            by_day.setdefault(day, []).append(StrategyDayDrawdown(report, window))

    if not by_day:
        return PortfolioDayDrawdown("", 0.0, 0.0, [])

    best_day = None
    worst_total_profit = 0.0
    for day, strategy_windows in by_day.items():
        total_profit = sum(sum(trade.profit_loss for trade in item.window.trades) for item in strategy_windows)
        if best_day is None or total_profit < worst_total_profit:
            best_day = day
            worst_total_profit = total_profit

    assert best_day is not None
    strategies = sorted(
        by_day[best_day],
        key=lambda item: sum(trade.profit_loss for trade in item.window.trades),
    )
    total_profit = sum(sum(trade.profit_loss for trade in item.window.trades) for item in strategies)
    return PortfolioDayDrawdown(best_day, max(-total_profit, 0.0), total_profit, strategies)


def max_portfolio_valley_drawdown(reports: list[StrategyReport]) -> PortfolioValleyDrawdown:
    valleys = top_portfolio_valley_drawdowns(reports, 1)
    if valleys:
        return valleys[0]

    return PortfolioValleyDrawdown(None, None, PORTFOLIO_ACCOUNT_BALANCE, PORTFOLIO_ACCOUNT_BALANCE, PORTFOLIO_ACCOUNT_BALANCE, 0.0, 0.0, [])


def top_portfolio_valley_drawdowns(reports: list[StrategyReport], limit: int = 5) -> list[PortfolioValleyDrawdown]:
    portfolio_trades: list[tuple[StrategyReport, Trade]] = []
    for report in reports:
        for trade in report.trades:
            portfolio_trades.append((report, trade))
    portfolio_trades.sort(key=lambda item: item[1].close_time)

    if not portfolio_trades:
        return []

    balance = PORTFOLIO_ACCOUNT_BALANCE
    peak_balance = PORTFOLIO_ACCOUNT_BALANCE
    peak_time: datetime | None = None
    peak_index = -1
    trough_balance = PORTFOLIO_ACCOUNT_BALANCE
    trough_time: datetime | None = None
    trough_index = -1
    valleys: list[PortfolioValleyDrawdown] = []

    for index, (_report, trade) in enumerate(portfolio_trades):
        balance += trade.profit_loss
        if balance > peak_balance:
            if trough_index > peak_index and peak_balance > trough_balance:
                valleys.append(
                    _portfolio_valley_from_indices(
                        portfolio_trades,
                        peak_index,
                        trough_index,
                        peak_time,
                        trough_time,
                        peak_balance,
                        trough_balance,
                    )
                )
            peak_balance = balance
            peak_time = trade.close_time
            peak_index = index
            trough_balance = balance
            trough_time = trade.close_time
            trough_index = index
        elif balance < trough_balance:
            trough_balance = balance
            trough_time = trade.close_time
            trough_index = index

    if trough_index > peak_index and peak_balance > trough_balance:
        valleys.append(
            _portfolio_valley_from_indices(
                portfolio_trades,
                peak_index,
                trough_index,
                peak_time,
                trough_time,
                peak_balance,
                trough_balance,
            )
        )

    valleys.sort(key=lambda valley: valley.drawdown, reverse=True)
    return valleys[:limit]


def _portfolio_valley_from_indices(
    portfolio_trades: list[tuple[StrategyReport, Trade]],
    peak_index: int,
    trough_index: int,
    peak_time: datetime | None,
    trough_time: datetime | None,
    peak_balance: float,
    trough_balance: float,
) -> PortfolioValleyDrawdown:
    selected = portfolio_trades[peak_index + 1 : trough_index + 1] if trough_index >= 0 else []
    grouped: dict[Path, tuple[StrategyReport, list[Trade]]] = {}
    for report, trade in selected:
        if report.path not in grouped:
            grouped[report.path] = (report, [])
        grouped[report.path][1].append(trade)

    contributions = [
        PortfolioValleyContribution(report, trades, sum(trade.profit_loss for trade in trades))
        for report, trades in grouped.values()
    ]
    contributions.sort(key=lambda item: item.total_profit)

    return PortfolioValleyDrawdown(
        peak_time=peak_time,
        trough_time=trough_time,
        initial_balance=PORTFOLIO_ACCOUNT_BALANCE,
        peak_balance=peak_balance,
        trough_balance=trough_balance,
        drawdown=peak_balance - trough_balance,
        total_profit=sum(item.total_profit for item in contributions),
        contributions=contributions,
    )


def max_drawdown_window(report: StrategyReport) -> DrawdownWindow:
    windows = daily_close_windows(report)
    if not windows:
        return DrawdownWindow([], report.initial_deposit, report.initial_deposit, 0.0, None, None)

    return max(windows.values(), key=lambda window: window.drawdown)


def daily_drawdown_windows(report: StrategyReport) -> dict[object, DrawdownWindow]:
    trades = sorted(report.trades, key=lambda trade: trade.close_time)
    if not trades:
        return {}

    balance = report.initial_deposit
    windows: dict[object, DrawdownWindow] = {}
    day_trades: list[tuple[Trade, float, float]] = []
    current_day = trades[0].close_time.date()

    for trade in trades:
        trade_day = trade.close_time.date()
        if trade_day != current_day:
            candidate = _daily_drawdown_window(day_trades)
            if candidate:
                windows[current_day] = candidate
            day_trades = []
            current_day = trade_day

        before = balance
        balance += trade.profit_loss
        day_trades.append((trade, before, balance))

    candidate = _daily_drawdown_window(day_trades)
    if candidate:
        windows[current_day] = candidate
    return windows


def daily_close_windows(report: StrategyReport) -> dict[object, DrawdownWindow]:
    trades = sorted(report.trades, key=lambda trade: trade.close_time)
    if not trades:
        return {}

    windows: dict[object, DrawdownWindow] = {}
    grouped: dict[object, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(trade.close_time.date(), []).append(trade)

    for day, day_trades in grouped.items():
        total_profit = sum(trade.profit_loss for trade in day_trades)
        close_time_start = day_trades[0].close_time
        close_time_end = day_trades[-1].close_time
        windows[day] = DrawdownWindow(
            trades=day_trades,
            peak_balance=report.initial_deposit,
            trough_balance=report.initial_deposit + total_profit,
            drawdown=max(-total_profit, 0.0),
            start_time=close_time_start,
            end_time=close_time_end,
        )
    return windows



def _daily_drawdown_window(day_trades: list[tuple[Trade, float, float]]) -> DrawdownWindow | None:
    if not day_trades:
        return None

    day_open_balance = day_trades[0][1]
    trough_index = 0
    trough_balance = day_trades[0][2]
    for index, (_trade, _before, after) in enumerate(day_trades):
        if after < trough_balance:
            trough_index = index
            trough_balance = after

    dd = max(day_open_balance - trough_balance, 0.0)
    if dd <= 0:
        return DrawdownWindow([], day_open_balance, day_open_balance, 0.0, None, None)

    # Include every trade that closes on this DD day, even if it opened earlier.
    selected = [trade for trade, _before, _after in day_trades]
    start_time = selected[0].close_time if selected else None
    end_time = selected[-1].close_time if selected else None
    return DrawdownWindow(selected, day_open_balance, trough_balance, dd, start_time, end_time)
