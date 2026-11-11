"""Comparacion OHLC vs Real Tick de una pasada del Final Tick."""
from __future__ import annotations


from ubs.score import ScoreResult, run_is_lossless
from ubs_agent_config import LosslessControlGate
from ubs_agent_robustness import (
    _bounded_profit_factor,
    _relative_delta_pct,
)

# Tolerancia absoluta del drawdown, en puntos porcentuales. Convive con la
# razon relativa: pasa el que cumpla cualquiera de las dos.
DEFAULT_MAX_DD_GAP_PP = 3.0


def _economic_direction_check(ohlc_result, real_tick_result, checks, required):
    """Comprueba que ambos modelos conservan la lectura economica basica."""
    comparisons = {
        "score": {
            "ohlc": float(ohlc_result.score),
            "real_tick": float(real_tick_result.score),
            "matches": (float(ohlc_result.score) >= 0) == (float(real_tick_result.score) >= 0),
        },
        "net_profit": {
            "ohlc": float(ohlc_result.normalized_net_profit),
            "real_tick": float(real_tick_result.normalized_net_profit),
            "matches": (float(ohlc_result.normalized_net_profit) >= 0)
            == (float(real_tick_result.normalized_net_profit) >= 0),
        },
        "profit_factor": {
            "ohlc": float(ohlc_result.profit_factor),
            "real_tick": float(real_tick_result.profit_factor),
            "matches": (float(ohlc_result.profit_factor) >= 1.0)
            == (float(real_tick_result.profit_factor) >= 1.0),
        },
    }
    matches = all(item["matches"] for item in comparisons.values())
    checks["economic_direction"] = {
        "accepted": matches if required else True,
        "matches": matches,
        "required": bool(required),
        "checked": bool(required),
        "components": comparisons,
    }
    return matches


def _final_tick_net_check(ohlc_result, real_tick_result, max_net_delta_pct, checks):
    """Compara el beneficio neto normalizado entre OHLC y Real Tick."""
    ohlc_net = float(ohlc_result.normalized_net_profit)
    tick_net  = float(real_tick_result.normalized_net_profit)
    net_denom = max(abs(ohlc_net), abs(tick_net), 1.0)
    net_delta = abs(tick_net - ohlc_net) / net_denom * 100.0
    checks["net_profit"] = {
        "ohlc": round(ohlc_net, 4),
        "real_tick": round(tick_net, 4),
        "delta_pct": round(net_delta, 4),
        "max_delta_pct": round(float(max_net_delta_pct), 4),
        "accepted": True,   # informacional: no bloquea
        "checked": False,
    }


def _final_tick_profit_factor_check(ohlc_result, real_tick_result, max_pf_delta_pct, min_model_profit_factor, checks, lossless_control, reasons):
    """Compara el profit factor entre OHLC y Real Tick."""
    ohlc_pf   = _bounded_profit_factor(ohlc_result.profit_factor)
    tick_pf   = _bounded_profit_factor(real_tick_result.profit_factor)
    if min_model_profit_factor is not None:
        min_pf = float(min_model_profit_factor)
        pf_floor_met = ohlc_pf >= min_pf and tick_pf >= min_pf
        checks["profit_factor_floor"] = {
            "ohlc": round(ohlc_pf, 4),
            "real_tick": round(tick_pf, 4),
            "min_profit_factor": round(min_pf, 4),
            "accepted": True,
            "meets_floor": pf_floor_met,
            "checked": False,
        }
    pf_delta  = _relative_delta_pct(ohlc_pf, tick_pf, floor=1.0)
    # Sin pérdidas en OHLC el PF es el centinela 99 recortado a 10: comparar
    # contra eso exigiría PF de tick >=7.0, que no mide nada de la estrategia.
    pf_similar = lossless_control or pf_delta <= max_pf_delta_pct
    checks["profit_factor"] = {
        "ohlc": round(ohlc_pf, 4),
        "real_tick": round(tick_pf, 4),
        "delta_pct": round(pf_delta, 4),
        "max_delta_pct": round(float(max_pf_delta_pct), 4),
        "accepted": True,
        "within_tolerance": pf_similar,
        "checked": False,
    }


def _final_tick_drawdown_check(
    ohlc_result, real_tick_result, max_dd_delta_pct, max_dd_gap_pp, checks,
    lossless_control, reasons,
):
    """Compara el drawdown entre OHLC y Real Tick."""
    ohlc_dd   = float(ohlc_result.drawdown_pct)
    tick_dd   = float(real_tick_result.drawdown_pct)
    dd_gap_pp = abs(tick_dd - ohlc_dd)
    dd_floor  = max(ohlc_dd, tick_dd, 2.0)
    dd_delta  = dd_gap_pp / dd_floor * 100.0
    # Con OHLC a 0.0 de DD la diferencia relativa es 100% en cuanto el tick pase
    # de 0.7pp: es la misma degeneración que el PF, no una divergencia real.
    # La banda absoluta cubre el mismo defecto en todo el tramo de DD pequeño:
    # con 6.5% contra 3.7% la razon relativa da 44% y rechaza, mientras que un
    # par 20%/27% —mas riesgo y peor— se queda en 26% y pasa. Por debajo de
    # unos pocos puntos de DD el cociente mide ruido de fills, no comportamiento.
    within_gap  = dd_gap_pp <= float(max_dd_gap_pp)
    dd_accepted = lossless_control or within_gap or dd_delta <= max_dd_delta_pct
    if not dd_accepted:
        reasons.append("drawdown_pct")
    checks["drawdown_pct"] = {
        "ohlc": round(ohlc_dd, 4),
        "real_tick": round(tick_dd, 4),
        "delta_pct": round(dd_delta, 4),
        "max_delta_pct": round(float(max_dd_delta_pct), 4),
        "gap_pp": round(dd_gap_pp, 4),
        "max_gap_pp": round(float(max_dd_gap_pp), 4),
        "within_gap": within_gap,
        "accepted": dd_accepted,
        "checked": not lossless_control,
    }
    return tick_dd


def _final_tick_trades_check(ohlc_result, real_tick_result, max_trades_delta_pct, checks, reasons):
    """Compara el numero de operaciones entre OHLC y Real Tick."""
    ohlc_trades   = float(ohlc_result.trades)
    tick_trades   = float(real_tick_result.trades)
    trades_delta  = _relative_delta_pct(ohlc_trades, tick_trades, floor=1.0)
    trades_accepted = trades_delta <= max_trades_delta_pct
    if not trades_accepted:
        reasons.append("trades")
    checks["trades"] = {
        "ohlc": round(ohlc_trades, 4),
        "real_tick": round(tick_trades, 4),
        "delta_pct": round(trades_delta, 4),
        "max_delta_pct": round(float(max_trades_delta_pct), 4),
        "accepted": trades_accepted,
        "checked": True,
    }
    return tick_trades


def _final_tick_absolute_gate(real_tick_result, lossless_control_gate, absolute_gate, checks, reasons, tick_dd, tick_trades):
    """Diagnosticos absolutos de las pasadas lossless del Final Tick 6M."""
    if absolute_gate:
        gate = lossless_control_gate
        for name, observed, limit, ok in (
            ("tick_trades", tick_trades, float(gate.min_trades), tick_trades >= gate.min_trades),
            (
                "tick_net_profit",
                float(real_tick_result.normalized_net_profit),
                gate.min_normalized_net_profit,
                float(real_tick_result.normalized_net_profit) >= gate.min_normalized_net_profit,
            ),
            (
                "tick_profit_factor",
                float(real_tick_result.profit_factor),
                gate.min_profit_factor,
                float(real_tick_result.profit_factor) >= gate.min_profit_factor,
            ),
            (
                "tick_drawdown_pct",
                tick_dd,
                gate.max_drawdown_pct,
                tick_dd <= gate.max_drawdown_pct,
            ),
            (
                "tick_recovery_factor",
                float(real_tick_result.recovery_factor),
                gate.min_recovery_factor,
                float(real_tick_result.recovery_factor) >= gate.min_recovery_factor,
            ),
            (
                "tick_positive_month_ratio",
                float(real_tick_result.positive_month_ratio),
                gate.min_positive_month_ratio,
                float(real_tick_result.positive_month_ratio) >= gate.min_positive_month_ratio,
            ),
        ):
            checks[name] = {
                "real_tick": round(float(observed), 4),
                "limit": round(float(limit), 4),
                "accepted": True,
                "meets_limit": bool(ok),
                "checked": False,
            }


def _initialize_similarity_checks(
    ohlc_result: ScoreResult,
    lossless_control_gate: LosslessControlGate | None,
    checks: dict[str, dict[str, object]],
) -> tuple[bool, bool]:
    # Si OHLC no tuvo perdidas, PF y DD relativos degeneran contra centinelas.
    # En 6M se guardan tambien los antiguos limites absolutos, exclusivamente
    # para auditoria; no sustituyen la comparacion ni deciden el veredicto.
    lossless_control = run_is_lossless(ohlc_result)
    absolute_gate = lossless_control and lossless_control_gate is not None
    checks["ohlc_lossless"] = {
        "ohlc_losing_trades": ohlc_result.losing_trades,
        "detected": lossless_control,
        "absolute_gate_applied": False,
        "absolute_diagnostics_applied": absolute_gate,
        "accepted": True,
        "checked": False,
    }
    return lossless_control, absolute_gate


def _final_tick_history_quality(
    result: ScoreResult, minimum: float, reasons: list[str],
) -> float | None:
    history_quality = result.history_quality
    if history_quality is None or history_quality < minimum:
        reasons.append("history_quality")
    return history_quality


def _similarity_payload(
    reasons: list[str], history_quality: float | None, minimum: float,
    checks: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "history_quality": history_quality,
        "min_history_quality": float(minimum),
        "checks": checks,
    }


def final_tick_similarity(
    ohlc_result: ScoreResult,
    real_tick_result: ScoreResult,
    *,
    min_history_quality: float,
    max_net_delta_pct: float,
    max_pf_delta_pct: float,
    max_dd_delta_pct: float,
    max_trades_delta_pct: float,
    max_dd_gap_pp: float = DEFAULT_MAX_DD_GAP_PP,
    min_model_profit_factor: float | None = None,
    lossless_control_gate: LosslessControlGate | None = None,
    require_economic_direction: bool = False,
) -> dict[str, object]:
    """Valida que OHLC y Every Tick reproduzcan un comportamiento parecido.

    Operaciones y drawdown miden comportamiento y bloquean. Las pruebas de
    seeds pueden exigir ademas que score, beneficio y PF conserven direccion.

    El drawdown pasa con la razon relativa o con la banda absoluta
    ``max_dd_gap_pp``: sin ella el mismo criterio es tanto mas estricto cuanto
    menor es el riesgo de las dos patas.
    """
    reasons: list[str] = []
    checks: dict[str, dict[str, object]] = {}
    lossless_control, absolute_gate = _initialize_similarity_checks(
        ohlc_result, lossless_control_gate, checks,
    )

    # 1. History quality
    history_quality = _final_tick_history_quality(
        real_tick_result, min_history_quality, reasons,
    )

    # 2. Resultado economico — informacional, no bloquea.
    _final_tick_net_check(ohlc_result, real_tick_result, max_net_delta_pct, checks)

    # PF relativo y su piso 6M son informacionales.
    _final_tick_profit_factor_check(ohlc_result, real_tick_result, max_pf_delta_pct, min_model_profit_factor, checks, lossless_control, reasons)
    if not _economic_direction_check(
        ohlc_result, real_tick_result, checks, require_economic_direction,
    ) and require_economic_direction:
        reasons.append("economic_direction")

    # 3. Comportamiento — drawdown y numero de operaciones deben parecerse.
    tick_dd = _final_tick_drawdown_check(
        ohlc_result, real_tick_result, max_dd_delta_pct, max_dd_gap_pp, checks,
        lossless_control, reasons,
    )

    tick_trades = _final_tick_trades_check(ohlc_result, real_tick_result, max_trades_delta_pct, checks, reasons)

    # 4. Los antiguos controles absolutos 6M quedan auditables, no bloquean.
    _final_tick_absolute_gate(real_tick_result, lossless_control_gate, absolute_gate, checks, reasons, tick_dd, tick_trades)

    return _similarity_payload(reasons, history_quality, min_history_quality, checks)
