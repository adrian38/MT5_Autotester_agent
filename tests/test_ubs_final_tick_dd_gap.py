"""Banda absoluta del drawdown en la similitud OHLC vs Real Tick.

La razon relativa sola es tanto mas estricta cuanto menor es el riesgo de las
dos patas: el denominador es el propio drawdown. En la memoria ICTRADING eso
dejaba el filtro reducido a una sola causa — 26 de 26 rechazos del tramo corto
y 24 de 28 del 6M caian por `drawdown_pct`, y en la mayoria el Real Tick habia
aguantado *menos* drawdown que el modelo OHLC.

El caso que lo motiva es la seed 686 (Daytrade_Pro XAUUSD D1, tramo 6M
2026.01.01 -> 2026.06.30): mismas 21 operaciones en las dos patas, DD 6.52% en
OHLC contra 3.66% en tick, mas beneficio y mas PF en tick. La razon relativa da
43.9% y rechazaba, mientras que un par 20% / 27% —mas riesgo, y empeorando—
se queda en 25.9% y pasaba. La banda absoluta corta esa inversion sin tocar la
razon relativa: pasa el que cumpla cualquiera de las dos.
"""

import unittest

from ubs.score import ScoreResult
from ubs_agent import final_tick_similarity


def make_result(**overrides) -> ScoreResult:
    payload = dict(
        report_path="report.htm",
        name="ea",
        symbol="XAUUSD",
        timeframe="D1",
        score=0.0,
        accepted=False,
        net_profit=0.0,
        raw_net_profit=0.0,
        normalized_net_profit=28.08,
        net_profit_factor=0.2616,
        net_profit_basis="ictrading_notional_normalization_ref1000",
        normalization_group="Metals",
        history_quality=100.0,
        profit_factor=2.0542,
        recovery_factor=1.5337,
        drawdown=69.98,
        drawdown_pct=6.52,
        losing_trades=8,
        trades=21,
        positive_month_ratio=0.8333,
        max_month_concentration=0.474,
        avg_trade=5.111,
        sqn=1.4214,
        reasons=(),
    )
    payload.update(overrides)
    return ScoreResult(**payload)


COMMON = dict(
    min_history_quality=80.0,
    max_net_delta_pct=35.0,
    max_pf_delta_pct=35.0,
    max_dd_delta_pct=35.0,
    max_trades_delta_pct=35.0,
)


def seed_686_ohlc() -> ScoreResult:
    return make_result()


def seed_686_tick(**overrides) -> ScoreResult:
    base = dict(
        normalized_net_profit=34.62,
        profit_factor=2.9779,
        recovery_factor=3.3657,
        drawdown=39.32,
        drawdown_pct=3.66,
        losing_trades=7,
    )
    return make_result(**{**base, **overrides})


class DrawdownGapBandTests(unittest.TestCase):
    def test_the_seed_686_case_is_accepted_by_the_absolute_band(self) -> None:
        """Mismas 21 ops y 2.86pp de DD a favor del tick: no es divergencia."""
        similarity = final_tick_similarity(seed_686_ohlc(), seed_686_tick(), **COMMON)
        self.assertTrue(similarity["accepted"])
        self.assertEqual(similarity["reasons"], [])
        check = similarity["checks"]["drawdown_pct"]
        self.assertTrue(check["accepted"])
        self.assertTrue(check["within_gap"])
        self.assertAlmostEqual(check["gap_pp"], 2.86, places=2)
        self.assertEqual(check["max_gap_pp"], 3.0)
        # La razon relativa sigue registrada tal cual: la banda no la maquilla.
        self.assertGreater(check["delta_pct"], check["max_delta_pct"])

    def test_a_wide_drawdown_divergence_is_still_rejected(self) -> None:
        """27.01% contra 13.05% (13.96pp) sigue siendo comportamiento distinto."""
        similarity = final_tick_similarity(
            make_result(drawdown_pct=27.01), make_result(drawdown_pct=13.05), **COMMON
        )
        self.assertFalse(similarity["accepted"])
        self.assertEqual(similarity["reasons"], ["drawdown_pct"])
        self.assertFalse(similarity["checks"]["drawdown_pct"]["within_gap"])

    def test_the_relative_ratio_still_decides_above_the_band(self) -> None:
        """Con DD grande manda la razon relativa: 20% -> 27% son 25.9%."""
        similarity = final_tick_similarity(
            make_result(drawdown_pct=20.0), make_result(drawdown_pct=27.0), **COMMON
        )
        self.assertTrue(similarity["accepted"])
        check = similarity["checks"]["drawdown_pct"]
        self.assertFalse(check["within_gap"])
        self.assertLessEqual(check["delta_pct"], check["max_delta_pct"])

    def test_the_band_does_not_rescue_a_divergence_in_trades(self) -> None:
        """El drawdown deja de bloquear, pero las operaciones siguen midiendo."""
        similarity = final_tick_similarity(
            seed_686_ohlc(), seed_686_tick(trades=9), **COMMON
        )
        self.assertFalse(similarity["accepted"])
        self.assertEqual(similarity["reasons"], ["trades"])

    def test_a_zero_band_restores_the_previous_rule(self) -> None:
        """El limite es configurable: con 0pp vuelve a decidir solo la razon."""
        similarity = final_tick_similarity(
            seed_686_ohlc(), seed_686_tick(), max_dd_gap_pp=0.0, **COMMON
        )
        self.assertFalse(similarity["accepted"])
        self.assertEqual(similarity["reasons"], ["drawdown_pct"])
        self.assertFalse(similarity["checks"]["drawdown_pct"]["within_gap"])


if __name__ == "__main__":
    unittest.main()
