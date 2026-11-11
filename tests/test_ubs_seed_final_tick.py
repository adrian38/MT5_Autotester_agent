from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_tests_symbols import infer_period_from_path
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs_agent_final_tick_pass import _final_tick_set_names
from ubs_agent_seed_final_tick import (
    SeedFinalTickMemoryAdapter,
    eligible_seed_final_tick_rows,
    seed_final_tick_cohort_id,
)
from ui.app_chrome import SIDEBAR_ITEMS
from ui.ubs_seeds_cleanup import UBSSeedsCleanupMixin


class UBSSeedFinalTickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.seed_path = root / "seed.set"
        self.seed_path.write_text("ForceSymbol=EURUSD\nTimeFrame=16385\n", encoding="utf-8")
        self.memory = AgentMemory(root / "memory.sqlite")
        stat = self.seed_path.stat()
        self.memory.conn.execute(
            """
            insert into seed_scores (
                seed_path, seed_mtime, seed_size, symbol, period, family,
                run_strategy, status, accepted, active, last_seen
            ) values (?, ?, ?, 'EURUSD', 'H1', 'manual', '1', 'accepted', 1, 1, 'now')
            """,
            (str(self.seed_path), stat.st_mtime, stat.st_size),
        )
        self.memory.conn.commit()
        self.seed_id = int(self.memory.conn.execute("select id from seed_scores").fetchone()[0])
        self.adapter = SeedFinalTickMemoryAdapter(self.memory, root / "output")

    def tearDown(self) -> None:
        self.memory.close()
        self.temp_dir.cleanup()

    def _record(self, status: str, stage: str = "probe") -> None:
        self.adapter.accepted_candidates_for_final_tick(0, final_tick_stage=stage)
        self.adapter.record_candidate_final_tick(
            self.seed_id, 0, status, None, None, None, None, None, None,
            80.0, "2026.05.01", "2026.05.31", 35.0, 35.0, 35.0, 35.0,
            final_tick_stage=stage,
        )

    def test_seed_tabs_follow_seeds_in_sidebar(self) -> None:
        keys = [key for key, _label in SIDEBAR_ITEMS]
        seed_index = keys.index("ubs_seeds")
        self.assertEqual(
            keys[seed_index:seed_index + 3],
            ["ubs_seeds", "ubs_seed_final_tick", "ubs_seed_final_tick_6m"],
        )

    def test_seed_adapter_enables_strict_economic_similarity(self) -> None:
        self.assertTrue(self.adapter.require_economic_direction_similarity)
        self.assertTrue(self.adapter.allow_better_tick_drawdown_rescue)

    def test_probe_result_gates_six_month_stage(self) -> None:
        self.assertEqual(len(eligible_seed_final_tick_rows(self.memory, "probe")), 1)
        self.assertEqual(eligible_seed_final_tick_rows(self.memory, "six_month"), [])
        self._record("accepted")
        rows = eligible_seed_final_tick_rows(self.memory, "six_month")
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["id"]), self.seed_id)

    def test_seed_rows_adapt_to_variants_without_mutation_metadata(self) -> None:
        for stage in ("probe", "six_month"):
            if stage == "six_month":
                self._record("accepted")
            row = eligible_seed_final_tick_rows(self.memory, stage)[0]
            variant = variant_from_candidate_row(row)

            self.assertEqual(variant.mutated_keys, ())
            self.assertEqual(variant.missing_lot_keys, ())
            self.assertEqual(variant.timeframe_keys, ())
            self.assertEqual(variant.policy, "seed_final_tick")

    def test_final_tick_copy_name_prioritizes_the_recorded_seed_period(self) -> None:
        names = _final_tick_set_names(
            ("ohlc", "tick"), 582,
            Path("MisterMember_Xmas__UBS-TSLA.NAS-H1-TSV01.set"), "M15",
        )
        self.assertEqual(
            names,
            (
                "ohlc_000582_M15_MisterMember_Xmas__UBS-TSLA.NAS-H1-TSV01.set",
                "tick_000582_M15_MisterMember_Xmas__UBS-TSLA.NAS-H1-TSV01.set",
            ),
        )
        inferred = [infer_period_from_path(Path(name)) for name in names]
        self.assertEqual(inferred, ["M15", "M15"])

    def test_probe_failure_removes_existing_six_month_result(self) -> None:
        self._record("accepted")
        self._record("accepted", "six_month")
        self.assertIsNotNone(
            self.memory.conn.execute(
                "select 1 from seed_final_tick_6m where seed_id=?", (self.seed_id,)
            ).fetchone()
        )
        self._record("rejected")
        self.assertIsNone(
            self.memory.conn.execute(
                "select 1 from seed_final_tick_6m where seed_id=?", (self.seed_id,)
            ).fetchone()
        )

    def test_seed_change_hides_stale_result(self) -> None:
        self._record("accepted")
        self.memory.conn.execute(
            "update seed_scores set seed_size=seed_size+1 where id=?", (self.seed_id,)
        )
        self.memory.conn.commit()
        row = eligible_seed_final_tick_rows(self.memory, "probe")[0]
        self.assertIsNone(row["final_tick_status"])

    def test_cohort_id_changes_with_seed_snapshot(self) -> None:
        rows = eligible_seed_final_tick_rows(self.memory, "probe")
        original = seed_final_tick_cohort_id(rows, "probe")
        self.memory.conn.execute(
            "update seed_scores set seed_size=seed_size+1 where id=?", (self.seed_id,)
        )
        self.memory.conn.commit()
        changed = seed_final_tick_cohort_id(
            eligible_seed_final_tick_rows(self.memory, "probe"), "probe"
        )
        self.assertNotEqual(original, changed)

    def test_seed_cleanup_removes_both_stage_rows(self) -> None:
        self._record("accepted")
        self._record("accepted", "six_month")
        UBSSeedsCleanupMixin()._cleanup_seed_db(
            self.memory.conn, [str(self.seed_path)]
        )
        for table in ("seed_final_tick", "seed_final_tick_6m"):
            count = self.memory.conn.execute(f"select count(*) from {table}").fetchone()[0]
            self.assertEqual(count, 0)

    def test_memory_reclassifies_low_tick_sample_for_candidates_and_seeds(self) -> None:
        similarity = json.dumps({"accepted": False, "reasons": ["tick_trades"]})
        for stage in ("probe", "six_month"):
            self._record("rejected", stage)
        for table in ("seed_final_tick", "seed_final_tick_6m"):
            self.memory.conn.execute(
                f"update {table} set similarity_json=? where seed_id=?",
                (similarity, self.seed_id),
            )
        for table in ("candidate_final_tick", "candidate_final_tick_6m"):
            self.memory.conn.execute(
                f"insert into {table} "
                "(candidate_id, run_id, status, accepted, similarity_json, evaluated_at) "
                "values (1, 1, 'rejected', 0, ?, 'now')",
                (similarity,),
            )
        mixed = json.dumps(
            {"accepted": False, "reasons": ["tick_trades", "tick_net_profit"]}
        )
        self.memory.conn.execute(
            "insert into candidate_final_tick_6m "
            "(candidate_id, run_id, status, accepted, similarity_json, evaluated_at) "
            "values (2, 1, 'rejected', 0, ?, 'now')",
            (mixed,),
        )
        self.memory.conn.commit()
        self.memory.close()
        self.memory = AgentMemory(Path(self.temp_dir.name) / "memory.sqlite")

        rows = (
            ("seed_final_tick", "seed_id", self.seed_id),
            ("seed_final_tick_6m", "seed_id", self.seed_id),
            ("candidate_final_tick", "candidate_id", 1),
            ("candidate_final_tick_6m", "candidate_id", 1),
        )
        for table, key, row_id in rows:
            status = self.memory.conn.execute(
                f"select status from {table} where {key}=?", (row_id,)
            ).fetchone()[0]
            self.assertEqual(status, "pending_tick_trades")
        mixed_status = self.memory.conn.execute(
            "select status from candidate_final_tick_6m where candidate_id=2"
        ).fetchone()[0]
        self.assertEqual(mixed_status, "rejected")


if __name__ == "__main__":
    unittest.main()
