from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .common import utc_now
from .live_audit_price import ADAPTIVE_PRICE_TOLERANCE_FLOORS
from .live_audit_helpers import _drawdown, _effective_price_tolerance, _pnl_comparison, _trade_view
from .live_audit_symbols import audit_symbol_key


@dataclass
class _CompareTally:
    """Recuento de la comparacion operacion a operacion."""

    matched: int = 0
    within_tolerance: int = 0
    deviations: int = 0
    matched_by_strategy: dict[str, int] = field(default_factory=dict)
    within_tolerance_by_strategy: dict[str, int] = field(default_factory=dict)
    deviating_by_strategy: dict[str, int] = field(default_factory=dict)
    missing_by_strategy: dict[str, int] = field(default_factory=dict)
    deviation_reasons: dict[str, int] = field(
        default_factory=lambda: {"close_time": 0, "open_price": 0, "volume": 0, "pnl": 0, "drawdown": 0}
    )
    tester_data_issues: dict[str, int] = field(default_factory=dict)
    operations: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _bump(counts: dict[str, int], key: str) -> None:
        """Suma uno a la clave indicada del recuento."""
        counts[key] = counts.get(key, 0) + 1

    def count_missing(self, strategy: str) -> None:
        """Anota una operacion del tester sin pareja en la cuenta real."""
        self._bump(self.missing_by_strategy, strategy)

    def count_matched(self, strategy: str) -> None:
        """Anota una pareja alineada entre tester y cuenta real."""
        self.matched += 1
        self._bump(self.matched_by_strategy, strategy)

    def count_outcome(self, strategy: str, reasons: list[str]) -> None:
        """Clasifica la pareja como desviada o dentro de todas las tolerancias."""
        if reasons:
            self.deviations += 1
            self._bump(self.deviating_by_strategy, strategy)
            for reason in reasons:
                self.deviation_reasons[reason] += 1
        else:
            self.within_tolerance += 1
            self._bump(self.within_tolerance_by_strategy, strategy)

    def count_data_issue(self, issue: str) -> None:
        """Anota un problema de datos del propio reporte del tester."""
        self._bump(self.tester_data_issues, issue)


def _match_candidates(
    real: list[dict[str, Any]], unused: set[int], expected: dict[str, Any], time_limit: float
) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    """Operaciones reales del mismo mercado y las que caen dentro del margen."""
    candidates: list[tuple[float, int]] = []
    same_market: list[tuple[float, int]] = []
    # Por clave, no por escritura: el tester puede correr en otro servidor del
    # mismo broker, donde el simbolo real `XAUUSD` se llama `XAUUSD.sa`.
    expected_symbol = audit_symbol_key(expected["symbol"])
    for index in unused:
        actual = real[index]
        if audit_symbol_key(actual["symbol"]) != expected_symbol or actual["side"] != expected["side"]:
            continue
        delta = abs((actual["open_time"] - expected["open_time"]).total_seconds())
        same_market.append((delta, index))
        if delta <= time_limit:
            candidates.append((delta, index))
    return candidates, same_market


def _missing_operation(
    tester_index: int, strategy: str, expected: dict[str, Any], real: list[dict[str, Any]],
    same_market: list[tuple[float, int]], time_limit: float, data_issues: list[str],
) -> dict[str, Any]:
    """Ficha de una operacion del tester que no aparece en la cuenta real."""
    nearest = min(same_market) if same_market else None
    nearest_trade = real[nearest[1]] if nearest else None
    return {
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
    }


@dataclass
class _PairDeltas:
    """Diferencias medidas entre una operacion real y la del tester."""

    open_time: float
    close_time: float
    open_price: float
    volume: float
    pnl: dict[str, Any]
    point: float
    price_limit: float | None
    price_limit_points: float | None
    price_limit_rule: str
    volume_limit: float

    @property
    def pnl_delta(self) -> float:
        """Diferencia absoluta de resultado entre real y tester."""
        return float(self.pnl["delta"])


def _pair_deltas(
    actual: dict[str, Any], expected: dict[str, Any], points: dict[str, float],
    request: dict[str, Any], open_time_delta: float,
) -> _PairDeltas:
    """Mide la pareja alineada y resuelve las tolerancias que le aplican."""
    point = points.get(actual["symbol"], 0.0)
    price_limit, price_limit_points, price_limit_rule = _effective_price_tolerance(
        actual["symbol"], point, request["price_tolerance_points"],
    )
    return _PairDeltas(
        open_time=open_time_delta,
        close_time=abs((actual["close_time"] - expected["close_time"]).total_seconds()),
        open_price=abs(float(actual["open_price"]) - float(expected["open_price"])),
        volume=abs(float(actual["volume"]) - float(expected["volume"])),
        pnl=_pnl_comparison(actual["profit"], expected["profit"], request["pnl_deviation_warning_pct"]),
        point=point,
        price_limit=price_limit,
        price_limit_points=price_limit_points,
        price_limit_rule=price_limit_rule,
        volume_limit=max(expected["volume"], 1e-9) * request["volume_tolerance_pct"] / 100,
    )


def _pair_reasons(deltas: _PairDeltas, time_limit: float) -> list[str]:
    """Motivos por los que una pareja queda fuera de tolerancia."""
    reasons: list[str] = []
    if deltas.close_time > time_limit:
        reasons.append("close_time")
    price_limit_epsilon = max(deltas.point * 1e-6, 1e-12)
    if (
        deltas.price_limit is not None
        and deltas.open_price > deltas.price_limit
        and not math.isclose(
            deltas.open_price, deltas.price_limit, rel_tol=0.0, abs_tol=price_limit_epsilon
        )
    ):
        reasons.append("open_price")
    if deltas.volume > deltas.volume_limit:
        reasons.append("volume")
    if deltas.pnl["outside_tolerance"]:
        reasons.append("pnl")
    return reasons


def _pair_measurements(deltas: _PairDeltas, expected: dict[str, Any]) -> dict[str, Any]:
    """Medidas publicadas para una pareja alineada."""
    pnl = deltas.pnl
    return {
        "open_time_delta_seconds": round(deltas.open_time, 3),
        "close_time_delta_seconds": round(deltas.close_time, 3),
        "open_price_delta": round(deltas.open_price, 10),
        "open_price_delta_points": round(deltas.open_price / deltas.point, 3) if deltas.point > 0 else None,
        "volume_delta": round(deltas.volume, 8),
        "volume_delta_pct": round(deltas.volume / max(abs(float(expected["volume"])), 1e-9) * 100, 3),
        "pnl_delta": round(deltas.pnl_delta, 2),
        "pnl_delta_pct": round(deltas.pnl_delta / max(abs(float(expected["profit"])), 1.0) * 100, 3),
        "pnl_change": round(float(pnl["change"]), 2),
        "pnl_change_pct": round(float(pnl["change_pct"]), 3),
        "pnl_adverse_delta": round(float(pnl["adverse_delta"]), 2),
        "pnl_adverse_delta_pct": round(float(pnl["adverse_delta_pct"]), 3),
        "pnl_direction": pnl["direction"],
    }


def _pair_limits(deltas: _PairDeltas, request: dict[str, Any], time_limit: float) -> dict[str, Any]:
    """Limites aplicados a una pareja alineada."""
    return {
        "open_time_seconds": time_limit,
        "close_time_seconds": time_limit,
        "open_price_points": round(deltas.price_limit_points, 3) if deltas.price_limit_points is not None else None,
        "open_price_absolute": round(deltas.price_limit, 10) if deltas.price_limit is not None else None,
        "open_price_configured_points": request["price_tolerance_points"],
        "open_price_rule": deltas.price_limit_rule,
        "volume_pct": request["volume_tolerance_pct"],
        "volume_absolute": round(deltas.volume_limit, 8),
        "pnl_pct": request["pnl_deviation_warning_pct"],
        "pnl_absolute": round(float(deltas.pnl["limit"]), 2),
    }


def _tester_data_issues(tally: _CompareTally, expected: dict[str, Any]) -> list[str]:
    """Problemas del propio reporte del tester para esta operacion."""
    data_issues: list[str] = []
    if expected["close_time"] < expected["open_time"]:
        data_issues.append("close_before_open")
        tally.count_data_issue("close_before_open")
    return data_issues


def _compare_operation(
    tally: _CompareTally, tester_index: int, expected: dict[str, Any],
    real: list[dict[str, Any]], unused: set[int], points: dict[str, float], request: dict[str, Any],
) -> None:
    """Empareja una operacion del tester con la cuenta real y la valida."""
    time_limit = request["trade_time_tolerance_seconds"]
    strategy = str(expected["strategy"])
    data_issues = _tester_data_issues(tally, expected)
    candidates, same_market = _match_candidates(real, unused, expected, time_limit)
    if not candidates:
        tally.count_missing(strategy)
        tally.operations.append(
            _missing_operation(
                tester_index, strategy, expected, real, same_market, time_limit, data_issues
            )
        )
        return
    open_time_delta, index = min(candidates)
    unused.remove(index)
    actual = real[index]
    tally.count_matched(strategy)
    deltas = _pair_deltas(actual, expected, points, request, open_time_delta)
    reasons = _pair_reasons(deltas, time_limit)
    tally.count_outcome(strategy, reasons)
    tally.operations.append({
        "tester_index": tester_index,
        "real_index": index + 1,
        "status": "deviation" if reasons else "matched",
        "strategy": strategy,
        "tester": _trade_view(expected),
        "real": _trade_view(actual),
        "nearest_unused_real": None,
        "measurements": _pair_measurements(deltas, expected),
        "limits": _pair_limits(deltas, request, time_limit),
        "data_issues": data_issues,
        "reasons": reasons,
    })


def _unmatched_real_trades(
    real: list[dict[str, Any]], unused: set[int]
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Cierres reales que ninguna operacion del tester llego a usar."""
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
    return unmatched_real, unmatched_real_operations


def _strategy_summary(tally: _CompareTally, strategies: dict[str, int]) -> list[dict[str, Any]]:
    """Resumen por estrategia de alineadas, desviadas y ausentes."""
    return [
        {
            "strategy": strategy,
            "tester_trades": int(strategies.get(strategy) or 0),
            "aligned": tally.matched_by_strategy.get(strategy, 0),
            "within_tolerance": tally.within_tolerance_by_strategy.get(strategy, 0),
            "with_deviations": tally.deviating_by_strategy.get(strategy, 0),
            "missing_real": tally.missing_by_strategy.get(strategy, 0),
        }
        for strategy in sorted(strategies)
    ]


def _methodology(request: dict[str, Any], time_limit: float) -> dict[str, Any]:
    """Como se alinean y validan las operaciones, con sus tolerancias."""
    return {
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
    }


class LiveAuditCompareMixin:
    """Comparacion entre lo real y lo simulado, y base del resultado."""

    @staticmethod
    def _compare(
        real: list[dict[str, Any]], tester: list[dict[str, Any]], points: dict[str, float],
        request: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        unused = set(range(len(real)))
        tally = _CompareTally()
        time_limit = request["trade_time_tolerance_seconds"]
        for tester_index, expected in enumerate(tester, 1):
            _compare_operation(tally, tester_index, expected, real, unused, points, request)
        missing = len(tester) - tally.matched
        extra = len(unused)
        real_dd, tester_dd = _drawdown(real), _drawdown(tester)
        dd_deviation = abs(real_dd - tester_dd) / max(tester_dd, 1.0) * 100
        dd_outside = dd_deviation > request["drawdown_deviation_warning_pct"]
        if dd_outside:
            tally.deviations += 1
            tally.deviation_reasons["drawdown"] += 1
        stalled = sum(
            1 for strategy, count in strategies.items()
            if count and not tally.matched_by_strategy.get(strategy)
        )
        unmatched_real, unmatched_real_operations = _unmatched_real_trades(real, unused)
        return {
            "matched_trades": tally.matched, "within_tolerance_trades": tally.within_tolerance,
            "missing_real_trades": missing, "extra_real_trades": extra,
            "deviating_pairs": sum(tally.deviating_by_strategy.values()),
            "deviating_trades": tally.deviations, "discrepancies": missing + extra + tally.deviations,
            "stalled_strategies": stalled, "real_drawdown": round(real_dd, 2),
            "tester_drawdown": round(tester_dd, 2), "drawdown_deviation_pct": round(dd_deviation, 2),
            "comparison_detail": {
                "matched_by_strategy": tally.matched_by_strategy,
                "within_tolerance_by_strategy": tally.within_tolerance_by_strategy,
                "deviating_by_strategy": tally.deviating_by_strategy,
                "missing_by_strategy": tally.missing_by_strategy,
                "unmatched_real": unmatched_real,
                "deviation_reasons": {key: value for key, value in tally.deviation_reasons.items() if value},
                "tester_data_issues": tally.tester_data_issues,
                "time_tolerance_seconds": time_limit,
                "methodology": _methodology(request, time_limit),
                "strategy_summary": _strategy_summary(tally, strategies),
                "operation_comparisons": tally.operations,
                "unmatched_real_operations": unmatched_real_operations,
                "drawdown": {
                    "real": round(real_dd, 2),
                    "tester": round(tester_dd, 2),
                    "deviation_pct": round(dd_deviation, 2),
                    "limit_pct": request["drawdown_deviation_warning_pct"],
                    "outside_tolerance": dd_outside,
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
