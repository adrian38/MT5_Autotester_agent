from __future__ import annotations

import configparser
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on", "si"}


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
        self.last_error = ""

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.server)

    @property
    def job_running(self) -> bool:
        process = getattr(self.controller, "process", None)
        return bool(process is not None and process.poll() is None)

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
            self.server = NodeServer((host, port), self.controller)
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
