"""Constructor de estrategias sinteticas para los tests de portafolio UBS."""
from __future__ import annotations

from datetime import datetime

from portfolio_manager.ubs_portfolio import (
    ClosedTrade,
    PeriodReport,
    RobustStrategySet,
    calc_point_dd,
    calc_valley_dd,
)


def _make_period(
    symbol: str, curve: list[float], trades: int, profit_factor: float, price: float | None,
) -> PeriodReport:
    valley = calc_valley_dd(curve)
    net = curve[-1]
    return PeriodReport(
        period_name="dummy",
        start_year=2020,
        end_year=2026,
        symbol=symbol,
        timeframe="H1",
        pnl_curve_001=curve,
        net_profit_001=net,
        valley_dd_001=valley,
        point_dd_001=calc_point_dd(curve),
        profit_factor=profit_factor,
        return_dd_ratio=net / max(valley, 1),
        trades=trades,
        gross_profit=max(net, 0),
        gross_loss=-max(valley, 1),
        closed_trades=[
            ClosedTrade(
                open_time=datetime(2026, 1, 1),
                close_time=datetime(2026, 1, 2),
                symbol=symbol,
                volume=0.01,
                profit=net,
                open_price=price,
                close_price=price,
            )
        ]
        if price is not None
        else [],
    )


def make_strategy(
    set_id: str,
    symbol: str,
    curve: list[float],
    *,
    candidate_id: str | None = None,
    status: str = "accepted",
    already_used: bool = False,
    trades: int = 120,
    profit_factor: float = 1.5,
    price: float | None = None,
) -> RobustStrategySet:
    valley = calc_valley_dd(curve)
    point = calc_point_dd(curve)
    net = curve[-1]
    period = _make_period(symbol, curve, trades, profit_factor, price)
    return RobustStrategySet(
        set_id=set_id,
        candidate_id=candidate_id or set_id,
        symbol=symbol,
        timeframe="H1",
        strategy_family="test",
        robustness_status=status,
        already_used=already_used,
        report_2020_2024=period,
        report_2025_2026=period,
        curve_2020_2026_001=curve,
        net_profit_2020_2026_001=net,
        valley_dd_2020_2026_001=valley,
        point_dd_2020_2026_001=point,
        profit_factor_2020_2026=profit_factor,
        return_dd_2020_2026=net / max(valley, 1),
        trades_2020_2026=trades,
        set_path=set_id,
    )
