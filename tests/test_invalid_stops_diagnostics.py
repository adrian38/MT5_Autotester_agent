import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ubs_agent_files import score
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs.tester_diagnostics import invalid_stops_metadata
from ubs_agent import evaluate_variant_report, evaluate_seed_report, rescore_candidate_scores_only
from ui.ubs_results_logic import UBSResultsLogicMixin
from ui.ubs_seeds_logic import UBSSeedsLogicMixin


class InvalidStopsDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "KNDI.NAS_M30.htm"
        self.report.write_text("<html></html>", encoding="utf-8")
        self.sidecar = self.report.with_suffix(".mt5log.txt")
        self.failure = "failed sell stop 0.1 KNDI.NAS at 6.730 sl: 25.730 tp: -3.770 [Invalid stops]\n"
        self.finish = "Core 01\tKNDI.NAS,M30: 862817 ticks, 4463 bars generated. Test passed in 0:00:03.321.\n"
        self.sidecar.write_text(self.failure + self.finish, encoding="utf-8")

    def metadata(self):
        return invalid_stops_metadata(self.report, "KNDI.NAS", "M30")

    def memory(self):
        memory = AgentMemory(self.root / "memory.sqlite")
        self.addCleanup(memory.close)
        run = memory.create_run(self.root / "source", self.root / "output", 1, 1, 10, True, False)
        seed = Seed(self.root / "seed.set", "KNDI.NAS", "M30", "family", "1")
        variant = Variant(self.root / "candidate.set", seed, "KNDI.NAS", "M30", (), (), "test")
        memory.record_variant(run, 1, variant)
        return memory, seed, variant

    def test_truncated_journal_uses_matching_completion_and_counts_only_failed_orders(self):
        self.sidecar.write_text(self.failure + "CTrade::OrderSend sell stop KNDI.NAS [invalid stops]\n" + self.finish, encoding="utf-8")
        metadata = self.metadata()
        self.assertEqual(metadata["invalid_order_count"], 1)
        self.assertEqual(metadata["invalid_order_count_scope"], "journal_excerpt")
        self.assertIn("tp: -3.770", metadata["invalid_order_sample"])
        self.assertFalse(metadata["retryable"])

    def test_no_attributable_test_or_wrong_symbol_timeframe_does_not_classify(self):
        for text in (self.failure, self.failure + self.finish.replace("M30", "H1"),
                     self.failure + self.finish.replace("KNDI.NAS", "OTHER.NAS"),
                     self.failure.replace("KNDI.NAS", "KNDI.NASX") + self.finish):
            with self.subTest(text=text):
                self.sidecar.write_text(text, encoding="utf-8")
                self.assertIsNone(self.metadata())

    def test_previous_attempt_does_not_contaminate_latest_attempt(self):
        for next_attempt in (self.finish,
                             "KNDI.NAS,M30: testing of Experts\\EA.ex5\n" + self.finish,
                             "OTHER.NAS,M30: testing of Experts\\EA.ex5\n"):
            with self.subTest(next_attempt=next_attempt):
                self.sidecar.write_text(self.failure + self.finish + next_attempt, encoding="utf-8")
                self.assertIsNone(self.metadata())

    def test_base_evaluation_persists_reason_and_rescore_without_journal_keeps_it(self):
        memory, seed, variant = self.memory()
        with patch("ubs_agent.score_report_file", return_value=score(-55, symbol="KNDI.NAS", timeframe="M30", trades=0)):
            status, result = evaluate_variant_report(memory, variant, self.report, ScoreConfig(), {}, "ICTRADING")
        self.assertEqual(status, "rejected")
        self.sidecar.unlink()
        args = argparse.Namespace(min_trades_w1=12, min_trades_mn=4, rescore_from_reports=False)
        rescore_candidate_scores_only(args, memory, ScoreConfig(min_net_profit=999))
        row = memory.conn.execute("select * from candidates").fetchone()
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["accepted"], 0)
        self.assertEqual(json.loads(row["metrics_json"])["failure_type"], "invalid_stops")
        self.assertEqual(json.loads(row["metrics_json"])["score_config"]["min_net_profit"], 999)
        self.assertIn("Invalid stops", UBSResultsLogicMixin()._ubs_result_reason(row, row["status"]))

    def test_real_trades_and_report_mismatch_keep_existing_semantics(self):
        memory, seed, variant = self.memory()
        for symbol, trades, expected in (("KNDI.NAS", 50, "accepted"), ("OTHER.NAS", 0, "report_mismatch")):
            with self.subTest(symbol=symbol), patch("ubs_agent.score_report_file", return_value=score(-55, symbol=symbol, timeframe="M30", trades=trades)):
                status, result = evaluate_variant_report(memory, variant, self.report, ScoreConfig(), {}, "ICTRADING")
                self.assertEqual(status, expected)
                payload = json.loads(memory.conn.execute("select metrics_json from candidates").fetchone()[0])
                self.assertNotIn("failure_type", payload)

    def test_legacy_latest_run_migration_is_idempotent_and_preserves_metrics(self):
        memory, seed, variant = self.memory()
        result = score(-55, symbol="KNDI.NAS", timeframe="M30", trades=0)
        memory.record_score(variant.path, result, "no_trades", self.report)
        self.assertEqual(memory._reclassify_invalid_stops_no_trades(), 1)
        self.assertEqual(memory._reclassify_invalid_stops_no_trades(), 0)
        row = memory.conn.execute("select * from candidates").fetchone()
        payload = json.loads(row["metrics_json"])
        self.assertEqual(row["score"], -55)
        self.assertEqual(payload["net_profit"], result.net_profit)
        self.assertEqual(row["status"], "rejected")

    def test_seed_and_result_views_show_evidence_even_for_legacy_status(self):
        row = {"metrics_json": json.dumps(self.metadata())}
        for status in ("rejected", "no_trades"):
            self.assertIn("Invalid stops", UBSResultsLogicMixin()._ubs_result_reason(row, status))
            self.assertIn("Invalid stops", UBSSeedsLogicMixin()._ubs_seed_reason(row, status))

    def test_seed_evaluation_records_rejection_with_audit(self):
        memory, seed, variant = self.memory()
        seed.path.write_text("[Inputs]", encoding="utf-8")
        memory.prepare_single_seed_evaluation(seed)
        status, _ = evaluate_seed_report(
            memory, seed, self.report, ScoreConfig(), {}, "ICTRADING",
            parsed_result=score(-55, symbol="KNDI.NAS", timeframe="M30", trades=0, accepted=False),
        )
        self.assertEqual(status, "rejected")
        row = memory.seed_score_row(seed.path)
        self.assertEqual(row["status"], "rejected")
        self.assertIn("Invalid stops", UBSSeedsLogicMixin()._ubs_seed_reason(row, status))

    def test_migration_preserves_robustness_audit_and_skips_older_base_runs(self):
        memory, seed, variant = self.memory()
        zero = score(-55, symbol="KNDI.NAS", timeframe="M30", trades=0, accepted=False)
        memory.record_score(variant.path, zero, "no_trades", self.report)
        run = memory.create_run(self.root / "source", self.root / "output", 1, 1, 10, True, False)
        newer = Variant(self.root / "newer.set", seed, "KNDI.NAS", "M30", (), (), "test")
        memory.record_variant(run, 1, newer)
        cid = memory.conn.execute("select id from candidates where set_path=?", (str(newer.path),)).fetchone()[0]
        memory.record_candidate_robustness(cid, run, zero, "no_trades", self.report,
                                         "2025.01.01", "2026.06.01", 15, -20,
                                         degradation={"existing_audit": "keep"})
        self.assertEqual(memory._reclassify_invalid_stops_no_trades(), 1)
        self.assertEqual(memory.conn.execute("select status from candidates where set_path=?", (str(variant.path),)).fetchone()[0], "no_trades")
        row = memory.conn.execute("select * from candidate_robustness").fetchone()
        self.assertEqual(row["status"], "rejected")
        audit = json.loads(row["degradation_json"])
        self.assertEqual(audit["existing_audit"], "keep")
        self.assertEqual(audit["failure_type"], "invalid_stops")
        self.assertEqual(row["negative_bonus"], -20)


if __name__ == "__main__":
    unittest.main()
