from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import sys
from tkinter import filedialog, messagebox

from ubs.account import ACCOUNT_TYPES, account_memory_path
from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSearchLogicMixin:
    def _run_ubs_search(self) -> None:
        if not hasattr(self, "ubs_search_tree"):
            return
        query = self.ubs_search_query.get().strip()
        if not query:
            self.ubs_search_status.set("Escribe parte del nombre del set.")
            return

        for item in self.ubs_search_tree.get_children():
            self.ubs_search_tree.delete(item)
        self.ubs_search_paths = {}

        rows: list[dict[str, object]] = []
        errors: list[str] = []
        for account_type in ACCOUNT_TYPES:
            memory_path = account_memory_path(BASE_DIR, account_type)
            if not memory_path.exists():
                continue
            try:
                rows.extend(self._ubs_search_rows(memory_path, account_type, query))
            except sqlite3.Error as exc:
                errors.append(f"{account_type}: {exc}")

        rows.sort(
            key=lambda row: (
                str(row.get("set_name") or "").casefold(),
                str(row.get("account_type") or ""),
                int(row.get("candidate_id") or 0),
            )
        )

        for index, row in enumerate(rows):
            tag = self._ubs_search_row_tag(row)
            item = self.ubs_search_tree.insert(
                "",
                "end",
                iid=f"{row['account_type']}:{row['candidate_id']}:{index}",
                values=(
                    row.get("account_type", ""),
                    row.get("candidate_id", ""),
                    row.get("status", ""),
                    row.get("robust_status", ""),
                    row.get("final_tick_status", ""),
                    row.get("target_symbol", "") or row.get("symbol", ""),
                    row.get("period", ""),
                    self._ubs_search_score(row.get("score")),
                    row.get("set_name", ""),
                    row.get("run_id", ""),
                ),
                tags=(tag,),
            )
            self.ubs_search_paths[item] = {
                "set": str(row.get("set_path") or ""),
                "base_report": str(row.get("report_path") or ""),
                "robust_report": str(row.get("robust_report_path") or ""),
                "ohlc_report": str(row.get("ohlc_report_path") or ""),
                "tick_report": str(row.get("real_tick_report_path") or ""),
            }

        message = f"{len(rows)} resultado(s) para: {query}"
        if errors:
            message += " | Errores: " + " ; ".join(errors)
        self.ubs_search_status.set(message)

    def _ubs_search_rows(self, memory_path: Path, account_type: str, query: str) -> list[dict[str, object]]:
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            pattern = f"%{query.casefold()}%"
            rows = conn.execute(
                """
                select ? as account_type,
                       c.id as candidate_id,
                       c.run_id,
                       c.status,
                       c.score,
                       c.set_path,
                       c.report_path,
                       c.symbol,
                       c.target_symbol,
                       c.period,
                       cr.status as robust_status,
                       cr.report_path as robust_report_path,
                       ft.status as final_tick_status,
                       ft.ohlc_report_path,
                       ft.real_tick_report_path
                from candidates c
                left join candidate_robustness cr on cr.candidate_id = c.id
                left join candidate_final_tick ft on ft.candidate_id = c.id
                where lower(c.set_path) like ?
                   or lower(coalesce(c.seed_path, '')) like ?
                   or lower(coalesce(c.report_path, '')) like ?
                order by c.id desc
                limit 1000
                """,
                (account_type, pattern, pattern, pattern),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["set_name"] = Path(str(item.get("set_path") or "")).name
            out.append(item)
        return out

    def _ubs_search_score(self, value: object) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return ""

    def _ubs_search_row_tag(self, row: dict[str, object]) -> str:
        final_status = str(row.get("final_tick_status") or "").strip().lower()
        robust_status = str(row.get("robust_status") or "").strip().lower()
        base_status = str(row.get("status") or "").strip().lower()
        if final_status == "accepted" or (not final_status and robust_status == "accepted") or (
            not final_status and not robust_status and base_status == "accepted"
        ):
            return "accepted"
        if final_status == "rejected" or robust_status == "rejected" or base_status == "rejected":
            return "rejected"
        return "pending"

    def _selected_ubs_search_items(self) -> list[str]:
        if not hasattr(self, "ubs_search_tree"):
            return []
        return list(self.ubs_search_tree.selection())

    def _selected_ubs_search_paths(self) -> dict[str, str] | None:
        selection = self._selected_ubs_search_items()
        if not selection:
            messagebox.showinfo("Buscador UBS", "Selecciona un set primero.")
            return None
        return getattr(self, "ubs_search_paths", {}).get(selection[0], {})

    def _open_selected_ubs_search_set(self) -> None:
        paths = self._selected_ubs_search_paths()
        if not paths:
            return
        set_path = paths.get("set")
        if not set_path:
            messagebox.showinfo("Buscador UBS", "La fila seleccionada no tiene set asociado.")
            return
        self._open_local_file(Path(set_path))

    def _open_selected_ubs_search_report(self) -> None:
        paths = self._selected_ubs_search_paths()
        if not paths:
            return
        for key in ("tick_report", "ohlc_report", "robust_report", "base_report"):
            value = paths.get(key)
            if value and Path(value).exists():
                self._open_local_file(Path(value))
                return
        messagebox.showinfo("Buscador UBS", "La fila seleccionada no tiene reporte disponible.")

    def _export_selected_ubs_search_set(self) -> None:
        items = self._selected_ubs_search_items()
        if not items:
            messagebox.showinfo("Exportar set", "Selecciona uno o mas sets primero.")
            return
        folder = filedialog.askdirectory(title="Carpeta destino para exportar sets")
        if not folder:
            return
        dest = Path(folder)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Exportar set", f"No pude crear la carpeta:\n{exc}")
            return

        exported = 0
        missing: list[str] = []
        paths_by_item = getattr(self, "ubs_search_paths", {})
        for item in items:
            set_path = Path(str(paths_by_item.get(item, {}).get("set") or ""))
            if not set_path.is_file():
                missing.append(set_path.name or item)
                continue
            out_path = dest / set_path.name
            try:
                if set_path.resolve() != out_path.resolve():
                    shutil.copy2(set_path, out_path)
                exported += 1
            except OSError:
                missing.append(set_path.name)

        status = f"Exportados {exported} set(s) a {dest}"
        if missing:
            status += f" | omitidos: {len(missing)}"
        self.ubs_search_status.set(status)
        message = status
        if missing:
            message += "\n\nOmitidos:\n" + "\n".join(missing[:20])
        messagebox.showinfo("Exportar set", message)
        if exported:
            self._open_local_file(dest)

    def _clear_ubs_search(self) -> None:
        if hasattr(self, "ubs_search_tree"):
            for item in self.ubs_search_tree.get_children():
                self.ubs_search_tree.delete(item)
        self.ubs_search_paths = {}
        self.ubs_search_query.set("")
        self.ubs_search_status.set("Sin busqueda.")
