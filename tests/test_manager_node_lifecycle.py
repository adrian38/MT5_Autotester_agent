from __future__ import annotations

import configparser
import json
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from manager_node_lifecycle import EmbeddedManagerNode, relaunch_application


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
            restart_request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v1/application/restart",
                data=b"{}",
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(restart_request, timeout=3) as response:
                self.assertEqual(response.status, 202)
                self.assertEqual(json.loads(response.read())["status"], "restarting")
            self.assertTrue(lifecycle.restart_requested)
            self.assertTrue(lifecycle.consume_restart_request())
            self.assertFalse(lifecycle.restart_requested)
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

    def test_relaunch_uses_python_and_the_source_entrypoint(self) -> None:
        entrypoint = Path(__file__).resolve().parent.parent / "app_ui.py"
        with (
            mock.patch("manager_node_lifecycle.sys.frozen", False, create=True),
            mock.patch("manager_node_lifecycle.sys.argv", ["app_ui.py", "--sample"]),
            mock.patch("manager_node_lifecycle.sync_origin_before_relaunch") as sync_origin,
            mock.patch("manager_node_lifecycle.os.execv") as execv,
        ):
            relaunch_application(entrypoint)

        sync_origin.assert_called_once_with(entrypoint.resolve().parent)
        execv.assert_called_once_with(
            sys.executable,
            [sys.executable, str(entrypoint.resolve()), "--sample"],
        )

    def test_relaunch_pulls_then_pushes_current_branch_before_exec(self) -> None:
        branch_result = mock.Mock(returncode=0, stdout="IC\n", stderr="")
        pull_result = mock.Mock(returncode=0, stdout="Already up to date.\n", stderr="")
        push_result = mock.Mock(returncode=0, stdout="", stderr="Everything up-to-date\n")
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            entrypoint = project_dir / "app_ui.py"
            with (
                mock.patch("manager_node_lifecycle.sys.frozen", False, create=True),
                mock.patch("manager_node_lifecycle.sys.argv", ["app_ui.py"]),
                mock.patch(
                    "manager_node_lifecycle.subprocess.run",
                    side_effect=[branch_result, pull_result, push_result],
                ) as run,
                mock.patch("manager_node_lifecycle.os.execv") as execv,
            ):
                relaunch_application(entrypoint, project_dir)

            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ["git", "branch", "--show-current"],
                    ["git", "pull", "--ff-only", "origin", "IC"],
                    ["git", "push", "origin", "IC"],
                ],
            )
            self.assertTrue(
                all(call.kwargs["cwd"] == project_dir for call in run.call_args_list)
            )
            self.assertTrue(
                all(
                    call.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
                    for call in run.call_args_list
                )
            )
            log_text = (project_dir / "logs" / "manager_node_restart.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("git pull --ff-only origin IC: exit 0", log_text)
            self.assertIn("git push origin IC: exit 0", log_text)
            execv.assert_called_once_with(
                sys.executable,
                [sys.executable, str(entrypoint)],
            )

    def test_relaunch_still_execs_and_skips_push_when_pull_fails(self) -> None:
        branch_result = mock.Mock(returncode=0, stdout="IC\n", stderr="")
        pull_result = mock.Mock(returncode=1, stdout="", stderr="Not possible to fast-forward\n")
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            entrypoint = project_dir / "app_ui.py"
            with (
                mock.patch("manager_node_lifecycle.sys.frozen", False, create=True),
                mock.patch("manager_node_lifecycle.sys.argv", ["app_ui.py"]),
                mock.patch(
                    "manager_node_lifecycle.subprocess.run",
                    side_effect=[branch_result, pull_result],
                ) as run,
                mock.patch("manager_node_lifecycle.os.execv") as execv,
            ):
                relaunch_application(entrypoint, project_dir)

            self.assertEqual(len(run.call_args_list), 2)
            self.assertIn(
                "El pull fallo; no se ejecuta git push",
                (project_dir / "logs" / "manager_node_restart.log").read_text(encoding="utf-8"),
            )
            execv.assert_called_once()
