import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from ubs.degradation import RobustnessDegradationConfig, evaluate_robustness_degradation
from ubs.risk_profit import RiskProfitConfig, combine_robustness_profit_gate, robustness_result_status
from ubs.score import ScoreConfig, ScoreResult, _equity_drawdown, rescore_result, score_report


def sample(**overrides):
    values = dict(
        report_path="report.htm", name="sample", symbol="USDCAD", timeframe="H1",
        score=218.147, accepted=False, net_profit=22.99, raw_net_profit=22.99,
        normalized_net_profit=26.55, net_profit_factor=1.1548,
        net_profit_basis="test", normalization_group="Forex", history_quality=100.0,
        profit_factor=3.2944, recovery_factor=67.6176, drawdown=.34, drawdown_pct=.03,
        trades=1108, positive_month_ratio=59/60, max_month_concentration=.0453,
        avg_trade=.0207, sqn=18.0, reasons=("net_profit",), active_months=60,
        residual_profit_ratio=.876, trade_curve_stability=.99,
        equity_drawdown=5.7, equity_drawdown_pct=.56,
        bootstrap_reps=2000, bootstrap_net_positive_probability=.999,
        bootstrap_pf_p05=1.8,
    )
    values.update(overrides)
    return ScoreResult(**values)


class RiskProfitTests(unittest.TestCase):
    def config(self, mode="enforce"):
        return ScoreConfig(risk_profit=RiskProfitConfig(mode=mode))

    def oos(self, **overrides):
        values = dict(net_profit=5.0, raw_net_profit=5.0, normalized_net_profit=5.77,
                      equity_drawdown=2.0, equity_drawdown_pct=.2, trades=250,
                      active_months=12, profit_factor=2.8, residual_profit_ratio=.5)
        values.update(overrides)
        return sample(**values)

    def combine(self, oos=None, base=None, years=1, mode="enforce", base_start="2020.01.01"):
        cfg = self.config(mode)
        base = base or sample()
        result = rescore_result(oos or self.oos(), cfg, risk_stage="oos")
        b, o = json.loads(base.to_json()), json.loads(result.to_json())
        degradation_cfg = RobustnessDegradationConfig()
        degradation = evaluate_robustness_degradation(
            b, o, base_from_date=base_start, base_to_date="2024.12.31",
            oos_from_date="2025.01.01", oos_to_date=f"{2024+years}.12.31",
            config=degradation_cfg,
        )
        reasons, audit, degradation = combine_robustness_profit_gate(
            b, o, list(result.reasons), degradation, cfg.risk_profit,
            max_drawdown_pct=cfg.max_drawdown_pct, min_recovery_factor=cfg.min_recovery_factor,
            degradation_config=degradation_cfg,
        )
        return replace(result, accepted=not reasons, reasons=tuple(reasons), risk_profit_audit=audit), degradation

    def test_real_case_rescued_only_when_enforced_and_roundtrips(self):
        shadow = rescore_result(sample(), self.config("shadow"))
        enabled = rescore_result(sample(), self.config())
        self.assertFalse(shadow.accepted)
        self.assertTrue(shadow.risk_profit_audit["would_rescue"])
        self.assertTrue(enabled.accepted)
        self.assertEqual(enabled.risk_profit_audit["selected_route"], "risk_adjusted")
        self.assertEqual(ScoreResult.from_json(enabled.to_json()), enabled)
        self.assertNotEqual(shadow.score_config_hash, enabled.score_config_hash)

    def test_does_not_waive_other_thresholds(self):
        result = rescore_result(sample(), replace(self.config(), min_profit_factor=4.0))
        self.assertFalse(result.accepted)
        self.assertIn("profit_factor", result.reasons)

    def test_uses_equity_not_tiny_balance_drawdown(self):
        result = rescore_result(sample(equity_drawdown=20.0), self.config())
        self.assertFalse(result.accepted)
        self.assertAlmostEqual(result.risk_profit_audit["checks"]["recovery"]["value"], 22.99/20)

    def test_unknown_zero_and_nonfinite_equity_never_rescue(self):
        for dd in (None, 0, -1, float("nan"), float("inf")):
            with self.subTest(dd=dd):
                result = rescore_result(sample(equity_drawdown=dd), self.config())
                self.assertFalse(result.accepted)
                self.assertFalse(result.risk_profit_audit["eligible"])

    def test_requires_breadth_and_residual_profit(self):
        for values in (dict(active_months=12), dict(trades=100), dict(positive_month_ratio=.5),
                       dict(residual_profit_ratio=.1), dict(net_profit=-1)):
            with self.subTest(values=values):
                self.assertFalse(rescore_result(sample(**values), self.config()).accepted)

    def test_equity_recovery_is_invariant_to_common_money_scale(self):
        first = rescore_result(sample(), self.config())
        second = rescore_result(sample(net_profit=45.98, equity_drawdown=11.4), self.config())
        self.assertEqual(first.risk_profit_audit["checks"]["recovery"], second.risk_profit_audit["checks"]["recovery"])

    def test_equity_parser_languages_and_relative_maximum(self):
        for metrics in (
            {"Equity Drawdown Maximal": "5.70 (0.55%)", "Equity Drawdown Relative": "0.56% (5.60)"},
            {"Reducción máxima de la equidad": "5,70 (0,56%)"},
            {"Equity Drawdown Maximal": "0.56% (5.70)"},
        ):
            self.assertEqual(_equity_drawdown(SimpleNamespace(metrics=metrics)), (5.7, .56))
        self.assertEqual(_equity_drawdown(SimpleNamespace(metrics={"Balance Drawdown Maximal": "5 (2%)"})), (None, None))
        self.assertEqual(_equity_drawdown(SimpleNamespace(metrics={"Equity Drawdown Maximal": "unknown"})), (None, None))

    def test_oos_uses_shorter_sample_and_requires_relative_checks(self):
        result, degradation = self.combine()
        self.assertTrue(result.accepted)
        self.assertEqual(degradation["risk_basis"], "equity")
        self.assertEqual(result.risk_profit_audit["selected_route"], "risk_adjusted")
        self.assertTrue(result.risk_profit_audit["equity_degradation"]["diagnostics"]["complete"])

    def test_oos_does_not_reject_rescue_using_legacy_balance_recovery(self):
        # Balance recovery 67.6 is a sentinel to the legacy comparison. Equity
        # recovery remains measurable and must appear in the new comparison,
        # over a drawdown floored at 2% of the account: 0.56% of it is not a
        # measurement of risk, and `dd_inflation` already refuses to compare it.
        result, degradation = self.combine()
        self.assertTrue(result.accepted)
        self.assertAlmostEqual(
            degradation["checks"]["recovery_retention"]["base"], 22.99 / (5.7 * 2.0 / .56)
        )

    def test_oos_duration_adjustment_and_degradation_failure(self):
        # El importe y el porcentaje describen la misma cuenta (~1000), como en
        # cualquier reporte real: el suelo del 2% se deriva de esa pareja.
        heavier = self.oos(equity_drawdown=5.0, equity_drawdown_pct=.5)
        one_year, _ = self.combine(heavier)
        five_years, _ = self.combine(heavier, years=5)
        self.assertTrue(one_year.accepted)
        self.assertFalse(five_years.accepted)
        self.assertFalse(five_years.risk_profit_audit["equity_degradation"]["checks"]["recovery_retention"]["accepted"])

    def test_oos_missing_dates_equity_or_bootstrap_is_pending(self):
        cases = [dict(base_start=""), dict(base=sample(equity_drawdown=None)),
                 dict(oos=self.oos(bootstrap_pf_p05=None))]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                result, _ = self.combine(**kwargs)
                self.assertFalse(result.accepted)
                self.assertEqual(robustness_result_status(result), "pending_risk_evidence")

    def test_oos_shadow_keeps_rejection_and_reports_possible_rescue(self):
        result, _ = self.combine(mode="shadow")
        self.assertFalse(result.accepted)
        self.assertTrue(result.risk_profit_audit["would_rescue"])
        self.assertEqual(robustness_result_status(result), "rejected")

    def test_oos_pf_degradation_is_not_waived(self):
        result, _ = self.combine(self.oos(profit_factor=1.3))
        self.assertFalse(result.accepted)
        self.assertFalse(result.risk_profit_audit["eligible"])

    def test_oos_parser_bootstraps_low_profit_candidate_before_combining(self):
        report = SimpleNamespace(
            path="report.htm", name="sample", symbol="USDCAD", timeframe="H1",
            trades=[SimpleNamespace(profit_loss=x) for x in [.1, .1, -.05]*40],
            monthly={2025: {i: .5 for i in range(1,13)}},
            metrics={"Equity Drawdown Maximal": "2.00 (0.2%)", "Balance Drawdown Maximal": "1.00 (0.1%)"},
        )
        with patch("ubs.score._generalization_bootstrap", return_value={"reps": 20, "mean_block": 5,
                  "net_positive_probability": 1, "net_p05": 1, "pf_p05": 1.5}) as bootstrap:
            result = score_report(report, self.config(), risk_stage="oos", include_generalization_bootstrap=True)
        bootstrap.assert_called_once()
        self.assertFalse(result.accepted)  # Comparisons against base are still required.
        self.assertEqual(result.bootstrap_reps, 20)

    def test_invalid_config_is_rejected(self):
        for values in (dict(mode="yes"), dict(min_recovery=0), dict(min_positive_month_ratio=2),
                       dict(min_trades=2.5), dict(oos_min_profit_factor=float("nan"))):
            with self.subTest(values=values), self.assertRaises(ValueError):
                RiskProfitConfig(**values)


if __name__ == "__main__":
    unittest.main()
