"""Que le falta por correr a cada etapa, y que arreglo le toca.

La auditoria de reglas movidas vive en `test_ubs_stage_rules_audit.py`; aqui
estan las otras dos preguntas que se hacen sobre lo mismo: quien se quedo sin
juzgar, y cual de los dos botones pone al dia cada etapa.
"""
from __future__ import annotations

import unittest

from tests.ubs.scoring.stage_audit_fixtures import (
    CURRENT_FINAL_TICK,
    FINAL_STATUSES,
    AFTER_CHANGE,
    BEFORE_CHANGE,
    declared_rule_change,
    insert_candidate,
    insert_final_tick,
    insert_robustness,
    memory,
)
from ubs.stage_rules_audit import (
    audit_pending_states,
    audit_stage_rules,
    stages_to_repeat,
    stages_to_rescore,
)


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

    def test_a_stale_row_without_a_verdict_queues_nothing(self) -> None:
        """Ni repuntuar ni repetir: cuando corra, correra con la regla de hoy."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="pending", evaluated_at=BEFORE_CHANGE)

        with declared_rule_change("final_tick"):
            audits = audit_stage_rules(
                conn,
                {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
                final_statuses=FINAL_STATUSES,
                stages=("final_tick",),
            )

        self.assertEqual(stages_to_rescore(audits), ())
        self.assertEqual(stages_to_repeat(audits), ())

    def test_a_moved_window_alone_only_asks_for_a_repeat(self) -> None:
        """Mover la ventana no torcio ningun veredicto: no hay que repuntuar.

        Ningun repuntuado mueve una fecha -Final Tick la relee del reporte
        OHLC, robustez no la reescribe y la regresiva se salta la fila cuya
        ventana no coincide-, y como ningun umbral se movio tampoco hay nada
        mas que volver a juzgar: encolarla era recorrer la tabla entera para
        reescribir cada veredicto con el valor que ya tenia y acabar igual de
        roja.
        """
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")

        audits = audit_stage_rules(
            conn,
            {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0,
                            "from_date": "2025.06.30"}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(audits[0].findings, ())
        self.assertEqual(audits[0].offwindow_verdicts, 1)
        self.assertEqual(stages_to_rescore(audits), ())
        self.assertEqual(stages_to_repeat(audits), ("final_tick",))

    def test_a_verdict_without_a_window_is_what_the_rescore_goes_for(self) -> None:
        """No se puede volver a juzgar, asi que el repuntuado lo baja a pendiente.

        Es el unico caso en el que el boton entra sin que se haya movido una
        regla: el veredicto se dio sobre una ventana que no existe y no vale,
        y `_demote_if_windowless()` lo devuelve a `no_report`. Repetir la etapa
        entera no hace falta: la fila ya pasa a ser de Arreglar pendientes.
        """
        conn = memory()
        insert_final_tick(
            conn, 1, run_id=7, status="rejected",
            from_date="1970.01.01", to_date="1970.01.01",
        )

        audits = audit_stage_rules(
            conn,
            {"final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(audits[0].undated_verdicts, 1)
        self.assertEqual(stages_to_rescore(audits), ("final_tick",))
        self.assertEqual(stages_to_repeat(audits), ())

    def test_without_a_rescore_mode_that_same_row_asks_for_a_repeat(self) -> None:
        """Seeds Final Tick no tiene `--rescore-*-only`: nadie puede bajarla."""
        conn = memory()
        conn.execute(
            """
            insert into seed_final_tick (
                seed_id, status, min_history_quality, from_date, to_date,
                max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                max_trades_delta_pct, max_dd_gap_pp, seed_mtime, seed_size, evaluated_at
            ) values (1, 'accepted', 80.0, '1970.01.01', '1970.01.01', 35.0, 35.0, 35.0, 35.0, 3.0, 0, 0, '')
            """
        )

        audits = audit_stage_rules(
            conn,
            {"seed_final_tick": CURRENT_FINAL_TICK},
            final_statuses=FINAL_STATUSES,
            stages=("seed_final_tick",),
        )

        self.assertEqual(audits[0].undated_verdicts, 1)
        self.assertEqual(stages_to_rescore(audits), ())
        self.assertEqual(stages_to_repeat(audits), ("seed_final_tick",))

    def test_a_stage_where_nothing_moved_is_not_queued(self) -> None:
        """Repuntuarla seria reescribir cada veredicto con el valor que tenia."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")

        with declared_rule_change("final_tick"):
            audits = audit_stage_rules(
                conn,
                {"final_tick": {**CURRENT_FINAL_TICK, "max_pf_delta_pct": 35.0}},
                final_statuses=FINAL_STATUSES,
                stages=("final_tick",),
            )

        self.assertEqual(audits[0].findings, ())
        self.assertEqual(stages_to_rescore(audits), ())

    def test_a_moved_threshold_alongside_a_moved_window_still_rescores(self) -> None:
        """La etapa entra por el umbral, y la fecha sigue pidiendo repetirla."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")

        audits = audit_stage_rules(
            conn,
            {"final_tick": {**CURRENT_FINAL_TICK, "from_date": "2025.06.30"}},
            final_statuses=FINAL_STATUSES,
            stages=("final_tick",),
        )

        self.assertEqual(stages_to_rescore(audits), ("final_tick",))
        self.assertEqual(stages_to_repeat(audits), ("final_tick",))

    def test_a_declared_rule_change_also_queues_the_rescore(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted", evaluated_at=BEFORE_CHANGE)

        with declared_rule_change("final_tick"):
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
        conn = memory()
        conn.execute(
            """
            insert into seed_final_tick (
                seed_id, status, min_history_quality, from_date, to_date,
                max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                max_trades_delta_pct, max_dd_gap_pp, seed_mtime, seed_size, evaluated_at
            ) values (1, 'accepted', 80.0, '2026.05.01', '2026.05.31', 35.0, 35.0, 35.0, 35.0, 3.0, 0, 0, ?)
            """,
            (BEFORE_CHANGE,),
        )

        with declared_rule_change("seed_final_tick", fix="repeat"):
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
