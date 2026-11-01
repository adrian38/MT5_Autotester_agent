from __future__ import annotations

import configparser
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


TRUE_VALUES = {"1", "true", "yes", "on", "si"}
GIT_SYNC_TIMEOUT_SECONDS = 300
GIT_PUSH_ATTEMPTS = 2

NODE_SUPERVISION_INTERVAL_MS = 5000
"""Cada cuanto mira la aplicacion si el hilo del nodo sigue vivo.

Cinco segundos no cuestan nada —es comparar un booleano— y acotan a eso el
tiempo que el manager puede estar hablandole a un nodo que ya no escucha. El
2026-11-01 ese hueco fueron nueve horas porque no habia nadie mirando.
"""

GitRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


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
        self.address: tuple[str, int] | None = None
        self._restart_requested = threading.Event()
        self._supervision_error = ""
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
            from manager_node_runtime.node import JobController

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
            self._serve(host, port)
            self._log(f"Nodo integrado iniciado en {host}:{port}")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.server = None
            self.controller = None
            self.thread = None
            self.address = None
            self._log(f"ERROR al iniciar nodo integrado: {exc}")
            return False

    def _serve(self, host: str, port: int) -> None:
        """Deja escuchando un servidor nuevo sobre el controlador ya creado.

        El controlador se recibe hecho y no se toca: cuando `supervise` vuelve
        a llamar aqui puede haber una generacion en marcha, y un `JobController`
        nuevo sobre el mismo `state.json` serian dos escritores para un trabajo.
        """
        from manager_node_runtime.node import NodeServer

        server = NodeServer(
            (host, port), self.controller, restart_callback=self._restart_requested.set
        )
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="mt5-manager-node",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            server.server_close()
            raise
        self.server, self.thread, self.address = server, thread, (host, port)

    def supervise(self) -> bool:
        """Vuelve a escuchar si el hilo del nodo murio sin que nadie lo parase.

        `serve_forever` solo se sale por `shutdown()` o por una excepcion que no
        atrapa. Lo primero lo hace `stop`, que deja `self.server` en `None` y no
        llega aqui; lo segundo mata el hilo y deja el socket escuchando sin que
        nadie lo atienda, que es peor que una caida: el puerto sigue abierto,
        las conexiones se amontonan en la cola y, cuando se llena, Windows
        empieza a contestar RST. El manager lo ve como «Connection refused» y no
        tiene forma de distinguirlo de un agente apagado. Paso el 2026-11-01.

        No se reinicia la aplicacion ni se toca el trabajo en curso: se cierra
        el socket huerfano —que ademas libera las conexiones a medio cerrar— y
        se levanta otro servidor sobre el mismo controlador.
        """
        if self.server is None or self.address is None:
            return False
        if self.thread is not None and self.thread.is_alive():
            return False
        self._log("El hilo del nodo murio solo; se vuelve a escuchar")
        try:
            self.server.server_close()
        except Exception:
            pass
        try:
            self._serve(*self.address)
        except Exception as exc:
            # El puerto puede tardar en soltarse. Se reintenta en la vuelta
            # siguiente, pero sin repetir la misma linea cada cinco segundos.
            if str(exc) != self._supervision_error:
                self._supervision_error = str(exc)
                self._log(f"ERROR al volver a escuchar el nodo: {exc}")
            return False
        self._supervision_error = ""
        host, port = self.address
        self._log(f"Nodo integrado relanzado en {host}:{port}")
        return True

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
        # Sin direccion `supervise` no relanza nada: una parada es una parada.
        self.address = None
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


def _parse_divergence(output: str) -> tuple[int, int] | None:
    parts = (output or "").split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _git_runner(project_dir: Path) -> GitRunner:
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

    return run_git


def _log_git(
    project_dir: Path, label: str, result: subprocess.CompletedProcess[str]
) -> None:
    detail = _git_result_detail(result)
    _write_restart_log(
        project_dir,
        f"{label}: exit {result.returncode}" + (f" | {detail}" if detail else ""),
    )


def _integrate_origin(project_dir: Path, run_git: GitRunner, branch: str) -> bool:
    """Fetch origin/<branch> and put the local commits on top of it."""
    fetch_result = run_git(["fetch", "origin", branch])
    _log_git(project_dir, f"git fetch origin {branch}", fetch_result)
    if fetch_result.returncode != 0:
        _write_restart_log(project_dir, "El fetch fallo; no se ejecuta git push")
        return False

    counts_result = run_git(["rev-list", "--left-right", "--count", "FETCH_HEAD...HEAD"])
    divergence = (
        _parse_divergence(counts_result.stdout) if counts_result.returncode == 0 else None
    )
    if divergence is None:
        _log_git(project_dir, "git rev-list --left-right --count FETCH_HEAD...HEAD", counts_result)
        _write_restart_log(project_dir, "No se pudo comparar con origin; no se ejecuta git push")
        return False

    behind, ahead = divergence
    _write_restart_log(
        project_dir,
        f"Rama {branch}: {behind} commit(s) en origin por traer, {ahead} local(es) por publicar",
    )
    if behind == 0:
        return True
    if ahead == 0:
        merge_result = run_git(["merge", "--ff-only", "FETCH_HEAD"])
        _log_git(project_dir, "git merge --ff-only FETCH_HEAD", merge_result)
        if merge_result.returncode != 0:
            _write_restart_log(project_dir, "El merge fallo; no se ejecuta git push")
            return False
        return True

    # Diverged branch: a fast-forward is impossible, so the local commits are
    # replayed on origin instead of staying unpublished for every later restart.
    # An autostash would resolve its own pop with conflict markers inside the
    # sources the relaunch is about to load, so a dirty tree stops the rebase.
    status_result = run_git(["status", "--porcelain", "--untracked-files=no"])
    if status_result.returncode != 0 or (status_result.stdout or "").strip():
        _log_git(project_dir, "git status --porcelain", status_result)
        _write_restart_log(
            project_dir,
            "Hay cambios sin commitear y la rama diverge; no se reorganiza ni se ejecuta git push",
        )
        return False

    rebase_result = run_git(["rebase", "FETCH_HEAD"])
    _log_git(project_dir, "git rebase FETCH_HEAD", rebase_result)
    if rebase_result.returncode != 0:
        _log_git(project_dir, "git rebase --abort", run_git(["rebase", "--abort"]))
        _write_restart_log(project_dir, "El rebase fallo; no se ejecuta git push")
        return False
    return True


def sync_origin_before_relaunch(project_dir: Path) -> bool:
    """Bring the current branch in line with origin and publish it before relaunching."""
    project_dir = project_dir.resolve()
    run_git = _git_runner(project_dir)
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
        for attempt in range(1, GIT_PUSH_ATTEMPTS + 1):
            if not _integrate_origin(project_dir, run_git, branch):
                return False
            push_result = run_git(["push", "origin", branch])
            _log_git(project_dir, f"git push origin {branch} (intento {attempt})", push_result)
            if push_result.returncode == 0:
                return True
        # A retry only helps when origin moved under us; a branch the remote
        # itself refuses keeps failing, and the relaunch continues regardless.
        _write_restart_log(
            project_dir, f"El push a origin/{branch} fallo tras {GIT_PUSH_ATTEMPTS} intentos"
        )
        return False
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
