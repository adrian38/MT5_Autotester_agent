"""El resultado publicado de una auditoria y el cierre de su uso.

`live_audit.py` se paso de las 600 lineas. Lo que sale aqui es el tramo final
del uso: construir el resultado que ve la pantalla -comparable o no-, devolver
cada terminal a su cuenta de restauracion, anotar en que cuenta quedo y fijar
el estado terminal. Lo que se queda alli es el arranque, la parada, el estado
y el hilo.

Es un mixin como los otros cinco de `LiveAuditController`, y el fichero
empieza por `live_audit` a proposito: la guarda de paridad del manager lee el
auditor del agente concatenando `live_audit*.py`, asi que partirlo no la deja
comprobando una fraccion de lo que comprobaba.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import utc_now
from .live_audit_helpers import RUNNING_STATUSES, _redact_runner_output


class LiveAuditOutcomeMixin:
    def _not_comparable_result(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        real_trades: list[dict[str, Any]], tester_trades: list[dict[str, Any]], quality: float | None,
    ) -> dict[str, Any]:
        """Resultado cuando la calidad del historial no permite comparar."""
        result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
        result.update(
            status="not_comparable", status_label="NO COMPARABLE", matched_trades=0,
            discrepancies=0, stalled_strategies=0,
            summary=("MT5 no informó History Quality." if quality is None else
                     f"History Quality {quality:.2f}% inferior al mínimo {request['min_tick_history_quality_pct']:.2f}%.")
        )
        return result

    def _compared_result(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        real_trades: list[dict[str, Any]], tester_trades: list[dict[str, Any]], quality: float | None,
        symbol_points: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        """Resultado de la comparacion completa entre cuenta real y tester."""
        comparison = self._compare(real_trades, tester_trades, symbol_points, request, strategies)
        result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
        result.update(comparison)
        invalid_tester = sum((comparison.get("comparison_detail") or {}).get("tester_data_issues", {}).values())
        result["summary"] = (
            f"{comparison['matched_trades']} parejas alineadas, "
            f"{comparison['within_tolerance_trades']} dentro de todas las tolerancias y "
            f"{comparison['discrepancies']} discrepancias; "
            f"{comparison['stalled_strategies']} estrategia(s) sin continuidad"
            + (f"; {invalid_tester} operación(es) tester con tiempos inválidos." if invalid_tester else ".")
        )
        result["status"] = result["status_label"] = "completed"
        result["status_label"] = "COMPLETADA"
        return result

    @staticmethod
    def _comparison_detail_log(result: dict[str, Any]) -> str:
        """Resumen en una linea del detalle por estrategia de la comparacion."""
        detail = result.get("comparison_detail") or {}
        return "; ".join(
            f"{key}={detail[key]}" for key in (
                "matched_by_strategy", "within_tolerance_by_strategy", "deviating_by_strategy",
                "missing_by_strategy", "unmatched_real", "deviation_reasons", "tester_data_issues",
            ) if detail.get(key)
        )

    def _restore_terminals(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Devuelve cada terminal tocada a su cuenta de restauracion."""
        try:
            return self._restore_tester_login(request)
        except Exception as exc:
            # Un fallo aquí no puede tapar el resultado de la auditoría.
            return [{
                "terminal": "desconocido", "mt5_path": "", "section": "",
                "expected_login": str(request.get("restore_login") or ""),
                "expected_server": str(request.get("restore_server") or ""),
                "login": None, "server": None, "restored": False,
                "error": _redact_runner_output(
                    str(exc), str(request.get("tester_password") or ""),
                    str(request.get("source_password") or ""),
                    str(request.get("restore_password") or ""),
                ),
            }]

    def _record_restore(
        self, audit_key: str, audit_id: str, restored: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Anota la cuenta en que quedo cada terminal y devuelve las no restauradas."""
        self._log(audit_key, "Cuenta dejada en cada terminal: " + "; ".join(
            f"{row['terminal']} → {row['expected_login']} ({row['expected_server']})"
            if row["restored"] else
            f"{row['terminal']} → SIN RESTAURAR: {row['error']}"
            for row in restored
        ))
        with self.lock:
            raw = self.states[audit_key]
            raw["terminal_restore"] = restored
            last_result = raw.get("last_result")
            if isinstance(last_result, dict) and str(last_result.get("audit_id") or "") == audit_id:
                last_result["terminal_restore"] = restored
            last_payload = raw.get("last_payload")
            if isinstance(last_payload, dict) and str(last_payload.get("audit_id") or "") == audit_id:
                last_payload["terminal_restore"] = restored
            self._persist()
        return [row for row in restored if not row["restored"]]

    def _close_audit_state(
        self, request: dict[str, Any], terminal_status: str, unrestored: list[dict[str, Any]]
    ) -> None:
        """Fija el estado final de la auditoria y avisa de terminales sin restaurar."""
        audit_key = request["audit_key"]
        with self.lock:
            raw = self.states[audit_key]
            # Cualquier estado en curso, no solo `finalizing` y `resuming`: si
            # algo piso el estado mientras se restauraban las terminales, no
            # cerrar aqui deja la auditoria viva para siempre. Lo que esta
            # guarda protege es un estado **terminal** ya publicado.
            if str(raw.get("status")) in RUNNING_STATUSES:
                raw.update(
                    status=terminal_status,
                    progress_text=str(
                        (raw.get("last_result") or {}).get("summary")
                        or raw.get("error") or "Auditoría finalizada."
                    ),
                )
            if unrestored:
                # No cambia el veredicto de la comparación, pero el usuario tiene
                # que enterarse sin abrir los logs: el terminal quedó en otra cuenta.
                raw["progress_text"] = str(raw.get("progress_text") or "") + (
                    " ⚠ "
                    + ", ".join(str(row["terminal"]) for row in unrestored)
                    + f" no quedó en la cuenta configurada {request['restore_login']}."
                )
            raw["finished_at"] = utc_now()
            self._persist()

    def _finish_run(
        self, request: dict[str, Any], audit_id: str, terminal_status: str, paused_by_auditor: bool
    ) -> None:
        """Restaura terminales, reanuda el pipeline y cierra el estado."""
        audit_key = request["audit_key"]
        # La cuenta activa de un terminal es estado persistente de MT5. Cada
        # terminal que tocó la auditoría se devuelve a la cuenta independiente
        # configurada para restauración antes de reanudar el pipeline.
        restored = self._restore_terminals(request)
        with self.lock:
            self.real_account_terminals.pop(audit_key, None)
        unrestored = self._record_restore(audit_key, audit_id, restored) if restored else []
        if paused_by_auditor:
            try:
                self._update(audit_key, "resuming", "Reanudando el proceso que pausó el auditor.", "Reanudación solicitada")
                self.owner.resume()
            except Exception as exc:
                self._update(audit_key, "failed", f"La auditoría terminó, pero no se pudo reanudar: {exc}", str(exc), error=str(exc))
        self._close_audit_state(request, terminal_status, unrestored)
        if getattr(self.owner, "queue", None):
            self.owner._schedule_queue_drain()
