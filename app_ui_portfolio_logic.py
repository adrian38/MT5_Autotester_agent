from __future__ import annotations

import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

from run_tests import REPORT_DIR

try:
    from portfolio_manager.generator import (
        find_report_files as portfolio_find_report_files,
        generate_dd_threshold_workbook,
        generate_drawdown_workbook,
        generate_portfolio_drawdown_workbook,
        generate_portfolio_valley_drawdown_workbook,
        generate_top_portfolio_valleys_workbook,
        generate_workbook as generate_portfolio_workbook,
    )
except Exception:
    portfolio_find_report_files = None
    generate_dd_threshold_workbook = None
    generate_drawdown_workbook = None
    generate_portfolio_drawdown_workbook = None
    generate_portfolio_valley_drawdown_workbook = None
    generate_top_portfolio_valleys_workbook = None
    generate_portfolio_workbook = None


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class PortfolioLogicMixin:
    def _browse_portfolio_input(self) -> None:
        path = filedialog.askdirectory(initialdir=self.portfolio_input.get().strip() or str(REPORT_DIR))
        if path:
            self.portfolio_input.set(path)
            output = Path(path) / "ALL_STRATEGIES.xlsx"
            if not self.portfolio_output.get().strip():
                self.portfolio_output.set(str(output))
            self._write_ui_settings()
            self._refresh_portfolio_count()

    def _browse_portfolio_output(self) -> None:
        current = (
            Path(self.portfolio_output.get()).expanduser()
            if self.portfolio_output.get().strip()
            else BASE_DIR / "outputs" / "ALL_STRATEGIES.xlsx"
        )
        path = filedialog.asksaveasfilename(
            initialdir=str(current.parent if current.parent.exists() else BASE_DIR),
            initialfile=current.name,
            defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"), ("Todos", "*.*")),
        )
        if path:
            self.portfolio_output.set(path)
            self._write_ui_settings()

    def _refresh_portfolio_count(self) -> None:
        if portfolio_find_report_files is None:
            self.portfolio_count.set("Portfolio Manager no disponible.")
            return
        try:
            input_dir = Path(self.portfolio_input.get()).expanduser()
            if not input_dir.exists() or not input_dir.is_dir():
                self.portfolio_count.set("La carpeta no existe.")
                return
            count = len(portfolio_find_report_files(input_dir))
            self.portfolio_count.set(f"Reports encontrados: {count}")
        except Exception as exc:
            self.portfolio_count.set(f"No se pudo leer la carpeta: {exc}")

    def _portfolio_output_path(self, filename: str) -> Path:
        output = Path(self.portfolio_output.get()).expanduser()
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        return output.with_name(filename)

    def _set_portfolio_running(self, running: bool) -> None:
        self.portfolio_running = running
        state = "disabled" if running else "normal"
        for button in self.portfolio_buttons:
            button.configure(state=state)
        if hasattr(self, "portfolio_progress"):
            if running:
                self.portfolio_progress.start(12)
            else:
                self.portfolio_progress.stop()

    def _run_portfolio_action(self, action: str) -> None:
        if self.portfolio_running:
            messagebox.showwarning("Portfolio en ejecucion", "Ya hay un proceso de Portfolio Manager en marcha.")
            return
        if portfolio_find_report_files is None:
            messagebox.showerror("Portfolio Manager no disponible", "No pude cargar el modulo local portfolio_manager.")
            return

        input_dir = Path(self.portfolio_input.get()).expanduser()
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("Carpeta invalida", f"No existe la carpeta de reportes:\n{input_dir}")
            return

        try:
            count = len(portfolio_find_report_files(input_dir))
        except Exception as exc:
            messagebox.showerror("No se pudo leer reports", str(exc))
            return
        if count <= 0:
            messagebox.showwarning("Sin reports", f"No hay .htm/.html en:\n{input_dir}")
            return

        try:
            threshold = abs(float(self.portfolio_threshold.get().replace(",", ".")))
        except ValueError:
            messagebox.showerror("Umbral invalido", "Umbral DD diario debe ser un numero. Ejemplo: 50")
            return

        actions = {
            "all": ("ALL_STRATEGIES", generate_portfolio_workbook, self._portfolio_output_path("ALL_STRATEGIES.xlsx"), ()),
            "dd": ("ALL_STRATEGIES_DD", generate_drawdown_workbook, self._portfolio_output_path("ALL_STRATEGIES_DD.xlsx"), ()),
            "portfolio_dd": ("PORTFOLIO_DD", generate_portfolio_drawdown_workbook, self._portfolio_output_path("PORTFOLIO_DD.xlsx"), ()),
            "portfolio_valley": ("DD_VALLE_TOTAL", generate_portfolio_valley_drawdown_workbook, self._portfolio_output_path("PORTFOLIO_VALLEY_DD.xlsx"), ()),
            "top_valleys": ("5 PEORES VALLES", generate_top_portfolio_valleys_workbook, self._portfolio_output_path("PORTFOLIO_TOP5_VALLEYS.xlsx"), ()),
            "threshold": ("FILTRAR DD", generate_dd_threshold_workbook, self._portfolio_output_path("DD_THRESHOLD.xlsx"), (threshold,)),
        }
        title, func, output, extra_args = actions[action]
        if func is None:
            messagebox.showerror("Portfolio Manager no disponible", "No pude cargar el generador seleccionado.")
            return

        if not messagebox.askyesno(
            f"Generar {title}",
            f"Se procesaran {count} reporte(s) desde:\n{input_dir}\n\nSalida:\n{output}\n\nEmpezar?",
        ):
            return

        self._write_ui_settings()
        self._set_portfolio_running(True)
        self.portfolio_status.set(f"Iniciando {title}...")
        thread = threading.Thread(
            target=self._portfolio_worker,
            args=(title, func, input_dir, output, extra_args),
            daemon=True,
        )
        thread.start()

    def _portfolio_worker(self, title: str, func, input_dir: Path, output: Path, extra_args: tuple) -> None:
        try:
            reports = func(input_dir, output, *extra_args, progress=lambda msg: self.after(0, self.portfolio_status.set, msg))
        except Exception as exc:
            self.after(0, self._portfolio_finished, False, title, str(exc), None, 0)
            return
        self.after(0, self._portfolio_finished, True, title, "", output, len(reports))

    def _portfolio_finished(self, ok: bool, title: str, error: str, output: Path | None, count: int) -> None:
        self._set_portfolio_running(False)
        self._refresh_portfolio_count()
        if ok:
            message = f"{title} creado: {output}\nEstrategias procesadas: {count}"
            self.portfolio_status.set(message)
            messagebox.showinfo("Portfolio terminado", message)
        else:
            self.portfolio_status.set(error)
            messagebox.showerror(f"Error generando {title}", error)
