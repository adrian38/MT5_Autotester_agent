"""Ejecucion del pipeline: lanzar, vigilar, detener y reanudar etapas."""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import guided_batches, node_commands, node_settings, node_snapshots
from .common import safe_int, utc_now
from .node_jobs_common import ACTIVE_STATUSES, CONTROL_LOCK_TIMEOUT
from .node_settings import CLEANUP_STAGES
from .universe_service import build_history_command


class JobPipelineMixin:
    """Ejecucion del pipeline: lanzar, vigilar, detener y reanudar etapas."""

    @staticmethod
    def _step_label(step: dict[str, Any]) -> str:
        cycle = step.get("cycle")
        stage = str(step["action"])
        if cycle is None and stage in CLEANUP_STAGES:
            run_id = safe_int(step.get("run_id"), 0, minimum=0)
            return f"run_{run_id}_{stage}" if run_id > 0 else stage
        # La reparacion recorre las mismas etapas una vez por fase. Sin la fase en
        # la clave, la segunda pasada pisaria el codigo de retorno, el comando y el
        # recuento de pendientes de la primera.
        phase = step.get("phase")
        phase_part = f"phase_{phase}_" if phase is not None else ""
        if cycle is not None:
            if step.get("attempt") is not None:
                return f"cycle_{cycle}_attempt_{step.get('attempt')}_{phase_part}{stage}"
            return f"cycle_{cycle}_{stage}"
        attempt = step.get("attempt")
        return f"run_{step.get('run_id')}_attempt_{attempt}_{phase_part}{stage}"

    def _append_skip_log(self, log_path: Path, label: str) -> None:
        with log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[manager-node] Etapa omitida: {label}; no hay candidatos pendientes.\n")

    def _honour_stop_request(self, step_index: int, log_path: Path) -> bool:
        """Cierra el pipeline porque el usuario lo pidio, no porque fallara.

        Devuelve True para que quien llama no lo de por terminado con
        `_complete`, que lo marcaria como completado o fallido.
        """
        paused = self.pause_requested and not self.stop_requested
        self.pause_requested = False
        self.stop_requested = False
        self.state["status"] = "paused" if paused else "stopped"
        # En pausa se conserva la posicion para reanudar en esta misma etapa; al
        # detener no queda nada que retomar.
        self.state["current_step_index"] = step_index if paused else None
        self.state["pid"] = None
        self.state["return_code"] = None
        if paused:
            self.state["paused_at"] = utc_now()
        else:
            self.state["finished_at"] = utc_now()
        self.process = None
        with contextlib.suppress(OSError):
            with log_path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(
                    "[manager-node] "
                    + ("Pipeline pausado" if paused else "Pipeline detenido")
                    + " a peticion del usuario.\n"
                )
        self._persist()
        if not paused and self.queue:
            self._schedule_queue_drain()
        return True

    def _launch_next_runnable(self, step_index: int, log_path: Path, *, first: bool = False) -> bool:
        pipeline = list(self.state.get("pipeline") or [])
        request = dict(self.state.get("request") or {})
        while step_index < len(pipeline):
            # Descartar una etapa vacia cuesta una consulta a SQLite y este bucle
            # retiene `self.lock`: en una reparacion de cien runs encadena miles de
            # descartes y `stop()`/`pause()` se quedaban esperando el bloqueo hasta
            # que expiraba el POST del manager. Por eso la peticion se atiende aqui,
            # entre etapa y etapa, en vez de exigir el bloqueo al que la pide.
            if self.stop_requested or self.pause_requested:
                return self._honour_stop_request(step_index, log_path)
            step = pipeline[step_index]
            stage = str(step["action"])
            label = self._step_label(step)
            step_request = dict(request)
            if step.get("max_workers") is not None:
                step_request["max_workers"] = step["max_workers"]
            if stage == "generation":
                command, cwd = node_commands.build_generation_command(self.config, step_request)
            elif stage == "universe_history":
                command, cwd = build_history_command(self.config, step_request)
            elif stage in CLEANUP_STAGES:
                command, cwd = node_settings.build_historical_cleanup_command(self.config, stage)
            else:
                run_id = safe_int(step.get("run_id"), 0, minimum=0)
                if run_id <= 0:
                    raise ValueError("No se encontro el run para continuar el pipeline")
                pending_count = node_snapshots.pipeline_stage_pending_count(self.config, step_request, stage, run_id)
                self.state.setdefault("stage_pending_counts", {})[label] = pending_count
                if pending_count == 0:
                    self.state.setdefault("skipped_stages", []).append(label)
                    self.state.setdefault("stage_return_codes", {})[label] = None
                    self._append_skip_log(log_path, label)
                    self._persist()
                    step_index += 1
                    first = False
                    continue
                command, cwd = node_commands.build_pipeline_stage_command(self.config, step_request, stage, run_id)
            self.state.setdefault("commands", {})[label] = command
            self._launch_step(step_index, command, cwd, log_path, first=first)
            return True
        return False

    def _complete(self, return_code: int) -> None:
        # Una peticion de parada que llego cuando ya no quedaba nada que parar no
        # puede sobrevivir al trabajo: mataria el siguiente nada mas lanzarlo.
        self.stop_requested = False
        self.pause_requested = False
        self.state["return_code"] = return_code
        self.state["finished_at"] = utc_now()
        self.state["status"] = "completed" if return_code == 0 else "failed"
        self.guided_completed()
        self.state["pid"] = None
        self.process = None
        self._notify_no_work_completion(return_code)
        self._persist()
        if self.queue:
            self._schedule_queue_drain()

    def _launch_step(self, step_index: int, command: list[str], cwd: Path, log_path: Path, *, first: bool = False) -> None:
        step = list(self.state.get("pipeline") or [])[step_index]
        stage = str(step["action"])
        mode = "w" if first else "a"
        self.log_handle = log_path.open(mode, encoding="utf-8", errors="replace", buffering=1)
        if not first:
            self.log_handle.write(f"\n[manager-node] Iniciando etapa: {stage}\n")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command, cwd=cwd, stdout=self.log_handle, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
        )
        self.process = process
        self.guided_stage_started()
        self.state["pid"] = process.pid
        # Sin esto la posicion del pipeline solo vivia en los argumentos del hilo
        # vigilante, asi que un cierre del agente la perdia y no habia por donde
        # retomar.
        self.state["current_step_index"] = step_index
        self.state["status"] = "running"
        self.state["current_stage"] = stage
        self.state["current_cycle"] = step.get("cycle")
        self.state["current_run_id"] = step.get("run_id")
        self.state["current_attempt"] = step.get("attempt")
        self.state["current_phase"] = step.get("phase")
        self.state["command"] = command
        self._persist()
        threading.Thread(target=self._watch, args=(process, step_index), daemon=True).start()

    def _watch(self, process: subprocess.Popen[str], step_index: int) -> None:
        return_code = process.wait()
        with self.lock:
            if process is not self.process:
                return
            if self.log_handle:
                self.log_handle.close()
                self.log_handle = None
            self.guided_stage_finished(str((self.state.get("pipeline") or [])[step_index]["action"]))
            if self.pause_requested:
                # La etapa se corto a peticion del usuario, no fallo. Se conserva
                # ``current_step_index`` para relanzar esta misma etapa: al volver,
                # ``pipeline_stage_pending_count`` recalcula lo que quede pendiente.
                self.pause_requested = False
                self.state["status"] = "paused"
                self.state["pid"] = None
                self.state["return_code"] = None
                self.state["paused_at"] = utc_now()
                self.process = None
                self._persist()
                return
            if self.stop_requested:
                # Detener no es un fallo de la etapa: si se dejara caer al camino
                # normal, el trabajo acabaria como «failed» cuando el usuario
                # cortase una etapa en marcha y como «stopped» cuando cortase
                # entre etapas. Mismo boton, mismo resultado.
                self._honour_stop_request(step_index, Path(str(self.state["log_path"])))
                return
            pipeline = list(self.state.get("pipeline") or [])
            step = pipeline[step_index]
            stage = str(step["action"])
            cycle = step.get("cycle")
            run_id = step.get("run_id")
            label = self._step_label(step)
            self.state.setdefault("stage_return_codes", {})[label] = return_code
            if return_code == 0:
                self.state.setdefault("completed_stages", []).append(label)
            elif stage in CLEANUP_STAGES:
                self.state["cleanup_failed"] = True
            has_downstream_for_cycle = any(
                pending.get("cycle") == cycle and pending.get("action") != "generation"
                for pending in pipeline[step_index + 1:]
            )
            if return_code == 0 and stage == "generation" and has_downstream_for_cycle:
                try:
                    self._bind_generated_cycle_run(pipeline, cycle)
                except Exception as exc:
                    self.state["error"] = str(exc)
                    return_code = 1
            self._continue_after_watched_step(pipeline, step_index, step, return_code)

    def _bind_generated_cycle_run(self, pipeline: list[dict[str, Any]], cycle: Any) -> None:
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        cfg = node_settings.read_settings(settings_path)
        snapshot = node_snapshots.database_snapshot(node_settings.memory_path(self.config, cfg))
        prepared_id = (self.state.get("request") or {}).get("guided_batch_id")
        prepared_run = guided_batches.read_run(project, prepared_id) if prepared_id else None
        if prepared_id and not prepared_run:
            raise ValueError("El lote preparado no publicó su run exacto")
        run_key = "run_id" if prepared_id else "id"
        generated_run = safe_int(
            (prepared_run or snapshot.get("latest_run") or {}).get(run_key), 0, minimum=0,
        )
        if generated_run <= 0:
            raise ValueError("No se encontro el run generado")
        self.state.setdefault("cycle_run_ids", {})[str(cycle)] = generated_run
        for pending_step in pipeline:
            if pending_step.get("cycle") == cycle:
                pending_step["run_id"] = generated_run
        self.state["pipeline"] = pipeline

    def _continue_after_watched_step(
        self, pipeline: list[dict[str, Any]], step_index: int,
        step: dict[str, Any], return_code: int,
    ) -> None:
        self._notify_stage_completion(step, return_code)
        next_index = step_index + 1
        next_is_cleanup = (
            next_index < len(pipeline)
            and str(pipeline[next_index].get("action")) in CLEANUP_STAGES
        )
        cleanup_failed = bool(self.state.get("cleanup_failed"))
        continue_pipeline = return_code == 0 and not cleanup_failed
        if str(step["action"]) in CLEANUP_STAGES and next_is_cleanup:
            continue_pipeline = True
        if continue_pipeline and next_index < len(pipeline):
            try:
                if self._launch_next_runnable(next_index, Path(str(self.state["log_path"]))):
                    return
            except Exception as exc:
                self.state["error"] = str(exc)
                return_code = 1
        if cleanup_failed:
            return_code = 1
        self._complete(return_code)

    def _terminate_current(self, process: subprocess.Popen[str]) -> None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
            process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()

    def stop(self) -> dict[str, Any]:
        # La bandera se pone ANTES de pedir el bloqueo. Si el pipeline lo tiene
        # retenido descartando etapas vacias -minutos enteros en una reparacion de
        # cien runs-, `_launch_next_runnable` atiende la peticion por su cuenta y
        # esta llamada devuelve enseguida, en vez de agotar el POST del manager y
        # dejar el trabajo corriendo como si nadie hubiera pulsado nada.
        self.stop_requested = True
        if not self.lock.acquire(timeout=CONTROL_LOCK_TIMEOUT):
            job, _queue, _observed_at, _busy = self._read_status_snapshot()
            job["status"] = "stopping"
            return job
        try:
            process = self.process
            if process is None or process.poll() is not None:
                # Un pipeline en pausa o interrumpido no tiene proceso vivo, pero
                # si reserva el nodo: pararlo es descartarlo para liberar la cola.
                if self._is_resumable():
                    self.stop_requested = False
                    self.state["status"] = "stopped"
                    self.state["current_step_index"] = None
                    self.state["finished_at"] = utc_now()
                    self._persist()
                    if self.queue:
                        self._schedule_queue_drain()
                    return dict(self.state)
                if str(self.state.get("status") or "") not in ACTIVE_STATUSES:
                    self.stop_requested = False
                    raise RuntimeError("No hay ninguna generacion activa")
                # En marcha y sin proceso: esta entre etapas. La bandera ya esta
                # puesta y el pipeline la atendera en la siguiente.
                self.state["status"] = "stopping"
                self._persist()
                return dict(self.state)
            self.pause_requested = False
            self._terminate_current(process)
            self.state["status"] = "stopping"
            self._persist()
            return dict(self.state)
        finally:
            self.lock.release()

    def pause(self) -> dict[str, Any]:
        """Corta la etapa en curso conservando la posicion del pipeline."""
        self.pause_requested = True
        if not self.lock.acquire(timeout=CONTROL_LOCK_TIMEOUT):
            job, _queue, _observed_at, _busy = self._read_status_snapshot()
            job["status"] = "pausing"
            return job
        try:
            process = self.process
            if process is None or process.poll() is not None:
                if self._is_resumable():
                    self.pause_requested = False
                    raise RuntimeError("El pipeline ya esta pausado")
                if str(self.state.get("status") or "") not in ACTIVE_STATUSES:
                    self.pause_requested = False
                    raise RuntimeError("No hay ninguna generacion activa que pausar")
                self.state["status"] = "pausing"
                self._persist()
                return dict(self.state)
            if self.state.get("current_step_index") is None:
                self.pause_requested = False
                raise RuntimeError("Este trabajo no registra su posicion; no se puede pausar")
            self.state["status"] = "pausing"
            self._persist()
            self._terminate_current(process)
            return dict(self.state)
        finally:
            self.lock.release()

    def resume(self) -> dict[str, Any]:
        """Relanza el pipeline desde la etapa en la que se quedo."""
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Ya hay una etapa en marcha")
            if not self._is_resumable():
                raise RuntimeError("No hay ningun pipeline pausado, interrumpido o fallido que reanudar")
            step_index = safe_int(self.state.get("current_step_index"), -1)
            pipeline = list(self.state.get("pipeline") or [])
            if not 0 <= step_index < len(pipeline):
                raise RuntimeError("La posicion guardada del pipeline no es valida")
            stored_log = str(self.state.get("log_path") or "").strip()
            if not stored_log:
                raise RuntimeError("No se conserva el log del trabajo; no se puede reanudar")
            log_path = Path(stored_log)
            self.state.pop("paused_at", None)
            self.state["resumed_at"] = utc_now()
            self.state["error"] = None
            try:
                # ``first=False`` para no truncar el log de lo ya ejecutado.
                if not self._launch_next_runnable(step_index, log_path):
                    # Nada pendiente desde aqui: el pipeline estaba de hecho acabado.
                    self._complete(0)
            except Exception as exc:
                self.state["error"] = str(exc)
                self.state["status"] = "failed"
                self._persist()
                raise
            self._persist()
            return dict(self.state)
