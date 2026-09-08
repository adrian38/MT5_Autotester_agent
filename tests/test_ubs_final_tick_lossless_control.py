"""Final Tick cuando el control OHLC cierra sin ninguna operacion perdedora.

En ese estado profit_factor queda en el centinela 99 (recortado a 10) y
drawdown_pct en 0.0. Compararlos exige PF de tick >=7.0 y DD de tick <=0.7pp, o
sea que el candidato se rechazaba por el artefacto y no por su comportamiento.

Comportamiento fijado aqui, distinto por etapa y por un motivo medido:
  - siempre: PF y DD dejan de comparase, quedan como informacionales.
  - 6M: en su lugar la pata de tick pasa puertas absolutas (su poblacion
    aceptada si tiene barra de calidad: PF>=1.2, net mediano 145, RF 1.6).
  - probe: no hay sustituto, porque su poblacion aceptada no tiene barra
    ninguna (net mediano 0.38, RF mediano 0.075); solo cribra divergencia.

En ambos casos el veredicto sigue terminando en accepted o rejected.
"""

import unittest

from ubs.score import ScoreResult, run_is_lossless
from ubs_agent import LosslessControlGate, final_tick_similarity


def make_result(**overrides) -> ScoreResult:
    payload = dict(
        report_path="report.htm",
        name="ea",
        symbol="ROKU+",
        timeframe="H1",
        score=0.0,
        accepted=False,
        net_profit=0.0,
        raw_net_profit=0.0,
        normalized_net_profit=0.0,
        net_profit_factor=6.8913,
        net_profit_basis="axi_notional_normalization_ref1000",
        normalization_group="Stocks",
        history_quality=99.0,
        profit_factor=1.5,
        recovery_factor=1.5,
        drawdown=1.0,
        drawdown_pct=2.4,
        trades=27,
        positive_month_ratio=0.833,
        max_month_concentration=0.4,
        avg_trade=0.7,
        sqn=1.0,
        reasons=(),
    )
    payload.update(overrides)
    return ScoreResult(**payload)


def lossless_ohlc(**overrides) -> ScoreResult:
    """Pata OHLC degenerada: sin perdidas, PF centinela y DD 0."""
    base = dict(
        profit_factor=99.0,
        recovery_factor=99.0,
        drawdown=0.0,
        drawdown_pct=0.0,
        losing_trades=0,
        trades=24,
        normalized_net_profit=300.32,
    )
    return make_result(**{**base, **overrides})


def healthy_tick(**overrides) -> ScoreResult:
    """Pata real-tick del caso real que motivo el cambio (run 361, ROKU+ H1)."""
    base = dict(
        profit_factor=1.7937,
        recovery_factor=0.7938,
        drawdown_pct=2.4,
        losing_trades=9,
        trades=27,
        normalized_net_profit=131.28,
        positive_month_ratio=0.833,
    )
    return make_result(**{**base, **overrides})


GATE = LosslessControlGate()
COMMON = dict(
    min_history_quality=80.0,
    max_net_delta_pct=35.0,
    max_pf_delta_pct=30.0,
    max_dd_delta_pct=35.0,
    max_trades_delta_pct=35.0,
    min_model_profit_factor=1.2,
)


class RunIsLosslessTests(unittest.TestCase):
    def test_zero_losing_trades_with_flat_drawdown_is_lossless(self) -> None:
        self.assertTrue(run_is_lossless(lossless_ohlc()))

    def test_a_losing_trade_is_not_lossless(self) -> None:
        self.assertFalse(run_is_lossless(healthy_tick()))

    def test_legacy_row_without_the_field_falls_back_to_the_sentinel(self) -> None:
        legacy = make_result(profit_factor=99.0, drawdown_pct=0.0, losing_trades=None)
        self.assertTrue(run_is_lossless(legacy))

    def test_a_real_profit_factor_above_99_is_not_the_sentinel(self) -> None:
        """La memoria tiene 28 filas con PF real >99; no son runs sin perdidas."""
        real = make_result(profit_factor=621.75, drawdown_pct=1.2, losing_trades=None)
        self.assertFalse(run_is_lossless(real))

    def test_drawdown_above_zero_rules_out_lossless_even_with_the_sentinel(self) -> None:
        self.assertFalse(run_is_lossless(make_result(profit_factor=99.0, drawdown_pct=0.4)))


class LosslessControlFallbackTests(unittest.TestCase):
    def test_probe_drops_the_degenerate_checks_without_absolute_gates(self) -> None:
        """Probe (1M): sin gate se descartan PF y DD, pero no hay sustituto.

        El probe solo cribra divergencia; su poblacion aceptada no tiene barra
        de calidad (net mediano 0.38, RF mediano 0.075), asi que no hay nada
        absoluto a lo que caer. Deciden las comprobaciones que siguen siendo
        medibles con un control degenerado.
        """
        similarity = final_tick_similarity(lossless_ohlc(), healthy_tick(), **COMMON)
        self.assertTrue(similarity["accepted"])
        self.assertEqual(similarity["reasons"], [])
        checks = similarity["checks"]
        self.assertTrue(checks["ohlc_lossless"]["detected"])
        self.assertFalse(checks["ohlc_lossless"]["absolute_gate_applied"])
        self.assertFalse(checks["profit_factor"]["checked"])
        self.assertFalse(checks["drawdown_pct"]["checked"])
        self.assertNotIn("tick_recovery_factor", checks)

    def test_probe_still_rejects_on_what_remains_measurable(self) -> None:
        """Las 2 de 65 que hoy caen por 'trades' tienen que seguir cayendo."""
        similarity = final_tick_similarity(lossless_ohlc(trades=10), healthy_tick(), **COMMON)
        self.assertFalse(similarity["accepted"])
        self.assertEqual(similarity["reasons"], ["trades"])

    def test_gate_replaces_the_degenerate_checks_and_accepts_a_solid_tick_leg(self) -> None:
        tick = healthy_tick(recovery_factor=1.2)
        similarity = final_tick_similarity(
            lossless_ohlc(), tick, lossless_control_gate=GATE, **COMMON
        )
        self.assertTrue(similarity["accepted"])
        self.assertEqual(similarity["reasons"], [])
        checks = similarity["checks"]
        self.assertTrue(checks["ohlc_lossless"]["absolute_gate_applied"])
        # Las dos degeneradas quedan registradas pero ya no deciden.
        self.assertFalse(checks["profit_factor"]["checked"])
        self.assertFalse(checks["drawdown_pct"]["checked"])
        self.assertTrue(checks["profit_factor"]["accepted"])
        self.assertTrue(checks["drawdown_pct"]["accepted"])
        # Y las absolutas si.
        self.assertTrue(checks["tick_recovery_factor"]["checked"])
        self.assertEqual(checks["tick_recovery_factor"]["limit"], 0.75)

    def test_gate_still_reaches_a_rejection_on_a_real_shortfall(self) -> None:
        """El caso ROKU+ del run 361: RF de tick 0.7938 pasa; 0.71 no."""
        weak = healthy_tick(recovery_factor=0.712)
        similarity = final_tick_similarity(
            lossless_ohlc(), weak, lossless_control_gate=GATE, **COMMON
        )
        self.assertFalse(similarity["accepted"])
        self.assertEqual(similarity["reasons"], ["tick_recovery_factor"])
        self.assertEqual(similarity["checks"]["tick_recovery_factor"]["real_tick"], 0.712)

    def test_the_run_361_cluster_splits_instead_of_being_rejected_wholesale(self) -> None:
        gate_kwargs = dict(lossless_control_gate=GATE, **COMMON)
        above = final_tick_similarity(lossless_ohlc(), healthy_tick(), **gate_kwargs)
        below = final_tick_similarity(
            lossless_ohlc(), healthy_tick(recovery_factor=0.746), **gate_kwargs
        )
        self.assertTrue(above["accepted"])
        self.assertFalse(below["accepted"])

    def test_every_absolute_gate_can_fire(self) -> None:
        cases = {
            "tick_trades": dict(trades=20),
            "tick_net_profit": dict(normalized_net_profit=5.0),
            "tick_profit_factor": dict(profit_factor=1.1),
            "tick_drawdown_pct": dict(drawdown_pct=25.0),
            "tick_recovery_factor": dict(recovery_factor=0.2),
            "tick_positive_month_ratio": dict(positive_month_ratio=0.2),
        }
        for reason, override in cases.items():
            with self.subTest(reason=reason):
                similarity = final_tick_similarity(
                    lossless_ohlc(),
                    healthy_tick(**{"recovery_factor": 1.2, **override}),
                    lossless_control_gate=GATE,
                    **COMMON,
                )
                self.assertIn(reason, similarity["reasons"])
                self.assertFalse(similarity["accepted"])

    def test_trades_delta_and_history_quality_survive_the_fallback(self) -> None:
        """Lo que si es medible con un control degenerado se sigue midiendo."""
        similarity = final_tick_similarity(
            lossless_ohlc(trades=10),
            healthy_tick(recovery_factor=1.2, history_quality=50.0),
            lossless_control_gate=GATE,
            **COMMON,
        )
        self.assertIn("trades", similarity["reasons"])
        self.assertIn("history_quality", similarity["reasons"])

    def test_profit_factor_floor_still_applies_to_the_tick_leg(self) -> None:
        similarity = final_tick_similarity(
            lossless_ohlc(),
            healthy_tick(profit_factor=1.0, recovery_factor=1.2),
            lossless_control_gate=GATE,
            **COMMON,
        )
        self.assertIn("profit_factor_floor", similarity["reasons"])

    def test_a_non_degenerate_control_is_unaffected_by_the_gate(self) -> None:
        """Con control normal el gate no debe tocar nada."""
        ohlc = make_result(profit_factor=1.9, drawdown_pct=2.2, losing_trades=8, trades=26)
        similarity = final_tick_similarity(
            ohlc, healthy_tick(), lossless_control_gate=GATE, **COMMON
        )
        self.assertTrue(similarity["accepted"])
        self.assertTrue(similarity["checks"]["profit_factor"]["checked"])
        self.assertTrue(similarity["checks"]["drawdown_pct"]["checked"])
        self.assertNotIn("tick_recovery_factor", similarity["checks"])


if __name__ == "__main__":
    unittest.main()
