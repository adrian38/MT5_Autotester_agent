"""Que runs de una etapa tienen algo que hacer, y cuanto a cada uno.

El arreglo de pendientes trabaja por run -cada uno abre MT5 con su carpeta de
salida-, asi que arreglar una etapa entera es recorrer los suyos. Esta hoja
vigila que esa lista cuente lo mismo que la pantalla: ni los veredictos, ni lo
que se quedo sin reporte que hacer, y si los huecos de los dos tipos.
"""
from __future__ import annotations

import unittest

from tests.ubs.scoring.stage_audit_fixtures import (
    FINAL_STATUSES,
    insert_candidate,
    insert_final_tick,
    insert_robustness,
    memory,
)
from ubs.stage_rules_audit import STAGE_SPECS, pending_runs, stage_spec


class PendingRunsTests(unittest.TestCase):
    def test_each_run_carries_its_own_count(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")
        insert_final_tick(conn, 2, run_id=7, status="pending")
        insert_final_tick(conn, 3, run_id=8, status="pending")
        insert_final_tick(conn, 4, run_id=8, status="parse_error")

        gaps = pending_runs(conn, "final_tick", final_statuses=FINAL_STATUSES)

        self.assertEqual([(gap.run_id, gap.pending) for gap in gaps], [(7, 1), (8, 2)])

    def test_a_run_with_only_verdicts_is_not_in_the_list(self) -> None:
        """La lista es exactamente lo que hay que lanzar."""
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="accepted")
        insert_final_tick(conn, 2, run_id=7, status="rejected")

        self.assertEqual(pending_runs(conn, "final_tick", final_statuses=FINAL_STATUSES), ())

    def test_what_has_no_report_to_make_does_not_bring_a_run_in(self) -> None:
        conn = memory()
        insert_final_tick(conn, 1, run_id=7, status="pending_ohlc_trades")

        self.assertEqual(pending_runs(conn, "final_tick", final_statuses=FINAL_STATUSES), ())

    def test_an_eligible_without_a_row_brings_its_run_in(self) -> None:
        """Un run cuyo unico hueco es «sin fila» tambien hay que correrlo.

        Contando solo estados se quedaba fuera, y con el la Regresiva entera:
        alli no hay nada a medias, solo elegibles que no han corrido.
        """
        conn = memory()
        for candidate_id, run_id in ((1, 7), (2, 8)):
            insert_candidate(conn, candidate_id, run_id=run_id, status="accepted", min_trades=60)
            insert_robustness(conn, candidate_id, run_id=run_id, status="accepted")
        insert_final_tick(conn, 1, run_id=7, status="accepted")

        gaps = pending_runs(conn, "final_tick", final_statuses=FINAL_STATUSES)

        self.assertEqual([(gap.run_id, gap.pending, gap.missing) for gap in gaps], [(8, 0, 1)])
        self.assertEqual(gaps[0].rows, 1)

    def test_a_history_probe_never_brings_a_run_in(self) -> None:
        conn = memory()
        insert_candidate(
            conn, 1, run_id=7, status="no_history", min_trades=None, policy="history_probe"
        )

        self.assertEqual(pending_runs(conn, "candidates", final_statuses=FINAL_STATUSES), ())

    def test_a_seed_stage_has_no_runs(self) -> None:
        """Las semillas no cuelgan de ningun run: las arregla una corrida."""
        conn = memory()

        for stage in ("seeds", "seed_final_tick", "seed_final_tick_6m"):
            with self.subTest(stage=stage):
                self.assertEqual(pending_runs(conn, stage, final_statuses=FINAL_STATUSES), ())


class UpstreamShapeTests(unittest.TestCase):
    """La consulta por run se deriva de la declarada, asi que tiene forma.

    `_grouped_upstream_sql` convierte el join de elegibilidad en el mismo
    contado por run. Es una derivacion, no una segunda declaracion -para que
    no puedan separarse-, y eso solo vale mientras las etapas de candidato
    tengan la forma que supone.
    """

    def test_every_run_scoped_upstream_can_be_grouped(self) -> None:
        for spec in STAGE_SPECS:
            if not spec.run_column or not spec.upstream_sql:
                continue
            with self.subTest(stage=spec.key):
                self.assertIn("select count(*)", spec.upstream_sql)
                self.assertIn("from candidates c", spec.upstream_sql)
                self.assertIn("{run_filter}", spec.upstream_sql)

    def test_the_grouped_query_runs_and_counts_the_same(self) -> None:
        conn = memory()
        for candidate_id, run_id in ((1, 7), (2, 7), (3, 8)):
            insert_candidate(conn, candidate_id, run_id=run_id, status="accepted", min_trades=60)

        gaps = pending_runs(conn, "robustness", final_statuses=FINAL_STATUSES)

        self.assertEqual([(gap.run_id, gap.missing) for gap in gaps], [(7, 2), (8, 1)])
        self.assertEqual(stage_spec("robustness").run_column, "run_id")


if __name__ == "__main__":
    unittest.main()
