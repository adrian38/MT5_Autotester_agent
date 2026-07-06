import tempfile
import unittest
from pathlib import Path

from ai_copilot.features import build_local_report
from ai_copilot.snapshot import load_run_snapshot_from_path
from ubs.memory import AgentMemory


class AICopilotSnapshotTests(unittest.TestCase):
    def test_snapshot_counts_reasons_and_missing_rows(self) -> None:
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
                rows = [
                    (1, "accepted", 100.0, '{"reasons": []}', "MaxSpread,Exit_stop", "XAUUSD", "H1"),
                    (2, "rejected", 40.0, '{"reasons": ["profit_factor"]}', "MaxSpread", "XAUUSD", "H1"),
                    (3, "no_trades", None, '{"reasons": ["trades"]}', "Exit_stop", "EURUSD", "M15"),
                    (4, "report_mismatch", None, "{}", "", "EURUSD", "M15"),
                ]
                for row in rows:
                    memory.conn.execute(
                        """
                        insert into candidates (
                            id, run_id, generation, seed_path, set_path, symbol, target_symbol,
                            period, family, run_strategy, mutated_keys, missing_lot_keys,
                            policy, score, accepted, metrics_json, status, created_at
                        ) values (?, 1, 1, 'seed.set', ?, ?, ?, ?, 'fam', 'strat', ?, '',
                            'test', ?, ?, ?, ?, 'now')
                        """,
                        (
                            row[0],
                            f"set{row[0]}.set",
                            row[5],
                            row[5],
                            row[6],
                            row[4],
                            row[2],
                            1 if row[1] == "accepted" else 0 if row[1] == "rejected" else None,
                            row[3],
                            row[1],
                        ),
                    )
                memory.conn.commit()
            finally:
                memory.close()

            snapshot = load_run_snapshot_from_path(Path(temp_dir) / "memory.sqlite", "ROBOFOREX", "ECN", 1)

        self.assertEqual(snapshot["counts"]["base_status"]["accepted"], 1)
        self.assertEqual(snapshot["counts"]["base_status"]["report_mismatch"], 1)
        self.assertEqual(snapshot["counts"]["missing"]["robustness"], 1)
        self.assertEqual(snapshot["reasons"]["base"]["profit_factor"], 1)
        key_counts = {row["key"]: row["count"] for row in snapshot["top_mutated_keys"]}
        self.assertEqual(key_counts["MaxSpread"], 2)
        self.assertEqual(key_counts["Exit_stop"], 2)

    def test_local_report_references_known_evidence(self) -> None:
        snapshot = {
            "broker": "ROBOFOREX",
            "account_type": "ECN",
            "run_id": 1,
            "counts": {
                "base_status": {"accepted": 1, "rejected": 2, "no_report": 1},
                "robustness_status": {},
                "final_tick_status": {},
                "final_tick_6m_status": {},
                "missing": {"robustness": 1, "final_tick": 0, "final_tick_6m": 0},
                "stale": {"robustness": 0, "final_tick": 0, "final_tick_6m": 0},
            },
            "reasons": {"base": {"profit_factor": 2}},
            "concentration": {"target_symbol": [{"key": "XAUUSD", "count": 4}], "period": []},
            "top_mutated_keys": [],
        }

        report = build_local_report(snapshot)

        self.assertTrue(report["recommendations"])
        self.assertTrue(all(rec["requires_approval"] for rec in report["recommendations"]))


if __name__ == "__main__":
    unittest.main()
