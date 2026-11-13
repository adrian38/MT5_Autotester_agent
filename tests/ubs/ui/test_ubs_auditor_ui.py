"""La pantalla UBS Auditor y su cadena de repuntuados.

Lo que se vigila aqui: que ninguna regla declarada en `STAGE_SPECS` se quede
sin valor vigente en la interfaz (si no, la auditoria la ignoraria en
silencio), que el repuntuado apunte a la cuenta auditada y no a la activa, y
que la cola de etapas avance solo cuando el proceso anterior termina bien.
"""
from __future__ import annotations

import unittest

from ubs.stage_rules_audit import STAGE_SPECS
from ui.app_chrome import SIDEBAR_ITEMS
from ui.ubs_auditor_logic import UBSAuditorLogicMixin


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


class NavigationTests(unittest.TestCase):
    def test_the_screen_has_its_own_sidebar_entry(self) -> None:
        keys = [key for key, _label in SIDEBAR_ITEMS]

        self.assertIn("ubs_auditor", keys)


if __name__ == "__main__":
    unittest.main()
