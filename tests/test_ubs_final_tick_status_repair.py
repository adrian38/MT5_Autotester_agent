"""Escaneo y reparacion de estados Final Tick incoherentes con su similarity_json.

La memoria tiene filas marcadas 'rejected' cuyo blob de similitud no reporta
ningun criterio fallado. El boton de la pestana Universo las corrige, pero lo
que NO puede hacer es tocar estados que decide una etapa anterior a la
comparacion: esas son las pruebas que importan aqui.
"""

import json
import sqlite3
import unittest

from ubs.db import configure_sqlite_connection
from ubs_agent import final_tick_status_from_similarity
from ui.ubs_universe_logic import UBSUniverseLogicMixin


class Scanner(UBSUniverseLogicMixin):
    """Solo hace falta el mixin: scan_final_tick_status_mismatches no usa mas."""


def sim(accepted: bool, reasons=(), **extra) -> str:
    payload = {"accepted": accepted, "reasons": list(reasons), "checks": {}}
    payload.update(extra)
    return json.dumps(payload)


def make_memory() -> sqlite3.Connection:
    conn = configure_sqlite_connection(sqlite3.connect(":memory:"))
    conn.row_factory = sqlite3.Row
    for table in ("candidate_final_tick", "candidate_final_tick_6m"):
        conn.execute(
            f"""create table {table} (
                candidate_id integer primary key, run_id integer, status text,
                accepted integer, similarity_json text
            )"""
        )
    return conn


def insert(conn, table, candidate_id, status, accepted, similarity):
    conn.execute(
        f"insert into {table} (candidate_id, run_id, status, accepted, similarity_json) values (?,?,?,?,?)",
        (candidate_id, 1, status, accepted, similarity),
    )


class StatusFromSimilarityTests(unittest.TestCase):
    def test_no_reasons_is_accepted(self) -> None:
        self.assertEqual(final_tick_status_from_similarity(json.loads(sim(True))), "accepted")

    def test_failed_criteria_is_rejected(self) -> None:
        payload = json.loads(sim(False, ["profit_factor"]))
        self.assertEqual(final_tick_status_from_similarity(payload), "rejected")

    def test_history_quality_outranks_the_rejection(self) -> None:
        payload = json.loads(sim(False, ["history_quality", "profit_factor"]))
        self.assertEqual(final_tick_status_from_similarity(payload), "pending_history_quality")

    def test_technical_history_failures_are_pending_not_rejected(self) -> None:
        """Las escriben rutas que no pasan por final_tick_similarity.

        La memoria tiene 2 filas 'empty_tester_context'; leerlas como rechazo
        convertiria un reintento pendiente en un descarte definitivo.
        """
        for reason in ("real_tick_no_history", "empty_tester_context"):
            with self.subTest(reason=reason):
                payload = json.loads(sim(False, [reason]))
                self.assertEqual(
                    final_tick_status_from_similarity(payload), "pending_history_quality"
                )

    def test_a_pending_payload_determines_no_verdict(self) -> None:
        payload = {"accepted": False, "pending": True, "reasons": ["ohlc_trades"], "checks": {}}
        self.assertIsNone(final_tick_status_from_similarity(payload))

    def test_junk_determines_no_verdict(self) -> None:
        for payload in (None, "", [], {}, {"reasons": []}):
            with self.subTest(payload=payload):
                self.assertIsNone(final_tick_status_from_similarity(payload))


class ScanFinalTickStatusMismatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = make_memory()
        self.scanner = Scanner()

    def tearDown(self) -> None:
        self.conn.close()

    def scan(self) -> list[dict]:
        return self.scanner.scan_final_tick_status_mismatches(self.conn)

    def test_finds_the_real_bug_rejected_without_any_failed_criterion(self) -> None:
        insert(self.conn, "candidate_final_tick_6m", 9640, "rejected", 0, sim(True))
        found = self.scan()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["candidate_id"], 9640)
        self.assertEqual(found[0]["stored_status"], "rejected")
        self.assertEqual(found[0]["expected_status"], "accepted")
        self.assertEqual(found[0]["expected_accepted"], 1)
        self.assertEqual(found[0]["table"], "candidate_final_tick_6m")

    def test_leaves_consistent_rows_alone(self) -> None:
        insert(self.conn, "candidate_final_tick", 1, "accepted", 1, sim(True))
        insert(self.conn, "candidate_final_tick", 2, "rejected", 0, sim(False, ["profit_factor"]))
        insert(self.conn, "candidate_final_tick_6m", 3, "pending_history_quality", 0,
               sim(False, ["history_quality"]))
        self.assertEqual(self.scan(), [])

    def test_never_touches_a_pending_ohlc_trades_row(self) -> None:
        """Su blob dice accepted=False pero el estado lo decide la etapa previa."""
        payload = json.dumps({"accepted": False, "pending": True, "reasons": ["ohlc_trades"],
                              "checks": {"ohlc_trades": {"ohlc": 1, "min_trades": 4}}})
        insert(self.conn, "candidate_final_tick", 4, "pending_ohlc_trades", 0, payload)
        self.assertEqual(self.scan(), [])

    def test_never_touches_a_technical_history_failure(self) -> None:
        """Caso real: 2 filas con 'empty_tester_context' esperando reintento."""
        for index, reason in enumerate(("empty_tester_context", "real_tick_no_history"), start=58095):
            insert(self.conn, "candidate_final_tick", index, "pending_history_quality", 0,
                   sim(False, [reason]))
        self.assertEqual(self.scan(), [])

    def test_never_touches_statuses_decided_before_the_comparison(self) -> None:
        for index, status in enumerate(
            ("no_report", "parse_error", "report_mismatch", "no_trades", "no_history",
             "trade_disabled", "symbol_not_exist", "pending_tester_context"),
            start=10,
        ):
            insert(self.conn, "candidate_final_tick", index, status, 0, sim(True))
        self.assertEqual(self.scan(), [])

    def test_ignores_rows_without_a_usable_blob(self) -> None:
        insert(self.conn, "candidate_final_tick", 20, "rejected", 0, None)
        insert(self.conn, "candidate_final_tick", 21, "rejected", 0, "")
        insert(self.conn, "candidate_final_tick", 22, "rejected", 0, "no-es-json{")
        insert(self.conn, "candidate_final_tick", 23, "rejected", 0, json.dumps({"reasons": []}))
        self.assertEqual(self.scan(), [])

    def test_detects_a_stale_accepted_that_should_be_rejected(self) -> None:
        insert(self.conn, "candidate_final_tick", 30, "accepted", 1, sim(False, ["drawdown_pct"]))
        found = self.scan()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["expected_status"], "rejected")
        self.assertEqual(found[0]["expected_accepted"], 0)

    def test_detects_a_missed_pending_history_quality(self) -> None:
        insert(self.conn, "candidate_final_tick_6m", 31, "rejected", 0,
               sim(False, ["history_quality", "trades"]))
        found = self.scan()
        self.assertEqual(found[0]["expected_status"], "pending_history_quality")
        self.assertEqual(found[0]["expected_accepted"], 0)

    def test_scans_both_tables(self) -> None:
        insert(self.conn, "candidate_final_tick", 40, "rejected", 0, sim(True))
        insert(self.conn, "candidate_final_tick_6m", 41, "rejected", 0, sim(True))
        self.assertEqual({item["table"] for item in self.scan()},
                         {"candidate_final_tick", "candidate_final_tick_6m"})

    def test_survives_a_memory_without_the_tables(self) -> None:
        empty = configure_sqlite_connection(sqlite3.connect(":memory:"))
        empty.row_factory = sqlite3.Row
        try:
            self.assertEqual(self.scanner.scan_final_tick_status_mismatches(empty), [])
        finally:
            empty.close()

    def test_the_repair_makes_the_scan_come_back_clean(self) -> None:
        insert(self.conn, "candidate_final_tick_6m", 50, "rejected", 0, sim(True))
        insert(self.conn, "candidate_final_tick", 51, "accepted", 1, sim(False, ["trades"]))
        for item in self.scan():
            self.conn.execute(
                f"update {item['table']} set status=?, accepted=? where candidate_id=?",
                (item["expected_status"], item["expected_accepted"], item["candidate_id"]),
            )
        self.conn.commit()
        self.assertEqual(self.scan(), [])
        row = self.conn.execute(
            "select status, accepted from candidate_final_tick_6m where candidate_id=50"
        ).fetchone()
        self.assertEqual((row["status"], row["accepted"]), ("accepted", 1))


class RecomputeFinalTickVerdictsTests(unittest.TestCase):
    """Recalculo del veredicto desde las metricas guardadas, sin abrir MT5."""

    def setUp(self) -> None:
        self.conn = make_memory()
        for table in ("candidate_final_tick", "candidate_final_tick_6m"):
            for column, kind in (
                ("ohlc_metrics_json", "text"), ("real_tick_metrics_json", "text"),
                ("min_history_quality", "real"), ("max_net_delta_pct", "real"),
                ("max_pf_delta_pct", "real"), ("max_dd_delta_pct", "real"),
                ("max_trades_delta_pct", "real"),
            ):
                self.conn.execute(f"alter table {table} add column {column} {kind}")
        self.scanner = Scanner()

    def tearDown(self) -> None:
        self.conn.close()

    def metrics(self, **overrides) -> str:
        payload = dict(
            report_path="r.htm", name="ea", symbol="ROKU+", timeframe="H1", score=0.0,
            accepted=False, net_profit=0.0, raw_net_profit=0.0, normalized_net_profit=131.28,
            net_profit_factor=6.8913, net_profit_basis="b", normalization_group="Stocks",
            history_quality=99.0, profit_factor=1.7937, recovery_factor=0.7938, drawdown=1.0,
            drawdown_pct=2.4, trades=27, positive_month_ratio=0.833, max_month_concentration=0.4,
            avg_trade=0.7, sqn=1.0, reasons=[], losing_trades=9,
            score_config={"min_profit_factor": 1.20},
        )
        payload.update(overrides)
        return json.dumps(payload)

    def lossless(self, **overrides) -> str:
        return self.metrics(**{
            "profit_factor": 99.0, "recovery_factor": 99.0, "drawdown_pct": 0.0,
            "losing_trades": 0, "trades": 24, "normalized_net_profit": 300.32, **overrides,
        })

    def add(self, table, candidate_id, status, accepted, ohlc, tick, similarity):
        self.conn.execute(
            f"""insert into {table} (candidate_id, run_id, status, accepted, similarity_json,
                    ohlc_metrics_json, real_tick_metrics_json, min_history_quality,
                    max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct)
                values (?,1,?,?,?,?,?,80.0,35.0,35.0,35.0,35.0)""",
            (candidate_id, status, accepted, similarity, ohlc, tick),
        )

    def test_the_run_361_case_flips_to_accepted_in_6m(self) -> None:
        """RF de tick 0.7938 >= 0.75: el candidato que el artefacto tumbaba."""
        self.add("candidate_final_tick_6m", 70233, "rejected", 0,
                 self.lossless(), self.metrics(), sim(False, ["profit_factor", "drawdown_pct"]))
        changes = self.scanner.recompute_final_tick_verdicts(self.conn)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["expected_status"], "accepted")
        self.assertEqual(changes[0]["cause"], "ohlc_lossless")
        self.assertIn("ohlc_lossless", json.loads(changes[0]["similarity_json"])["checks"])

    def test_a_weak_tick_leg_stays_rejected_with_a_real_cause(self) -> None:
        self.add("candidate_final_tick_6m", 70235, "rejected", 0,
                 self.lossless(), self.metrics(recovery_factor=0.712),
                 sim(False, ["profit_factor", "drawdown_pct"]))
        self.assertEqual(self.scanner.recompute_final_tick_verdicts(self.conn), [])

    def test_it_reports_when_the_change_is_not_the_lossless_fix(self) -> None:
        """Filas de julio con la formula de delta antigua: causa distinta."""
        self.add("candidate_final_tick", 2921, "rejected", 0,
                 self.metrics(profit_factor=1.0068, losing_trades=5),
                 self.metrics(profit_factor=1.4715),
                 sim(False, ["profit_factor"]))
        changes = self.scanner.recompute_final_tick_verdicts(self.conn)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["cause"], "reglas_cambiadas")
        self.assertEqual(changes[0]["expected_status"], "accepted")

    def test_it_keeps_the_previous_blob_for_the_audit(self) -> None:
        previous = sim(False, ["profit_factor", "drawdown_pct"])
        self.add("candidate_final_tick_6m", 70237, "rejected", 0,
                 self.lossless(), self.metrics(), previous)
        changes = self.scanner.recompute_final_tick_verdicts(self.conn)
        self.assertEqual(changes[0]["stored_similarity_json"], previous)

    def test_it_skips_rows_without_tick_metrics(self) -> None:
        """Los fallos tecnicos guardan el OHLC pero no la pata de tick."""
        self.add("candidate_final_tick", 58095, "pending_history_quality", 0,
                 self.lossless(), None, sim(False, ["empty_tester_context"]))
        self.assertEqual(self.scanner.recompute_final_tick_verdicts(self.conn), [])

    def test_it_skips_statuses_decided_before_the_comparison(self) -> None:
        payload = json.dumps({"accepted": False, "pending": True, "reasons": ["ohlc_trades"]})
        self.add("candidate_final_tick", 70241, "pending_ohlc_trades", 0,
                 self.lossless(), self.metrics(), payload)
        self.assertEqual(self.scanner.recompute_final_tick_verdicts(self.conn), [])

    def test_it_leaves_a_verdict_that_still_holds(self) -> None:
        self.add("candidate_final_tick", 1, "accepted", 1,
                 self.metrics(profit_factor=1.75, losing_trades=8), self.metrics(),
                 sim(True))
        self.assertEqual(self.scanner.recompute_final_tick_verdicts(self.conn), [])

    def test_the_probe_gets_no_absolute_gates(self) -> None:
        """Mismo par que en 6M: en probe pasa, porque no hay puertas absolutas."""
        weak = self.metrics(recovery_factor=0.10, normalized_net_profit=1.0)
        self.add("candidate_final_tick", 500, "rejected", 0, self.lossless(), weak,
                 sim(False, ["profit_factor", "drawdown_pct"]))
        self.add("candidate_final_tick_6m", 501, "rejected", 0, self.lossless(), weak,
                 sim(False, ["profit_factor", "drawdown_pct"]))
        changes = {item["table"]: item for item in self.scanner.recompute_final_tick_verdicts(self.conn)}
        self.assertIn("candidate_final_tick", changes)
        self.assertEqual(changes["candidate_final_tick"]["expected_status"], "accepted")
        self.assertNotIn("candidate_final_tick_6m", changes)


class QueueSixMonthTests(unittest.TestCase):
    """Al desbloquear el corto, los candidatos pasan a 6M como 'pending'."""

    def setUp(self) -> None:
        self.conn = configure_sqlite_connection(sqlite3.connect(":memory:"))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "create table candidates (id integer primary key, run_id integer, status text,"
            " target_symbol text, symbol text, period text)"
        )
        self.conn.execute(
            "create table candidate_robustness (candidate_id integer primary key, status text)"
        )
        self.conn.execute(
            "create table candidate_final_tick_6m (candidate_id integer primary key,"
            " run_id integer, status text, accepted integer, evaluated_at text)"
        )
        self.scanner = Scanner()

    def tearDown(self) -> None:
        self.conn.close()

    def add_candidate(self, cid, *, run=7, status="accepted", robust="accepted", six_month=None):
        self.conn.execute(
            "insert into candidates (id, run_id, status, target_symbol, symbol, period)"
            " values (?,?,?,'ROKU+','ROKU+','H1')",
            (cid, run, status),
        )
        if robust is not None:
            self.conn.execute(
                "insert into candidate_robustness (candidate_id, status) values (?,?)",
                (cid, robust),
            )
        if six_month is not None:
            self.conn.execute(
                "insert into candidate_final_tick_6m (candidate_id, run_id, status, accepted,"
                " evaluated_at) values (?,?,?,0,'now')",
                (cid, run, six_month),
            )

    def test_queues_a_newly_eligible_candidate(self) -> None:
        self.add_candidate(101)
        queued = self.scanner.six_month_rows_to_queue(self.conn, [101])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["candidate_id"], 101)
        self.assertEqual(queued[0]["run_id"], 7)

    def test_does_not_queue_one_that_already_has_a_6m_row(self) -> None:
        self.add_candidate(102, six_month="rejected")
        self.assertEqual(self.scanner.six_month_rows_to_queue(self.conn, [102]), [])

    def test_does_not_queue_without_robustness_accepted(self) -> None:
        self.add_candidate(103, robust="rejected")
        self.add_candidate(104, robust=None)
        self.assertEqual(self.scanner.six_month_rows_to_queue(self.conn, [103, 104]), [])

    def test_does_not_queue_a_candidate_that_is_not_accepted(self) -> None:
        self.add_candidate(105, status="rejected")
        self.assertEqual(self.scanner.six_month_rows_to_queue(self.conn, [105]), [])

    def test_empty_input_is_a_no_op(self) -> None:
        self.assertEqual(self.scanner.six_month_rows_to_queue(self.conn, []), [])

    def test_pending_is_a_status_the_six_month_stage_retries(self) -> None:
        """Si 'pending' no fuese reintentable, la fila bloquearia el candidato."""
        from ubs_agent import FINAL_TICK_RETRYABLE_STATUSES

        self.assertIn("pending", FINAL_TICK_RETRYABLE_STATUSES)

    def test_a_queued_row_is_not_mistaken_for_a_verdict(self) -> None:
        """El reparador no debe tocar las filas que el mismo encola."""
        self.assertNotIn("pending", Scanner.FINAL_TICK_VERDICT_STATUSES)


if __name__ == "__main__":
    unittest.main()
