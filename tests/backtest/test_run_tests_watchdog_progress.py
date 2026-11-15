"""Una pasada que avanza no puede morir por parecer callada.

La madrugada del 15 de noviembre de 2026 el auditor de IC perdio
AUDJPY_M1_GOLD_Goldtrade_Pro_d6094da6_g002_s001_v005: Model=1, M1, de 2020 a
2024. El journal del tester se quedo en 'testing of Experts\\...' a las
02:35:27 y no volvio a escribir nada, asi que el detector la mato a los 920s,
la reintento y la volvio a matar en el mismo punto. El journal del terminal
contaba la otra historia: processing 10 % a las 02:40, 20 % a las 02:45 y 31 %
a las 02:50, 38 segundos antes de la primera muerte. La pasada necesitaba unos
48 minutos y nunca llego a dejar reporte.
"""
import tempfile
import unittest
from pathlib import Path

from backtest.base import TESTER_PROGRESS_STALL_FLOOR_SECONDS
from backtest import watchdog as run_tests_watchdog


def _journal_lines(*entries: str) -> str:
    return "".join(f"{entry}\n" for entry in entries)


class _Logger:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _ExitedProcess:
    """MT5 ya cerrado: terminate_process_tree no tiene a quien matar."""

    returncode = 1

    def poll(self):
        return 1

    def wait(self, timeout=None):
        return 1


class TerminalProgressLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.journal = Path(self.temporary.name) / "20261115.log"

    def _write(self, text: str) -> None:
        self.journal.write_bytes(text.encode("utf-16-le"))

    def test_the_last_heartbeat_wins(self):
        self._write(_journal_lines(
            "QH\t0\t02:40:10.242\tAutoTesting\tprocessing 10 %",
            "RN\t0\t02:45:10.335\tAutoTesting\tprocessing 20 %",
            "ED\t0\t02:50:10.428\tAutoTesting\tprocessing 31 %",
        ))
        line = run_tests_watchdog._terminal_progress_line(self.journal)
        self.assertIn("31 %", line)
        self.assertEqual(run_tests_watchdog._terminal_progress_percent(line), "31%")

    def test_reconnecting_to_the_broker_is_not_progress(self):
        # El terminal reconecta solo cada pocos minutos aunque este colgado:
        # contar cualquier linea nueva como progreso taparia atascos reales.
        self._write(_journal_lines(
            "GF\t1\t02:35:55.759\tNetwork\t'11637157': connection to CapitalPointTrading-MT5-4 lost",
            "HM\t0\t02:35:56.862\tNetwork\t'11637157': authorized on CapitalPointTrading-MT5-4",
            "KD\t0\t02:35:09.823\tTester\tautomatic testing started",
        ))
        self.assertEqual(run_tests_watchdog._terminal_progress_line(self.journal), "")

    def test_without_journal_there_is_no_heartbeat(self):
        self.assertEqual(run_tests_watchdog._terminal_progress_line(None), "")


class TerminalHeartbeatTests(unittest.TestCase):
    """El latido del terminal como senal de progreso del detector generico."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"
        (self.data_dir / "logs").mkdir(parents=True)
        self.journal = self.data_dir / "logs" / "20261115.log"
        self.logger = _Logger()

    def _write(self, *entries: str) -> None:
        self.journal.write_bytes(_journal_lines(*entries).encode("utf-16-le"))

    def _watchdog(self, *, stall_after=900, max_runtime=0):
        limits = run_tests_watchdog._WatchdogLimits(
            stall_after_seconds=stall_after,
            max_runtime_seconds=max_runtime,
            tester_model="1",
            tester_log_dirs=[self.data_dir],
        )
        return run_tests_watchdog._Mt5Watchdog(_ExitedProcess(), self.logger, limits)

    def test_the_heartbeat_of_this_pass_is_progress(self):
        self._write("QH\t0\t02:40:10.242\tAutoTesting\tprocessing 10 %")
        watchdog = self._watchdog()
        self.assertFalse(watchdog._terminal_state(10.0))  # lectura de referencia
        self._write(
            "QH\t0\t02:40:10.242\tAutoTesting\tprocessing 10 %",
            "RN\t0\t02:45:10.335\tAutoTesting\tprocessing 20 %",
        )
        self.assertTrue(watchdog._terminal_state(320.0))
        self.assertEqual(watchdog.terminal_progress_at, 320.0)

    def test_the_heartbeat_left_by_the_previous_job_does_not_count(self):
        # El journal es del dia, no del trabajo: al arrancar, el ultimo latido
        # puede ser del trabajo anterior y no dice nada de este.
        self._write("ED\t0\t02:50:10.428\tAutoTesting\tprocessing 90 %")
        watchdog = self._watchdog()
        self.assertFalse(watchdog._terminal_state(10.0))
        self.assertIsNone(watchdog.terminal_progress_at)

    def test_a_journal_that_appears_later_still_counts(self):
        # Primera lectura sin journal: el latido que llegue despues es el
        # primero de esta pasada y tiene que contar.
        watchdog = self._watchdog()
        self.assertFalse(watchdog._terminal_state(10.0))
        self._write("QH\t0\t02:40:10.242\tAutoTesting\tprocessing 10 %")
        self.assertTrue(watchdog._terminal_state(310.0))

    def test_a_terminal_that_never_beats_still_looks_stuck(self):
        self._write("KD\t0\t03:37:50.207\tTester\tautomatic testing started")
        watchdog = self._watchdog()
        watchdog._terminal_state(10.0)
        self.assertFalse(watchdog._terminal_state(900.0))
        self.assertIsNone(watchdog.terminal_progress_at)

    def test_the_idle_window_never_falls_below_two_heartbeats(self):
        watchdog = self._watchdog(stall_after=300)
        self.assertEqual(watchdog._stall_window(), 300)
        watchdog.terminal_progress_at = 300.0
        self.assertEqual(watchdog._stall_window(), TESTER_PROGRESS_STALL_FLOOR_SECONDS)

    def test_the_stall_detector_backs_off_while_the_terminal_beats(self):
        watchdog = self._watchdog(stall_after=900)
        watchdog.generic_stall_checks = 1
        result = watchdog._apply_generic_stall(
            1000.0, journal_progress=False, bases_progress=False,
            terminal_progress=True, stuck_detail="latido",
        )
        self.assertIsNone(result)
        self.assertEqual(watchdog.generic_stall_checks, 0)

    def test_without_any_signal_it_still_kills_at_the_second_check(self):
        watchdog = self._watchdog(stall_after=900)
        watchdog.generic_stall_checks = 1
        result = watchdog._apply_generic_stall(
            1000.0, journal_progress=False, bases_progress=False,
            terminal_progress=False, stuck_detail="ultima linea log: 'testing of ...'",
        )
        self.assertIsNotNone(result)
        self.assertIn("se reinicia el proceso", self.logger.text)


class MaxRuntimeTests(unittest.TestCase):
    """El limite absoluto no puede matar una pasada que esta terminando."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"
        (self.data_dir / "logs").mkdir(parents=True)
        self.logger = _Logger()

    def _watchdog(self, max_runtime):
        limits = run_tests_watchdog._WatchdogLimits(
            stall_after_seconds=900,
            max_runtime_seconds=max_runtime,
            tester_model="1",
            tester_log_dirs=[self.data_dir],
        )
        return run_tests_watchdog._Mt5Watchdog(_ExitedProcess(), self.logger, limits)

    def test_an_advancing_pass_outlives_the_absolute_limit(self):
        watchdog = self._watchdog(1800)
        watchdog.terminal_progress_at = 1750.0
        watchdog.last_terminal_line = "ED\t0\t02:50:10.428\tAutoTesting\tprocessing 31 %"
        self.assertIsNone(watchdog._check_max_runtime(1900.0))
        self.assertIn("31%", self.logger.text)
        self.assertIn("sigue avanzando", self.logger.text)

    def test_the_ceiling_still_ends_it(self):
        watchdog = self._watchdog(1800)
        watchdog.terminal_progress_at = 7100.0
        result = watchdog._check_max_runtime(7200.0)
        self.assertEqual(result, (1, True, 7200.0))
        self.assertIn("supero el techo", self.logger.text)

    def test_a_pass_that_stops_beating_dies_at_the_limit(self):
        watchdog = self._watchdog(1800)
        watchdog.terminal_progress_at = 100.0
        result = watchdog._check_max_runtime(1900.0)
        self.assertEqual(result, (1, True, 1900.0))
        self.assertIn("sin latir", self.logger.text)

    def test_without_any_heartbeat_the_limit_is_the_old_one(self):
        watchdog = self._watchdog(1800)
        result = watchdog._check_max_runtime(1800.0)
        self.assertEqual(result, (1, True, 1800.0))
        self.assertIn("limite absoluto de 1800s", self.logger.text)


if __name__ == "__main__":
    unittest.main()
