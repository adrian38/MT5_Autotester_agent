from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_seed_scores
from ubs.path_utils import resolve_workspace_path, workspace_path_exists


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSeedsTableMixin:
    """Tabla de semillas, marcado manual, apertura y reintento."""

    def _ubs_seed_table_memory(self):
        """Filas, overrides y semillas inactivas guardadas para la tabla."""
        score_rows: dict[str, sqlite3.Row] = {}
        overrides: dict[str, tuple[str, str]] = {}
        inactive_rows: list[sqlite3.Row] = []
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return score_rows, overrides, inactive_rows
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_seed_override_schema(conn)
            if self._sqlite_table_exists(conn, "seed_scores"):
                rows = conn.execute("select * from seed_scores").fetchall()
                # SQLite may have been copied from another checkout.  Match
                # relocated workspace paths to the physical seed files so
                # evaluated seeds do not appear pending merely because the
                # repository root changed.
                score_rows = {
                    str(resolve_workspace_path(row["seed_path"])): row
                    for row in rows
                }
                inactive_rows = [row for row in rows if not int(row["active"] or 0)]
            for row in conn.execute("select seed_path, symbol, period from seed_overrides").fetchall():
                overrides[str(resolve_workspace_path(row["seed_path"]))] = (
                    str(row["symbol"] or "").strip().upper(),
                    str(row["period"] or "").strip().upper(),
                )
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_seed_detail.set(f"Error SQLite semillas: {exc}")
        return score_rows, overrides, inactive_rows

    def _insert_obsolete_ubs_seed_rows(self, tree, inactive_rows, overrides, current_paths, current_checked) -> None:
        """Semillas que ya no estan en disco pero conservan veredicto."""
        for row in inactive_rows:
            path_text = str(row["seed_path"] or "")
            if not path_text or str(resolve_workspace_path(path_text)) in current_paths:
                continue
            status = str(row["status"] or "obsoleta")
            override_symbol, override_period = overrides.get(path_text, ("", ""))
            symbol = override_symbol or str(row["symbol"] or "").strip().upper()
            period = override_period or str(row["period"] or "").strip().upper()
            reason = self._ubs_seed_reason(row, status)
            item = tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(path_text in current_checked),
                    "obsoleta",
                    symbol,
                    period,
                    self._format_ubs_number(row["score"]),
                    "",
                    "si" if override_symbol or override_period else "no",
                    reason,
                    Path(path_text).name,
                ),
                tags=("pending",),
            )
            self.ubs_seed_paths[item] = {"seed_path": path_text, "active": "0", "status": status}

    def _insert_ubs_seed_row(self, tree, path, score_rows, overrides, current_checked) -> str:
        """Una fila de la tabla de semillas con su veredicto y motivo."""
        path_text = str(path)
        row = score_rows.get(path_text)
        stored_path_text = str(row["seed_path"]) if row else path_text
        inferred_symbol, inferred_period = self._inferred_ubs_seed_fields(path)
        override_symbol, override_period = overrides.get(path_text, ("", ""))
        symbol = override_symbol or (str(row["symbol"] or "").strip().upper() if row else inferred_symbol)
        period = override_period or (str(row["period"] or "").strip().upper() if row else inferred_period)
        status = str(row["status"] or "pending") if row else "pending"
        display_status = status
        if (not symbol or not period or symbol == "UNKNOWN" or period == "UNKNOWN") and not override_symbol:
            display_status = "invalid_seed"
        accepted = ""
        if row and row["accepted"] is not None:
            accepted = "si" if int(row["accepted"]) else "no"
        reason = self._ubs_seed_reason(row, display_status)
        item = tree.insert(
            "",
            "end",
            values=(
                self._checkbox_text(stored_path_text in current_checked),
                self._format_ubs_status(display_status),
                symbol,
                period,
                self._format_ubs_number(row["score"] if row else None),
                accepted,
                "si" if override_symbol or override_period else "no",
                reason,
                path.name,
            ),
            tags=(self._ubs_result_tag(display_status),),
        )
        self.ubs_seed_paths[item] = {"seed_path": stored_path_text, "active": "1", "status": display_status, "has_row": "1" if row else "0"}
        return item

    def _refresh_ubs_seeds(self) -> None:
        if not hasattr(self, "ubs_seeds_tree"):
            return
        tree = self.ubs_seeds_tree
        tree.delete(*tree.get_children(""))
        self.ubs_seed_paths.clear()
        current_checked = set(self.ubs_seed_checked)

        try:
            seed_files = self._current_ubs_seed_files()
        except Exception as exc:
            self.ubs_seed_detail.set(f"Carpeta de seeds no valida: {exc}")
            return

        score_rows, overrides, inactive_rows = self._ubs_seed_table_memory()

        current_paths = {str(path) for path in seed_files}
        first_item = ""
        for path in seed_files:
            item = self._insert_ubs_seed_row(tree, path, score_rows, overrides, current_checked)
            if not first_item:
                first_item = item

        self._insert_obsolete_ubs_seed_rows(tree, inactive_rows, overrides, current_paths, current_checked)

        valid_paths = {info["seed_path"] for info in self.ubs_seed_paths.values() if info.get("seed_path")}
        self.ubs_seed_checked.intersection_update(valid_paths)

        if first_item:
            tree.selection_set(first_item)
            tree.focus(first_item)
            self._on_ubs_seed_select()
        else:
            self.ubs_seed_detail.set("No hay semillas .set en la carpeta UBS")
            self.ubs_seed_override_symbol.set("")
            self.ubs_seed_override_period.set("")

    def _selected_ubs_seed_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_seeds_tree"):
            return {}
        selected = self.ubs_seeds_tree.selection()
        if not selected:
            return {}
        return self.ubs_seed_paths.get(selected[0], {})

    def _checked_ubs_seed_infos(self, *, fallback_selected: bool = True) -> list[dict[str, str]]:
        infos = [
            info for info in self.ubs_seed_paths.values()
            if info.get("seed_path") in self.ubs_seed_checked
        ]
        if infos or not fallback_selected:
            return infos
        selected = self._selected_ubs_seed_info()
        return [selected] if selected else []

    def _manual_mark_selected_ubs_seeds(self, status: str) -> None:
        infos = self._checked_ubs_seed_infos()
        seed_paths = [info.get("seed_path", "") for info in infos if info.get("active") == "1"]
        if not seed_paths:
            messagebox.showinfo("Estado manual", "Selecciona una o mas seeds activas primero.")
            return
        label = "aceptada" if status == "accepted" else "rechazada"
        if not messagebox.askyesno(
            "Estado manual",
            f"Marcar {len(seed_paths)} seed(s) como {label} manual?\n\n"
            "Si la seed no tiene score, se guarda el estado pero no aporta al peso.",
        ):
            return
        try:
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_seed_override_schema(conn)
            now = datetime.now().isoformat(timespec="seconds")
            for seed_path in seed_paths:
                if conn.execute("select 1 from seed_scores where seed_path=?", (seed_path,)).fetchone():
                    continue
                path = resolve_workspace_path(seed_path)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                symbol, period = self._inferred_ubs_seed_fields(path)
                conn.execute(
                    """
                    insert into seed_scores (
                        seed_path, seed_mtime, seed_size, symbol, period, family, run_strategy,
                        status, active, last_seen
                    ) values (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?)
                    """,
                    (
                        seed_path,
                        float(stat.st_mtime),
                        int(stat.st_size),
                        symbol,
                        period,
                        path.parent.name or "manual",
                        "manual",
                        now,
                    ),
                )
            updated = mark_seed_scores(conn, seed_paths, status)
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("No se pudo aplicar estado manual", str(exc))
            return
        self.ubs_seed_checked.clear()
        self.ubs_weights_locked.set(False)
        self.status_text.set(f"Estado manual aplicado a {updated} seed(s)")
        self._refresh_ubs_seeds_panel()

    def _manual_accept_selected_ubs_seeds(self) -> None:
        self._manual_mark_selected_ubs_seeds("accepted")

    def _manual_reject_selected_ubs_seeds(self) -> None:
        self._manual_mark_selected_ubs_seeds("rejected")

    def _on_ubs_seed_tree_click(self, event: tk.Event) -> None:
        item, column = self._tree_item_from_event(self.ubs_seeds_tree, event)
        if not item or column != "#1":
            return
        info = self.ubs_seed_paths.get(item, {})
        seed_path = info.get("seed_path", "")
        if not seed_path:
            return
        if seed_path in self.ubs_seed_checked:
            self.ubs_seed_checked.remove(seed_path)
        else:
            self.ubs_seed_checked.add(seed_path)
        values = list(self.ubs_seeds_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(seed_path in self.ubs_seed_checked)
            self.ubs_seeds_tree.item(item, values=values)
        return "break"

    def _on_ubs_seed_select(self) -> None:
        info = self._selected_ubs_seed_info()
        if not info:
            return
        item = self.ubs_seeds_tree.selection()[0]
        values = self.ubs_seeds_tree.item(item, "values")
        seed_path = info.get("seed_path", "")
        symbol = str(values[2] if len(values) > 2 else "").strip().upper()
        period = str(values[3] if len(values) > 3 else "").strip().upper()
        self.ubs_seed_override_symbol.set("" if symbol == "UNKNOWN" else symbol)
        self.ubs_seed_override_period.set("" if period == "UNKNOWN" else period)
        self.ubs_seed_detail.set(f"{Path(seed_path).name} | estado: {values[1] if len(values) > 1 else '-'}")

    def _open_selected_ubs_seed(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            self._show_error("Sin seleccion", "Selecciona una semilla.")
            return
        for info in infos:
            seed_path = info.get("seed_path", "")
            if seed_path:
                self._open_local_file(resolve_workspace_path(seed_path))

    def _open_selected_ubs_seed_report(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            return
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Semillas UBS", "Sin memoria UBS. Evalua las semillas primero.")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            rows = []
            for info in infos:
                seed_path = info.get("seed_path", "")
                if seed_path:
                    row = conn.execute("select report_path from seed_scores where seed_path=?", (seed_path,)).fetchone()
                    if row and row["report_path"]:
                        rows.append(row)
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error SQLite", str(exc))
            return
        if not rows:
            messagebox.showinfo("Semillas UBS", "Esta semilla no tiene reporte asociado.\nEjecuta 'Evaluar semillas' primero.")
            return
        for row in rows:
            self._open_local_file(resolve_workspace_path(str(row["report_path"])))

    def _retry_selected_ubs_seed(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            messagebox.showinfo("Semillas UBS", "Selecciona una semilla primero.")
            return
        active_infos = [info for info in infos if info.get("active") != "0" and workspace_path_exists(info.get("seed_path", ""))]
        if not active_infos:
            messagebox.showinfo("Semillas UBS", "No hay seeds activas/existentes entre las marcadas.")
            return
        paths = [resolve_workspace_path(info["seed_path"]) for info in active_infos]
        try:
            output_dir = self._ubs_generation_output_dir()
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--broker", self._ubs_broker(),
                "--account-type", self._ubs_account_type(),
                "--output-dir", str(output_dir),
                "--template", self.template_path.get(),
                "--delay", str(self.delay.get()),
            ]
            for path in paths:
                args.extend(["--retry-seed-path", str(path)])
            args.extend(self._ubs_seed_score_args())
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            symbol_map = self._effective_ubs_symbol_map_text()
            if symbol_map:
                args.extend(["--symbol-map", symbol_map])
            args.extend(self._effective_symbol_suffix_args())
        except Exception as exc:
            self._show_error("No se pudo preparar retry seed", str(exc))
            return

        selected_items = self.ubs_seeds_tree.selection() if hasattr(self, "ubs_seeds_tree") else ()
        values = self.ubs_seeds_tree.item(selected_items[0], "values") if selected_items else ()
        details = [
            "Accion: Repetir backtest seed UBS",
            f"Seeds: {len(paths)}",
            f"Primera: {paths[0].name}",
            f"Estado actual: {values[1] if len(values) > 1 else active_infos[0].get('status', '-')}",
            f"Backtests previstos: {len(paths)}",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar retry seed", len(paths), details):
            self.ubs_seed_checked.clear()
            self._safe_refresh("ubs_seeds", self._refresh_ubs_seeds)
            self._run_script("ubs_agent.py", args)
