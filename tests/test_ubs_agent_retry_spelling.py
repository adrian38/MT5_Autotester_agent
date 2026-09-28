import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from ubs_agent import (
    write_retry_set,
    write_set_use_every_tick,
)


class RetrySetBrokerSpellingTests(unittest.TestCase):
    def test_retry_repairs_exact_symbol_spelling_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old.set"
            destination = root / "retry.set"
            assets = root / "assets.ini"
            source.write_text(
                "ForceSymbol=MIDDE50\nUseEveryTick=true||false||0||true||N\n",
                encoding="utf-8",
            )
            assets.write_text(
                "[Indices]\nsymbols=MidDE50,TecDE30,.JP225Cash,MixedSuffix.a\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(broker="ICTRADING", assets=assets, symbol_map="")

            exact = write_retry_set(source, destination, False, args, "MIDDE50")

            self.assertEqual(exact, "MidDE50")
            self.assertIn("ForceSymbol=MidDE50", destination.read_text(encoding="utf-8"))
            self.assertIn("UseEveryTick=false", destination.read_text(encoding="utf-8"))
            self.assertIn("ForceSymbol=MIDDE50", source.read_text(encoding="utf-8"))

    def test_retry_rejects_ambiguous_case_before_mt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old.set"
            assets = root / "assets.ini"
            source.write_text("ForceSymbol=MIDDE50\n", encoding="utf-8")
            assets.write_text("[Indices]\nsymbols=MidDE50,MIDDE50\n", encoding="utf-8")
            args = SimpleNamespace(broker="ICTRADING", assets=assets, symbol_map="")

            with self.assertRaisesRegex(ValueError, "nombre MT5 único"):
                write_retry_set(source, root / "retry.set", False, args, "MIDDE50")

    def test_retry_repairs_spelling_for_every_broker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old.set"
            destination = root / "retry.set"
            assets = root / "assets.ini"
            source.write_text("ForceSymbol=.US500CASH\n", encoding="utf-8")
            assets.write_text(
                "[IndicesEnergies]\nsymbols=.US500Cash,.US30Cash\n"
                "[CommonAliases]\nUS500=.US500Cash\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(broker="ROBOFOREX", assets=assets, symbol_map="")

            exact = write_retry_set(source, destination, False, args, ".US500CASH")

            self.assertEqual(exact, ".US500Cash")
            self.assertIn("ForceSymbol=.US500Cash", destination.read_text(encoding="utf-8"))

    def test_missing_universe_leaves_the_stage_copy_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old.set"
            destination = root / "stage.set"
            source.write_text("ForceSymbol=.US500CASH\n", encoding="utf-8")
            args = SimpleNamespace(broker="ICTRADING", assets=root / "missing.ini", symbol_map="")

            exact = write_retry_set(source, destination, False, args, ".US500CASH")

            self.assertEqual(exact, ".US500CASH")
            self.assertIn("ForceSymbol=.US500CASH", destination.read_text(encoding="utf-8"))

    def test_stage_copy_is_byte_identical_when_spelling_already_matches(self) -> None:
        # Final Tick compara byte a byte para reutilizar OHLC ya ejecutados, asi
        # que el caso normal no puede normalizar finales de linea.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old.set"
            plain = root / "plain.set"
            repaired = root / "repaired.set"
            assets = root / "assets.ini"
            source.write_bytes(b"ForceSymbol=.US500Cash\r\nATR_Period=30\r\n")
            assets.write_text("[IndicesEnergies]\nsymbols=.US500Cash\n", encoding="utf-8")
            args = SimpleNamespace(broker="ROBOFOREX", assets=assets, symbol_map="")

            write_set_use_every_tick(source, plain, False)
            exact = write_retry_set(source, repaired, False, args, ".US500Cash")

            self.assertEqual(exact, ".US500Cash")
            self.assertEqual(repaired.read_bytes(), plain.read_bytes())

    def test_retry_outside_ic_keeps_unresolvable_symbol_instead_of_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "old.set"
            destination = root / "retry.set"
            assets = root / "assets.ini"
            source.write_text("ForceSymbol=US500\n", encoding="utf-8")
            assets.write_text(
                "[IndicesEnergies]\nsymbols=.US500Cash\n[CommonAliases]\nUS500=.US500Cash\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(broker="ROBOFOREX", assets=assets, symbol_map="")

            exact = write_retry_set(source, destination, False, args, "US500")

            self.assertEqual(exact, "US500")
            self.assertIn("ForceSymbol=US500", destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
