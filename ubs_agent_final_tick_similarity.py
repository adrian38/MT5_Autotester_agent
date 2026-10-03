"""Comparacion OHLC vs Real Tick de una pasada del Final Tick."""
from __future__ import annotations


from ubs.score import ScoreResult, run_is_lossless
from ubs_agent_config import LosslessControlGate
from ubs_agent_robustness import (
    _bounded_profit_factor,
    _relative_delta_pct,
)

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
        pf_floor_accepted = ohlc_pf >= min_pf and tick_pf >= min_pf
        if not pf_floor_accepted:
            reasons.append("profit_factor_floor")
        checks["profit_factor_floor"] = {
            "ohlc": round(ohlc_pf, 4),
            "real_tick": round(tick_pf, 4),
            "min_profit_factor": round(min_pf, 4),
            "accepted": pf_floor_accepted,
            "checked": True,
        }
    pf_delta  = _relative_delta_pct(ohlc_pf, tick_pf, floor=1.0)
    # Sin pérdidas en OHLC el PF es el centinela 99 recortado a 10: comparar
    # contra eso exigiría PF de tick >=7.0, que no mide nada de la estrategia.
    pf_accepted = lossless_control or pf_delta <= max_pf_delta_pct
    if not pf_accepted:
        reasons.append("profit_factor")
    checks["profit_factor"] = {
        "ohlc": round(ohlc_pf, 4),
        "real_tick": round(tick_pf, 4),
        "delta_pct": round(pf_delta, 4),
        "max_delta_pct": round(float(max_pf_delta_pct), 4),
        "accepted": pf_accepted,
        "checked": not lossless_control,
    }


def _final_tick_drawdown_check(ohlc_result, real_tick_result, max_dd_delta_pct, checks, lossless_control, reasons):
    """Compara el drawdown entre OHLC y Real Tick."""
    ohlc_dd   = float(ohlc_result.drawdown_pct)
    tick_dd   = float(real_tick_result.drawdown_pct)
    dd_floor  = max(ohlc_dd, tick_dd, 2.0)
    dd_delta  = abs(tick_dd - ohlc_dd) / dd_floor * 100.0
    # Con OHLC a 0.0 de DD la diferencia relativa es 100% en cuanto el tick pase
    # de 0.7pp: es la misma degeneración que el PF, no una divergencia real.
    dd_accepted = lossless_control or dd_delta <= max_dd_delta_pct
    if not dd_accepted:
        reasons.append("drawdown_pct")
    checks["drawdown_pct"] = {
        "ohlc": round(ohlc_dd, 4),
        "real_tick": round(tick_dd, 4),
        "delta_pct": round(dd_delta, 4),
        "max_delta_pct": round(float(max_dd_delta_pct), 4),
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
    """Puerta absoluta de las pasadas lossless del Final Tick 6M."""
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
            if not ok:
                reasons.append(name)
            checks[name] = {
                "real_tick": round(float(observed), 4),
                "limit": round(float(limit), 4),
                "accepted": bool(ok),
                "checked": True,
            }


def _initialize_similarity_checks(
    ohlc_result: ScoreResult,
    lossless_control_gate: LosslessControlGate | None,
    checks: dict[str, dict[str, object]],
) -> tuple[bool, bool]:
    # La degeneracion de PF y DD no depende de la etapa: si el control no perdio
    # nunca, esas dos comparaciones no miden nada ni en el probe ni en 6M, y se
    # descartan siempre. Lo que si es especifico de 6M es el sustituto: alli hay
    # una barra de calidad a la que caer (la poblacion aceptada tiene PF>=1.2,
    # net mediano 145, RF mediano 1.6) y en el probe no la hay (net mediano
    # 0.38, RF mediano 0.075), porque el probe solo cribra divergencia. Sin gate
    # se descartan las dos y deciden las que siguen siendo medibles.
    lossless_control = run_is_lossless(ohlc_result)
    absolute_gate = lossless_control and lossless_control_gate is not None
    checks["ohlc_lossless"] = {
        "ohlc_losing_trades": ohlc_result.losing_trades,
        "detected": lossless_control,
        "absolute_gate_applied": absolute_gate,
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
    min_model_profit_factor: float | None = None,
    lossless_control_gate: LosslessControlGate | None = None,
) -> dict[str, object]:
    """Decide si OHLC y real-tick son suficientemente parecidos.

    Es la MISMA estrategia ejecutada con dos modelos de datos distintos.
    La pregunta es: ¿dan resultados similares? No cuál es mejor.

    Criterios activos (contribuyen a accepted/rejected):
      - profit_factor  : diferencia relativa simétrica; cap [0,10]; piso 1.0
      - drawdown_pct   : diferencia relativa simétrica; piso 2pp evita falsos fallos en DDs pequeños
      - trades         : diferencia relativa simétrica

    net_profit se guarda como informacional pero NO bloquea la aceptación.
    La escala de normalized_net_profit depende del grupo de normalización y produce
    falsos fallos cuando los valores son pequeños en comparación absoluta.

    Cuando la pata OHLC no tiene ni una operación perdedora, PF y DD dejan de
    compararse en cualquier etapa (quedan como informacionales): son un
    centinela contra un cero, no una divergencia. Si además se pasa
    ``lossless_control_gate`` —solo 6M— la pata de tick tiene que pasar en su
    lugar las puertas absolutas de ``LosslessControlGate``. El veredicto sigue
    siendo accepted/rejected: lo que cambia es que se decide con lo medible.
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

    # 2. Net profit — informacional, no bloquea aceptación
    _final_tick_net_check(ohlc_result, real_tick_result, max_net_delta_pct, checks)

    # 3. Profit factor — simétrico, cap [0, 10], piso 1.0
    _final_tick_profit_factor_check(ohlc_result, real_tick_result, max_pf_delta_pct, min_model_profit_factor, checks, lossless_control, reasons)

    # 4. Drawdown — simétrico, piso 2pp
    tick_dd = _final_tick_drawdown_check(ohlc_result, real_tick_result, max_dd_delta_pct, checks, lossless_control, reasons)

    # 5. Trades — simétrico
    tick_trades = _final_tick_trades_check(ohlc_result, real_tick_result, max_trades_delta_pct, checks, reasons)

    # 6. Control sin pérdidas en 6M: la pata de tick tiene que sostenerse sola.
    _final_tick_absolute_gate(real_tick_result, lossless_control_gate, absolute_gate, checks, reasons, tick_dd, tick_trades)

    return _similarity_payload(reasons, history_quality, min_history_quality, checks)
