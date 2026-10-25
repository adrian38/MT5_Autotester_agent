"""El reparto de pruebas por grupos cubre tests/ exactamente una vez."""
from __future__ import annotations

import unittest

from tools import test_groups


class TestGroupRegistryTests(unittest.TestCase):
    def test_no_test_module_is_left_without_a_group(self) -> None:
        orphans = sorted(test_groups.orphan_modules())
        self.assertEqual(
            orphans,
            [],
            "Hay pruebas que ningun grupo corre; anadelas a TEST_GROUPS: "
            + ", ".join(orphans),
        )

    def test_the_registry_does_not_name_modules_that_are_gone(self) -> None:
        unknown = sorted(test_groups.unknown_modules())
        self.assertEqual(
            unknown,
            [],
            "TEST_GROUPS cita modulos que ya no existen: " + ", ".join(unknown),
        )

    def test_no_module_is_shared_by_two_groups(self) -> None:
        repeated = sorted(test_groups.duplicated_modules())
        self.assertEqual(
            repeated,
            [],
            "Modulos repartidos en mas de un grupo: " + ", ".join(repeated),
        )

    def test_the_guard_group_exists_and_carries_the_ratchets(self) -> None:
        guards = test_groups.modules_for(test_groups.GUARD_GROUP)
        for module in ("test_file_length", "test_function_length", "test_test_groups"):
            self.assertIn(module, guards)

    def test_unittest_targets_are_importable_names(self) -> None:
        targets = test_groups.unittest_targets("guardas")
        self.assertIn("tests.test_file_length", targets)
        self.assertTrue(all(name.startswith("tests.") for name in targets))

    def test_an_unknown_group_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            test_groups.modules_for("no_existe")

    def test_every_group_has_at_least_one_module(self) -> None:
        for group in test_groups.group_names():
            self.assertTrue(test_groups.modules_for(group), group)


if __name__ == "__main__":
    unittest.main()
