"""ScoreResult sintetico compartido por los tests del agente UBS."""
from ubs.score import ScoreResult



def score(
    value: float,
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "H1",
    net_profit: float = 100.0,
    profit_factor: float = 2.0,
    drawdown_pct: float = 1.0,
    trades: int = 100,
    history_quality: float | None = 100.0,
    accepted: bool = True,
) -> ScoreResult:
    return ScoreResult(
        report_path="report.htm",
        name="report",
        symbol=symbol,
        timeframe=timeframe,
        score=value,
        accepted=accepted,
        net_profit=net_profit,
        raw_net_profit=net_profit,
        normalized_net_profit=net_profit,
        net_profit_factor=1.0,
        net_profit_basis="test",
        normalization_group="test",
        history_quality=history_quality,
        profit_factor=profit_factor,
        recovery_factor=2.0,
        drawdown=10.0,
        drawdown_pct=drawdown_pct,
        trades=trades,
        positive_month_ratio=1.0,
        max_month_concentration=0.1,
        avg_trade=1.0,
        sqn=1.0,
        reasons=(),
    )
