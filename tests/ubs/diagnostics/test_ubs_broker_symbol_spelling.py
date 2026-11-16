"""El objetivo logico de una seed contra la ortografia literal del broker.

AXI escribe su catalogo con sufijo de presentacion (`USTECH.sa`), mientras que
el objetivo de una seed viaja en el nombre logico (`USTEC`) y solo gana la
ortografia del broker al ejecutarse. Comparar las dos cosas tal cual daba por
retirados simbolos que el terminal tiene y testea sin problema, con un estado
terminal y sin reintento.
"""
import argparse
import tempfile
import unittest
from pathlib import Path

from run_tests import apply_symbol_map, parse_symbol_map
from ubs.agent.generation.universe import (
    broker_universe_symbols,
    symbol_not_offered,
    symbol_spellings,
    universe_suffixes,
)


AXI_UNIVERSE = """[Forex]
symbols=EURUSD.sa,GBPUSD.sa,USDJPY.sa

[Indices]
symbols=USTECH.sa,GER40.sa,US30.sa,NAS100.fs,DAX40.fs

[Energies]
symbols=USOIL.sa,WTI.fs

[Stocks]
symbols=Tesla+,Apple+,C3.AI+
"""

# ICTrading lista el mismo ticker en varias bolsas: ahi `.NAS` distingue dos
# productos, no es decoracion del broker.
ICTRADING_UNIVERSE = """[Stocks]
symbols=AAPL.NAS,TSLA.NAS,MSFT.NAS,VOD.LSE,BP.LSE
"""


def universe_args(text: str, directory: str, **suffixes) -> argparse.Namespace:
    path = Path(directory) / "assets.ini"
    path.write_text(text, encoding="utf-8")
    return argparse.Namespace(assets=str(path), **suffixes)


AXI_SUFFIXES = {
    "symbol_suffix": ".sa",
    "symbol_futures_suffix": ".fs",
    "symbol_shares_suffix": "+",
}


class UniverseSuffixTests(unittest.TestCase):
    def test_reads_the_suffixes_the_file_itself_repeats(self) -> None:
        listed = {"EURUSD.sa", "USTECH.sa", "NAS100.fs", "WTI.fs", "Tesla+", "Apple+"}
        self.assertEqual(universe_suffixes(listed), (".fs", ".sa", "+"))

    def test_a_name_with_a_dot_is_not_a_suffix(self) -> None:
        # `C3.AI` aparece una sola vez y en mayusculas: es parte del nombre.
        self.assertNotIn(".AI", universe_suffixes({"C3.AI", "EURUSD.sa", "USDJPY.sa"}))

    def test_an_exchange_qualifier_is_never_presentation(self) -> None:
        listed = {"AAPL.NAS", "TSLA.NAS", "MSFT.NAS", "VOD.LSE", "BP.LSE"}
        self.assertEqual(universe_suffixes(listed), ())


class SymbolNotOfferedTests(unittest.TestCase):
    def test_a_mapped_target_without_suffix_is_not_retired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = universe_args(AXI_UNIVERSE, temporary, **AXI_SUFFIXES)
            universe = broker_universe_symbols(args)
            symbol_map = parse_symbol_map("USTEC=USTECH,DE40=GER40,XTIUSD=USOIL")
            for symbol in ("USTEC", "DE40", "XTIUSD"):
                with self.subTest(symbol=symbol):
                    self.assertFalse(symbol_not_offered(symbol, universe, symbol_map))

    def test_the_broker_spelling_itself_still_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            universe = broker_universe_symbols(universe_args(AXI_UNIVERSE, temporary, **AXI_SUFFIXES))
            self.assertFalse(symbol_not_offered("USTECH.sa", universe, {}))
            self.assertFalse(symbol_not_offered("Tesla+", universe, {}))

    def test_a_symbol_the_broker_does_not_have_is_still_retired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            universe = broker_universe_symbols(universe_args(AXI_UNIVERSE, temporary, **AXI_SUFFIXES))
            self.assertTrue(symbol_not_offered("DXY_U6", universe, {}))
            # Sin alias, AXI no tiene nada que se llame asi: lista `Tesla+`.
            self.assertTrue(symbol_not_offered("TSLA.NAS", universe, {}))

    def test_an_exchange_qualifier_is_not_dropped_to_force_a_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            universe = broker_universe_symbols(universe_args(ICTRADING_UNIVERSE, temporary))
            self.assertFalse(symbol_not_offered("AAPL.NAS", universe, {}))
            self.assertTrue(symbol_not_offered("AAPL", universe, {}))

    def test_an_empty_universe_never_retires_anything(self) -> None:
        self.assertFalse(symbol_not_offered("CUALQUIERA", set(), {}))


class ShareAliasTests(unittest.TestCase):
    def test_an_alias_written_with_its_exchange_reaches_the_map(self) -> None:
        symbol_map = parse_symbol_map("TSLA.NAS=Tesla+,AAPL.NAS=Apple+")
        self.assertEqual(apply_symbol_map("TSLA.NAS", symbol_map), "Tesla+")
        self.assertEqual(apply_symbol_map("AAPL.NAS", symbol_map), "Apple+")

    def test_a_symbol_already_in_broker_spelling_is_left_alone(self) -> None:
        # `USOIL=USOIL` no puede despojar del sufijo a un objetivo que ya lo
        # trae: `parse_symbol_map` solo conserva la clave entera cuando el
        # sufijo es de bolsa, y `.sa` no lo es.
        symbol_map = parse_symbol_map("USOIL=USOIL,XTIUSD=USOIL")
        self.assertEqual(apply_symbol_map("USOIL.sa", symbol_map), "USOIL.sa")
        self.assertEqual(apply_symbol_map("NAS100.fs", parse_symbol_map("NAS100=NAS100.fs")), "NAS100.fs")

    def test_the_alias_rescues_a_client_seed_from_a_terminal_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            universe = broker_universe_symbols(universe_args(AXI_UNIVERSE, temporary, **AXI_SUFFIXES))
            symbol_map = parse_symbol_map("TSLA.NAS=Tesla+,AAPL.NAS=Apple+")
            self.assertFalse(symbol_not_offered("TSLA.NAS", universe, symbol_map))
            self.assertFalse(symbol_not_offered("AAPL.NAS", universe, symbol_map))


class SymbolSpellingsTests(unittest.TestCase):
    def test_covers_the_logical_name_the_mapped_one_and_the_broker_one(self) -> None:
        symbol_map = parse_symbol_map("USTEC=USTECH")
        self.assertEqual(
            symbol_spellings("USTEC", symbol_map, ".sa"),
            ("USTEC", "USTECH", "USTECH.sa"),
        )

    def test_a_name_that_already_carries_its_suffix_is_not_doubled(self) -> None:
        self.assertEqual(symbol_spellings("Tesla+", {}, ".sa"), ("Tesla+",))
        self.assertEqual(symbol_spellings("WTI.fs", {}, ".sa"), ("WTI.fs",))

    def test_an_empty_symbol_has_no_spellings(self) -> None:
        self.assertEqual(symbol_spellings("", {}, ".sa"), ())


if __name__ == "__main__":
    unittest.main()
