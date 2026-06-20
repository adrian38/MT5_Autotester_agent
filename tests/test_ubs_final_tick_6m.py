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


if __name__ == "__main__":
    unittest.main()
