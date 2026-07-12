from __future__ import annotations

import configparser
import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path

from manager_node_lifecycle import EmbeddedManagerNode


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class EmbeddedManagerNodeTests(unittest.TestCase):
    def test_node_starts_and_stops_with_matching_app_project(self) -> None:
        app_base = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "node.json"
            token = "test-embedded-token"
            port = free_port()
            config_path.write_text(
                json.dumps(
                    {
                        "node_id": "embedded-test",
                        "display_name": "Embedded Test",
                        "project_dir": str(app_base),
                        "broker": "ICTRADING",
                        "account_type": "STANDARD",
                        "host": "127.0.0.1",
                        "port": port,
                        "token": token,
                    }
                ),
                encoding="utf-8",
            )
            settings = configparser.ConfigParser()
            settings["ManagerNode"] = {
                "enabled": "1",
                "config_file": str(config_path),
            }
            lifecycle = EmbeddedManagerNode(app_base, settings)
            self.assertTrue(lifecycle.start(), lifecycle.last_error)
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v1/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            lifecycle.stop()
            self.assertFalse(lifecycle.running)

    def test_node_rejects_config_for_another_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "node.json"
            config_path.write_text(
                json.dumps(
                    {
                        "node_id": "wrong",
                        "project_dir": str(root / "other"),
                        "host": "127.0.0.1",
                        "port": free_port(),
                        "token": "secret",
                    }
                ),
                encoding="utf-8",
            )
            settings = configparser.ConfigParser()
            settings["ManagerNode"] = {
                "enabled": "1",
                "config_file": str(config_path),
            }
            lifecycle = EmbeddedManagerNode(root, settings)
            self.assertFalse(lifecycle.start())
            self.assertIn("pertenece a otro proyecto", lifecycle.last_error)
