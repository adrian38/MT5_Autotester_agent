from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.weights import NON_PARAMETER_CHANGE_KEYS, parameter_mutation_keys


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseRepairMixin:
    """Reparacion de claves de mutacion y de veredictos Final Tick."""

    def scan_non_parameter_mutation_keys(self, conn) -> list[dict]:
        """Find persisted execution-context keys incorrectly labelled mutations."""

        try:
            rows = conn.execute(
                """select id, run_id, mutated_keys
                   from candidates
                   where coalesce(mutated_keys, '') != ''
                   order by run_id, id"""
            ).fetchall()
        except sqlite3.Error:
            return []

        changes: list[dict] = []
        for row in rows:
            previous = str(row["mutated_keys"] or "")
            raw_keys = tuple(key.strip() for key in previous.split(";") if key.strip())
            removed = tuple(key for key in raw_keys if key in NON_PARAMETER_CHANGE_KEYS)
            if not removed:
                continue
            changes.append(
                {
                    "candidate_id": int(row["id"]),
                    "run_id": int(row["run_id"]),
                    "previous_mutated_keys": previous,
                    "mutated_keys": ";".join(parameter_mutation_keys(previous)),
                    "removed_keys": list(removed),
                }
            )
        return changes

    def apply_non_parameter_mutation_key_updates(self, conn, changes: list[dict]) -> int:
        """Apply a reviewed repair without overwriting concurrent row changes."""

        updated = 0
        for item in changes:
            cursor = conn.execute(
                """update candidates
                   set mutated_keys=?
                   where id=? and mutated_keys=?""",
                (
                    item["mutated_keys"],
                    item["candidate_id"],
                    item["previous_mutated_keys"],
                ),
            )
            updated += max(0, int(cursor.rowcount))
        conn.commit()
        return updated

    def _repair_non_parameter_mutation_keys(self) -> None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Reparar claves de mutacion", "No existe memoria UBS.")
            return

        conn = connect_memory(memory_path)
        try:
            changes = self.scan_non_parameter_mutation_keys(conn)
        finally:
            conn.close()

        if not changes:
            messagebox.showinfo(
                "Reparar claves de mutacion",
                "Nada que hacer: no hay claves de contexto guardadas como mutaciones.",
            )
            self.status_text.set("Claves de mutacion: memoria al dia")
            return

        runs = sorted({item["run_id"] for item in changes})
        examples = "\n".join(
            f"  #{item['candidate_id']} (run {item['run_id']}): "
            f"{item['previous_mutated_keys']} -> {item['mutated_keys'] or '(vacio)'}"
            for item in changes[:12]
        )
        if len(changes) > 12:
            examples += f"\n  ... (+{len(changes) - 12})"
        if not messagebox.askyesno(
            "Reparar claves de mutacion",
            "Se quitaran de mutated_keys solo las claves de contexto de ejecucion "
            f"({', '.join(sorted(NON_PARAMETER_CHANGE_KEYS))}).\n\n"
            f"Filas afectadas: {len(changes)}\n"
            f"Runs afectados: {len(runs)}\n\n"
            f"{examples}\n\n"
            "mutation_details_json y todos los resultados de backtest se conservaran. "
            "Se guardara una auditoria reversible. ¿Continuar?",
        ):
            return

        audit_path = self._write_mutation_key_repair_audit(memory_path, changes)
        conn = connect_memory(memory_path)
        try:
            updated = self.apply_non_parameter_mutation_key_updates(conn, changes)
        finally:
            conn.close()

        self.status_text.set(
            f"Claves de mutacion reparadas: {updated}/{len(changes)}; auditoria={audit_path.name}"
        )
        messagebox.showinfo(
            "Reparar claves de mutacion",
            f"Filas reparadas: {updated} de {len(changes)}.\n"
            "Los detalles del retarget y los resultados historicos se conservaron.\n\n"
            f"Auditoria: {audit_path}",
        )
        self._refresh_ubs_universe()

    def _write_mutation_key_repair_audit(self, memory_path: Path, changes: list[dict]) -> Path:
        folder = memory_path.parent / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"mutation_key_repair_{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "memory": str(memory_path),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "changes": changes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    # ----- Final Tick: estados incoherentes con su propio veredicto -----------

    FINAL_TICK_STATUS_TABLES = (
        ("candidate_final_tick", "Final Tick"),
        ("candidate_final_tick_6m", "Final Tick 6M"),
    )
    # Solo se reescriben estos: son los tres que produce un veredicto de
    # similitud. Cualquier otro (no_report, parse_error, report_mismatch,
    # no_trades, no_history, pending_ohlc_trades, ...) lo decide una etapa
    # anterior a la comparacion y no se toca aunque haya un blob guardado.
    FINAL_TICK_VERDICT_STATUSES = frozenset({"accepted", "rejected", "pending_history_quality"})

    def scan_final_tick_status_mismatches(self, conn) -> list[dict]:
        """Filas cuyo status no concuerda con el veredicto de su similarity_json.

        El veredicto se recalcula con la misma funcion que usa el agente al
        escribirlo, asi que una fila solo sale aqui si el status guardado no se
        puede derivar del blob que la acompana.
        """

        from ubs_agent import final_tick_status_from_similarity

        found: list[dict] = []
        for table, label in self.FINAL_TICK_STATUS_TABLES:
            try:
                rows = conn.execute(
                    f"select candidate_id, run_id, status, accepted, similarity_json from {table}"
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                status = str(row["status"] or "")
                if status not in self.FINAL_TICK_VERDICT_STATUSES:
                    continue
                raw = row["similarity_json"]
                if raw is None or not str(raw).strip():
                    continue
                try:
                    similarity = json.loads(str(raw))
                except (TypeError, ValueError):
                    continue
                expected = final_tick_status_from_similarity(similarity)
                if expected is None or expected == status:
                    continue
                found.append(
                    {
                        "table": table,
                        "label": label,
                        "candidate_id": int(row["candidate_id"]),
                        "run_id": row["run_id"],
                        "stored_status": status,
                        "stored_accepted": row["accepted"],
                        "expected_status": expected,
                        "expected_accepted": 1 if expected == "accepted" else 0,
                        "reasons": list(similarity.get("reasons") or ()),
                    }
                )
        return found

    def _recompute_final_tick_row(self, row, table, label, six_month, changes) -> None:
        """Veredicto recalculado de una fila Final Tick."""
        from ubs.score import ScoreResult, run_is_lossless
        from ubs_agent import (
            LosslessControlGate,
            final_tick_similarity,
            final_tick_status_from_similarity,
        )
        status = str(row["status"] or "")
        if status not in self.FINAL_TICK_VERDICT_STATUSES:
            return
        try:
            ohlc_raw = json.loads(str(row["ohlc_metrics_json"] or "{}"))
            tick_raw = json.loads(str(row["real_tick_metrics_json"] or "{}"))
            if not ohlc_raw or not tick_raw:
                return
            ohlc = ScoreResult.from_json(ohlc_raw)
            tick = ScoreResult.from_json(tick_raw)
        except (TypeError, ValueError):
            return

        stored_max_pf = float(row["max_pf_delta_pct"] or 35.0)
        # min_profit_factor viene del score_config que la propia fila
        # guarda, no de la configuracion actual de la UI.
        min_model_pf = None
        if six_month:
            try:
                min_model_pf = float(tick_raw.get("score_config", {}).get("min_profit_factor", 1.20))
            except (AttributeError, TypeError, ValueError):
                min_model_pf = 1.20
        similarity = final_tick_similarity(
            ohlc,
            tick,
            min_history_quality=float(row["min_history_quality"] or 80.0),
            max_net_delta_pct=float(row["max_net_delta_pct"] or 35.0),
            max_pf_delta_pct=min(stored_max_pf, 30.0) if six_month else stored_max_pf,
            max_dd_delta_pct=float(row["max_dd_delta_pct"] or 35.0),
            max_trades_delta_pct=float(row["max_trades_delta_pct"] or 35.0),
            min_model_profit_factor=min_model_pf,
            lossless_control_gate=LosslessControlGate() if six_month else None,
        )
        expected = final_tick_status_from_similarity(similarity)
        if expected is None or expected == status:
            return
        changes.append(
            {
                "table": table,
                "label": label,
                "candidate_id": int(row["candidate_id"]),
                "run_id": row["run_id"],
                "stored_status": status,
                "stored_accepted": row["accepted"],
                "stored_similarity_json": row["similarity_json"],
                "expected_status": expected,
                "expected_accepted": 1 if expected == "accepted" else 0,
                "similarity_json": json.dumps(similarity, ensure_ascii=True, sort_keys=True),
                "reasons": list(similarity.get("reasons") or ()),
                "cause": "ohlc_lossless" if run_is_lossless(ohlc) else "reglas_cambiadas",
            }
        )

    def _recompute_final_tick_table(self, conn, table, label, changes) -> None:
        """Veredictos recalculados de una tabla Final Tick."""
        six_month = table == "candidate_final_tick_6m"
        try:
            rows = conn.execute(
                f"""select candidate_id, run_id, status, accepted, similarity_json,
                           ohlc_metrics_json, real_tick_metrics_json, min_history_quality,
                           max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
                           max_trades_delta_pct
                    from {table}"""
            ).fetchall()
        except sqlite3.Error:
            return
        for row in rows:
            self._recompute_final_tick_row(row, table, label, six_month, changes)

    def recompute_final_tick_verdicts(self, conn) -> list[dict]:
        """Filas cuyo veredicto guardado ya no es el que produce el codigo actual.

        Recalcula ``final_tick_similarity`` sobre las metricas guardadas, pero
        **con los umbrales que guarda cada fila**, no con los de una invocacion
        global. Eso aisla lo que cambia por el codigo de lo que cambiaria por
        haber movido un umbral, que es justo lo que un rescore por CLI mezcla.

        No abre MT5 ni reparsea reportes: solo vuelve a comparar las dos patas
        ya medidas. Las filas sin metricas de tick (fallo tecnico pendiente de
        reintento) se saltan solas porque no hay nada que comparar.
        """


        changes: list[dict] = []
        for table, label in self.FINAL_TICK_STATUS_TABLES:
            self._recompute_final_tick_table(conn, table, label, changes)
        return changes

    def six_month_rows_to_queue(self, conn, candidate_ids: list[int]) -> list[dict]:
        """Candidatos que pasan a elegibles para 6M y todavia no tienen fila.

        Al desbloquear el Final Tick corto, la etapa 6M ya los recogeria (su
        filtro es ``probe.status in ('accepted','pending_ohlc_trades')`` y un
        estado 6M vacio cuenta como reintentable). Lo que no habria es rastro:
        quedarian en el cubo "sin 6M" de la pestana, indistinguibles de los que
        nunca fueron elegibles. Se les escribe una fila ``pending`` —estado que
        ya esta en ``FINAL_TICK_RETRYABLE_STATUSES``, o sea que la etapa lo
        reprocesa igual— para que se lean como trabajo encolado a proposito.
        """

        if not candidate_ids:
            return []
        placeholders = ",".join("?" for _id in candidate_ids)
        try:
            rows = conn.execute(
                f"""
                select c.id as candidate_id, c.run_id, c.target_symbol, c.symbol, c.period
                from candidates c
                join candidate_robustness cr on cr.candidate_id = c.id and cr.status='accepted'
                left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
                where c.id in ({placeholders})
                  and c.status='accepted'
                  and ft6.candidate_id is null
                order by c.run_id, c.id
                """,
                candidate_ids,
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "candidate_id": int(row["candidate_id"]),
                "run_id": int(row["run_id"]),
                "symbol": row["target_symbol"] or row["symbol"],
                "period": row["period"],
            }
            for row in rows
        ]

    def apply_final_tick_state_updates(self, conn, status_only, recomputed, queued) -> None:
        """Escribe los tres bloques. Sin UI: el boton y cualquier ejecucion
        manual pasan por aqui, para que no haya dos versiones de la escritura."""

        for item in status_only:
            conn.execute(
                f"update {item['table']} set status=?, accepted=? where candidate_id=?",
                (item["expected_status"], item["expected_accepted"], item["candidate_id"]),
            )
        for item in recomputed:
            conn.execute(
                f"update {item['table']} set status=?, accepted=?, similarity_json=? "
                "where candidate_id=?",
                (
                    item["expected_status"],
                    item["expected_accepted"],
                    item["similarity_json"],
                    item["candidate_id"],
                ),
            )
        # Fila 6M vacia salvo el estado: no se ha medido nada todavia, y
        # from_date/to_date vacios dejan claro que no hay ventana corrida.
        evaluated_at = datetime.now().isoformat(timespec="seconds")
        for item in queued:
            conn.execute(
                """insert into candidate_final_tick_6m
                       (candidate_id, run_id, status, accepted, evaluated_at)
                   values (?, ?, 'pending', 0, ?)
                   on conflict(candidate_id) do nothing""",
                (item["candidate_id"], item["run_id"], evaluated_at),
            )
        conn.commit()

    def _final_tick_repair_summary(self, mismatches, recomputed, queued):
        """Resumen y lineas de detalle de la reparacion Final Tick."""
        by_cause: dict[str, int] = {}
        for item in recomputed:
            by_cause[item["cause"]] = by_cause.get(item["cause"], 0) + 1
        summary = []
        if mismatches:
            summary.append(
                f"A) Estados incoherentes con su propio blob: {len(mismatches)}\n"
                "   Se corrige status/accepted. El similarity_json no se toca."
            )
        if recomputed:
            detail = "\n".join(
                f"     - {'control OHLC sin perdidas' if cause == 'ohlc_lossless' else 'reglas de comparacion cambiadas despues de evaluarlas'}: {count}"
                for cause, count in sorted(by_cause.items())
            )
            summary.append(
                f"B) Veredictos obsoletos: {len(recomputed)}\n"
                "   Se vuelve a comparar con las metricas y los umbrales que ya\n"
                "   guarda cada fila (no se abre MT5). Reescribe status, accepted\n"
                f"   y similarity_json.\n{detail}"
            )
        if queued:
            runs = sorted({item["run_id"] for item in queued})
            summary.append(
                f"C) Se encolan para Final Tick 6M: {len(queued)}\n"
                "   Son candidatos que el corto descartaba y que ahora pasan a\n"
                "   elegibles. Se les crea fila 6M en estado 'pending'.\n"
                f"   Repartidos en {len(runs)} runs: {', '.join(str(r) for r in runs[:12])}"
                + (f", ... (+{len(runs) - 12})" if len(runs) > 12 else "")
                + "\n   OJO: la etapa 6M los procesa por run_id, y cada uno gasta\n"
                "   un backtest OHLC y uno de real tick a 6 meses."
            )
        lines = [
            f"  #{item['candidate_id']} ({item['label']}, run {item['run_id']}): "
            f"{item['stored_status']} -> {item['expected_status']}"
            for item in (mismatches + recomputed)[:12]
        ]
        total = len(mismatches) + len(recomputed)
        if total > 12:
            lines.append(f"  ... (+{total - 12})")
        return summary, lines, total
    def _apply_final_tick_status_repair(
        self, memory_path, mismatches, recomputed, queued, summary, total,
    ) -> None:
        """Escribe la auditoria, aplica los cambios y avisa del resultado."""
        audit_path = self._write_final_tick_status_repair_audit(
            memory_path,
            {"status_only": mismatches, "recomputed": recomputed, "queued_six_month": queued},
        )
        conn = connect_memory(memory_path)
        try:
            self.apply_final_tick_state_updates(conn, mismatches, recomputed, queued)
        finally:
            conn.close()

        self.status_text.set(
            f"Estados Final Tick actualizados: {total} "
            f"(incoherentes={len(mismatches)}, recalculados={len(recomputed)}, "
            f"encolados 6M={len(queued)}); auditoria={audit_path.name}"
        )
        messagebox.showinfo(
            "Actualizar estados Final Tick",
            f"Reescritas {total} filas: {len(mismatches)} incoherentes y "
            f"{len(recomputed)} recalculadas.\n"
            f"Encolados para Final Tick 6M en estado 'pending': {len(queued)}.\n\n"
            f"Auditoria: {audit_path}",
        )
        self._refresh_ubs_universe()

    def _repair_final_tick_status_mismatches(self) -> None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Actualizar estados Final Tick", "No existe memoria UBS.")
            return

        conn = connect_memory(memory_path)
        try:
            mismatches = self.scan_final_tick_status_mismatches(conn)
            recomputed = self.recompute_final_tick_verdicts(conn)
            unlocked = [
                item["candidate_id"]
                for item in (mismatches + recomputed)
                if item["table"] == "candidate_final_tick" and item["expected_status"] == "accepted"
            ]
            queued = self.six_month_rows_to_queue(conn, unlocked)
        finally:
            conn.close()

        # Una fila incoherente que ademas cambia de veredicto se resuelve por el
        # recalculo, que es la version mas informada: reescribe tambien el blob.
        recomputed_ids = {(item["table"], item["candidate_id"]) for item in recomputed}
        mismatches = [
            item for item in mismatches
            if (item["table"], item["candidate_id"]) not in recomputed_ids
        ]

        if not mismatches and not recomputed and not queued:
            messagebox.showinfo(
                "Actualizar estados Final Tick",
                "Nada que hacer: los estados de Final Tick y Final Tick 6M concuerdan "
                "con su similarity_json y con lo que produce el codigo actual.",
            )
            self.status_text.set("Final Tick: estados al dia")
            return

        summary, lines, total = self._final_tick_repair_summary(
            mismatches, recomputed, queued,
        )
        if not messagebox.askyesno(
            "Actualizar estados Final Tick",
            "\n\n".join(summary)
            + f"\n\nTotal a reescribir: {total}"
            + (f" | filas 6M nuevas: {len(queued)}" if queued else "")
            + "\n\n"
            + "\n".join(lines)
            + "\n\nSe guardara un fichero de auditoria con el antes/despues "
            "(incluido el similarity_json anterior). ¿Continuar?",
        ):
            return

        self._apply_final_tick_status_repair(
            memory_path, mismatches, recomputed, queued, summary, total,
        )

    def _write_final_tick_status_repair_audit(self, memory_path: Path, groups: dict) -> Path:
        """Deja el antes/despues en disco para poder deshacer el arreglo.

        Copiar la memoria entera no es razonable (cientos de MB para tocar unas
        pocas filas), pero un arreglo que reescribe estados sin dejar rastro
        tampoco: esto guarda lo justo para revertirlo fila a fila, incluido el
        ``similarity_json`` anterior de las que se recalculan (esas si pierden
        su blob original al reescribirse).
        """

        folder = memory_path.parent / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"final_tick_status_repair_{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "memory": str(memory_path),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    **groups,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
