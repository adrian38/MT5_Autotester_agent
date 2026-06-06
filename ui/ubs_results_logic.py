from __future__ import annotations

import html
import json
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSResultsLogicMixin:
    def _ubs_result_reason(self, row: object, status: str) -> str:
        if status == "report_mismatch":
            return "mismatch symbol/TF"
        if status == "parse_error":
            return "error al parsear reporte"
        if status == "no_report":
            return "sin reporte"
        if status == "no_trades":
            return "reporte sin operaciones"
        if status in ("generated",):
            return "sin backtest"
        metrics_json = None
        try:
            metrics_json = row["metrics_json"]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            pass
        if not metrics_json:
            return ""
        try:
            data = json.loads(metrics_json)
            reasons = data.get("reasons") or []
            if not reasons:
                return ""
            formats = {
                "net_profit": ("net profit", ".0f", ""),
                "profit_factor": ("PF", ".2f", ""),
                "trades": ("trades", "d", ""),
                "drawdown_pct": ("DD", ".1f", "%"),
                "recovery_factor": ("RF", ".2f", ""),
                "positive_month_ratio": ("meses+", ".0%", ""),
            }
            parts = []
            for reason in reasons:
                label, fmt, suffix = formats.get(reason, (reason, "", ""))
                value = data.get(reason)
                if value is None:
                    parts.append(label)
                    continue
                try:
                    parts.append(f"{label}: {value:{fmt}}{suffix}")
                except (TypeError, ValueError):
                    parts.append(f"{label}: {value}")
            return " | ".join(parts)
        except Exception:
            return ""

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
            ("ubs_history", self._refresh_ubs_history),
            ("ubs_comparison", self._refresh_ubs_comparison),
            ("ubs_continue", self._refresh_ubs_continue_state),
        ):
            self._safe_refresh(label, callback)

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
        return BASE_DIR / "outputs" / "ubs_memory.sqlite"

    def _ensure_ubs_memory_schema(self, conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("pragma table_info(runs)")}
        if "hidden" not in columns:
            conn.execute("alter table runs add column hidden integer not null default 0")
        conn.execute(
            """
            create table if not exists candidate_robustness (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                from_date text not null default '',
                to_date text not null default '',
                positive_bonus real not null default 70.0,
                negative_bonus real not null default -70.0,
                evaluated_at text not null
            )
            """
        )
        conn.commit()

    def _ubs_continuation_info(self) -> dict[str, object]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return {"available": False, "message": "Continuar: sin memoria UBS"}
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs order by id desc limit 1").fetchone()
            if run is None:
                conn.close()
                return {"available": False, "message": "Continuar: no hay runs guardados"}
            generation_row = conn.execute(
                "select max(generation) as generation from candidates where run_id=?",
                (run["id"],),
            ).fetchone()
            latest_generation = int(generation_row["generation"] or 0)
            pending_row = conn.execute(
                """
                select min(generation) as generation
                from candidates
                where run_id=? and status='generated'
                """,
                (run["id"],),
            ).fetchone()
            pending_generation = int(pending_row["generation"] or 0)
            if pending_generation > 0:
                pending_count = int(conn.execute(
                    """
                    select count(*) as total
                    from candidates
                    where run_id=? and generation=? and status='generated'
                    """,
                    (run["id"], pending_generation),
                ).fetchone()["total"] or 0)
            else:
                pending_count = 0
            rows = conn.execute(
                "select set_path from candidates where run_id=? and generation=?",
                (run["id"], latest_generation),
            ).fetchall() if latest_generation > 0 else []
            conn.close()
        except sqlite3.Error as exc:
            return {"available": False, "message": f"Continuar: error SQLite ({exc})"}

        planned_generations = int(run["generations"] or 0)
        variants_per_seed = int(run["variants_per_seed"] or 0)
        max_seeds = int(run["max_seeds"] or 0)
        execute_backtests = bool(run["execute_backtests"])
        seed_count = len({str(Path(row["set_path"])) for row in rows if Path(row["set_path"]).exists()})
        if latest_generation <= 0 or seed_count <= 0:
            return {"available": False, "message": f"Continuar: run #{run['id']} sin seeds disponibles"}

        if execute_backtests and pending_generation > 0 and pending_count > 0:
            remaining_after_pending = max(0, planned_generations - pending_generation)
            return {
                "available": True,
                "message": (
                    f"Continuar: gen {pending_generation} generada sin backtest "
                    f"({pending_count} pendientes); luego faltan {remaining_after_pending} gen"
                ),
                "run_id": int(run["id"]),
                "latest_generation": latest_generation,
                "pending_generation": pending_generation,
                "pending_count": pending_count,
                "planned_generations": planned_generations,
                "remaining": remaining_after_pending,
                "seed_count": pending_count,
                "variants_per_seed": variants_per_seed,
                "max_seeds": max_seeds,
                "execute_backtests": execute_backtests,
            }

        remaining = max(0, planned_generations - latest_generation)
        if remaining <= 0:
            return {
                "available": False,
                "message": f"Continuar: deshabilitado, run #{run['id']} completo ({latest_generation}/{planned_generations})",
                "run_id": int(run["id"]),
                "latest_generation": latest_generation,
                "planned_generations": planned_generations,
                "remaining": 0,
                "seed_count": seed_count,
                "variants_per_seed": variants_per_seed,
                "max_seeds": max_seeds,
                "execute_backtests": execute_backtests,
            }
        return {
            "available": True,
            "message": f"Continuar: run #{run['id']} pendiente ({latest_generation}/{planned_generations}), faltan {remaining} gen",
            "run_id": int(run["id"]),
            "latest_generation": latest_generation,
            "pending_generation": 0,
            "pending_count": 0,
            "planned_generations": planned_generations,
            "remaining": remaining,
            "seed_count": seed_count,
            "variants_per_seed": variants_per_seed,
            "max_seeds": max_seeds,
            "execute_backtests": execute_backtests,
        }

    def _refresh_ubs_continue_state(self) -> None:
        info = self._ubs_continuation_info()
        available = bool(info.get("available"))
        self.ubs_continue_status.set(str(info.get("message") or "Continuar: no disponible"))
        if self.ubs_continue_button is not None:
            self.ubs_continue_button.set_disabled(not available)

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
            return

        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            latest_run = conn.execute(
                "select * from runs where hidden=0 order by id desc limit 1"
            ).fetchone()
            if latest_run is None:
                total_runs = conn.execute("select count(*) as total from runs").fetchone()["total"]
                self.ubs_results_summary.set("Sin resultados visibles")
                if total_runs:
                    self.ubs_results_status.set("Los resultados anteriores estan archivados; el agente conserva la memoria.")
                else:
                    self.ubs_results_status.set(f"Memoria: {memory_path}")
                conn.close()
                return

            counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when score is not null then 1 else 0 end) as scored,
                    sum(case when status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when status = 'rejected' then 1 else 0 end) as rejected,
                    sum(case when status = 'generated' then 1 else 0 end) as generated,
                    sum(case when status = 'no_report' then 1 else 0 end) as no_report,
                    sum(case when status = 'no_trades' then 1 else 0 end) as no_trades,
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
                        when score is not null then 1
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
            return

        total = int(counts["total"] or 0)
        scored = int(counts["scored"] or 0)
        accepted = int(counts["accepted"] or 0)
        rejected = int(counts["rejected"] or 0)
        generated = int(counts["generated"] or 0)
        no_report = int(counts["no_report"] or 0)
        no_trades = int(counts["no_trades"] or 0)
        report_mismatch = int(counts["report_mismatch"] or 0)
        self.ubs_results_summary.set(
            f"Run #{latest_run['id']} | {latest_run['created_at']} | "
            f"candidatos {total} | puntuados {scored} | aceptados {accepted} | rechazados {rejected}"
        )
        extra = []
        if generated:
            extra.append(f"generados sin backtest {generated}")
        if no_report:
            extra.append(f"sin reporte {no_report}")
        if no_trades:
            extra.append(f"sin operaciones {no_trades}")
        if report_mismatch:
            extra.append(f"mismatch reporte {report_mismatch}")
        extra_text = f" | {', '.join(extra)}" if extra else ""
        backtests = "si" if latest_run["execute_backtests"] else "no"
        shown = len(rows)
        self.ubs_results_status.set(
            f"Output: {latest_run['output_dir']} | Backtests: {backtests} | mostrando {shown}/{total}{extra_text}"
        )

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

    def _refresh_ubs_history(self) -> None:
        if hasattr(self, "ubs_history_runs_tree"):
            for item in self.ubs_history_runs_tree.get_children():
                self.ubs_history_runs_tree.delete(item)
        if hasattr(self, "ubs_history_candidates_tree"):
            for item in self.ubs_history_candidates_tree.get_children():
                self.ubs_history_candidates_tree.delete(item)
        self.ubs_history_candidate_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_history_summary.set("Sin memoria SQLite UBS")
            self.ubs_history_candidate_summary.set(f"No existe: {memory_path}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            rows = conn.execute(
                """
                select
                    r.id, r.created_at, r.generations, r.variants_per_seed, r.max_seeds,
                    r.execute_backtests, r.hidden, r.output_dir,
                    count(c.id) as total,
                    sum(case when c.status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when c.status = 'rejected' then 1 else 0 end) as rejected
                from runs r
                left join candidates c on c.run_id = r.id
                group by r.id
                order by r.id desc
                """
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_history_summary.set("No se pudo leer historico UBS")
            self.ubs_history_candidate_summary.set(str(exc))
            return

        self.ubs_history_summary.set(f"Runs en SQLite: {len(rows)} | Memoria: {memory_path}")
        if not hasattr(self, "ubs_history_runs_tree"):
            return
        for row in rows:
            run_iid = str(row["id"])
            self.ubs_history_runs_tree.insert(
                "",
                "end",
                iid=run_iid,
                values=(
                    self._checkbox_text(run_iid in self.ubs_history_run_checked),
                    row["id"],
                    row["created_at"],
                    row["generations"],
                    row["variants_per_seed"],
                    row["max_seeds"],
                    "si" if row["execute_backtests"] else "no",
                    "si" if row["hidden"] else "no",
                    int(row["total"] or 0),
                    int(row["accepted"] or 0),
                    int(row["rejected"] or 0),
                    row["output_dir"],
                ),
            )
        if rows:
            self.ubs_history_runs_tree.selection_set(str(rows[0]["id"]))
            self._refresh_ubs_history_candidates()
        else:
            self.ubs_history_candidate_summary.set("Sin runs registrados")

    def _selected_ubs_history_run_id(self) -> int | None:
        if not hasattr(self, "ubs_history_runs_tree"):
            return None
        selected = self.ubs_history_runs_tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def _refresh_ubs_history_candidates(self) -> None:
        if hasattr(self, "ubs_history_candidates_tree"):
            for item in self.ubs_history_candidates_tree.get_children():
                self.ubs_history_candidates_tree.delete(item)
        self.ubs_history_candidate_paths.clear()
        run_id = self._selected_ubs_history_run_id()
        if run_id is None:
            self.ubs_history_candidate_summary.set("Selecciona un run")
            return
        memory_path = self._ubs_memory_path()
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            rows = conn.execute(
                """
                select
                    c.*,
                    cr.status as robust_status,
                    cr.score as robust_score,
                    cr.positive_bonus as robust_positive_bonus,
                    cr.negative_bonus as robust_negative_bonus
                from candidates c
                left join candidate_robustness cr on cr.candidate_id = c.id
                where c.run_id=?
                order by c.generation desc,
                    case
                        when c.status = 'accepted' then 0
                        when c.score is not null then 1
                        else 2
                    end,
                    c.score desc,
                    c.id desc
                limit 1000
                """,
                (run_id,),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_history_candidate_summary.set(str(exc))
            return

        total = len(rows)
        accepted = sum(1 for row in rows if row["status"] == "accepted")
        rejected = sum(1 for row in rows if row["status"] == "rejected")
        robust_ok = sum(1 for row in rows if row["robust_status"] == "accepted")
        robust_fail = sum(1 for row in rows if row["robust_status"] == "rejected")
        self.ubs_history_candidate_summary.set(
            f"Run #{run_id}: {total} candidatos | aceptados {accepted} | rechazados {rejected} | robust OK {robust_ok} FAIL {robust_fail}"
        )
        if not hasattr(self, "ubs_history_candidates_tree"):
            return
        for row in rows:
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            robust_status = str(row["robust_status"] or "")
            robust_label = (
                self._format_ubs_robust_status(
                    robust_status,
                    row["robust_positive_bonus"],
                    row["robust_negative_bonus"],
                )
                if robust_status or status == "accepted"
                else "-"
            )
            cid = str(row["id"] or "")
            item = self.ubs_history_candidates_tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(cid in self.ubs_history_candidate_checked),
                    row["id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    robust_label,
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_int(metrics.get("trades")),
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_history_candidate_paths[item] = {
                "id": cid,
                "set": str(row["set_path"] or ""),
                "seed": str(row["seed_path"] or ""),
                "report": str(row["report_path"] or ""),
            }

    def _refresh_ubs_comparison(self) -> None:
        if hasattr(self, "ubs_compare_sets_tree"):
            for item in self.ubs_compare_sets_tree.get_children():
                self.ubs_compare_sets_tree.delete(item)
        if hasattr(self, "ubs_compare_diff_tree"):
            for item in self.ubs_compare_diff_tree.get_children():
                self.ubs_compare_diff_tree.delete(item)
        self.ubs_compare_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_compare_summary.set("Sin memoria SQLite UBS")
            self.ubs_compare_detail.set(f"No existe: {memory_path}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            run_options = self._ubs_compare_run_options(conn)
            selected_run_id = self._selected_ubs_compare_run_id(run_options)
            if selected_run_id <= 0:
                conn.close()
                self.ubs_compare_summary.set("Sin run visible")
                self.ubs_compare_detail.set("No hay runs UBS visibles en memoria.")
                return
            self._update_ubs_compare_run_combo(run_options, selected_run_id)
            counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when status = 'accepted' then 1 else 0 end) as accepted,
                    sum(case when status = 'rejected' then 1 else 0 end) as rejected
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                """,
                (selected_run_id,),
            ).fetchone()
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                order by
                    case when status = 'accepted' then 0 else 1 end,
                    score desc,
                    id desc
                """,
                (selected_run_id,),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_compare_summary.set("No se pudo leer comparacion UBS")
            self.ubs_compare_detail.set(str(exc))
            return

        total = int(counts["total"] or 0) if counts else len(rows)
        accepted = int(counts["accepted"] or 0) if counts else sum(1 for row in rows if row["status"] == "accepted")
        rejected = int(counts["rejected"] or 0) if counts else sum(1 for row in rows if row["status"] == "rejected")
        self.ubs_compare_summary.set(
            f"Run #{selected_run_id}: resultados {total} | aceptados {accepted} | rechazados {rejected} | cargados {len(rows)}"
        )
        if not hasattr(self, "ubs_compare_sets_tree"):
            return
        for row in rows:
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            cid = str(row["id"] or "")
            item = self.ubs_compare_sets_tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(cid in self.ubs_compare_checked),
                    row["run_id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_compare_paths[item] = {
                "id": cid,
                "candidate_id": cid,
                "set": str(row["set_path"] or ""),
                "seed": str(row["seed_path"] or ""),
                "mutated": str(row["mutated_keys"] or ""),
            }
        if rows:
            first = self.ubs_compare_sets_tree.get_children()[0]
            self.ubs_compare_sets_tree.selection_set(first)
            self._refresh_ubs_comparison_diff()
        else:
            self.ubs_compare_detail.set("No hay resultados puntuados para el run visible.")

    def _ubs_compare_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                count(c.id) as total,
                sum(case when c.status = 'accepted' then 1 else 0 end) as accepted,
                sum(case when c.status = 'rejected' then 1 else 0 end) as rejected
            from runs r
            left join candidates c
                on c.run_id = r.id and c.status in ('accepted', 'rejected')
            where coalesce(r.hidden, 0) = 0
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            accepted = int(row["accepted"] or 0)
            rejected = int(row["rejected"] or 0)
            options.append((run_id, f"#{run_id} | {created} | {total} ({accepted}/{rejected})"))
        return options

    def _selected_ubs_compare_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_compare_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_compare_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_compare_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _label in options):
                return run_id
        return newest_run_id

    def _update_ubs_compare_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_compare_run_combo"):
            return
        labels = [label for _run_id, label in options]
        self.ubs_compare_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_compare_run_id.get() != selected_label:
            self.ubs_compare_run_id.set(selected_label)

    def _refresh_ubs_comparison_diff(self) -> None:
        if hasattr(self, "ubs_compare_diff_tree"):
            for item in self.ubs_compare_diff_tree.get_children():
                self.ubs_compare_diff_tree.delete(item)
        paths = self._selected_ubs_compare_paths()
        if not paths:
            self.ubs_compare_detail.set("Selecciona un resultado para comparar contra su seed.")
            return
        seed_path = Path(paths.get("seed", "")).expanduser()
        set_path = Path(paths.get("set", "")).expanduser()
        if not seed_path.exists() or not set_path.exists():
            self.ubs_compare_detail.set("No existe el seed o el set aceptado en disco.")
            return
        seed_values = self._read_set_values_for_compare(seed_path)
        set_values = self._read_set_values_for_compare(set_path)
        changed = []
        for key in sorted(set(seed_values) | set(set_values)):
            seed_value = seed_values.get(key, "(faltante)")
            set_value = set_values.get(key, "(faltante)")
            if seed_value != set_value:
                changed.append((key, seed_value, set_value))
        mutated = [key for key in paths.get("mutated", "").split(";") if key]
        mutated_hint = f" | mutados por agente: {', '.join(mutated[:8])}" if mutated else ""
        self.ubs_compare_detail.set(
            f"{len(changed)} diferencias | Seed: {self._short_filename(seed_path.name)} | "
            f"Resultado: {self._short_filename(set_path.name)}{mutated_hint}"
        )
        if not hasattr(self, "ubs_compare_diff_tree"):
            return
        for key, seed_value, set_value in changed:
            self.ubs_compare_diff_tree.insert("", "end", values=(key, seed_value, set_value))

    def _ubs_compare_rows_for_report(self) -> tuple[int, list[sqlite3.Row]]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0, []
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            run_options = self._ubs_compare_run_options(conn)
            run_id = self._selected_ubs_compare_run_id(run_options)
            if run_id <= 0:
                return 0, []
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                order by
                    case when status = 'accepted' then 0 else 1 end,
                    score desc,
                    id desc
                """,
                (run_id,),
            ).fetchall()
            return run_id, rows
        finally:
            conn.close()

    def _set_diff_rows(self, seed_path: Path, set_path: Path) -> list[tuple[str, str, str]]:
        seed_values = self._read_set_values_for_compare(seed_path)
        set_values = self._read_set_values_for_compare(set_path)
        changed: list[tuple[str, str, str]] = []
        for key in sorted(set(seed_values) | set(set_values)):
            seed_value = seed_values.get(key, "(faltante)")
            set_value = set_values.get(key, "(faltante)")
            if seed_value != set_value:
                changed.append((key, seed_value, set_value))
        return changed

    def _generate_ubs_compare_report(self) -> None:
        try:
            run_id, rows = self._ubs_compare_rows_for_report()
        except sqlite3.Error as exc:
            self._show_error("No se pudo generar reporte UBS", str(exc))
            return
        if not rows:
            messagebox.showinfo("Reporte UBS", "No hay resultados puntuados para reportar.")
            return

        output_dir = BASE_DIR / "outputs" / "ubs_compare"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"ubs_seed_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

        summary_rows: list[str] = []
        detail_blocks: list[str] = []
        total_changes = 0
        for index, row in enumerate(rows, start=1):
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            seed_path = Path(row["seed_path"])
            set_path = Path(row["set_path"])
            if seed_path.exists() and set_path.exists():
                changes = self._set_diff_rows(seed_path, set_path)
                missing_note = ""
            else:
                changes = []
                missing_note = "Archivo seed o aceptado no encontrado"
            total_changes += len(changes)
            mutated = [key for key in str(row["mutated_keys"] or "").split(";") if key]
            summary_rows.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td>{html.escape(str(row['run_id']))}</td>"
                f"<td>{html.escape(str(row['generation']))}</td>"
                f"<td>{html.escape(self._format_ubs_status(str(row['status'] or '')))}</td>"
                f"<td>{html.escape(str(row['target_symbol'] or row['symbol']))}</td>"
                f"<td>{html.escape(str(row['period']))}</td>"
                f"<td>{html.escape(self._format_ubs_number(row['score']))}</td>"
                f"<td>{html.escape(self._format_ubs_number(metrics.get('net_profit')))}</td>"
                f"<td>{html.escape(self._format_ubs_number(metrics.get('profit_factor')))}</td>"
                f"<td>{html.escape(self._format_ubs_number(metrics.get('drawdown_pct')))}</td>"
                f"<td>{len(changes)}</td>"
                f"<td>{html.escape(set_path.name)}</td>"
                f"<td>{html.escape(seed_path.name)}</td>"
                "</tr>"
            )
            diff_rows = "\n".join(
                "<tr>"
                f"<td>{html.escape(key)}</td>"
                f"<td>{html.escape(seed_value)}</td>"
                f"<td>{html.escape(set_value)}</td>"
                "</tr>"
                for key, seed_value, set_value in changes
            )
            if not diff_rows:
                diff_rows = f"<tr><td colspan='3'>{html.escape(missing_note or 'Sin diferencias')}</td></tr>"
            detail_blocks.append(
                "<details>"
                f"<summary>#{index} {html.escape(self._format_ubs_status(str(row['status'] or '')))} | "
                f"{html.escape(str(row['target_symbol'] or row['symbol']))} "
                f"{html.escape(str(row['period']))} | score {html.escape(self._format_ubs_number(row['score']))} "
                f"| cambios {len(changes)} | {html.escape(set_path.name)}</summary>"
                f"<p><b>Seed:</b> {html.escape(str(seed_path))}<br>"
                f"<b>Set:</b> {html.escape(str(set_path))}<br>"
                f"<b>Mutados por agente:</b> {html.escape(', '.join(mutated) if mutated else '-')}</p>"
                "<table><thead><tr><th>Parametro</th><th>Seed</th><th>Set</th></tr></thead>"
                f"<tbody>{diff_rows}</tbody></table>"
                "</details>"
            )

        accepted = sum(1 for row in rows if row["status"] == "accepted")
        rejected = sum(1 for row in rows if row["status"] == "rejected")

        html_text = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>UBS Seed Compare</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:24px;}"
            "h1{margin:0 0 8px;font-size:24px;} h2{margin-top:28px;}"
            ".meta{color:#a8b3c7;margin-bottom:18px;}"
            "table{border-collapse:collapse;width:100%;margin:12px 0;background:#111827;}"
            "th,td{border:1px solid #334155;padding:6px 8px;font-size:12px;vertical-align:top;}"
            "th{background:#243247;color:#dbeafe;} tr:nth-child(even){background:#172033;}"
            "details{border:1px solid #334155;border-radius:6px;padding:10px;margin:10px 0;background:#111827;}"
            "summary{cursor:pointer;font-weight:600;color:#86efac;} p{color:#cbd5e1;font-size:13px;}"
            "</style></head><body>"
            "<h1>UBS comparacion resultados contra seed</h1>"
            f"<div class='meta'>Generado: {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} | "
            f"run #{run_id} | resultados: {len(rows)} | aceptados: {accepted} | rechazados: {rejected} | "
            f"cambios totales: {total_changes}</div>"
            "<h2>Resumen</h2>"
            "<table><thead><tr>"
            "<th>#</th><th>Run</th><th>Gen</th><th>Estado</th><th>Symbol</th><th>TF</th><th>Score</th>"
            "<th>Net</th><th>PF</th><th>DD %</th><th>Cambios</th><th>Set</th><th>Seed</th>"
            "</tr></thead><tbody>"
            + "\n".join(summary_rows)
            + "</tbody></table><h2>Detalle por set</h2>"
            + "\n".join(detail_blocks)
            + "</body></html>"
        )
        report_path.write_text(html_text, encoding="utf-8")
        self.status_text.set(f"Reporte UBS generado: {report_path.name}")
        self._open_local_file(report_path)

    def _selected_ubs_compare_paths(self) -> dict[str, str] | None:
        if not hasattr(self, "ubs_compare_sets_tree"):
            return None
        selected = self.ubs_compare_sets_tree.selection()
        if not selected:
            return None
        return self.ubs_compare_paths.get(selected[0])

    def _read_set_values_for_compare(self, path: Path) -> dict[str, str]:
        text = ""
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except UnicodeError:
                continue
        if not text:
            text = path.read_text(errors="replace")
        values: dict[str, str] = {}
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith(";"):
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = raw_value.split("||", 1)[0].strip()
        return values

    def _selected_ubs_compare_path(self, kind: str) -> Path | None:
        paths = self._selected_ubs_compare_paths()
        if not paths:
            return None
        raw_path = paths.get(kind, "")
        return Path(raw_path).expanduser() if raw_path else None

    def _open_selected_ubs_compare_seed(self) -> None:
        path = self._selected_ubs_compare_path("seed")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_compare_set(self) -> None:
        path = self._selected_ubs_compare_path("set")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _short_filename(self, value, max_length: int = 72) -> str:
        name = Path(str(value)).name
        if len(name) <= max_length:
            return name
        suffix = Path(name).suffix
        stem = name[: -len(suffix)] if suffix else name
        tail_length = max(12, max_length // 3)
        head_length = max(8, max_length - tail_length - len(suffix) - 3)
        return f"{stem[:head_length]}...{stem[-tail_length:]}{suffix}"

    def _ubs_variant_code(self, set_name: str) -> str:
        matches = re.findall(r"g\d+_s\d+_v\d+", set_name, flags=re.IGNORECASE)
        return matches[-1] if matches else ""

    def _format_ubs_set_label(self, row: sqlite3.Row) -> str:
        set_path = Path(str(row["set_path"] or ""))
        name = set_path.name
        candidate_id = str(row["id"] or "").strip()
        symbol = str(row["target_symbol"] or row["symbol"] or "").strip()
        period = str(row["period"] or "").strip()
        variant_code = self._ubs_variant_code(name)
        prefix = f"#{candidate_id} " if candidate_id else ""
        if symbol and period and variant_code:
            return f"{prefix}{symbol}_{period}_{variant_code}{set_path.suffix or '.set'}"
        if variant_code:
            return f"{prefix}{variant_code}{set_path.suffix or '.set'}"
        return f"{prefix}{self._short_filename(name)}"

    def _parse_ubs_metrics(self, raw) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _format_ubs_number(self, value, decimals: int = 2) -> str:
        if value in (None, ""):
            return ""
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)

    def _format_ubs_int(self, value) -> str:
        if value in (None, ""):
            return ""
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)

    def _format_ubs_status(self, status: str) -> str:
        labels = {
            "accepted": "aceptado",
            "rejected": "rechazado",
            "generated": "generado",
            "no_report": "sin reporte",
            "no_trades": "sin operaciones",
            "disabled_symbol": "deshabilitado",
            "parse_error": "parse error",
            "report_mismatch": "mismatch reporte",
            "invalid_seed": "sin Symbol/TF",
            "pending": "pendiente",
            "sin_evaluar": "sin evaluar",
        }
        return labels.get(status, status or "-")

    def _format_ubs_robust_status(self, status: str, positive_bonus, negative_bonus) -> str:
        if not status:
            return "pendiente"
        labels = {
            "accepted": "OK",
            "rejected": "FAIL",
            "no_trades": "sin ops",
            "no_report": "sin reporte",
            "parse_error": "parse error",
            "report_mismatch": "mismatch",
        }
        label = labels.get(status, status)
        bonus = None
        if status == "accepted":
            bonus = positive_bonus
        elif status == "rejected":
            bonus = negative_bonus
        if bonus in (None, ""):
            return label
        try:
            bonus_value = float(bonus)
        except (TypeError, ValueError):
            return label
        return f"{label} {bonus_value:+.0f}"

    def _ubs_result_tag(self, status: str) -> str:
        if status == "accepted":
            return "accepted"
        if status in {"rejected", "parse_error", "report_mismatch", "no_trades", "invalid_seed"}:
            return "rejected"
        if status == "disabled_symbol":
            return "pending"
        return "pending"

    def _selected_ubs_result_path(self, kind: str):
        info = self._selected_ubs_result_info()
        if not info:
            return None
        raw_path = info.get(kind, "")
        return Path(raw_path).expanduser() if raw_path else None

    def _selected_ubs_result_info(self) -> dict:
        if not hasattr(self, "ubs_results_tree"):
            return {}
        selected = self.ubs_results_tree.selection()
        if not selected:
            return {}
        return self.ubs_result_paths.get(selected[0], {})

    def _open_ubs_output_dir(self) -> None:
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent")).expanduser()
        if not output_dir.exists():
            messagebox.showinfo("Agente UBS", f"No existe la carpeta:\n{output_dir}")
            return
        subprocess.Popen(["explorer", str(output_dir)])

    def _open_selected_ubs_set(self) -> None:
        path = self._selected_ubs_result_path("set")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_report(self) -> None:
        path = self._selected_ubs_result_path("report")
        if path is None:
            messagebox.showinfo("Agente UBS", "Ese resultado no tiene reporte asociado.")
            return
        self._open_local_file(path)

    def _retry_selected_ubs_mismatch(self) -> None:
        info = self._selected_ubs_result_info()
        if not info:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        if info.get("status") not in {"report_mismatch", "no_report"}:
            messagebox.showinfo(
                "Agente UBS",
                "Esta accion solo aplica a filas con estado mismatch reporte o sin reporte.",
            )
            return
        candidate_id = info.get("id", "").strip()
        set_path = Path(info.get("set", "")).expanduser()
        if not candidate_id:
            messagebox.showinfo("Agente UBS", "La fila seleccionada no tiene candidate id.")
            return
        if not set_path.exists():
            messagebox.showinfo("Agente UBS", f"No existe el set:\n{set_path}")
            return
        try:
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--template", self.template_path.get(),
                "--retry-candidate-id", candidate_id,
                "--delay", str(self.delay.get()),
            ]
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
            args.extend(self._ubs_score_args())
            if not self.multiterminal_enabled.get():
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudo preparar retry de fila", str(exc))
            return

        status_label = "sin reporte" if info.get("status") == "no_report" else "mismatch reporte"
        details = [
            f"Accion: Reprobar fila UBS ({status_label})",
            f"Candidate: #{candidate_id}",
            f"Objetivo: {info.get('symbol', '')} {info.get('period', '')}",
            f"Set: {set_path.name}",
            "Backtests previstos: 1",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar retry fila", 1, details):
            self._run_script("ubs_agent.py", args)

    def _retry_visible_ubs_run_mismatches(self) -> None:
        try:
            run_id = self._visible_ubs_run_id()
            if run_id <= 0:
                messagebox.showinfo("Agente UBS", "No hay run visible para reprobar.")
                return
            problem_count = self._count_ubs_run_retryable_problems(run_id)
            if problem_count <= 0:
                messagebox.showinfo("Agente UBS", f"Run #{run_id} no tiene mismatch/sin reporte pendientes.")
                return
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--template", self.template_path.get(),
                "--retry-run-id", str(run_id),
                "--retry-mismatch-run",
                "--delay", str(self.delay.get()),
            ]
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
            args.extend(self._ubs_score_args())
            if not self.multiterminal_enabled.get():
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudo preparar retry de run", str(exc))
            return

        details = [
            "Accion: Reprobar mismatch/sin reporte de run UBS",
            f"Run: #{run_id}",
            f"Backtests previstos: {problem_count}",
            "Al terminar actualiza esas mismas filas SQLite.",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar retry run", problem_count, details):
            self._run_script("ubs_agent.py", args)

    def _visible_ubs_run_id(self) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        conn = connect_memory(memory_path)
        try:
            row = conn.execute("select id from runs where hidden=0 order by id desc limit 1").fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _count_ubs_run_mismatches(self, run_id: int) -> int:
        return self._count_ubs_run_retryable_problems(run_id)

    def _count_ubs_run_retryable_problems(self, run_id: int) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        conn = connect_memory(memory_path)
        try:
            row = conn.execute(
                """
                select count(*) as total
                from candidates
                where run_id=? and status in ('report_mismatch', 'no_report')
                """,
                (run_id,),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _retry_no_trades_result(self) -> None:
        info = self._selected_ubs_result_info()
        if not info:
            messagebox.showinfo("Repetir sin ops", "Selecciona un resultado primero.")
            return
        if info.get("status") != "no_trades":
            messagebox.showinfo("Repetir sin ops",
                                "Esta acción solo aplica a filas con estado 'sin operaciones'.")
            return
        candidate_id = info.get("id", "").strip()
        set_path = info.get("set", "")
        if not candidate_id:
            messagebox.showinfo("Repetir sin ops", "La fila seleccionada no tiene candidate id.")
            return
        try:
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--template", self.template_path.get(),
                "--retry-candidate-id", candidate_id,
                "--delay", str(self.delay.get()),
            ]
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
            args.extend(self._ubs_score_args())
            if not self.multiterminal_enabled.get():
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudo preparar retry sin ops", str(exc))
            return

        details = [
            "Accion: Repetir candidato sin operaciones",
            f"Candidate: #{candidate_id}",
            f"Objetivo: {info.get('symbol', '')} {info.get('period', '')}",
            f"Set: {Path(set_path).name if set_path else '-'}",
            "Backtests previstos: 1",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar repetir sin ops", 1, details):
            self._run_script("ubs_agent.py", args)

    # ──────────────────────────────────────────────────────────────────────
    # Export
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _report_related_files(rep_str: str) -> list[Path]:
        """Return the .htm/.html report + all associated image files."""
        if not rep_str:
            return []
        rep = Path(rep_str)
        parent = rep.parent if rep.parent.exists() else BASE_DIR / "reports"
        stem = rep.stem
        found = [f for f in parent.glob(f"{stem}*") if f.is_file() and f.suffix.lower() != ".set"]
        if not found:
            for ext in (".htm", ".html"):
                alt = parent / (stem + ext)
                if alt.exists():
                    found = [f for f in parent.glob(f"{alt.stem}*") if f.is_file() and f.suffix.lower() != ".set"]
                    break
        return found

    def _export_ubs_results_run(self) -> None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Exportar run", "No existe memoria UBS.")
            return

        base_dir = filedialog.askdirectory(title="Carpeta destino para la exportación")
        if not base_dir:
            return

        # ── Leer candidatos ──────────────────────────────────────────────
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute(
                "select * from runs where hidden=0 order by id desc limit 1"
            ).fetchone()
            if run is None:
                messagebox.showinfo("Exportar run", "No hay ningún run visible.")
                conn.close()
                return
            rows = conn.execute(
                """
                select id, status, set_path, report_path, metrics_json,
                       target_symbol, period
                from candidates where run_id = ?
                """,
                (run["id"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error al leer memoria UBS", str(exc))
            return

        created    = str(run["created_at"] or "").replace(":", "-").replace(" ", "_")[:16]
        run_folder = Path(base_dir) / f"Run_{run['id']}_{created}"
        accept_dir = run_folder / "aceptados"
        netpos_dir = run_folder / "fallidos" / "net_profit_positivo"
        otros_dir  = run_folder / "fallidos" / "otros"
        for d in (accept_dir, netpos_dir, otros_dir):
            d.mkdir(parents=True, exist_ok=True)

        total  = len(rows)
        counts = {"aceptados": 0, "net_profit_positivo": 0, "otros": 0, "sin_archivo": 0}
        q: queue.Queue = queue.Queue()

        def _folder_name(cid: int, set_str: str, symbol: str, period: str) -> str:
            if set_str and Path(set_str).stem:
                return Path(set_str).stem
            parts = [p for p in (symbol, period) if p and p.upper() != "UNKNOWN"]
            return "_".join(parts + [str(cid)]) if parts else str(cid)

        def _copy_candidate(dest_cat: Path, cid: int, set_str: str,
                            rep_str: str, symbol: str, period: str) -> bool:
            folder = dest_cat / _folder_name(cid, set_str, symbol, period)
            folder.mkdir(parents=True, exist_ok=True)
            copied = False
            if set_str:
                src = Path(set_str)
                if src.exists():
                    shutil.copy2(src, folder / src.name)
                    copied = True
            for f in self._report_related_files(rep_str):
                shutil.copy2(f, folder / f.name)
                copied = True
            return copied

        def _do_export() -> None:
            for idx, row in enumerate(rows):
                status   = str(row["status"] or "")
                cid      = int(row["id"] or 0)
                set_path = str(row["set_path"] or "")
                rep_path = str(row["report_path"] or "")
                symbol   = str(row["target_symbol"] or "")
                period   = str(row["period"] or "")
                label    = Path(set_path).stem if set_path else f"#{cid}"
                q.put(("progress", idx, total, label))

                if status == "accepted":
                    ok = _copy_candidate(accept_dir, cid, set_path, rep_path, symbol, period)
                    counts["aceptados" if ok else "sin_archivo"] += 1

                elif status in ("rejected", "no_trades"):
                    net_profit = 0.0
                    try:
                        data = json.loads(row["metrics_json"] or "{}")
                        net_profit = float(data.get("net_profit") or 0)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                    dest_cat = netpos_dir if net_profit > 0 else otros_dir
                    key = "net_profit_positivo" if net_profit > 0 else "otros"
                    ok = _copy_candidate(dest_cat, cid, set_path, rep_path, symbol, period)
                    counts[key if ok else "sin_archivo"] += 1

                else:
                    ok = _copy_candidate(otros_dir, cid, set_path, "", symbol, period)
                    counts["otros" if ok else "sin_archivo"] += 1

            q.put(("done",))

        # ── Dialogo de progreso ────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title("Exportando...")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        dlg.configure(bg=self.colors["panel"])
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)

        body = tk.Frame(dlg, bg=self.colors["panel"], padx=28, pady=22)
        body.pack()

        tk.Label(body, text="Exportando run UBS",
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(body, text=f"Run #{run['id']}  ·  {run['created_at']}",
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 16))

        bar = ttk.Progressbar(body, mode="determinate", maximum=100,
                              style="Horizontal.TProgressbar", length=440)
        bar.pack(fill="x")

        count_var  = tk.StringVar(value=f"0 / {total}")
        status_var = tk.StringVar(value="Iniciando...")
        tk.Label(body, textvariable=count_var,
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(anchor="e", pady=(4, 0))
        tk.Label(body, textvariable=status_var,
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=440, anchor="w").pack(
            fill="x", pady=(3, 0))

        dlg.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width()  - dlg.winfo_width())  // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dlg.winfo_height()) // 2)
        dlg.geometry(f"+{x}+{y}")

        threading.Thread(target=_do_export, daemon=True).start()

        def _poll() -> None:
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] == "progress":
                        _, idx, tot, label = msg
                        pct = int((idx + 1) / max(tot, 1) * 100)
                        bar["value"] = pct
                        count_var.set(f"{idx + 1} / {tot}")
                        name = label[:55] + "..." if len(label) > 55 else label
                        status_var.set(f"Copiando: {name}")
                    elif msg[0] == "done":
                        bar["value"] = 100
                        status_var.set("Completado.")
                        dlg.after(400, _finish)
                        return
            except queue.Empty:
                pass
            dlg.after(40, _poll)

        def _finish() -> None:
            dlg.grab_release()
            dlg.destroy()
            summary = (
                f"Exportado en:\n{run_folder}\n\n"
                f"  aceptados/                   {counts['aceptados']}\n"
                f"  fallidos/net_profit_positivo  {counts['net_profit_positivo']}\n"
                f"  fallidos/otros               {counts['otros']}"
            )
            if counts["sin_archivo"]:
                summary += f"\n\n  Sin archivos disponibles:    {counts['sin_archivo']}"
            messagebox.showinfo("Exportar run — completado", summary)
            try:
                subprocess.Popen(["explorer", str(run_folder)])
            except Exception:
                pass

        dlg.after(40, _poll)

    # ── History: eliminar run completo ────────────────────────────────────

    def _delete_ubs_history_run(self) -> None:
        if not hasattr(self, "ubs_history_runs_tree"):
            return
        selected = self.ubs_history_runs_tree.selection()
        if not selected:
            messagebox.showinfo("Eliminar run", "Selecciona un run primero.")
            return
        try:
            run_id = int(selected[0])
        except ValueError:
            return

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return

        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs where id=?", (run_id,)).fetchone()
            if run is None:
                conn.close()
                return
            rows = conn.execute(
                "select set_path, report_path from candidates where run_id=?",
                (run_id,),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error al leer run", str(exc))
            return

        total = len(rows)
        created = str(run["created_at"])
        if not messagebox.askyesno(
            "Eliminar run completo",
            f"Run #{run_id}  —  {created}\n\n"
            f"Esto eliminará:\n"
            f"  • {total} candidatos de la DB y el run\n"
            f"  • Sus archivos .set del disco\n"
            f"  • Sus reportes (.htm + imágenes)\n"
            f"  • Scores de evaluación de seeds (pesos → 0)\n\n"
            "¿Continuar?",
        ):
            return

        deleted_files = 0
        for row in rows:
            for f in self._report_related_files(str(row["report_path"] or "")):
                try:
                    f.unlink(missing_ok=True)
                    deleted_files += 1
                except OSError:
                    pass
            sp = Path(str(row["set_path"] or ""))
            if sp.suffix.lower() == ".set" and sp.exists():
                try:
                    sp.unlink()
                    deleted_files += 1
                except OSError:
                    pass

        try:
            conn = connect_memory(memory_path)
            conn.execute("delete from candidates where run_id=?", (run_id,))
            conn.execute("delete from runs where id=?", (run_id,))
            # Limpiar también los scores de seed_scores → los pesos del Universo van a 0
            conn.execute(
                "update seed_scores set score=null, accepted=null "
                "where score is not null"
            )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error al borrar de SQLite", str(exc))
            return

        self.ubs_history_run_checked.discard(str(run_id))
        self.status_text.set(
            f"Run #{run_id} eliminado — {total} candidatos, {deleted_files} archivos"
        )
        self._refresh_ubs_history_panel()
        self._safe_refresh("ubs_universe", self._refresh_ubs_universe)

    # ── History: eliminar set de candidato ────────────────────────────────

    def _delete_ubs_history_candidate_set(self) -> None:
        if not hasattr(self, "ubs_history_candidates_tree"):
            return
        checked = [
            info for item, info in self.ubs_history_candidate_paths.items()
            if info.get("id") in self.ubs_history_candidate_checked
        ]
        if not checked:
            sel = self.ubs_history_candidates_tree.selection()
            if not sel:
                messagebox.showinfo("Eliminar set", "Selecciona un candidato primero.")
                return
            checked = [self.ubs_history_candidate_paths.get(sel[0], {})]

        count = len(checked)
        if not messagebox.askyesno(
            "Eliminar set(s)",
            f"Eliminar {count} set(s) del disco y poner su peso a 0 (score=NULL)?\n"
            "El candidato queda en la DB como referencia histórica.",
        ):
            return

        memory_path = self._ubs_memory_path()
        deleted = 0
        cids: list[str] = []

        for info in checked:
            sp = Path(str(info.get("set", "") or ""))
            if sp.exists():
                try:
                    sp.unlink()
                    deleted += 1
                except OSError:
                    pass
            cid = info.get("id", "")
            if cid:
                cids.append(cid)

        if cids and memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                ph = ",".join("?" for _ in cids)
                conn.execute(
                    f"update candidates set score=null, accepted=null where id in ({ph})",
                    cids,
                )
                conn.commit()
                conn.close()
            except sqlite3.Error:
                pass

        self.ubs_history_candidate_checked.clear()
        self.status_text.set(
            f"Sets eliminados: {deleted} | pesos limpiados: {len(cids)}"
        )
        self._refresh_ubs_history_candidates()
        self._safe_refresh("ubs_universe", self._refresh_ubs_universe)
