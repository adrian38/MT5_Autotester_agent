"""Umbrales, fechas, motivos y seleccion de filas de la pantalla Final Tick."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent

FINAL_TICK_RETRYABLE_STATUSES = {
    "pending",
    "no_report",
    "parse_error",
    "report_mismatch",
}
FINAL_TICK_DATE_RETRYABLE_STATUSES = {
    "pending_history_quality",
}


class UBSFinalTickRowsMixin:
    """Lectura de umbrales, fechas de etapa, motivos y apertura de ficheros."""

    def _ubs_final_tick_threshold_values(self) -> dict[str, float]:
        return {
            "min_quality": self._score_float(
                self.ubs_final_tick_min_history_quality,
                "Final Tick calidad minima",
                minimum=0.0,
                maximum=100.0,
            ),
            "min_ohlc_trades": int(
                self._score_float(
                    self.ubs_final_tick_min_ohlc_trades,
                    "Final Tick min ops OHLC",
                    minimum=0.0,
                )
            ),
            "min_trades_w1": int(
                self._score_float(
                    self.ubs_final_tick_min_trades_w1,
                    "Final Tick W1 min ops",
                    minimum=0.0,
                )
            ),
            "min_trades_mn": int(
                self._score_float(
                    self.ubs_final_tick_min_trades_mn,
                    "Final Tick MN min ops",
                    minimum=0.0,
                )
            ),
            "net_delta": self._score_float(
                self.ubs_final_tick_max_net_delta_pct,
                "Final Tick delta net",
                minimum=0.0,
            ),
            "pf_delta": self._score_float(
                self.ubs_final_tick_max_pf_delta_pct,
                "Final Tick delta PF",
                minimum=0.0,
            ),
            "dd_delta": self._score_float(
                self.ubs_final_tick_max_dd_delta_pct,
                "Final Tick delta DD",
                minimum=0.0,
            ),
            "trades_delta": self._score_float(
                self.ubs_final_tick_max_trades_delta_pct,
                "Final Tick delta trades",
                minimum=0.0,
            ),
        }

    def _ubs_final_tick_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                r.hidden,
                count(c.id) as total,
                sum(case when c.status='accepted' and cr.status='accepted' then 1 else 0 end) as robust_ok,
                sum(case when c.status='accepted' and cr.status='accepted' and ft.status in ('accepted', 'rejected') then 1 else 0 end) as final_done,
                sum(case when c.status='accepted' and cr.status='accepted' and ft.status='accepted' then 1 else 0 end) as final_ok,
                sum(case when c.status='accepted' and cr.status='accepted' and ft.status='rejected' then 1 else 0 end) as final_fail
            from runs r
            left join candidates c on c.run_id = r.id
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            robust_ok = int(row["robust_ok"] or 0)
            final_done = int(row["final_done"] or 0)
            final_ok = int(row["final_ok"] or 0)
            final_fail = int(row["final_fail"] or 0)
            hidden_tag = " [arch]" if row["hidden"] else ""
            options.append((
                run_id,
                f"#{run_id} | {created} | cand {total} | robust {robust_ok} | FT {final_done} OK {final_ok} FAIL {final_fail}{hidden_tag}",
            ))
        return options

    def _selected_ubs_final_tick_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_final_tick_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_final_tick_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_final_tick_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _ in options):
                return run_id
        return newest_run_id

    def _update_ubs_final_tick_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_final_tick_run_combo"):
            return
        labels = [label for _, label in options]
        self.ubs_final_tick_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_final_tick_run_id.get() != selected_label:
            self.ubs_final_tick_run_id.set(selected_label)

    def _latest_visible_ubs_run_for_final_tick(self, *, final_tick_stage: str = "probe") -> sqlite3.Row | None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return None
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            run_var = self.ubs_final_tick_6m_run_id if final_tick_stage == "six_month" else self.ubs_final_tick_run_id
            selected = run_var.get().strip()
            match = re.search(r"#?(\d+)", selected)
            if match:
                run = conn.execute("select * from runs where id=?", (int(match.group(1)),)).fetchone()
                if run is not None:
                    return run
            return conn.execute("select * from runs where hidden=0 order by id desc limit 1").fetchone()
        finally:
            conn.close()

    def _accepted_candidates_for_final_tick(self, run_id: int, *, final_tick_stage: str = "probe") -> list[sqlite3.Row]:
        memory_path = self._ubs_memory_path()
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            final_tick_stage = str(final_tick_stage or "probe").strip().lower()
            final_tick_table = "candidate_final_tick_6m" if final_tick_stage in {"six_month", "6m"} else "candidate_final_tick"
            probe_join = ""
            if final_tick_table != "candidate_final_tick":
                probe_join = (
                    "join candidate_final_tick probe_ft on probe_ft.candidate_id = c.id "
                    "and probe_ft.status in ('accepted', 'pending_ohlc_trades')"
                )
            return conn.execute(
                f"""
                select
                    c.*,
                    ft.status as final_tick_status,
                    ft.from_date as final_tick_from_date,
                    ft.to_date as final_tick_to_date
                from candidates c
                join candidate_robustness cr on cr.candidate_id = c.id
                {probe_join}
                left join {final_tick_table} ft on ft.candidate_id = c.id
                where c.run_id=? and c.status='accepted' and cr.status='accepted'
                order by c.generation, c.id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()

    def _final_tick_stage_dates(self, final_tick_stage: str = "probe") -> tuple[str, str, str, str]:
        stage = str(final_tick_stage or "probe").strip().lower()
        if stage in {"six_month", "6m"}:
            return (
                self.ubs_final_tick_6m_from_date.get().strip(),
                self.ubs_final_tick_6m_to_date.get().strip(),
                self.ubs_final_tick_6m_ohlc_from_date.get().strip(),
                self.ubs_final_tick_6m_ohlc_to_date.get().strip(),
            )
        return (
            self.ubs_final_tick_from_date.get().strip(),
            self.ubs_final_tick_to_date.get().strip(),
            "",
            "",
        )

    def _final_tick_effective_dates_for_row(self, row: sqlite3.Row, *, final_tick_stage: str = "probe") -> tuple[str, str]:
        status = str(row["final_tick_status"] or "").strip()
        from_date, to_date, ohlc_from, ohlc_to = self._final_tick_stage_dates(final_tick_stage)
        if final_tick_stage in {"six_month", "6m"} and status == "pending_ohlc_trades" and ohlc_from and ohlc_to:
            return ohlc_from, ohlc_to
        return from_date, to_date

    def _final_tick_row_dates_match(self, row: sqlite3.Row, *, final_tick_stage: str = "probe") -> bool:
        stored_from = str(row["final_tick_from_date"] or "").strip()
        stored_to = str(row["final_tick_to_date"] or "").strip()
        if not stored_from and not stored_to:
            return False
        from_date, to_date = self._final_tick_effective_dates_for_row(row, final_tick_stage=final_tick_stage)
        return stored_from == from_date and stored_to == to_date

    def _final_tick_row_pending_for_current_dates(self, row: sqlite3.Row, *, final_tick_stage: str = "probe") -> bool:
        stage = str(final_tick_stage or "probe").strip().lower()
        status = str(row["final_tick_status"] or "").strip()
        if not status:
            return True
        if status in FINAL_TICK_RETRYABLE_STATUSES:
            return True
        if stage in {"six_month", "6m"} and status == "pending_ohlc_trades":
            return not self._final_tick_row_dates_match(row, final_tick_stage=final_tick_stage)
        if status in FINAL_TICK_DATE_RETRYABLE_STATUSES:
            return stage in {"six_month", "6m"} and not self._final_tick_row_dates_match(row, final_tick_stage=final_tick_stage)
        return False

    def _parse_ubs_final_tick_similarity(self, raw) -> dict:
        try:
            data = json.loads(str(raw or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ubs_final_tick_reason(
        self,
        status: str,
        similarity: dict,
        *,
        history_quality: object = None,
        min_history_quality: object = None,
    ) -> str:
        if status == "missing_6m":
            return "sin resultado Final Tick 6M"
        if status == "pending":
            return "pendiente"
        if status == "pending_history_quality":
            return self._final_tick_pending_quality_reason(
                similarity, history_quality, min_history_quality,
            )
        if status == "pending_ohlc_trades":
            checks = similarity.get("checks") if isinstance(similarity.get("checks"), dict) else {}
            check = checks.get("ohlc_trades", {}) if isinstance(checks, dict) else {}
            trades = check.get("ohlc") if isinstance(check, dict) else None
            minimum = check.get("min_trades") if isinstance(check, dict) else None
            return f"OHLC pendiente: {self._format_ubs_int(trades)} ops < {self._format_ubs_int(minimum)}"
        if status == "no_report":
            return "sin reporte OHLC o real tick"
        if status == "parse_error":
            return "error al parsear reporte"
        if status == "report_mismatch":
            return "mismatch symbol/TF"
        if status == "no_trades":
            return "sin operaciones en el tramo"
        reasons = similarity.get("reasons") or []
        if not reasons:
            return ""
        checks = similarity.get("checks") if isinstance(similarity.get("checks"), dict) else {}
        return " | ".join(
            self._final_tick_reason_part(reason, similarity, checks) for reason in reasons
        )

    def _final_tick_pending_quality_reason(
        self, similarity: dict, history_quality: object, min_history_quality: object,
    ) -> str:
        quality = similarity.get("history_quality", history_quality)
        minimum = similarity.get("min_history_quality", min_history_quality)
        reasons = {str(reason) for reason in (similarity.get("reasons") or [])}
        if "real_tick_no_history" in reasons:
            return "descarga Real Tick interrumpida; reintento pendiente"
        if "empty_tester_context" in reasons:
            quality_text = self._format_ubs_number(quality)
            return (
                f"contexto tester pendiente (calidad reportada {quality_text}%)"
                if quality_text
                else "contexto tester pendiente"
            )
        if quality is not None and minimum is not None:
            try:
                quality_below_minimum = float(quality) < float(minimum)
            except (TypeError, ValueError):
                quality_below_minimum = False
            if quality_below_minimum:
                return (
                    f"calidad pendiente: {self._format_ubs_number(quality)}% < "
                    f"{self._format_ubs_number(minimum)}%"
                )
            return (
                f"historico/contexto pendiente: calidad {self._format_ubs_number(quality)}% "
                f"(minimo {self._format_ubs_number(minimum)}%)"
            )
        return "historico/contexto pendiente"

    def _final_tick_reason_part(self, reason: object, similarity: dict, checks: object) -> str:
        if reason == "history_quality":
            quality = similarity.get("history_quality")
            minimum = similarity.get("min_history_quality")
            return f"calidad: {self._format_ubs_number(quality)}% <= {self._format_ubs_number(minimum)}%"
        if reason == "ohlc_trades":
            check = checks.get("ohlc_trades", {}) if isinstance(checks, dict) else {}
            trades = check.get("ohlc") if isinstance(check, dict) else None
            minimum = check.get("min_trades") if isinstance(check, dict) else None
            return f"OHLC ops: {self._format_ubs_int(trades)} < {self._format_ubs_int(minimum)}"
        if reason == "profit_factor_floor":
            check = checks.get("profit_factor_floor", {}) if isinstance(checks, dict) else {}
            ohlc = check.get("ohlc") if isinstance(check, dict) else None
            tick = check.get("real_tick") if isinstance(check, dict) else None
            minimum = check.get("min_profit_factor") if isinstance(check, dict) else None
            return (
                f"PF minimo: OHLC {self._format_ubs_number(ohlc)} / tick {self._format_ubs_number(tick)} < {self._format_ubs_number(minimum)}"
            )
        # Puertas absolutas del respaldo por control OHLC sin perdidas: no
        # llevan delta contra el OHLC, sino el valor de la pata de tick
        # contra su limite.
        absolute_labels = {
            "tick_trades": ("ops tick", "<"),
            "tick_net_profit": ("net tick", "<"),
            "tick_profit_factor": ("PF tick", "<"),
            "tick_drawdown_pct": ("DD tick", ">"),
            "tick_recovery_factor": ("RF tick", "<"),
            "tick_positive_month_ratio": ("meses+ tick", "<"),
        }
        if str(reason) in absolute_labels:
            check = checks.get(str(reason), {}) if isinstance(checks, dict) else {}
            label, operator = absolute_labels[str(reason)]
            observed = check.get("real_tick") if isinstance(check, dict) else None
            limit = check.get("limit") if isinstance(check, dict) else None
            return (
                f"{label}: {self._format_ubs_number(observed)} {operator} {self._format_ubs_number(limit)}"
                if observed is not None
                else label
            )
        check = checks.get(str(reason), {}) if isinstance(checks, dict) else {}
        delta = check.get("delta_pct") if isinstance(check, dict) else None
        maximum = check.get("max_delta_pct") if isinstance(check, dict) else None
        labels = {
            "net_profit": "net",
            "profit_factor": "PF",
            "drawdown_pct": "DD",
            "trades": "trades",
        }
        label = labels.get(str(reason), str(reason))
        if delta is None:
            return label
        return f"{label}: {self._format_ubs_number(delta)}% > {self._format_ubs_number(maximum)}%"

    def _metric_from_json(self, raw, key: str):
        metrics = self._parse_ubs_metrics(raw)
        return metrics.get(key)

    def _selected_ubs_final_tick_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_final_tick_tree"):
            return {}
        selected = self.ubs_final_tick_tree.selection()
        if not selected:
            return {}
        return self.ubs_final_tick_paths.get(selected[0], {})

    def _selected_ubs_final_tick_path(self, kind: str) -> Path | None:
        info = self._selected_ubs_final_tick_info()
        raw_path = info.get(kind, "")
        return Path(raw_path).expanduser() if raw_path else None

    def _open_selected_ubs_final_tick_set(self) -> None:
        path = self._selected_ubs_final_tick_path("set")
        if path is None:
            messagebox.showinfo("Final Tick UBS", "Selecciona una fila primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_final_tick_ohlc_report(self) -> None:
        path = self._selected_ubs_final_tick_path("ohlc_report")
        if path is None:
            messagebox.showinfo("Final Tick UBS", "Esa fila no tiene reporte OHLC asociado.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_final_tick_real_report(self) -> None:
        path = self._selected_ubs_final_tick_path("real_report")
        if path is None:
            messagebox.showinfo("Final Tick UBS", "Esa fila no tiene reporte real tick asociado.")
            return
        self._open_local_file(path)
