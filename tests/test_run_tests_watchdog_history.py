"""El watchdog de Model=4 no puede matar la sincronizacion que el pidio.

El 20 de octubre de 2026 tres auditorias de AXI murieron por esto. El preflight
de Model=4 aparta la cache M1 del simbolo a proposito; mientras MT5 la rehace
no vuelca el journal ni toca los .tkc, que eran las dos unicas senales que
miraba el detector, asi que lo mataba a los 40s. El orden del log lo dice:
XAUUSD.sa (cache 2014-2026) murio en los dos intentos, USDJPY.sa (2015-2026) en
el primero, y BTCUSD.sa y ETHUSD.sa (tres anos) pasaron a la primera.
"""
import tempfile
import unittest
from pathlib import Path

import run_tests_watchdog


class HistorySyncSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_a_cache_still_missing_counts_as_nothing(self):
        missing = self.root / "2026.hcc"
        self.assertEqual(run_tests_watchdog._history_sync_signature([missing]), (0, 0))

    def test_the_signature_grows_as_mt5_rebuilds_the_cache(self):
        first, second = self.root / "2025.hcc", self.root / "2026.hcc"
        first.write_bytes(b"x" * 10)
        self.assertEqual(
            run_tests_watchdog._history_sync_signature([first, second]), (1, 10),
        )
        second.write_bytes(b"y" * 7)
        self.assertEqual(
            run_tests_watchdog._history_sync_signature([first, second]), (2, 17),
        )

    def test_without_rotations_it_is_empty_and_changes_nothing(self):
        self.assertEqual(run_tests_watchdog._history_sync_signature([]), (0, 0))


class WatchdogProgressTests(unittest.TestCase):
    """Sin ticks ni journal, rehacer el historico M1 sigue siendo progreso."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "data"
        (self.data_dir / "bases").mkdir(parents=True)
        self.target = self.root / "2026.hcc"

    def _watchdog(self, history_targets):
        limits = run_tests_watchdog._WatchdogLimits(
            kick_after_seconds=30,
            tester_log_dirs=[self.data_dir],
            history_targets=history_targets,
        )
        return run_tests_watchdog._Mt5Watchdog(None, None, limits)

    def test_the_m1_rebuild_is_seen_as_progress(self):
        watchdog = self._watchdog([self.target])
        watchdog._bases_state()  # primera lectura: todavia no hay con que comparar
        self.target.write_bytes(b"x" * 64)
        _signature, progress, first = watchdog._bases_state()
        self.assertTrue(progress)
        self.assertFalse(first)

    def test_a_terminal_that_moves_nothing_still_looks_stuck(self):
        # El arreglo no puede tapar un atasco de verdad: sin ticks, sin journal
        # y sin historico, la firma no cambia y el detector sigue disparando.
        watchdog = self._watchdog([self.target])
        watchdog._bases_state()
        _signature, progress, _first = watchdog._bases_state()
        self.assertFalse(progress)


if __name__ == "__main__":
    unittest.main()
