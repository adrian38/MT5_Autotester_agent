import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ubs_agent_files import score
from tests import test_invalid_stops_diagnostics as fixtures
from ubs.tester_diagnostics import incompatible_volume_metadata
from ubs_agent import evaluate_variant_report, rescore_candidate_scores_only, classify_zero_trade_robustness
from ubs.score import ScoreConfig
from ui.ubs_results_logic import UBSResultsLogicMixin
from manager_node_runtime.node import database_snapshot


class IncompatibleVolumeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InvalidStopsDiagnosticsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.report = self.fixture.report
        self.sidecar = self.fixture.sidecar
        self.text = ("KNDI.NAS,M30: testing of Experts\\EA.ex5\n"
                     "  MaxLots=99\n  StartLots=0.01\n"
                     "Minimum lotsize for this broker is 100.0lots!!\n" + self.fixture.finish)
        self.sidecar.write_text(self.text, encoding="utf-8")

    def detect(self):
        return incompatible_volume_metadata(self.report, "KNDI.NAS", "M30")

    def test_requires_incompatible_maximum_and_attributable_attempt(self):
        self.assertEqual(self.detect()["volume_min"], 100)
        for text in (self.text.replace("MaxLots=99", "MaxLots=100"),
                     self.text.replace("MaxLots=99", "MaxLots=1000"),
                     self.text.replace("MaxLots=99", ""),
                     self.text.replace("KNDI.NAS", "OTHER"),
                     self.text.replace("M30", "H1"),
                     self.text + "KNDI.NAS,M30: testing of Experts\\EA.ex5\n" + self.fixture.finish):
            with self.subTest(text=text):
                self.sidecar.write_text(text, encoding="utf-8")
                self.assertIsNone(self.detect())

    def test_truncated_input_is_not_inferred_from_minimum_warning(self):
        self.sidecar.write_text("Minimum lotsize for this broker is 100.0lots!!\n" + self.fixture.finish)
        self.assertIsNone(self.detect())

    def test_evaluation_rescore_and_card_snapshot(self):
        memory, seed, variant = self.fixture.memory()
        with patch("ubs_agent.score_report_file", return_value=score(-55, symbol="KNDI.NAS", timeframe="M30", trades=0)):
            status, _ = evaluate_variant_report(memory, variant, self.report, ScoreConfig(), {}, "ICTRADING")
        self.assertEqual(status, "rejected")
        self.assertEqual(classify_zero_trade_robustness(self.report, variant)[1]["failure_type"], "incompatible_volume")
        self.sidecar.unlink()
        args = argparse.Namespace(min_trades_w1=12, min_trades_mn=4, rescore_from_reports=False)
        rescore_candidate_scores_only(args, memory, ScoreConfig())
        row = memory.conn.execute("select * from candidates").fetchone()
        self.assertEqual(json.loads(row["metrics_json"])["max_lots"], 99)
        self.assertIn("lotaje incompatible", UBSResultsLogicMixin()._ubs_result_reason(row, row["status"]))
        memory.conn.commit()
        snapshot = database_snapshot(memory.path)
        self.assertEqual(snapshot["execution_failures"]["generation"], {"incompatible_volume": 1})
        self.assertEqual(snapshot["stages"]["generation"], {"rejected": 1})

    def test_migration_preserves_score_and_is_idempotent(self):
        memory, seed, variant = self.fixture.memory()
        memory.record_score(variant.path, score(-55, symbol="KNDI.NAS", timeframe="M30", trades=0), "no_trades", self.report)
        self.assertEqual(memory._reclassify_invalid_stops_no_trades(), 1)
        self.assertEqual(memory._reclassify_invalid_stops_no_trades(), 0)
        row = memory.conn.execute("select * from candidates").fetchone()
        self.assertEqual(row["score"], -55)
        self.assertEqual(json.loads(row["metrics_json"])["failure_type"], "incompatible_volume")
