from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backtest.symbols import infer_period_from_path
from ubs.memory import AgentMemory, variant_from_candidate_row
from ubs.agent.final_tick.economic_sample import (
    PENDING_ECONOMIC_SAMPLE,
    economic_sample_pending_check,
    economic_sample_status,
)
from ubs.agent.final_tick.pass_stage import _final_tick_set_names
from ubs.agent.final_tick.trade_alignment import final_tick_trade_alignment
from ubs.agent.seeds.final_tick import (
    SeedFinalTickMemoryAdapter,
    eligible_seed_final_tick_rows,
    mark_seed_economic_sample_pending,
    seed_economic_sample_review_rows,
    seed_final_tick_cohort_id,
)
from ui.app_chrome import SIDEBAR_ITEMS
from ui.ubs_seed_final_tick_view import UBSSeedFinalTickViewMixin
from ui.ubs_seeds_cleanup import UBSSeedsCleanupMixin


class SeedFinalTickFixture(unittest.TestCase):
    """Memoria con una seed aceptada y su adaptador de etapa."""

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


class UBSSeedFinalTickTests(SeedFinalTickFixture):
    MATCHED_TRADES = {"accepted": True, "matched": 11, "match_ratio": 11 / 12}

    @staticmethod
    def _economic_similarity(ohlc_trades: int = 11, tick_trades: int = 12) -> dict:
        return {
            "accepted": False,
            "reasons": ["economic_direction"],
            "checks": {
                "economic_direction": {
                    "required": True,
                    "matches": False,
                },
                "trades": {
                    "accepted": True,
                    "ohlc": ohlc_trades,
                    "real_tick": tick_trades,
                },
                "drawdown_pct": {"accepted": True},
            },
        }

    def test_seed_tabs_follow_seeds_in_sidebar(self) -> None:
        keys = [key for key, _label in SIDEBAR_ITEMS]
        seed_index = keys.index("ubs_seeds")
        self.assertEqual(
            keys[seed_index:seed_index + 3],
            ["ubs_seeds", "ubs_seed_final_tick", "ubs_seed_final_tick_6m"],
        )

    def test_both_seed_stages_include_historical_sample_review(self) -> None:
        self.assertIn(
            "_review_ubs_seed_final_tick_6m_economic_samples",
            UBSSeedFinalTickViewMixin._seed_final_tick_action_names("six_month"),
        )
        self.assertIn(
            "_review_ubs_seed_final_tick_economic_samples",
            UBSSeedFinalTickViewMixin._seed_final_tick_action_names("probe"),
        )

    def test_seed_adapter_enables_strict_economic_similarity(self) -> None:
        self.assertTrue(self.adapter.require_economic_direction_similarity)
        self.assertTrue(self.adapter.allow_better_tick_drawdown_rescue)

    def test_small_isolated_economic_divergence_waits_for_long_window(self) -> None:
        similarity = self._economic_similarity()
        args = SimpleNamespace(
            from_date="2026.01.01",
            to_date="2026.06.30",
            final_tick_ohlc_from_date="2024.07.01",
            final_tick_ohlc_to_date="2026.06.30",
            final_tick_economic_min_trades=50,
        )

        result = SimpleNamespace(report_path="report.htm")
        with patch(
            "ubs.agent.final_tick.economic_sample.final_tick_trade_alignment",
            return_value=self.MATCHED_TRADES,
        ):
            status = economic_sample_status(
                "rejected", result, result, similarity, args,
            )

        self.assertEqual(status, PENDING_ECONOMIC_SAMPLE)
        self.assertEqual(
            similarity["checks"]["economic_sample"]["min_trades"], 50,
        )

    def test_long_window_makes_economic_divergence_final(self) -> None:
        similarity = self._economic_similarity()
        args = SimpleNamespace(
            from_date="2024.07.01",
            to_date="2026.06.30",
            final_tick_ohlc_from_date="2024.07.01",
            final_tick_ohlc_to_date="2026.06.30",
            final_tick_economic_min_trades=50,
        )

        status = economic_sample_status(
            "rejected", None, None, similarity, args,
        )

        self.assertEqual(status, "rejected")

    def test_review_reclassifies_existing_small_sample_rejection(self) -> None:
        self._record("accepted")
        self._record("rejected", "six_month")
        similarity = json.dumps(self._economic_similarity())
        self.memory.conn.execute(
            "update seed_final_tick_6m set similarity_json=?, from_date=?, to_date=?, "
            "ohlc_report_path='ohlc.htm', real_tick_report_path='tick.htm' "
            "where seed_id=?",
            (similarity, "2026.01.01", "2026.06.30", self.seed_id),
        )
        self.memory.conn.commit()

        with patch(
            "ubs.agent.final_tick.economic_sample.final_tick_trade_alignment",
            return_value=self.MATCHED_TRADES,
        ):
            rows = seed_economic_sample_review_rows(
                self.memory, "six_month", 50, "2026.01.01", "2026.06.30",
                "2024.07.01", "2026.06.30",
            )
            updated = mark_seed_economic_sample_pending(
                self.memory, "six_month", rows, 50,
                "2024.07.01", "2026.06.30",
            )

        stored = self.memory.conn.execute(
            "select status, similarity_json from seed_final_tick_6m where seed_id=?",
            (self.seed_id,),
        ).fetchone()
        self.assertEqual(updated, 1)
        self.assertEqual(stored["status"], PENDING_ECONOMIC_SAMPLE)
        detail = json.loads(stored["similarity_json"])["checks"]["economic_sample"]
        self.assertEqual(detail["retry_from"], "2024.07.01")

    def test_other_failure_or_large_sample_remains_rejected(self) -> None:
        similarity = self._economic_similarity(50, 51)
        self.assertIsNone(
            economic_sample_pending_check(similarity, 50, self.MATCHED_TRADES)
        )
        similarity["reasons"].append("drawdown_pct")
        self.assertIsNone(
            economic_sample_pending_check(similarity, 100, self.MATCHED_TRADES)
        )

    def test_unaligned_trades_do_not_get_a_longer_economic_retry(self) -> None:
        similarity = self._economic_similarity()
        alignment = {"accepted": False, "matched": 2, "match_ratio": 2 / 12}
        self.assertIsNone(economic_sample_pending_check(similarity, 50, alignment))

    def test_candidate_diagnostic_direction_can_request_more_sample(self) -> None:
        similarity = self._economic_similarity()
        similarity["reasons"] = []
        similarity["checks"]["economic_direction"]["required"] = False

        detail = economic_sample_pending_check(similarity, 50, self.MATCHED_TRADES)

        self.assertIsNotNone(detail)

    def test_eleven_of_twelve_time_aligned_trades_are_similar(self) -> None:
        start = datetime(2026, 1, 1, 12, 0)

        def trade(index: int, seconds: int = 0):
            opened = start + timedelta(days=index, seconds=seconds)
            return SimpleNamespace(
                trade_type="Buy", open_time=opened,
                close_time=opened + timedelta(hours=1),
            )

        ohlc = SimpleNamespace(trades=[trade(index) for index in range(11)])
        tick = SimpleNamespace(
            trades=[trade(index, 30) for index in range(11)] + [trade(20)],
        )
        with patch(
            "ubs.agent.final_tick.trade_alignment.parse_report",
            side_effect=[ohlc, tick],
        ):
            alignment = final_tick_trade_alignment("ohlc.htm", "tick.htm")

        self.assertTrue(alignment["accepted"])
        self.assertEqual(alignment["matched"], 11)
        self.assertAlmostEqual(alignment["match_ratio"], 11 / 12, places=6)

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


LEGACY_SEED_FINAL_TICK_DDL = """
create table {table} (
    seed_path text primary key,
    symbol text not null default '',
    period text not null default '',
    status text not null,
    accepted integer,
    ohlc_report_path text,
    real_tick_report_path text,
    ohlc_score real,
    real_tick_score real,
    ohlc_metrics_json text,
    real_tick_metrics_json text,
    similarity_json text,
    history_quality real,
    min_history_quality real not null default 80.0,
    from_date text not null default '',
    to_date text not null default '',
    max_net_delta_pct real not null default 35.0,
    max_pf_delta_pct real not null default 35.0,
    max_dd_delta_pct real not null default 35.0,
    max_trades_delta_pct real not null default 35.0,
    evaluated_at text not null
)
"""


class UBSSeedFinalTickLegacyKeyTests(SeedFinalTickFixture):
    """La etapa vivio clavada en seed_path: abrir esa memoria no puede romperse."""

    def _downgrade(self, table: str, orphan: bool = False) -> None:
        self.memory.conn.execute(f"drop table {table}")
        self.memory.conn.executescript(LEGACY_SEED_FINAL_TICK_DDL.format(table=table))
        self.memory.conn.execute(
            f"insert into {table} (seed_path, symbol, period, status, accepted, "
            "ohlc_score, real_tick_score, evaluated_at) values (?, 'EURUSD', 'H1', "
            "'accepted', 1, 88.5, 87.0, 'antes')",
            (str(self.seed_path),),
        )
        if orphan:
            self.memory.conn.execute(
                f"insert into {table} (seed_path, symbol, period, status, accepted, "
                "evaluated_at) values ('X:/borrada.set', 'EURUSD', 'H1', 'rejected', 0, 'antes')"
            )
        self.memory.conn.commit()

    def _reopen(self) -> None:
        self.memory.close()
        self.memory = AgentMemory(Path(self.temp_dir.name) / "memory.sqlite")

    def test_legacy_rows_are_rekeyed_by_seed_id(self) -> None:
        self._downgrade("seed_final_tick")
        self._reopen()

        row = self.memory.conn.execute(
            "select seed_id, status, ohlc_score, real_tick_score, seed_mtime, seed_size "
            "from seed_final_tick"
        ).fetchone()
        stat = self.seed_path.stat()
        self.assertEqual(int(row["seed_id"]), self.seed_id)
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["ohlc_score"], 88.5)
        self.assertEqual(row["real_tick_score"], 87.0)
        self.assertEqual(int(row["seed_size"]), stat.st_size)
        self.assertEqual(
            self.memory.conn.execute(
                "select count(*) from sqlite_master where name='seed_final_tick_legacy_seed_path'"
            ).fetchone()[0],
            0,
        )

    def test_rows_without_seed_keep_their_table(self) -> None:
        self._downgrade("seed_final_tick_6m", orphan=True)
        self._reopen()

        self.assertEqual(
            self.memory.conn.execute("select count(*) from seed_final_tick_6m").fetchone()[0], 1
        )
        kept = self.memory.conn.execute(
            "select seed_path from seed_final_tick_6m_legacy_seed_path "
            "where seed_path='X:/borrada.set'"
        ).fetchone()
        self.assertIsNotNone(kept)

    def test_second_open_does_not_migrate_again(self) -> None:
        self._downgrade("seed_final_tick")
        self._reopen()
        self.memory.conn.execute(
            "update seed_final_tick set status='pending_tick_trades' where seed_id=?",
            (self.seed_id,),
        )
        self.memory.conn.commit()
        self._reopen()

        status = self.memory.conn.execute(
            "select status from seed_final_tick where seed_id=?", (self.seed_id,)
        ).fetchone()[0]
        self.assertEqual(status, "pending_tick_trades")


if __name__ == "__main__":
    unittest.main()
