import json
import tempfile
import unittest
from pathlib import Path

from ubs.memory import AgentMemory


class UBSFinalTick6MEligibilityTests(unittest.TestCase):
    def test_six_month_accepts_short_accepted_and_pending_ohlc_trades(self) -> None:
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
                for candidate_id, status in (
                    (1, "accepted"),
                    (2, "pending_ohlc_trades"),
                    (3, "rejected"),
                ):
                    memory.conn.execute(
                        """
                        insert into candidates (
                            id, run_id, generation, seed_path, set_path, symbol,
                            target_symbol, period, family, run_strategy, mutated_keys,
                            missing_lot_keys, policy, score, accepted, status, created_at
                        ) values (?, 1, 1, ?, ?, 'USTEC', 'USTEC', 'H1', 'fam', 'strat', '', '', 'test', 100, 1, 'accepted', 'now')
                        """,
                        (candidate_id, f"seed{candidate_id}.set", f"set{candidate_id}.set"),
                    )
                    memory.conn.execute(
                        """
                        insert into candidate_robustness (
                            candidate_id, run_id, status, accepted, from_date, to_date, evaluated_at
                        ) values (?, 1, 'accepted', 1, '2025.01.01', '2025.12.31', 'now')
                        """,
                        (candidate_id,),
                    )
                    memory.conn.execute(
                        """
                        insert into candidate_final_tick (
                            candidate_id, run_id, status, accepted, from_date, to_date, evaluated_at
                        ) values (?, 1, ?, ?, '2026.05.01', '2026.05.31', 'now')
                        """,
                        (candidate_id, status, 1 if status == "accepted" else 0),
                    )
                memory.conn.commit()

                rows = memory.accepted_candidates_for_final_tick(1, final_tick_stage="six_month")

                self.assertEqual([int(row["id"]) for row in rows], [1, 2])
            finally:
                memory.close()

    def test_legacy_tick_sync_rejections_are_migrated_without_touching_real_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite"
            memory = AgentMemory(db_path)
            legacy_context = {
                "accepted": False,
                "reasons": ["real_tick_no_history"],
                "history": {
                    "message": "no history data, stop testing",
                    "tick_download_failed": True,
                },
            }
            ordinary_context = {
                "accepted": False,
                "reasons": ["pf_delta"],
                "history": {"tick_download_failed": False},
            }
            try:
                for table, candidate_id in (
                    ("candidate_final_tick", 11),
                    ("candidate_final_tick_6m", 12),
                ):
                    memory.conn.execute(
                        f"""
                        insert into {table} (
                            candidate_id, run_id, status, accepted,
                            real_tick_score, real_tick_metrics_json,
                            similarity_json, history_quality,
                            min_history_quality, evaluated_at
                        ) values (?, 1, 'rejected', 0, -55.0, '{{"trades": 0}}', ?, 0.0, 80.0, 'now')
                        """,
                        (candidate_id, json.dumps(legacy_context)),
                    )
                memory.conn.execute(
                    """
                    insert into candidate_final_tick_6m (
                        candidate_id, run_id, status, accepted,
                        real_tick_score, real_tick_metrics_json,
                        similarity_json, history_quality,
                        min_history_quality, evaluated_at
                    ) values (13, 1, 'rejected', 0, 17.0, '{"trades": 10}', ?, 99.0, 80.0, 'now')
                    """,
                    (json.dumps(ordinary_context),),
                )
                memory.conn.commit()
            finally:
                memory.close()

            migrated_memory = AgentMemory(db_path)
            try:
                for table, candidate_id in (
                    ("candidate_final_tick", 11),
                    ("candidate_final_tick_6m", 12),
                ):
                    row = migrated_memory.conn.execute(
                        f"select * from {table} where candidate_id=?",
                        (candidate_id,),
                    ).fetchone()
                    self.assertEqual(row["status"], "pending_history_quality")
                    self.assertEqual(row["accepted"], 0)
                    self.assertIsNone(row["real_tick_score"])
                    self.assertIsNone(row["real_tick_metrics_json"])
                    context = json.loads(row["similarity_json"])
                    self.assertTrue(context["technical_failure"])
                    self.assertTrue(context["history"]["retryable"])
                    self.assertEqual(context["history"]["failure_type"], "tick_history_sync")
                    self.assertEqual(
                        context["status_audit"]["classification"],
                        "transient_tick_sync_failure",
                    )
                    self.assertEqual(
                        context["status_audit"]["migrated_from_status"],
                        "rejected",
                    )

                ordinary = migrated_memory.conn.execute(
                    "select * from candidate_final_tick_6m where candidate_id=13"
                ).fetchone()
                self.assertEqual(ordinary["status"], "rejected")
                self.assertEqual(ordinary["real_tick_score"], 17.0)
                self.assertIsNotNone(ordinary["real_tick_metrics_json"])
            finally:
                migrated_memory.close()


if __name__ == "__main__":
    unittest.main()
