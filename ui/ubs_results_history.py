from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSResultsHistoryMixin:
    """Historico de runs y de candidatos."""

    def _insert_ubs_history_rows(self, rows):
        """Una fila por run en el arbol del historico."""
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
        self._insert_ubs_history_rows(rows)
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

    def _insert_ubs_history_candidate_rows(self, rows):
        """Una fila por candidato del run seleccionado."""
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
        self._insert_ubs_history_candidate_rows(rows)
