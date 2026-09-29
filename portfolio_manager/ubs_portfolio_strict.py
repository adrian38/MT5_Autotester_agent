"""Validacion estricta de un portafolio mensual ya compuesto."""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from .ubs_portfolio import (
    RobustStrategySet,
)
from .ubs_portfolio_curves import (
    calc_point_dd,
    calc_valley_dd,
)


def validate_strict_monthly_portfolio(
    strategies: Sequence[RobustStrategySet],
    allocations: dict[str, int],
    *,
    target_month: int,
    target_valley_dd: float,
    target_point_dd: float,
    lookback_years: int = 5,
    enforce_point_dd: bool = True,
) -> dict[str, object]:
    """Validate a monthly portfolio year-by-year, by DD caps, and by dominance.

    The optimizer builds the portfolio on the selected month aggregated across
    history.  This stricter audit checks the fixed final allocation against each
    selected-month year in the latest ``lookback_years`` available years,
    verifies that every calendar month in that same window respects the DD caps,
    then verifies that the selected month is the best aggregate month by net.
    """
    month = int(target_month)
    if not 1 <= month <= 12:
        raise ValueError("target_month must be between 1 and 12")
    years_back = max(int(lookback_years), 1)

    increments: list[tuple[datetime, float]] = []
    for strategy in strategies:
        units = max(int(allocations.get(strategy.set_id, 0)), 0)
        if units <= 0 or not strategy.curve_points_2020_2026_001:
            continue
        previous_value = 0.0
        for timestamp, accumulated_value in strategy.curve_points_2020_2026_001:
            increment = (float(accumulated_value) - previous_value) * units
            previous_value = float(accumulated_value)
            increments.append((timestamp, increment))

    if not increments:
        return {
            "passed": False,
            "target_month": month,
            "lookback_years": years_back,
            "years": [],
            "yearly": [],
            "monthly_dd": {},
            "month_net_by_month": {},
            "best_month": None,
            "best_month_net": 0.0,
            "target_month_net": 0.0,
            "reasons": ["sin trades fechados para validar el portafolio mensual"],
            "enforce_point_dd": bool(enforce_point_dd),
        }

    target_month_years = [
        timestamp.year
        for timestamp, _increment in increments
        if timestamp.month == month
    ]
    latest_year = max(target_month_years) if target_month_years else max(
        timestamp.year for timestamp, _increment in increments
    )
    earliest_year = latest_year - years_back + 1
    years = list(range(earliest_year, latest_year + 1))
    increments = [
        (timestamp, increment)
        for timestamp, increment in increments
        if earliest_year <= timestamp.year <= latest_year
    ]

    reasons: list[str] = []
    yearly: list[dict[str, object]] = []
    for year in years:
        year_month_increments = [
            (timestamp, increment)
            for timestamp, increment in increments
            if timestamp.year == year and timestamp.month == month
        ]
        year_month_increments.sort(key=lambda item: item[0])
        total = 0.0
        curve = [0.0]
        for _timestamp, increment in year_month_increments:
            total += increment
            curve.append(total)
        valley_dd = calc_valley_dd(curve)
        point_dd = calc_point_dd(curve)
        passed_year = (
            bool(year_month_increments)
            and total > 0
            and valley_dd <= float(target_valley_dd) + 1e-9
            and (
                not enforce_point_dd
                or point_dd <= float(target_point_dd) + 1e-9
            )
        )
        if not passed_year:
            if not year_month_increments:
                reasons.append(f"{year}: sin trades en mes {month:02d}")
            elif total <= 0:
                reasons.append(f"{year}: net {total:,.2f} <= 0 en mes {month:02d}")
            elif valley_dd > float(target_valley_dd) + 1e-9:
                reasons.append(
                    f"{year}: DD valle {valley_dd:,.2f} > {float(target_valley_dd):,.2f}"
                )
            elif enforce_point_dd and point_dd > float(target_point_dd) + 1e-9:
                reasons.append(
                    f"{year}: DD puntual {point_dd:,.2f} > {float(target_point_dd):,.2f}"
                )
        yearly.append(
            {
                "year": year,
                "trades": len(year_month_increments),
                "net": total,
                "valley_dd": valley_dd,
                "point_dd": point_dd,
                "passed": passed_year,
            }
        )

    month_net_by_month = {item: 0.0 for item in range(1, 13)}
    for timestamp, increment in increments:
        month_net_by_month[timestamp.month] += increment

    monthly_dd: dict[str, dict[str, object]] = {}
    for month_no in range(1, 13):
        month_increments = [
            (timestamp, increment)
            for timestamp, increment in increments
            if timestamp.month == month_no
        ]
        month_increments.sort(key=lambda item: item[0])
        curve = [0.0]
        total = 0.0
        for _timestamp, increment in month_increments:
            total += increment
            curve.append(total)
        valley_dd = calc_valley_dd(curve)
        point_dd = calc_point_dd(curve)
        passed_dd = (
            valley_dd <= float(target_valley_dd) + 1e-9
            and (
                not enforce_point_dd
                or point_dd <= float(target_point_dd) + 1e-9
            )
        )
        if not passed_dd:
            label = f"mes {month_no:02d}"
            if valley_dd > float(target_valley_dd) + 1e-9:
                reasons.append(
                    f"{label}: DD valle {valley_dd:,.2f} > {float(target_valley_dd):,.2f}"
                )
            if enforce_point_dd and point_dd > float(target_point_dd) + 1e-9:
                reasons.append(
                    f"{label}: DD puntual {point_dd:,.2f} > {float(target_point_dd):,.2f}"
                )
        monthly_dd[f"{month_no:02d}"] = {
            "trades": len(month_increments),
            "net": total,
            "valley_dd": valley_dd,
            "point_dd": point_dd,
            "passed_dd": passed_dd,
        }

    best_month, best_month_net = max(
        month_net_by_month.items(),
        key=lambda item: (item[1], -abs(item[0] - month)),
    )
    target_month_net = month_net_by_month.get(month, 0.0)
    if best_month != month:
        reasons.append(
            f"mes {month:02d} no es el mejor de los ultimos {years_back} años "
            f"(mejor {best_month:02d}: {best_month_net:,.2f} vs {target_month_net:,.2f})"
        )

    return {
        "passed": not reasons,
        "target_month": month,
        "lookback_years": years_back,
        "years": years,
        "yearly": yearly,
        "monthly_dd": monthly_dd,
        "month_net_by_month": {
            f"{key:02d}": value for key, value in sorted(month_net_by_month.items())
        },
        "best_month": best_month,
        "best_month_net": best_month_net,
        "target_month_net": target_month_net,
        "target_valley_dd": float(target_valley_dd),
        "target_point_dd": float(target_point_dd),
        "enforce_point_dd": bool(enforce_point_dd),
        "reasons": reasons,
    }
