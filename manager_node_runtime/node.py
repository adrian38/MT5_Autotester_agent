from __future__ import annotations

import argparse
import configparser
import contextlib
import hmac
import json
import os
import platform
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .common import json_bytes, load_json, safe_int, save_json, utc_now


SCORE_OPTIONS = {
    "ubs_pass_min_net_profit": "--min-net-profit",
    "ubs_pass_min_profit_factor": "--min-profit-factor",
    "ubs_pass_min_trades": "--min-trades",
    "ubs_pass_max_drawdown_pct": "--max-drawdown-pct",
    "ubs_pass_min_recovery_factor": "--min-recovery-factor",
    "ubs_long_tf_min_trades_w1": "--min-trades-w1",
    "ubs_long_tf_min_trades_mn": "--min-trades-mn",
}

VALUE_OPTIONS = {
    "--source-dir", "--output-dir", "--memory", "--broker", "--account-type", "--template",
    "--generations", "--variants-per-seed", "--max-seeds", "--delay", "--generation-mode",
    "--from-date", "--to-date", "--min-net-profit", "--min-profit-factor", "--min-trades",
    "--max-drawdown-pct", "--min-recovery-factor", "--min-trades-w1", "--min-trades-mn",
    "--terminals-config", "--max-workers", "--expert", "--mt5-path", "--data-dir", "--symbol-map",
    "--symbol-suffix", "--symbol-futures-suffix", "--symbol-shares-suffix",
}


def read_settings(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def setting(parser: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    return parser.get(section, key, fallback=default).strip()


def setting_bool(parser: configparser.ConfigParser, section: str, key: str, default: bool = False) -> bool:
    try:
        return parser.getboolean(section, key, fallback=default)
    except ValueError:
        return default


def memory_path(config: dict[str, Any], parser: configparser.ConfigParser) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    explicit = str(config.get("memory_path") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_absolute() else project / path
    broker = str(config.get("broker") or setting(parser, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(parser, "General", "ubs_account_type", "ECN")).upper()
    scoped = project / "outputs" / f"ubs_memory_{broker}_{account}.sqlite"
    legacy = project / "outputs" / "ubs_memory.sqlite"
    script = project / "ubs_agent.py"
    try:
        supports_broker = '"--broker"' in script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        supports_broker = True
    return scoped if supports_broker else legacy


def _add(args: list[str], option: str, value: Any) -> None:
    text = str(value).strip()
    if text:
        args.extend([option, text])


def filter_supported_options(command: list[str], script: Path) -> list[str]:
    """Remove manager options that an older broker branch does not expose."""
    try:
        source = script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return command
    supported = set(re.findall(r"[\"'](--[a-z0-9-]+)[\"']", source, flags=re.IGNORECASE))
    # A custom wrapper may not define argparse options in its own source.
    if "--generations" not in supported:
        return command
    prefix, options = command[:3], command[3:]
    filtered: list[str] = []
    index = 0
    while index < len(options):
        token = options[index]
        if token.startswith("--") and token not in supported:
            index += 2 if token in VALUE_OPTIONS and index + 1 < len(options) else 1
            continue
        filtered.append(token)
        index += 1
    return prefix + filtered


def build_generation_command(config: dict[str, Any], payload: dict[str, Any]) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    script = project / "ubs_agent.py"
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    if not script.is_file():
        raise ValueError(f"No existe {script}")
    if not settings_path.is_file():
        raise ValueError(f"No existe {settings_path}")
    cfg = read_settings(settings_path)
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}

    def pick(name: str, settings_key: str, fallback: Any) -> Any:
        if name in payload:
            return payload[name]
        if name in defaults:
            return defaults[name]
        return setting(cfg, "General", settings_key, str(fallback))

    broker = str(config.get("broker") or setting(cfg, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(cfg, "General", "ubs_account_type", "ECN")).upper()
    source = str(config.get("source_dir") or setting(cfg, "Paths", "set_files_root"))
    output = str(config.get("output_dir") or setting(cfg, "Paths", "ubs_generation_output"))
    template = str(config.get("template") or setting(cfg, "Paths", "template_path", str(project / "tester_template.ini")))
    python = str(config.get("python_executable") or sys.executable)
    generations = safe_int(pick("generations", "ubs_generation_count", 1), 1, minimum=1, maximum=1000)
    variants = safe_int(pick("variants_per_seed", "ubs_variants_per_seed", 10), 10, minimum=1, maximum=10000)
    max_seeds = safe_int(pick("max_seeds", "ubs_max_seeds", 30), 30, minimum=0, maximum=100000)
    generation_mode = str(pick("generation_mode", "ubs_generation_mode", "production")).lower()
    if generation_mode not in {"production", "discovery"}:
        raise ValueError("generation_mode debe ser production o discovery")
    execute = payload.get("execute_backtests", defaults.get("execute_backtests", setting_bool(cfg, "General", "ubs_agent_execute", True)))

    args = [python, "-u", str(script)]
    _add(args, "--source-dir", source)
    _add(args, "--output-dir", output)
    _add(args, "--memory", memory_path(config, cfg))
    _add(args, "--broker", broker)
    _add(args, "--account-type", account)
    _add(args, "--template", template)
    _add(args, "--generations", generations)
    _add(args, "--variants-per-seed", variants)
    _add(args, "--max-seeds", max_seeds)
    _add(args, "--delay", pick("delay", "delay", 5))
    _add(args, "--generation-mode", generation_mode)
    _add(args, "--from-date", payload.get("from_date", defaults.get("from_date", setting(cfg, "General", "ubs_agent_from_date"))))
    _add(args, "--to-date", payload.get("to_date", defaults.get("to_date", setting(cfg, "General", "ubs_agent_to_date"))))

    for key, option in SCORE_OPTIONS.items():
        _add(args, option, setting(cfg, "General", key))
    if setting_bool(cfg, "General", "ubs_experimental_long_timeframes"):
        args.append("--experimental-long-timeframes")
    if bool(payload.get("continue_last", False)):
        args.append("--continue-last-run")
    if bool(payload.get("dry_run", False)):
        args.append("--dry-run")
    if execute:
        args.append("--execute-backtests")
        if setting_bool(cfg, "Multiterminal", "enabled"):
            args.extend(["--multi-terminal", "--terminals-config", str(settings_path)])
            _add(args, "--max-workers", setting(cfg, "Multiterminal", "workers", "1"))
        else:
            expert = str(config.get("expert") or setting(cfg, "Paths", "ubs_ex5_file"))
            if not expert:
                raise ValueError("Falta Paths.ubs_ex5_file y no hay multiterminal habilitado")
            _add(args, "--expert", expert)
            _add(args, "--mt5-path", setting(cfg, "Paths", "mt5_path"))
            _add(args, "--data-dir", setting(cfg, "Paths", "mt5_data_root"))
        broker_key = broker.lower().replace(" ", "")
        if setting_bool(cfg, "General", "symbol_map_enabled"):
            symbol_map = setting(cfg, "General", f"symbol_map_{broker_key}") or setting(cfg, "General", "symbol_map")
            _add(args, "--symbol-map", symbol_map)
        if setting_bool(cfg, "General", "symbol_suffix_enabled"):
            _add(args, "--symbol-suffix", setting(cfg, "General", "symbol_suffix"))
            _add(args, "--symbol-futures-suffix", setting(cfg, "General", "symbol_futures_suffix"))
            _add(args, "--symbol-shares-suffix", setting(cfg, "General", "symbol_shares_suffix"))
    return filter_supported_options(args, script), project


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone()
    return row is not None


def _counts(conn: sqlite3.Connection, table: str, run_id: int) -> dict[str, int]:
    if not _table_exists(conn, table):
        return {}
    rows = conn.execute(f"select status, count(*) total from {table} where run_id=? group by status", (run_id,))
    return {str(row[0] or "unknown"): int(row[1]) for row in rows}


def database_snapshot(path: Path) -> dict[str, Any]:
    empty = {"available": False, "path": str(path), "latest_run": None, "stages": {}}
    if not path.is_file():
        return empty
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "runs"):
                return empty
            run = conn.execute("select * from runs where coalesce(hidden,0)=0 order by id desc limit 1").fetchone()
            if run is None:
                return {**empty, "available": True}
            run_dict = dict(run)
            run_id = int(run_dict["id"])
            max_generation = conn.execute("select coalesce(max(generation),0) from candidates where run_id=?", (run_id,)).fetchone()[0]
            stages = {
                "generation": _counts(conn, "candidates", run_id),
                "robustness": _counts(conn, "candidate_robustness", run_id),
                "final_tick": _counts(conn, "candidate_final_tick", run_id),
                "final_tick_6m": _counts(conn, "candidate_final_tick_6m", run_id),
            }
            return {
                "available": True,
                "path": str(path),
                "latest_run": run_dict,
                "max_generation": int(max_generation or 0),
                "stages": stages,
            }
    except (sqlite3.Error, OSError) as exc:
        return {**empty, "error": str(exc)}


class JobController:
    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.runtime_dir = config_path.parent / "runtime" / str(config.get("node_id") or "node")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.state: dict[str, Any] = {
            "job_id": None, "status": "idle", "pid": None, "started_at": None,
            "finished_at": None, "return_code": None, "request": None, "command": None,
            "log_path": None, "error": None,
        }
        if self.state_path.is_file():
            try:
                old = load_json(self.state_path)
                self.state.update(old)
                if self.state.get("status") == "running":
                    self.state["status"] = "unknown_after_restart"
            except ValueError:
                pass

    def _persist(self) -> None:
        save_json(self.state_path, self.state)

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Este nodo ya tiene una generacion en curso")
            command, cwd = build_generation_command(self.config, payload)
            job_id = time.strftime("%Y%m%d_%H%M%S")
            log_path = self.runtime_dir / f"generation_{job_id}.log"
            self.log_handle = log_path.open("w", encoding="utf-8", errors="replace", buffering=1)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self.process = subprocess.Popen(
                command, cwd=cwd, stdout=self.log_handle, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
            )
            self.state = {
                "job_id": job_id, "status": "running", "pid": self.process.pid,
                "started_at": utc_now(), "finished_at": None, "return_code": None,
                "request": payload, "command": command, "log_path": str(log_path), "error": None,
            }
            self._persist()
            threading.Thread(target=self._watch, args=(self.process,), daemon=True).start()
            return dict(self.state)

    def _watch(self, process: subprocess.Popen[str]) -> None:
        return_code = process.wait()
        with self.lock:
            if process is not self.process:
                return
            if self.log_handle:
                self.log_handle.close()
                self.log_handle = None
            self.state["return_code"] = return_code
            self.state["finished_at"] = utc_now()
            self.state["status"] = "completed" if return_code == 0 else "failed"
            self._persist()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                raise RuntimeError("No hay ninguna generacion activa")
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGTERM)
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
            self.state["status"] = "stopping"
            self._persist()
            return dict(self.state)

    def status(self) -> dict[str, Any]:
        with self.lock:
            result = dict(self.state)
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        try:
            cfg = read_settings(settings_path)
            db = database_snapshot(memory_path(self.config, cfg))
        except Exception as exc:
            db = {"available": False, "error": str(exc)}
        return {
            "node": {
                "id": self.config.get("node_id"),
                "name": self.config.get("display_name") or self.config.get("node_id"),
                "broker": self.config.get("broker"),
                "account_type": self.config.get("account_type"),
                "machine": os.environ.get("COMPUTERNAME") or platform.node(),
                "user": os.environ.get("USERNAME") or os.environ.get("USER"),
                "project_dir": str(project),
            },
            "job": result,
            "database": db,
            "observed_at": utc_now(),
        }

    def log_tail(self, lines: int = 200) -> dict[str, Any]:
        with self.lock:
            path_text = self.state.get("log_path")
        if not path_text or not Path(path_text).is_file():
            return {"lines": [], "log_path": path_text}
        content = Path(path_text).read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": content[-max(1, min(lines, 2000)):], "log_path": path_text}


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

    def _body(self) -> dict[str, Any]:
        length = safe_int(self.headers.get("Content-Length"), 0, minimum=0, maximum=1_000_000)
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
        elif parsed.path == "/api/v1/status":
            self._send(200, self.server.controller.status())
        elif parsed.path == "/api/v1/logs":
            query = urllib.parse.parse_qs(parsed.query)
            self._send(200, self.server.controller.log_tail(safe_int(query.get("lines", [200])[0], 200)))
        else:
            self._send(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "No autorizado"})
            return
        try:
            if self.path == "/api/v1/jobs/generation":
                self._send(202, self.server.controller.start(self._body()))
            elif self.path == "/api/v1/jobs/stop":
                self._send(202, self.server.controller.stop())
            else:
                self._send(404, {"error": "Ruta no encontrada"})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._send(409, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": str(exc)})


class NodeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: JobController) -> None:
        self.controller = controller
        super().__init__(address, NodeHandler)


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
