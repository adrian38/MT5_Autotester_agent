import argparse
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ubs.memory import AgentMemory
from ubs.score import ScoreConfig, ScoreResult
from ubs_agent import rescore_candidate_scores_only, rescore_robustness_only


class UBSAgentRescoreTests(unittest.TestCase):
    @staticmethod
    def _stored_result() -> ScoreResult:
        return ScoreResult(
            report_path="missing.htm", name="candidate", symbol="EURUSD", timeframe="H1",
            score=50.0, accepted=True, net_profit=150.0, raw_net_profit=150.0,
            normalized_net_profit=150.0, net_profit_factor=1.0, net_profit_basis="stored",
            normalization_group="stored", history_quality=99.0, profit_factor=1.5,
            recovery_factor=2.0, drawdown=10.0, drawdown_pct=10.0, trades=60,
            positive_month_ratio=0.6, max_month_concentration=0.2, avg_trade=2.5,
            sqn=1.2, reasons=(),
        )

    def test_rescore_candidates_uses_sqlite_metrics_without_local_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = AgentMemory(Path(temp_dir) / "memory.sqlite")
            try:
                memory.conn.execute(
                    """
                    insert into candidates (
                        id, run_id, generation, seed_path, set_path, symbol, target_symbol,
                        period, family, run_strategy, mutated_keys, missing_lot_keys, policy,
                        report_path, score, accepted, metrics_json, status, created_at
                    ) values (
                        1, 1, 1, 'seed.set', 'candidate.set', 'EURUSD', 'EURUSD',
                        'H1', 'fam', 'strat', '', '', 'test', 'missing.htm',
                        50, 1, ?, 'accepted', 'now'
                    )
                    """,
                    (self._stored_result().to_json(),),
                )
                memory.conn.commit()
                args = argparse.Namespace(
                    min_trades_w1=12,
                    min_trades_mn=4,
                    rescore_from_reports=False,
                )

                self.assertEqual(
                    rescore_candidate_scores_only(
                        args,
                        memory,
                        ScoreConfig(min_net_profit=200.0),
                    ),
                    0,
                )

                row = memory.conn.execute(
                    "select status, accepted, metrics_json from candidates where id=1"
                ).fetchone()
                self.assertEqual(row["status"], "rejected")
                self.assertEqual(row["accepted"], 0)
                self.assertIn("net_profit", ScoreResult.from_json(row["metrics_json"]).reasons)
            finally:
                memory.close()

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
                rescore_from_reports=True,
            )

            with (
                patch("ubs_agent.evaluate_history_probe", return_value=("history_ok", None)) as history_probe,
                patch("ubs_agent.evaluate_variant_report", return_value=("accepted", None)) as candidate_score,
            ):
                self.assertEqual(rescore_candidate_scores_only(args, memory, ScoreConfig()), 0)

            history_probe.assert_called_once()
            self.assertEqual(history_probe.call_args.kwargs["report_path"], report)
            candidate_score.assert_not_called()

    def test_rescore_robustness_applies_degradation_gate_and_persists_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = AgentMemory(Path(temp_dir) / "memory.sqlite")
            try:
                base = replace(
                    self._stored_result(),
                    normalized_net_profit=500.0,
                    profit_factor=2.0,
                    recovery_factor=2.0,
                    drawdown_pct=10.0,
                )
                oos = replace(
                    self._stored_result(),
                    normalized_net_profit=100.0,
                    profit_factor=1.2,
                    recovery_factor=1.2,
                    drawdown_pct=15.0,
                )
                config_json = json.dumps(
                    {"execution": {"from_date": "2020.01.01", "to_date": "2024.12.31"}}
                )
                memory.conn.execute(
                    """
                    insert into runs (
                        id, created_at, source_dir, output_dir, generations,
                        variants_per_seed, max_seeds, execute_backtests, dry_run, config_json
                    ) values (1, 'now', 'src', 'out', 1, 1, 1, 1, 0, ?)
                    """,
                    (config_json,),
                )
                memory.conn.execute(
                    """
                    insert into candidates (
                        id, run_id, generation, seed_path, set_path, symbol, target_symbol,
                        period, family, run_strategy, mutated_keys, missing_lot_keys, policy,
                        score, accepted, metrics_json, status, created_at
                    ) values (
                        1, 1, 1, 'seed.set', 'candidate.set', 'EURUSD', 'EURUSD',
                        'H1', 'fam', 'strat', '', '', 'test', 50, 1, ?, 'accepted', 'now'
                    )
                    """,
                    (base.to_json(),),
                )
                memory.record_candidate_robustness(
                    1, 1, oos, "accepted", None,
                    "2025.01.01", "2026.06.01", 70.0, -70.0,
                )
                args = argparse.Namespace(
                    min_trades_w1=12,
                    min_trades_mn=4,
                    rescore_from_reports=False,
                    robust_min_net_retention=0.5,
                    robust_min_pf_edge_retention=0.5,
                    robust_min_recovery_retention=0.5,
                    robust_max_dd_inflation=2.0,
                )

                self.assertEqual(
                    rescore_robustness_only(
                        args,
                        memory,
                        ScoreConfig(
                            min_net_profit=0.0,
                            min_profit_factor=1.0,
                            min_trades=1,
                            max_drawdown_pct=25.0,
                            min_recovery_factor=0.1,
                        ),
                    ),
                    0,
                )

                row = memory.conn.execute(
                    "select status, accepted, metrics_json, degradation_json from candidate_robustness where candidate_id=1"
                ).fetchone()
                self.assertEqual(row["status"], "rejected")
                self.assertEqual(row["accepted"], 0)
                self.assertIn("degradation_profit_factor", ScoreResult.from_json(row["metrics_json"]).reasons)
                audit = json.loads(row["degradation_json"])
                self.assertFalse(audit["accepted"])
                self.assertEqual(audit["checks"]["pf_edge_retention"]["value"], 0.2)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
