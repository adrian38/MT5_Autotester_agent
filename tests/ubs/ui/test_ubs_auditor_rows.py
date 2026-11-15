"""Lo que la pantalla del auditor pinta a partir de lo que conto la memoria.

Aqui no hay interfaz que lanzar: son funciones que reciben una etapa auditada
y devuelven la fila que se ve. La hoja hermana,
`test_ubs_auditor_ui.py`, mira lo contrario: lo que la pantalla hace cuando se
pulsa. Las dos vivian juntas hasta que el fichero se paso de las 600 lineas.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.ubs.scoring.stage_audit_fixtures import (
    RULE_CHANGE_NOTE,
    RULE_CHANGED_AT,
    declared_rule_change,
)
from tests.ubs.ui.auditor_fixtures import fake_audit_row, fake_finding, fake_pending_row
from ui.ubs_auditor_queries import (
    MAX_STORED_VALUES_SHOWN,
    _blind_rules_note,
    _of_total,
    _pending_group,
    _pending_headline,
    _rule_group,
    _rules_status_line,
    _settled_note,
    _stage_headline,
    _stored_cell,
)


class CellTests(unittest.TestCase):
    def test_counts_say_what_they_count_and_against_what(self) -> None:
        self.assertEqual(_of_total(2, 3798, "candidates"), "2 de 3798 candidatos")
        self.assertEqual(_of_total(0, 322, "seeds"), "0 de 322 semillas")

    def test_a_long_list_of_stored_values_is_summed_up(self) -> None:
        finding = SimpleNamespace(
            stored=(("2026.01.01", 2396), ("(vacio)", 1046), ("2025.09.01", 8))
        )

        cell = _stored_cell(finding)

        self.assertEqual(cell, "2026.01.01 (2396), (vacio) (1046) +1 mas")
        self.assertEqual(cell.count("("), MAX_STORED_VALUES_SHOWN + 1)

    def test_a_short_list_is_left_alone(self) -> None:
        finding = SimpleNamespace(stored=(("1970.01.01", 2),))

        self.assertEqual(_stored_cell(finding), "1970.01.01 (2)")

    def test_blind_rules_are_named_once_per_group_of_stages(self) -> None:
        note = _blind_rules_note()

        self.assertEqual(note.count("Min ops OHLC"), 1)
        self.assertIn("Final Tick 6M", note)
        self.assertIn("Robustez", note)


class StageHeadlineTests(unittest.TestCase):
    """Lo que se lee de una etapa sin desplegarla."""

    @staticmethod
    def _audit(*, moved=0, undated=0, undated_verdicts=0, offwindow_verdicts=0,
               stale=0, stale_verdicts=None):
        return SimpleNamespace(
            findings=tuple(fake_finding(verdicts=1) for _ in range(moved)),
            undated=undated,
            undated_verdicts=undated_verdicts,
            offwindow_verdicts=offwindow_verdicts,
            stale=stale,
            stale_verdicts=stale if stale_verdicts is None else stale_verdicts,
        )

    def test_moved_rules_win_the_headline(self) -> None:
        self.assertEqual(_stage_headline(self._audit(moved=2)), "2 reglas movidas")
        self.assertEqual(_stage_headline(self._audit(moved=1)), "1 regla movida")

    def test_a_moved_rule_nobody_judged_is_not_a_moved_rule_yet(self) -> None:
        """Alcanza solo a pendientes: se juzgaran con el valor de ahora."""
        audit = self._audit()
        audit.findings = (fake_finding(verdicts=0),)

        self.assertEqual(_stage_headline(audit), "Al dia")

    def test_a_changed_rule_also_takes_the_stage_out_of_date(self) -> None:
        """Sin un umbral movido, la etapa sigue sin estar al dia."""
        self.assertEqual(_stage_headline(self._audit(stale=12)), "Regla cambiada")

    def test_a_changed_rule_nobody_judged_leaves_the_stage_up_to_date(self) -> None:
        audit = self._audit(stale=1046, stale_verdicts=0)

        self.assertEqual(_stage_headline(audit), "Al dia")

    def test_a_moved_rule_is_more_concrete_than_a_changed_one(self) -> None:
        audit = self._audit(moved=1, stale=12)

        self.assertEqual(_stage_headline(audit), "1 regla movida")

    def test_rows_judged_without_a_window_are_not_up_to_date(self) -> None:
        audit = self._audit(undated=2, undated_verdicts=2)

        self.assertEqual(_stage_headline(audit), "2 sin fecha valida")

    def test_rows_that_simply_have_not_run_leave_the_stage_up_to_date(self) -> None:
        audit = self._audit(undated=1046, undated_verdicts=0)

        self.assertEqual(_stage_headline(audit), "Al dia")

    def test_nothing_wrong_reads_as_up_to_date(self) -> None:
        self.assertEqual(_stage_headline(self._audit()), "Al dia")



class RuleGroupTests(unittest.TestCase):
    def test_a_changed_rule_hangs_off_the_stage_in_red(self) -> None:
        with declared_rule_change("final_tick"):
            _stage, _label, values, tag, children = _rule_group(
                fake_audit_row(stale=4, stale_verdicts=3, affected=4, affected_verdicts=3)
            )

        self.assertEqual(tag, "rejected")
        self.assertEqual(values[0], "Regla cambiada")
        self.assertEqual(values[4], "3 de 10 candidatos")
        self.assertEqual(len(children), 1)
        child_values, child_tag = children[0]
        self.assertEqual(child_tag, "rejected")
        self.assertEqual(child_values[0], "Regla cambiada")
        # La celda ancha dice que cambio y desde cuando, sin la hora.
        self.assertIn(RULE_CHANGE_NOTE, child_values[3])
        self.assertIn(RULE_CHANGED_AT[:10], child_values[3])
        self.assertNotIn(RULE_CHANGED_AT[10:], child_values[3])

    def test_nothing_that_lacks_a_verdict_hangs_at_all(self) -> None:
        """Al dia y sin nada colgando: la etapa no tiene de que hablar.

        Filas selladas antes del cambio pero sin veredicto, filas sin fecha
        que no han corrido, filas sin configuracion guardada: ninguna se juzgo
        con una regla vieja, que es lo unico que este boton contesta. Colgaban
        en gris y eran tres lineas de ruido bajo una etapa verde.
        """
        _stage, _label, values, tag, children = _rule_group(
            fake_audit_row(stale=1046, undated=1045, unknown=86237)
        )

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(values[4], "0 de 10 candidatos")
        self.assertEqual(children, ())

    def test_a_moved_rule_that_reached_no_verdict_hangs_either(self) -> None:
        """Se movio, pero lo que alcanza correra con la regla de ahora."""
        _stage, _label, values, tag, children = _rule_group(
            fake_audit_row(findings=(fake_finding(verdicts=0),))
        )

        self.assertEqual(values[0], "Al dia")
        self.assertEqual(tag, "accepted")
        self.assertEqual(children, ())

    def test_the_invalid_dates_name_themselves_with_their_counts(self) -> None:
        _stage, _label, _values, _tag, children = _rule_group(
            fake_audit_row(undated=3, undated_verdicts=1,
                       undated_stored=(("(vacio)", 2), ("1970.01.01", 1)))
        )

        self.assertEqual(children[0][0][3], "(vacio) (2), 1970.01.01 (1)")

    def test_a_stage_with_nothing_wrong_stays_green_and_closed(self) -> None:
        _stage, _label, values, tag, children = _rule_group(fake_audit_row())

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(children, ())


class PendingGroupTests(unittest.TestCase):
    def test_the_two_gaps_are_separate_children(self) -> None:
        _stage, _label, values, tag, children = _pending_group(
            fake_pending_row(rows=10, pending=2, missing=3, by_status=(("pending_ohlc", 2),))
        )

        self.assertEqual(tag, "rejected")
        self.assertEqual(values[0], "A medias y sin correr")
        # El total cuenta tambien a los que no tienen fila: 2 + 3 de 10 + 3.
        self.assertEqual(values[4], "5 de 13 candidatos")
        self.assertEqual([child[0][0] for child in children], ["Se quedaron a medias", "Sin fila todavia"])
        self.assertEqual(children[0][0][4], "2 de 13 candidatos")
        self.assertEqual(children[1][0][4], "3 de 13 candidatos")

    def test_what_is_half_done_names_its_run_in_the_detail(self) -> None:
        """Sin el run, el desglose decia que paso pero no donde arreglarlo."""
        _stage, _label, _values, _tag, children = _pending_group(
            fake_pending_row(
                rows=10,
                pending=3,
                by_status=(("no_report", 2), ("generated", 1)),
                by_run_status=(
                    (7, (("no_report", 2),)),
                    (9, (("generated", 1),)),
                ),
            )
        )

        self.assertEqual(
            children[0][0][3], "run #7: no_report (2); run #9: generated (1)"
        )

    def test_a_stage_without_runs_keeps_the_bare_status(self) -> None:
        """Las de semilla no cuelgan de ningun run: no se inventa uno."""
        _stage, _label, _values, _tag, children = _pending_group(
            fake_pending_row(rows=10, pending=2, by_status=(("pending", 2),))
        )

        self.assertEqual(children[0][0][3], "pending (2)")

    def test_an_eligible_without_a_row_alone_still_opens_the_stage(self) -> None:
        """Antes esta etapa salia «Al dia» porque sin fila no hay estado."""
        _stage, _label, values, tag, children = _pending_group(
            fake_pending_row(rows=0, pending=0, missing=4)
        )

        self.assertEqual(tag, "rejected")
        self.assertEqual(values[0], "Sin correr")
        self.assertEqual(values[4], "4 de 4 candidatos")
        self.assertEqual(len(children), 1)

    def test_a_stage_with_both_gaps_closed_is_up_to_date(self) -> None:
        _stage, _label, values, tag, children = _pending_group(fake_pending_row())

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(children, ())

    def test_each_gap_names_itself_in_the_headline(self) -> None:
        self.assertEqual(_pending_headline(fake_pending_row(pending=1)), "Se quedaron a medias")
        self.assertEqual(_pending_headline(fake_pending_row(missing=1)), "Sin correr")

    def test_what_nobody_retries_does_not_open_the_stage(self) -> None:
        """La etapa con 9.954 parados y nada pendiente esta al dia."""
        _stage, _label, values, tag, children = _pending_group(
            fake_pending_row(rows=10, settled=9954, settled_by_status=(("no_trades", 9954),))
        )

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(children, ())


class SettledNoteTests(unittest.TestCase):
    def test_what_was_left_out_is_said_once_and_outside_the_table(self) -> None:
        note = _settled_note((
            fake_pending_row(
                stage="candidates",
                stage_label="Resultados",
                settled=3,
                settled_by_status=(("no_trades", 2), ("symbol_not_exist", 1)),
            ),
            fake_pending_row(),
        ))

        self.assertIn("3 sin reporte que hacer", note)
        self.assertIn("Resultados (no_trades 2, symbol_not_exist 1)", note)
        self.assertNotIn("Final Tick", note)

    def test_a_memory_without_parked_rows_says_nothing(self) -> None:
        self.assertEqual(_settled_note((fake_pending_row(),)), "")


class StatusLineTests(unittest.TestCase):
    def test_a_changed_rule_is_said_apart_from_a_moved_one(self) -> None:
        line = _rules_status_line(
            (fake_audit_row(stale=4, stale_verdicts=3, affected_verdicts=3),),
            "ICTRADING/REAL | run #7",
        )

        # Cuenta veredictos: son los unicos que el repuntuado puede cambiar.
        self.assertIn("1 etapa(s) con la regla cambiada (3 veredicto(s))", line)
        self.assertNotIn("regla(s) movida(s)", line)
        self.assertIn("Afecta a 3 veredicto(s)", line)

    def test_what_the_rescore_cannot_reach_is_said_in_the_same_line(self) -> None:
        """Lo que sigue rojo despues de repuntuar tiene que explicarse solo."""
        line = _rules_status_line(
            (fake_audit_row(stage="seed_final_tick", stage_label="Seeds Final Tick",
                        stale=303, stale_verdicts=207, affected_verdicts=207),),
            "ICTRADING/STANDARD | toda la memoria",
        )

        self.assertIn("El repuntuado no llega a Seeds Final Tick", line)
        self.assertIn("hay que repetir la etapa", line)

    def test_nothing_found_says_so(self) -> None:
        line = _rules_status_line((fake_audit_row(),), "ICTRADING/REAL | run #7")

        self.assertIn("ningun veredicto se dio con otras reglas", line)


if __name__ == "__main__":
    unittest.main()
