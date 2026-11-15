"""Las cuatro pantallas Final Tick deben seguir leyendo las mismas reglas.

Si una pantalla vuelve a copiarse sus umbrales, su botonera o su causa, estos
tests fallan: ese es el punto de centralizarlas.
"""
from __future__ import annotations

import unittest

from ui.ubs_final_tick_6m_logic import UBSFinalTick6MLogicMixin
from ui.ubs_final_tick_6m_view import UBSFinalTick6MViewMixin
from ui.ubs_final_tick_logic import UBSFinalTickLogicMixin
from ui.ubs_final_tick_shared import (
    FINAL_TICK_THRESHOLD_FIELDS,
    final_tick_columns,
    is_six_month,
    stage_label,
    stage_suffix,
)
from ui.ubs_final_tick_view import UBSFinalTickViewMixin
from ui.ubs_seed_final_tick_6m_logic import UBSSeedFinalTick6MLogicMixin
from ui.ubs_seed_final_tick_6m_view import UBSSeedFinalTick6MViewMixin
from ui.ubs_seed_final_tick_logic import UBSSeedFinalTickLogicMixin
from ui.ubs_seed_final_tick_view import UBSSeedFinalTickViewMixin


SCREEN_MIXINS = (
    UBSFinalTickViewMixin,
    UBSFinalTick6MViewMixin,
    UBSSeedFinalTickViewMixin,
    UBSSeedFinalTick6MViewMixin,
)


class ThresholdVariableHarness:
    """Guarda que variable pide cada pantalla, sin construir widgets."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def __getattr__(self, name: str) -> str:
        if name.startswith("ubs_final_tick"):
            self.requested.append(name)
            return name
        raise AttributeError(name)


class StageNamingTests(unittest.TestCase):
    def test_six_month_is_recognized_however_it_is_written(self) -> None:
        for stage in ("six_month", "SIX-MONTH", "6m", " sixmonth "):
            with self.subTest(stage=stage):
                self.assertTrue(is_six_month(stage))
                self.assertEqual(stage_suffix(stage), "_6m")

    def test_probe_is_the_unsuffixed_stage(self) -> None:
        self.assertFalse(is_six_month("probe"))
        self.assertEqual(stage_suffix("probe"), "")
        self.assertEqual(stage_label("probe"), "Final Tick")
        self.assertEqual(stage_label("six_month"), "Final Tick 6M")


class SharedThresholdTests(unittest.TestCase):
    def test_every_screen_draws_the_same_thresholds_in_the_same_order(self) -> None:
        expected = [attribute for _label, attribute, _width in FINAL_TICK_THRESHOLD_FIELDS]
        for stage in ("probe", "six_month"):
            with self.subTest(stage=stage):
                harness = ThresholdVariableHarness()
                _dates, thresholds = UBSFinalTickViewMixin._final_tick_criteria_fields(
                    harness, stage,
                )
                self.assertEqual([value for _label, value, _width in thresholds], expected)

    def test_six_month_adds_its_retry_dates_and_probe_does_not(self) -> None:
        probe_dates, _ = UBSFinalTickViewMixin._final_tick_criteria_fields(
            ThresholdVariableHarness(), "probe",
        )
        six_month_dates, _ = UBSFinalTickViewMixin._final_tick_criteria_fields(
            ThresholdVariableHarness(), "six_month",
        )
        self.assertEqual([label for label, _var, _width in probe_dates], ["Desde", "Hasta"])
        self.assertEqual(
            [label for label, _var, _width in six_month_dates],
            ["Desde", "Hasta", "Ops bajas desde", "Ops bajas hasta"],
        )


class SharedTableTests(unittest.TestCase):
    def test_both_candidate_stages_share_every_column_but_the_verdict(self) -> None:
        probe = final_tick_columns("probe")
        six_month = final_tick_columns("six_month")
        self.assertEqual(
            [(key, width) for key, _heading, width in probe],
            [(key, width) for key, _heading, width in six_month],
        )
        verdicts = {
            stage: next(heading for key, heading, _w in columns if key == "status")
            for stage, columns in (("probe", probe), ("six_month", six_month))
        }
        self.assertEqual(verdicts, {"probe": "FINAL", "six_month": "6M"})

    def test_the_selection_checkbox_is_the_first_column_of_both_stages(self) -> None:
        for stage in ("probe", "six_month"):
            with self.subTest(stage=stage):
                self.assertEqual(final_tick_columns(stage)[0][:2], ("mark", "SEL"))


class SharedImplementationTests(unittest.TestCase):
    def test_the_6m_screens_only_delegate_with_their_stage(self) -> None:
        """Un metodo propio en una pantalla 6M seria una regla duplicada."""
        for mixin in (
            UBSFinalTick6MViewMixin,
            UBSFinalTick6MLogicMixin,
            UBSSeedFinalTick6MViewMixin,
            UBSSeedFinalTick6MLogicMixin,
        ):
            for name, member in vars(mixin).items():
                if name.startswith("__"):
                    continue
                with self.subTest(mixin=mixin.__name__, method=name):
                    self.assertIn(
                        "six_month", member.__code__.co_consts,
                        f"{mixin.__name__}.{name} no delega pasando su etapa",
                    )

    def test_the_four_screens_build_through_the_shared_mixin(self) -> None:
        from ui.ubs_final_tick_shared import UBSFinalTickSharedMixin

        for mixin in SCREEN_MIXINS:
            with self.subTest(mixin=mixin.__name__):
                self.assertTrue(
                    hasattr(mixin, "_final_tick_action_button")
                    or UBSFinalTickSharedMixin in mixin.__mro__
                    or mixin.__name__.endswith("6MViewMixin"),
                )
        for mixin in (UBSFinalTickViewMixin, UBSSeedFinalTickViewMixin):
            with self.subTest(mixin=mixin.__name__):
                self.assertIn(UBSFinalTickSharedMixin, mixin.__mro__)

    def test_both_candidate_stages_run_the_same_panel_refresh(self) -> None:
        for stage_method in (
            UBSFinalTickLogicMixin._refresh_ubs_final_tick_panel,
            UBSFinalTick6MLogicMixin._refresh_ubs_final_tick_6m_panel,
        ):
            with self.subTest(method=stage_method.__name__):
                self.assertIn(
                    "_refresh_final_tick_panel", stage_method.__code__.co_names,
                )


if __name__ == "__main__":
    unittest.main()
