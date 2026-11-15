from __future__ import annotations

import unittest

from tools.verify_project import command_specs, parse_args


class VerifyProjectTests(unittest.TestCase):
    def test_default_plan_contains_every_layer(self) -> None:
        names = [spec.name for spec in command_specs()]
        self.assertEqual(
            names,
            ["nombres", "funciones", "ficheros", "contexto", "protocolo",
             "guardas", "suite IC"],
        )

    def test_quick_only_omits_the_full_suite(self) -> None:
        names = [spec.name for spec in command_specs(
            include_full_suite=parse_args(["--quick"]),
        )]
        self.assertNotIn("suite IC", names)
        self.assertIn("guardas", names)

    def test_unknown_arguments_fail(self) -> None:
        with self.assertRaises(ValueError):
            parse_args(["--silenciar-fallos"])


if __name__ == "__main__":
    unittest.main()
