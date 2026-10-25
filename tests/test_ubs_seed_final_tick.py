"""Etapas tick a tick de las seeds: cascada, veredicto manual y puerta de pesos."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ubs.manual_status import mark_seed_final_tick
from ubs.memory import AgentMemory
from ui.ubs_seeds_logic import UBSSeedsLogicMixin


class _WeightGateProbe(UBSSeedsLogicMixin):
    """Solo para llamar al contador de la puerta sin montar la UI."""

    def _sqlite_table_exists(self, conn, table):
        return bool(
            conn.execute(
                "select name from sqlite_master where type='table' and name=?", (table,)
            ).fetchone()
        )


class SeedFinalTickStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = AgentMemory(Path(self._tmp.name) / "memory.sqlite")
        self.addCleanup(self.memory.close)
        self.memory.conn.row_factory = sqlite3.Row

    def _add_seed(self, seed_path: str, status: str = "accepted", score: float = 10.0) -> None:
        self.memory.conn.execute(
            """
            insert into seed_scores (
                seed_path, symbol, period, status, score, active,
                seed_mtime, seed_size, family, run_strategy, evaluated_at, last_seen
            ) values (?, 'EURUSD', 'H1', ?, ?, 1, 0, 0, '', '', '2026-01-01', '2026-01-01')
            """,
            (seed_path, status, score),
        )
        self.memory.conn.commit()

    def test_six_month_needs_an_eligible_probe(self) -> None:
        self._add_seed("a.set")
        self.assertEqual(
            self.memory.accepted_seeds_for_final_tick(final_tick_stage="six_month"), []
        )
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted")
        self.assertEqual(
            len(self.memory.accepted_seeds_for_final_tick(final_tick_stage="six_month")), 1
        )

    def test_pending_ohlc_trades_probe_still_reaches_six_month(self) -> None:
        self._add_seed("a.set")
        self.memory.record_seed_final_tick(
            "a.set", "EURUSD", "H1", "pending_ohlc_trades",
            None, None, None, None, None, None,
            80.0, "2026.05.01", "2026.05.31", 35.0, 35.0, 35.0, 35.0,
            final_tick_stage="probe",
        )
        self.assertEqual(
            len(self.memory.accepted_seeds_for_final_tick(final_tick_stage="six_month")), 1
        )

    def test_rejected_probe_drops_its_six_month_row(self) -> None:
        self._add_seed("a.set")
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted")
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted", final_tick_stage="6m")
        self.assertEqual(
            self.memory.conn.execute("select count(*) from seed_final_tick_6m").fetchone()[0], 1
        )
        mark_seed_final_tick(self.memory.conn, ["a.set"], "rejected")
        self.assertEqual(
            self.memory.conn.execute("select count(*) from seed_final_tick_6m").fetchone()[0], 0
        )

    def test_manual_verdict_keeps_stored_reports(self) -> None:
        self._add_seed("a.set")
        self.memory.record_seed_final_tick(
            "a.set", "EURUSD", "H1", "rejected",
            None, None, Path("ohlc.html"), Path("tick.html"), None, 91.5,
            80.0, "2026.05.01", "2026.05.31", 35.0, 35.0, 35.0, 35.0,
            final_tick_stage="probe",
        )
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted")
        row = self.memory.conn.execute("select * from seed_final_tick").fetchone()
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["accepted"], 1)
        self.assertEqual(row["ohlc_report_path"], "ohlc.html")
        self.assertEqual(row["real_tick_report_path"], "tick.html")
        self.assertEqual(row["history_quality"], 91.5)
        # El rango guardado manda sobre el que se pasa al marcar a mano.
        self.assertEqual(row["from_date"], "2026.05.01")

    def test_feedback_rows_expose_both_stage_verdicts(self) -> None:
        self._add_seed("a.set")
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted")
        mark_seed_final_tick(self.memory.conn, ["a.set"], "rejected", final_tick_stage="6m")
        row = self.memory._seed_feedback_rows()[0]
        self.assertEqual(row["final_tick_status"], "accepted")
        self.assertEqual(row["final_tick_6m_status"], "rejected")

    def test_feedback_rows_hide_stages_of_a_rejected_seed(self) -> None:
        self._add_seed("a.set", status="rejected")
        self.memory.record_seed_final_tick(
            "a.set", "EURUSD", "H1", "accepted",
            None, None, None, None, None, None,
            80.0, "", "", 35.0, 35.0, 35.0, 35.0,
            final_tick_stage="probe",
        )
        row = self.memory._seed_feedback_rows()[0]
        self.assertIsNone(row["final_tick_status"])


class SeedFinalTickWeightGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.memory = AgentMemory(Path(self._tmp.name) / "memory.sqlite")
        self.addCleanup(self.memory.close)
        self.memory.conn.row_factory = sqlite3.Row
        self.probe = _WeightGateProbe()
        for name in ("a.set", "b.set"):
            self.memory.conn.execute(
                """
                insert into seed_scores (
                    seed_path, symbol, period, status, score, active,
                    seed_mtime, seed_size, family, run_strategy, evaluated_at, last_seen
                ) values (?, 'EURUSD', 'H1', 'accepted', 10, 1, 0, 0, '', '', '2026-01-01', '2026-01-01')
                """,
                (name,),
            )
        self.memory.conn.commit()

    def counts(self):
        return self.probe._pending_seed_final_tick_counts(self.memory.conn)

    def test_every_accepted_seed_starts_pending_the_short_stage(self) -> None:
        self.assertEqual(self.counts(), (2, 0))

    def test_closing_the_short_stage_opens_the_six_month_one(self) -> None:
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted")
        mark_seed_final_tick(self.memory.conn, ["b.set"], "rejected")
        # b.set queda cerrado en corto y fuera de 6M; a.set pasa a deber el 6M.
        self.assertEqual(self.counts(), (0, 1))

    def test_both_stages_closed_releases_the_gate(self) -> None:
        mark_seed_final_tick(self.memory.conn, ["a.set", "b.set"], "accepted")
        mark_seed_final_tick(self.memory.conn, ["a.set"], "accepted", final_tick_stage="6m")
        mark_seed_final_tick(self.memory.conn, ["b.set"], "rejected", final_tick_stage="6m")
        self.assertEqual(self.counts(), (0, 0))

    def test_a_retryable_stage_state_keeps_the_gate_closed(self) -> None:
        mark_seed_final_tick(self.memory.conn, ["a.set", "b.set"], "accepted")
        self.memory.record_seed_final_tick(
            "a.set", "EURUSD", "H1", "pending_history_quality",
            None, None, None, None, None, None,
            80.0, "", "", 35.0, 35.0, 35.0, 35.0,
            final_tick_stage="six_month",
        )
        mark_seed_final_tick(self.memory.conn, ["b.set"], "accepted", final_tick_stage="6m")
        self.assertEqual(self.counts(), (0, 1))


if __name__ == "__main__":
    unittest.main()
