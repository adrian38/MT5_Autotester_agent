"""El veredicto dado sobre una ventana que no existe vuelve a pendiente."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubs.agent.final_tick.rescore import _rescore_final_tick_row
from ubs.score import ScoreConfig


class _RecordingMemory:
    """Anota lo que el repuntuado escribe, que es lo unico que se comprueba."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record_candidate_final_tick(self, *args, **kwargs) -> None:
        self.calls.append(args)


def _args(from_date: str = "2026.01.01", to_date: str = "2026.06.30") -> SimpleNamespace:
    return SimpleNamespace(
        broker="ICTRADING",
        symbol_map="",
        symbol_suffix="",
        from_date=from_date,
        to_date=to_date,
        final_tick_min_history_quality=80.0,
        final_tick_min_trades_w1=2,
        final_tick_min_trades_mn=0,
        final_tick_max_net_delta_pct=35.0,
        final_tick_max_pf_delta_pct=35.0,
        final_tick_max_dd_delta_pct=35.0,
        final_tick_max_dd_gap_pp=3.0,
        final_tick_max_trades_delta_pct=35.0,
    )


def _row(ohlc_report: str, *, from_date: str, to_date: str) -> dict:
    return {
        "id": 42,
        "run_id": 7,
        "ft_run_id": 7,
        "ft_ohlc_report_path": ohlc_report,
        "ft_real_tick_report_path": "",
        "ft_from_date": from_date,
        "ft_to_date": to_date,
    }


class RescoreWindowTests(unittest.TestCase):
    def test_the_mt5_zero_sends_the_row_back_to_pending(self) -> None:
        """1970.01.01 es un backtest que no produjo nada: no hay que juzgar.

        El reporte ni siquiera esta en disco -quedo apuntando al de otra
        maquina-, asi que la ventana sale de lo que guardo la fila, y lo que
        guardo no es una ventana.
        """
        memory = _RecordingMemory()
        status_counts: dict[str, int] = {}

        handled = _rescore_final_tick_row(
            _args(), memory, ScoreConfig(), {},
            _row("G:/no/existe/ohlc.htm", from_date="1970.01.01", to_date="1970.01.01"),
            status_counts,
        )

        self.assertTrue(handled)
        self.assertEqual(status_counts, {"no_report": 1})
        # candidate_id, run_id, status: `no_report` es reintentable, asi que la
        # fila sale del rojo de la auditoria y la recoge Arreglar pendientes.
        self.assertEqual(memory.calls[0][:3], (42, 7, "no_report"))

    def test_a_report_without_a_period_also_goes_back(self) -> None:
        """El reporte si esta, pero no trae periodo: manda el, y no dice nada."""
        memory = _RecordingMemory()
        status_counts: dict[str, int] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            ohlc_report = Path(temp_dir) / "ohlc.htm"
            ohlc_report.touch()
            with patch(
                "ubs.agent.final_tick.rescore._read_ohlc_report_cfg_dates",
                return_value=("1970.01.01", "1970.01.01"),
            ):
                handled = _rescore_final_tick_row(
                    _args(), memory, ScoreConfig(), {},
                    _row(str(ohlc_report), from_date="2026.01.01", to_date="2026.06.30"),
                    status_counts,
                )

        self.assertTrue(handled)
        self.assertEqual(status_counts, {"no_report": 1})

    def test_a_row_with_a_window_and_no_report_is_left_alone(self) -> None:
        """Sin reporte no hay nada que repuntuar, pero su ventana es buena.

        Se cuenta como omitida y conserva su veredicto: lo roto es que el
        reporte no esta aqui, no el juicio que se dio sobre el.
        """
        memory = _RecordingMemory()
        status_counts: dict[str, int] = {}

        handled = _rescore_final_tick_row(
            _args(), memory, ScoreConfig(), {},
            _row("G:/no/existe/ohlc.htm", from_date="2026.01.01", to_date="2026.06.30"),
            status_counts,
        )

        self.assertFalse(handled)
        self.assertEqual(status_counts, {})
        self.assertEqual(memory.calls, [])


if __name__ == "__main__":
    unittest.main()
