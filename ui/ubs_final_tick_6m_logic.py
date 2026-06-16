from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_final_tick


class UBSFinalTick6MLogicMixin:
    def _ubs_final_tick_6m_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id, r.created_at, r.hidden,
                count(distinct c.id) as total,
                sum(case when probe.status in ('accepted', 'pending_ohlc_trades') then 1 else 0 end) as probe_eligible,
                sum(case when ft6.status in ('accepted', 'rejected') then 1 else 0 end) as six_done,
                sum(case when ft6.status='accepted' then 1 else 0 end) as six_ok,
                sum(case when ft6.status='rejected' then 1 else 0 end) as six_fail
            from runs r
            left join candidates c on c.run_id = r.id
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick probe on probe.candidate_id = c.id
            left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
            where c.id is null
               or (c.status='accepted' and cr.status='accepted')
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            probe_eligible = int(row["probe_eligible"] or 0)
            six_done = int(row["six_done"] or 0)
            six_ok = int(row["six_ok"] or 0)
            six_fail = int(row["six_fail"] or 0)
            hidden_tag = " [arch]" if row["hidden"] else ""
            options.append((
                run_id,
                f"#{run_id} | {created} | cand {total} | corto eleg {probe_eligible} | 6M {six_done} OK {six_ok} FAIL {six_fail}{hidden_tag}",
            ))
        return options

    def _selected_ubs_final_tick_6m_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_final_tick_6m_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_final_tick_6m_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_final_tick_6m_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _ in options):
                return run_id
        return newest_run_id

    def _update_ubs_final_tick_6m_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_final_tick_6m_run_combo"):
            return
        labels = [label for _, label in options]
        self.ubs_final_tick_6m_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_final_tick_6m_run_id.get() != selected_label:
            self.ubs_final_tick_6m_run_id.set(selected_label)

    def _refresh_ubs_final_tick_6m_panel(self) -> None:
        self._refresh_ubs_final_tick_6m()

    def _refresh_ubs_final_tick_6m(self) -> None:
        if hasattr(self, "ubs_final_tick_6m_tree"):
            for item in self.ubs_final_tick_6m_tree.get_children():
                self.ubs_final_tick_6m_tree.delete(item)
        self.ubs_final_tick_6m_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_final_tick_6m_summary.set("Final Tick 6M: sin memoria UBS")
            self.ubs_final_tick_6m_status.set(f"No existe memoria: {memory_path}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run_options = self._ubs_final_tick_6m_run_options(conn)
            selected_run_id = self._selected_ubs_final_tick_6m_run_id(run_options)
            self._update_ubs_final_tick_6m_run_combo(run_options, selected_run_id)
            if selected_run_id <= 0:
                conn.close()
                self.ubs_final_tick_6m_summary.set("Final Tick 6M: sin run visible")
                self.ubs_final_tick_6m_status.set("No hay run seleccionado.")
                return
            run = conn.execute("select * from runs where id=?", (selected_run_id,)).fetchone()
            if run is None:
                conn.close()
                self.ubs_final_tick_6m_summary.set("Final Tick 6M: sin run visible")
                self.ubs_final_tick_6m_status.set("No hay run seleccionado.")
                return
            rows = conn.execute(
                """
                select
                    c.id, c.run_id, c.generation, c.target_symbol, c.symbol, c.period,
                    c.set_path,
                    ft6.status as final_status,
                    ft6.ohlc_report_path,
                    ft6.real_tick_report_path,
                    ft6.ohlc_score,
                    ft6.real_tick_score,
                    ft6.ohlc_metrics_json,
                    ft6.real_tick_metrics_json,
                    ft6.similarity_json,
                    ft6.history_quality,
                    ft6.from_date,
                    ft6.to_date
                from candidates c
                join candidate_robustness cr on cr.candidate_id = c.id
                join candidate_final_tick probe
                    on probe.candidate_id = c.id
                   and probe.status in ('accepted', 'pending_ohlc_trades')
                left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
                where c.run_id=? and c.status='accepted' and cr.status='accepted'
                order by
                    case
                        when ft6.status='accepted' then 0
                        when ft6.status='rejected' then 1
                        when ft6.status is null then 2
                        else 3
                    end,
                    ft6.real_tick_score desc,
                    c.id desc
                """,
                (run["id"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_final_tick_6m_summary.set("Final Tick 6M: error SQLite")
            self.ubs_final_tick_6m_status.set(str(exc))
            return

        total = len(rows)
        accepted = sum(1 for row in rows if row["final_status"] == "accepted")
        rejected = sum(1 for row in rows if row["final_status"] == "rejected")
        pending = total - accepted - rejected
        self.ubs_final_tick_6m_summary.set(
            f"Run #{run['id']} | candidatos corto elegibles {total} | 6M OK {accepted} | 6M FAIL {rejected} | pend {pending}"
        )
        from_date, to_date, retry_from, retry_to = self._final_tick_stage_dates("six_month")
        retry_label = f" | Retry pocas ops OHLC: {retry_from} -> {retry_to}" if retry_from and retry_to else ""
        self.ubs_final_tick_6m_status.set(
            f"Fechas 6M config: {from_date} -> {to_date}{retry_label}"
        )
        if not hasattr(self, "ubs_final_tick_6m_tree"):
            return

        for index, row in enumerate(rows):
            status = str(row["final_status"] or "pending")
            similarity = self._parse_ubs_final_tick_similarity(row["similarity_json"])
            date_range = ""
            if row["from_date"] or row["to_date"]:
                date_range = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}"
            item = self.ubs_final_tick_6m_tree.insert(
                "",
                "end",
                values=(
                    row["run_id"],
                    row["id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    self._ubs_final_tick_reason(status, similarity),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    f"{self._format_ubs_number(row['history_quality'])}%" if row["history_quality"] is not None else "",
                    self._format_ubs_number(row["ohlc_score"]),
                    self._format_ubs_number(row["real_tick_score"]),
                    self._format_ubs_number(self._metric_from_json(row["ohlc_metrics_json"], "net_profit")),
                    self._format_ubs_number(self._metric_from_json(row["real_tick_metrics_json"], "net_profit")),
                    self._format_ubs_number(self._metric_from_json(row["ohlc_metrics_json"], "profit_factor")),
                    self._format_ubs_number(self._metric_from_json(row["real_tick_metrics_json"], "profit_factor")),
                    self._format_ubs_number(self._metric_from_json(row["ohlc_metrics_json"], "drawdown_pct")),
                    self._format_ubs_number(self._metric_from_json(row["real_tick_metrics_json"], "drawdown_pct")),
                    self._format_ubs_int(self._metric_from_json(row["ohlc_metrics_json"], "trades")),
                    self._format_ubs_int(self._metric_from_json(row["real_tick_metrics_json"], "trades")),
                    date_range,
                    Path(str(row["set_path"] or "")).name,
                ),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            self.ubs_final_tick_6m_paths[item] = {
                "id": str(row["id"] or ""),
                "set": str(row["set_path"] or ""),
                "ohlc_report": str(row["ohlc_report_path"] or ""),
                "real_report": str(row["real_tick_report_path"] or ""),
            }

    def _selected_ubs_final_tick_6m_infos(self) -> list[dict[str, str]]:
        if not hasattr(self, "ubs_final_tick_6m_tree"):
            return []
        selected = self.ubs_final_tick_6m_tree.selection()
        return [
            self.ubs_final_tick_6m_paths[item]
            for item in selected
            if item in self.ubs_final_tick_6m_paths
        ]

    def _selected_ubs_final_tick_6m_info(self) -> dict[str, str]:
        infos = self._selected_ubs_final_tick_6m_infos()
        return infos[0] if infos else {}

    def _manual_mark_selected_ubs_final_tick_6m(self, status: str) -> None:
        infos = self._selected_ubs_final_tick_6m_infos()
        ids = [info.get("id", "") for info in infos if info.get("id")]
        if not ids:
            messagebox.showinfo("Estado manual 6M", "Selecciona una o mas filas de Final Tick 6M primero.")
            return
        label = "OK" if status == "accepted" else "FAIL"
        if not messagebox.askyesno(
            "Estado manual 6M",
            f"Marcar {len(ids)} fila(s) de Final Tick 6M como {label} manual?\n\n"
            "Esto cambia SQLite, pesos, score de feedback y elegibilidad de UBS Portfolio.",
        ):
            return
        try:
            thresholds = self._ubs_final_tick_threshold_values()
            from_date, to_date, _retry_from, _retry_to = self._final_tick_stage_dates("six_month")
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            updated = mark_candidate_final_tick(
                conn,
                ids,
                status,
                final_tick_stage="six_month",
                min_history_quality=thresholds["min_quality"],
                from_date=from_date,
                to_date=to_date,
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, ValueError) as exc:
            self._show_error("No se pudo aplicar estado manual 6M", str(exc))
            return
        self.ubs_weights_locked.set(False)
        self.status_text.set(f"Estado manual aplicado a {updated} fila(s) de Final Tick 6M")
        self._refresh_ubs_final_tick_6m_panel()
        for label_name, callback_name in (
            ("ubs_universe", "_refresh_ubs_universe"),
            ("ubs_portfolio_availability", "_refresh_ubs_portfolio_availability"),
        ):
            if hasattr(self, callback_name):
                self._safe_refresh(label_name, getattr(self, callback_name))

    def _manual_accept_selected_ubs_final_tick_6m(self) -> None:
        self._manual_mark_selected_ubs_final_tick_6m("accepted")

    def _manual_reject_selected_ubs_final_tick_6m(self) -> None:
        self._manual_mark_selected_ubs_final_tick_6m("rejected")

    def _selected_ubs_final_tick_6m_path(self, kind: str) -> Path | None:
        info = self._selected_ubs_final_tick_6m_info()
        value = info.get(kind, "") if info else ""
        return Path(value) if value else None

    def _open_selected_ubs_final_tick_6m_set(self) -> None:
        path = self._selected_ubs_final_tick_6m_path("set")
        if path is None:
            messagebox.showinfo("Final Tick 6M UBS", "Selecciona una fila primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_final_tick_6m_ohlc_report(self) -> None:
        path = self._selected_ubs_final_tick_6m_path("ohlc_report")
        if path is None:
            messagebox.showinfo("Final Tick 6M UBS", "Esa fila no tiene reporte OHLC asociado.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_final_tick_6m_real_report(self) -> None:
        path = self._selected_ubs_final_tick_6m_path("real_report")
        if path is None:
            messagebox.showinfo("Final Tick 6M UBS", "Esa fila no tiene reporte real tick asociado.")
            return
        self._open_local_file(path)
