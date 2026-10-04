from __future__ import annotations

import json
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseRiskRepairMixin:
    """Reaplicacion de la regla riesgo/beneficio a estados guardados."""

    RISK_PROFIT_REPAIR_TITLE = "Reaplicar regla riesgo/beneficio"

    def _confirm_risk_profit_repair(self, title: str) -> bool:
        """Pregunta que hace la reaplicacion y que no toca."""
        return messagebox.askyesno(
            title,
            "Se revisaran los estados guardados que la regla de riesgo/beneficio "
            "puede mover:\n"
            "  - Resultados rechazados unicamente por net profit.\n"
            "  - Robustez cuya unica causa absoluta es ese mismo net profit.\n\n"
            "Para cada uno se relee el equity drawdown de su reporte en disco, "
            "porque las filas anteriores a la regla no lo guardaron. En las de "
            "robustez se relee tambien la serie mensual, que es lo que permite "
            "medir la concentracion con la misma severidad que en resultados "
            "(quitar el 5% de los meses en vez de 3 fijos: en 17 meses son 3 de "
            "17, en 60 son 3 de 60). Esas filas tardan mas porque hay que "
            "parsear el reporte completo.\n\n"
            "No se abre MT5 y todavia no se escribe nada: primero veras el "
            "resumen. Puede tardar unos minutos. ¿Analizar ahora?",
        )

    def _repair_risk_profit_states(self) -> None:
        """Reaplica la via de riesgo a resultados y robustez ya guardados.

        El analisis relee el equity drawdown de cada reporte en disco (la regla
        lo necesita y las filas antiguas no lo guardaron), asi que va en un hilo
        con progreso. No escribe nada hasta que el resumen se confirma.
        """

        title = self.RISK_PROFIT_REPAIR_TITLE
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo(title, "No existe memoria UBS.")
            return
        if not self._confirm_risk_profit_repair(title):
            return
        from ubs.risk_profit import RiskProfitConfig
        from ubs.risk_profit_repair import scan_risk_profit_restatements

        policy = RiskProfitConfig()
        q: queue.Queue = queue.Queue()

        def _worker() -> None:
            try:
                conn = connect_memory(memory_path)
                try:
                    plan = scan_risk_profit_restatements(
                        conn,
                        policy=policy,
                        progress=lambda done, total, label: q.put(
                            ("progress", done - 1, total, label or "revisando memoria")
                        ),
                    )
                finally:
                    conn.close()
            except Exception as exc:  # el dialogo lo muestra y se cierra solo
                q.put(("failed", f"No se pudo analizar la memoria:\n{exc}"))
                return
            q.put(("done", plan))

        def _on_done(payload: tuple) -> None:
            self._finish_risk_profit_repair(memory_path, payload[0])

        dlg, poll = self._ubs_seed_progress_dialog(
            title,
            "Analizando estados guardados",
            "Releyendo el equity drawdown de los reportes implicados. "
            "No se modifica nada durante el analisis.",
            0,
            q,
            _on_done,
        )
        threading.Thread(target=_worker, daemon=True).start()
        dlg.after(40, poll)

    def _risk_profit_repair_summary(self, plan, skipped):
        """Resumen y lineas de detalle del plan de reaplicacion."""
        summary = [
            f"Regla riesgo/beneficio, modo {plan.policy.get('mode', '?')}.\n"
            "Cada fila se rejuzga con los umbrales que ella misma guardo, asi que "
            "un cambio de criterio pendiente no viaja dentro de este arreglo.\n"
            "Solo se reescriben las filas que cambian de estado."
        ]
        if plan.base:
            summary.append(
                f"A) Resultados que pasan a aceptado: {len(plan.base)}\n"
                "   La via de riesgo sustituye el net profit por recuperacion sobre\n"
                "   equity, muestra y consistencia temporal. El resto de umbrales\n"
                "   (PF, DD, operaciones) se mantiene tal cual."
            )
        if plan.pending_robustness:
            runs = sorted({item["run_id"] for item in plan.pending_robustness})
            summary.append(
                f"   OJO: {len(plan.pending_robustness)} quedan aceptados sin fila de\n"
                "   robustez, o sea trabajo OOS nuevo (un backtest cada uno) en\n"
                f"   {len(runs)} runs: {', '.join(str(r) for r in runs[:12])}"
                + (f", ... (+{len(runs) - 12})" if len(runs) > 12 else "")
            )
        if plan.robustness:
            destinos = {}
            for item in plan.robustness:
                destinos[item["expected_status"]] = destinos.get(item["expected_status"], 0) + 1
            detalle = "\n".join(f"     - {status}: {count}" for status, count in sorted(destinos.items()))
            summary.append(
                f"B) Robustez que cambia de estado: {len(plan.robustness)}\n{detalle}\n"
                "   Se les guarda la evidencia releida del reporte (equity, "
                "concentracion\n   escalada, estabilidad y bootstrap), que es lo que "
                "sostiene el cambio.\n"
                "   'pending_risk_evidence' no es un rechazo: la comparacion contra la\n"
                "   ventana de construccion sigue incompleta."
            )
        if plan.base_evidence:
            summary.append(
                f"C) Resultados cuya evidencia acompana a un cambio: {len(plan.base_evidence)}\n"
                "   Mantienen su veredicto; se guarda el equity con el que se comparo\n"
                "   la robustez, para que el estado nuevo se derive de la memoria."
            )
        if plan.audit_only:
            summary.append(
                f"D) Revisadas y NO tocadas: {len(plan.audit_only)}\n"
                "   La regla no las mueve, asi que su fila no se reescribe. El porque\n"
                "   de cada una queda en el fichero de auditoria."
            )
        lines = [
            f"  #{item['candidate_id']} ({item['symbol']} {item['period']}, run {item['run_id']}): "
            f"{item['stored_status']} -> {item['expected_status']}"
            for item in (plan.base + plan.robustness)[:12]
        ]
        if plan.verdict_changes() > 12:
            lines.append(f"  ... (+{plan.verdict_changes() - 12})")
        return summary, lines
    def _apply_risk_profit_repair(self, memory_path, plan, title) -> None:
        """Escribe la auditoria, aplica el plan y refresca las pantallas."""
        from ubs.risk_profit_repair import apply_risk_profit_restatements

        audit_path = self._write_risk_profit_repair_audit(memory_path, plan)
        conn = connect_memory(memory_path)
        try:
            written = apply_risk_profit_restatements(conn, plan)
        finally:
            conn.close()

        total = sum(written.values())
        resumen = ", ".join(f"{group}={count}" for group, count in sorted(written.items()) if count)
        self.status_text.set(
            f"Regla riesgo/beneficio aplicada: {total} filas ({resumen}); auditoria={audit_path.name}"
        )
        messagebox.showinfo(
            title,
            f"Filas reescritas: {total} de {plan.rows_to_write()}.\n"
            f"Resultados a aceptado: {written['base']} | robustez con estado nuevo: "
            f"{written['robustness']} | evidencia base que los sostiene: "
            f"{written['base_evidence']}.\n"
            f"Revisadas sin tocar: {len(plan.audit_only)}.\n\n"
            f"Auditoria: {audit_path}",
        )
        for label, callback in (
            ("ubs_universe", self._refresh_ubs_universe),
            ("ubs_resultados", getattr(self, "_refresh_ubs_results", None)),
            ("ubs_robustez", getattr(self, "_refresh_ubs_robustness", None)),
        ):
            if callback is not None:
                self._safe_refresh(label, callback)

    def _finish_risk_profit_repair(self, memory_path: Path, plan) -> None:
        title = self.RISK_PROFIT_REPAIR_TITLE
        skipped = ", ".join(f"{reason}={count}" for reason, count in sorted(plan.skipped.items()))
        if plan.is_empty():
            messagebox.showinfo(
                title,
                "Nada que hacer: ninguna fila guardada cambia de estado con la "
                "regla de riesgo/beneficio.\n\n"
                f"Revisadas sin moverlas: {len(plan.audit_only)}.\n"
                + (f"Omitidas: {skipped}" if skipped else ""),
            )
            self.status_text.set("Regla riesgo/beneficio: estados al dia")
            return

        summary, lines = self._risk_profit_repair_summary(plan, skipped)
        if not messagebox.askyesno(
            title,
            "\n\n".join(summary)
            + (f"\n\nOmitidas: {skipped}" if skipped else "")
            + f"\n\nFilas a reescribir: {plan.rows_to_write()} "
            f"(cambian de veredicto: {plan.verdict_changes()})\n\n"
            + "\n".join(lines)
            + "\n\nSe guardara una auditoria con el antes/despues. ¿Aplicar?",
        ):
            self.status_text.set("Regla riesgo/beneficio: analisis descartado")
            return

        self._apply_risk_profit_repair(memory_path, plan, title)

    def _write_risk_profit_repair_audit(self, memory_path: Path, plan) -> Path:
        """Antes/despues fila a fila, para poder deshacer el arreglo.

        Incluye el ``metrics_json`` nuevo y, en robustez, el ``degradation_json``
        recalculado sobre equity: son los dos blobs que esta operacion
        sobrescribe.
        """

        folder = memory_path.parent / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"risk_profit_repair_{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "memory": str(memory_path),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    **plan.to_audit(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
