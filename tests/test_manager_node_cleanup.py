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


class ManagerNodeCleanupTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        scripts = root / "scripts"
        scripts.mkdir()
        for filename in ("cleanOldTest.ps1", "cleanOlddata.ps1"):
            (scripts / filename).write_text("Write-Host clean\n", encoding="utf-8")

    def _controller(self, project: Path) -> JobController:
        return JobController(
            {"node_id": "ic", "project_dir": str(project)},
            project / "manager_node.json",
        )

    def test_cleanup_uses_the_same_scripts_as_the_agent_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            config = {"project_dir": str(project)}

            scripts = historical_cleanup_scripts(config)
            tester_command, cwd = build_historical_cleanup_command(config, "cleanup_tester")
            verify_command, _ = build_historical_cleanup_command(config, "cleanup_verify")

            self.assertEqual(cwd, project)
            self.assertEqual(scripts["cleanup_tester"], project / "scripts" / "cleanOldTest.ps1")
            self.assertEqual(scripts["cleanup_data"], project / "scripts" / "cleanOlddata.ps1")
            self.assertEqual(tester_command[-1], str(scripts["cleanup_tester"]))
            self.assertEqual(verify_command[:2], [sys.executable, "-c"])

    def test_generation_cleans_after_every_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            self._project(project)
            controller = self._controller(project)
            command = [sys.executable, str(project / "worker.py")]

            with patch(
                "manager_node_runtime.node.build_generation_command",
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
                "manager_node_runtime.node.stored_run_generation_mode",
                return_value="production",
            ), patch.object(controller, "_launch_next_runnable", return_value=True):
                repair = controller.start_repair({
                    "run_ids": [7, 9],
                    "repair_attempts": 1,
                    "retry_low_quality": False,
                })

            repair_actions = ["result", "robustness", "final_tick", "final_tick_6m", "regression"]
            self.assertEqual(
                [(step["run_id"], step["action"]) for step in repair["pipeline"]],
                [
                    *((7, action) for action in (*repair_actions, *CLEANUP_STAGES)),
                    *((9, action) for action in (*repair_actions, *CLEANUP_STAGES)),
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
