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


def write_embedded_config(path: Path, app_base: Path, port: int, token: str) -> None:
    path.write_text(
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


def health_status(port: int, token: str) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return int(response.status)


class EmbeddedManagerNodeTests(unittest.TestCase):
    def test_node_starts_and_stops_with_matching_app_project(self) -> None:
        app_base = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "node.json"
            token = "test-embedded-token"
            port = free_port()
            write_embedded_config(config_path, app_base, port, token)
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
            with lifecycle.controller.lock:
                lifecycle.controller.state.update(
                    {
                        "status": "paused",
                        "pipeline": [{"action": "generation"}],
                        "current_step_index": 0,
                        "paused_at": "2026-08-20T20:36:39+02:00",
                    }
                )
                lifecycle.controller._persist()
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
            self.assertEqual(lifecycle.controller.state["status"], "paused")
            self.assertEqual(lifecycle.controller.state["current_step_index"], 0)
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

    def started_lifecycle(self, temp: str, port: int, token: str) -> EmbeddedManagerNode:
        app_base = Path(__file__).resolve().parent.parent
        config_path = Path(temp) / "node.json"
        write_embedded_config(config_path, app_base, port, token)
        settings = configparser.ConfigParser()
        settings["ManagerNode"] = {"enabled": "1", "config_file": str(config_path)}
        lifecycle = EmbeddedManagerNode(app_base, settings)
        self.assertTrue(lifecycle.start(), lifecycle.last_error)
        self.addCleanup(lifecycle.stop)
        return lifecycle

    @staticmethod
    def kill_serving_thread(lifecycle: EmbeddedManagerNode) -> None:
        """Deja el nodo como quedo el 2026-11-01: sin hilo y con socket abierto.

        `shutdown()` saca al hilo de `serve_forever` sin cerrar el socket, que
        es exactamente lo que deja una excepcion sin atrapar dentro del bucle.
        """
        lifecycle.server.shutdown()
        lifecycle.thread.join(timeout=5)

    def test_supervision_serves_again_when_the_node_thread_dies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            port, token = free_port(), "test-supervision-token"
            lifecycle = self.started_lifecycle(temp, port, token)
            self.assertEqual(health_status(port, token), 200)
            controller = lifecycle.controller
            self.kill_serving_thread(lifecycle)
            self.assertFalse(lifecycle.running)

            self.assertTrue(lifecycle.supervise())

            self.assertTrue(lifecycle.running)
            self.assertEqual(health_status(port, token), 200)
            # El controlador se conserva: uno nuevo sobre el mismo `state.json`
            # serian dos escritores para la generacion que siguiera en marcha.
            self.assertIs(lifecycle.controller, controller)

    def test_supervision_leaves_a_live_node_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            port, token = free_port(), "test-supervision-token"
            lifecycle = self.started_lifecycle(temp, port, token)
            thread, server = lifecycle.thread, lifecycle.server

            self.assertFalse(lifecycle.supervise())

            self.assertIs(lifecycle.thread, thread)
            self.assertIs(lifecycle.server, server)
            self.assertEqual(health_status(port, token), 200)

    def test_supervision_does_not_resurrect_a_node_that_was_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            port, token = free_port(), "test-supervision-token"
            lifecycle = self.started_lifecycle(temp, port, token)
            lifecycle.stop()

            self.assertFalse(lifecycle.supervise())

            self.assertIsNone(lifecycle.server)
            self.assertIsNone(lifecycle.address)

    def test_supervision_reports_a_failed_relaunch_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            port, token = free_port(), "test-supervision-token"
            lifecycle = self.started_lifecycle(temp, port, token)
            self.kill_serving_thread(lifecycle)
            lines: list[str] = []
            with (
                mock.patch.object(lifecycle, "_log", lines.append),
                mock.patch.object(
                    lifecycle, "_serve", side_effect=OSError("puerto ocupado")
                ),
            ):
                self.assertFalse(lifecycle.supervise())
                self.assertFalse(lifecycle.supervise())
            # Reintenta cada vuelta, pero no repite la linea cada cinco segundos.
            errors = [line for line in lines if line.startswith("ERROR")]
            self.assertEqual(errors, ["ERROR al volver a escuchar el nodo: puerto ocupado"])

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

    def run_relaunch(self, project_dir: Path, results: list[mock.Mock]) -> mock.Mock:
        entrypoint = project_dir / "app_ui.py"
        with (
            mock.patch("manager_node_lifecycle.sys.frozen", False, create=True),
            mock.patch("manager_node_lifecycle.sys.argv", ["app_ui.py"]),
            mock.patch(
                "manager_node_lifecycle.subprocess.run", side_effect=results
            ) as run,
            mock.patch("manager_node_lifecycle.os.execv") as execv,
        ):
            relaunch_application(entrypoint, project_dir)
        execv.assert_called_once_with(sys.executable, [sys.executable, str(entrypoint)])
        return run

    @staticmethod
    def restart_log(project_dir: Path) -> str:
        return (project_dir / "logs" / "manager_node_restart.log").read_text(encoding="utf-8")

    def test_relaunch_fetches_then_pushes_current_branch_before_exec(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr="From github.com/adrian38\n"),
            mock.Mock(returncode=0, stdout="0\t1\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr="To github.com/adrian38\n"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ["git", "branch", "--show-current"],
                    ["git", "fetch", "origin", "IC"],
                    ["git", "rev-list", "--left-right", "--count", "FETCH_HEAD...HEAD"],
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
            log_text = self.restart_log(project_dir)
            self.assertIn("0 commit(s) en origin por traer, 1 local(es) por publicar", log_text)
            self.assertIn("git push origin IC (intento 1): exit 0", log_text)

    def test_relaunch_fast_forwards_when_only_origin_moved(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="2\t0\n", stderr=""),
            mock.Mock(returncode=0, stdout="Updating c53f081..80a7a30\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr="Everything up-to-date\n"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertIn(
                ["git", "merge", "--ff-only", "FETCH_HEAD"],
                [call.args[0] for call in run.call_args_list],
            )

    def test_relaunch_rebases_local_commits_when_the_branch_diverged(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="3\t1\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="Successfully rebased\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr="To github.com/adrian38\n"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ["git", "branch", "--show-current"],
                    ["git", "fetch", "origin", "IC"],
                    ["git", "rev-list", "--left-right", "--count", "FETCH_HEAD...HEAD"],
                    ["git", "status", "--porcelain", "--untracked-files=no"],
                    ["git", "rebase", "FETCH_HEAD"],
                    ["git", "push", "origin", "IC"],
                ],
            )

    def test_relaunch_aborts_a_conflicting_rebase_and_skips_push(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="3\t1\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="CONFLICT (content)\n"),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertEqual(
                [call.args[0] for call in run.call_args_list][-2:],
                [
                    ["git", "rebase", "FETCH_HEAD"],
                    ["git", "rebase", "--abort"],
                ],
            )
            self.assertIn("El rebase fallo; no se ejecuta git push", self.restart_log(project_dir))

    def test_relaunch_retries_the_push_after_a_rejection(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="0\t1\n", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="! [remote rejected] IC -> IC (failed)\n"),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="0\t1\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr="To github.com/adrian38\n"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertEqual(
                [call.args[0] for call in run.call_args_list].count(
                    ["git", "push", "origin", "IC"]
                ),
                2,
            )
            log_text = self.restart_log(project_dir)
            self.assertIn("git push origin IC (intento 1): exit 1", log_text)
            self.assertIn("git push origin IC (intento 2): exit 0", log_text)

    def test_relaunch_still_execs_and_skips_push_when_fetch_fails(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="fatal: bad object refs/remotes/origin/HEAD\n"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertEqual(len(run.call_args_list), 2)
            self.assertIn("El fetch fallo; no se ejecuta git push", self.restart_log(project_dir))

    def test_relaunch_leaves_a_dirty_diverged_checkout_untouched(self) -> None:
        results = [
            mock.Mock(returncode=0, stdout="IC\n", stderr=""),
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=0, stdout="3\t1\n", stderr=""),
            mock.Mock(returncode=0, stdout=" M app_ui.py\n", stderr=""),
        ]
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp).resolve()
            run = self.run_relaunch(project_dir, results)

            self.assertEqual(len(run.call_args_list), 4)
            self.assertIn(
                "Hay cambios sin commitear y la rama diverge",
                self.restart_log(project_dir),
            )
