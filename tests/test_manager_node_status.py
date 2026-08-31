from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import JobController, NodeServer


class ManagerNodeStatusTests(unittest.TestCase):
    def test_http_status_and_logs_remain_available_during_repair_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = JobController({"project_dir": temp, "node_id": "test", "token": "test"}, root / "node.json")
            log = root / "repair.log"
            log.write_text("repair in progress\n", encoding="utf-8")
            controller.state.update(status="running", log_path=str(log),
                                    pipeline=[{"action": "robustness", "run_id": 1}], request={})
            controller._persist()
            entered, release = threading.Event(), threading.Event()
            errors = []

            def pending(*args):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test preflight release timed out")
                return 0

            def prepare():
                try:
                    with controller.lock:
                        controller._launch_next_runnable(0, log)
                except Exception as exc:
                    errors.append(exc)

            server = NodeServer(("127.0.0.1", 0), controller)
            http_thread = threading.Thread(target=server.serve_forever, daemon=True)
            http_thread.start()
            try:
                with patch("manager_node_runtime.node.pipeline_stage_pending_count", side_effect=pending):
                    worker = threading.Thread(target=prepare)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        values = {}
                        for route in ("status", "logs"):
                            request = urllib.request.Request(
                                f"http://127.0.0.1:{server.server_address[1]}/api/v1/{route}",
                                headers={"Authorization": "Bearer test"},
                            )
                            with urllib.request.urlopen(request, timeout=1) as response:
                                values[route] = json.load(response)
                        self.assertEqual(values["status"]["job"]["status"], "running")
                        self.assertTrue(values["status"]["job_snapshot_stale"])
                        self.assertTrue(values["status"]["job_observed_at"])
                        self.assertIn("repair in progress", values["logs"]["lines"])
                    finally:
                        release.set()
                        worker.join(3)
                self.assertFalse(errors)
                current = controller.status()
                self.assertFalse(current["job_snapshot_stale"])
                self.assertEqual(len(current["job"]["skipped_stages"]), 1)
                current["job"]["pipeline"][0]["run_id"] = 999
                self.assertEqual(controller.status()["job"]["pipeline"][0]["run_id"], 1)
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                http_thread.join(2)

