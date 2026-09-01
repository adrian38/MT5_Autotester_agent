from __future__ import annotations

import configparser
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on", "si"}
GIT_SYNC_TIMEOUT_SECONDS = 300


class EmbeddedManagerNode:
    """Owns the manager node server inside the Tk application process."""

    def __init__(
        self,
        app_base_dir: Path,
        settings: configparser.ConfigParser,
    ) -> None:
        self.app_base_dir = app_base_dir.resolve()
        section = settings["ManagerNode"] if settings.has_section("ManagerNode") else {}
        configured_enabled = str(section.get("enabled", "")).strip().lower()
        config_value = str(section.get("config_file", "")).strip()
        env_config = os.environ.get("MT5_MANAGER_NODE_CONFIG", "").strip()
        if config_value or env_config:
            self.config_path = Path(config_value or env_config).expanduser()
        else:
            self.config_path = self.app_base_dir / "manager_node.json"
        if not self.config_path.is_absolute():
            self.config_path = self.app_base_dir / self.config_path
        self.enabled = (
            configured_enabled in TRUE_VALUES
            if configured_enabled
            else self.config_path.is_file()
        )
        self.server: Any = None
        self.controller: Any = None
        self.thread: threading.Thread | None = None
        self._restart_requested = threading.Event()
        self.last_error = ""

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.server)

    @property
    def job_running(self) -> bool:
        process = getattr(self.controller, "process", None)
        return bool(
            getattr(self.controller, "universe_operation_running", False)
            or (process is not None and process.poll() is None)
        )

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested.is_set()

    def consume_restart_request(self) -> bool:
        if not self._restart_requested.is_set():
            return False
        self._restart_requested.clear()
        return True

    def start(self) -> bool:
        if not self.enabled:
            return False
        try:
            if not self.config_path.is_file():
                raise ValueError(f"No existe la configuracion del nodo: {self.config_path}")
            from manager_node_runtime.common import load_json, safe_int
            from manager_node_runtime.node import JobController, NodeServer

            config = load_json(self.config_path)
            project_dir = Path(str(config.get("project_dir") or "")).expanduser().resolve()
            if project_dir != self.app_base_dir:
                raise ValueError(
                    "El node.json pertenece a otro proyecto: "
                    f"{project_dir} (app actual: {self.app_base_dir})"
                )
            host = str(config.get("host") or "0.0.0.0")
            port = safe_int(config.get("port"), 8761, minimum=1, maximum=65535)
            self.controller = JobController(config, self.config_path)
            self.server = NodeServer(
                (host, port), self.controller, restart_callback=self._restart_requested.set
            )
            self.thread = threading.Thread(
                target=self.server.serve_forever,
                kwargs={"poll_interval": 0.5},
                name="mt5-manager-node",
                daemon=True,
            )
            self.thread.start()
            self._log(f"Nodo integrado iniciado en {host}:{port}")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.server = None
            self.controller = None
            self.thread = None
            self._log(f"ERROR al iniciar nodo integrado: {exc}")
            return False

    def stop(self, *, stop_job: bool = True) -> None:
        controller = self.controller
        if stop_job and self.job_running:
            try:
                controller.stop()
            except Exception as exc:
                self._log(f"ERROR al detener generacion remota: {exc}")
        server = self.server
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                self._log(f"ERROR en shutdown del nodo: {exc}")
            try:
                server.server_close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.server = None
        self.controller = None
        self.thread = None
        self._log("Nodo integrado detenido")

    def _log(self, message: str) -> None:
        try:
            log_path = self.app_base_dir / "logs" / "manager_node.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {message}\n")
        except OSError:
            pass


def _write_restart_log(project_dir: Path, message: str) -> None:
    try:
        log_path = project_dir / "logs" / "manager_node_restart.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _git_result_detail(result: subprocess.CompletedProcess[str]) -> str:
    detail = "\n".join(
        part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip()
    )
    if len(detail) > 4000:
        detail = detail[-4000:]
    return detail


def sync_origin_before_relaunch(project_dir: Path) -> bool:
    """Fast-forward from origin and publish existing commits before relaunching."""
    project_dir = project_dir.resolve()
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    run_options = {
        "cwd": project_dir,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
        "timeout": GIT_SYNC_TIMEOUT_SECONDS,
        "env": environment,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }

    def run_git(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *arguments], **run_options)

    try:
        branch_result = run_git(["branch", "--show-current"])
        branch = (branch_result.stdout or "").strip()
        if branch_result.returncode != 0 or not branch:
            detail = _git_result_detail(branch_result)
            _write_restart_log(
                project_dir,
                "No se pudo determinar la rama actual; se omiten pull y push"
                + (f": {detail}" if detail else "."),
            )
            return False

        _write_restart_log(project_dir, f"Sincronizando rama {branch} con origin antes del reinicio")
        pull_result = run_git(["pull", "--ff-only", "origin", branch])
        pull_detail = _git_result_detail(pull_result)
        _write_restart_log(
            project_dir,
            f"git pull --ff-only origin {branch}: exit {pull_result.returncode}"
            + (f" | {pull_detail}" if pull_detail else ""),
        )
        if pull_result.returncode != 0:
            _write_restart_log(project_dir, "El pull fallo; no se ejecuta git push")
            return False

        push_result = run_git(["push", "origin", branch])
        push_detail = _git_result_detail(push_result)
        _write_restart_log(
            project_dir,
            f"git push origin {branch}: exit {push_result.returncode}"
            + (f" | {push_detail}" if push_detail else ""),
        )
        return push_result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        _write_restart_log(project_dir, f"ERROR al sincronizar con origin: {exc}")
        return False


def relaunch_application(entrypoint: Path, project_dir: Path | None = None) -> None:
    """Synchronize origin and replace the app process after a clean shutdown."""
    if getattr(sys, "frozen", False):
        arguments = [sys.executable, *sys.argv[1:]]
    else:
        arguments = [sys.executable, str(entrypoint.resolve()), *sys.argv[1:]]
    restart_dir = (project_dir or entrypoint.resolve().parent).resolve()
    try:
        sync_origin_before_relaunch(restart_dir)
    finally:
        os.execv(sys.executable, arguments)
