import json
import sqlite3
import unittest

from tools.fast_rescore_from_metrics import Gates, rescore_robustness_stage, rescore_stage
from ubs.degradation import RobustnessDegradationConfig, evaluate_robustness_degradation


def _metrics(raw_net: float, *, profit_factor: float = 1.5) -> dict:
    return {
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "raw_net_profit": raw_net,
        "net_profit": raw_net,
        "normalized_net_profit": raw_net,
        "net_profit_factor": 1.0,
        "profit_factor": profit_factor,
        "recovery_factor": 2.0,
        "drawdown_pct": 10.0,
        "trades": 100,
        "positive_month_ratio": 0.75,
        "max_month_concentration": 0.2,
        "residual_profit_ratio": 0.5,
        "trade_curve_stability": 0.9,
        "sqn": 2.0,
        "accepted": True,
        "reasons": [],
    }


class FastRescoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gates = Gates(100.0, 1.2, 50, 25.0, 1.0, 0.0, 12, 4)

    def test_scored_stage_keeps_metrics_accepted_in_sync_with_status(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "create table candidates (id integer primary key, status text, score real, "
                "accepted integer, metrics_json text)"
            )
            conn.execute(
                "insert into candidates values (1, 'accepted', 10, 1, ?)",
                (json.dumps(_metrics(100.0)),),
            )

            rescore_stage(conn, "candidates", ["id"], self.gates, "ROBOFOREX", False)

            status, accepted, metrics_raw = conn.execute(
                "select status, accepted, metrics_json from candidates where id=1"
            ).fetchone()
            metrics = json.loads(metrics_raw)
            self.assertEqual(status, "rejected")
            self.assertEqual(accepted, 0)
            self.assertFalse(metrics["accepted"])
            self.assertIn("net_profit", metrics["reasons"])
        finally:
            conn.close()

    def test_robustness_rescore_retains_degradation_rejection(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("create table candidates (id integer primary key, metrics_json text)")
            conn.execute(
                "create table candidate_robustness (candidate_id integer, run_id integer, "
                "status text, score real, accepted integer, metrics_json text, degradation_json text)"
            )
            base = _metrics(1000.0, profit_factor=2.0)
            oos = _metrics(500.0, profit_factor=1.2)
            config = RobustnessDegradationConfig(
                min_net_retention=0.0,
                min_pf_edge_retention=0.5,
                min_recovery_retention=0.0,
                max_dd_inflation=0.0,
                min_trade_rate_retention=0.0,
                min_residual_profit_ratio=0.0,
                min_oos_positive_month_ratio=0.0,
                min_trade_curve_stability=0.0,
                min_stability_retention=0.0,
                min_bootstrap_net_positive_probability=0.0,
                min_bootstrap_pf_p05=0.0,
            )
            degradation = evaluate_robustness_degradation(
                base,
                oos,
                base_from_date="2020.01.01",
                base_to_date="2024.12.31",
                oos_from_date="2025.01.01",
                oos_to_date="2026.06.30",
                config=config,
            )
            conn.execute("insert into candidates values (1, ?)", (json.dumps(base),))
            conn.execute(
                "insert into candidate_robustness values (1, 7, 'rejected', 10, 0, ?, ?)",
                (json.dumps(oos), json.dumps(degradation)),
            )
            robust_gates = Gates(20.0, 1.2, 50, 25.0, 1.0, 0.0, 12, 4)

            rescore_robustness_stage(conn, robust_gates, "ROBOFOREX", False)

            status, accepted, metrics_raw, degradation_raw = conn.execute(
                "select status, accepted, metrics_json, degradation_json "
                "from candidate_robustness where candidate_id=1"
            ).fetchone()
            metrics = json.loads(metrics_raw)
            audit = json.loads(degradation_raw)
            self.assertEqual(status, "rejected")
            self.assertEqual(accepted, 0)
            self.assertFalse(metrics["accepted"])
            self.assertIn("degradation_profit_factor", metrics["reasons"])
            self.assertFalse(audit["accepted"])
            self.assertTrue(audit["absolute_accepted"])
            self.assertFalse(audit["final_accepted"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
