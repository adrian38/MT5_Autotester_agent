import tempfile
import unittest
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreResult
from ubs.universe import seed_symbol_disabled
from ubs_agent import copy_accepted, final_tick_similarity, recreate_work_dir, robust_status_pending_for_retry, validate_seed_backtest_set, write_set_force_symbol
from run_tests import parse_symbol_map


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
    def test_crudeoil_seed_is_disabled_when_wti_is_disabled(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertTrue(seed_symbol_disabled(seed, {"WTI"}, symbol_map))

    def test_seed_validation_rejects_incomplete_ubs_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.set"
            path.write_text(
                "\n".join(
                    [
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=60||5||0||16385||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(path, "XAUUSD", "H1", "family", "")

            issues = validate_seed_backtest_set(seed)

            self.assertIn("sin ForceSymbol", issues)
            self.assertIn("sin Run_Strategy valido", issues)
            self.assertIn("Entry_Timing=60 no es timeframe MT5 valido", issues)

    def test_seed_validation_accepts_bound_ubs_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "good.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "ST1_Timeframe=16385||0||0||49153||N",
                        "Entry_Timing=16385||5||0||16385||N",
                        "ATR_Timeframe=16385||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(path, "XAUUSD", "H1", "family", "1")

            self.assertEqual(validate_seed_backtest_set(seed), [])

    def test_write_set_force_symbol_adds_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seed.set"
            path.write_text("Run_Strategy=1||1||0||2||N\nST1_Timeframe=16385||0||0||49153||N", encoding="utf-8")

            write_set_force_symbol(path, path, "XAUUSD")

            self.assertIn("ForceSymbol=XAUUSD", path.read_text(encoding="utf-8"))

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
