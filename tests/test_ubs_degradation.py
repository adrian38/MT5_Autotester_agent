import unittest

from ubs.degradation import RobustnessDegradationConfig, evaluate_robustness_degradation


class UBSRobustnessDegradationTests(unittest.TestCase):
    def test_measures_edge_retention_and_duration_adjusted_net(self) -> None:
        result = evaluate_robustness_degradation(
            {
                "normalized_net_profit": 500.0,
                "profit_factor": 2.0,
                "recovery_factor": 2.4,
                "drawdown_pct": 10.0,
                "trades": 100,
                "positive_month_ratio": 0.7,
            },
            {
                "normalized_net_profit": 85.0,
                "profit_factor": 1.3,
                "recovery_factor": 1.2,
                "drawdown_pct": 18.0,
                "trades": 50,
                "positive_month_ratio": 0.5,
            },
            base_from_date="2020.01.01",
            base_to_date="2024.12.31",
            oos_from_date="2025.01.01",
            oos_to_date="2026.06.01",
        )

        checks = result["checks"]
        self.assertAlmostEqual(checks["net_retention"]["value"], 0.60, delta=0.01)
        self.assertAlmostEqual(checks["pf_edge_retention"]["value"], 0.30, places=6)
        self.assertAlmostEqual(checks["recovery_retention"]["value"], 0.50, places=6)
        self.assertAlmostEqual(checks["dd_inflation"]["value"], 1.80, places=6)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reasons"], ["degradation_profit_factor"])

    def test_all_relative_checks_can_pass(self) -> None:
        result = evaluate_robustness_degradation(
            {
                "normalized_net_profit": 500.0,
                "profit_factor": 2.0,
                "recovery_factor": 2.0,
                "drawdown_pct": 10.0,
            },
            {
                "normalized_net_profit": 100.0,
                "profit_factor": 1.6,
                "recovery_factor": 1.2,
                "drawdown_pct": 15.0,
            },
            base_from_date="2020.01.01",
            base_to_date="2024.12.31",
            oos_from_date="2025.01.01",
            oos_to_date="2026.06.01",
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["reasons"], [])

    def test_missing_dates_skip_only_duration_dependent_check(self) -> None:
        result = evaluate_robustness_degradation(
            {"normalized_net_profit": 500.0, "profit_factor": 2.0, "recovery_factor": 2.0, "drawdown_pct": 10.0},
            {"normalized_net_profit": 20.0, "profit_factor": 1.6, "recovery_factor": 1.2, "drawdown_pct": 15.0},
            base_from_date="",
            base_to_date="",
            oos_from_date="",
            oos_to_date="",
        )

        self.assertFalse(result["checks"]["net_retention"]["available"])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["diagnostics"]["complete"])

    def test_zero_threshold_disables_a_check(self) -> None:
        result = evaluate_robustness_degradation(
            {"normalized_net_profit": 500.0, "profit_factor": 2.0, "recovery_factor": 2.0, "drawdown_pct": 10.0},
            {"normalized_net_profit": 10.0, "profit_factor": 1.1, "recovery_factor": 0.1, "drawdown_pct": 50.0},
            base_from_date="2020.01.01",
            base_to_date="2024.12.31",
            oos_from_date="2025.01.01",
            oos_to_date="2026.06.01",
            config=RobustnessDegradationConfig(0.0, 0.0, 0.0, 0.0),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["reasons"], [])


if __name__ == "__main__":
    unittest.main()
