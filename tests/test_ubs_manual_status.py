import sqlite3
import unittest

from ubs.manual_status import (
    mark_candidate_final_tick,
    mark_candidate_robustness,
    mark_candidates,
    mark_seed_scores,
)
from ubs.weights import ASSET_ACCEPTED_BONUS, feedback_weight


def memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        create table candidates (
            id integer primary key,
            run_id integer not null,
            status text not null,
            score real,
            accepted integer,
            metrics_json text,
            target_symbol text,
            symbol text,
            period text,
            family text,
            seed_path text
        );
        create table seed_scores (
            seed_path text primary key,
            status text not null,
            score real,
            accepted integer,
            active integer not null default 1,
            evaluated_at text
        );
        create table candidate_robustness (
            candidate_id integer primary key,
            run_id integer not null,
            status text not null,
            report_path text,
            score real,
            accepted integer,
            metrics_json text,
            from_date text not null default '',
            to_date text not null default '',
            positive_bonus real not null default 70.0,
            negative_bonus real not null default -70.0,
            evaluated_at text not null
        );
        create table candidate_final_tick (
            candidate_id integer primary key,
            run_id integer not null,
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
        );
        """
    )
    return conn


class UBSManualStatusTests(unittest.TestCase):
    def test_mark_candidate_accepted_without_score_does_not_contribute_to_weights(self) -> None:
        conn = memory_conn()
        conn.execute(
            "insert into candidates (id, run_id, status, score, target_symbol, period) values (1, 7, 'no_trades', null, 'GS', 'H4')"
        )

        self.assertEqual(mark_candidates(conn, [1], "accepted"), 1)
        row = conn.execute("select * from candidates where id=1").fetchone()

        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["accepted"], 1)
        self.assertIsNone(feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS))

    def test_mark_seed_status_updates_state_even_without_score(self) -> None:
        conn = memory_conn()
        conn.execute("insert into seed_scores (seed_path, status, score) values ('seed.set', 'pending', null)")

        self.assertEqual(mark_seed_scores(conn, ["seed.set"], "rejected"), 1)
        row = conn.execute("select * from seed_scores where seed_path='seed.set'").fetchone()

        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["accepted"], 0)

    def test_manual_robust_accepted_makes_candidate_available_for_final_tick(self) -> None:
        conn = memory_conn()
        conn.execute(
            "insert into candidates (id, run_id, status, score, target_symbol, period) values (2, 7, 'accepted', 100, 'CAT', 'H1')"
        )

        self.assertEqual(mark_candidate_robustness(conn, [2], "accepted", from_date="2025.01.01"), 1)
        row = conn.execute("select * from candidate_robustness where candidate_id=2").fetchone()

        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["accepted"], 1)
        self.assertEqual(row["from_date"], "2025.01.01")

    def test_manual_final_tick_preserves_existing_metrics(self) -> None:
        conn = memory_conn()
        conn.execute(
            "insert into candidates (id, run_id, status, score, target_symbol, period) values (3, 7, 'accepted', 100, 'TSLA', 'H1')"
        )
        conn.execute(
            """
            insert into candidate_final_tick (
                candidate_id, run_id, status, accepted, ohlc_score, real_tick_score,
                similarity_json, min_history_quality, from_date, to_date, evaluated_at
            ) values (3, 7, 'pending_ohlc_trades', 0, 10, 20, '{"checks":{}}', 80, '2026.05.01', '2026.05.31', 'now')
            """
        )

        self.assertEqual(mark_candidate_final_tick(conn, [3], "accepted"), 1)
        row = conn.execute("select * from candidate_final_tick where candidate_id=3").fetchone()

        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["accepted"], 1)
        self.assertEqual(row["ohlc_score"], 10)
        self.assertEqual(row["real_tick_score"], 20)
        self.assertEqual(row["similarity_json"], '{"checks":{}}')


if __name__ == "__main__":
    unittest.main()
