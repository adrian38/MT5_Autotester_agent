from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import JobController


class ManagerNodeQueueTests(unittest.TestCase):
    def test_generation_queue_is_fifo_persistent_and_cancellable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            worker = project / "worker.py"
            worker.write_text("import time\ntime.sleep(.15)\n", encoding="utf-8")
            controller = JobController(
                {"node_id": "ic", "project_dir": str(project)},
                project / "manager_node.json",
            )
            payload = {
                "cycles": 1,
                "generations": 1,
                "max_seeds": 1,
                "execute_backtests": False,
                "dry_run": True,
            }
            command = [sys.executable, str(worker)]

            with patch(
                "manager_node_runtime.node.build_generation_command",
                return_value=(command, project),
            ):
                first = controller.start({**payload, "variants_per_seed": 1})
                second = controller.start({**payload, "variants_per_seed": 2})
                third = controller.start({**payload, "variants_per_seed": 3})

                self.assertFalse(first["queued"])
                self.assertEqual(second["queue_item"]["position"], 1)
                self.assertEqual(third["queue_item"]["position"], 2)
                stored = json.loads(controller.queue_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    [item["payload"]["variants_per_seed"] for item in stored],
                    [2, 3],
                )

                cancelled = controller.cancel_queued(second["queue_item"]["id"])
                self.assertEqual(cancelled["task_queue"]["count"], 1)
                self.assertEqual(cancelled["task_queue"]["items"][0]["position"], 1)

                deadline = time.time() + 5
                while time.time() < deadline:
                    status = controller.status()
                    if status["job"]["status"] != "running" and status["task_queue"]["count"] == 0:
                        break
                    time.sleep(.03)

            status = controller.status()
            self.assertEqual(status["job"]["status"], "completed")
            self.assertEqual(status["job"]["request"]["variants_per_seed"], 3)
            self.assertEqual(status["task_queue"]["count"], 0)
            self.assertEqual(json.loads(controller.queue_path.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
