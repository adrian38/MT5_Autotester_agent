"""Que filas se juzgaron con una regla que ya no es la de ahora."""
from __future__ import annotations

import sqlite3
import unittest

from tests.ubs.scoring.stage_audit_fixtures import (
    CURRENT_FINAL_TICK,
    CURRENT_SCORE,
    FINAL_STATUSES,
    AFTER_CHANGE,
    BEFORE_CHANGE,
    declared_rule_change,
    insert_candidate,
    insert_final_tick,
    memory,
)
from ubs.stage_rules_audit import MAX_REPORTED_IDS, audit_stage_rules


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
        # Sigue contando como veredicto torcido -se juzgo sobre una ventana que
        # no existe-, pero por `undated`, no por una regla movida.
        self.assertEqual(audit.undated_verdicts, 1)
        self.assertEqual(audit.affected_verdicts, 1)
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

    def test_the_invalid_dates_say_why_there_is_no_window(self) -> None:
        """Ni «vacia o 1970.01.01» ni «1970.01.01»: el motivo, que es lo util.

        El valor guardado no dice nada por si solo. Lo que separa a una fila de
        otra es si no ha corrido o si corrio sin producir nada.
        """
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

        # Cada motivo cuenta las filas a las que alcanza, no las celdas: la
        # fila 1 trae el cero de MT5 en sus dos fechas y cuenta una vez.
        self.assertEqual(audit.undated, 3)
        self.assertEqual(
            audit.undated_stored, (("no ha corrido", 2), ("reporte sin periodo", 2))
        )

    def test_a_real_date_move_is_reported_as_window_not_as_rule(self) -> None:
        """La ventana se dice, pero no como regla movida: no juzga nada.

        La fila corrio sobre ese periodo y su veredicto esta bien dado, asi que
        tampoco cuenta como afectada: no hay nada que arreglarle. Se cuenta y
        se ensena, fecha por fecha, y ahi acaba.
        """
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

        self.assertEqual(audit.findings, ())
        self.assertEqual(audit.offwindow_verdicts, 1)
        self.assertEqual([w.rule for w in audit.window_findings], ["from_date"])
        self.assertEqual(audit.window_findings[0].stored, (("2026.01.01", 1),))
        self.assertEqual(audit.window_findings[0].current, "2026.05.01")
        self.assertEqual(audit.affected_verdicts, 0)

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

        self.assertEqual(audits[0].window_findings[0].stored, (("2026.04.01", 1),))
        self.assertEqual(audits[0].window_findings[0].current, "2026.05.01")

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
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", evaluated_at=BEFORE_CHANGE)
        insert_final_tick(conn, 2, run_id=7, status="pending", evaluated_at=BEFORE_CHANGE)
        insert_final_tick(conn, 3, run_id=7, status="accepted", evaluated_at=AFTER_CHANGE)

        with declared_rule_change("final_tick"):
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
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="pending", evaluated_at=BEFORE_CHANGE)

        with declared_rule_change("final_tick"):
            audit = audit_stage_rules(
                conn,
                {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
                final_statuses=FINAL_STATUSES,
                stages=("final_tick",),
            )[0]

        self.assertEqual(audit.stale, 1)
        self.assertEqual(audit.stale_verdicts, 0)
        self.assertEqual(audit.affected, 0)

    def test_a_row_that_has_not_run_did_not_meet_the_old_rule(self) -> None:
        """Sin sello la fila no se ha juzgado, y correra con la regla de hoy."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="pending", evaluated_at="")

        with declared_rule_change("final_tick"):
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


if __name__ == "__main__":
    unittest.main()
