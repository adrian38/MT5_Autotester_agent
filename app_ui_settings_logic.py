from __future__ import annotations

import subprocess
from pathlib import Path
from tkinter import messagebox

from run_tests import REPORT_DIR


class SettingsLogicMixin:
    def _load_template(self) -> None:
        template_text = self.template_path.get().strip()
        if not template_text:
            raise ValueError("Indica la ruta del template tester.")
        template = Path(template_text).expanduser()
        if not template.exists():
            fallback = BASE_DIR / template.name
            if fallback.exists():
                template = fallback
                self.template_path.set(str(fallback))
            else:
                raise FileNotFoundError(f"No existe el archivo:\n{template}")
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(template, encoding="utf-8-sig")
        tester = parser["Tester"] if parser.has_section("Tester") else {}
        for key, variable in self.tester_vars.items():
            variable.set(tester.get(key, ""))
        self.status_text.set(f"Cargado {template.name}")
    def _load_template_clicked(self, show_success: bool = True) -> None:
        try:
            self._load_template()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo cargar el template", str(exc))
            return
        if show_success:
            messagebox.showinfo("Template cargado", f"Datos cargados desde:\n{self.template_path.get().strip()}")
    def _save_template(self) -> None:
        template_text = self.template_path.get().strip()
        if not template_text:
            raise ValueError("Indica una ruta para el template tester antes de guardar.")

        template = Path(template_text).expanduser()
        template.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Tester"] = {"Expert": ""}
        for key, variable in self.tester_vars.items():
            parser["Tester"][key] = variable.get().strip()
        parser["Tester"]["Report"] = ""
        with template.open("w", encoding="utf-8", newline="\n") as file:
            parser.write(file, space_around_delimiters=False)
        self._write_ui_settings()
        self.status_text.set(f"Guardado {template.name}")
    def _save_template_clicked(self) -> None:
        try:
            self._save_template()
        except Exception as exc:
            self._show_error("No se pudo guardar el template", str(exc))
            return
        messagebox.showinfo("Template guardado", f"tester_template.ini guardado correctamente en:\n{self.template_path.get().strip()}")
    def _save_paths_clicked(self) -> None:
        try:
            self._save_paths()
        except Exception as exc:
            self._show_error("No se pudieron guardar las rutas", str(exc))
            return
        messagebox.showinfo("Rutas guardadas", "Las rutas y opciones se guardaron correctamente.")
    def _save_config_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar la configuracion", str(exc))
            return
        self.status_text.set("Configuracion guardada")
        messagebox.showinfo("Configuracion guardada", "La configuracion se guardo correctamente.")
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
            "Esto cerrara MetaTrader y borrara cache de tester/bases/history en TODAS las terminales.\n\n"
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

