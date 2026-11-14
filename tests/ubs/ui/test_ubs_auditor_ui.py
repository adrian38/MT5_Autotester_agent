"""La pantalla UBS Auditor y su cadena de repuntuados.

Lo que se vigila aqui: que ninguna regla declarada en `STAGE_SPECS` se quede
sin valor vigente en la interfaz (si no, la auditoria la ignoraria en
silencio), que el repuntuado apunte a la cuenta auditada y no a la activa, y
que la cola de etapas avance solo cuando el proceso anterior termina bien.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests.ubs.scoring.stage_audit_fixtures import (
    RULE_CHANGE_NOTE,
    RULE_CHANGED_AT,
    declared_rule_change,
)
from ubs.stage_rules_audit import STAGE_SPECS
from ui.app_chrome import SIDEBAR_ITEMS
from ui.ubs_auditor_logic import UBSAuditorLogicMixin
from ui.ubs_auditor_queries import (
    MAX_STORED_VALUES_SHOWN,
    _blind_rules_note,
    _of_total,
    _pending_group,
    _pending_headline,
    _rule_group,
    _rules_status_line,
    _stage_headline,
    _stored_cell,
)
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
                {rule.key for rule in spec.rules},
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
            stage="robustness", findings=(_finding(verdicts=4),),
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
    def _audit(*, moved=0, undated=0, undated_verdicts=0, stale=0, stale_verdicts=None):
        return SimpleNamespace(
            findings=tuple(_finding(verdicts=1) for _ in range(moved)),
            undated=undated,
            undated_verdicts=undated_verdicts,
            stale=stale,
            stale_verdicts=stale if stale_verdicts is None else stale_verdicts,
        )

    def test_moved_rules_win_the_headline(self) -> None:
        self.assertEqual(_stage_headline(self._audit(moved=2)), "2 reglas movidas")
        self.assertEqual(_stage_headline(self._audit(moved=1)), "1 regla movida")

    def test_a_moved_rule_nobody_judged_is_not_a_moved_rule_yet(self) -> None:
        """Alcanza solo a pendientes: se juzgaran con el valor de ahora."""
        audit = self._audit()
        audit.findings = (_finding(verdicts=0),)

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


def _finding(**overrides):
    """Un hallazgo de pega: una regla movida con sus filas y sus veredictos."""
    values = {
        "rule": "max_pf_delta_pct", "rule_label": "Delta PF %", "current": "30",
        "stored": (("35", 4),), "rows": 4, "verdicts": 4, "fix": "rescore",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _audit_row(**overrides):
    """Una etapa auditada de pega, con todo a cero salvo lo que pida la prueba."""
    values = {
        "stage": "final_tick",
        "stage_label": "Final Tick",
        "rows": 10,
        "affected": 0,
        "affected_verdicts": 0,
        "findings": (),
        "stale": 0,
        "stale_verdicts": 0,
        "undated": 0,
        "undated_verdicts": 0,
        "undated_stored": (),
        "unknown": 0,
        "missing_table": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _pending_row(**overrides):
    values = {
        "stage": "final_tick",
        "stage_label": "Final Tick",
        "rows": 10,
        "pending": 0,
        "missing": 0,
        "by_status": (),
        "missing_table": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RuleGroupTests(unittest.TestCase):
    def test_a_changed_rule_hangs_off_the_stage_in_red(self) -> None:
        with declared_rule_change("final_tick"):
            _stage, _label, values, tag, children = _rule_group(
                _audit_row(stale=4, stale_verdicts=3, affected=4, affected_verdicts=3)
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

    def test_a_changed_rule_nobody_judged_hangs_muted(self) -> None:
        """Lo que no tiene veredicto no arrastra: se dice, pero no en rojo."""
        _stage, _label, values, tag, children = _rule_group(_audit_row(stale=1046))

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(values[4], "0 de 10 candidatos")
        self.assertEqual(children[0][1], "pending")

    def test_a_finding_the_button_cannot_fix_says_so(self) -> None:
        """La ventana movida no se repuntua: la fila corrio sobre otras fechas."""
        window = SimpleNamespace(
            rule="from_date", rule_label="Desde", current="2025.06.30",
            stored=(("2026.01.01", 2396),), rows=2404, verdicts=2404, fix="repeat",
        )
        _stage, _label, _values, _tag, children = _rule_group(
            _audit_row(findings=(window,), affected=2404, affected_verdicts=2404)
        )

        self.assertEqual(children[0][0][0], "Hay que repetir")
        self.assertEqual(children[0][0][1], "Desde")

    def test_the_invalid_dates_name_themselves_with_their_counts(self) -> None:
        _stage, _label, _values, _tag, children = _rule_group(
            _audit_row(undated=3, undated_verdicts=1,
                       undated_stored=(("(vacio)", 2), ("1970.01.01", 1)))
        )

        self.assertEqual(children[0][0][3], "(vacio) (2), 1970.01.01 (1)")

    def test_a_stage_with_nothing_wrong_stays_green_and_closed(self) -> None:
        _stage, _label, values, tag, children = _rule_group(_audit_row())

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(children, ())


class PendingGroupTests(unittest.TestCase):
    def test_the_two_gaps_are_separate_children(self) -> None:
        _stage, _label, values, tag, children = _pending_group(
            _pending_row(rows=10, pending=2, missing=3, by_status=(("pending_ohlc", 2),))
        )

        self.assertEqual(tag, "rejected")
        self.assertEqual(values[0], "A medias y sin correr")
        # El total cuenta tambien a los que no tienen fila: 2 + 3 de 10 + 3.
        self.assertEqual(values[4], "5 de 13 candidatos")
        self.assertEqual([child[0][0] for child in children], ["Se quedaron a medias", "Sin fila todavia"])
        self.assertEqual(children[0][0][4], "2 de 13 candidatos")
        self.assertEqual(children[1][0][4], "3 de 13 candidatos")

    def test_an_eligible_without_a_row_alone_still_opens_the_stage(self) -> None:
        """Antes esta etapa salia «Al dia» porque sin fila no hay estado."""
        _stage, _label, values, tag, children = _pending_group(
            _pending_row(rows=0, pending=0, missing=4)
        )

        self.assertEqual(tag, "rejected")
        self.assertEqual(values[0], "Sin correr")
        self.assertEqual(values[4], "4 de 4 candidatos")
        self.assertEqual(len(children), 1)

    def test_a_stage_with_both_gaps_closed_is_up_to_date(self) -> None:
        _stage, _label, values, tag, children = _pending_group(_pending_row())

        self.assertEqual(tag, "accepted")
        self.assertEqual(values[0], "Al dia")
        self.assertEqual(children, ())

    def test_each_gap_names_itself_in_the_headline(self) -> None:
        self.assertEqual(_pending_headline(_pending_row(pending=1)), "Se quedaron a medias")
        self.assertEqual(_pending_headline(_pending_row(missing=1)), "Sin correr")


class StatusLineTests(unittest.TestCase):
    def test_a_changed_rule_is_said_apart_from_a_moved_one(self) -> None:
        line = _rules_status_line(
            (_audit_row(stale=4, stale_verdicts=3, affected_verdicts=3),),
            "ICTRADING/REAL | run #7",
        )

        # Cuenta veredictos: son los unicos que el repuntuado puede cambiar.
        self.assertIn("1 etapa(s) con la regla cambiada (3 veredicto(s))", line)
        self.assertNotIn("regla(s) movida(s)", line)
        self.assertIn("Afecta a 3 veredicto(s)", line)

    def test_what_the_rescore_cannot_reach_is_said_in_the_same_line(self) -> None:
        """Lo que sigue rojo despues de repuntuar tiene que explicarse solo."""
        line = _rules_status_line(
            (_audit_row(stage="seed_final_tick", stage_label="Seeds Final Tick",
                        stale=303, stale_verdicts=207, affected_verdicts=207),),
            "ICTRADING/STANDARD | toda la memoria",
        )

        self.assertIn("El repuntuado no llega a Seeds Final Tick", line)
        self.assertIn("hay que repetir la etapa", line)

    def test_nothing_found_says_so(self) -> None:
        line = _rules_status_line((_audit_row(),), "ICTRADING/REAL | run #7")

        self.assertIn("ningun veredicto se dio con otras reglas", line)


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


if __name__ == "__main__":
    unittest.main()
