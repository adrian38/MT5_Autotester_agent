import tempfile
import unittest
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreResult
from ubs_agent import copy_accepted, final_tick_similarity, recreate_work_dir, robust_status_pending_for_retry


def score(
    value: float,
    *,
    net_profit: float = 100.0,
    profit_factor: float = 2.0,
    drawdown_pct: float = 1.0,
    trades: int = 100,
    history_quality: float | None = 100.0,
) -> ScoreResult:
    return ScoreResult(
        report_path="report.htm",
        name="report",
        symbol="XAUUSD",
        timeframe="H1",
        score=value,
        accepted=True,
        net_profit=net_profit,
        raw_net_profit=net_profit,
        normalized_net_profit=net_profit,
        net_profit_factor=1.0,
        net_profit_basis="test",
        normalization_group="test",
        history_quality=history_quality,
        profit_factor=profit_factor,
        recovery_factor=2.0,
        drawdown=10.0,
        drawdown_pct=drawdown_pct,
        trades=trades,
        positive_month_ratio=1.0,
        max_month_concentration=0.1,
        avg_trade=1.0,
        sqn=1.0,
        reasons=(),
    )


class UBSSetsFileTests(unittest.TestCase):
    def test_copy_accepted_replaces_previous_copy_for_same_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = recreate_work_dir(Path(temp_dir))
            source = root / "candidate.set"
            source.write_text("set", encoding="utf-8")
            accepted_dir = root / "accepted"
            seed = Seed(source, "XAUUSD", "H1", "family", "1")
            variant = Variant(source, seed, "XAUUSD", "H1", (), (), "test")

            first = copy_accepted([(variant, score(10.0))], accepted_dir)
            second = copy_accepted([(variant, score(20.0))], accepted_dir)

            files = sorted(accepted_dir.glob("*.set"))
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(files, second)
            self.assertEqual(files[0].name, "score_0020.00__candidate.set")

    def test_recreate_work_dir_removes_previous_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "work"
            path.mkdir()
            (path / "old.set").write_text("old", encoding="utf-8")

            recreated = recreate_work_dir(path)

            self.assertEqual(recreated, path)
            self.assertTrue(path.exists())
            self.assertEqual(list(path.iterdir()), [])

    def test_final_tick_similarity_requires_history_quality(self) -> None:
        result = final_tick_similarity(
            score(10.0),
            score(10.0, history_quality=None),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertFalse(result["accepted"])
        self.assertIn("history_quality", result["reasons"])

    def test_final_tick_similarity_keeps_net_profit_drift_informational(self) -> None:
        result = final_tick_similarity(
            score(10.0, net_profit=100.0),
            score(10.0, net_profit=200.0),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertTrue(result["accepted"])
        self.assertNotIn("net_profit", result["reasons"])
        self.assertFalse(result["checks"]["net_profit"]["checked"])

    def test_final_tick_similarity_rejects_large_profit_factor_drift(self) -> None:
        result = final_tick_similarity(
            score(10.0, profit_factor=2.0),
            score(10.0, profit_factor=1.0),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertFalse(result["accepted"])
        self.assertIn("profit_factor", result["reasons"])

    def test_robust_pending_retry_includes_diagnostic_statuses(self) -> None:
        for status in ("", None, "no_report", "parse_error", "report_mismatch", "no_trades"):
            self.assertTrue(robust_status_pending_for_retry(status))
        for status in ("accepted", "rejected"):
            self.assertFalse(robust_status_pending_for_retry(status))


if __name__ == "__main__":
    unittest.main()
