from __future__ import annotations

import unittest
from tests import PROJECT_ROOT


class ProjectRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    def test_rules_name_the_enforced_workflow(self) -> None:
        for required in (
            "## Matriz de escritura por repositorio y rama",
            "python -m tools.verify_project",
            "## Tamaño y legibilidad del código",
            "## Definición de terminado y entrega",
            "ALLOWED_STAR_IMPORTS",
        ):
            self.assertIn(required, self.rules)

    def test_ic_runtime_and_operational_safety_are_explicit(self) -> None:
        for required in (
            "manager_node_runtime/",
            "No lanzar MT5",
            "reports/`, `logs/`, `configs/` y `outputs/",
            "manager_node_runtime/guided_batches.py",
            "idénticos byte a byte",
        ):
            self.assertIn(required, self.rules)


if __name__ == "__main__":
    unittest.main()
