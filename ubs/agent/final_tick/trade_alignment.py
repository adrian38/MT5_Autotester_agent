"""Alineacion de operaciones OHLC y Tick para decidir muestras economicas."""
from __future__ import annotations

from pathlib import Path

from portfolio_manager.mt5_report import parse_report


def final_tick_trade_alignment(
    ohlc_report: str | Path,
    tick_report: str | Path,
    *,
    max_time_delta_seconds: float = 300.0,
    min_match_ratio: float = 0.80,
) -> dict | None:
    """Compara la huella temporal de las operaciones de ambos modelos."""
    try:
        ohlc_trades = parse_report(Path(ohlc_report)).trades
        tick_trades = parse_report(Path(tick_report)).trades
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not ohlc_trades or not tick_trades:
        return None
    unused = set(range(len(tick_trades)))
    matched = 0
    for expected in ohlc_trades:
        candidates = []
        for index in unused:
            actual = tick_trades[index]
            if actual.trade_type.lower() != expected.trade_type.lower():
                continue
            open_delta = abs((actual.open_time - expected.open_time).total_seconds())
            close_delta = abs((actual.close_time - expected.close_time).total_seconds())
            if open_delta <= max_time_delta_seconds and close_delta <= max_time_delta_seconds:
                candidates.append((open_delta + close_delta, index))
        if candidates:
            _distance, index = min(candidates)
            unused.remove(index)
            matched += 1
    denominator = max(len(ohlc_trades), len(tick_trades))
    ratio = matched / denominator
    return {
        "accepted": ratio >= min_match_ratio,
        "checked": True,
        "matched": matched,
        "ohlc_trades": len(ohlc_trades),
        "real_tick_trades": len(tick_trades),
        "match_ratio": round(ratio, 6),
        "min_match_ratio": min_match_ratio,
        "max_time_delta_seconds": max_time_delta_seconds,
    }
