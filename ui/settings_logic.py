from __future__ import annotations

import configparser
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import telegram_notify
from mt5_env import ENV_FILE
from run_tests import EXPERTS_ROOT_FILE, REPORT_DIR


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent

COMPILE_ROOT_FILE = BASE_DIR / "compile_root.txt"
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REPORT_SUFFIXES = {".htm", ".html", ".xml", ".png", ".gif", ".set"}


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

    def _telegram_values(self) -> tuple[str, str]:
        token = self.telegram_bot_token.get().strip()
        chat_id = self.telegram_chat_id.get().strip()
        if not token:
            raise ValueError("Indica TELEGRAM_BOT_TOKEN.")
        if not chat_id:
            raise ValueError("Indica TELEGRAM_CHAT_ID.")
        return token, chat_id

    def _save_telegram_settings(self) -> None:
        self._update_env_vars(
            {
                "TELEGRAM_BOT_TOKEN": self.telegram_bot_token.get().strip(),
                "TELEGRAM_CHAT_ID": self.telegram_chat_id.get().strip(),
            }
        )
        self._write_ui_settings()
        self.status_text.set("Telegram guardado")

    def _save_telegram_clicked(self) -> None:
        try:
            self._save_telegram_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Telegram", str(exc))
            return
        messagebox.showinfo("Telegram guardado", f"Credenciales guardadas en:\n{ENV_FILE}")

    def _test_telegram_clicked(self) -> None:
        try:
            token, chat_id = self._telegram_values()
        except Exception as exc:
            self._show_error("No se pudo probar Telegram", str(exc))
            return
        self.status_text.set("Probando Telegram...")
        self._append_console("\n[Telegram] Enviando mensaje de prueba...\n", tag="telegram")

        def on_result(error: str | None) -> None:
            def finish() -> None:
                if error:
                    self.status_text.set("Telegram: error")
                    self._append_console(f"[Telegram] {error}\n", tag="error")
                    self._show_error("Prueba Telegram fallida", error)
                    return
                self.status_text.set("Telegram probado correctamente")
                self._append_console("[Telegram] Mensaje de prueba enviado correctamente.\n", tag="telegram")
                messagebox.showinfo("Telegram", "Mensaje de prueba enviado correctamente.")

            self.after(0, finish)

        telegram_notify.send_async(
            "MT5 Autotester: mensaje de prueba de Telegram.",
            token=token,
            chat_id=chat_id,
            on_result=on_result,
        )
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
            "Esto cerrara MetaTrader y borrara cache de tester/bases/history y reportes en TODAS las terminales.\n"
            f"Tambien borrara los reportes locales en:\n{REPORT_DIR}\n\n"
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
        self._progress_total = len(scripts) + 1
        self._progress_done = 0
        self._progress_target = 2.0
        try:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate", maximum=100)
            self.progress_var.set(0.0)
        except Exception:
            pass
        threading.Thread(target=self._run_clean_scripts, args=(scripts,), daemon=True).start()

    def _browse_file(self, variable) -> None:
        path = filedialog.askopenfilename(initialdir=str(BASE_DIR))
        if path:
            variable.set(path)

    def _browse_template_file(self, variable) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else BASE_DIR
        initial_dir = current.parent if current.parent.exists() else BASE_DIR
        path = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            filetypes=(("INI files", "*.ini"), ("Todos", "*.*")),
        )
        if path:
            variable.set(path)
            self._load_template_clicked(show_success=False)

    def _browse_dir(self, variable) -> None:
        path = filedialog.askdirectory(initialdir=str(BASE_DIR))
        if path:
            variable.set(path)

    def _browse_mq5_file(self, variable) -> None:
        initial_dir = self.compile_root.get().strip() or str(BASE_DIR)
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("MQL5 source", "*.mq5"), ("Todos", "*.*")),
        )
        if path:
            variable.set(str(Path(path)))

    def _browse_ex5_file(self, variable) -> None:
        initial_dir = self.experts_root.get().strip() or str(BASE_DIR)
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("Compiled Expert Advisor", "*.ex5"), ("Todos", "*.*")),
        )
        if path:
            selected = Path(path)
            root = Path(self.experts_root.get()).expanduser() if self.experts_root.get().strip() else None
            if root:
                try:
                    variable.set(str(selected.relative_to(root)))
                    return
                except ValueError:
                    pass
            variable.set(str(selected))

    def _browse_profile_ex5_file(self, variable) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else None
        experts_root = Path(self.mt_profile_experts_root.get()).expanduser() if self.mt_profile_experts_root.get().strip() else None
        initial_dir = (
            str(current.parent)
            if current and current.parent.exists()
            else str(experts_root if experts_root and experts_root.exists() else BASE_DIR)
        )
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("Compiled Expert Advisor", "*.ex5"), ("Todos", "*.*")),
        )
        if path:
            variable.set(str(Path(path)))

    def _browse_set_file(self, variable) -> None:
        current = Path(variable.get()).expanduser() if variable.get().strip() else None
        initial_dir = (
            str(current.parent)
            if current and current.parent.exists()
            else (self.set_files_root.get().strip() or str(BASE_DIR))
        )
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            filetypes=(("Set files", "*.set"), ("Todos", "*.*")),
        )
        if path:
            variable.set(path)

    def _save_paths(self) -> None:
        self._write_single_path(COMPILE_ROOT_FILE, self.compile_root.get(), "Carpeta raiz donde estan los .mq5 a compilar.")
        self._write_single_path(EXPERTS_ROOT_FILE, self.experts_root.get(), "Carpeta raiz donde estan los .ex5 a testear.")
        self._write_ui_settings()
        self.status_text.set("Rutas guardadas")
        try:
            self._load_template()
        except Exception:
            self.status_text.set("Rutas guardadas; template tester no cargado")
        self._refresh_all()

    def _update_env_vars(self, updates: dict[str, str]) -> None:
        existing_lines: list[str] = []
        if ENV_FILE.exists():
            existing_lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()

        remaining = dict(updates)
        new_lines: list[str] = []
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            name = stripped.split("=", 1)[0].strip()
            if name in remaining:
                new_lines.append(f"{name}={remaining.pop(name)}")
            else:
                new_lines.append(line)

        for name, value in remaining.items():
            new_lines.append(f"{name}={value}")

        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        import mt5_env
        mt5_env._PROJECT_ENV = None

    def _write_ui_settings(self) -> None:
        self._save_current_multiterminal_editor()
        saved_multiterminal_tuning = {
            "terminal_cooldown": "1",
            "tester_kick_after": "30",
        }
        if UI_SETTINGS_FILE.exists():
            existing = configparser.ConfigParser(interpolation=None)
            existing.optionxform = str
            existing.read(UI_SETTINGS_FILE, encoding="utf-8-sig")
            if existing.has_section("Multiterminal"):
                for key, default in saved_multiterminal_tuning.items():
                    saved_multiterminal_tuning[key] = existing["Multiterminal"].get(key, default).strip() or default
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Paths"] = {
            "mt5_path": self.mt5_path.get().strip(),
            "mt5_data_root": self.mt5_data_root.get().strip(),
            "metaeditor_path": self.metaeditor_path.get().strip(),
            "compile_root": self.compile_root.get().strip(),
            "compile_file": self.compile_file.get().strip(),
            "experts_root": self.experts_root.get().strip(),
            "ubs_ex5_file": self.ubs_ex5_file.get().strip(),
            "set_files_root": self.set_files_root.get().strip(),
            "ubs_set_file": self.ubs_set_file.get().strip(),
            "template_path": self.template_path.get().strip(),
            "ubs_generation_output": self.ubs_generation_output.get().strip(),
            "portfolio_input": self.portfolio_input.get().strip(),
            "portfolio_output": self.portfolio_output.get().strip(),
        }
        parser["General"] = {
            "recursive": "1" if self.recursive.get() else "0",
            "delay": str(self.delay.get()),
            "ubs_generation_count": str(self.ubs_generation_count.get()),
            "ubs_variants_per_seed": str(self.ubs_variants_per_seed.get()),
            "ubs_max_seeds": str(self.ubs_max_seeds.get()),
            "ubs_agent_execute": "1" if self.ubs_agent_execute.get() else "0",
            "ubs_force_unseeded_universe": "1" if self.ubs_force_unseeded_universe.get() else "0",
            "ubs_pass_min_net_profit": self.ubs_pass_min_net_profit.get().strip(),
            "ubs_pass_min_profit_factor": self.ubs_pass_min_profit_factor.get().strip(),
            "ubs_pass_min_trades": str(self.ubs_pass_min_trades.get()),
            "ubs_pass_max_drawdown_pct": self.ubs_pass_max_drawdown_pct.get().strip(),
            "ubs_pass_min_recovery_factor": self.ubs_pass_min_recovery_factor.get().strip(),
            "ubs_seed_pass_min_net_profit": self.ubs_seed_pass_min_net_profit.get().strip(),
            "ubs_seed_pass_min_profit_factor": self.ubs_seed_pass_min_profit_factor.get().strip(),
            "ubs_seed_pass_min_trades": str(self.ubs_seed_pass_min_trades.get()),
            "ubs_seed_pass_max_drawdown_pct": self.ubs_seed_pass_max_drawdown_pct.get().strip(),
            "ubs_seed_pass_min_recovery_factor": self.ubs_seed_pass_min_recovery_factor.get().strip(),
            "ubs_robust_pass_min_net_profit": self.ubs_robust_pass_min_net_profit.get().strip(),
            "ubs_robust_pass_min_profit_factor": self.ubs_robust_pass_min_profit_factor.get().strip(),
            "ubs_robust_pass_min_trades": str(self.ubs_robust_pass_min_trades.get()),
            "ubs_robust_pass_max_drawdown_pct": self.ubs_robust_pass_max_drawdown_pct.get().strip(),
            "ubs_robust_pass_min_recovery_factor": self.ubs_robust_pass_min_recovery_factor.get().strip(),
            "ubs_robust_positive_bonus": self.ubs_robust_positive_bonus.get().strip(),
            "ubs_robust_negative_bonus": self.ubs_robust_negative_bonus.get().strip(),
            "ubs_robust_auto": "1" if self.ubs_robust_auto.get() else "0",
            "ubs_agent_from_date": self.ubs_agent_from_date.get().strip(),
            "ubs_agent_to_date": self.ubs_agent_to_date.get().strip(),
            "ubs_seed_from_date": self.ubs_seed_from_date.get().strip(),
            "ubs_seed_to_date": self.ubs_seed_to_date.get().strip(),
            "ubs_robust_from_date": self.ubs_robust_from_date.get().strip(),
            "ubs_robust_to_date": self.ubs_robust_to_date.get().strip(),
            "ubs_final_tick_from_date": self.ubs_final_tick_from_date.get().strip(),
            "ubs_final_tick_to_date": self.ubs_final_tick_to_date.get().strip(),
            "ubs_final_tick_min_history_quality": self.ubs_final_tick_min_history_quality.get().strip(),
            "ubs_final_tick_max_net_delta_pct": self.ubs_final_tick_max_net_delta_pct.get().strip(),
            "ubs_final_tick_max_pf_delta_pct": self.ubs_final_tick_max_pf_delta_pct.get().strip(),
            "ubs_final_tick_max_dd_delta_pct": self.ubs_final_tick_max_dd_delta_pct.get().strip(),
            "ubs_final_tick_max_trades_delta_pct": self.ubs_final_tick_max_trades_delta_pct.get().strip(),
            "symbol_suffix_enabled": "1" if self.symbol_suffix_enabled.get() else "0",
            "symbol_suffix": self.symbol_suffix.get().strip(),
            "symbol_map_enabled": "1" if self.symbol_map_enabled.get() else "0",
            "symbol_map": self.symbol_map.get().strip(),
            "telegram_enabled": "1" if self.telegram_enabled.get() else "0",
            "portfolio_threshold": self.portfolio_threshold.get().strip(),
            "ubs_portfolio_num_symbols": str(self.ubs_portfolio_num_symbols.get()),
            "ubs_portfolio_type": self.ubs_portfolio_type.get().strip(),
            "ubs_portfolio_valley_pct": self.ubs_portfolio_valley_pct.get().strip(),
            "ubs_portfolio_point_pct": self.ubs_portfolio_point_pct.get().strip(),
            "ubs_portfolio_capital": self.ubs_portfolio_capital.get().strip(),
            "ubs_portfolio_top_k": str(self.ubs_portfolio_top_k.get()),
            "ubs_portfolio_max_candidates": str(self.ubs_portfolio_max_candidates.get()),
            "ubs_portfolio_min_trades": str(self.ubs_portfolio_min_trades.get()),
            "ubs_portfolio_max_units_per_set": self.ubs_portfolio_max_units_per_set.get().strip(),
            "ubs_portfolio_max_total_units": self.ubs_portfolio_max_total_units.get().strip(),
            "ubs_portfolio_max_units_per_symbol": self.ubs_portfolio_max_units_per_symbol.get().strip(),
            "ubs_portfolio_max_sets_per_symbol": str(self.ubs_portfolio_max_sets_per_symbol.get()),
            "ubs_portfolio_run_local_search": "1" if self.ubs_portfolio_run_local_search.get() else "0",
            "ubs_portfolio_use_correlation": "1" if self.ubs_portfolio_use_correlation.get() else "0",
            "ubs_portfolio_max_pair_corr": self.ubs_portfolio_max_pair_corr.get().strip(),
            "ubs_portfolio_max_downside_corr": self.ubs_portfolio_max_downside_corr.get().strip(),
            "ubs_portfolio_max_dd_overlap": self.ubs_portfolio_max_dd_overlap.get().strip(),
            "ubs_portfolio_max_portfolio_corr": self.ubs_portfolio_max_portfolio_corr.get().strip(),
            "theme": self.theme_mode.get(),
        }
        parser["Multiterminal"] = {
            "enabled": "1" if self.multiterminal_enabled.get() else "0",
            "workers": str(self._multiterminal_worker_limit()),
            "terminal_cooldown": saved_multiterminal_tuning["terminal_cooldown"],
            "tester_kick_after": saved_multiterminal_tuning["tester_kick_after"],
        }
        for index, profile in enumerate(self.multiterminal_profiles, start=1):
            parser[f"Terminal.{index}"] = {
                "enabled": "1" if bool(profile.get("enabled")) else "0",
                "name": str(profile.get("name") or f"Terminal {index}").strip(),
                "mt5_path": str(profile.get("mt5_path") or "").strip(),
                "data_dir": str(profile.get("data_dir") or "").strip(),
                "experts_root": str(profile.get("experts_root") or "").strip(),
                "ubs_ex5_file": str(profile.get("ubs_ex5_file") or "").strip(),
                "portable": "1" if bool(profile.get("portable")) else "0",
            }
        with UI_SETTINGS_FILE.open("w", encoding="utf-8", newline="\n") as file:
            parser.write(file, space_around_delimiters=False)
        self._update_multiterminal_summary()

    def _write_single_path(self, path: Path, value: str, comment: str) -> None:
        text = f"# {comment}\n{value.strip()}\n" if value.strip() else f"# {comment}\n"
        path.write_text(text, encoding="utf-8")

    def _delete_old_reports(self) -> None:
        files = self._project_report_files()
        if not files:
            messagebox.showinfo("Sin reportes", "No hay reportes generados para borrar.")
            return
        if not messagebox.askyesno(
            "Borrar reportes antiguos",
            f"Se borraran {len(files)} archivo(s) de reportes de la carpeta {REPORT_DIR}.\n\nContinuar?"
        ):
            return

        deleted = 0
        failures: list[str] = []
        for path in files:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")

        self._refresh_reports()
        self.status_text.set(f"Reportes borrados: {deleted}")
        self._append_console(f"\nReportes borrados: {deleted}\n", tag="warn")
        if failures:
            details = "\n".join(failures[:12])
            self._show_error("No se pudieron borrar todos los reportes", details)
        else:
            messagebox.showinfo("Reportes borrados", f"Se borraron {deleted} reporte(s).")

    def _project_report_files(self) -> list[Path]:
        if not REPORT_DIR.exists():
            return []
        return [
            path for path in REPORT_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in REPORT_SUFFIXES
        ]

    def _delete_project_reports_for_clean(self) -> tuple[int, list[str]]:
        deleted = 0
        failures: list[str] = []
        for path in self._project_report_files():
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
        return deleted, failures

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

    def _run_clean_scripts(self, scripts: list[Path]) -> None:
        total = max(1, len(scripts) + 1)
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
        self.output_queue.put("\n>>> Borrando reportes locales de reports/\n")
        self.after(0, lambda: self._set_clean_progress(100.0 * len(scripts) / total))
        deleted_reports, report_failures = self._delete_project_reports_for_clean()
        self.output_queue.put(f"Reportes locales eliminados: {deleted_reports}\n")
        if report_failures:
            failures += 1
            self.output_queue.put("No se pudieron borrar algunos reportes locales:\n")
            for detail in report_failures[:20]:
                self.output_queue.put(f"  {detail}\n")
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
            self.status_text.set(f"Limpieza terminada con {failures} script(s) fallido(s)")
            messagebox.showwarning(
                "Limpieza con errores",
                f"La limpieza termino con {failures} script(s) fallido(s).\nRevisa la consola en la pestaña Logs."
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
                "Se eliminaron los datos historicos correctamente (tester, bases, history, reports, .fxt, .tick)."
            )
        self._refresh_all()
