"""Cola de trabajos, instantanea de estado y avisos de Telegram."""
from __future__ import annotations

import configparser
import copy
import threading
import uuid
from pathlib import Path
from typing import Any

import telegram_notify

from . import node_settings, node_snapshots
from .common import safe_int, save_json, utc_now
from .node_jobs_common import RESUMABLE_STATUSES


class JobQueueMixin:
    """Cola de trabajos, instantanea de estado y avisos de Telegram."""

    def _persist(self) -> None:
        save_json(self.state_path, self.state)
        self._publish_status_snapshot()

    def _publish_status_snapshot(self) -> None:
        # Writers own self.lock. Publish a detached tuple in one assignment so
        # HTTP readers never wait for a bulk repair's database preflight.
        self._status_snapshot = (
            copy.deepcopy(self.state), self._queue_snapshot(), utc_now(),
        )

    def _read_status_snapshot(self) -> tuple[dict[str, Any], dict[str, Any], str, bool]:
        if self.lock.acquire(blocking=False):
            try:
                self._publish_status_snapshot()
                snapshot = self._status_snapshot
                busy = False
            finally:
                self.lock.release()
        else:
            snapshot = self._status_snapshot
            busy = True
        job, queue, observed_at = copy.deepcopy(snapshot)
        return job, queue, observed_at, busy

    def _persist_queue(self) -> None:
        save_json(self.queue_path, self.queue)
        self._publish_status_snapshot()

    def _queue_snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self.queue),
            "items": [
                {
                    "id": str(item.get("id") or ""),
                    "type": str(item.get("type") or "generation"),
                    "created_at": item.get("created_at"),
                    "summary": str(item.get("summary") or ""),
                    "position": index,
                }
                for index, item in enumerate(self.queue, 1)
            ],
        }

    def _busy(self) -> bool:
        # Un pipeline en pausa tambien reserva el nodo: si no, la cola arrancaria
        # el siguiente trabajo encima del que el usuario dejo a medias y ya no
        # habria forma de reanudarlo.
        return self.process is not None or self._is_resumable() or self.live_audits.is_running()

    def _is_resumable(self) -> bool:
        pipeline = list(self.state.get("pipeline") or [])
        step_index = safe_int(self.state.get("current_step_index"), -1)
        return (
            str(self.state.get("status") or "") in RESUMABLE_STATUSES
            and 0 <= step_index < len(pipeline)
            and bool(str(self.state.get("log_path") or "").strip())
        )

    def _enqueue(self, task_type: str, payload: dict[str, Any], summary: str) -> dict[str, Any]:
        if len(self.queue) >= 100:
            raise RuntimeError("La cola de este nodo alcanzo el limite de 100 tareas")
        task_id = uuid.uuid4().hex
        item = {
            "id": task_id,
            "type": task_type,
            "payload": payload,
            "created_at": utc_now(),
            "summary": summary,
        }
        self.queue.append(item)
        self._persist_queue()
        return {
            **dict(self.state),
            "queued": True,
            "queue_item": dict(self._queue_snapshot()["items"][-1]),
            "task_queue": self._queue_snapshot(),
        }

    def _schedule_queue_drain(self) -> None:
        timer = threading.Timer(0.05, self._drain_queue)
        timer.daemon = True
        timer.start()

    def _drain_queue(self) -> None:
        with self.lock:
            if self._busy() or not self.queue:
                return
            item = self.queue.pop(0)
            # Keep the published snapshot on the queued item until the new job
            # persists its running state. Otherwise readers can briefly observe
            # the previous completed job with an empty queue and conclude that
            # the whole FIFO has finished.
            save_json(self.queue_path, self.queue)
            try:
                payload = dict(item.get("payload") or {})
                if item.get("type") == "repair":
                    self._start_repair(payload)
                elif item.get("type") == "regression":
                    self._start_regression(payload)
                elif item.get("type") == "cleanup":
                    self._start_cleanup()
                else:
                    self._start_generation(payload)
            except Exception as exc:
                self.state = {
                    "job_id": item.get("id"), "job_type": item.get("type"),
                    "status": "failed", "pid": None, "started_at": utc_now(),
                    "finished_at": utc_now(), "return_code": 1, "request": item.get("payload"),
                    "command": None, "log_path": None, "error": str(exc), "pipeline": [],
                    "current_stage": None, "completed_stages": [], "stage_return_codes": {},
                    "telegram_notifications": [],
                }
                self._persist()
                if self.queue:
                    self._schedule_queue_drain()

    def cancel_queued(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            task_id = str(task_id or "").strip()
            if not task_id:
                raise ValueError("Falta el id de la tarea")
            before = len(self.queue)
            self.queue = [item for item in self.queue if str(item.get("id")) != task_id]
            if len(self.queue) == before:
                raise ValueError("La tarea ya no esta en la cola")
            self._persist_queue()
            return {"cancelled": task_id, "task_queue": self._queue_snapshot()}

    def _settings_and_memory(self) -> tuple[configparser.ConfigParser, Path]:
        project = Path(str(self.config["project_dir"])).expanduser().resolve()
        settings_path = Path(str(self.config.get("settings_file") or "ui_settings.ini"))
        if not settings_path.is_absolute():
            settings_path = project / settings_path
        cfg = node_settings.read_settings(settings_path)
        return cfg, node_settings.memory_path(self.config, cfg)

    def _telegram_enabled(self) -> bool:
        try:
            cfg, _ = self._settings_and_memory()
            return node_settings.setting_bool(cfg, "General", "telegram_enabled", False)
        except (OSError, ValueError, KeyError):
            return False

    def _append_telegram_log(self, message: str) -> None:
        path_text = self.state.get("log_path")
        if not path_text:
            return
        try:
            with Path(str(path_text)).open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(f"[Telegram] {message}\n")
        except OSError:
            pass

    def _send_telegram(self, key: str, message: str) -> None:
        if not self._telegram_enabled():
            return
        sent = self.state.setdefault("telegram_notifications", [])
        if key in sent:
            return
        sent.append(key)
        self._append_telegram_log(f"Enviando aviso: {key}")

        def on_result(error: str | None) -> None:
            if error:
                self._append_telegram_log(f"ERROR: {error}")
            else:
                self._append_telegram_log("Mensaje enviado correctamente.")

        try:
            telegram_notify.send_async(message, on_result=on_result)
        except Exception as exc:
            self._append_telegram_log(f"ERROR al preparar el envio: {exc}")

    def _notify_stage_completion(
        self,
        step: dict[str, Any],
        return_code: int,
    ) -> None:
        stage = str(step.get("action") or "")
        run_id = safe_int(step.get("run_id"), 0, minimum=0)
        counts: dict[str, int] = {}
        try:
            _, db_path = self._settings_and_memory()
            if run_id <= 0 and stage == "generation":
                snapshot = node_snapshots.database_snapshot(db_path)
                run_id = safe_int((snapshot.get("latest_run") or {}).get("id"), 0, minimum=0)
            counts = node_snapshots.stage_run_counts(db_path, run_id, stage)
        except (OSError, ValueError, KeyError):
            counts = {}
        label = self._step_label(step)
        message = node_snapshots.stage_notification_message(
            self.config,
            self.state,
            step,
            return_code,
            run_id,
            counts,
        )
        self._send_telegram(label, message)

    def _notify_no_work_completion(self, return_code: int) -> None:
        if self.state.get("telegram_notifications"):
            return
        request = self.state.get("request") if isinstance(self.state.get("request"), dict) else {}
        run_ids = request.get("run_ids") if isinstance(request.get("run_ids"), list) else []
        run_text = ", ".join(f"#{safe_int(value, 0, minimum=0)}" for value in run_ids) or "-"
        outcome = "OK" if return_code == 0 else f"ERROR codigo {return_code}"
        job_labels = {"repair": "Reparacion", "regression": "Prueba regresiva"}
        job_label = job_labels.get(str(self.state.get("job_type") or ""), "Ejecucion")
        message = (
            f"MT5 Autotester Manager: {job_label} finalizada ({outcome}).\n"
            f"Nodo: {self.config.get('display_name') or self.config.get('node_id')} | "
            f"{str(self.config.get('broker') or '').upper()}/{str(self.config.get('account_type') or '').upper()}\n"
            f"Runs: {run_text}\n"
            "No habia etapas pendientes para ejecutar."
        )
        self._send_telegram(f"job_{self.state.get('job_id')}_no_work", message)
