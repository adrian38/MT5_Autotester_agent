import tempfile
import unittest
from pathlib import Path

from ubs.params_catalog import parse_set_parameter_text
from ubs.seed_dedup import SeedDuplicateIndex, SeedFingerprint
from ubs.set_rules import normalize_generated_set_text
from ubs.set_utils import read_set_with_encoding
from ubs.v6_parameters import UBS_VERSION, V6_DEFAULT_SET_TEXT, V6_PARAMETER_KEYS
from ubs.version_rules import (
    UBS_VERSION_RULES,
    filter_set_parameters_for_version,
    version_rule,
)
from ui.ubs_seeds_import import UBSSeedsImportMixin
from ubs_prepare_sets import seed_target


class UBSVersionRulesTests(unittest.TestCase):
    def test_v6_rule_registers_exactly_the_164_canonical_keys(self) -> None:
        rule = version_rule("v6")

        self.assertIs(rule, UBS_VERSION_RULES[UBS_VERSION])
        self.assertEqual(rule.allowed_parameter_keys, frozenset(V6_PARAMETER_KEYS))

    def test_filter_removes_only_unknown_real_parameters(self) -> None:
        text = (
            "; comentario\r\n"
            "General=--------------------\r\n"
            "Run_Strategy=2||1||1||3||N\r\n"
            "FutureKey=99||0||1||100||N\r\n"
            "OnlyUp=false||false||0||true||N"
        )

        filtered, removed = filter_set_parameters_for_version(text, "V6")

        self.assertEqual(removed, ("FutureKey",))
        self.assertEqual(
            filtered,
            "; comentario\r\n"
            "General=--------------------\r\n"
            "Run_Strategy=2||1||1||3||N\r\n"
            "OnlyUp=false||false||0||true||N",
        )

    def test_filter_does_not_add_missing_v6_keys(self) -> None:
        filtered, removed = filter_set_parameters_for_version("Run_Strategy=1\n")

        self.assertEqual(filtered, "Run_Strategy=1\n")
        self.assertEqual(removed, ())

    def test_unknown_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "V7"):
            filter_set_parameters_for_version("Run_Strategy=1\n", "V7")

    def test_seed_import_writes_only_v6_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            source = source_dir / "XAUUSD_H1.set"
            source.write_text(
                V6_DEFAULT_SET_TEXT + "FutureKey=99||0||1||100||N\n",
                encoding="utf-8",
            )
            errors: list[str] = []

            outcome = UBSSeedsImportMixin._import_one_ubs_seed(
                SeedDuplicateIndex(), source, source_dir, output_dir, [], errors,
            )

            imported = [path for path in output_dir.rglob("*.set") if path.is_file()]
            self.assertEqual(outcome, "copied")
            self.assertEqual(errors, [])
            self.assertEqual(len(imported), 1)
            text, _encoding = read_set_with_encoding(imported[0])
            keys = tuple(param["key"] for param in parse_set_parameter_text(text))
            self.assertEqual(keys, V6_PARAMETER_KEYS)
            self.assertNotIn("FutureKey=", text)

    def test_seed_import_rejects_unsupported_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            source = source_dir / "DE40_D1.set"
            source.write_text(
                V6_DEFAULT_SET_TEXT.replace("Run_Strategy=1", "Run_Strategy=3", 1),
                encoding="utf-8",
            )
            errors: list[str] = []

            outcome = UBSSeedsImportMixin._import_one_ubs_seed(
                SeedDuplicateIndex(), source, source_dir, output_dir, [], errors,
            )

            self.assertEqual(outcome, "")
            self.assertEqual(list(output_dir.rglob("*.set")), [])
            self.assertIn("Run_Strategy=3 no soportado por UBS V6", errors[0])

    def test_general_onechart_set_is_listed_and_not_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            source = source_dir / "ubs_onechart_live_MainEA_Settings.set"
            source.write_text("UseAutoLoader=true\nRun_Strategy=1\n", encoding="utf-8")
            skipped: list[str] = []

            outcome = UBSSeedsImportMixin._import_one_ubs_seed(
                SeedDuplicateIndex(), source, source_dir, output_dir, [], [], skipped,
            )

            self.assertEqual(outcome, "general")
            self.assertEqual(skipped, [source.name])
            self.assertEqual(list(output_dir.rglob("*.set")), [])

    def test_same_logical_seed_replaces_old_content_without_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            source = source_dir / "XAUUSD_H1.set"
            relative = source.relative_to(source_dir)
            target = seed_target(output_dir, relative, "XAUUSD", "H1")
            target.parent.mkdir(parents=True)
            target.write_text("Run_Strategy=1\nExit_stop=10\n", encoding="utf-8")
            index = SeedDuplicateIndex()
            index.add(
                SeedFingerprint.from_text(
                    target, target.read_text(encoding="utf-8"), "XAUUSD", "H1"
                )
            )
            source.write_text(
                "Run_Strategy=1\nExit_stop=20\nFutureKey=99\n", encoding="utf-8"
            )

            outcome = UBSSeedsImportMixin._import_one_ubs_seed(
                index, source, source_dir, output_dir, [], [],
            )

            files = [path for path in output_dir.rglob("*.set") if path.is_file()]
            imported, _encoding = read_set_with_encoding(target)
            self.assertEqual(outcome, "replaced")
            self.assertEqual(files, [target])
            self.assertIn("Exit_stop=20", imported)
            self.assertNotIn("FutureKey=", imported)
            self.assertEqual(len(index), 1)

    def test_matching_v6_fingerprint_reuses_existing_seed_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            existing = output_dir / "old_seed.set"
            existing_text, _found, _missing = normalize_generated_set_text(
                "Run_Strategy=1\nExit_stop=20\n"
            )
            existing.write_text(existing_text, encoding="utf-8")
            index = SeedDuplicateIndex()
            index.add(
                SeedFingerprint.from_text(
                    existing, existing.read_text(encoding="utf-8"), "XAUUSD", "H1"
                )
            )
            source = source_dir / "new_family" / "XAUUSD_H1.set"
            source.parent.mkdir()
            source.write_text("Run_Strategy=1\nExit_stop=20\n", encoding="utf-8")

            outcome = UBSSeedsImportMixin._import_one_ubs_seed(
                index, source, source_dir, output_dir, [], [],
            )

            files = [path for path in output_dir.rglob("*.set") if path.is_file()]
            self.assertEqual(outcome, "replaced")
            self.assertEqual(files, [existing])
            self.assertEqual(len(index), 1)


if __name__ == "__main__":
    unittest.main()
