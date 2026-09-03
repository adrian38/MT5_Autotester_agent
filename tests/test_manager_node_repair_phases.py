"""La reparación se parte en dos fases por intento.

Lo único que las diferencia es cuántos terminales usa cada una a la vez: la fase 1
en paralelo y la fase 2 sobre lo que la fase 1 haya dejado pendiente, porque todas
las etapas son «pending-only». El manager solo manda los dos límites
(`max_workers` y `repair_phase2_max_workers`); quien construye el pipeline es este
nodo, así que sin estas pruebas el campo del diálogo podría no hacer nada.

Copia de la cobertura de `tests/test_node.py` del manager, exigida por
`tests/test_node_runtime_fork_parity.py`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import JobController

REPAIR_ACTIONS = ("result", "robustness", "final_tick", "final_tick_6m")


class ManagerNodeRepairPhasesTests(unittest.TestCase):
    def _controller(self, project: Path) -> JobController:
        return JobController(
            {"node_id": "ic", "project_dir": str(project)},
            project / "manager_node.json",
        )

    def _repair(self, payload: dict) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            with patch(
                "manager_node_runtime.node.stored_run_generation_mode",
                return_value="discovery",
            ), patch(
                "manager_node_runtime.node.pipeline_stage_pending_count",
                return_value=0,
            ):
                return controller.start_repair(payload)

    def test_each_attempt_runs_both_phases_with_its_own_terminal_limit(self) -> None:
        result = self._repair({
            "run_ids": [12],
            "max_workers": 6,
            "repair_phase2_max_workers": 2,
            "repair_attempts": 2,
            "retry_low_quality": False,
        })

        self.assertEqual(result["request"]["max_workers"], 6)
        self.assertEqual(result["request"]["repair_phase2_max_workers"], 2)
        self.assertEqual(
            [
                (step["attempt"], step["phase"], step["max_workers"])
                for step in result["pipeline"]
            ],
            [
                (attempt, phase, workers)
                for attempt in (1, 2)
                for phase, workers in ((1, 6), (2, 2))
                for _action in REPAIR_ACTIONS
            ],
        )

    def test_the_attempt_belongs_to_the_selected_run(self) -> None:
        # El reintento es por run seleccionado: cada run agota sus reintentos y sus
        # dos fases antes de que empiece el siguiente.
        result = self._repair({
            "run_ids": [7, 9, 11],
            "max_workers": 5,
            "repair_phase2_max_workers": 1,
            "repair_attempts": 2,
            "retry_low_quality": False,
        })

        self.assertEqual(
            [
                (step["run_id"], step["attempt"], step["phase"], step["max_workers"])
                for step in result["pipeline"] if step["action"] == "result"
            ],
            [
                (run_id, attempt, phase, workers)
                for run_id in (7, 9, 11)
                for attempt in (1, 2)
                for phase, workers in ((1, 5), (2, 1))
            ],
        )

    def test_the_phase_belongs_to_the_stage_key(self) -> None:
        # Sin la fase en la clave, la segunda pasada pisaría el código de retorno,
        # el comando y el recuento de pendientes de la primera.
        result = self._repair({
            "run_ids": [12],
            "max_workers": 4,
            "repair_phase2_max_workers": 1,
            "repair_attempts": 1,
            "retry_low_quality": False,
        })

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["skipped_stages"],
            [
                f"run_12_attempt_1_phase_{phase}_{action}"
                for phase in (1, 2)
                for action in REPAIR_ACTIONS
            ],
        )

    def test_an_older_manager_still_gets_a_sequential_second_phase(self) -> None:
        # Un manager que no conozca el campo no debe quedarse en una sola pasada:
        # la fase 2 existe igual y por omisión recoge con un solo terminal.
        result = self._repair({
            "run_ids": [12],
            "max_workers": 8,
            "repair_attempts": 1,
            "retry_low_quality": False,
        })

        self.assertEqual(result["request"]["repair_phase2_max_workers"], 1)
        self.assertEqual(
            [
                (step["phase"], step["max_workers"])
                for step in result["pipeline"] if step["action"] == "result"
            ],
            [(1, 8), (2, 1)],
        )

    def test_the_automatic_repair_after_a_run_also_runs_both_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=([sys.executable, "-c", "pass"], project),
            ), patch.object(controller, "_launch_step"):
                state = controller.start({
                    "cycles": 1,
                    "max_workers": 7,
                    "repair_max_workers": 3,
                    "repair_phase2_max_workers": 1,
                    "repair_after_generation": True,
                    "repair_attempts": 1,
                    "run_robustness": True,
                    "run_final_tick": False,
                    "run_final_tick_6m": False,
                    "run_regression": False,
                    "cleanup_after_run": False,
                })

        self.assertEqual(state["request"]["repair_max_workers"], 3)
        self.assertEqual(state["request"]["repair_phase2_max_workers"], 1)
        self.assertEqual(
            [
                (step["action"], step["phase"], step["max_workers"])
                for step in state["pipeline"] if step["action"] != "generation"
            ],
            [
                (action, phase, workers)
                for phase, workers in ((1, 3), (2, 1))
                for action in ("result", "robustness")
            ],
        )


if __name__ == "__main__":
    unittest.main()
