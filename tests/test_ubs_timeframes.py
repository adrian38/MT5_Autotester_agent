"""Soporte de timeframes: la tabla canonica y su propagacion a toda la app."""

import tempfile
import unittest
from pathlib import Path

from run_tests import (
    KNOWN_TIMEFRAMES,
    TIMEFRAME_ENUM,
    infer_period_from_path,
    infer_period_from_set,
    load_set_params,
)
from ubs.account import DEFAULT_TIMEFRAME_UNIVERSE, load_account_timeframe_universe
from ubs.seeds import seed_eval_filename, seed_from_path
from ubs.selection import FITNESS_TIMEFRAMES
from ubs_agent import (
    BASE_TIMEFRAME_UNIVERSE,
    LEGACY_TIMEFRAME_TO_ENUM,
    TIMEFRAME_TO_ENUM,
    related_timeframes,
)


def _write_set(directory: Path, name: str, params: dict[str, str]) -> Path:
    path = directory / name
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in params.items()) + "\n",
        encoding="utf-8",
    )
    return path


class CanonicalTimeframeTableTests(unittest.TestCase):
    def test_known_timeframes_is_derived_from_the_enum_in_duration_order(self) -> None:
        self.assertEqual(KNOWN_TIMEFRAMES, tuple(dict.fromkeys(TIMEFRAME_ENUM.values())))
        self.assertEqual(
            KNOWN_TIMEFRAMES,
            ("M1", "M5", "M15", "M30", "H1", "H2", "H3", "H4", "D1", "W1", "MN"),
        )

    def test_mt5_hourly_encoding_is_16384_plus_hours(self) -> None:
        for enum_value, period in TIMEFRAME_ENUM.items():
            if not period.startswith("H"):
                continue
            self.assertEqual(int(enum_value) - 16384, int(period[1:]), period)

    def test_selection_reuses_the_canonical_table(self) -> None:
        self.assertEqual(FITNESS_TIMEFRAMES, KNOWN_TIMEFRAMES)

    def test_every_known_timeframe_round_trips_through_the_agent_map(self) -> None:
        for period in KNOWN_TIMEFRAMES:
            self.assertEqual(TIMEFRAME_ENUM[TIMEFRAME_TO_ENUM[period]], period)

    def test_legacy_minute_aliases_cover_the_new_hourly_timeframes(self) -> None:
        self.assertEqual(LEGACY_TIMEFRAME_TO_ENUM["120"], TIMEFRAME_TO_ENUM["H2"])
        self.assertEqual(LEGACY_TIMEFRAME_TO_ENUM["180"], TIMEFRAME_TO_ENUM["H3"])


class H2H3InferenceTests(unittest.TestCase):
    def test_strategy_timeframe_16386_is_h2_not_a_fallback(self) -> None:
        """Antes caia al ultimo recurso y Entry_Timing decidia el periodo."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_set(
                Path(tmp),
                "UBS-XAGUSD-H2-AGA04.set",
                {
                    "ForceSymbol": "XAGUSD",
                    "Run_Strategy": "1",
                    "ST1_Timeframe": "16386",
                    "Entry_Timing": "5",
                    "ATR_Timeframe": "16408",
                },
            )
            self.assertEqual(infer_period_from_set(path, load_set_params(path)), "H2")

    def test_strategy_timeframe_16387_is_h3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_set(
                Path(tmp),
                "seed.set",
                {
                    "ForceSymbol": "XAUUSD",
                    "Run_Strategy": "1",
                    "ST1_Timeframe": "16387",
                    "Entry_Timing": "16385",
                },
            )
            self.assertEqual(infer_period_from_set(path, load_set_params(path)), "H3")

    def test_h2_and_h3_are_recognised_in_file_names(self) -> None:
        self.assertEqual(infer_period_from_path(Path("UBS-XAGUSD-H2-AGA04.set")), "H2")
        self.assertEqual(infer_period_from_path(Path("UBS-XAUUSD-H3-H3V113.set")), "H3")

    def test_h2_in_a_name_does_not_shadow_h1_or_h4(self) -> None:
        self.assertEqual(infer_period_from_path(Path("x_H1_a.set")), "H1")
        self.assertEqual(infer_period_from_path(Path("x_H4_a.set")), "H4")

    def test_seed_eval_copy_carries_the_period_to_the_tester(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_set(
                Path(tmp),
                "9637036_Till_UBS-XAGUSD-H2-AGA04.set",
                {
                    "ForceSymbol": "XAGUSD",
                    "Run_Strategy": "1",
                    "ST1_Timeframe": "16386",
                    "Entry_Timing": "5",
                },
            )
            seed = seed_from_path(path)
            self.assertEqual((seed.symbol, seed.period), ("XAGUSD", "H2"))
            name = seed_eval_filename(1, seed, set())
            # run_tests corre con --prefer-set-path-timeframe: manda el nombre.
            self.assertEqual(infer_period_from_path(Path(name)), "H2")


class GenerationUniverseTests(unittest.TestCase):
    def test_h2_and_h3_are_generation_targets_by_default(self) -> None:
        for universe in (DEFAULT_TIMEFRAME_UNIVERSE, BASE_TIMEFRAME_UNIVERSE):
            self.assertIn("H2", universe)
            self.assertIn("H3", universe)
        self.assertEqual(DEFAULT_TIMEFRAME_UNIVERSE, BASE_TIMEFRAME_UNIVERSE)

    def test_the_universe_is_broker_agnostic(self) -> None:
        """Los TFs son la unica dimension no aislada por broker."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            universes = {
                load_account_timeframe_universe(base, account, broker)
                for broker, account in (
                    ("ICTRADING", "STANDARD"),
                    ("ROBOFOREX", "ECN"),
                    ("AXI", "STANDARD"),
                )
            }
            self.assertEqual(len(universes), 1)
            self.assertEqual(universes.pop(), DEFAULT_TIMEFRAME_UNIVERSE)

    def test_the_universe_stays_inside_the_supported_table(self) -> None:
        for period in (*DEFAULT_TIMEFRAME_UNIVERSE, "W1", "MN"):
            self.assertIn(period, KNOWN_TIMEFRAMES)


class RelatedTimeframeTests(unittest.TestCase):
    def test_h2_and_h3_are_only_offered_when_the_universe_allows_them(self) -> None:
        closed = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
        self.assertNotIn("H2", related_timeframes("H1", closed))

        opened = (*closed, "H2", "H3")
        self.assertIn("H2", related_timeframes("H1", opened))
        self.assertIn("H3", related_timeframes("H4", opened))

    def test_h2_neighbours_stay_inside_the_hourly_band(self) -> None:
        opened = ("M1", "M5", "M15", "M30", "H1", "H2", "H3", "H4", "D1")
        self.assertEqual(related_timeframes("H2", opened), ("H1", "H2", "H3", "H4"))


if __name__ == "__main__":
    unittest.main()
