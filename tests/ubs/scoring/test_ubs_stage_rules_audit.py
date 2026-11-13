import json
import sqlite3
import unittest

from ubs.memory_schema import SCHEMA_DDL
from ubs.stage_rules_audit import (
    MAX_REPORTED_IDS,
    audit_pending_states,
    audit_stage_rules,
    stages_to_rescore,
)


FINAL_STATUSES = ("accepted", "rejected")

CURRENT_FINAL_TICK = {
    "min_history_quality": 80.0,
    "max_net_delta_pct": 35.0,
    "max_pf_delta_pct": 30.0,
    "max_dd_delta_pct": 35.0,
    "max_trades_delta_pct": 35.0,
    "max_dd_gap_pp": 3.0,
    "from_date": "2026.05.01",
    "to_date": "2026.05.31",
}

CURRENT_SCORE = {
    "min_net_profit": 100.0,
    "min_profit_factor": 1.20,
    "min_trades": 60,
    "max_drawdown_pct": 25.0,
    "min_recovery_factor": 1.0,
}


def memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_DDL)
    return conn


def insert_candidate(conn, candidate_id: int, *, run_id: int, status: str, min_trades: object) -> None:
    metrics = {}
    if min_trades is not None:
        metrics["score_config"] = {**CURRENT_SCORE, "min_trades": min_trades}
    conn.execute(
        """
        insert into candidates (
            id, run_id, generation, seed_path, set_path, symbol, target_symbol, period,
            family, run_strategy, mutated_keys, timeframe_keys, mutation_details_json,
            missing_lot_keys, policy, metrics_json, status, created_at
        ) values (?, ?, 1, '', '', 'EURUSD', 'EURUSD', 'H1', 'fam', 'run', '', '', '', '', '', ?, ?, '')
        """,
        (candidate_id, run_id, json.dumps(metrics), status),
    )


def insert_final_tick(conn, candidate_id: int, *, run_id: int, status: str, **overrides) -> None:
    values = {
        "min_history_quality": 80.0,
        "max_net_delta_pct": 35.0,
        "max_pf_delta_pct": 35.0,
        "max_dd_delta_pct": 35.0,
        "max_trades_delta_pct": 35.0,
        "max_dd_gap_pp": 3.0,
        "from_date": "2026.05.01",
        "to_date": "2026.05.31",
    }
    values.update(overrides)
    conn.execute(
        """
        insert into candidate_final_tick (
            candidate_id, run_id, status, min_history_quality, from_date, to_date,
            max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
            max_dd_gap_pp, evaluated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
        """,
        (
            candidate_id, run_id, status, values["min_history_quality"],
            values["from_date"], values["to_date"], values["max_net_delta_pct"],
            values["max_pf_delta_pct"], values["max_dd_delta_pct"],
            values["max_trades_delta_pct"], values["max_dd_gap_pp"],
        ),
    )


class StageRulesAuditTests(unittest.TestCase):
    def test_moved_threshold_is_reported_with_its_rows(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")
        insert_final_tick(conn, 2, run_id=7, status="rejected")
        insert_final_tick(conn, 3, run_id=7, status="pending", max_pf_delta_pct=30.0)

        audits = {
            audit.stage: audit
            for audit in audit_stage_rules(
                conn,
                {"final_tick": CURRENT_FINAL_TICK},
                final_statuses=FINAL_STATUSES,
                stages=("final_tick",),
            )
        }

        findings = audits["final_tick"].findings
        self.assertEqual([finding.rule for finding in findings], ["max_pf_delta_pct"])
        self.assertEqual(findings[0].current, "30")
        self.assertEqual(findings[0].stored, (("35", 2),))
        self.assertEqual(findings[0].rows, 2)
        self.assertEqual(findings[0].verdicts, 2)
        self.assertEqual(findings[0].ids, (1, 2))
        self.assertEqual(audits["final_tick"].affected, 2)

    def test_affected_rows_are_counted_past_the_reported_ids(self) -> None:
        conn = memory()
        total = MAX_REPORTED_IDS + 5
        for candidate_id in range(1, total + 1):
            insert_final_tick(conn, candidate_id, run_id=7, status="accepted")

        audits = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(audits[0].affected, total)
        self.assertEqual(audits[0].findings[0].rows, total)
        self.assertEqual(len(audits[0].findings[0].ids), MAX_REPORTED_IDS)

    def test_unchanged_rules_leave_no_findings(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", max_pf_delta_pct=30.0)

        audits = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(audits[0].findings, ())
        self.assertEqual(audits[0].verdicts, 1)

    def test_dates_are_compared_as_text(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", from_date="2026.04.01")

        audits = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        findings = {finding.rule: finding for finding in audits[0].findings}
        self.assertIn("from_date", findings)
        self.assertEqual(findings["from_date"].stored, (("2026.04.01", 1),))
        self.assertEqual(findings["from_date"].current, "2026.05.01")

    def test_score_config_lives_inside_metrics_json(self) -> None:
        conn = memory()
        insert_candidate(conn, 1, run_id=7, status="accepted", min_trades=50)
        insert_candidate(conn, 2, run_id=7, status="rejected", min_trades=60)
        insert_candidate(conn, 3, run_id=7, status="accepted", min_trades=None)

        audits = audit_stage_rules(
            conn,
            {"candidates": CURRENT_SCORE},
            final_statuses=FINAL_STATUSES,
            stages=("candidates",),
        )

        findings = audits[0].findings
        self.assertEqual([finding.rule for finding in findings], ["min_trades"])
        self.assertEqual(findings[0].rows, 1)
        self.assertEqual(findings[0].ids, (1,))
        self.assertEqual(audits[0].unknown, 1)

    def test_run_scope_limits_the_audit(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")
        insert_final_tick(conn, 2, run_id=8, status="accepted")

        scoped = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            run_id=8,
            stages=("final_tick",),
        )

        self.assertEqual(scoped[0].findings[0].ids, (2,))

    def test_only_rules_with_a_current_value_are_audited(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", from_date="2026.04.01")

        audits = audit_stage_rules(
            conn,
            {"final_tick": {"max_pf_delta_pct": 35.0}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(audits[0].findings, ())

    def test_missing_table_is_flagged_instead_of_raising(self) -> None:
        conn = sqlite3.connect(":memory:")

        audits = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertTrue(audits[0].missing_table)
        self.assertEqual(audits[0].findings, ())


class PendingStatesAuditTests(unittest.TestCase):
    def test_counts_every_state_that_is_not_final(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")
        insert_final_tick(conn, 2, run_id=7, status="rejected")
        insert_final_tick(conn, 3, run_id=7, status="pending_history_quality")
        insert_final_tick(conn, 4, run_id=7, status="pending_tick_trades")
        insert_final_tick(conn, 5, run_id=7, status="pending_tick_trades")

        summary = audit_pending_states(
            conn, final_statuses=FINAL_STATUSES, stages=("final_tick",)
        )[0]

        self.assertEqual(summary.rows, 5)
        self.assertEqual(summary.pending, 3)
        self.assertEqual(
            summary.by_status,
            (("pending_tick_trades", 2), ("pending_history_quality", 1)),
        )
        self.assertEqual(summary.ids, (3, 4, 5))

    def test_stage_without_pending_rows_reports_zero(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")

        summary = audit_pending_states(
            conn, final_statuses=FINAL_STATUSES, stages=("final_tick",)
        )[0]

        self.assertEqual(summary.pending, 0)
        self.assertEqual(summary.by_status, ())


class StagesToRescoreTests(unittest.TestCase):
    def test_shared_final_tick_mode_is_not_launched_twice(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")
        conn.execute(
            """
            insert into candidate_final_tick_6m (
                candidate_id, run_id, status, min_history_quality, from_date, to_date,
                max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                max_trades_delta_pct, max_dd_gap_pp, evaluated_at
            ) values (1, 7, 'accepted', 80.0, '2026.01.01', '2026.06.30', 35.0, 35.0, 35.0, 35.0, 3.0, '')
            """
        )

        audits = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK, "final_tick_6m": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick", "final_tick_6m"),
        )

        self.assertEqual(stages_to_rescore(audits), ("final_tick",))

    def test_stage_without_rescore_mode_is_left_out(self) -> None:
        conn = memory()
        conn.execute(
            """
            insert into seed_final_tick (
                seed_id, status, min_history_quality, from_date, to_date,
                max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                max_trades_delta_pct, max_dd_gap_pp, seed_mtime, seed_size, evaluated_at
            ) values (1, 'accepted', 80.0, '2026.05.01', '2026.05.31', 35.0, 35.0, 35.0, 35.0, 3.0, 0, 0, '')
            """
        )

        audits = audit_stage_rules(
            conn,
            {"seed_final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("seed_final_tick",),
        )

        self.assertTrue(audits[0].findings)
        self.assertEqual(stages_to_rescore(audits), ())


if __name__ == "__main__":
    unittest.main()
