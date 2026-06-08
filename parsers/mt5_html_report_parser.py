"""MT5 HTML parser adapter for the UBS portfolio builder."""

from __future__ import annotations

from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    build_equity_curve_from_closed_trades,
    extract_period_info,
    parse_mt5_html_report,
)

__all__ = [
    "ClosedTrade",
    "PeriodReport",
    "build_equity_curve_from_closed_trades",
    "extract_period_info",
    "parse_mt5_html_report",
]
