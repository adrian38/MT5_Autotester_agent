"""Los umbrales que la pantalla ensena son los que juzgan al correr."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from ui.ubs_final_tick_run import UBSFinalTickRunMixin

THRESHOLDS = {
    "min_quality": 80.0,
    "min_ohlc_trades": 4,
    "min_trades_w1": 2,
    "min_trades_mn": 0,
    "net_delta": 35.0,
    "pf_delta": 35.0,
    "dd_delta": 35.0,
    "trades_delta": 35.0,
    "dd_gap_pp": 7.5,
}

# Cada umbral de Configuracion con la bandera por la que viaja hasta el agente.
# Es la tabla que faltaba: `dd_gap_pp` se editaba, se guardaba en la fila y se
# auditaba, pero no se pasaba, asi que al correr decidia el 3.0 por defecto del
# agente y al repuntuar decidia el de Configuracion.
THRESHOLD_FLAGS = {
    "min_quality": "--final-tick-min-history-quality",
    "min_ohlc_trades": "--final-tick-min-ohlc-trades",
    "min_trades_w1": "--final-tick-min-trades-w1",
    "min_trades_mn": "--final-tick-min-trades-mn",
    "net_delta": "--final-tick-max-net-delta-pct",
    "pf_delta": "--final-tick-max-pf-delta-pct",
    "dd_delta": "--final-tick-max-dd-delta-pct",
    "trades_delta": "--final-tick-max-trades-delta-pct",
    "dd_gap_pp": "--final-tick-max-dd-gap-pp",
}


class _RunHarness(UBSFinalTickRunMixin):
    """Lo justo para construir la linea de comandos, sin tkinter detras."""

    def __init__(self) -> None:
        self.template_path = SimpleNamespace(get=lambda: "tester_template.ini")
        self.delay = SimpleNamespace(get=lambda: 0)
        self.multiterminal_enabled = SimpleNamespace(get=lambda: False)
        self.mt5_path = SimpleNamespace(get=lambda: "")
        self.mt5_data_root = SimpleNamespace(get=lambda: "")

    def _final_tick_stage_dates(self, _stage: str) -> tuple[str, str, str, str]:
        return ("2026.05.01", "2026.05.31", "", "")

    def _ubs_generation_output_dir(self) -> Path:
        return Path("outputs")

    def _ubs_generator_source_dir(self) -> Path:
        return Path("sets")

    def _ubs_memory_path(self) -> Path:
        return Path("memory.sqlite")

    def _ubs_broker(self) -> str:
        return "ICTRADING"

    def _ubs_account_type(self) -> str:
        return "STANDARD"

    def _ubs_final_tick_threshold_values(self) -> dict:
        return dict(THRESHOLDS)

    def _required_ubs_ex5_file(self) -> str:
        return "Ultimate Breakout System.ex5"

    def _effective_ubs_symbol_map_text(self) -> str:
        return ""

    def _effective_symbol_suffix_args(self) -> list[str]:
        return []


class FinalTickRunArgsTests(unittest.TestCase):
    def test_every_threshold_reaches_the_agent(self) -> None:
        """Un umbral que no viaja no decide nada, y nadie lo ve desde la pantalla."""
        args = _RunHarness()._ubs_final_tick_args(7)

        for key, flag in THRESHOLD_FLAGS.items():
            with self.subTest(flag=flag):
                self.assertIn(flag, args)
                self.assertEqual(args[args.index(flag) + 1], str(THRESHOLDS[key]))


if __name__ == "__main__":
    unittest.main()
