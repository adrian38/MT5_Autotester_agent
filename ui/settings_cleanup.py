"""Limpieza de reportes, scripts e historico desde la pantalla de ajustes."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import messagebox

from run_tests import REPORT_DIR
from ubs.account import BROKER_ACCOUNT_TYPES, account_memory_path
from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REPORT_SUFFIXES = {".htm", ".html", ".xml", ".png", ".gif", ".set"}


class SettingsCleanupMixin:
    """Borrado de reportes, datos historicos y ejecucion de scripts de limpieza."""

    def _delete_historical_data(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Proceso activo", "Hay un proceso en ejecucion. Detenlo antes de limpiar.")
            return
        scripts = self._find_clean_scripts()
        if not scripts:
            messagebox.showerror(
                "Scripts no encontrados",
                "No se encontraron cleanOldTest.ps1 / cleanOlddata.ps1 en la carpeta scripts/."
            )
            return
        if not messagebox.askyesno(
            "Eliminar datos historicos",
            "Esto cerrara MetaTrader y borrara cache de tester/bases/history y reportes "
            "en las carpetas de datos de TODAS las terminales.\n\n"
            f"No borrara las copias locales del proyecto en:\n{REPORT_DIR}\n\n"
            f"Se ejecutaran en orden:\n  - {scripts[0].name}\n  - {scripts[1].name}\n\nContinuar?"
        ):
            return
        self.status_text.set("Limpiando datos historicos...")
        self._append_console("\n=== Limpieza de datos historicos ===\n", tag="warn")
        # Inicializa la barra de progreso para esta tarea
        self.active_task_text.set("Limpiando datos historicos")
        self.active_task_detail.set("0%")
        self._set_progress_color("accent")
        self._progress_running = True
        self._progress_total = len(scripts)
        self._progress_done = 0
        self._progress_target = 2.0
        try:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate", maximum=100)
            self.progress_var.set(0.0)
        except Exception:
            pass
        threading.Thread(target=self._run_clean_scripts, args=(scripts,), daemon=True).start()

    def _delete_old_reports(self) -> None:
        files = self._project_report_files()
        if not files:
            messagebox.showinfo("Sin reportes", "No hay reportes generados para borrar.")
            return
        protected = self._protected_ubs_report_files()
        delete_files = [path for path in files if self._norm_report_path(path) not in protected]
        skipped = len(files) - len(delete_files)
        if not messagebox.askyesno(
            "Borrar reportes antiguos",
            f"Se borraran {len(delete_files)} archivo(s) de reportes de la carpeta {REPORT_DIR}.\n"
            f"Se conservaran {skipped} reporte(s) protegidos por UBS.\n\nContinuar?"
        ):
            return

        deleted = 0
        failures: list[str] = []
        for path in delete_files:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")

        self._refresh_reports()
        self.status_text.set(f"Reportes borrados: {deleted} | protegidos: {skipped}")
        self._append_console(f"\nReportes borrados: {deleted} | protegidos UBS: {skipped}\n", tag="warn")
        if failures:
            details = "\n".join(failures[:12])
            self._show_error("No se pudieron borrar todos los reportes", details)
        else:
            messagebox.showinfo(
                "Reportes borrados",
                f"Se borraron {deleted} reporte(s).\nSe conservaron {skipped} reporte(s) protegidos por UBS.",
            )

    def _project_report_files(self) -> list[Path]:
        if not REPORT_DIR.exists():
            return []
        return [
            path for path in REPORT_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in REPORT_SUFFIXES
        ]

    def _delete_project_reports_for_clean(self) -> tuple[int, int, list[str]]:
        deleted = 0
        skipped = 0
        failures: list[str] = []
        protected = self._protected_ubs_report_files()
        for path in self._project_report_files():
            if self._norm_report_path(path) in protected:
                skipped += 1
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
        return deleted, skipped, failures

    def _protected_ubs_report_files(self) -> set[str]:
        protected: set[str] = set()
        for broker, account_type in BROKER_ACCOUNT_TYPES:
            memory_path = account_memory_path(BASE_DIR, account_type, broker)
            if not memory_path.exists():
                continue
            try:
                conn = connect_memory(memory_path)
                conn.row_factory = sqlite3.Row
                try:
                    self._ensure_ubs_memory_schema(conn)
                    rows = conn.execute(
                        """
                        select c.report_path as base_report,
                               cr.report_path as robust_report,
                               ft.ohlc_report_path as final_ohlc_report,
                               ft.real_tick_report_path as final_tick_report,
                               ft6.ohlc_report_path as final_ohlc_6m_report,
                               ft6.real_tick_report_path as final_tick_6m_report,
                               rg.report_path as regression_report
                        from candidates c
                        left join candidate_robustness cr on cr.candidate_id = c.id
                        left join candidate_final_tick ft on ft.candidate_id = c.id
                        left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
                        left join candidate_regression rg on rg.candidate_id = c.id
                        where c.status = 'accepted'
                           or cr.status = 'accepted'
                           or ft.status = 'accepted'
                           or ft6.status = 'accepted'
                           or rg.status in ('accepted', 'rejected', 'no_trades')
                        """
                    ).fetchall()
                except sqlite3.Error:
                    rows = []
                finally:
                    conn.close()
            except Exception:
                continue
            for row in rows:
                for key in (
                    "base_report",
                    "robust_report",
                    "final_ohlc_report",
                    "final_tick_report",
                    "final_ohlc_6m_report",
                    "final_tick_6m_report",
                    "regression_report",
                ):
                    value = str(row[key] or "").strip()
                    if not value:
                        continue
                    report_path = Path(value)
                    protected.add(self._norm_report_path(report_path))
                    for sibling in report_path.parent.glob(f"{report_path.stem}.*"):
                        if sibling.suffix.lower() in REPORT_SUFFIXES:
                            protected.add(self._norm_report_path(sibling))
        return protected

    def _norm_report_path(self, path: Path) -> str:
        try:
            return str(path.resolve()).casefold()
        except OSError:
            return str(path).casefold()

    def _find_clean_scripts(self) -> list[Path]:
        candidates_dirs = [BASE_DIR / "scripts", BASE_DIR]
        if getattr(sys, "_MEIPASS", None):
            candidates_dirs.insert(0, Path(sys._MEIPASS) / "scripts")
        order = ("cleanOldTest.ps1", "cleanOlddata.ps1")
        for d in candidates_dirs:
            paths = [d / name for name in order]
            if all(p.exists() for p in paths):
                return paths
        return []

    def _historical_data_roots(self) -> list[Path]:
        roots: list[Path] = []

        def add_root(value: object) -> None:
            text = str(value or "").strip()
            if not text:
                return
            root = Path(text).expanduser().resolve(strict=False)
            if root not in roots:
                roots.append(root)

        if hasattr(self, "mt5_data_root"):
            add_root(self.mt5_data_root.get())
        for profile in getattr(self, "multiterminal_profiles", []):
            add_root(profile.get("data_dir"))

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            global_tester = Path(appdata) / "MetaQuotes" / "Tester"
            if global_tester.is_dir():
                add_root(global_tester)
            terminal_base = Path(appdata) / "MetaQuotes" / "Terminal"
            if terminal_base.is_dir():
                for child in terminal_base.iterdir():
                    if child.is_dir():
                        add_root(child)
        return roots

    def _historical_data_leftovers(self) -> list[tuple[Path, int, int]]:
        leftovers: list[tuple[Path, int, int]] = []
        for root in self._historical_data_roots():
            subdirs = (".",) if root.name.casefold() == "tester" else ("Tester", "tester", "bases", "history")
            for subdir in subdirs:
                path = root / subdir
                if not path.exists() or not path.is_dir():
                    continue
                file_count = 0
                total_bytes = 0
                try:
                    for item in path.rglob("*"):
                        if not item.is_file():
                            continue
                        file_count += 1
                        try:
                            total_bytes += item.stat().st_size
                        except OSError:
                            pass
                except OSError:
                    file_count += 1
                if file_count > 0 or total_bytes > 0:
                    leftovers.append((path, file_count, total_bytes))
        unique: dict[str, tuple[Path, int, int]] = {}
        for path, file_count, total_bytes in leftovers:
            unique[str(path).casefold()] = (path, file_count, total_bytes)
        return sorted(unique.values(), key=lambda item: item[2], reverse=True)

    def _run_clean_scripts(self, scripts: list[Path]) -> None:
        total = max(1, len(scripts))
        failures = 0
        for index, script in enumerate(scripts):
            self.output_queue.put(f"\n>>> Ejecutando {script.name}\n")
            slot_start = 100.0 * index / total
            self.after(0, lambda v=slot_start + 100.0 / total * 0.15: self._set_clean_progress(v))
            try:
                proc = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                    creationflags=NO_WINDOW,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.output_queue.put(line)
                proc.wait()
                if proc.returncode != 0:
                    failures += 1
                self.output_queue.put(f"\n>>> {script.name} termino con codigo {proc.returncode}\n")
            except Exception as exc:
                failures += 1
                self.output_queue.put(f"\nERROR ejecutando {script.name}: {exc}\n")
            slot_end = 100.0 * (index + 1) / total
            self.after(0, lambda v=slot_end: self._set_clean_progress(v))
        leftovers = self._historical_data_leftovers()
        if leftovers:
            failures += 1
            self.output_queue.put("\nAVISO: quedan datos historicos despues de limpiar:\n")
            for path, file_count, total_bytes in leftovers[:20]:
                self.output_queue.put(
                    f" - {path} | {file_count:,} archivos | {total_bytes / (1024 ** 3):.2f} GB\n"
                )
            if len(leftovers) > 20:
                self.output_queue.put(f" - ... {len(leftovers) - 20} carpeta(s) mas\n")
        self.after(0, lambda: self._set_clean_progress(100.0))
        self.output_queue.put("\n=== Limpieza terminada ===\n")
        self.after(0, self._finish_clean, failures)

    def _set_clean_progress(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        self._progress_target = value
        try:
            self.progress_var.set(value)
        except Exception:
            pass
        self.active_task_detail.set(f"{int(round(value))}%")

    def _finish_clean(self, failures: int) -> None:
        self._progress_running = False
        if failures:
            self._set_progress_color("danger")
            self.active_task_text.set("Limpieza con errores")
            self.status_text.set(f"Limpieza terminada con {failures} problema(s)")
            messagebox.showwarning(
                "Limpieza con errores",
                f"La limpieza termino con {failures} problema(s).\n"
                "Revisa la consola en la pestana Logs para ver scripts fallidos o carpetas restantes."
            )
        else:
            self._set_progress_color("accent")
            try:
                self.progress_var.set(100.0)
            except Exception:
                pass
            self.active_task_text.set("Limpieza completada")
            self.active_task_detail.set("100%")
            self.status_text.set("Limpieza terminada correctamente")
            messagebox.showinfo(
                "Limpieza completada",
                "Se eliminaron los datos historicos de MT5 correctamente "
                "(tester, bases, history, reports de terminal, .fxt, .tick)."
            )
        self._refresh_all()
