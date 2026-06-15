import json
import unittest

from ubs.score import SCORE_FORMULA_VERSION, ScoreConfig, ScoreResult


class UBSScoreTests(unittest.TestCase):
    def test_score_result_accepts_legacy_json_without_version_metadata(self) -> None:
        payload = {
            "report_path": "report.htm",
            "name": "report",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "score": 100.0,
            "accepted": True,
            "net_profit": 100.0,
            "raw_net_profit": 100.0,
            "normalized_net_profit": 100.0,
            "net_profit_factor": 1.0,
            "net_profit_basis": "legacy",
            "normalization_group": "legacy",
            "history_quality": 99.0,
            "profit_factor": 1.5,
            "recovery_factor": 2.0,
            "drawdown": 10.0,
            "drawdown_pct": 1.0,
            "trades": 100,
            "positive_month_ratio": 1.0,
            "max_month_concentration": 0.2,
            "avg_trade": 1.0,
            "sqn": 2.0,
            "reasons": [],
        }

        result = ScoreResult(**json.loads(json.dumps(payload)))

        self.assertEqual(result.score_formula_version, SCORE_FORMULA_VERSION)
        self.assertEqual(result.score_config, {})
        self.assertEqual(result.score_config_hash, "")

    def test_score_config_hash_is_stable_and_changes_with_thresholds(self) -> None:
        base = ScoreConfig()
        same = ScoreConfig()
        changed = ScoreConfig(min_profit_factor=1.3)

        self.assertEqual(base.stable_hash(), same.stable_hash())
        self.assertNotEqual(base.stable_hash(), changed.stable_hash())


if __name__ == "__main__":
    unittest.main()
