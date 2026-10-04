"""Constantes y estructuras compartidas de los libros de drawdown."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .mt5_report import Trade


PORTFOLIO_ACCOUNT_BALANCE = 1000.0

SUMMARY_HEADERS = [
    "Symbol",
    "Timeframe",
    "Period start",
    "Period end",
    "Initial deposit",
    "Total profit",
    "# of trades",
    "Sharpe ratio",
    "Profit factor",
    "Return / DD ratio",
    "Winning %",
    "Profit in pips",
    "Yearly avg profit",
    "Drawdown",
    "Percent drawdown",
    "Daily avg profit",
    "Monthly avg profit",
    "Average trade",
    "Yearly avg % return",
    "CAGR",
]


@dataclass
class DrawdownWindow:
    trades: list[Trade]
    peak_balance: float
    trough_balance: float
    drawdown: float
    start_time: datetime | None
    end_time: datetime | None


@dataclass
class StrategyDayDrawdown:
    report: StrategyReport
    window: DrawdownWindow


@dataclass
class PortfolioDayDrawdown:
    day: object
    total_drawdown: float
    total_profit: float
    strategies: list[StrategyDayDrawdown]


@dataclass
class PortfolioValleyContribution:
    report: StrategyReport
    trades: list[Trade]
    total_profit: float


@dataclass
class PortfolioValleyDrawdown:
    peak_time: datetime | None
    trough_time: datetime | None
    initial_balance: float
    peak_balance: float
    trough_balance: float
    drawdown: float
    total_profit: float
    contributions: list[PortfolioValleyContribution]
