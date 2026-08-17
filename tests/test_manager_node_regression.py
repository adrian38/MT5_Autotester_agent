from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import JobController, build_generation_command


class ManagerNodeRegressionTests(unittest.TestCase):
    def _controller(self, project: Path) -> JobController:
        return JobController(
            {"node_id": "ic", "project_dir": str(project)},
            project / "manager_node.json",
        )

    def test_regression_job_runs_only_regression_stage_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            worker = project / "worker.py"
            worker.write_text("", encoding="utf-8")
            controller = self._controller(project)
            command = [sys.executable, str(worker)]
            with patch(
                "manager_node_runtime.node.pipeline_stage_pending_count",
                return_value=1,
            ), patch(
                "manager_node_runtime.node.build_pipeline_stage_command",
                return_value=(command, project),
            ) as build_command:
                result = controller.start_regression({
                    "run_ids": [9, 7, 9],
                    "max_workers": 5,
                })
                deadline = time.time() + 5
                while time.time() < deadline:
                    if controller.status()["job"]["status"] != "running":
                        break
                    time.sleep(.03)

            self.assertFalse(result["queued"])
            self.assertEqual(result["job_type"], "regression")
            self.assertEqual(
                result["pipeline"],
                [
                    {"action": "regression", "cycle": None, "run_id": 9, "attempt": 1},
                    {"action": "regression", "cycle": None, "run_id": 7, "attempt": 1},
                ],
            )
            self.assertEqual(build_command.call_args.args[2:], ("regression", 7))
            self.assertEqual(build_command.call_args.args[1]["max_workers"], 5)
            self.assertEqual(controller.status()["job"]["status"], "completed")
            self.assertEqual(
                controller.status()["job"]["completed_stages"],
                ["run_9_attempt_1_regression", "run_7_attempt_1_regression"],
            )

    def test_regression_job_completes_when_nothing_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            with patch(
                "manager_node_runtime.node.pipeline_stage_pending_count",
                return_value=0,
            ):
                result = controller.start_regression({"run_ids": [4]})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["skipped_stages"], ["run_4_attempt_1_regression"])

    def test_regression_job_is_queued_while_the_node_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            worker = project / "worker.py"
            worker.write_text("import time\ntime.sleep(.15)\n", encoding="utf-8")
            controller = self._controller(project)
            command = [sys.executable, str(worker)]
            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=(command, project),
            ), patch(
                "manager_node_runtime.node.pipeline_stage_pending_count",
                return_value=0,
            ):
                controller.start({"cycles": 1, "dry_run": True})
                queued = controller.start_regression({"run_ids": [7]})

                self.assertTrue(queued["queued"])
                self.assertEqual(queued["queue_item"]["type"], "regression")

                deadline = time.time() + 5
                while time.time() < deadline:
                    status = controller.status()
                    if status["job"]["status"] != "running" and status["task_queue"]["count"] == 0:
                        break
                    time.sleep(.03)

            status = controller.status()
            self.assertEqual(status["job"]["job_type"], "regression")
            self.assertEqual(status["job"]["status"], "completed")

    def test_regression_payload_requires_valid_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = self._controller(Path(temp_dir))
            with self.assertRaises(ValueError):
                controller.start_regression({"run_ids": "7"})
            with self.assertRaises(ValueError):
                controller.start_regression({"run_ids": [0, -3]})

    def test_repair_and_regression_normalize_requested_worker_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = self._controller(Path(temp_dir))

            repair = controller._normalize_repair({
                "run_ids": [7],
                "max_workers": 6,
            })
            regression = controller._normalize_regression({
                "run_ids": [7],
                "max_workers": 8,
            })

            self.assertEqual(repair["max_workers"], 6)
            self.assertEqual(regression["max_workers"], 8)
            self.assertEqual(
                controller._normalize_repair({"run_ids": [7], "max_workers": 99})["max_workers"],
                64,
            )
            self.assertEqual(
                controller._normalize_regression({"run_ids": [7], "max_workers": 0})["max_workers"],
                1,
            )

    def test_manual_repair_appends_regression_only_for_production_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)

            def mode_for_run(_config: dict, run_id: int) -> str | None:
                if run_id == 7:
                    return "discovery"
                if run_id == 9:
                    return "production"
                return None

            with patch(
                "manager_node_runtime.node.stored_run_generation_mode",
                side_effect=mode_for_run,
            ), patch.object(controller, "_launch_next_runnable", return_value=True):
                result = controller.start_repair({
                    "run_ids": [7, 9, 11],
                    "repair_attempts": 1,
                    "retry_low_quality": False,
                    "cleanup_after_run": False,
                })

            discovery_actions = [
                step["action"] for step in result["pipeline"] if step["run_id"] == 7
            ]
            production_actions = [
                step["action"] for step in result["pipeline"] if step["run_id"] == 9
            ]
            unknown_actions = [
                step["action"] for step in result["pipeline"] if step["run_id"] == 11
            ]
            self.assertEqual(
                discovery_actions,
                ["result", "robustness", "final_tick", "final_tick_6m"],
            )
            self.assertEqual(production_actions, [*discovery_actions, "regression"])
            self.assertEqual(unknown_actions, discovery_actions)
            self.assertEqual(
                result["request"]["run_generation_modes"],
                {"7": "discovery", "9": "production", "11": "unknown"},
            )

    def test_manual_repair_can_skip_the_regression_stage(self) -> None:
        # La casilla «Prueba regresiva» del diálogo de Reparar envía
        # `run_regression`. Omitirlo mantiene el flujo anterior a la casilla.
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)

            def actions_for(payload: dict) -> list[str]:
                with patch(
                    "manager_node_runtime.node.stored_run_generation_mode",
                    return_value="production",
                ), patch.object(controller, "_launch_next_runnable", return_value=True):
                    result = controller.start_repair({
                        "run_ids": [9],
                        "repair_attempts": 1,
                        "retry_low_quality": False,
                        "cleanup_after_run": False,
                        **payload,
                    })
                return [step["action"] for step in result["pipeline"]]

            base = ["result", "robustness", "final_tick", "final_tick_6m"]
            self.assertEqual(actions_for({"run_regression": False}), base)
            self.assertEqual(actions_for({"run_regression": True}), [*base, "regression"])
            self.assertEqual(actions_for({}), [*base, "regression"])
            self.assertFalse(
                controller._normalize_repair({
                    "run_ids": [9], "run_regression": False,
                })["run_regression"],
            )
            self.assertTrue(
                controller._normalize_repair({"run_ids": [9]})["run_regression"],
            )

    def test_manual_repair_reads_the_persisted_run_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = project / "memory.sqlite"
            conn = sqlite3.connect(memory)
            try:
                conn.execute("create table runs (id integer primary key, config_json text)")
                conn.executemany(
                    "insert into runs(id, config_json) values (?, ?)",
                    [
                        (7, json.dumps({"generation": {"mode": "discovery"}})),
                        (9, json.dumps({"args": {"generation_mode": "production"}})),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            controller = JobController(
                {
                    "node_id": "ic",
                    "project_dir": str(project),
                    "memory_path": str(memory),
                },
                project / "manager_node.json",
            )

            with patch.object(controller, "_launch_next_runnable", return_value=True):
                result = controller.start_repair({
                    "run_ids": [7, 9],
                    "repair_attempts": 1,
                    "retry_low_quality": False,
                    "cleanup_after_run": False,
                })

            regression_runs = [
                step["run_id"] for step in result["pipeline"]
                if step["action"] == "regression"
            ]
            self.assertEqual(regression_runs, [9])

    def test_discovery_generation_never_schedules_regression(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            command = [sys.executable, str(project / "worker.py")]
            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=(command, project),
            ), patch.object(controller, "_launch_step"):
                result = controller.start({
                    "cycles": 1,
                    "generation_mode": "discovery",
                    "run_regression": True,
                })

            self.assertFalse(result["request"]["run_regression"])
            self.assertNotIn("regression", [step["action"] for step in result["pipeline"]])

    def test_production_generation_can_schedule_regression(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            command = [sys.executable, str(project / "worker.py")]
            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=(command, project),
            ), patch.object(controller, "_launch_step"):
                result = controller.start({
                    "cycles": 1,
                    "generation_mode": "production",
                    "run_regression": True,
                })

            self.assertTrue(result["request"]["run_regression"])
            self.assertIn("regression", [step["action"] for step in result["pipeline"]])

    def test_generation_forwards_and_normalizes_random_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            (project / "ubs_agent.py").write_text(
                'parser.add_argument("--random-seed")\n',
                encoding="utf-8",
            )
            (project / "tester_template.ini").write_text("[Tester]\n", encoding="utf-8")
            (project / "ui_settings.ini").write_text(
                "\n".join([
                    "[Paths]",
                    f"set_files_root={project / 'sets'}",
                    f"ubs_generation_output={project / 'outputs'}",
                    f"template_path={project / 'tester_template.ini'}",
                    "[General]",
                    "ubs_generation_mode=discovery",
                    "ubs_broker=ICTRADING",
                    "ubs_account_type=STANDARD",
                    "[Multiterminal]",
                    "enabled=0",
                ]),
                encoding="utf-8",
            )
            config = {
                "node_id": "ic",
                "project_dir": str(project),
                "broker": "ICTRADING",
                "account_type": "STANDARD",
            }
            controller = JobController(config, project / "manager_node.json")

            normalized = controller._normalize_generation({
                "random_seed": "20260812",
                "execute_backtests": False,
            })
            command, _cwd = build_generation_command(config, normalized)

            self.assertEqual(normalized["random_seed"], 20260812)
            self.assertEqual(command[command.index("--random-seed") + 1], "20260812")
            self.assertIsNone(controller._normalize_generation({"random_seed": None})["random_seed"])
            with self.assertRaisesRegex(ValueError, "random_seed"):
                controller._normalize_generation({"random_seed": "invalid"})

    def test_auto_repair_uses_its_own_worker_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            command = [sys.executable, str(project / "worker.py")]
            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=(command, project),
            ), patch.object(controller, "_launch_step"):
                result = controller.start({
                    "cycles": 1,
                    "max_workers": 7,
                    "repair_max_workers": 3,
                    "repair_after_generation": True,
                    "repair_attempts": 1,
                    "run_robustness": True,
                })

            self.assertEqual(result["request"]["max_workers"], 7)
            self.assertEqual(result["request"]["repair_max_workers"], 3)
            repair_steps = [
                step for step in result["pipeline"]
                if step["action"] != "generation"
            ]
            self.assertTrue(repair_steps)
            self.assertTrue(all(step["max_workers"] == 3 for step in repair_steps))

    def test_auto_repair_defaults_to_the_generation_worker_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            controller = self._controller(project)
            command = [sys.executable, str(project / "worker.py")]
            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=(command, project),
            ), patch.object(controller, "_launch_step"):
                result = controller.start({
                    "cycles": 1,
                    "max_workers": 7,
                    "repair_after_generation": True,
                    "repair_attempts": 1,
                    "run_robustness": True,
                })

            self.assertEqual(result["request"]["repair_max_workers"], 7)


if __name__ == "__main__":
    unittest.main()
