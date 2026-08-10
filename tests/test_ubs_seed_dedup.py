import sqlite3
import tempfile
import unittest
from pathlib import Path

from ubs.seed_dedup import (
    DUPLICATE_EQUIVALENT,
    DUPLICATE_EXACT,
    SeedDuplicateIndex,
    SeedFingerprint,
    parse_set_params,
    scan_duplicates,
)
from ui.ubs_seeds_logic import UBSSeedsLogicMixin


def _set_text(pairs: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in pairs.items()) + "\n"


def _base_params(count: int = 150) -> dict[str, str]:
    params = {f"Param_{i:03d}": str(i) for i in range(count)}
    params["ForceSymbol"] = "XAUUSD"
    params["Run_Strategy"] = "1"
    return params


def _fingerprint(name: str, params: dict[str, str], symbol="XAUUSD", period="H1"):
    return SeedFingerprint.from_text(Path(name), _set_text(params), symbol, period)


class ParseSetParamsTests(unittest.TestCase):
    def test_strips_optimization_ranges_comments_and_bom(self) -> None:
        text = "﻿Lots=0.01||0.01||0.01||1.0||N\n; comentario\n\nSL=30\nnovalue\n"
        self.assertEqual(parse_set_params(text), {"Lots": "0.01", "SL": "30"})


class SeedDuplicateIndexTests(unittest.TestCase):
    def test_identical_content_is_detected_as_exact(self) -> None:
        index = SeedDuplicateIndex()
        params = _base_params()
        index.add(_fingerprint("existing.set", params))

        match = index.find_duplicate(_fingerprint("incoming.set", params))

        self.assertIsNotNone(match)
        existing, reason = match
        self.assertEqual(reason, DUPLICATE_EXACT)
        self.assertEqual(existing.path, Path("existing.set"))

    def test_new_ea_keys_do_not_hide_an_equivalent_seed(self) -> None:
        """El caso real: la EA se actualiza y anade claves, el hash ya no coincide."""
        index = SeedDuplicateIndex()
        old_params = _base_params()
        index.add(_fingerprint("existing.set", old_params))

        updated = dict(old_params)
        updated.update({f"RNG_New_{i}": "0" for i in range(40)})

        match = index.find_duplicate(_fingerprint("incoming.set", updated))

        self.assertIsNotNone(match)
        self.assertEqual(match[1], DUPLICATE_EQUIVALENT)

    def test_different_value_on_a_shared_key_is_not_a_duplicate(self) -> None:
        index = SeedDuplicateIndex()
        old_params = _base_params()
        index.add(_fingerprint("existing.set", old_params))

        changed = dict(old_params)
        changed["Param_007"] = "999"
        changed.update({f"RNG_New_{i}": "0" for i in range(40)})

        self.assertIsNone(index.find_duplicate(_fingerprint("incoming.set", changed)))

    def test_same_params_on_another_symbol_or_timeframe_is_not_a_duplicate(self) -> None:
        index = SeedDuplicateIndex()
        params = _base_params()
        index.add(_fingerprint("existing.set", params, symbol="XAUUSD", period="H1"))

        self.assertIsNone(
            index.find_duplicate(_fingerprint("other.set", params, symbol="EURUSD"))
        )
        self.assertIsNone(
            index.find_duplicate(_fingerprint("other.set", params, period="M15"))
        )

    def test_small_overlap_is_not_enough(self) -> None:
        index = SeedDuplicateIndex()
        index.add(_fingerprint("existing.set", _base_params()))

        tiny = {f"Param_{i:03d}": str(i) for i in range(20)}
        self.assertIsNone(index.find_duplicate(_fingerprint("tiny.set", tiny)))

    def test_shared_ratio_guard_rejects_a_thin_subset(self) -> None:
        """Un fichero pequeno contenido en otro mucho mayor no es la misma seed."""
        index = SeedDuplicateIndex()
        index.add(_fingerprint("existing.set", _base_params(400)))

        subset = {f"Param_{i:03d}": str(i) for i in range(120)}
        subset.update({f"Only_{i}": "1" for i in range(120)})

        self.assertIsNone(index.find_duplicate(_fingerprint("subset.set", subset)))

    def test_empty_params_never_match(self) -> None:
        index = SeedDuplicateIndex()
        index.add(_fingerprint("existing.set", _base_params()))

        self.assertIsNone(index.find_duplicate(_fingerprint("empty.set", {})))

    def test_index_grows_so_the_batch_dedupes_against_itself(self) -> None:
        index = SeedDuplicateIndex()
        params = _base_params()
        self.assertIsNone(index.find_duplicate(_fingerprint("first.set", params)))
        index.add(_fingerprint("first.set", params))

        self.assertIsNotNone(index.find_duplicate(_fingerprint("second.set", params)))
        self.assertEqual(len(index), 1)

    def test_unknown_symbol_and_period_are_normalised(self) -> None:
        index = SeedDuplicateIndex()
        params = _base_params()
        index.add(SeedFingerprint.from_text(Path("a.set"), _set_text(params), "", ""))

        match = index.find_duplicate(
            SeedFingerprint.from_text(Path("b.set"), _set_text(params), "unknown", "unknown")
        )
        self.assertIsNotNone(match)


class ScanDuplicatesTests(unittest.TestCase):
    def test_pool_without_duplicates_yields_no_groups(self) -> None:
        a = _base_params()
        b = dict(a)
        b["Param_010"] = "999"
        self.assertEqual(
            scan_duplicates([_fingerprint("a.set", a), _fingerprint("b.set", b)]), []
        )

    def test_groups_every_redundant_seed_under_one_keeper(self) -> None:
        params = _base_params()
        newer = dict(params)
        newer.update({f"RNG_New_{i}": "0" for i in range(40)})

        groups = scan_duplicates([
            _fingerprint("keep.set", params),
            _fingerprint("dup_one.set", params),
            _fingerprint("dup_two.set", newer),
        ])

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.size, 3)
        self.assertEqual(group.keeper.path, Path("keep.set"))
        reasons = {fp.path.name: reason for fp, reason in group.redundant}
        self.assertEqual(reasons["dup_one.set"], DUPLICATE_EXACT)
        self.assertEqual(reasons["dup_two.set"], DUPLICATE_EQUIVALENT)

    def test_priority_decides_which_seed_survives(self) -> None:
        params = _base_params()
        evaluated = {Path("scored.set")}

        groups = scan_duplicates(
            [_fingerprint("aaa.set", params), _fingerprint("scored.set", params)],
            priority=lambda fp: (0 if fp.path in evaluated else 1,),
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].keeper.path, Path("scored.set"))
        self.assertEqual(groups[0].redundant[0][0].path, Path("aaa.set"))

    def test_default_priority_keeps_the_shortest_path_deterministically(self) -> None:
        params = _base_params()
        seeds = [
            _fingerprint("zzz/a_very_long_name_optimization.set", params),
            _fingerprint("zzz/short.set", params),
        ]

        first = scan_duplicates(seeds)
        second = scan_duplicates(list(reversed(seeds)))

        self.assertEqual(first[0].keeper.path, Path("zzz/short.set"))
        self.assertEqual(first[0].keeper.path, second[0].keeper.path)

    def test_separate_scopes_produce_separate_groups(self) -> None:
        params = _base_params()
        groups = scan_duplicates([
            _fingerprint("h1_a.set", params, period="H1"),
            _fingerprint("h1_b.set", params, period="H1"),
            _fingerprint("h4_a.set", params, period="H4"),
            _fingerprint("h4_b.set", params, period="H4"),
        ])

        self.assertEqual(len(groups), 2)
        self.assertEqual({g.size for g in groups}, {2})

    def test_cleanup_removes_only_redundant_seed_memory_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            keeper = root / "keeper.set"
            redundant = root / "redundant.set"
            conn = sqlite3.connect(":memory:")
            conn.executescript(
                """
                create table seed_scores (seed_path text primary key);
                create table seed_overrides (seed_path text primary key);
                """
            )
            for path in (keeper, redundant):
                conn.execute("insert into seed_scores values (?)", (str(path),))
                conn.execute("insert into seed_overrides values (?)", (str(path),))

            logic = object.__new__(UBSSeedsLogicMixin)
            logic._cleanup_seed_db(conn, [str(redundant)])

            self.assertEqual(
                conn.execute("select seed_path from seed_scores").fetchall(),
                [(str(keeper),)],
            )
            self.assertEqual(
                conn.execute("select seed_path from seed_overrides").fetchall(),
                [(str(keeper),)],
            )
            conn.close()


if __name__ == "__main__":
    unittest.main()
