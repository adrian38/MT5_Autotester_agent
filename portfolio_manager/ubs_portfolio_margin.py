"""Margen requerido por asignacion segun el perfil del broker."""
from __future__ import annotations


from .ubs_portfolio import (
    RobustStrategySet,
    portfolio_symbol_key,
)
from .ubs_portfolio_utils import (
    portfolio_group_key,
)


def roboforex_margin_leverage(symbol: str) -> float:
    """Portfolio leverage rule requested for RoboForex portfolios."""
    return 20.0 if portfolio_group_key(symbol) == "Stocks" else 500.0


def roboforex_contract_size(symbol: str) -> float:
    """Contract-size rule requested for the portfolio margin guard."""
    return 100.0 if portfolio_group_key(symbol) == "Stocks" else 1.0


def normalize_margin_profile(profile: str | None) -> str:
    value = str(profile or "roboforex").strip().lower()
    if value in {"ttp", "thetradingpit", "tradingpit", "the_trading_pit"}:
        return "ttp"
    if value in {"axi", "axi trading", "axitrading", "axi_select", "axiselect"}:
        return "axi"
    if value in {"ictrading", "ic trading", "ic", "icmarkets", "ic markets"}:
        return "ictrading"
    return "roboforex"


def margin_profile_label(profile: str | None) -> str:
    return {
        "ttp": "TTP",
        "axi": "AXI",
        "ictrading": "ICTrading",
        "roboforex": "RoboForex",
    }.get(normalize_margin_profile(profile), "RoboForex")


def margin_leverage_for_profile(
    symbol: str,
    *,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
) -> float:
    profile = normalize_margin_profile(margin_profile)
    if profile == "ttp":
        group = portfolio_group_key(symbol)
        symbol_key = portfolio_symbol_key(symbol)
        if group == "Stocks" or group == "Crypto":
            return 2.0
        if group == "Metals":
            return 10.0
        if group in {"Indices", "Energies", "IndicesEnergies"}:
            return 10.0 if group == "Energies" or symbol_key in {"BRENT", "WTI"} else 15.0
        if group == "Forex":
            return 50.0
        return 50.0
    return stock_leverage if portfolio_group_key(symbol) == "Stocks" else default_leverage


def margin_contract_size_for_profile(
    symbol: str,
    *,
    margin_profile: str | None = "roboforex",
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    # Contract size is kept explicit and broker-independent for now: stocks use
    # 100 and every other group uses 1, matching the portfolio margin model.
    return stock_contract_size if portfolio_group_key(symbol) == "Stocks" else default_contract_size


def strategy_reference_price(strategy: RobustStrategySet) -> float:
    """Conservative price estimate from parsed MT5 closed trades."""
    prices: list[float] = []
    for report in (strategy.report_2020_2024, strategy.report_2025_2026):
        for trade in report.closed_trades:
            for price in (trade.open_price, trade.close_price):
                if price is not None and price > 0:
                    prices.append(float(price))
    if prices:
        return max(prices)
    return 1.0


def allocation_margin_required(
    strategy: RobustStrategySet,
    units: int,
    *,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> float:
    units = max(int(units), 0)
    if units <= 0:
        return 0.0
    lot = units * 0.01
    leverage = margin_leverage_for_profile(
        strategy.symbol,
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
    )
    contract_size = margin_contract_size_for_profile(
        strategy.symbol,
        margin_profile=margin_profile,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    if leverage <= 0:
        return float("inf")
    return lot * contract_size * strategy_reference_price(strategy) / leverage


def _strategy_margin_entry(
    strategy: RobustStrategySet, units: int, *, margin_profile: str | None,
    stock_leverage: float, default_leverage: float,
    stock_contract_size: float, default_contract_size: float,
) -> tuple[float, dict[str, float | str | int]]:
    leverage = margin_leverage_for_profile(
        strategy.symbol, margin_profile=margin_profile,
        stock_leverage=stock_leverage, default_leverage=default_leverage,
    )
    contract_size = margin_contract_size_for_profile(
        strategy.symbol, margin_profile=margin_profile,
        stock_contract_size=stock_contract_size, default_contract_size=default_contract_size,
    )
    margin = allocation_margin_required(
        strategy, units, margin_profile=margin_profile,
        stock_leverage=stock_leverage, default_leverage=default_leverage,
        stock_contract_size=stock_contract_size, default_contract_size=default_contract_size,
    )
    return margin, {
        "symbol": strategy.symbol, "group": portfolio_group_key(strategy.symbol),
        "units": units, "lot": units * 0.01, "leverage": leverage,
        "contract_size": contract_size, "price": strategy_reference_price(strategy),
        "margin": margin,
    }


def portfolio_margin_summary(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float,
    max_margin_pct: float,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> dict[str, object]:
    by_set: dict[str, dict[str, float | str | int]] = {}
    total = 0.0
    for strategy in sets:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0:
            continue
        margin, entry = _strategy_margin_entry(
            strategy, units, margin_profile=margin_profile,
            stock_leverage=stock_leverage, default_leverage=default_leverage,
            stock_contract_size=stock_contract_size, default_contract_size=default_contract_size,
        )
        total += margin
        by_set[strategy.set_id] = entry
    limit = float(balance) * float(max_margin_pct) / 100.0 if balance > 0 else 0.0
    return {
        "enabled": True,
        "balance": float(balance),
        "max_margin_pct": float(max_margin_pct),
        "limit": limit,
        "total": total,
        "usage_pct": total / limit * 100.0 if limit > 0 else 0.0,
        "profile": normalize_margin_profile(margin_profile),
        "profile_label": margin_profile_label(margin_profile),
        "stock_leverage": float(stock_leverage),
        "default_leverage": float(default_leverage),
        "stock_contract_size": float(stock_contract_size),
        "default_contract_size": float(default_contract_size),
        "by_set": by_set,
    }


def allocations_respect_margin_limit(
    sets: list[RobustStrategySet],
    allocations: dict[str, int],
    *,
    balance: float | None,
    max_margin_pct: float | None,
    margin_profile: str | None = "roboforex",
    stock_leverage: float = 20.0,
    default_leverage: float = 500.0,
    stock_contract_size: float = 100.0,
    default_contract_size: float = 1.0,
) -> bool:
    if balance is None or max_margin_pct is None:
        return True
    summary = portfolio_margin_summary(
        sets,
        allocations,
        balance=float(balance),
        max_margin_pct=float(max_margin_pct),
        margin_profile=margin_profile,
        stock_leverage=stock_leverage,
        default_leverage=default_leverage,
        stock_contract_size=stock_contract_size,
        default_contract_size=default_contract_size,
    )
    return float(summary["total"]) <= float(summary["limit"]) + 1e-9
