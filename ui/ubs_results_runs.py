from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path, workspace_path_exists


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


CANDIDATE_ROBUSTNESS_DDL = """
            create table if not exists candidate_robustness (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                degradation_json text not null default '',
                from_date text not null default '',
                to_date text not null default '',
                positive_bonus real not null default 70.0,
                negative_bonus real not null default -70.0,
                evaluated_at text not null
            )
"""


CANDIDATE_FINAL_TICK_DDL = """
            create table if not exists candidate_final_tick (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                accepted integer,
                ohlc_report_path text,
                real_tick_report_path text,
                ohlc_score real,
                real_tick_score real,
                ohlc_metrics_json text,
                real_tick_metrics_json text,
                similarity_json text,
                history_quality real,
                min_history_quality real not null default 80.0,
                from_date text not null default '',
                to_date text not null default '',
                max_net_delta_pct real not null default 35.0,
                max_pf_delta_pct real not null default 35.0,
                max_dd_delta_pct real not null default 35.0,
                max_trades_delta_pct real not null default 35.0,
                evaluated_at text not null
            )
"""


CANDIDATE_FINAL_TICK_6M_DDL = """
            create table if not exists candidate_final_tick_6m (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                accepted integer,
                ohlc_report_path text,
                real_tick_report_path text,
                ohlc_score real,
                real_tick_score real,
                ohlc_metrics_json text,
                real_tick_metrics_json text,
                similarity_json text,
                history_quality real,
                min_history_quality real not null default 80.0,
                from_date text not null default '',
                to_date text not null default '',
                max_net_delta_pct real not null default 35.0,
                max_pf_delta_pct real not null default 35.0,
                max_dd_delta_pct real not null default 35.0,
                max_trades_delta_pct real not null default 35.0,
                evaluated_at text not null
            )
"""


CANDIDATE_REGRESSION_DDL = """
            create table if not exists candidate_regression (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                accepted integer,
                report_path text,
                score real,
                metrics_json text,
                details_json text,
                from_date text not null default '2017.01.01',
                to_date text not null default '2019.12.31',
                positive_points real not null default 80.0,
                negative_points real not null default -100.0,
                points_applied real not null default 0.0,
                evaluated_at text not null
            )
"""


class UBSResultsRunsMixin:
    """Esquema de memoria, continuacion y seleccion de run."""

    def _ensure_ubs_memory_schema(self, conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("pragma table_info(runs)")}
        if "hidden" not in columns:
            conn.execute("alter table runs add column hidden integer not null default 0")
        conn.execute(CANDIDATE_ROBUSTNESS_DDL)
        robust_columns = {str(row["name"]) for row in conn.execute("pragma table_info(candidate_robustness)")}
        if "degradation_json" not in robust_columns:
            conn.execute("alter table candidate_robustness add column degradation_json text not null default ''")
        conn.execute(CANDIDATE_FINAL_TICK_DDL)
        conn.execute(CANDIDATE_FINAL_TICK_6M_DDL)
        conn.execute(CANDIDATE_REGRESSION_DDL)
        conn.commit()

    @staticmethod
    def _ubs_run_pending_counters(conn, run):
        """Ultima generacion, la pendiente de backtest y los reintentables."""
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
        retryable_count = int(conn.execute(
            """
            select count(*) as total
            from candidates
            where run_id=? and status in ('report_mismatch', 'no_report')
            """,
            (run["id"],),
        ).fetchone()["total"] or 0)
        return latest_generation, pending_generation, pending_count, retryable_count

    def _read_ubs_continuation_state(self) -> dict[str, object]:
        """Ultimo run y sus contadores, o el motivo por el que no se puede continuar."""
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
            latest_generation, pending_generation, pending_count, retryable_count = (
                self._ubs_run_pending_counters(conn, run)
            )
            rows = conn.execute(
                "select set_path from candidates where run_id=? and generation=?",
                (run["id"], latest_generation),
            ).fetchall() if latest_generation > 0 else []
            conn.close()
        except sqlite3.Error as exc:
            return {"available": False, "message": f"Continuar: error SQLite ({exc})"}

        return {
            "run": run,
            "latest_generation": latest_generation,
            "pending_generation": pending_generation,
            "pending_count": pending_count,
            "retryable_count": retryable_count,
            "rows": rows,
        }

    @staticmethod
    def _ubs_pending_backtest_info(
        run, latest_generation, pending_generation, pending_count,
        retryable_count, planned_generations, variants_per_seed, max_seeds,
    ) -> dict[str, object]:
        """Respuesta cuando la ultima generacion se creo sin lanzar su backtest."""
        execute_backtests = True
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
            "retryable_count": retryable_count,
            "planned_generations": planned_generations,
            "remaining": remaining_after_pending,
            "seed_count": pending_count,
            "variants_per_seed": variants_per_seed,
            "max_seeds": max_seeds,
            "execute_backtests": execute_backtests,
        }

    def _ubs_continuation_info(self) -> dict[str, object]:
        state = self._read_ubs_continuation_state()
        if "message" in state:
            return state
        run = state["run"]
        latest_generation = state["latest_generation"]
        pending_generation = state["pending_generation"]
        pending_count = state["pending_count"]
        retryable_count = state["retryable_count"]
        rows = state["rows"]
        planned_generations = int(run["generations"] or 0)
        variants_per_seed = int(run["variants_per_seed"] or 0)
        max_seeds = int(run["max_seeds"] or 0)
        execute_backtests = bool(run["execute_backtests"])
        seed_count = len({str(resolve_workspace_path(row["set_path"])) for row in rows if workspace_path_exists(row["set_path"])})
        if latest_generation <= 0 or seed_count <= 0:
            return {"available": False, "message": f"Continuar: run #{run['id']} sin seeds disponibles"}

        if execute_backtests and pending_generation > 0 and pending_count > 0:
            return self._ubs_pending_backtest_info(
                run, latest_generation, pending_generation, pending_count,
                retryable_count, planned_generations, variants_per_seed, max_seeds,
            )
        remaining = max(0, planned_generations - latest_generation)
        if remaining <= 0:
            return {
                "available": False,
                "message": f"Continuar: deshabilitado, run #{run['id']} completo ({latest_generation}/{planned_generations})",
                "run_id": int(run["id"]),
                "latest_generation": latest_generation,
                "planned_generations": planned_generations,
                "remaining": 0,
                "retryable_count": retryable_count,
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
            "retryable_count": retryable_count,
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

    def _set_ubs_results_execute_backtests_enabled(self, enabled: bool) -> None:
        button = getattr(self, "ubs_results_execute_backtests_btn", None)
        if button is None:
            return
        button.configure(state=("normal" if enabled else "disabled"), cursor=("hand2" if enabled else ""))

    def _set_ubs_results_complete_run_enabled(self, enabled: bool) -> None:
        button = getattr(self, "ubs_results_complete_run_btn", None)
        if button is None:
            return
        button.configure(state=("normal" if enabled else "disabled"), cursor=("hand2" if enabled else ""))

    def _set_ubs_results_continue_run_enabled(self, enabled: bool) -> None:
        button = getattr(self, "ubs_results_continue_run_btn", None)
        if button is None:
            return
        button.configure(state=("normal" if enabled else "disabled"), cursor=("hand2" if enabled else ""))

    def _visible_ubs_pending_backtests_info(self) -> dict[str, object]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return {"available": False, "message": "No existe memoria UBS."}
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs where hidden=0 order by id desc limit 1").fetchone()
            if run is None:
                conn.close()
                return {"available": False, "message": "No hay run visible en Resultados."}
            pending = int(conn.execute(
                """
                select count(*) as total
                from candidates
                where run_id=? and status='generated'
                """,
                (run["id"],),
            ).fetchone()["total"] or 0)
            conn.close()
            return {
                "available": pending > 0,
                "message": (
                    f"Run #{run['id']} tiene {pending} backtests pendientes."
                    if pending > 0
                    else f"Run #{run['id']} no tiene backtests pendientes."
                ),
                "run_id": int(run["id"]),
                "pending_count": pending,
            }
        except sqlite3.Error as exc:
            return {"available": False, "message": f"Error SQLite: {exc}"}

    def _run_visible_ubs_pending_backtests(self) -> None:
        visible_info = self._visible_ubs_pending_backtests_info()
        if not visible_info.get("available"):
            messagebox.showinfo("Ejecutar backtests", str(visible_info.get("message") or "No hay backtests pendientes."))
            return
        continuation_info = self._ubs_continuation_info()
        if not continuation_info.get("available"):
            messagebox.showwarning(
                "Ejecutar backtests",
                str(continuation_info.get("message") or "El run pendiente no se puede continuar."),
            )
            return
        if int(visible_info.get("run_id") or 0) != int(continuation_info.get("run_id") or 0):
            messagebox.showwarning(
                "Ejecutar backtests",
                "El run visible no coincide con el ultimo run continuable. Actualiza la vista antes de ejecutar.",
            )
            return
        if int(continuation_info.get("pending_count") or 0) <= 0:
            messagebox.showinfo("Ejecutar backtests", "El run continuable no tiene backtests pendientes.")
            return
        try:
            args = self._ubs_generator_args(continue_last=True)
        except Exception as exc:
            self._show_error("No se pudo iniciar backtests pendientes", str(exc))
            return
        args.append("--backtest-pending-only")
        pending_count = int(continuation_info.get("pending_count") or 0)
        details = [
            "Accion: Ejecutar backtests pendientes UBS",
            f"Run: #{continuation_info.get('run_id')}",
            f"Generacion pendiente: {continuation_info.get('pending_generation')}",
            f"Backtests pendientes: {pending_count}",
            "Generaciones nuevas: no",
            f"Auto robustez: {'si' if self.ubs_robust_auto.get() else 'no'}",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar backtests pendientes UBS", pending_count, details):
            self._run_script("ubs_agent.py", args)

    def _run_visible_ubs_complete_run(self) -> None:
        visible_info = self._visible_ubs_pending_backtests_info()
        continuation_info = self._ubs_continuation_info()
        if not continuation_info.get("available"):
            messagebox.showinfo(
                "Completar run",
                str(continuation_info.get("message") or "No hay run pendiente para completar."),
            )
            return
        if int(visible_info.get("run_id") or 0) != int(continuation_info.get("run_id") or 0):
            messagebox.showwarning(
                "Completar run",
                "El run visible no coincide con el ultimo run continuable. Actualiza la vista antes de ejecutar.",
            )
            return
        remaining = int(continuation_info.get("remaining") or 0)
        pending_count = int(continuation_info.get("pending_count") or 0)
        if remaining <= 0 and pending_count <= 0:
            messagebox.showinfo("Completar run", "El run visible ya esta completo.")
            return
        self._run_ubs_continue()

    def _ubs_results_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                r.hidden,
                count(c.id) as total,
                sum(case when c.status = 'accepted' then 1 else 0 end) as accepted,
                sum(case when c.status = 'rejected' then 1 else 0 end) as rejected
            from runs r
            left join candidates c on c.run_id = r.id
            where r.hidden = 0
            group by r.id
            having count(c.id) > 0
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
            hidden_tag = " [arch]" if row["hidden"] else ""
            options.append((run_id, f"#{run_id} | {created} | {total} ({accepted}/{rejected}){hidden_tag}"))
        return options

    def _selected_ubs_results_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_results_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_results_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_results_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _label in options):
                return run_id
        return newest_run_id

    def _update_ubs_results_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_results_run_combo"):
            return
        labels = [label for _run_id, label in options]
        self.ubs_results_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_results_run_id.get() != selected_label:
            self.ubs_results_run_id.set(selected_label)
