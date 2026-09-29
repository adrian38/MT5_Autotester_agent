from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import sqlite3
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import guided_batches
from .common import json_bytes, load_json, safe_int, utc_now
from .guided_controller import GuidedControllerMixin
from .live_audit import LiveAuditController
from .universe_service import UniverseControllerMixin
from .node_jobs_api import JobApiMixin
from .node_jobs_pipeline import JobPipelineMixin
from .node_jobs_queue import JobQueueMixin
from .node_jobs_start import JobStartMixin

# Reexportados para que los consumidores (y los parches de los tests) sigan
# encontrandolos en `manager_node_runtime.node`.
from .node_jobs_common import (  # noqa: F401
    ACTIVE_STATUSES,
    CONTROL_LOCK_TIMEOUT,
    RESUMABLE_STATUSES,
)
from .node_settings import (  # noqa: F401
    CLEANUP_STAGES,
    CLEANUP_STAGE_SCRIPTS,
    _load_universe_rows,
    _table_exists,
    _universe_paths,
    build_historical_cleanup_command,
    cleanup_after_run_enabled,
    historical_cleanup_scripts,
    memory_path,
    read_settings,
    resolve_generation_mode,
    setting,
    setting_bool,
    stored_run_generation_mode,
)
from .node_commands import (  # noqa: F401
    SCORE_OPTIONS,
    VALUE_OPTIONS,
    build_generation_command,
    build_pipeline_stage_command,
    filter_supported_options,
)
from .node_snapshots import (  # noqa: F401
    FINAL_TICK_RETRYABLE_STATUSES,
    ROBUST_RETRYABLE_STATUSES,
    STAGE_NOTIFICATION_LABELS,
    STAGE_TABLES,
    STATUS_NOTIFICATION_ORDER,
    completed_runs_snapshot,
    database_snapshot,
    format_stage_counts,
    pipeline_stage_pending_count,
    stage_notification_message,
    stage_run_counts,
)


class JobController(
    GuidedControllerMixin,
    UniverseControllerMixin,
    JobQueueMixin,
    JobStartMixin,
    JobPipelineMixin,
    JobApiMixin,
):
    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.runtime_dir = config_path.parent / "runtime" / str(config.get("node_id") or "node")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.queue_path = self.runtime_dir / "queue.json"
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.queue: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {
            "job_id": None, "status": "idle", "pid": None, "started_at": None,
            "finished_at": None, "return_code": None, "request": None, "command": None,
            "log_path": None, "error": None, "pipeline": [], "current_stage": None,
            "completed_stages": [], "stage_return_codes": {}, "current_step_index": None,
        }
        self.pause_requested = False
        # Se leen y escriben FUERA de `self.lock` a proposito: durante una
        # reparacion masiva el bloqueo se queda retenido minutos enteros en
        # `_launch_next_runnable` descartando etapas sin pendientes, y detener o
        # pausar no puede depender de conseguirlo.
        self.stop_requested = False
        if self.state_path.is_file():
            try:
                old = load_json(self.state_path)
                self.state.update(old)
                if self.state.get("status") in {"running", "stopping"}:
                    # El proceso murio con el agente. Si sabemos en que paso iba,
                    # queda como interrumpido y se puede reanudar; si no, no hay
                    # nada que retomar y se deja como antes.
                    resumable = (
                        self.state.get("current_step_index") is not None
                        and bool(self.state.get("pipeline"))
                    )
                    self.state["status"] = "interrupted" if resumable else "unknown_after_restart"
                    self.state["pid"] = None
            except ValueError:
                pass
        if self.queue_path.is_file():
            try:
                stored_queue = load_json(self.queue_path)
                if isinstance(stored_queue, list):
                    self.queue = [dict(item) for item in stored_queue if isinstance(item, dict)]
            except ValueError:
                pass
        self._publish_status_snapshot()
        self.live_audits = LiveAuditController(self, self.runtime_dir)
        if self.queue:
            self._schedule_queue_drain()






class NodeHandler(BaseHTTPRequestHandler):
    server: "NodeServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[node-http] " + (fmt % args) + "\n")

    def _authorized(self) -> bool:
        expected = str(self.server.controller.config.get("token") or "")
        supplied = self.headers.get("Authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())

    def _send(self, status: int, value: Any) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_artifact(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, maximum: int = 1_000_000) -> dict[str, Any]:
        length = safe_int(self.headers.get("Content-Length"), 0, minimum=0, maximum=maximum)
        if length == 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON")
        return value

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v1/health":
            self._send(200, {"ok": True, "node_id": self.server.controller.config.get("node_id"), "time": utc_now()})
        elif parsed.path.startswith("/api/v1/guided-batches/"):
            try:
                self._send(200, self.server.controller.guided_status(parsed.path.rsplit("/", 1)[-1]))
            except (ValueError, OSError, sqlite3.Error) as exc:
                self._send(400, {"error": str(exc)})
        elif parsed.path == "/api/v1/status":
            self._send(200, self.server.controller.status())
        elif parsed.path == "/api/v1/logs":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.log_tail(safe_int(query.get("lines", [200])[0], 200)))
        elif parsed.path == "/api/v1/runs":
            query = urllib.parse.parse_qs(parsed.query)
            limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=100)
            offset = safe_int(query.get("offset", [0])[0], 0, minimum=0)
            self._send(200, self.server.controller.runs(limit, offset))
        elif parsed.path == "/api/v1/universe":
            self._send(200, self.server.controller.universe())
        elif parsed.path == "/api/v1/live-audits":
            self._send(200, {"audits": self.server.controller.live_audits.all_states(), "observed_at": utc_now()})
        elif (
            len(parsed.path.strip("/").split("/")) == 7
            and parsed.path.strip("/").split("/")[:3] == ["api", "v1", "live-audits"]
            and parsed.path.strip("/").split("/")[4] == "artifacts"
        ):
            parts = parsed.path.strip("/").split("/")
            try:
                path = self.server.controller.live_audits.artifact_path(
                    urllib.parse.unquote(parts[3]),
                    urllib.parse.unquote(parts[5]),
                    urllib.parse.unquote(parts[6]),
                )
                self._send_artifact(path)
            except (ValueError, FileNotFoundError):
                self._send(404, {"error": "Reporte de auditoría no encontrado"})
        elif parsed.path.startswith("/api/v1/live-audits/"):
            audit_key = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            self._send(200, {"audit": self.server.controller.live_audits.state(audit_key), "observed_at": utc_now()})
        elif parsed.path == "/api/v1/portfolios":
            query=urllib.parse.parse_qs(parsed.query); self._send(200,self.server.controller.portfolios(query.get("scope",["full_history"])[0]))
        elif parsed.path.startswith("/api/v1/portfolios/"):
            query=urllib.parse.parse_qs(parsed.query); portfolio_id=safe_int(parsed.path.rsplit("/",1)[-1],0,minimum=1); self._send(200,self.server.controller.portfolio_detail(portfolio_id,query.get("scope",["full_history"])[0]))
        else:
            self._send(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        try:
            if self.path == "/api/v1/application/restart":
                self._send(202, self.server.request_application_restart())
            elif self.path == "/api/v1/guided-batches":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= guided_batches.MAX_BODY:
                    raise ValueError("Lote demasiado grande o vacío")
                self._send(202, self.server.controller.submit_guided(self._body(guided_batches.MAX_BODY)))
            elif self.path == "/api/v1/jobs/generation":
                self._send(202, self.server.controller.start(self._body()))
            elif self.path == "/api/v1/jobs/repair":
                self._send(202, self.server.controller.start_repair(self._body()))
            elif self.path == "/api/v1/jobs/regression":
                self._send(202, self.server.controller.start_regression(self._body()))
            elif self.path == "/api/v1/jobs/cleanup":
                self._send(202, self.server.controller.start_cleanup())
            elif self.path == "/api/v1/jobs/stop":
                self._send(202, self.server.controller.stop())
            elif self.path == "/api/v1/jobs/pause":
                self._send(202, self.server.controller.pause())
            elif self.path == "/api/v1/jobs/resume":
                self._send(202, self.server.controller.resume())
            elif self.path == "/api/v1/jobs/queue/cancel":
                self._send(200, self.server.controller.cancel_queued(str(self._body().get("task_id") or "")))
            elif self.path.startswith("/api/v1/live-audits/") and self.path.endswith("/run"):
                portfolio_id = safe_int(self.path.strip("/").split("/")[-2], 0, minimum=1)
                body = self._body()
                body["portfolio_id"] = portfolio_id
                self._send(202, {"audit": self.server.controller.live_audits.start(body)})
            elif self.path == "/api/v1/universe/symbols":
                self._send(200, self.server.controller.update_universe(self._body()))
            elif self.path in {
                "/api/v1/universe/sync", "/api/v1/universe/history-preview",
                "/api/v1/universe/disable-preview", "/api/v1/universe/disable-no-history",
                "/api/v1/universe/trade-disabled-preview",
                "/api/v1/universe/disable-trade-disabled",
            }:
                self._send(200, self.server.controller.universe_action(self.path.rsplit("/", 1)[1], self._body()))
            elif self.path == "/api/v1/jobs/universe-history":
                self._send(202, self.server.controller.start_universe_history())
            elif self.path == "/api/v1/portfolios/save":
                self._send(201, self.server.controller.save_portfolio(self._body(50_000_000)))
            elif self.path == "/api/v1/portfolios/alias":
                self._send(200, self.server.controller.set_portfolio_alias(self._body()))
            elif self.path == "/api/v1/portfolios/delete":
                self._send(200, self.server.controller.delete_portfolio(self._body()))
            elif self.path == "/api/v1/portfolios/exclude":
                self._send(200, self.server.controller.exclude_portfolio_members(self._body()))
            elif self.path == "/api/v1/portfolios/requalify":
                self._send(200, self.server.controller.requalify_portfolio_member(self._body()))
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": str(exc)})


class NodeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        controller: JobController,
        restart_callback: Callable[[], None] | None = None,
    ) -> None:
        self.controller = controller
        self.restart_callback = restart_callback
        self.controller.application_restart_available = restart_callback is not None
        super().__init__(address, NodeHandler)

    def request_application_restart(self) -> dict[str, Any]:
        callback = self.restart_callback
        if callback is None:
            raise RuntimeError("El reinicio remoto solo esta disponible en la aplicacion integrada")
        with self.controller.lock:
            process = self.controller.process
            process_running = process is not None and process.poll() is None
            status = str(self.controller.state.get("status") or "")
            restartable = status in {"idle", "completed", "failed", "stopped", "paused", "interrupted"}
            if process_running or self.controller.live_audits.is_running() or self.controller.queue or not restartable:
                raise RuntimeError(
                    "No se puede reiniciar la aplicacion con una ejecucion activa o tareas pendientes"
                )
        callback()
        return {"status": "restarting", "message": "Reinicio de la aplicacion solicitado"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nodo remoto para MT5 Autotester Manager")
    parser.add_argument("--config", default="node.json")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    config = load_json(config_path)
    for key in ("node_id", "project_dir", "token"):
        if not str(config.get(key) or "").strip():
            parser.error(f"Falta {key} en {config_path}")
    host = str(config.get("host") or "0.0.0.0")
    port = safe_int(config.get("port"), 8761, minimum=1, maximum=65535)
    server = NodeServer((host, port), JobController(config, config_path))
    print(f"Nodo {config['node_id']} escuchando en http://{host}:{port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
