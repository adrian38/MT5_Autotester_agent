import argparse
import shutil
import tempfile
import unittest
from pathlib import Path

from ubs.agent.history import _probe_explicit_symbols


AXI_UNIVERSE_INI = """[Indices]
symbols = GER40.sa, US500.sa, USTECH.sa

[Stocks]
symbols = Tesla+
"""


class ExplicitProbeSymbolTests(unittest.TestCase):
    """Un probe pedido a mano no pasa por los filtros del probe global."""

    def setUp(self) -> None:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        self.universe = directory / "assets.ini"
        self.universe.write_text(AXI_UNIVERSE_INI, encoding="utf-8")
        self.args = argparse.Namespace(assets=str(self.universe))

    def test_disabled_symbol_is_still_probeable(self) -> None:
        # GER40.sa esta apagado y ya sentenciado como no_history: el probe
        # global lo descarta dos veces, y es justo el que se quiere remedir.
        symbols, universe, unknown = _probe_explicit_symbols(self.args, ["GER40.sa"])

        self.assertEqual(symbols, ("GER40.sa",))
        self.assertEqual(unknown, [])
        self.assertIn("GER40.sa", universe)

    def test_resolution_keeps_the_ini_spelling(self) -> None:
        # Lo que viaja al tester es la ortografia del `.ini`, no la tecleada.
        symbols, _universe, unknown = _probe_explicit_symbols(self.args, ["ger40.SA"])

        self.assertEqual(symbols, ("GER40.sa",))
        self.assertEqual(unknown, [])

    def test_repeated_symbol_runs_once(self) -> None:
        symbols, _universe, unknown = _probe_explicit_symbols(
            self.args, ["US500.sa", "us500.sa", "Tesla+"]
        )

        self.assertEqual(symbols, ("US500.sa", "Tesla+"))
        self.assertEqual(unknown, [])

    def test_symbol_outside_the_universe_is_reported(self) -> None:
        symbols, _universe, unknown = _probe_explicit_symbols(
            self.args, ["US500.sa", "DE40"]
        )

        self.assertEqual(symbols, ("US500.sa",))
        self.assertEqual(unknown, ["DE40"])


if __name__ == "__main__":
    unittest.main()
