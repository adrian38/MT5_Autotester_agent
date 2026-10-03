from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from ubs.account import account_memory_path
from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_search_audit_base import (  # noqa: F401  fachada del modulo
    AUDIT_FINAL_STATUSES,
    audit_nonfinal_count,
)


class UBSSearchAuditWindowMixin:
    """Ventana de detalle de la auditoria y reinicio de filas pendientes."""

    def _on_ubs_audit_detail_double_click(self, event) -> None:
        tree = event.widget
        item = tree.focus()
        if not item:
            return
        values = tree.item(item, "values")
        title = str(values[0]) if values else "Auditoria run UBS"
        details = getattr(self, "ubs_audit_details", {})
        detail = details.get((id(tree), item), details.get(item, ""))
        if not detail:
            messagebox.showinfo("Auditoria run UBS", "Esta fila no tiene detalle adicional.")
            return
        self._show_ubs_audit_detail_window(title, detail)

    def _audit_detail_actions(self, window, detail_tree, header_labels, header_index,
                              inserted_items, title):
        """Barra de acciones de la ventana de detalle."""
        actions = tk.Frame(window, bg=self.colors["panel_alt"])
        actions.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        actions.columnconfigure(0, weight=1)
        can_mark_pending = {"ID", "TEST"}.issubset({label.strip().upper() for label in header_labels})
        id_index = header_index.get("ID")
        unique_candidate_ids = set()
        if id_index is not None:
            unique_candidate_ids = {
                str(detail_tree.item(item, "values")[id_index]).strip()
                for item in inserted_items
                if len(detail_tree.item(item, "values")) > id_index
                and str(detail_tree.item(item, "values")[id_index]).strip()
            }
        count_label = "Filas reporte" if can_mark_pending else "Filas"
        tk.Label(
            actions,
            text=f"{count_label}: {len(inserted_items)} | candidatos unicos: {len(unique_candidate_ids)}",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        next_column = 1
        if can_mark_pending:
            tk.Button(
                actions,
                text="Poner pendientes",
                bg=self.colors["accent"],
                fg="#ffffff",
                relief="flat",
                borderwidth=0,
                padx=10,
                pady=5,
                font=("Segoe UI", 9, "bold"),
                cursor="hand2",
                command=lambda: self._reset_ubs_audit_detail_rows_pending(title, detail_tree, window),
            ).grid(row=0, column=next_column, sticky="e", padx=(0, 6), pady=5)
            next_column += 1
        tk.Button(
            actions,
            text="Cerrar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=window.destroy,
        ).grid(row=0, column=next_column, sticky="e", padx=(0, 10), pady=5)

    def _audit_detail_tree(self, frame, header_labels):
        """Arbol de la ventana de detalle con sus columnas y etiquetas."""
        columns = tuple(f"c{index}" for index in range(len(header_labels)))
        detail_tree = ttk.Treeview(frame, columns=columns, show="headings", height=16, selectmode="extended")
        width_by_label = {
            "GEN": 55,
            "ID": 80,
            "SET": 360,
            "TEST": 120,
            "ESPERADO": 90,
            "REPORTE": 90,
            "HEADER": 210,
            "ARCHIVO": 390,
            "STATUS": 110,
            "BASE": 86,
            "ROBUST": 86,
            "FT CORTO": 86,
            "FT 6M": 86,
            "PESO": 86,
            "CHECK": 90,
            "RAZONES": 300,
        }
        for column in columns:
            label = header_labels[int(column[1:])]
            detail_tree.heading(column, text=label)
            detail_tree.column(column, width=width_by_label.get(label.upper(), 130), minwidth=42, anchor="w", stretch=False)
        detail_tree.tag_configure("rejected", foreground=self.colors["danger"])
        detail_tree.tag_configure("pending", foreground=self.colors["muted"])
        detail_tree.tag_configure("accepted", foreground=self.colors["accent"])
        return detail_tree, columns

    def _fill_audit_detail_rows(self, detail_tree, data_lines, header_labels):
        """Una fila del informe por linea de detalle."""
        inserted_items: list[str] = []
        header_index = {label.strip().upper(): idx for idx, label in enumerate(header_labels)}
        check_index = header_index.get("CHECK")
        status_index = header_index.get("STATUS") if "STATUS" in header_index else header_index.get("ESTADO")
        for line in data_lines:
            parts = line.split("\t")
            if len(parts) < len(columns):
                parts = [*parts, *([""] * (len(columns) - len(parts)))]
            check_value = str(parts[check_index]).strip().upper() if check_index is not None and len(parts) > check_index else ""
            status_value = str(parts[status_index]).strip().casefold() if status_index is not None and len(parts) > status_index else ""
            text = "\t".join(parts).casefold()
            if check_value == "OK":
                tag = "accepted"
            elif check_value:
                tag = "rejected"
            elif "sin_encabezado" in text or "pend" in text:
                tag = "pending"
            elif status_value in {"accepted", "aceptado", "ok"}:
                tag = "accepted"
            elif status_value in {"rejected", "rechazado", "revisar"}:
                tag = "rejected"
            else:
                tag = "pending"
            inserted_items.append(detail_tree.insert("", "end", values=parts[: len(columns)], tags=(tag,)))
        return inserted_items, header_index

    def _show_ubs_audit_detail_window(self, title: str, detail: str) -> None:
        parent = getattr(self, "root", self)
        window = tk.Toplevel(parent)
        window.title(title)
        window.geometry("1180x520")
        window.configure(bg=self.colors["panel"])
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        tk.Label(
            window,
            text=title,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        lines = [line for line in detail.splitlines() if line.strip()]
        if lines and "\t" in lines[0]:
            header_labels = [part.strip() or f"COL {idx + 1}" for idx, part in enumerate(lines[0].split("\t"))]
            data_lines = lines[1:]
        else:
            header_labels = ["GEN", "ID", "SET", "TEST", "ESPERADO", "REPORTE", "HEADER", "ARCHIVO"]
            data_lines = lines
        detail_tree, columns = self._audit_detail_tree(frame, header_labels)
        inserted_items, header_index = self._fill_audit_detail_rows(
            detail_tree, data_lines, header_labels,
        )
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=detail_tree.xview)
        detail_tree.configure(xscrollcommand=xscroll.set)
        detail_tree.grid(row=0, column=0, sticky="nsew")
        xscroll.grid(row=1, column=0, sticky="ew")

        self._audit_detail_actions(
            window, detail_tree, header_labels, header_index, inserted_items, title,
        )

    def _audit_reset_summary(self, stage_ids):
        """Texto por etapa de lo que se va a reiniciar."""
        summary = []
        if stage_ids["base"]:
            summary.append(f"Base/generacion: {len(stage_ids['base'])} candidato(s)")
        if stage_ids["robust"]:
            summary.append(f"Robustez: {len(stage_ids['robust'])} candidato(s)")
        if stage_ids["final_tick"]:
            summary.append(f"Final Tick corto: {len(stage_ids['final_tick'])} candidato(s)")
        if stage_ids["final_tick_6m"]:
            summary.append(f"Final Tick 6M: {len(stage_ids['final_tick_6m'])} candidato(s)")
        return summary

    def _audit_reset_stage_ids(self, detail_tree, selected_items, header_index):
        """Identificadores por etapa de las filas seleccionadas."""
        stage_ids: dict[str, set[int]] = {"base": set(), "robust": set(), "final_tick": set(), "final_tick_6m": set()}
        for item in selected_items:
            values = detail_tree.item(item, "values")
            if len(values) < 4:
                continue
            try:
                candidate_id = int(str(values[1]).strip())
            except (TypeError, ValueError):
                continue
            test_name = f"{values[3]} {title}".casefold()
            if "6m" in test_name:
                stage_ids["final_tick_6m"].add(candidate_id)
            elif "ft corto" in test_name or "final tick" in test_name:
                stage_ids["final_tick"].add(candidate_id)
            elif "robust" in test_name:
                stage_ids["robust"].add(candidate_id)
            elif "base" in test_name or "generacion" in test_name:
                stage_ids["base"].add(candidate_id)
        return stage_ids

    def _apply_audit_reset(self, broker, account_type, run_id, stage_ids,
                           selected_report_rows, window):
        """Aplica el reinicio en la memoria y refresca las pantallas."""
        memory_path = account_memory_path(BASE_DIR, account_type, broker)
        try:
            conn = connect_memory(memory_path)
            try:
                self._ensure_ubs_memory_schema(conn)
                changed = self._reset_ubs_candidate_stage_rows(conn, run_id, stage_ids)
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            messagebox.showerror("Auditoria run UBS", str(exc))
            return

        self.ubs_audit_status.set(
            "Pendientes actualizados: "
            + ", ".join(f"{stage}={count}" for stage, count in changed.items() if count)
        )
        window.destroy()
        self._run_ubs_audit_from_search()
        for label, callback_name in (
            ("ubs_results", "_refresh_ubs_results"),
            ("ubs_robustness", "_refresh_ubs_robustness"),
            ("ubs_final_tick", "_refresh_ubs_final_tick"),
            ("ubs_final_tick_6m", "_refresh_ubs_final_tick_6m"),
            ("ubs_regression", "_refresh_ubs_regression"),
            ("ubs_universe", "_refresh_ubs_universe"),
        ):
            callback = getattr(self, callback_name, None)
            if callback is not None:
                self._safe_refresh(label, callback)

    def _reset_ubs_audit_detail_rows_pending(
        self,
        title: str,
        detail_tree: ttk.Treeview,
        window: tk.Toplevel,
    ) -> None:
        selected_items = list(detail_tree.selection()) or list(detail_tree.get_children())
        if not selected_items:
            messagebox.showinfo("Auditoria run UBS", "No hay filas para poner pendientes.")
            return

        stage_ids = self._audit_reset_stage_ids(detail_tree, selected_items, header_index)
        total_ids = sum(len(ids) for ids in stage_ids.values())
        if total_ids == 0:
            messagebox.showinfo("Auditoria run UBS", "No pude identificar la prueba de esas filas.")
            return

        context = self._parse_ubs_account_context(self.ubs_audit_account.get())
        if context is None:
            messagebox.showerror("Auditoria run UBS", "Cuenta invalida.")
            return
        broker, account_type = context
        run_id = self._parse_ubs_audit_run_id(self.ubs_audit_run_id.get())
        if run_id <= 0:
            messagebox.showerror("Auditoria run UBS", "Run invalido.")
            return

        selected_report_rows = len(selected_items)
        summary = self._audit_reset_summary(stage_ids)
        if not messagebox.askyesno(
            "Poner pendientes",
            "Se marcaran como pendientes para que se vuelvan a ejecutar:\n\n"
            + "\n".join(summary)
            + f"\n\nFilas de reporte seleccionadas: {selected_report_rows}"
            + "\n\nNo se borraran candidatos, archivos .set ni reportes del disco.",
        ):
            return

        self._apply_audit_reset(
            broker, account_type, run_id, stage_ids, selected_report_rows, window,
        )

    def _stage_regression_pending(self, conn, run_id, scoped):
        """Marcado pendiente de la regresiva."""
        def mark_regression_pending(ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            now = datetime.now().isoformat(timespec="seconds")
            for candidate_id in valid_ids:
                conn.execute(
                    """
                    insert into candidate_regression (
                        candidate_id, run_id, status, accepted, report_path, score,
                        metrics_json, details_json, from_date, to_date,
                        positive_points, negative_points, points_applied, evaluated_at
                    ) values (?, ?, 'pending', null, null, null, null, null,
                              '2017.01.01', '2019.12.31', 80.0, -100.0, 0.0, ?)
                    on conflict(candidate_id) do update set
                        run_id=excluded.run_id,
                        status='pending',
                        accepted=null,
                        report_path=null,
                        score=null,
                        metrics_json=null,
                        details_json=null,
                        points_applied=0.0,
                        evaluated_at=excluded.evaluated_at
                    """,
                    (candidate_id, int(run_id), now),
                )
            return len(valid_ids)
        return (
            scoped, delete_stage, mark_stage_pending,
            mark_robust_pending, mark_regression_pending,
        )
        return mark_stage_pending, mark_robust_pending, mark_regression_pending
        return mark_robust_pending, mark_regression_pending
        return mark_regression_pending

    def _stage_pending_extra(self, conn, run_id, scoped):
        """Marcado pendiente de robustez y de regresiva."""
        def mark_robust_pending(ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            now = datetime.now().isoformat(timespec="seconds")
            for candidate_id in valid_ids:
                conn.execute(
                    """
                    insert into candidate_robustness (
                        candidate_id, run_id, status, report_path, score, accepted, metrics_json,
                        from_date, to_date, positive_bonus, negative_bonus, evaluated_at
                    ) values (?, ?, 'pending', null, null, null, null, '', '', 70.0, -70.0, ?)
                    on conflict(candidate_id) do update set
                        run_id=excluded.run_id,
                        status='pending',
                        report_path=null,
                        score=null,
                        accepted=null,
                        metrics_json=null,
                        from_date='',
                        to_date='',
                        evaluated_at=excluded.evaluated_at
                    """,
                    (candidate_id, int(run_id), now),
                )
            return len(valid_ids)

        mark_regression_pending = self._stage_regression_pending(conn, run_id, scoped)

    def _stage_pending_helpers(self, conn, run_id, scoped):
        """Marcado pendiente de Final Tick, robustez y regresiva."""
        def mark_stage_pending(table: str, ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            now = datetime.now().isoformat(timespec="seconds")
            for candidate_id in valid_ids:
                conn.execute(
                    f"""
                    insert into {table} (
                        candidate_id, run_id, status, accepted,
                        ohlc_report_path, real_tick_report_path,
                        ohlc_score, real_tick_score,
                        ohlc_metrics_json, real_tick_metrics_json, similarity_json,
                        history_quality, min_history_quality, from_date, to_date,
                        max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
                        evaluated_at
                    ) values (?, ?, 'pending', null, null, null, null, null, null, null, null, null, 80.0, '', '', 35.0, 35.0, 35.0, 35.0, ?)
                    on conflict(candidate_id) do update set
                        run_id=excluded.run_id,
                        status='pending',
                        accepted=null,
                        ohlc_report_path=null,
                        real_tick_report_path=null,
                        ohlc_score=null,
                        real_tick_score=null,
                        ohlc_metrics_json=null,
                        real_tick_metrics_json=null,
                        similarity_json=null,
                        history_quality=null,
                        from_date='',
                        to_date='',
                        evaluated_at=excluded.evaluated_at
                    """,
                    (candidate_id, int(run_id), now),
                )
            return len(valid_ids)

        mark_robust_pending, mark_regression_pending = (
            self._stage_pending_extra(conn, run_id, scoped)
        )

    def _stage_reset_helpers(self, conn, run_id, stage_ids):
        """Los cinco pasos que reinician una etapa guardada."""
        def scoped(ids: set[int]) -> list[int]:
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"select id from candidates where run_id=? and id in ({placeholders})",
                (int(run_id), *sorted(ids)),
            ).fetchall()
            return [int(row[0]) for row in rows]

        def delete_stage(table: str, ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            placeholders = ",".join("?" for _ in valid_ids)
            cur = conn.execute(
                f"delete from {table} where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *valid_ids),
            )
            return int(cur.rowcount or 0)

        mark_stage_pending, mark_robust_pending, mark_regression_pending = (
            self._stage_pending_helpers(conn, run_id, scoped)
        )

    def _reset_base_stage_rows(self, conn, run_id, stage_ids, scoped, changed):
        """Reinicio de la etapa base y de lo que cuelga de ella."""
        base_ids = set(scoped(stage_ids.get("base", set())))
        if base_ids:
            placeholders = ",".join("?" for _ in base_ids)
            conn.execute(
                f"delete from candidate_regression where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            conn.execute(
                f"delete from candidate_final_tick_6m where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            conn.execute(
                f"delete from candidate_final_tick where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            conn.execute(
                f"delete from candidate_robustness where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            cur = conn.execute(
                f"""
                update candidates
                set status='generated',
                    report_path=null,
                    score=null,
                    accepted=null,
                    metrics_json=null
                where run_id=? and id in ({placeholders})
                """,
                (int(run_id), *sorted(base_ids)),
            )
            changed["base"] = int(cur.rowcount or 0)

    def _reset_ubs_candidate_stage_rows(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        stage_ids: dict[str, set[int]],
    ) -> dict[str, int]:
        scoped, delete_stage, mark_stage_pending, mark_robust_pending, mark_regression_pending = (
            self._stage_reset_helpers(conn, run_id, stage_ids)
        )

        changed = {"base": 0, "robust": 0, "final_tick": 0, "final_tick_6m": 0, "regression": 0}

        self._reset_base_stage_rows(conn, run_id, stage_ids, scoped, changed)
        robust_ids = set(scoped(stage_ids.get("robust", set())))
        if robust_ids:
            delete_stage("candidate_regression", robust_ids)
            delete_stage("candidate_final_tick_6m", robust_ids)
            delete_stage("candidate_final_tick", robust_ids)
            changed["robust"] = mark_robust_pending(robust_ids)

        final_tick_ids = set(scoped(stage_ids.get("final_tick", set())))
        if final_tick_ids:
            delete_stage("candidate_regression", final_tick_ids)
            delete_stage("candidate_final_tick_6m", final_tick_ids)
            changed["final_tick"] = mark_stage_pending("candidate_final_tick", final_tick_ids)

        final_tick_6m_ids = set(scoped(stage_ids.get("final_tick_6m", set())))
        if final_tick_6m_ids:
            delete_stage("candidate_regression", final_tick_6m_ids)
            changed["final_tick_6m"] = mark_stage_pending("candidate_final_tick_6m", final_tick_6m_ids)

        regression_ids = set(scoped(stage_ids.get("regression", set())))
        if regression_ids:
            changed["regression"] = mark_regression_pending(regression_ids)

        return changed
