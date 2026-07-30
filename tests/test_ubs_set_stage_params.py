import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ubs.models import Seed
from ubs.set_utils import (
    set_matches_use_every_tick_source,
    set_use_every_tick_text,
    write_set_use_every_tick,
)
from ubs_agent import create_variant, is_agent_mutable_key
from ubs_generate_sets import mutate_text


class UBSSetStageParameterTests(unittest.TestCase):
    def test_use_every_tick_cannot_be_made_agent_mutable(self) -> None:
        with patch("ubs_agent.load_mutation_overrides", return_value=({}, {"UseEveryTick"})):
            self.assertFalse(is_agent_mutable_key("UseEveryTick"))

    def test_existing_use_every_tick_keeps_set_metadata(self) -> None:
        source = "\n".join(
            [
                "; Exit",
                "UseEveryTick=false||false||0||true||N",
                "Exit_stop=100||10||5||200||Y",
            ]
        )

        transformed = set_use_every_tick_text(source, True)

        self.assertIn("UseEveryTick=true||false||0||true||N", transformed)
        self.assertIn("Exit_stop=100||10||5||200||Y", transformed)

    def test_missing_use_every_tick_is_added_with_non_optimizing_metadata(self) -> None:
        transformed = set_use_every_tick_text("; Header\nExit_stop=100||10||5||200||Y", False)

        self.assertEqual(
            transformed.splitlines(),
            [
                "; Header",
                "UseEveryTick=false||false||0||true||N",
                "Exit_stop=100||10||5||200||Y",
            ],
        )

    def test_stage_copy_does_not_modify_source_and_can_be_compared_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "candidate.set"
            ohlc_destination = Path(temp_dir) / "final_tick_ohlc.set"
            real_tick_destination = Path(temp_dir) / "final_tick_real.set"
            original = "UseEveryTick=true||false||0||true||N\nExit_stop=100||10||5||200||Y"
            source.write_text(original, encoding="utf-8")

            write_set_use_every_tick(source, ohlc_destination, False)
            write_set_use_every_tick(source, real_tick_destination, True)

            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertIn(
                "UseEveryTick=false||false||0||true||N",
                ohlc_destination.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "UseEveryTick=true||false||0||true||N",
                real_tick_destination.read_text(encoding="utf-8"),
            )
            self.assertTrue(set_matches_use_every_tick_source(source, ohlc_destination, False))
            self.assertFalse(set_matches_use_every_tick_source(source, ohlc_destination, True))

    def test_agent_generated_result_set_forces_use_every_tick_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_path = root / "seed.set"
            seed_path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=EURUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "UseEveryTick=true||false||0||true||N",
                        "Exit_stop=100||10||5||200||Y",
                        "AdjustLotsizeToVariableValues=true||false||0||true||N",
                        "Risk=1||0||1||20||N",
                        "StartLots=0.10||0.01||0.01||1.00||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(seed_path, "EURUSD", "H1", "family", "1")

            with patch("ubs_agent.load_mutation_overrides", return_value=({}, set())):
                variant = create_variant(
                    seed,
                    "EURUSD",
                    "H1",
                    root / "results",
                    1,
                    1,
                    1,
                    0,
                    {},
                    {},
                    "same",
                    random.Random(7),
                )

            self.assertIn(
                "UseEveryTick=false||false||0||true||N",
                variant.path.read_text(encoding="utf-8"),
            )

    def test_standalone_generator_does_not_mutate_use_every_tick(self) -> None:
        source = "\n".join(
            [
                "UseEveryTick=true||false||0||true||Y",
                "Exit_stop=100||10||5||200||Y",
            ]
        )

        transformed, changed, _ = mutate_text(source, random.Random(3), 2)

        self.assertIn("UseEveryTick=false||false||0||true||Y", transformed)
        self.assertNotIn("UseEveryTick", changed)


if __name__ == "__main__":
    unittest.main()
