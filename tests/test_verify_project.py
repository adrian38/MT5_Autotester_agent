from __future__ import annotations

import unittest

from tools.test_groups import GUARD_GROUP
from tools.verify_project import command_specs, parse_args


class VerifyProjectTests(unittest.TestCase):
    def test_default_plan_is_the_cheap_one(self) -> None:
        names = [spec.name for spec in command_specs()]
        self.assertEqual(
            names,
            ["nombres", "funciones", "ficheros", "contexto", "protocolo",
             "grupos", f"grupo {GUARD_GROUP}"],
        )

    def test_quick_still_means_the_default(self) -> None:
        self.assertEqual(parse_args(["--quick"]), parse_args([]))

    def test_full_appends_the_whole_suite_last(self) -> None:
        plan = parse_args(["--full"])
        self.assertTrue(plan.include_full_suite)
        names = [spec.name for spec in command_specs(
            groups=plan.groups, include_full_suite=plan.include_full_suite,
        )]
        self.assertEqual(names[-1], "suite AXI")

    def test_a_requested_group_runs_after_the_guards(self) -> None:
        plan = parse_args(["--group", "final_tick"])
        self.assertEqual(plan.groups, ("final_tick",))
        names = [spec.name for spec in command_specs(groups=plan.groups)]
        self.assertEqual(names[-1], "grupo final_tick")
        self.assertNotIn("suite AXI", names)

    def test_several_groups_are_kept_in_order(self) -> None:
        plan = parse_args(["--group", "agente", "--group=universo"])
        self.assertEqual(plan.groups, ("agente", "universo"))

    def test_the_guard_group_is_never_run_twice(self) -> None:
        names = [spec.name for spec in command_specs(groups=(GUARD_GROUP,))]
        self.assertEqual(names.count(f"grupo {GUARD_GROUP}"), 1)

    def test_list_groups_asks_for_nothing_to_run(self) -> None:
        plan = parse_args(["--list-groups"])
        self.assertTrue(plan.list_groups)
        self.assertFalse(plan.include_full_suite)

    def test_an_unknown_group_fails_before_launching_anything(self) -> None:
        with self.assertRaises(ValueError):
            parse_args(["--group", "no_existe"])

    def test_a_group_flag_without_a_name_fails(self) -> None:
        with self.assertRaises(ValueError):
            parse_args(["--group"])

    def test_unknown_arguments_fail(self) -> None:
        with self.assertRaises(ValueError):
            parse_args(["--silenciar-fallos"])


if __name__ == "__main__":
    unittest.main()
