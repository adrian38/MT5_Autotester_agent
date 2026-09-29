from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .common import utc_now
from .live_audit_price import ADAPTIVE_PRICE_TOLERANCE_FLOORS
from .live_audit_helpers import _drawdown, _effective_price_tolerance, _pnl_comparison, _trade_view


class LiveAuditCompareMixin:
    """Comparacion entre lo real y lo simulado, y base del resultado."""

    @staticmethod
    def _compare(
        real: list[dict[str, Any]], tester: list[dict[str, Any]], points: dict[str, float],
        request: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        unused = set(range(len(real)))
        matched = 0
        within_tolerance = 0
        deviations = 0
        matched_by_strategy: dict[str, int] = {}
        within_tolerance_by_strategy: dict[str, int] = {}
        deviating_by_strategy: dict[str, int] = {}
        missing_by_strategy: dict[str, int] = {}
        deviation_reasons = {"close_time": 0, "open_price": 0, "volume": 0, "pnl": 0, "drawdown": 0}
        tester_data_issues: dict[str, int] = {}
        operation_comparisons: list[dict[str, Any]] = []
        time_limit = request["trade_time_tolerance_seconds"]
        for tester_index, expected in enumerate(tester, 1):
            strategy = str(expected["strategy"])
            data_issues: list[str] = []
            if expected["close_time"] < expected["open_time"]:
                data_issues.append("close_before_open")
                tester_data_issues["close_before_open"] = tester_data_issues.get("close_before_open", 0) + 1
            candidates: list[tuple[float, int]] = []
            same_market: list[tuple[float, int]] = []
            for index in unused:
                actual = real[index]
                if actual["symbol"].casefold() != expected["symbol"].casefold() or actual["side"] != expected["side"]:
                    continue
                delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
                same_market.append((delta, index))
                if delta <= time_limit:
                    candidates.append((delta, index))
            if not candidates:
                missing_by_strategy[strategy] = missing_by_strategy.get(strategy, 0) + 1
                nearest = min(same_market) if same_market else None
                nearest_trade = real[nearest[1]] if nearest else None
                operation_comparisons.append({
                    "tester_index": tester_index,
                    "status": "missing",
                    "strategy": strategy,
                    "tester": _trade_view(expected),
                    "real": None,
                    "nearest_unused_real": _trade_view(nearest_trade),
                    "measurements": {
                        "nearest_open_time_delta_seconds": round(nearest[0], 3) if nearest else None,
                    },
                    "limits": {"open_time_seconds": time_limit},
                    "data_issues": data_issues,
                    "reasons": [
                        "open_time_outside_tolerance" if nearest else "no_real_same_symbol_and_side"
                    ],
                })
                continue
            open_time_delta, index = min(candidates)
            unused.remove(index)
            actual = real[index]
            matched += 1
            matched_by_strategy[strategy] = matched_by_strategy.get(strategy, 0) + 1
            point = points.get(actual["symbol"], 0.0)
            price_limit, price_limit_points, price_limit_rule = _effective_price_tolerance(
                actual["symbol"], point, request["price_tolerance_points"],
            )
            volume_limit = max(expected["volume"], 1e-9) * request["volume_tolerance_pct"] / 100
            pnl = _pnl_comparison(
                actual["profit"], expected["profit"], request["pnl_deviation_warning_pct"],
            )
            pnl_limit = float(pnl["limit"])
            close_time_delta = abs((actual["close_time"] - expected["close_time"]).total_seconds())
            open_price_delta = abs(float(actual["open_price"]) - float(expected["open_price"]))
            volume_delta = abs(float(actual["volume"]) - float(expected["volume"]))
            pnl_delta = float(pnl["delta"])
            reasons: list[str] = []
            if close_time_delta > time_limit:
                reasons.append("close_time")
            price_limit_epsilon = max(point * 1e-6, 1e-12)
            if (
                price_limit is not None
                and open_price_delta > price_limit
                and not math.isclose(open_price_delta, price_limit, rel_tol=0.0, abs_tol=price_limit_epsilon)
            ):
                reasons.append("open_price")
            if volume_delta > volume_limit:
                reasons.append("volume")
            if pnl["outside_tolerance"]:
                reasons.append("pnl")
            if reasons:
                deviations += 1
                deviating_by_strategy[strategy] = deviating_by_strategy.get(strategy, 0) + 1
                for reason in reasons:
                    deviation_reasons[reason] += 1
            else:
                within_tolerance += 1
                within_tolerance_by_strategy[strategy] = within_tolerance_by_strategy.get(strategy, 0) + 1
            operation_comparisons.append({
                "tester_index": tester_index,
                "real_index": index + 1,
                "status": "deviation" if reasons else "matched",
                "strategy": strategy,
                "tester": _trade_view(expected),
                "real": _trade_view(actual),
                "nearest_unused_real": None,
                "measurements": {
                    "open_time_delta_seconds": round(open_time_delta, 3),
                    "close_time_delta_seconds": round(close_time_delta, 3),
                    "open_price_delta": round(open_price_delta, 10),
                    "open_price_delta_points": round(open_price_delta / point, 3) if point > 0 else None,
                    "volume_delta": round(volume_delta, 8),
                    "volume_delta_pct": round(volume_delta / max(abs(float(expected["volume"])), 1e-9) * 100, 3),
                    "pnl_delta": round(pnl_delta, 2),
                    "pnl_delta_pct": round(pnl_delta / max(abs(float(expected["profit"])), 1.0) * 100, 3),
                    "pnl_change": round(float(pnl["change"]), 2),
                    "pnl_change_pct": round(float(pnl["change_pct"]), 3),
                    "pnl_adverse_delta": round(float(pnl["adverse_delta"]), 2),
                    "pnl_adverse_delta_pct": round(float(pnl["adverse_delta_pct"]), 3),
                    "pnl_direction": pnl["direction"],
                },
                "limits": {
                    "open_time_seconds": time_limit,
                    "close_time_seconds": time_limit,
                    "open_price_points": round(price_limit_points, 3) if price_limit_points is not None else None,
                    "open_price_absolute": round(price_limit, 10) if price_limit is not None else None,
                    "open_price_configured_points": request["price_tolerance_points"],
                    "open_price_rule": price_limit_rule,
                    "volume_pct": request["volume_tolerance_pct"],
                    "volume_absolute": round(volume_limit, 8),
                    "pnl_pct": request["pnl_deviation_warning_pct"],
                    "pnl_absolute": round(pnl_limit, 2),
                },
                "data_issues": data_issues,
                "reasons": reasons,
            })
        missing = len(tester) - matched
        extra = len(unused)
        real_dd, tester_dd = _drawdown(real), _drawdown(tester)
        dd_deviation = abs(real_dd - tester_dd) / max(tester_dd, 1.0) * 100
        if dd_deviation > request["drawdown_deviation_warning_pct"]:
            deviations += 1
            deviation_reasons["drawdown"] += 1
        stalled = sum(1 for strategy, count in strategies.items() if count and not matched_by_strategy.get(strategy))
        unmatched_real: dict[str, int] = {}
        unmatched_real_operations: list[dict[str, Any]] = []
        for index in unused:
            trade = real[index]
            key = f"{trade.get('symbol') or '?'} / lote {float(trade.get('volume') or 0):g}"
            unmatched_real[key] = unmatched_real.get(key, 0) + 1
            unmatched_real_operations.append({
                "real_index": index + 1,
                "status": "extra",
                "real": _trade_view(trade),
                "reason": "not_used_by_any_tester_operation",
            })
        strategy_summary = []
        for strategy in sorted(strategies):
            strategy_summary.append({
                "strategy": strategy,
                "tester_trades": int(strategies.get(strategy) or 0),
                "aligned": matched_by_strategy.get(strategy, 0),
                "within_tolerance": within_tolerance_by_strategy.get(strategy, 0),
                "with_deviations": deviating_by_strategy.get(strategy, 0),
                "missing_real": missing_by_strategy.get(strategy, 0),
            })
        return {
            "matched_trades": matched, "within_tolerance_trades": within_tolerance,
            "missing_real_trades": missing, "extra_real_trades": extra,
            "deviating_pairs": sum(deviating_by_strategy.values()),
            "deviating_trades": deviations, "discrepancies": missing + extra + deviations,
            "stalled_strategies": stalled, "real_drawdown": round(real_dd, 2),
            "tester_drawdown": round(tester_dd, 2), "drawdown_deviation_pct": round(dd_deviation, 2),
            "comparison_detail": {
                "matched_by_strategy": matched_by_strategy,
                "within_tolerance_by_strategy": within_tolerance_by_strategy,
                "deviating_by_strategy": deviating_by_strategy,
                "missing_by_strategy": missing_by_strategy,
                "unmatched_real": unmatched_real,
                "deviation_reasons": {key: value for key, value in deviation_reasons.items() if value},
                "tester_data_issues": tester_data_issues,
                "time_tolerance_seconds": time_limit,
                "methodology": {
                    "alignment": "Mismo símbolo y lado; apertura dentro de tolerancia; se elige el menor delta y cada real se usa una vez.",
                    "validation": "Después se validan cierre, precio de apertura y volumen. El PnL solo alerta si el resultado real empeora frente al tester; una mejora es admisible. El drawdown se valida sobre el conjunto.",
                    "tolerances": {
                        "time_seconds": time_limit,
                        "price_points": request["price_tolerance_points"],
                        "price_policy": "adaptive_by_instrument",
                        "price_absolute_floors": ADAPTIVE_PRICE_TOLERANCE_FLOORS,
                        "volume_pct": request["volume_tolerance_pct"],
                        "pnl_pct": request["pnl_deviation_warning_pct"],
                        "pnl_policy": "adverse_shortfall_only",
                        "drawdown_pct": request["drawdown_deviation_warning_pct"],
                    },
                },
                "strategy_summary": strategy_summary,
                "operation_comparisons": operation_comparisons,
                "unmatched_real_operations": unmatched_real_operations,
                "drawdown": {
                    "real": round(real_dd, 2),
                    "tester": round(tester_dd, 2),
                    "deviation_pct": round(dd_deviation, 2),
                    "limit_pct": request["drawdown_deviation_warning_pct"],
                    "outside_tolerance": dd_deviation > request["drawdown_deviation_warning_pct"],
                },
            },
        }

    @staticmethod
    def _result_base(
        request: dict[str, Any], period_start: datetime, period_end: datetime,
        real: list[dict[str, Any]], tester: list[dict[str, Any]], quality: float | None,
    ) -> dict[str, Any]:
        return {
            "audit_key": request["audit_key"], "portfolio_id": request["portfolio_id"],
            "portfolio_type": request["portfolio_type"], "completed_at": utc_now(),
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "period_mode": request.get("period_mode", "rolling_days"),
            "period_days": request["period_days"],
            "period_start_date": request.get("period_start_date", ""),
            "period_end_date": request.get("period_end_date", ""),
            "history_quality_pct": round(quality, 2) if quality is not None else None,
            "real_trades": len(real), "tester_trades": len(tester),
        }
