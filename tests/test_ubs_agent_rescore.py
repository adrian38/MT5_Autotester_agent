import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ubs.score import ScoreConfig
from ubs_agent import rescore_candidate_scores_only


class UBSAgentRescoreTests(unittest.TestCase):
    def test_rescore_candidates_preserves_history_probe_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "probe.htm"
            report.write_text("report", encoding="utf-8")
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.execute(
                """
                create table candidates (
                    id integer primary key,
                    run_id integer,
                    generation integer,
                    status text,
                    report_path text,
                    set_path text,
                    seed_path text,
                    symbol text,
                    period text,
                    family text,
                    run_strategy text,
                    target_symbol text,
                    mutated_keys text,
                    missing_lot_keys text,
                    policy text,
                    timeframe_keys text
                )
                """
            )
            db.execute(
                """
                insert into candidates (
                    id, run_id, generation, status, report_path, set_path, seed_path, symbol,
                    period, family, run_strategy, target_symbol, mutated_keys, missing_lot_keys,
                    policy, timeframe_keys
                ) values (1, 0, 0, 'history_ok', ?, ?, ?, 'US30.sa', 'H1', 'seed', '', 'US30.sa', '', '', 'history_probe', '')
                """,
                (str(report), str(root / "probe.set"), str(root / "seed.set")),
            )
            memory = argparse.Namespace(conn=db, path=root / "memory.sqlite")
            args = argparse.Namespace(
                symbol_map="",
                broker="AXI",
                min_trades_w1=12,
                min_trades_mn=4,
                symbol_suffix=".sa",
            )

            with (
                patch("ubs_agent.evaluate_history_probe", return_value=("history_ok", None)) as history_probe,
                patch("ubs_agent.evaluate_variant_report", return_value=("accepted", None)) as candidate_score,
            ):
                self.assertEqual(rescore_candidate_scores_only(args, memory, ScoreConfig()), 0)

            history_probe.assert_called_once()
            self.assertEqual(history_probe.call_args.kwargs["report_path"], report)
            candidate_score.assert_not_called()


if __name__ == "__main__":
    unittest.main()
