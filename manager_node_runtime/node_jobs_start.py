"""Alta de trabajos: normaliza la peticion y arma el pipeline de etapas."""
from __future__ import annotations

import time
from typing import Any

from . import node_commands, node_settings
from .common import safe_int, utc_now
from .node_settings import CLEANUP_STAGES


class JobStartMixin:
    """Alta de trabajos: normaliza la peticion y arma el pipeline de etapas."""

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "guided_batch_id" in payload or "prepared_manifest" in payload:
            raise ValueError("Usar la entrada autenticada de lotes preparados")
        with self.lock:
            normalized = self._normalize_generation(payload)
            node_commands.build_generation_command(self.config, normalized)
            if self._busy() or self.queue:
                cycles = normalized["cycles"]
                mode = normalized.get("generation_mode", "production")
                return self._enqueue("generation", normalized, f"{cycles} ciclo(s) · {mode}")
            return self._start_generation(normalized)

    def _normalize_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload["generation_mode"] = node_settings.resolve_generation_mode(self.config, payload)
        random_seed = payload.get("random_seed")
        if random_seed is None or str(random_seed).strip() == "":
            payload["random_seed"] = None
        else:
            try:
                payload["random_seed"] = int(random_seed)
            except (TypeError, ValueError) as exc:
                raise ValueError("random_seed debe ser un entero o null") from exc
        cycles = safe_int(payload.get("cycles"), 1, minimum=1, maximum=100)
        payload["cycles"] = cycles
        run_robustness = bool(payload.get("run_robustness", False))
        run_final_tick = bool(payload.get("run_final_tick", False))
        run_final_tick_6m = bool(payload.get("run_final_tick_6m", False))
        run_regression = (
            bool(payload.get("run_regression", False))
            and payload["generation_mode"] == "production"
        )
        if run_regression:
            run_final_tick_6m = True
            run_final_tick = True
            run_robustness = True
        elif run_final_tick_6m:
            run_final_tick = True
            run_robustness = True
        elif run_final_tick:
            run_robustness = True
        payload["run_robustness"] = run_robustness
        payload["run_final_tick"] = run_final_tick
        payload["run_final_tick_6m"] = run_final_tick_6m
        payload["run_regression"] = run_regression
        payload["repair_after_generation"] = bool(payload.get("repair_after_generation", False))
        payload["repair_max_workers"] = safe_int(
            payload.get("repair_max_workers"),
            safe_int(payload.get("max_workers"), 1, minimum=1, maximum=64),
            minimum=1,
            maximum=64,
        )
        payload["repair_phase2_max_workers"] = safe_int(
            payload.get("repair_phase2_max_workers"), 1, minimum=1, maximum=64
        )
        payload["repair_attempts"] = safe_int(payload.get("repair_attempts"), 1, minimum=1, maximum=20)
        payload["cleanup_after_run"] = node_settings.cleanup_after_run_enabled(self.config, payload)
        return payload

    @staticmethod
    def _generation_pipeline(payload: dict[str, Any]) -> list[dict[str, Any]]:
        cycles = payload["cycles"]
        run_robustness = payload["run_robustness"]
        run_final_tick = payload["run_final_tick"]
        run_final_tick_6m = payload["run_final_tick_6m"]
        run_regression = payload["run_regression"]
        repair_after_generation = payload["repair_after_generation"]
        repair_phase_workers = (
            payload["repair_max_workers"], payload["repair_phase2_max_workers"],
        )
        repair_attempts = payload["repair_attempts"]
        cleanup_after_run = payload["cleanup_after_run"]
        pipeline: list[dict[str, Any]] = []
        for cycle in range(1, cycles + 1):
            pipeline.append({"action": "generation", "cycle": cycle, "run_id": None})
            # Complete the ordinary run once with its own worker limit. Repair is
            # a later pass over the finished run; it must never replace or split
            # these stages.
            if run_robustness:
                pipeline.append({"action": "robustness", "cycle": cycle, "run_id": None})
            if run_final_tick:
                pipeline.append({"action": "final_tick", "cycle": cycle, "run_id": None})
            if run_final_tick_6m:
                pipeline.append({"action": "final_tick_6m", "cycle": cycle, "run_id": None})
            if run_regression:
                pipeline.append({"action": "regression", "cycle": cycle, "run_id": None})
            if repair_after_generation:
                repair_actions = ["result"]
                if run_robustness:
                    repair_actions.append("robustness")
                if run_final_tick:
                    repair_actions.extend(["final_tick", "final_tick_quality"])
                if run_final_tick_6m:
                    repair_actions.extend(["final_tick_6m", "final_tick_6m_quality"])
                if run_regression:
                    repair_actions.append("regression")
                # Cada intento se parte en dos fases sobre las mismas etapas: la
                # primera con los terminales de reparacion y la segunda con los
                # suyos. Todas las etapas son «pending-only», asi que la segunda
                # solo trabaja lo que la primera dejo pendiente y se omite sin
                # lanzar proceso cuando no queda nada.
                pipeline.extend(
                    {
                        "action": action, "cycle": cycle, "run_id": None,
                        "attempt": attempt, "phase": phase, "max_workers": workers,
                    }
                    for attempt in range(1, repair_attempts + 1)
                    for phase, workers in enumerate(repair_phase_workers, start=1)
                    for action in repair_actions
                )
            if cleanup_after_run:
                pipeline.extend(
                    {"action": action, "cycle": cycle, "run_id": None}
                    for action in CLEANUP_STAGES
                )
        return pipeline

    def _start_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_generation(payload)
        pipeline = self._generation_pipeline(payload)
        command, cwd = node_commands.build_generation_command(self.config, payload)
        job_id = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        log_path = self.runtime_dir / f"generation_{job_id}.log"
        self.state = {
            "job_id": job_id, "status": "running", "pid": None,
            "started_at": utc_now(), "finished_at": None, "return_code": None,
            "request": payload, "command": command, "log_path": str(log_path), "error": None,
            "job_type": "generation", "pipeline": pipeline, "current_stage": "generation",
            "current_cycle": 1, "current_run_id": None, "completed_stages": [],
            "stage_return_codes": {}, "commands": {"cycle_1_generation": command},
            "cycle_run_ids": {}, "skipped_stages": [], "stage_pending_counts": {},
            "telegram_notifications": [], "cleanup_failed": False,
        }
        self._launch_step(0, command, cwd, log_path, first=True)
        return {**dict(self.state), "queued": False, "task_queue": self._queue_snapshot()}

    def start_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = self._normalize_repair(payload)
            if self._busy() or self.queue:
                run_ids = normalized["run_ids"]
                attempts = normalized["repair_attempts"]
                return self._enqueue(
                    "repair", normalized,
                    f"Run(s) {', '.join(str(value) for value in run_ids)} · {attempts} intento(s)",
                )
            return self._start_repair(normalized)

    def _normalize_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = payload.get("run_ids")
        if not isinstance(requested, list):
            raise ValueError("run_ids debe ser una lista")
        run_ids = list(dict.fromkeys(safe_int(value, 0, minimum=0) for value in requested))
        run_ids = [value for value in run_ids if value > 0]
        if not run_ids:
            raise ValueError("Selecciona al menos un run terminado")
        payload = dict(payload)
        payload["run_ids"] = run_ids
        payload["max_workers"] = safe_int(
            payload.get("max_workers"), 1, minimum=1, maximum=64
        )
        payload["execute_backtests"] = True
        # `max_workers` son los terminales de la primera fase; la segunda tiene los
        # suyos y por omision es secuencial, que es el sentido de partir el intento.
        payload["repair_phase2_max_workers"] = safe_int(
            payload.get("repair_phase2_max_workers"), 1, minimum=1, maximum=64
        )
        payload["repair_attempts"] = safe_int(payload.get("repair_attempts"), 1, minimum=1, maximum=20)
        payload["retry_low_quality"] = bool(payload.get("retry_low_quality", True))
        # La etapa regresiva del flujo de Reparar es opcional: la elige la casilla
        # «Prueba regresiva» del diálogo del manager. Sin el campo se conserva el
        # comportamiento anterior a la casilla, que era ejecutarla siempre en los
        # runs de producción, para no cambiarle el flujo a un cliente antiguo ni a
        # una tarea que ya estaba en la cola.
        payload["run_regression"] = bool(payload.get("run_regression", True))
        payload["cleanup_after_run"] = node_settings.cleanup_after_run_enabled(self.config, payload)
        return payload

    @staticmethod
    def _repair_pipeline(
        payload: dict[str, Any], run_modes: dict[int, str | None]
    ) -> list[dict[str, Any]]:
        run_ids = payload["run_ids"]
        repair_attempts = payload["repair_attempts"]
        retry_low_quality = payload["retry_low_quality"]
        run_regression = payload["run_regression"]
        actions = ["result", "robustness", "final_tick"]
        if retry_low_quality:
            actions.append("final_tick_quality")
        actions.append("final_tick_6m")
        if retry_low_quality:
            actions.append("final_tick_6m_quality")
        # El reintento pertenece a un run seleccionado: se termina con ese run
        # antes de pasar al siguiente. Dentro de cada reintento hay dos fases,
        # distinguidas solo por cuantos terminales usan a la vez: la fase 1 recorre
        # todas sus etapas en paralelo y la fase 2 vuelve a recorrerlas sobre lo
        # que la primera dejo pendiente.
        phase_workers = (payload["max_workers"], payload["repair_phase2_max_workers"])
        pipeline: list[dict[str, Any]] = []
        for run_id in run_ids:
            run_actions = [*actions]
            if run_regression and run_modes[run_id] == "production":
                run_actions.append("regression")
            pipeline.extend(
                {
                    "action": action, "cycle": None, "run_id": run_id,
                    "attempt": attempt, "phase": phase, "max_workers": workers,
                }
                for attempt in range(1, repair_attempts + 1)
                for phase, workers in enumerate(phase_workers, start=1)
                for action in run_actions
            )
            if payload["cleanup_after_run"]:
                pipeline.extend(
                    {"action": action, "cycle": None, "run_id": run_id}
                    for action in CLEANUP_STAGES
                )
        return pipeline

    def _start_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_repair(payload)
        run_ids = payload["run_ids"]
        run_modes = {
            run_id: node_settings.stored_run_generation_mode(self.config, run_id)
            for run_id in run_ids
        }
        payload["run_generation_modes"] = {
            str(run_id): mode or "unknown" for run_id, mode in run_modes.items()
        }
        pipeline = self._repair_pipeline(payload, run_modes)
        job_id = "repair_" + time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        log_path = self.runtime_dir / f"{job_id}.log"
        self.state = {
            "job_id": job_id, "job_type": "repair", "status": "running", "pid": None,
            "started_at": utc_now(), "finished_at": None, "return_code": None,
            "request": payload, "command": None, "log_path": str(log_path), "error": None,
            "pipeline": pipeline, "current_stage": None, "current_cycle": None,
            "current_run_id": None, "current_attempt": None, "current_phase": None,
            "completed_stages": [], "skipped_stages": [],
            "stage_return_codes": {}, "stage_pending_counts": {}, "commands": {}, "cycle_run_ids": {},
            "telegram_notifications": [], "cleanup_failed": False,
        }
        try:
            launched = self._launch_next_runnable(0, log_path, first=True)
        except Exception as exc:
            self.state["error"] = str(exc)
            self.state["return_code"] = 1
            self.state["finished_at"] = utc_now()
            self.state["status"] = "failed"
            self._persist()
            raise
        if not launched:
            self._complete(0)
        return {**dict(self.state), "queued": False, "task_queue": self._queue_snapshot()}

    def start_regression(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            normalized = self._normalize_regression(payload)
            if self._busy() or self.queue:
                run_ids = normalized["run_ids"]
                return self._enqueue(
                    "regression", normalized,
                    f"Run(s) {', '.join(str(value) for value in run_ids)} · solo regresiva",
                )
            return self._start_regression(normalized)

    def _normalize_regression(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested = payload.get("run_ids")
        if not isinstance(requested, list):
            raise ValueError("run_ids debe ser una lista")
        run_ids = list(dict.fromkeys(safe_int(value, 0, minimum=0) for value in requested))
        run_ids = [value for value in run_ids if value > 0]
        if not run_ids:
            raise ValueError("Selecciona al menos un run terminado")
        payload = dict(payload)
        payload["run_ids"] = run_ids
        payload["max_workers"] = safe_int(
            payload.get("max_workers"), 1, minimum=1, maximum=64
        )
        payload["execute_backtests"] = True
        payload["cleanup_after_run"] = node_settings.cleanup_after_run_enabled(self.config, payload)
        return payload

    def _start_regression(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalize_regression(payload)
        pipeline: list[dict[str, Any]] = []
        for run_id in payload["run_ids"]:
            pipeline.append({
                "action": "regression", "cycle": None, "run_id": run_id, "attempt": 1,
            })
            if payload["cleanup_after_run"]:
                pipeline.extend(
                    {"action": action, "cycle": None, "run_id": run_id}
                    for action in CLEANUP_STAGES
                )
        job_id = "regression_" + time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
        log_path = self.runtime_dir / f"{job_id}.log"
        self.state = {
            "job_id": job_id, "job_type": "regression", "status": "running", "pid": None,
            "started_at": utc_now(), "finished_at": None, "return_code": None,
            "request": payload, "command": None, "log_path": str(log_path), "error": None,
            "pipeline": pipeline, "current_stage": None, "current_cycle": None,
            "current_run_id": None, "current_attempt": None, "current_phase": None,
            "completed_stages": [], "skipped_stages": [],
            "stage_return_codes": {}, "stage_pending_counts": {}, "commands": {}, "cycle_run_ids": {},
            "telegram_notifications": [], "cleanup_failed": False,
        }
        try:
            launched = self._launch_next_runnable(0, log_path, first=True)
        except Exception as exc:
            self.state["error"] = str(exc)
            self.state["return_code"] = 1
            self.state["finished_at"] = utc_now()
            self.state["status"] = "failed"
            self._persist()
            raise
        if not launched:
            self._complete(0)
        return {**dict(self.state), "queued": False, "task_queue": self._queue_snapshot()}

    def start_cleanup(self) -> dict[str, Any]:
        with self.lock:
            node_settings.historical_cleanup_scripts(self.config)
            if self._busy() or self.queue:
                return self._enqueue(
                    "cleanup", {}, "Cierra MT5 y elimina tester/bases/history",
                )
            return self._start_cleanup()

    def _start_cleanup(self) -> dict[str, Any]:
        node_settings.historical_cleanup_scripts(self.config)
        pipeline = [{"action": action, "cycle": None, "run_id": None} for action in CLEANUP_STAGES]
        job_id = (
            "cleanup_" + time.strftime("%Y%m%d_%H%M%S")
            + f"_{time.time_ns() % 1_000_000:06d}"
        )
        log_path = self.runtime_dir / f"{job_id}.log"
        self.state = {
            "job_id": job_id, "job_type": "cleanup", "status": "running", "pid": None,
            "started_at": utc_now(), "finished_at": None, "return_code": None,
            "request": {}, "command": None, "log_path": str(log_path), "error": None,
            "pipeline": pipeline, "current_stage": "cleanup_tester", "current_cycle": None,
            "current_run_id": None, "current_attempt": None, "current_phase": None,
            "completed_stages": [],
            "skipped_stages": [], "stage_return_codes": {}, "stage_pending_counts": {},
            "commands": {}, "cycle_run_ids": {}, "telegram_notifications": [],
            "cleanup_failed": False,
        }
        self._launch_next_runnable(0, log_path, first=True)
        return {**dict(self.state), "queued": False, "task_queue": self._queue_snapshot()}
