"""La pantalla UBS Auditor y su cadena de repuntuados.

Lo que se vigila aqui: que ninguna regla declarada en `STAGE_SPECS` se quede
sin valor vigente en la interfaz (si no, la auditoria la ignoraria en
silencio), que el repuntuado apunte a la cuenta auditada y no a la activa, y
que la cola de etapas avance solo cuando el proceso anterior termina bien.

Tambien que el clic derecho sobre una fila lleve al arreglo de esa etapa, que
es el mismo del boton y no una segunda copia.

Lo que la pantalla pinta a partir de lo contado -las filas, sus cabeceras y
las lineas de resumen- se mira en `test_ubs_auditor_rows.py`.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.ubs.ui.auditor_fixtures import fake_finding
from ubs.stage_rules_audit import STAGE_SPECS
from ui.app_chrome import SIDEBAR_ITEMS
from ui.ubs_auditor_logic import UBSAuditorLogicMixin
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


class FakeVar:
    def __init__(self, value: object = "") -> None:
        self._value = value

    def get(self) -> object:
        return self._value

    def set(self, value: object) -> None:
        self._value = value


class AuditorHarness(UBSAuditorLogicMixin):
    """Auditor con variables, rutas y lanzamientos de pega."""

    def __init__(self) -> None:
        self.ubs_auditor_account = FakeVar("ICTRADING/REAL")
        self.ubs_auditor_run_id = FakeVar("#7 | 2026-11-01 10:00")
        self.ubs_auditor_status = FakeVar("")
        self.ubs_seed_pass_min_net_profit = FakeVar("0")
        self.ubs_seed_pass_min_profit_factor = FakeVar("1.20")
        self.ubs_seed_pass_min_trades = FakeVar(50)
        self.ubs_seed_pass_max_drawdown_pct = FakeVar("25")
        self.ubs_seed_pass_min_recovery_factor = FakeVar("1.0")
        self.ubs_pass_min_net_profit = FakeVar("100")
        self.ubs_pass_min_profit_factor = FakeVar("1.20")
        self.ubs_pass_min_trades = FakeVar(50)
        self.ubs_pass_max_drawdown_pct = FakeVar("25")
        self.ubs_pass_min_recovery_factor = FakeVar("1.0")
        self.ubs_robust_pass_min_net_profit = FakeVar("100")
        self.ubs_robust_pass_min_profit_factor = FakeVar("1.20")
        self.ubs_robust_pass_min_trades = FakeVar(50)
        self.ubs_robust_pass_max_drawdown_pct = FakeVar("25")
        self.ubs_robust_pass_min_recovery_factor = FakeVar("1.0")
        self.ubs_robust_from_date = FakeVar("2024.01.01")
        self.ubs_robust_to_date = FakeVar("2025.12.31")
        self.ubs_regression_positive_points = FakeVar("80")
        self.ubs_regression_negative_points = FakeVar("-100")
        self.ubs_regression_from_date = FakeVar("2017.01.01")
        self.ubs_regression_to_date = FakeVar("2019.12.31")
        self._ubs_auditor_rescore_queue: list[str] = []
        self.launched: list[list[str]] = []
        self.console: list[str] = []

    # Reglas y umbrales vigentes
    def _score_float(self, variable, label, *, minimum=None, maximum=None) -> float:
        return float(str(variable.get()).replace(",", "."))

    def _ubs_robust_bonus_values(self) -> tuple[float, float]:
        return 70.0, -70.0

    def _ubs_final_tick_threshold_values(self) -> dict[str, float]:
        return {
            "min_quality": 80.0,
            "min_ohlc_trades": 5,
            "min_trades_w1": 2,
            "min_trades_mn": 1,
            "net_delta": 35.0,
            "pf_delta": 35.0,
            "dd_delta": 35.0,
            "trades_delta": 35.0,
            "dd_gap_pp": 3.0,
        }

    def _final_tick_stage_dates(self, final_tick_stage: str = "probe"):
        if final_tick_stage == "six_month":
            return "2026.01.01", "2026.06.30", "", ""
        return "2026.05.01", "2026.05.31", "", ""

    def _ubs_regression_threshold_values(self) -> dict[str, object]:
        return {
            "net": 0.0, "pf": 1.10, "trades": 36, "trades_w1": 12, "trades_mn": 4,
            "dd": 30.0, "recovery": 0.75, "months": 0.50, "pf_efficiency": 0.50,
            "dd_ratio": 2.0, "positive": 80.0, "negative": -100.0,
        }

    # Contexto de cuenta y rutas
    def _parse_ubs_account_context(self, value):
        text = str(value or "")
        if "/" not in text:
            return None
        broker, account = text.split("/", 1)
        return broker.strip().upper(), account.strip().upper()

    def _ubs_generator_source_dir(self):
        return "sets"

    def _ubs_generation_output_dir(self):
        return "outputs"

    def _ubs_seed_score_args(self) -> list[str]:
        return ["--min-trades", "50"]

    def _ubs_score_args(self) -> list[str]:
        return ["--min-trades", "50", "--robust-min-net-retention", "0.50"]

    def _ubs_robust_score_args(self) -> list[str]:
        return ["--min-trades", "50"]

    def _effective_ubs_symbol_map_text(self) -> str:
        return ""

    def _effective_symbol_suffix_args(self) -> list[str]:
        return []

    # Proceso
    def _run_script(self, script_name: str, args: list[str]) -> None:
        self.launched.append(list(args))

    def _append_console(self, text: str, tag: str = "") -> None:
        self.console.append(text)

    def after(self, _delay, callback):
        callback()


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


class _QueueHarness(AuditorHarness):
    """Auditor cuya etapa apunta los runs que le mandan en vez de correrlos.

    `runs_that_do_nothing` son los que el runner deja pasar -el .set ya no
    esta, el estado no es de los suyos-: no dejan proceso, asi que de ellos no
    cuelga el siguiente de la cola.
    """

    def __init__(self, *, runs_that_do_nothing=()) -> None:
        super().__init__()
        self.started: list[int] = []
        self._skip = set(runs_that_do_nothing)

    def _run_ubs_auditor_pending(self, stage_key: str, run_id: int) -> bool:
        self.started.append(run_id)
        return run_id not in self._skip


class PendingQueueTests(unittest.TestCase):
    """La cola de runs del arreglo de pendientes."""

    @staticmethod
    def _harness(queue, **kwargs) -> _QueueHarness:
        harness = _QueueHarness(**kwargs)
        harness._ubs_auditor_pending_stage = "final_tick_6m"
        harness._ubs_auditor_pending_queue = list(queue)
        return harness

    def test_the_queue_advances_one_run_per_process(self) -> None:
        harness = self._harness([7, 8, 9])

        self.assertTrue(harness._start_next_ubs_auditor_pending())

        self.assertEqual(harness.started, [7])
        self.assertEqual(harness._ubs_auditor_pending_queue, [8, 9])

        harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 0)

        self.assertEqual(harness.started, [7, 8])
        self.assertEqual(harness._ubs_auditor_pending_queue, [9])

    def test_a_run_the_runner_skips_does_not_stall_the_queue(self) -> None:
        """Sin proceso no hay final al que colgar el siguiente."""
        harness = self._harness([7, 8, 9], runs_that_do_nothing=(7, 8))

        self.assertTrue(harness._start_next_ubs_auditor_pending())

        self.assertEqual(harness.started, [7, 8, 9])
        self.assertEqual(harness._ubs_auditor_pending_queue, [])

    def test_a_queue_nobody_runs_ends_saying_so(self) -> None:
        harness = self._harness([7, 8], runs_that_do_nothing=(7, 8))

        self.assertFalse(harness._start_next_ubs_auditor_pending())

        self.assertIn("2 run(s) sin nada", harness.ubs_auditor_status.get())

    def test_a_bad_exit_cancels_what_was_left(self) -> None:
        harness = self._harness([7, 8])

        started = harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 2)

        self.assertFalse(started)
        self.assertEqual(harness.started, [])
        self.assertEqual(harness._ubs_auditor_pending_queue, [])
        self.assertTrue(any("cancelada" in line for line in harness.console))

    def test_an_empty_queue_lets_the_other_chains_answer(self) -> None:
        harness = self._harness([])

        self.assertFalse(harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 0))
