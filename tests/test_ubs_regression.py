import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.regression import RegressionRuntime, evaluate_regression_report
from ubs.regression_rules import (
    regression_points,
    regression_points_breakdown,
    validate_regression_date_range,
)
from ubs.score import ScoreConfig, ScoreResult


class UBSRegressionRulesTests(unittest.TestCase):
    def test_default_three_year_range_is_valid(self) -> None:
        self.assertEqual(validate_regression_date_range("2017.01.01", "2019.12.31"), "")

    def test_short_or_malformed_range_is_rejected(self) -> None:
        self.assertIn("al menos", validate_regression_date_range("2019.01.01", "2019.12.31"))
        self.assertIn("YYYY.MM.DD", validate_regression_date_range("2017-01-01", "2019.12.31"))

    def test_points_reward_pass_and_cap_multi_cause_failure(self) -> None:
        self.assertEqual(regression_points("accepted"), 80.0)
        breakdown = regression_points_breakdown(
            "rejected",
            ("net_profit", "profit_factor", "trades", "drawdown_pct", "recovery_factor"),
        )
        self.assertEqual(breakdown["reason_penalty"], 60.0)
        self.assertEqual(breakdown["applied"], -160.0)

    def test_technical_statuses_are_neutral(self) -> None:
        for status in ("no_report", "parse_error", "report_mismatch", "date_mismatch", "no_history"):
            with self.subTest(status=status):
                self.assertEqual(regression_points(status, ("profit_factor",)), 0.0)


class UBSRegressionMemoryTests(unittest.TestCase):
    def test_only_final_tick_6m_accepted_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = AgentMemory(Path(temp_dir) / "memory.sqlite")
            try:
                memory.conn.execute(
                    """
                    insert into runs (
                        id, created_at, source_dir, output_dir, generations,
                        variants_per_seed, max_seeds, execute_backtests, dry_run
                    ) values (1, 'now', 'src', 'out', 1, 1, 1, 1, 0)
                    """
                )
                for candidate_id, six_month_status in ((1, "accepted"), (2, "rejected")):
                    memory.conn.execute(
                        """
                        insert into candidates (
                            id, run_id, generation, seed_path, set_path, symbol,
                            target_symbol, period, family, run_strategy, mutated_keys,
                            missing_lot_keys, policy, score, accepted, status, created_at
                        ) values (?, 1, 1, ?, ?, 'EURUSD', 'EURUSD', 'H1', 'fam', 'strat', '', '',
                                  'test', 100, 1, 'accepted', 'now')
                        """,
                        (candidate_id, f"seed{candidate_id}.set", f"set{candidate_id}.set"),
                    )
                    memory.conn.execute(
                        """
                        insert into candidate_robustness (
                            candidate_id, run_id, status, accepted, from_date, to_date, evaluated_at
                        ) values (?, 1, 'accepted', 1, '', '', 'now')
                        """,
                        (candidate_id,),
                    )
                    memory.conn.execute(
                        """
                        insert into candidate_final_tick_6m (
                            candidate_id, run_id, status, accepted, from_date, to_date, evaluated_at
                        ) values (?, 1, ?, ?, '', '', 'now')
                        """,
                        (candidate_id, six_month_status, int(six_month_status == "accepted")),
                    )
                memory.conn.commit()

                rows = memory.accepted_candidates_for_regression(1)

                self.assertEqual([int(row["id"]) for row in rows], [1])
            finally:
                memory.close()


class UBSRegressionReportTests(unittest.TestCase):
    @staticmethod
    def _result() -> ScoreResult:
        return ScoreResult(
            report_path="report.htm", name="report", symbol="EURUSD", timeframe="H1",
            score=42.0, accepted=True, net_profit=100.0, raw_net_profit=100.0,
            normalized_net_profit=100.0, net_profit_factor=1.0, net_profit_basis="test",
            normalization_group="test", history_quality=None, profit_factor=1.5,
            recovery_factor=1.2, drawdown=20.0, drawdown_pct=10.0, trades=50,
            positive_month_ratio=0.6, max_month_concentration=0.2, avg_trade=2.0,
            sqn=1.0, reasons=(),
        )

    @staticmethod
    def _args() -> SimpleNamespace:
        return SimpleNamespace(
            broker="ROBOFOREX", symbol_suffix="", regression_min_trades_w1=12,
            regression_min_trades_mn=4, regression_from_date="2017.01.01",
            regression_to_date="2019.12.31", regression_positive_points=80.0,
            regression_negative_points=-100.0,
        )

    @staticmethod
    def _variant() -> Variant:
        seed = Seed(Path("seed.set"), "EURUSD", "H1", "fam", "1")
        return Variant(Path("candidate.set"), seed, "EURUSD", "H1", (), (), "test")

    def test_exact_dates_apply_positive_points(self) -> None:
        recorded: list[tuple] = []
        memory = SimpleNamespace(record_candidate_regression=lambda *args: recorded.append(args))
        runtime = RegressionRuntime(
            3, lambda path: path, lambda path: None, lambda row: self._variant(),
            lambda *args, **kwargs: 0, lambda *args, **kwargs: Path("report.htm"),
            lambda value: {}, lambda *args: (True, ""), lambda result: False,
            lambda report: ("2017.01.01", "2019.12.31"), lambda report, variant: None,
        )
        with patch("ubs.regression.score_report_file", return_value=self._result()):
            status = evaluate_regression_report(
                memory, self._args(), runtime, ScoreConfig(), {}, 1, 7,
                self._variant(), Path("report.htm"),
            )
        self.assertEqual(status, "accepted")
        self.assertEqual(recorded[0][2], "accepted")
        self.assertEqual(recorded[0][10], 80.0)

    def test_shifted_report_dates_are_neutral_and_retryable(self) -> None:
        recorded: list[tuple] = []
        memory = SimpleNamespace(record_candidate_regression=lambda *args: recorded.append(args))
        runtime = RegressionRuntime(
            3, lambda path: path, lambda path: None, lambda row: self._variant(),
            lambda *args, **kwargs: 0, lambda *args, **kwargs: Path("report.htm"),
            lambda value: {}, lambda *args: (True, ""), lambda result: False,
            lambda report: ("2017.06.01", "2019.12.31"), lambda report, variant: None,
        )
        with patch("ubs.regression.score_report_file", return_value=self._result()):
            status = evaluate_regression_report(
                memory, self._args(), runtime, ScoreConfig(), {}, 1, 7,
                self._variant(), Path("report.htm"),
            )
        self.assertEqual(status, "date_mismatch")
        self.assertEqual(recorded[0][10], 0.0)

    def test_losing_six_month_acceptance_invalidates_regression(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = AgentMemory(Path(temp_dir) / "memory.sqlite")
            try:
                memory.conn.execute(
                    """
                    insert into candidates (
                        id, run_id, generation, seed_path, set_path, symbol,
                        target_symbol, period, family, run_strategy, mutated_keys,
                        missing_lot_keys, policy, score, accepted, status, created_at
                    ) values (1, 1, 1, 'seed.set', 'set.set', 'EURUSD', 'EURUSD', 'H1', 'fam',
                              'strat', '', '', 'test', 100, 1, 'accepted', 'now')
                    """
                )
                memory.record_candidate_regression(
                    1, 1, "accepted", None, None, "{}", "2017.01.01", "2019.12.31", 80, -100, 80
                )
                memory.record_candidate_final_tick(
                    1, 1, "rejected", None, None, None, None, None, None, 80,
                    "2026.01.01", "2026.06.30", 35, 35, 35, 35, final_tick_stage="six_month",
                )

                row = memory.conn.execute(
                    "select 1 from candidate_regression where candidate_id=1"
                ).fetchone()
                self.assertIsNone(row)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
