import tempfile
import unittest
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreResult
from ubs_agent import copy_accepted, recreate_work_dir


def score(value: float) -> ScoreResult:
    return ScoreResult(
        report_path="report.htm",
        name="report",
        symbol="XAUUSD",
        timeframe="H1",
        score=value,
        accepted=True,
        net_profit=100.0,
        raw_net_profit=100.0,
        normalized_net_profit=100.0,
        net_profit_factor=1.0,
        net_profit_basis="test",
        normalization_group="test",
        profit_factor=2.0,
        recovery_factor=2.0,
        drawdown=10.0,
        drawdown_pct=1.0,
        trades=100,
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


if __name__ == "__main__":
    unittest.main()
