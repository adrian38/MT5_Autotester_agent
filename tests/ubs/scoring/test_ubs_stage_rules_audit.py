import json
import sqlite3
import unittest

from ubs.memory_schema import SCHEMA_DDL
from ubs.stage_rules_audit import (
    MAX_REPORTED_IDS,
    audit_pending_states,
    audit_stage_rules,
    stage_spec,
    stages_to_repeat,
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
        # Sin sello la fila no se juzgo nunca, que es lo que quieren casi todas
        # estas pruebas: asi el cambio de regla declarado no las alcanza.
        "evaluated_at": "",
    }
    values.update(overrides)
    conn.execute(
        """
        insert into candidate_final_tick (
            candidate_id, run_id, status, min_history_quality, from_date, to_date,
            max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
            max_dd_gap_pp, evaluated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id, run_id, status, values["min_history_quality"],
            values["from_date"], values["to_date"], values["max_net_delta_pct"],
            values["max_pf_delta_pct"], values["max_dd_delta_pct"],
            values["max_trades_delta_pct"], values["max_dd_gap_pp"],
            values["evaluated_at"],
        ),
    )


def before_and_after(stage_key: str) -> tuple[str, str]:
    """Dos sellos que rodean al cambio de regla declarado de una etapa.

    Se derivan de la declaracion en vez de escribirse a mano: la fecha se
    movera cuando la regla vuelva a cambiar, y la prueba pregunta por el antes
    y el despues, no por un dia concreto.
    """
    stamp = stage_spec(stage_key).rules_changed_at
    return f"{int(stamp[:4]) - 1}-01-01T00:00:00", "9999-01-01T00:00:00"


def insert_robustness(conn, candidate_id: int, *, run_id: int, status: str) -> None:
    conn.execute(
        """
        insert into candidate_robustness (
            candidate_id, run_id, status, from_date, to_date,
            positive_bonus, negative_bonus, evaluated_at
        ) values (?, ?, ?, '2026.01.01', '2026.06.30', 70.0, -70.0, '')
        """,
        (candidate_id, run_id, status),
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

    def test_the_mt5_zero_date_is_not_a_moved_rule(self) -> None:
        """1970.01.01 es el cero que MT5 escribe sin periodo, no un umbral.

        `_read_ohlc_report_cfg_dates()` copia a la fila las fechas del reporte
        OHLC, y un reporte sin periodo las trae a cero. Denunciarlo como regla
        movida mandaba a repuntuar una etapa que nadie habia tocado.
        """
        conn = memory()
        insert_final_tick(
            conn, 1, run_id=7, status="rejected", max_pf_delta_pct=30.0,
            from_date="1970.01.01", to_date="1970.01.01",
        )
        insert_final_tick(conn, 2, run_id=7, status="accepted", max_pf_delta_pct=30.0)

        audit = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual([finding.rule for finding in audit.findings], [])
        self.assertEqual(audit.affected, 0)
        # Con veredicto: se juzgo sobre una ventana imposible, no esta al dia.
        self.assertEqual(audit.undated, 1)
        self.assertEqual(audit.undated_verdicts, 1)

    def test_an_empty_date_is_not_a_moved_rule_either(self) -> None:
        """Una fila que aun no ha corrido no guarda fecha, no guarda otra."""
        conn = memory()
        insert_final_tick(
            conn, 1, run_id=7, status="pending", max_pf_delta_pct=30.0,
            from_date="", to_date="",
        )

        audit = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual(audit.findings, ())
        # Sin veredicto: no ha corrido, que es cosa de Consultar pendientes.
        self.assertEqual(audit.undated, 1)
        self.assertEqual(audit.undated_verdicts, 0)

    def test_the_invalid_dates_say_which_one_they_were(self) -> None:
        """«vacia o 1970.01.01» era una adivinanza: el dato esta en la fila."""
        conn = memory()
        insert_final_tick(
            conn, 1, run_id=7, status="rejected",
            from_date="1970.01.01", to_date="1970.01.01",
        )
        insert_final_tick(conn, 2, run_id=7, status="pending", from_date="", to_date="")
        # Una misma fila puede traer las dos: la vacia en una fecha y el cero
        # en la otra, y entonces cuenta una vez en cada valor.
        insert_final_tick(conn, 3, run_id=7, status="pending", from_date="", to_date="1970.01.01")

        audit = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        # Cada valor cuenta las filas a las que alcanza, no las celdas: la
        # fila 1 trae el cero de MT5 en sus dos fechas y cuenta una vez.
        self.assertEqual(audit.undated, 3)
        self.assertEqual(audit.undated_stored, (("(vacio)", 2), ("1970.01.01", 2)))

    def test_a_real_date_move_is_still_reported(self) -> None:
        conn = memory()
        insert_final_tick(
            conn, 1, run_id=7, status="accepted",
            max_pf_delta_pct=30.0, from_date="2026.01.01",
        )

        audit = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual([finding.rule for finding in audit.findings], ["from_date"])
        self.assertEqual(audit.findings[0].stored, (("2026.01.01", 1),))
        self.assertEqual(audit.affected_verdicts, 1)

    def test_affected_verdicts_count_each_row_once(self) -> None:
        """Dos reglas movidas sobre la misma fila son una fila, no dos."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", max_dd_gap_pp=5.0)
        insert_final_tick(conn, 2, run_id=7, status="pending", max_dd_gap_pp=5.0)

        audit = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual(len(audit.findings), 2)
        self.assertEqual(audit.affected, 2)
        self.assertEqual(audit.affected_verdicts, 1)

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

    def test_a_declared_rule_change_marks_what_it_preceded(self) -> None:
        """Los umbrales son los de hoy; lo que cambio fue como se aplican.

        Un cambio asi no deja rastro en la fila -los numeros guardados siguen
        coincidiendo-, asi que solo el sello dice quien nacio con la regla
        vieja.
        """
        before, after = before_and_after("final_tick")
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", evaluated_at=before)
        insert_final_tick(conn, 2, run_id=7, status="pending", evaluated_at=before)
        insert_final_tick(conn, 3, run_id=7, status="accepted", evaluated_at=after)

        audit = audit_stage_rules(
            conn,
            {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual(audit.findings, ())
        self.assertEqual(audit.stale, 2)
        self.assertEqual(audit.stale_verdicts, 1)
        # Arrastra solo la que tiene veredicto: la pendiente se juzgara con la
        # regla de ahora en cuanto corra, y el repuntuado ni la mira.
        self.assertEqual(audit.affected, 1)
        self.assertEqual(audit.affected_verdicts, 1)

    def test_a_stale_row_without_a_verdict_leaves_the_stage_up_to_date(self) -> None:
        """Un repuntuado no toca lo que no tiene veredicto: no hay que puntuar.

        Contarlo dejaba la etapa en rojo despues de repuntuar, y por mucho que
        se repitiera: la fila sigue pendiente y su sello sigue siendo viejo.
        """
        before, _after = before_and_after("final_tick")
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="pending", evaluated_at=before)

        audit = audit_stage_rules(
            conn,
            {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual(audit.stale, 1)
        self.assertEqual(audit.stale_verdicts, 0)
        self.assertEqual(audit.affected, 0)
        self.assertEqual(stages_to_rescore((audit,)), ())

    def test_a_row_that_has_not_run_did_not_meet_the_old_rule(self) -> None:
        """Sin sello la fila no se ha juzgado, y correra con la regla de hoy."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="pending", evaluated_at="")

        audit = audit_stage_rules(
            conn,
            {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )[0]

        self.assertEqual(audit.stale, 0)
        self.assertEqual(audit.affected, 0)

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
        self.assertEqual(summary.missing, 0)

    def test_an_eligible_without_a_row_is_counted_apart(self) -> None:
        """Quien se vuelve elegible al repuntuar no se queda pendiente: no esta.

        Sin fila no hay estado que contar, asi que la etapa parecia al dia
        mientras le faltaba por juzgar justo a quien acababa de pasar a
        accepted en la etapa de arriba.
        """
        conn = memory()
        for candidate_id in (1, 2, 3):
            insert_candidate(conn, candidate_id, run_id=7, status="accepted", min_trades=60)
            insert_robustness(conn, candidate_id, run_id=7, status="accepted")
        insert_final_tick(conn, 1, run_id=7, status="accepted")

        summary = audit_pending_states(
            conn, final_statuses=FINAL_STATUSES, stages=("final_tick",)
        )[0]

        self.assertEqual(summary.rows, 1)
        self.assertEqual(summary.pending, 0)
        self.assertEqual(summary.missing, 2)

    def test_an_eligible_without_a_row_respects_the_run_scope(self) -> None:
        conn = memory()
        for candidate_id, run_id in ((1, 7), (2, 8)):
            insert_candidate(conn, candidate_id, run_id=run_id, status="accepted", min_trades=60)
            insert_robustness(conn, candidate_id, run_id=run_id, status="accepted")

        summary = audit_pending_states(
            conn, final_statuses=FINAL_STATUSES, run_id=7, stages=("final_tick",)
        )[0]

        self.assertEqual(summary.missing, 1)

    def test_a_candidate_the_stage_never_wanted_is_not_missing(self) -> None:
        """Final Tick solo quiere a los que la robustez acepto."""
        conn = memory()
        insert_candidate(conn, 1, run_id=7, status="accepted", min_trades=60)
        insert_robustness(conn, 1, run_id=7, status="rejected")

        summary = audit_pending_states(
            conn, final_statuses=FINAL_STATUSES, stages=("final_tick",)
        )[0]

        self.assertEqual(summary.missing, 0)


class StagesToRescoreTests(unittest.TestCase):
    def test_both_final_tick_tables_enter_the_queue(self) -> None:
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

        self.assertEqual(stages_to_rescore(audits), ("final_tick", "final_tick_6m"))

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
        self.assertEqual(stages_to_repeat(audits), ("seed_final_tick",))

    def test_a_declared_rule_change_also_queues_the_rescore(self) -> None:
        before, _after = before_and_after("final_tick")
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", evaluated_at=before)

        audits = audit_stage_rules(
            conn,
            {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(audits[0].findings, ())
        self.assertEqual(stages_to_rescore(audits), ("final_tick",))
        self.assertEqual(stages_to_repeat(audits), ())

    def test_a_change_that_needs_the_stage_repeated_is_not_rescored(self) -> None:
        """Seeds Final Tick no tiene repuntuado: su cambio se declara `repeat`."""
        before, _after = before_and_after("seed_final_tick")
        conn = memory()
        conn.execute(
            """
            insert into seed_final_tick (
                seed_id, status, min_history_quality, from_date, to_date,
                max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                max_trades_delta_pct, max_dd_gap_pp, seed_mtime, seed_size, evaluated_at
            ) values (1, 'accepted', 80.0, '2026.05.01', '2026.05.31', 35.0, 35.0, 35.0, 35.0, 3.0, 0, 0, ?)
            """,
            (before,),
        )

        audits = audit_stage_rules(
            conn,
            {"seed_final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
            final_statuses=FINAL_STATUSES,
            stages=("seed_final_tick",),
        )

        self.assertEqual(audits[0].stale, 1)
        self.assertEqual(stages_to_rescore(audits), ())
        self.assertEqual(stages_to_repeat(audits), ("seed_final_tick",))


if __name__ == "__main__":
    unittest.main()
