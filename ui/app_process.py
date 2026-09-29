"""Lanzamiento de scripts, consola, progreso y parada del proceso."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import sys
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from run_tests import RUNNING_TERMINAL_EXIT_CODE, find_matching_running_terminals
from ui.app_base import (
    BASE_DIR,
    COLORS,
    CONSOLE_MAX_LINES,
    NO_WINDOW,
    OUTPUT_DRAIN_BUSY_INTERVAL_MS,
    OUTPUT_DRAIN_IDLE_INTERVAL_MS,
    OUTPUT_DRAIN_MAX_ITEMS,
    OUTPUT_DRAIN_TIME_BUDGET_SECONDS,
    resolve_existing_local_file,
)


class AppProcessMixin:
    """Lanzamiento de scripts, consola, progreso y parada del proceso."""

    def _open_local_file(self, path: Path) -> None:
        path = resolve_existing_local_file(path, BASE_DIR)
        if not path.exists():
            messagebox.showinfo("Agente UBS", f"No existe el archivo:\n{path}")
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["explorer", "/select,", str(path)])

    def _run_script(self, script_name: str, args: list[str]) -> None:
        if self._manager_node.job_running:
            messagebox.showwarning("Proceso remoto activo", "El manager esta utilizando el agente. Espera a que termine.")
            return
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Proceso activo", "Ya hay un proceso en ejecucion.")
            return
        if self._should_block_for_running_mt5(script_name, args):
            return
        try:
            self._save_template()
            command = self._script_command(script_name, args)
            self._running_script_name = script_name
            self._running_script_args = list(args)
            self._append_console(f"\n> {self._format_command(command)}\n", tag="debug")
            self.status_text.set(f"Ejecutando {script_name}")
            self.running_text.set("Proceso activo")
            self.active_task_text.set(self._script_label(script_name, args))
            self.active_task_detail.set("0%")
            self.engine_status_text.set("Engine Running")
            if hasattr(self, "term_status_text"):
                self.term_status_text.set(f"Running: {script_name}")
                self.term_status_icon.configure(fg="#ffb95f")
                self.idle_label.configure(text="RUN")
            self._progress_total = 0
            self._progress_done = 0
            self._progress_target = 4.0
            self._progress_running = True
            self._set_progress_color("accent")
            try:
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate", maximum=100)
                self.progress_var.set(0.0)
            except Exception:
                pass
            self.stop_requested = False
            self.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=NO_WINDOW,
            )
        except Exception as exc:
            self.running_text.set("Sin proceso activo")
            self.status_text.set("No se pudo iniciar el proceso")
            self._show_error("No se pudo iniciar", str(exc), traceback.format_exc())
            return

        self.reader_thread = threading.Thread(target=self._read_process_output, daemon=True)
        self.reader_thread.start()

    def _should_block_for_running_mt5(self, script_name: str, args: list[str] | None = None) -> bool:
        if script_name not in {"run_tests.py", "compile_and_backtest.py", "ubs_agent.py"}:
            return False
        args = args or []
        if "--multi-terminal" in args:
            if "--skip-running-check" in args:
                return False
            return self._should_block_for_running_multiterminal_mt5()
        ubs_runs_backtests = (
            self.ubs_agent_execute.get()
            or "--execute-backtests" in args
            or "--probe-universe-history" in args
            or "--evaluate-seeds" in args
            or "--evaluate-robustness" in args
            or "--evaluate-final-tick" in args
            or "--backtest-pending-only" in args
            or "--retry-candidate-id" in args
            or "--retry-seed-path" in args
            or "--retry-mismatch-run" in args
            or "--retry-mismatch-generation" in args
            or "--retry-full-run" in args
        )
        if script_name == "ubs_agent.py" and not ubs_runs_backtests:
            return False

        mt5_path = Path(self.mt5_path.get()).expanduser()
        running = find_matching_running_terminals(mt5_path)
        if not running:
            return False

        process_lines = "\n".join(f"PID {process['pid']}: {process['path']}" for process in running)
        messagebox.showerror(
            "MT5 ya esta abierto",
            "RoboForex MT5 ya esta abierto.\n\n"
            f"{process_lines}\n\n"
            "Cierra MT5 completamente y vuelve a ejecutar el backtest.",
        )
        self.status_text.set("Backtest cancelado: MT5 ya esta abierto")
        return True

    def _should_block_for_running_multiterminal_mt5(self) -> bool:
        if not hasattr(self, "_active_multiterminal_profiles"):
            return False
        running_lines: list[str] = []
        for profile in self._active_multiterminal_profiles():
            name = str(profile.get("name") or "Terminal")
            mt5_path = self._profile_path(profile, "mt5_path") if hasattr(self, "_profile_path") else None
            if not mt5_path:
                continue
            running = find_matching_running_terminals(mt5_path)
            for process in running:
                running_lines.append(f"{name} | PID {process['pid']}: {process['path']}")
        if not running_lines:
            return False
        messagebox.showerror(
            "MT5 multiterminal ya abierto",
            "Hay una o mas terminales MT5 de perfiles multiterminal abiertas.\n\n"
            + "\n".join(running_lines)
            + "\n\nCierra esas terminales completamente y vuelve a ejecutar.",
        )
        self.status_text.set("Backtest cancelado: MT5 multiterminal ya abierto")
        return True

    def _read_process_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output_queue.put(line)
        code = self.process.wait()
        self.output_queue.put(("DONE", code))

    def _drain_output_queue(self) -> None:
        deadline = time.perf_counter() + OUTPUT_DRAIN_TIME_BUDGET_SECONDS
        processed = 0
        console_batch: list[tuple[str, str | None]] = []
        try:
            while processed < OUTPUT_DRAIN_MAX_ITEMS and time.perf_counter() < deadline:
                item = self.output_queue.get_nowait()
                processed += 1
                if isinstance(item, tuple) and item[0] == "DONE":
                    self._append_console_batch(console_batch)
                    console_batch = []
                    code = item[1]
                    finished_script_name = getattr(self, "_running_script_name", "")
                    finished_script_args = list(getattr(self, "_running_script_args", []))
                    self.running_text.set("Sin proceso activo")
                    self.engine_status_text.set("Engine Ready")
                    self._progress_running = False
                    try:
                        self.progress_bar.stop()
                    except Exception:
                        pass
                    current_pct = int(round(float(self.progress_var.get())))
                    if self.stop_requested:
                        self._set_progress_color("danger")
                        self.status_text.set("Proceso detenido")
                        self._append_console(f"\nProceso detenido por el usuario. Codigo: {code}\n", tag="error")
                        self.active_task_text.set("Detenido")
                        self.active_task_detail.set(f"Detenido en {current_pct}%")
                        self.stop_requested = False
                        self._refresh_all()
                        continue

                    if code == 0:
                        self._set_progress_color("accent")
                        self._progress_target = 100.0
                        try:
                            self.progress_var.set(100.0)
                        except Exception:
                            pass
                        self.active_task_text.set("Finalizado")
                        self.active_task_detail.set("100%")
                    else:
                        self._set_progress_color("danger")
                        self.active_task_text.set("Error")
                        self.active_task_detail.set(f"Fallo en {current_pct}%")
                    self.status_text.set(f"Proceso terminado con codigo {code}")
                    tag = "info" if code == 0 else "error"
                    self._append_console(f"\nProceso terminado con codigo {code}\n", tag=tag)
                    if hasattr(self, "term_status_text"):
                        self.term_status_text.set(f"Process finished with code {code}")
                        self.term_status_icon.configure(fg=COLORS["log_info"] if code == 0 else COLORS["log_error"])
                        self.idle_label.configure(text="IDLE")
                    self._refresh_all()
                    notification_message = self._completion_notification_message(
                        finished_script_name,
                        finished_script_args,
                        code,
                    )
                    auto_followup_started = False
                    if code == 0 and hasattr(self, "_maybe_auto_run_ubs_robustness"):
                        auto_followup_started = self._maybe_auto_run_ubs_robustness(
                            finished_script_name,
                            finished_script_args,
                            code,
                        )
                    if (
                        code == 0
                        and not auto_followup_started
                        and hasattr(self, "_maybe_auto_run_ubs_final_tick")
                    ):
                        auto_followup_started = self._maybe_auto_run_ubs_final_tick(
                            finished_script_name,
                            finished_script_args,
                            code,
                        )
                    if (
                        code == 0
                        and not auto_followup_started
                        and hasattr(self, "_maybe_auto_run_ubs_regression")
                    ):
                        auto_followup_started = self._maybe_auto_run_ubs_regression(
                            finished_script_name,
                            finished_script_args,
                            code,
                        )
                    if code == 0:
                        self._notify_telegram(notification_message)
                        if not auto_followup_started:
                            messagebox.showinfo("Proceso terminado", "El proceso termino correctamente.")
                    elif code == RUNNING_TERMINAL_EXIT_CODE:
                        self._notify_telegram(notification_message)
                        messagebox.showerror(
                            "MT5 ya esta abierto",
                            "El proceso se cancelo porque una terminal MT5 ya estaba abierta.\n\n"
                            "Cierra las terminales MT5 usadas por el proceso y vuelve a ejecutar.",
                        )
                    else:
                        self._notify_telegram(notification_message)
                        self._show_error(
                            "Proceso terminado con error",
                            f"El proceso termino con codigo {code}.",
                            self._console_tail(),
                        )
                else:
                    line = str(item)
                    console_batch.append((line, self._tag_for_line(line)))
                    self._update_progress_from_line(line)
        except queue.Empty:
            pass
        self._append_console_batch(console_batch)
        interval = (
            OUTPUT_DRAIN_BUSY_INTERVAL_MS
            if not self.output_queue.empty()
            else OUTPUT_DRAIN_IDLE_INTERVAL_MS
        )
        self.after(interval, self._drain_output_queue)

    def _stop_process(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.status_text.set("No hay proceso activo")
            return
        self.stop_requested = True
        self.status_text.set("Deteniendo proceso")
        self._append_console("\nDeteniendo proceso y subprocesos...\n")
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=NO_WINDOW,
            )
        except Exception:
            self.process.terminate()

    def _load_log_file(self, path: Path) -> None:
        if not path.exists():
            messagebox.showinfo("Log", f"No existe {path.name}")
            return
        self._clear_console()
        self._append_console(path.read_text(encoding="utf-8", errors="replace"))

    def _clear_console(self) -> None:
        self.console.delete("1.0", "end")

    def _append_console(self, text: str, tag: str | None = None) -> None:
        self._append_console_batch([(text, tag)])

    def _append_console_batch(self, entries: list[tuple[str, str | None]]) -> None:
        if not entries:
            return
        segments: list[tuple[str, str | None]] = []
        for text, tag in entries:
            if segments and segments[-1][1] == tag:
                previous_text, _previous_tag = segments[-1]
                segments[-1] = (previous_text + text, tag)
            else:
                segments.append((text, tag))
        for text, tag in segments:
            if tag:
                self.console.insert("end", text, tag)
            else:
                self.console.insert("end", text)
        try:
            line_count = int(self.console.index("end-1c").split(".", 1)[0])
            excess_lines = line_count - CONSOLE_MAX_LINES
            if excess_lines > 0:
                self.console.delete("1.0", f"{excess_lines + 1}.0")
        except (tk.TclError, ValueError):
            pass
        self.console.see("end")

    def _update_progress_from_line(self, line: str) -> None:
        import re
        low = line.lower()
        # Total de tareas (varias variantes que emiten los scripts)
        m = re.search(r"expert advisors:\s*(\d+)", low)
        if not m:
            m = re.search(r"\.ex5 disponibles[^:]*:\s*(\d+)", low)
        if not m:
            m = re.search(r"backtests en cola:\s*(\d+)", low)
        if m:
            try:
                total = int(m.group(1))
                if total > 0:
                    self._progress_total = total
                    self._progress_done = 0
                    self._progress_target = 6.0
                    self.active_task_detail.set("0%")
            except ValueError:
                pass
            return
        # Inicio de un sub-paso: avanza un poquito hacia el siguiente checkpoint
        if low.startswith("config:") or "comando:" in low or "reporte esperado:" in low:
            total = self._progress_total or 1
            base = 100.0 * self._progress_done / total
            sub = 100.0 / total * 0.35  # ~35% del slot al detectar inicio del backtest
            self._progress_target = min(99.0, base + sub)
            return
        # Fin de un backtest/compilación → siguiente slot completo
        if "mt5 termino con codigo" in low or "compilado" in low or "compilation successful" in low:
            self._progress_done += 1
            total = self._progress_total or max(self._progress_done, 1)
            pct = min(99.0, 100.0 * self._progress_done / total)
            self._progress_target = pct
            self.active_task_detail.set(f"{int(pct)}%")
            return
        # Reportes encontrados/copiados: marca fin del slot
        if "reportes encontrados" in low or "copiado a reports" in low:
            total = self._progress_total or 1
            base = 100.0 * self._progress_done / total
            sub = 100.0 / total * 0.85
            self._progress_target = max(self._progress_target, min(99.0, base + sub))
            return
        # Frases de fin que indican casi-fin
        if "todos los backtests han terminado" in low or "dry-run terminado" in low:
            self._progress_target = 99.0

    def _set_progress_color(self, kind: str) -> None:
        try:
            style = ttk.Style(self)
            if kind == "danger":
                color = COLORS["danger"]
            else:
                color = COLORS["accent"]
            style.configure("Horizontal.TProgressbar", background=color,
                            lightcolor=color, darkcolor=color)
        except Exception:
            pass

    def _animate_progress(self) -> None:
        try:
            current = float(self.progress_var.get())
            target = float(self._progress_target)
            changed = False
            if abs(target - current) > 0.05:
                step = (target - current) * 0.15
                if self._progress_running and 0 < step < 0.35:
                    step = 0.35
                new_val = current + step
                if (step > 0 and new_val > target) or (step < 0 and new_val < target):
                    new_val = target
                new_val = max(0.0, min(100.0, new_val))
                self.progress_var.set(new_val)
                current = new_val
                changed = True
            elif self._progress_running and current < self._progress_target - 0.5:
                self.progress_var.set(current + 0.3)
                current += 0.3
                changed = True
            if changed and self._progress_running:
                self.active_task_detail.set(f"{int(round(current))}%")
        except Exception:
            pass
        self.after(60, self._animate_progress)

    def _tag_for_line(self, line: str) -> str | None:
        low = line.lower()
        if "[telegram]" in low:
            return "telegram"
        if "error" in low or "fallo" in low or "exception" in low or "traceback" in low:
            return "error"
        if "terminado" in low or "ok" in low or "exito" in low or "completed" in low or "correctamente" in low:
            return "info"
        return None

    def _script_label(self, script_name: str, args: list[str] | None = None) -> str:
        args = args or []
        if script_name == "ubs_agent.py":
            if "--probe-universe-history" in args:
                return "Probando history universo"
            if "--evaluate-robustness" in args:
                return "Robustez UBS"
            if "--evaluate-seeds" in args:
                return "Evaluando seeds UBS"
        labels = {
            "compile_mq5.py": "Compilando .mq5",
            "run_tests.py": "Ejecutando backtests",
            "compile_and_backtest.py": "Compilando y backtesteando",
            "ubs_generate_sets.py": "Generando sets UBS",
            "ubs_agent.py": "Agente UBS",
        }
        return labels.get(script_name, script_name)

    def _format_command(self, command: list[str]) -> str:
        return " ".join(f'"{part}"' if " " in part else part for part in command)

    def _script_command(self, script_name: str, args: list[str]) -> list[str]:
        if getattr(sys, "frozen", False):
            exe_path = BASE_DIR / Path(script_name).with_suffix(".exe")
            return [str(exe_path), *args]
        return [sys.executable, str(BASE_DIR / script_name), *args]

    def _console_tail(self, max_chars: int = 4000) -> str:
        text = self.console.get("1.0", "end").strip()
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def _show_error(self, title: str, message: str, details: str = "") -> None:
        full_message = message.strip()
        if details.strip():
            full_message = f"{full_message}\n\nDetalles:\n{details.strip()}"
        messagebox.showerror(title, full_message)
