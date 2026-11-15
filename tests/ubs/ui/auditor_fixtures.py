"""Lo de pega del auditor: sus filas y el propio auditor sin ventana.

Las filas -un hallazgo, una etapa, una pendiente- y `AuditorHarness`, que es
la pantalla con variables, rutas y lanzamientos de pega, para poder probarla
sin abrir Tk.

Lo comparten las tres hojas de pruebas de la pantalla -lo que hace, lo que
pinta y el arreglo de pendientes-, que salieron de un mismo fichero cada vez
que se paso de las 600 lineas.
"""
from __future__ import annotations

from types import SimpleNamespace

from ui.ubs_auditor_logic import UBSAuditorLogicMixin


def fake_finding(**overrides):
    """Un hallazgo de pega: una regla movida con sus filas y sus veredictos."""
    values = {
        "rule": "max_pf_delta_pct", "rule_label": "Delta PF %", "current": "30",
        "stored": (("35", 4),), "rows": 4, "verdicts": 4, "fix": "rescore",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_audit_row(**overrides):
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
        "offwindow": 0,
        "offwindow_verdicts": 0,
        "window_findings": (),
        "unknown": 0,
        "missing_table": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_pending_row(**overrides):
    values = {
        "stage": "final_tick",
        "stage_label": "Final Tick",
        "rows": 10,
        "pending": 0,
        "missing": 0,
        "by_status": (),
        "by_run_status": (),
        "missing_table": False,
        "settled": 0,
        "settled_by_status": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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

