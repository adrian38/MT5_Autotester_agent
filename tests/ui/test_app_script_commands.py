"""Los scripts que lanza la interfaz existen y se lanzan como toca.

El reparto de la raiz mueve scripts a su paquete, y la interfaz los nombra por
cadena: un nombre viejo no rompe ningun import y falla delante del usuario al
pulsar el boton. Aqui se comprueban las dos mitades, el nombre y la forma de la
orden.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

from tests import PROJECT_ROOT
from ui.app_process import AppProcessMixin

_LAUNCH = re.compile(r"_run_script\(\s*\"([^\"]+)\"")


def _launched_scripts() -> set[str]:
    """Los nombres que la interfaz pasa a `_run_script` como literal."""
    names: set[str] = set()
    for path in sorted((PROJECT_ROOT / "ui").glob("*.py")):
        names.update(_LAUNCH.findall(path.read_text(encoding="utf-8")))
    return names


class ScriptCommandTests(unittest.TestCase):
    def test_the_interface_launches_at_least_the_known_scripts(self) -> None:
        """Si el patron deja de encontrarlos, lo de abajo pasaria en vacio."""
        launched = _launched_scripts()
        self.assertIn("ubs_agent.py", launched)
        self.assertIn("ubs/set_rules_sweep.py", launched)

    def test_every_launched_script_exists(self) -> None:
        for name in sorted(_launched_scripts()):
            with self.subTest(name):
                self.assertTrue((PROJECT_ROOT / name).is_file(), name)

    def test_every_launched_script_has_a_main(self) -> None:
        for name in sorted(_launched_scripts()):
            with self.subTest(name):
                tree = ast.parse((PROJECT_ROOT / name).read_text(encoding="utf-8"))
                mains = [
                    node.name for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "main"
                ]
                self.assertEqual(mains, ["main"], name)

    def test_a_root_script_runs_by_path(self) -> None:
        command = AppProcessMixin._script_command(None, "ubs_agent.py", ["--x"])
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "ubs_agent.py")
        self.assertEqual(command[2:], ["--x"])

    def test_a_packaged_script_runs_as_a_module(self) -> None:
        """Por ruta, `sys.path[0]` seria `ubs/` y sus imports no resolverian."""
        command = AppProcessMixin._script_command(None, "ubs/set_rules_sweep.py", ["--dry-run"])
        self.assertEqual(command, [sys.executable, "-m", "ubs.set_rules_sweep", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
