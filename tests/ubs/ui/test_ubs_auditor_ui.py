"""La pantalla UBS Auditor y su cadena de repuntuados.

Lo que se vigila aqui: que ninguna regla declarada en `STAGE_SPECS` se quede
sin valor vigente en la interfaz (si no, la auditoria la ignoraria en
silencio), que el repuntuado apunte a la cuenta auditada y no a la activa, y
que la cola de etapas avance solo cuando el proceso anterior termina bien.

Tambien que el clic derecho sobre una fila lleve al arreglo de esa etapa, que
es el mismo del boton y no una segunda copia.

Lo que la pantalla pinta a partir de lo contado -las filas, sus cabeceras y
las lineas de resumen- se mira en `test_ubs_auditor_rows.py`, y el arreglo de
pendientes -a que etapas llega y como avanza su cola- en
`test_ubs_auditor_pending.py`.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.ubs.ui.auditor_fixtures import AuditorHarness, fake_finding
from ubs.stage_rules_audit import STAGE_SPECS
from ui.app_chrome import SIDEBAR_ITEMS
from ui.ubs_auditor_view import (
    AUDITOR_COLUMNS,
    AUDITOR_PENDING_LAYOUT,
    AUDITOR_RULE_LAYOUT,
    AUDITOR_STAGE_COLUMN_WIDTH,
    AUDITOR_TEXT_WRAPLENGTH,
)


# Ancho util de la tabla con la ventana en su tamano de siempre, descontados el
# margen de la tarjeta y la barra vertical. Es el presupuesto que no se puede
# pasar: la columna ETAPA es la primera y se va con la barra horizontal, asi
# que una tabla mas ancha que esto nace escondiendo lo que se viene a mirar.
AUDITOR_TABLE_BUDGET = 930



class CurrentRulesTests(unittest.TestCase):
    def test_every_audited_rule_has_a_value_in_the_interface(self) -> None:
        current = AuditorHarness()._ubs_auditor_current_rules()

        for spec in STAGE_SPECS:
            self.assertIn(spec.key, current, spec.label)
            self.assertEqual(
                {rule.key for rule in (*spec.rules, *spec.window)},
                set(current[spec.key]),
                f"Reglas sin valor vigente en {spec.label}",
            )

    def test_six_month_stage_audits_its_own_dates(self) -> None:
        current = AuditorHarness()._ubs_auditor_current_rules()

        self.assertEqual(current["final_tick"]["from_date"], "2026.05.01")
        self.assertEqual(current["final_tick_6m"]["from_date"], "2026.01.01")


class RescoreArgsTests(unittest.TestCase):
    def test_args_point_at_the_audited_account(self) -> None:
        harness = AuditorHarness()

        args = harness._ubs_auditor_rescore_args("candidates")

        self.assertIn("--rescore-candidates-only", args)
        self.assertEqual(args[args.index("--broker") + 1], "ICTRADING")
        self.assertEqual(args[args.index("--account-type") + 1], "REAL")
        memory = args[args.index("--memory") + 1]
        self.assertIn("ICTRADING", memory.upper())

    def test_six_month_rescore_carries_its_stage_and_dates(self) -> None:
        harness = AuditorHarness()

        args = harness._ubs_auditor_rescore_args("final_tick_6m")

        self.assertIn("--rescore-final-tick-only", args)
        self.assertEqual(args[args.index("--final-tick-stage") + 1], "six_month")
        self.assertEqual(args[args.index("--from-date") + 1], "2026.01.01")
        self.assertEqual(args[args.index("--final-tick-max-dd-gap-pp") + 1], "3.0")

    def test_stage_without_rescore_mode_is_refused(self) -> None:
        harness = AuditorHarness()

        with self.assertRaises(ValueError):
            harness._ubs_auditor_rescore_args("seed_final_tick")


class StagesToRescoreTests(unittest.TestCase):
    """Que etapas entran al pulsar el boton, con y sin etapa marcada."""

    @staticmethod
    def _harness(selected: str) -> AuditorHarness:
        harness = AuditorHarness()
        harness._selected_ubs_auditor_stage = lambda: selected
        return harness

    def test_a_marked_stage_wins(self) -> None:
        """Marcarla es como se pide lo que la auditoria no puede ver."""
        harness = self._harness("final_tick")

        stages, why = harness._ubs_auditor_stages_to_rescore(())

        self.assertEqual(stages, ["final_tick"])
        self.assertEqual(why, "marcada a mano")

    def test_without_a_mark_only_where_the_rule_changed(self) -> None:
        harness = self._harness("")
        audit = SimpleNamespace(
            stage="robustness", findings=(fake_finding(verdicts=4),),
            stale_verdicts=0, undated_verdicts=0,
        )

        stages, why = harness._ubs_auditor_stages_to_rescore((audit,))

        self.assertEqual(stages, ["robustness"])
        self.assertEqual(why, "donde cambio la regla")

    def test_a_stage_where_nothing_changed_is_left_alone(self) -> None:
        """Repuntuarla seria reescribir cada veredicto con el valor que tenia."""
        harness = self._harness("")

        stages, _why = harness._ubs_auditor_stages_to_rescore(())

        self.assertEqual(stages, [])


class RescoreQueueTests(unittest.TestCase):
    def test_next_stage_runs_when_the_previous_one_succeeds(self) -> None:
        harness = AuditorHarness()
        harness._ubs_auditor_rescore_queue = ["robustness", "regression"]

        started = harness._maybe_auto_run_ubs_auditor_rescore("ubs_agent.py", [], 0)

        self.assertTrue(started)
        self.assertEqual(harness._ubs_auditor_rescore_queue, ["regression"])
        self.assertIn("--rescore-robustness-only", harness.launched[0])

    def test_queue_is_dropped_when_the_previous_one_fails(self) -> None:
        harness = AuditorHarness()
        harness._ubs_auditor_rescore_queue = ["robustness"]

        started = harness._maybe_auto_run_ubs_auditor_rescore("ubs_agent.py", [], 2)

        self.assertFalse(started)
        self.assertEqual(harness._ubs_auditor_rescore_queue, [])
        self.assertEqual(harness.launched, [])

    def test_nothing_happens_without_a_queue(self) -> None:
        harness = AuditorHarness()

        self.assertFalse(harness._maybe_auto_run_ubs_auditor_rescore("ubs_agent.py", [], 0))


class TableLayoutTests(unittest.TestCase):
    def test_every_layout_shows_declared_columns(self) -> None:
        for layout in (AUDITOR_RULE_LAYOUT, AUDITOR_PENDING_LAYOUT):
            for column, _heading, _width, _stretch in layout:
                self.assertIn(column, AUDITOR_COLUMNS)

    def test_no_layout_pushes_the_stage_column_off_screen(self) -> None:
        for layout in (AUDITOR_RULE_LAYOUT, AUDITOR_PENDING_LAYOUT):
            total = AUDITOR_STAGE_COLUMN_WIDTH + sum(width for _c, _h, width, _s in layout)
            self.assertLessEqual(total, AUDITOR_TABLE_BUDGET, layout[0][1])

    def test_the_lines_of_text_wrap_inside_the_panel(self) -> None:
        """El resumen crece con lo que encuentre y tiene que caber envuelto."""
        self.assertLessEqual(AUDITOR_TEXT_WRAPLENGTH, AUDITOR_TABLE_BUDGET)

    def test_each_layout_has_one_column_that_takes_the_slack(self) -> None:
        for layout in (AUDITOR_RULE_LAYOUT, AUDITOR_PENDING_LAYOUT):
            stretching = [column for column, _h, _w, stretch in layout if stretch]
            self.assertEqual(len(stretching), 1, layout[0][1])


class NavigationTests(unittest.TestCase):
    def test_the_screen_has_its_own_sidebar_entry(self) -> None:
        keys = [key for key, _label in SIDEBAR_ITEMS]

        self.assertIn("ubs_auditor", keys)


class FakeMenu:
    """Un menu que apunta lo que se le cuelga en vez de abrirse."""

    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []
        self.popped_at: tuple[int, int] | None = None
        self.released = False

    def add_command(self, *, label: str, command) -> None:
        self.items.append((label, command))

    def tk_popup(self, x_root: int, y_root: int) -> None:
        self.popped_at = (x_root, y_root)

    def grab_release(self) -> None:
        self.released = True


class FakeTree:
    """Lo justo del Treeview que el menu toca: que fila hay bajo el raton.

    Y de quien cuelga: el menu solo se abre sobre la fila de la etapa, asi que
    tiene que poder distinguirla de su detalle.
    """

    def __init__(self, row_at: dict[int, str], parents: dict[str, str] | None = None) -> None:
        self._row_at = row_at
        self._parents = parents or {}
        self.selected: list[str] = []
        self.focused = ""

    def identify_row(self, y: int) -> str:
        return self._row_at.get(y, "")

    def parent(self, item: str) -> str:
        return self._parents.get(item, "")

    def selection_set(self, item: str) -> None:
        self.selected.append(item)

    def focus(self, item: str) -> None:
        self.focused = item


def _menu_harness(row_at: dict[int, str], row_stage: dict[str, str], parents=None):
    harness = AuditorHarness()
    harness.ubs_auditor_tree = FakeTree(row_at, parents)
    harness.ubs_auditor_row_stage = row_stage
    menu = FakeMenu()
    harness._new_ubs_auditor_menu = lambda: menu
    harness.fixed = []
    harness._fix_ubs_pending_states_clicked = lambda: harness.fixed.append("fix")
    return harness, menu


class RowMenuTests(unittest.TestCase):
    def test_the_row_under_the_pointer_becomes_the_marked_stage(self) -> None:
        """Pulsar la fila es marcarla: el arreglo es el de siempre."""
        harness, menu = _menu_harness({40: "I002"}, {"I002": "robustness"})

        harness._ubs_auditor_row_menu(SimpleNamespace(y=40, x_root=300, y_root=200))

        self.assertEqual(harness.ubs_auditor_tree.selected, ["I002"])
        self.assertEqual(harness.ubs_auditor_tree.focused, "I002")
        self.assertEqual(menu.popped_at, (300, 200))
        self.assertTrue(menu.released)

    def test_the_entry_names_the_stage_and_launches_its_fix(self) -> None:
        harness, menu = _menu_harness({40: "I002"}, {"I002": "final_tick_6m"})

        harness._ubs_auditor_row_menu(SimpleNamespace(y=40, x_root=0, y_root=0))

        self.assertEqual(len(menu.items), 1)
        label, command = menu.items[0]
        self.assertEqual(label, "Arreglar pendientes de Final Tick 6M")
        command()
        self.assertEqual(harness.fixed, ["fix"])

    def test_a_detail_row_opens_nothing(self) -> None:
        """La hija es el detalle de lo encontrado, no algo que arreglar aparte.

        El arreglo es siempre de la etapa entera, asi que colgarlo de una hija
        prometia una precision que no existe.
        """
        harness, menu = _menu_harness(
            {60: "I003"},
            {"I002": "regression", "I003": "regression"},
            parents={"I003": "I002"},
        )

        harness._ubs_auditor_row_menu(SimpleNamespace(y=60, x_root=0, y_root=0))

        self.assertEqual(menu.items, [])
        self.assertIsNone(menu.popped_at)
        self.assertEqual(harness.ubs_auditor_tree.selected, [])

    def test_clicking_below_the_rows_opens_nothing(self) -> None:
        harness, menu = _menu_harness({}, {"I002": "robustness"})

        harness._ubs_auditor_row_menu(SimpleNamespace(y=900, x_root=0, y_root=0))

        self.assertEqual(menu.items, [])
        self.assertIsNone(menu.popped_at)


if __name__ == "__main__":
    unittest.main()


