from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.account import (
    DEFAULT_BROKER,
    account_memory_path,
    normalize_account_type,
    normalize_broker,
)
from ubs.db import connect_memory
from ubs.manual_status import mark_candidates, sync_manual_accepted_candidate_copies
from ui.ubs_results_base import ubs_run_base_dates  # noqa: F401  fachada del modulo
from ui.ubs_results_compare import UBSResultsCompareMixin
from ui.ubs_results_export import UBSResultsExportMixin
from ui.ubs_results_format import UBSResultsFormatMixin
from ui.ubs_results_history import UBSResultsHistoryMixin
from ui.ubs_results_retry import UBSResultsRetryMixin
from ui.ubs_results_runs import UBSResultsRunsMixin


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSResultsLogicMixin(
    UBSResultsFormatMixin,
    UBSResultsRunsMixin,
    UBSResultsHistoryMixin,
    UBSResultsCompareMixin,
    UBSResultsRetryMixin,
    UBSResultsExportMixin,
):
    def _on_ubs_history_run_click(self, event) -> None:
        if not hasattr(self, "ubs_history_runs_tree"):
            return
        item, column = self._tree_item_from_event(self.ubs_history_runs_tree, event)
        if not item or column != "#1":
            return
        if item in self.ubs_history_run_checked:
            self.ubs_history_run_checked.remove(item)
        else:
            self.ubs_history_run_checked.add(item)
        values = list(self.ubs_history_runs_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(item in self.ubs_history_run_checked)
            self.ubs_history_runs_tree.item(item, values=values)
        return "break"

    def _on_ubs_history_candidate_click(self, event) -> None:
        if not hasattr(self, "ubs_history_candidates_tree"):
            return
        item, column = self._tree_item_from_event(self.ubs_history_candidates_tree, event)
        if not item or column != "#1":
            return
        info = self.ubs_history_candidate_paths.get(item, {})
        cid = info.get("id", item)
        if cid in self.ubs_history_candidate_checked:
            self.ubs_history_candidate_checked.remove(cid)
        else:
            self.ubs_history_candidate_checked.add(cid)
        values = list(self.ubs_history_candidates_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(cid in self.ubs_history_candidate_checked)
            self.ubs_history_candidates_tree.item(item, values=values)
        return "break"

    def _on_ubs_compare_click(self, event) -> None:
        if not hasattr(self, "ubs_compare_sets_tree"):
            return
        item, column = self._tree_item_from_event(self.ubs_compare_sets_tree, event)
        if not item or column != "#1":
            return
        info = self.ubs_compare_paths.get(item, {})
        cid = info.get("id", item)
        if cid in self.ubs_compare_checked:
            self.ubs_compare_checked.remove(cid)
        else:
            self.ubs_compare_checked.add(cid)
        values = list(self.ubs_compare_sets_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(cid in self.ubs_compare_checked)
            self.ubs_compare_sets_tree.item(item, values=values)
        return "break"

    def _on_ubs_result_tree_click(self, event) -> None:
        if not hasattr(self, "ubs_results_tree"):
            return
        item, column = self._tree_item_from_event(self.ubs_results_tree, event)
        if not item or column != "#1":
            return
        info = self.ubs_result_paths.get(item, {})
        candidate_id = info.get("id", "")
        if not candidate_id:
            return
        if candidate_id in self.ubs_result_checked:
            self.ubs_result_checked.remove(candidate_id)
        else:
            self.ubs_result_checked.add(candidate_id)
        values = list(self.ubs_results_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(candidate_id in self.ubs_result_checked)
            self.ubs_results_tree.item(item, values=values)
        return "break"

    def _checked_ubs_result_infos(self, *, fallback_selected: bool = True) -> list[dict[str, str]]:
        checked = [
            info for item, info in self.ubs_result_paths.items()
            if info.get("id") in self.ubs_result_checked
        ]
        if checked:
            return checked
        if not fallback_selected:
            return []
        return [self._selected_ubs_result_info()] if self._selected_ubs_result_info() else []

    def _refresh_ubs_results_panel(self) -> None:
        for label, callback in (
            ("ubs_results", self._refresh_ubs_results),
            ("ubs_robustness", self._refresh_ubs_robustness),
            ("ubs_final_tick", self._refresh_ubs_final_tick),
            ("ubs_final_tick_6m", self._refresh_ubs_final_tick_6m),
            ("ubs_regression", self._refresh_ubs_regression),
            ("ubs_universe", self._refresh_ubs_universe),
            ("ubs_history", self._refresh_ubs_history),
            ("ubs_comparison", self._refresh_ubs_comparison),
            ("ubs_continue", self._refresh_ubs_continue_state),
        ):
            self._safe_refresh(label, callback)

    def _manual_mark_selected_ubs_results(self, status: str) -> None:
        infos = self._checked_ubs_result_infos()
        ids = [info.get("id", "") for info in infos]
        if not ids:
            messagebox.showinfo("Estado manual", "Selecciona uno o mas resultados primero.")
            return
        label = "aceptado" if status == "accepted" else "rechazado"
        if not messagebox.askyesno(
            "Estado manual",
            f"Marcar {len(ids)} resultado(s) como {label} manual?\n\n"
            "Si la fila no tiene score, se desbloquea el flujo siguiente pero no aporta al peso.",
        ):
            return
        try:
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            updated = mark_candidates(conn, ids, status)
            copied = sync_manual_accepted_candidate_copies(conn, ids) if status == "accepted" else 0
            conn.commit()
            conn.close()
        except (OSError, sqlite3.Error) as exc:
            self._show_error("No se pudo aplicar estado manual", str(exc))
            return
        self.ubs_result_checked.clear()
        self.ubs_weights_locked.set(False)
        copy_text = f" | copias accepted={copied}" if status == "accepted" else ""
        self.status_text.set(f"Estado manual aplicado a {updated} resultado(s){copy_text}")
        self._refresh_ubs_results_panel()

    def _manual_accept_selected_ubs_results(self) -> None:
        self._manual_mark_selected_ubs_results("accepted")

    def _manual_reject_selected_ubs_results(self) -> None:
        self._manual_mark_selected_ubs_results("rejected")

    def _refresh_ubs_history_panel(self) -> None:
        for label, callback in (
            ("ubs_history", self._refresh_ubs_history),
            ("ubs_continue", self._refresh_ubs_continue_state),
        ):
            self._safe_refresh(label, callback)

    def _refresh_ubs_comparison_panel(self) -> None:
        for label, callback in (
            ("ubs_comparison", self._refresh_ubs_comparison),
            ("ubs_continue", self._refresh_ubs_continue_state),
        ):
            self._safe_refresh(label, callback)

    def _ubs_memory_path(self) -> Path:
        return account_memory_path(BASE_DIR, self._ubs_account_type(), self._ubs_broker())

    def _ubs_broker(self) -> str:
        variable = getattr(self, "ubs_broker", None)
        return normalize_broker(variable.get() if variable is not None else DEFAULT_BROKER)

    def _ubs_account_type(self) -> str:
        variable = getattr(self, "ubs_account_type", None)
        return normalize_account_type(variable.get() if variable is not None else "", self._ubs_broker())

    def _refresh_ubs_results(self) -> None:
        if hasattr(self, "ubs_results_tree"):
            for item in self.ubs_results_tree.get_children():
                self.ubs_results_tree.delete(item)
        self.ubs_result_paths.clear()
        self.ubs_result_checked.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_results_summary.set("Sin resultados UBS")
            self.ubs_results_status.set(f"No existe memoria: {memory_path}")
            self._set_ubs_results_execute_backtests_enabled(False)
            self._set_ubs_results_complete_run_enabled(False)
            self._set_ubs_results_continue_run_enabled(False)
            return

        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run_options = self._ubs_results_run_options(conn)
            selected_run_id = self._selected_ubs_results_run_id(run_options)
            self._update_ubs_results_run_combo(run_options, selected_run_id)
            if selected_run_id <= 0:
                total_runs = conn.execute("select count(*) as total from runs").fetchone()["total"]
                self.ubs_results_summary.set("Sin resultados visibles")
                if total_runs:
                    self.ubs_results_status.set("Los resultados anteriores estan archivados; el agente conserva la memoria.")
                else:
                    self.ubs_results_status.set(f"Memoria: {memory_path}")
                conn.close()
                self._set_ubs_results_execute_backtests_enabled(False)
                self._set_ubs_results_complete_run_enabled(False)
                self._set_ubs_results_continue_run_enabled(False)
                return
            latest_run = conn.execute(
                "select * from runs where id=?", (selected_run_id,)
            ).fetchone()
            if latest_run is None:
                total_runs = conn.execute("select count(*) as total from runs").fetchone()["total"]
                self.ubs_results_summary.set("Sin resultados visibles")
                if total_runs:
                    self.ubs_results_status.set("Los resultados anteriores estan archivados; el agente conserva la memoria.")
                else:
                    self.ubs_results_status.set(f"Memoria: {memory_path}")
                conn.close()
                self._set_ubs_results_execute_backtests_enabled(False)
                self._set_ubs_results_complete_run_enabled(False)
                self._set_ubs_results_continue_run_enabled(False)
                return

            counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when status in ('accepted', 'rejected') and score is not null then 1 else 0 end) as scored,
                    sum(case when status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when status = 'rejected' then 1 else 0 end) as rejected,
                    sum(case when status = 'generated' then 1 else 0 end) as generated,
                    sum(case when status = 'no_report' then 1 else 0 end) as no_report,
                    sum(case when status = 'no_trades' then 1 else 0 end) as no_trades,
                    sum(case when status = 'no_history' then 1 else 0 end) as no_history,
                    sum(case when status = 'trade_disabled' then 1 else 0 end) as trade_disabled,
                    sum(case when status = 'symbol_not_exist' then 1 else 0 end) as symbol_not_exist,
                    sum(case when status = 'report_mismatch' then 1 else 0 end) as report_mismatch
                from candidates
                where run_id = ?
                """,
                (latest_run["id"],),
            ).fetchone()
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ?
                order by
                    case
                        when status = 'accepted' then 0
                        when status = 'rejected' then 1
                        else 2
                    end,
                    score desc,
                    id desc
                """,
                (latest_run["id"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_results_summary.set("No se pudieron leer resultados UBS")
            self.ubs_results_status.set(str(exc))
            self._set_ubs_results_execute_backtests_enabled(False)
            self._set_ubs_results_complete_run_enabled(False)
            self._set_ubs_results_continue_run_enabled(False)
            return

        total = int(counts["total"] or 0)
        scored = int(counts["scored"] or 0)
        accepted = int(counts["accepted"] or 0)
        rejected = int(counts["rejected"] or 0)
        generated = int(counts["generated"] or 0)
        no_report = int(counts["no_report"] or 0)
        no_trades = int(counts["no_trades"] or 0)
        no_history = int(counts["no_history"] or 0)
        trade_disabled = int(counts["trade_disabled"] or 0)
        symbol_not_exist = int(counts["symbol_not_exist"] or 0)
        report_mismatch = int(counts["report_mismatch"] or 0)
        self.ubs_results_summary.set(
            f"Run #{latest_run['id']} | {latest_run['created_at']} | "
            f"candidatos {total} | puntuados validos {scored} | aceptados {accepted} | rechazados {rejected}"
        )
        extra = []
        if generated:
            extra.append(f"generados sin backtest {generated}")
        if no_report:
            extra.append(f"sin reporte {no_report}")
        if no_trades:
            extra.append(f"sin operaciones {no_trades}")
        if no_history:
            extra.append(f"sin historico {no_history}")
        if trade_disabled:
            extra.append(f"trading bloqueado {trade_disabled}")
        if symbol_not_exist:
            extra.append(f"simbolo inexistente {symbol_not_exist}")
        if report_mismatch:
            extra.append(f"mismatch reporte {report_mismatch}")
        extra_text = f" | {', '.join(extra)}" if extra else ""
        backtests = "si" if latest_run["execute_backtests"] else "no"
        shown = len(rows)
        self.ubs_results_status.set(
            f"Output: {latest_run['output_dir']} | Backtests: {backtests} | mostrando {shown}/{total}{extra_text}"
        )
        self._set_ubs_results_execute_backtests_enabled(generated > 0)
        continuation_info = self._ubs_continuation_info()
        complete_enabled = (
            bool(continuation_info.get("available"))
            and int(continuation_info.get("run_id") or 0) == int(latest_run["id"])
        )
        self._set_ubs_results_complete_run_enabled(complete_enabled)
        self._set_ubs_results_continue_run_enabled((report_mismatch + no_report) > 0)

        if not hasattr(self, "ubs_results_tree"):
            return
        valid_ids = set()
        for index, row in enumerate(rows):
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            candidate_id = str(row["id"] or "")
            valid_ids.add(candidate_id)
            reason = self._ubs_result_reason(row, status)
            item = self.ubs_results_tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(candidate_id in self.ubs_result_checked),
                    row["run_id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("normalized_net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_int(metrics.get("trades")),
                    reason,
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            self.ubs_result_paths[item] = {
                "id": candidate_id,
                "run": str(row["run_id"] or ""),
                "generation": str(row["generation"] or ""),
                "status": status,
                "symbol": str(row["target_symbol"] or row["symbol"] or ""),
                "period": str(row["period"] or ""),
                "set": str(row["set_path"] or ""),
                "report": str(row["report_path"] or ""),
            }
        self.ubs_result_checked.intersection_update(valid_ids)

    def _hide_latest_ubs_results(self) -> None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Agente UBS", "No hay memoria UBS para limpiar.")
            return
        if not messagebox.askyesno(
            "Limpiar vista",
            "Esto ocultara el ultimo run de la tabla, pero conservara la memoria para el agente.\n\nContinuar?",
        ):
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            latest_run = conn.execute("select id from runs where hidden=0 order by id desc limit 1").fetchone()
            if latest_run is None:
                conn.close()
                messagebox.showinfo("Agente UBS", "No hay resultados visibles para limpiar.")
                return
            conn.execute("update runs set hidden=1 where id=?", (latest_run["id"],))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("No se pudo limpiar la vista UBS", str(exc))
            return
        self.status_text.set("Resultados UBS archivados en memoria")
        self._refresh_ubs_results()
        self._refresh_ubs_robustness()
        self._refresh_ubs_history()
        self._refresh_ubs_comparison()
