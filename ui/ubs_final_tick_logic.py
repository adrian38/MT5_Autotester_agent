"""Panel de UBS Final Tick: estado manual, reconciliacion y repintado.

Las dos pantallas de candidatos (`UBS Final Tick` y `UBS Final Tick 6M`) son
esta misma clase con otra etapa. Lo comun a las cuatro pantallas Final Tick
vive en `ui/ubs_final_tick_shared.py`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_final_tick
from ui.ubs_final_tick_queries import (
    FINAL_TICK_ELIGIBLE_LABELS,
    FINAL_TICK_MANUAL_WARNINGS,
    final_tick_run_rows_sql,
)
from ui.ubs_final_tick_run import UBSFinalTickRunMixin
from ui.ubs_final_tick_stage import UBSFinalTickStageMixin
from ui.ubs_final_tick_shared import is_six_month, stage_label, stage_suffix


class UBSFinalTickLogicMixin(UBSFinalTickRunMixin, UBSFinalTickStageMixin):

    # ------------------------------------------------------------------
    # Estado manual
    # ------------------------------------------------------------------
    def _manual_mark_final_tick(self, stage: str, status: str) -> None:
        suffix = stage_suffix(stage)
        label = stage_label(stage)
        ids = [info.get("id", "") for info in self._checked_final_tick_infos(stage) if info.get("id")]
        if not ids:
            messagebox.showinfo(
                f"Estado manual {label}", f"Selecciona una o mas filas de {label} primero.",
            )
            return
        verdict = "OK" if status == "accepted" else "FAIL"
        if not messagebox.askyesno(
            f"Estado manual {label}",
            f"Marcar {len(ids)} fila(s) de {label} como {verdict} manual?\n\n"
            f"{FINAL_TICK_MANUAL_WARNINGS[suffix]}",
        ):
            return
        try:
            thresholds = self._ubs_final_tick_threshold_values()
            from_date, to_date, _retry_from, _retry_to = self._final_tick_stage_dates(stage)
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            updated = mark_candidate_final_tick(
                conn,
                ids,
                status,
                final_tick_stage="six_month" if is_six_month(stage) else "probe",
                min_history_quality=thresholds["min_quality"],
                from_date=from_date,
                to_date=to_date,
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
                max_dd_gap_pp=thresholds["dd_gap_pp"],
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, ValueError) as exc:
            self._show_error(f"No se pudo aplicar estado manual {label}", str(exc))
            return
        self._final_tick_checked(stage).clear()
        self.ubs_weights_locked.set(False)
        self.status_text.set(f"Estado manual aplicado a {updated} fila(s) de {label}")
        self._refresh_final_tick_panel(stage)
        if hasattr(self, "_refresh_ubs_portfolio_availability"):
            self._safe_refresh(
                "ubs_portfolio_availability", self._refresh_ubs_portfolio_availability,
            )

    # ------------------------------------------------------------------
    # Reconciliacion desde disco
    # ------------------------------------------------------------------
    def _reconcile_final_tick_from_disk(self, stage: str) -> None:
        """Concilia reportes OHLC/Every Tick ya generados en disco (p. ej. tras
        cortar el proceso manualmente) antes de repintar la tabla. No abre MT5."""
        if self.process and self.process.poll() is None:
            return  # hay un proceso activo escribiendo; no competir con el
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return
        from ubs_agent import reconcile_final_tick_reports
        from ubs.memory import AgentMemory
        from ubs.score import ScoreConfig
        from run_tests import parse_symbol_map

        final_tick_stage = "six_month" if is_six_month(stage) else "probe"
        run = self._latest_visible_ubs_run_for_final_tick(final_tick_stage=final_tick_stage)
        if run is None:
            return
        thresholds = self._ubs_final_tick_threshold_values()
        score_config = ScoreConfig(
            min_net_profit=float(self.ubs_pass_min_net_profit.get() or 100),
            min_profit_factor=float(self.ubs_pass_min_profit_factor.get() or 1.2),
            min_trades=int(float(self.ubs_pass_min_trades.get() or 50)),
            max_drawdown_pct=float(self.ubs_pass_max_drawdown_pct.get() or 25),
            min_recovery_factor=float(self.ubs_pass_min_recovery_factor.get() or 1.0),
        )
        symbol_map = parse_symbol_map(self._effective_ubs_symbol_map_text())
        memory = AgentMemory(memory_path)
        try:
            counts = reconcile_final_tick_reports(
                memory,
                int(run["id"]),
                score_config,
                symbol_map,
                final_tick_stage=final_tick_stage,
                broker=self._ubs_broker(),
                min_history_quality=thresholds["min_quality"],
                min_ohlc_trades=thresholds["min_ohlc_trades"],
                min_trades_w1=thresholds["min_trades_w1"],
                min_trades_mn=thresholds["min_trades_mn"],
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
                max_dd_gap_pp=thresholds["dd_gap_pp"],
                symbol_suffix=self._effective_symbol_suffix_text(),
            )
        finally:
            memory.close()
        if counts:
            resumen = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            self.status_text.set(f"{stage_label(stage)} reconciliado desde disco: {resumen}")

    # ------------------------------------------------------------------
    # Pintado de la tabla
    # ------------------------------------------------------------------
    def _final_tick_run_rows(self, conn: sqlite3.Connection, run_id: int, stage: str) -> list:
        """Candidatos elegibles del run con su resultado de la etapa, si lo tienen."""
        return conn.execute(final_tick_run_rows_sql(stage_suffix(stage)), (run_id,)).fetchall()

    def _load_final_tick_rows(self, stage: str):
        """Run visible y sus filas; None si no hay memoria, run o la consulta falla."""
        label = stage_label(stage)
        summary = self._final_tick_attr(stage, "summary")
        status_var = self._final_tick_attr(stage, "status")
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            summary.set(f"{label}: sin memoria UBS")
            status_var.set(f"No existe memoria: {memory_path}")
            return None
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run_options = self._final_tick_run_options(conn, stage)
            selected_run_id = self._selected_final_tick_run_id(run_options, stage)
            self._update_final_tick_run_combo(run_options, selected_run_id, stage)
            run = (
                conn.execute("select * from runs where id=?", (selected_run_id,)).fetchone()
                if selected_run_id > 0
                else None
            )
            if run is None:
                conn.close()
                summary.set(f"{label}: sin run visible")
                status_var.set("Limpiaste la vista de resultados; el historico conserva la memoria.")
                return None
            rows = self._final_tick_run_rows(conn, run["id"], stage)
            conn.close()
        except sqlite3.Error as exc:
            summary.set(f"{label}: error SQLite")
            status_var.set(str(exc))
            return None
        return run, rows

    def _set_final_tick_summary(self, stage: str, run, rows: list) -> None:
        """Cabecera del panel: recuento por estado y fechas configuradas."""
        total = len(rows)
        accepted = sum(1 for row in rows if row["final_status"] == "accepted")
        rejected = sum(1 for row in rows if row["final_status"] == "rejected")
        missing = sum(1 for row in rows if row["final_status"] is None)
        pending = total - accepted - rejected - missing
        self._final_tick_attr(stage, "summary").set(
            f"Run #{run['id']} | {FINAL_TICK_ELIGIBLE_LABELS[stage_suffix(stage)]} {total} | "
            f"resueltos {accepted + rejected} | OK {accepted} | FAIL {rejected} | "
            f"pendientes {pending} | sin resultado {missing}"
        )
        from_date, to_date, retry_from, retry_to = self._final_tick_stage_dates(stage)
        retry_label = f" | Retry pocas ops OHLC: {retry_from} -> {retry_to}" if retry_from and retry_to else ""
        self._final_tick_attr(stage, "status").set(
            f"Fechas {stage_label(stage)} config: {from_date} -> {to_date}{retry_label}"
        )

    def _final_tick_row_values(self, stage: str, row, status: str, cid: str) -> tuple:
        """Columnas de una fila del arbol, iguales en las dos etapas."""
        similarity = self._parse_ubs_final_tick_similarity(row["similarity_json"])
        date_range = ""
        if row["from_date"] or row["to_date"]:
            date_range = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}"
        return (
            self._checkbox_text(cid in self._final_tick_checked(stage)),
            row["run_id"],
            row["id"],
            row["generation"],
            self._format_ubs_status(status),
            self._ubs_final_tick_reason(
                status,
                similarity,
                history_quality=row["history_quality"],
                min_history_quality=row["min_history_quality"],
            ),
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
        )

    def _final_tick_row_paths(self, row, status: str, cid: str) -> dict[str, str]:
        """Rutas y datos que el panel guarda para cada fila."""
        ohlc_trades = self._metric_from_json(row["ohlc_metrics_json"], "trades")
        return {
            "id": cid,
            "set": str(row["set_path"] or ""),
            "ohlc_report": str(row["ohlc_report_path"] or ""),
            "real_report": str(row["real_tick_report_path"] or ""),
            "status": status,
            "ohlc_trades": "" if ohlc_trades is None else str(ohlc_trades),
        }

    def _fill_final_tick_tree(self, stage: str, rows: list) -> dict[str, str]:
        """Pinta las filas y devuelve el item de cada candidato."""
        tree = self._final_tick_tree_for(stage)
        paths = self._final_tick_attr(stage, "paths")
        missing_status = "missing_6m" if is_six_month(stage) else "pending"
        id_to_item: dict[str, str] = {}
        for index, row in enumerate(rows):
            status = str(row["final_status"] or missing_status)
            cid = str(row["id"] or "")
            item = tree.insert(
                "",
                "end",
                values=self._final_tick_row_values(stage, row, status, cid),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            paths[item] = self._final_tick_row_paths(row, status, cid)
            if cid:
                id_to_item[cid] = item
        return id_to_item

    def _final_tick_view_state(self, stage: str) -> tuple:
        """Guarda scroll y seleccion y deja el arbol vacio para repintarlo."""
        previous_yview: tuple[float, float] | None = None
        previous_xview: tuple[float, float] | None = None
        previous_selected_ids: set[str] = set()
        tree = self._final_tick_tree_for(stage)
        paths = self._final_tick_attr(stage, "paths")
        if tree is not None:
            try:
                previous_yview = tree.yview()
                previous_xview = tree.xview()
                previous_selected_ids = {
                    str(paths.get(item, {}).get("id") or "") for item in tree.selection()
                }
                previous_selected_ids.discard("")
            except Exception:
                previous_yview = None
                previous_xview = None
                previous_selected_ids = set()
            tree.delete(*tree.get_children())
        paths.clear()
        return previous_yview, previous_xview, previous_selected_ids

    def _restore_final_tick_view(self, stage: str, state: tuple, id_to_item: dict[str, str]) -> None:
        """Devuelve el arbol al scroll y la seleccion que tenia antes."""
        tree = self._final_tick_tree_for(stage)
        if tree is None:
            return
        previous_yview, previous_xview, previous_selected_ids = state
        restored_items = [
            id_to_item[candidate_id]
            for candidate_id in previous_selected_ids
            if candidate_id in id_to_item
        ]
        if restored_items:
            tree.selection_set(restored_items)
        for view, mover in ((previous_xview, tree.xview_moveto), (previous_yview, tree.yview_moveto)):
            if view:
                try:
                    mover(view[0])
                except Exception:
                    pass

    def _refresh_final_tick_stage(self, stage: str) -> None:
        state = self._final_tick_view_state(stage)
        loaded = self._load_final_tick_rows(stage)
        if loaded is None:
            return
        run, rows = loaded
        self._set_final_tick_summary(stage, run, rows)
        if self._final_tick_tree_for(stage) is None:
            return
        id_to_item = self._fill_final_tick_tree(stage, rows)
        self._final_tick_checked(stage).intersection_update(set(id_to_item))
        self._restore_final_tick_view(stage, state, id_to_item)

    def _refresh_final_tick_panel(self, stage: str) -> None:
        suffix = stage_suffix(stage)
        self._safe_refresh(
            f"ubs_final_tick{suffix}_reconcile",
            lambda: self._reconcile_final_tick_from_disk(stage),
        )
        for label, callback in (
            (f"ubs_final_tick{suffix}", lambda: self._refresh_final_tick_stage(stage)),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    # ------------------------------------------------------------------
    # Nombres por etapa que usa el resto de la aplicacion
    # ------------------------------------------------------------------
    def _refresh_ubs_final_tick(self) -> None:
        self._refresh_final_tick_stage("probe")

    def _refresh_ubs_final_tick_panel(self) -> None:
        self._refresh_final_tick_panel("probe")

    def _on_ubs_final_tick_tree_click(self, event) -> str | None:
        return self._on_final_tick_tree_click(event, "probe")

    def _checked_ubs_final_tick_infos(self, *, fallback_selected: bool = True) -> list[dict[str, str]]:
        return self._checked_final_tick_infos("probe", fallback_selected=fallback_selected)

    def _manual_accept_selected_ubs_final_tick(self) -> None:
        self._manual_mark_final_tick("probe", "accepted")

    def _manual_reject_selected_ubs_final_tick(self) -> None:
        self._manual_mark_final_tick("probe", "rejected")

    def _open_selected_ubs_final_tick_set(self) -> None:
        self._open_final_tick_path("probe", "set")

    def _open_selected_ubs_final_tick_ohlc_report(self) -> None:
        self._open_final_tick_path("probe", "ohlc_report")

    def _open_selected_ubs_final_tick_real_report(self) -> None:
        self._open_final_tick_path("probe", "real_report")
