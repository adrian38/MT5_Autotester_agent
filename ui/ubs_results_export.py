from __future__ import annotations

import json
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


@dataclass
class _ExportJob:
    run: object
    rows: list
    run_folder: Path
    directories: tuple[Path, Path, Path]
    counts: dict[str, int]
    messages: queue.Queue


def _export_folder_name(cid: int, set_str: str, symbol: str, period: str) -> str:
    if set_str and Path(set_str).stem:
        return Path(set_str).stem
    parts = [part for part in (symbol, period) if part and part.upper() != "UNKNOWN"]
    return "_".join(parts + [str(cid)]) if parts else str(cid)


class UBSResultsExportMixin:
    """Exportacion de un run y borrado del historico."""

    def _report_related_files(rep_str: str) -> list[Path]:
        """Return the .htm/.html report + all associated image files."""
        if not rep_str:
            return []
        rep = resolve_workspace_path(rep_str)
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

    def _read_ubs_export_rows(self, memory_path: Path):
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute(
                "select * from runs where hidden=0 order by id desc limit 1"
            ).fetchone()
            rows = [] if run is None else conn.execute(
                """
                select id, status, set_path, report_path, metrics_json,
                       target_symbol, period
                from candidates where run_id = ?
                """,
                (run["id"],),
            ).fetchall()
            return run, rows
        finally:
            conn.close()

    @staticmethod
    def _prepare_ubs_export_job(run, rows, base_dir: str) -> _ExportJob:
        created    = str(run["created_at"] or "").replace(":", "-").replace(" ", "_")[:16]
        run_folder = Path(base_dir) / f"Run_{run['id']}_{created}"
        accept_dir = run_folder / "aceptados"
        netpos_dir = run_folder / "fallidos" / "net_profit_positivo"
        otros_dir  = run_folder / "fallidos" / "otros"
        for d in (accept_dir, netpos_dir, otros_dir):
            d.mkdir(parents=True, exist_ok=True)
        return _ExportJob(
            run, list(rows), run_folder, (accept_dir, netpos_dir, otros_dir),
            {"aceptados": 0, "net_profit_positivo": 0, "otros": 0, "sin_archivo": 0},
            queue.Queue(),
        )

    def _copy_ubs_export_candidate(
        self, destination: Path, cid: int, set_path: str, report_path: str,
        symbol: str, period: str,
    ) -> bool:
        folder = destination / _export_folder_name(cid, set_path, symbol, period)
        folder.mkdir(parents=True, exist_ok=True)
        copied = False
        if set_path:
            source = resolve_workspace_path(set_path)
            if source.exists():
                shutil.copy2(source, folder / source.name)
                copied = True
        for related in self._report_related_files(report_path):
            shutil.copy2(related, folder / related.name)
            copied = True
        return copied

    def _export_ubs_candidate(self, job: _ExportJob, row) -> None:
        status = str(row["status"] or "")
        cid = int(row["id"] or 0)
        set_path = str(row["set_path"] or "")
        report_path = str(row["report_path"] or "")
        symbol = str(row["target_symbol"] or "")
        period = str(row["period"] or "")
        accepted_dir, netpos_dir, other_dir = job.directories
        category, destination = "otros", other_dir
        if status == "accepted":
            category, destination = "aceptados", accepted_dir
        elif status == "rejected":
            try:
                net_profit = float(json.loads(row["metrics_json"] or "{}").get("net_profit") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                net_profit = 0.0
            if net_profit > 0:
                category, destination = "net_profit_positivo", netpos_dir
        copied = self._copy_ubs_export_candidate(
            destination, cid, set_path,
            report_path if status in {"accepted", "rejected"} else "",
            symbol, period,
        )
        job.counts[category if copied else "sin_archivo"] += 1

    def _run_ubs_export(self, job: _ExportJob) -> None:
        total = len(job.rows)
        for index, row in enumerate(job.rows):
            set_path = str(row["set_path"] or "")
            label = Path(set_path).stem if set_path else f"#{int(row['id'] or 0)}"
            job.messages.put(("progress", index, total, label))
            self._export_ubs_candidate(job, row)
        job.messages.put(("done",))

    def _build_ubs_export_dialog(self, job: _ExportJob):
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
        tk.Label(body, text=f"Run #{job.run['id']}  ·  {job.run['created_at']}",
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 16))

        bar = ttk.Progressbar(body, mode="determinate", maximum=100,
                              style="Horizontal.TProgressbar", length=440)
        bar.pack(fill="x")

        count_var  = tk.StringVar(value=f"0 / {len(job.rows)}")
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

        return dlg, bar, count_var, status_var

    def _finish_ubs_export(self, dialog, job: _ExportJob) -> None:
        dialog.grab_release()
        dialog.destroy()
        counts = job.counts
        summary = (
            f"Exportado en:\n{job.run_folder}\n\n"
            f"  aceptados/                   {counts['aceptados']}\n"
            f"  fallidos/net_profit_positivo  {counts['net_profit_positivo']}\n"
            f"  fallidos/otros               {counts['otros']}"
        )
        if counts["sin_archivo"]:
            summary += f"\n\n  Sin archivos disponibles:    {counts['sin_archivo']}"
        messagebox.showinfo("Exportar run — completado", summary)
        try:
            subprocess.Popen(["explorer", str(job.run_folder)])
        except Exception:
            pass

    def _poll_ubs_export(self, dialog, widgets, job: _ExportJob) -> None:
        bar, count_var, status_var = widgets
        try:
            while True:
                message = job.messages.get_nowait()
                if message[0] == "progress":
                    _, index, total, label = message
                    bar["value"] = int((index + 1) / max(total, 1) * 100)
                    count_var.set(f"{index + 1} / {total}")
                    name = label[:55] + "..." if len(label) > 55 else label
                    status_var.set(f"Copiando: {name}")
                elif message[0] == "done":
                    bar["value"] = 100
                    status_var.set("Completado.")
                    dialog.after(400, lambda: self._finish_ubs_export(dialog, job))
                    return
        except queue.Empty:
            pass
        dialog.after(40, lambda: self._poll_ubs_export(dialog, widgets, job))

    def _export_ubs_results_run(self) -> None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Exportar run", "No existe memoria UBS.")
            return
        base_dir = filedialog.askdirectory(title="Carpeta destino para la exportación")
        if not base_dir:
            return
        try:
            run, rows = self._read_ubs_export_rows(memory_path)
        except sqlite3.Error as exc:
            self._show_error("Error al leer memoria UBS", str(exc))
            return
        if run is None:
            messagebox.showinfo("Exportar run", "No hay ningún run visible.")
            return
        job = self._prepare_ubs_export_job(run, rows, base_dir)
        dialog, bar, count_var, status_var = self._build_ubs_export_dialog(job)
        threading.Thread(target=self._run_ubs_export, args=(job,), daemon=True).start()
        dialog.after(
            40, lambda: self._poll_ubs_export(
                dialog, (bar, count_var, status_var), job,
            ),
        )

    # ── History: eliminar run completo ────────────────────────────────────

    def _selected_ubs_history_delete_run(self) -> int | None:
        if not hasattr(self, "ubs_history_runs_tree"):
            return None
        selected = self.ubs_history_runs_tree.selection()
        if not selected:
            messagebox.showinfo("Eliminar run", "Selecciona un run primero.")
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def _read_ubs_history_delete_run(self, memory_path: Path, run_id: int):
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs where id=?", (run_id,)).fetchone()
            if run is None:
                return None, []
            rows = conn.execute(
                "select set_path, report_path from candidates where run_id=?",
                (run_id,),
            ).fetchall()
            return run, rows
        finally:
            conn.close()

    @staticmethod
    def _confirm_ubs_history_delete(run_id: int, created: str, total: int) -> bool:
        return messagebox.askyesno(
            "Eliminar run completo",
            f"Run #{run_id}  —  {created}\n\n"
            f"Esto eliminará:\n"
            f"  • {total} candidatos de la DB y el run\n"
            f"  • Sus archivos .set del disco\n"
            f"  • Sus reportes (.htm + imágenes)\n"
            f"  • Scores de evaluación de seeds (pesos → 0)\n\n"
            "¿Continuar?",
        )

    def _delete_ubs_history_files(self, rows) -> int:
        deleted_files = 0
        for row in rows:
            for f in self._report_related_files(str(row["report_path"] or "")):
                try:
                    f.unlink(missing_ok=True)
                    deleted_files += 1
                except OSError:
                    pass
            sp = resolve_workspace_path(str(row["set_path"] or ""))
            if sp.suffix.lower() == ".set" and sp.exists():
                try:
                    sp.unlink()
                    deleted_files += 1
                except OSError:
                    pass
        return deleted_files

    @staticmethod
    def _delete_ubs_history_rows(memory_path: Path, run_id: int) -> None:
        conn = connect_memory(memory_path)
        try:
            conn.execute("delete from candidate_regression where run_id=?", (run_id,))
            conn.execute("delete from candidate_final_tick_6m where run_id=?", (run_id,))
            conn.execute("delete from candidate_final_tick where run_id=?", (run_id,))
            conn.execute("delete from candidate_robustness where run_id=?", (run_id,))
            conn.execute("delete from candidates where run_id=?", (run_id,))
            conn.execute("delete from runs where id=?", (run_id,))
            # Limpiar también los scores de seed_scores → los pesos del Universo van a 0
            conn.execute(
                "update seed_scores set score=null, accepted=null "
                "where score is not null"
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_ubs_history_run(self) -> None:
        run_id = self._selected_ubs_history_delete_run()
        memory_path = self._ubs_memory_path()
        if run_id is None or not memory_path.exists():
            return
        try:
            run, rows = self._read_ubs_history_delete_run(memory_path, run_id)
        except sqlite3.Error as exc:
            self._show_error("Error al leer run", str(exc))
            return
        if run is None or not self._confirm_ubs_history_delete(
            run_id, str(run["created_at"]), len(rows),
        ):
            return
        deleted_files = self._delete_ubs_history_files(rows)
        try:
            self._delete_ubs_history_rows(memory_path, run_id)
        except sqlite3.Error as exc:
            self._show_error("Error al borrar de SQLite", str(exc))
            return
        self.ubs_history_run_checked.discard(str(run_id))
        self.status_text.set(
            f"Run #{run_id} eliminado — {len(rows)} candidatos, {deleted_files} archivos"
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
            sp = resolve_workspace_path(str(info.get("set", "") or ""))
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
