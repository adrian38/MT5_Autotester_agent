"""Detener y pausar no pueden depender de conseguir `self.lock`.

El bucle que descarta etapas sin candidatos pendientes retiene el bloqueo y hace
una consulta a SQLite por etapa: en una reparación de cien runs lo tiene minutos
enteros. Con `stop()` pidiendo ese mismo bloqueo, el POST del manager expiraba,
el estado se quedaba en `running` y el trabajo seguía como si nadie hubiera
pulsado el botón (2026-09-03, run de reparación de 4800 etapas).

Copia de la cobertura de `tests/test_node.py` del manager, exigida por
`tests/test_node_runtime_fork_parity.py`.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import JobController


class ManagerNodeStopTests(unittest.TestCase):
    def _controller(self, project: Path) -> JobController:
        controller = JobController(
            {"node_id": "ic", "project_dir": str(project)},
            project / "manager_node.json",
        )
        controller.state.update({
            "status": "running",
            "job_type": "repair",
            "log_path": str(project / "repair.log"),
            "pipeline": [
                {"action": "result", "cycle": None, "run_id": run_id,
                 "attempt": 1, "phase": 1, "max_workers": 5}
                for run_id in (7, 9, 11)
            ],
            "current_step_index": 0,
        })
        return controller

    def _while_the_lock_is_busy(self, controller: JobController, call) -> dict:
        held = threading.Event()
        released = threading.Event()

        def hold_the_lock() -> None:
            with controller.lock:
                held.set()
                released.wait(5)

        keeper = threading.Thread(target=hold_the_lock, daemon=True)
        keeper.start()
        self.assertTrue(held.wait(5))
        try:
            return call()
        finally:
            released.set()
            keeper.join(5)

    def test_stop_answers_without_the_lock_and_the_pipeline_obeys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)

            state = self._while_the_lock_is_busy(controller, controller.stop)

            self.assertEqual(state["status"], "stopping")
            self.assertTrue(controller.stop_requested)

            with patch.object(controller, "_launch_step") as launch:
                self.assertTrue(controller._launch_next_runnable(
                    0, Path(str(controller.state["log_path"])),
                ))
            self.assertFalse(launch.called)
            self.assertEqual(controller.state["status"], "stopped")
            self.assertIsNone(controller.state["current_step_index"])
            self.assertFalse(controller.stop_requested)
            self.assertIn(
                "Pipeline detenido a peticion del usuario",
                Path(str(controller.state["log_path"])).read_text(encoding="utf-8"),
            )

    def test_pause_answers_without_the_lock_and_keeps_the_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)

            state = self._while_the_lock_is_busy(controller, controller.pause)

            self.assertEqual(state["status"], "pausing")
            self.assertTrue(controller.pause_requested)

            with patch.object(controller, "_launch_step") as launch:
                self.assertTrue(controller._launch_next_runnable(
                    1, Path(str(controller.state["log_path"])),
                ))
            self.assertFalse(launch.called)
            self.assertEqual(controller.state["status"], "paused")
            # La posición se conserva para reanudar en esa misma etapa.
            self.assertEqual(controller.state["current_step_index"], 1)
            self.assertFalse(controller.pause_requested)

    def test_stopping_a_live_stage_ends_as_stopped_not_as_failed(self) -> None:
        # Mismo botón, mismo resultado: cortando una etapa en marcha o cortando
        # entre etapas, el trabajo queda detenido, no fallido.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            controller.stop_requested = True

            class DeadProcess:
                returncode = 1

                def wait(self) -> int:
                    return 1

            process = DeadProcess()
            controller.process = process
            controller._watch(process, 0)

            self.assertEqual(controller.state["status"], "stopped")
            self.assertFalse(controller.stop_requested)

    def test_a_pending_stop_never_survives_into_the_next_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            controller.stop_requested = True

            controller._complete(0)

            self.assertFalse(controller.stop_requested)
            self.assertEqual(controller.state["status"], "completed")


if __name__ == "__main__":
    unittest.main()
