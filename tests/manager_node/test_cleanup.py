from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import (
    CLEANUP_STAGES,
    JobController,
    build_historical_cleanup_command,
    historical_cleanup_scripts,
)

TERMINALS = r"""[Multiterminal]
enabled=1
broker=ICTRADING

[Terminal.1]
enabled=1
broker=ROBOFOREX
name=MT5 principal
mt5_path=C:\Program Files\RoboForex MT5 Terminal\terminal64.exe
data_dir=C:\MT5\OTRO

[Terminal.2]
enabled=1
broker=ICTRADING
name=MT5_IC_1
mt5_path=C:\Program Files\IC\terminal64.exe
data_dir=C:\MT5\IC_1

[Terminal.3]
enabled=0
broker=ICTRADING
name=MT5_IC_2
mt5_path=C:\Program Files\IC\terminal64.exe
data_dir=C:\MT5\IC_2
"""
"""Las terminales tal y como las declara el `ui_settings.ini` del agente.

El `[Terminal.1]` de RoboForex no esta de adorno: ese mismo lo arrastran el
`ui_settings.ini` de IC y el de AXI, y es el que la limpieza no debe tocar."""


class ManagerNodeCleanupTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        scripts = root / "scripts"
        scripts.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts / filename).write_text("Write-Host clean\n", encoding="utf-8")
        (root / "ui_settings.ini").write_text(TERMINALS, encoding="utf-8")

    def _controller(self, project: Path) -> JobController:
        return JobController(
            {"node_id": "ic", "project_dir": str(project)},
            project / "manager_node.json",
        )

    def test_cleanup_uses_the_same_scripts_as_the_agent_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            config = {"project_dir": str(project), "broker": "ICTRADING"}

            scripts = historical_cleanup_scripts(config)
            tester_command, cwd = build_historical_cleanup_command(config, "cleanup_tester")
            verify_command, _ = build_historical_cleanup_command(config, "cleanup_verify")

            self.assertEqual(cwd, project)
            self.assertEqual(scripts["cleanup_tester"], project / "scripts" / "cleanOldTest.ps1")
            self.assertEqual(scripts["cleanup_data"], project / "scripts" / "cleanOlddata.ps1")
            self.assertEqual(tester_command[5], str(scripts["cleanup_tester"]))
            self.assertEqual(verify_command[:2], [sys.executable, "-c"])

    def test_the_cleanup_only_reaches_the_terminals_of_this_broker(self) -> None:
        """Lo que fallo el 2026-11-17: IC borraba la cache del Tester de AXI.

        El criterio era `%APPDATA%\\MetaQuotes` entero, asi que este agente
        entraba en las carpetas de los otros -que corren en otra sesion de
        Windows y las tienen abiertas-, no podia borrarlas y `cleanup_verify`
        marcaba el trabajo como fallido. Ahora las carpetas salen del
        `ui_settings.ini` y son solo las de su broker.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            config = {"project_dir": str(project), "broker": "ICTRADING"}

            tester_command, _ = build_historical_cleanup_command(config, "cleanup_tester")
            verify_command, _ = build_historical_cleanup_command(config, "cleanup_verify")
            data_dirs = tester_command[tester_command.index("-DataDirs") + 1]
            executables = tester_command[tester_command.index("-TerminalPaths") + 1]

            self.assertEqual(data_dirs.split("|"), [r"C:\MT5\IC_1", r"C:\MT5\IC_2"])
            self.assertEqual(executables.split("|"), [r"C:\Program Files\IC\terminal64.exe"])
            self.assertEqual(verify_command[3:], [r"C:\MT5\IC_1", r"C:\MT5\IC_2"])
            self.assertNotIn(r"C:\MT5\OTRO", tester_command + verify_command)
            self.assertNotIn("MetaQuotes", "".join(verify_command[2:]))

    def test_an_agent_without_terminals_of_its_own_refuses_to_clean(self) -> None:
        """Sin terminales declaradas no se limpia «todo»: no se limpia nada."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            config = {"project_dir": str(project), "broker": "VANTAGE"}

            for stage in CLEANUP_STAGES:
                with self.subTest(stage=stage), self.assertRaises(ValueError) as error:
                    build_historical_cleanup_command(config, stage)
                self.assertIn("VANTAGE", str(error.exception))

    def test_generation_cleans_after_every_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            controller = self._controller(project)
            command = [sys.executable, str(project / "worker.py")]

            with patch(
                "manager_node_runtime.node_commands.build_generation_command",
                return_value=(command, project),
            ), patch.object(controller, "_launch_step"):
                state = controller.start({"cycles": 2})

            self.assertTrue(state["request"]["cleanup_after_run"])
            self.assertEqual(
                [step["action"] for step in state["pipeline"]],
                ["generation", *CLEANUP_STAGES, "generation", *CLEANUP_STAGES],
            )

    def test_manual_repair_and_regression_clean_after_each_selected_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            controller = self._controller(project)

            with patch(
                "manager_node_runtime.node_settings.stored_run_generation_mode",
                return_value="production",
            ), patch.object(controller, "_launch_next_runnable", return_value=True):
                repair = controller.start_repair({
                    "run_ids": [7, 9],
                    "repair_attempts": 1,
                    "retry_low_quality": False,
                })

            repair_actions = ["result", "robustness", "final_tick", "final_tick_6m", "regression"]
            # El reintento es por run: sus dos fases y su limpieza se terminan
            # antes de empezar el siguiente run.
            self.assertEqual(
                [(step["run_id"], step["action"]) for step in repair["pipeline"]],
                [
                    *((7, action) for action in (*repair_actions, *repair_actions, *CLEANUP_STAGES)),
                    *((9, action) for action in (*repair_actions, *repair_actions, *CLEANUP_STAGES)),
                ],
            )
            self.assertEqual(
                repair["request"]["run_generation_modes"],
                {"7": "production", "9": "production"},
            )

            controller.state["status"] = "completed"
            with patch.object(controller, "_launch_next_runnable", return_value=True):
                regression = controller.start_regression({"run_ids": [11, 12]})

            self.assertEqual(
                [(step["run_id"], step["action"]) for step in regression["pipeline"]],
                [
                    (11, "regression"),
                    *((11, action) for action in CLEANUP_STAGES),
                    (12, "regression"),
                    *((12, action) for action in CLEANUP_STAGES),
                ],
            )
            self.assertEqual(
                controller._step_label({"action": "cleanup_tester", "cycle": None, "run_id": 11}),
                "run_11_cleanup_tester",
            )

    def test_manual_cleanup_is_queueable_and_advertised(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            controller = self._controller(project)

            with patch.object(controller, "_launch_next_runnable", return_value=True):
                state = controller.start_cleanup()

            self.assertEqual(state["job_type"], "cleanup")
            self.assertEqual([step["action"] for step in state["pipeline"]], list(CLEANUP_STAGES))
            self.assertTrue(controller.status()["capabilities"]["historical_cleanup"])

    def test_cleanup_failure_finishes_remaining_cleanup_and_blocks_next_run(self) -> None:
        class FinishedProcess:
            def wait(self) -> int:
                return 1

        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            controller = self._controller(project)
            process = FinishedProcess()
            log_path = project / "cleanup.log"
            controller.process = process  # type: ignore[assignment]
            controller.state = {
                "job_id": "cleanup-test",
                "job_type": "regression",
                "status": "running",
                "request": {},
                "log_path": str(log_path),
                "pipeline": [
                    {"action": "cleanup_tester", "cycle": None, "run_id": 11},
                    {"action": "cleanup_data", "cycle": None, "run_id": 11},
                    {"action": "cleanup_verify", "cycle": None, "run_id": 11},
                    {"action": "regression", "cycle": None, "run_id": 12, "attempt": 1},
                ],
                "completed_stages": [],
                "stage_return_codes": {},
                "telegram_notifications": [],
                "cleanup_failed": False,
            }

            with patch.object(controller, "_notify_stage_completion"), patch.object(
                controller, "_launch_next_runnable", return_value=True,
            ) as launch_next:
                controller._watch(process, 0)  # type: ignore[arg-type]

            self.assertTrue(controller.state["cleanup_failed"])
            launch_next.assert_called_once()
            self.assertEqual(launch_next.call_args.args[0], 1)

            process = FinishedProcess()
            controller.process = process  # type: ignore[assignment]
            controller.state["pipeline"] = [
                {"action": "cleanup_verify", "cycle": None, "run_id": 11},
                {"action": "regression", "cycle": None, "run_id": 12, "attempt": 1},
            ]
            controller.state["cleanup_failed"] = False
            controller.state["status"] = "running"
            with patch.object(controller, "_notify_stage_completion"), patch.object(
                controller, "_launch_next_runnable",
            ) as launch_next:
                controller._watch(process, 0)  # type: ignore[arg-type]

            self.assertEqual(controller.state["status"], "failed")
            self.assertEqual(controller.state["return_code"], 1)
            launch_next.assert_not_called()


if __name__ == "__main__":
    unittest.main()
