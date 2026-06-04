from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class DashboardViewMixin:
    def _build_dashboard(self, parent: ttk.Frame) -> None:
        outer = parent
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        parent = ttk.Frame(canvas, padding=(0, 0, 10, 0))
        content_id = canvas.create_window((0, 0), window=parent, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
        parent.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_wheel(event):
            delta = -1 if event.delta > 0 else 1
            if hasattr(event, "num"):
                if event.num == 4:
                    delta = -1
                elif event.num == 5:
                    delta = 1
            canvas.yview_scroll(delta, "units")

        def _bind_wheel(_e=None):
            self.bind_all("<MouseWheel>", _on_wheel)
            self.bind_all("<Button-4>", _on_wheel)
            self.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_e=None):
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        for widget in (canvas, outer, parent):
            widget.bind("<Enter>", _bind_wheel)
            widget.bind("<Leave>", _unbind_wheel)

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        metrics = ttk.Frame(parent)
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        for column in range(4):
            metrics.columnconfigure(column, weight=1)
        self._metric(metrics, 0, "Expert Advisors", self.experts_count, "▦")
        self._metric(metrics, 1, "Reportes generados", self.reports_count, "▤")
        self._metric(metrics, 2, "Modo ejecucion", self.mode_text, "⚡")


        active_task = self._card(parent, "Tarea activa", chip_text=None)
        active_task.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        active_task.columnconfigure(0, weight=1)
        task_row = ttk.Frame(active_task, style="Panel.TFrame")
        task_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        task_row.columnconfigure(0, weight=1)
        ttk.Label(task_row, textvariable=self.active_task_text, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(task_row, textvariable=self.active_task_detail, style="CardDesc.TLabel").grid(row=0, column=1, sticky="e")
        self.progress_bar = ttk.Progressbar(active_task, mode="determinate", maximum=100,
                                             variable=self.progress_var, style="Horizontal.TProgressbar")
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 18))

        body = ttk.Frame(parent)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=8)
        body.columnconfigure(1, weight=4)

        actions_card = self._card(body, "Acciones")
        actions_card.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        actions_card.columnconfigure(0, weight=1)
        actions_card.columnconfigure(1, weight=1)
        self._build_multiterminal_inline(actions_card)

        self._action_card(
            actions_card, 2, 0,
            icon="< >",
            title="Compilar .mq5",
            description="Regenera los .ex5 a partir de los .mq5 del directorio fuente.",
            command=self._run_compile,
        )
        self._action_card(
            actions_card, 2, 1,
            icon="▶",
            title="Ejecutar backtests",
            description="Lanza la cola configurada contra los datos historicos.",
            command=self._run_backtests,
        )
        self._action_card(
            actions_card, 3, 0,
            icon="UBS",
            title="Tester UBS",
            description="Testea un solo bot usando todos los set files configurados.",
            command=self._run_ubs_tester,
        )
        self._action_card(
            actions_card, 3, 1,
            icon="GEN",
            title="Agente UBS",
            description="Elige seeds/assets, muta parametros y usa feedback si ejecuta backtests.",
            command=self._run_ubs_generator,
        )

        compile_and = self._rounded_button_cls(
            actions_card,
            text="🚀  Compilar y backtest      Flujo completo automatizado",
            bg=self.colors["accent"], hover_bg=self.colors["accent_hover"],
            font=("Segoe UI", 11, "bold"),
            radius=14, padx=20, pady=18, anchor="w",
            parent_bg=self.colors["panel"],
            command=self._run_full_flow,
        )
        compile_and.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(8, 8))

        stop_btn = self._rounded_button_cls(
            actions_card,
            text="✕  DETENER PROCESO",
            bg=self.colors["danger"], hover_bg="#8a0d0d",
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=16, pady=12,
            parent_bg=self.colors["panel"],
            command=self._stop_process,
        )
        stop_btn.grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))

        config_card = self._card(body, "Configuration")
        config_card.grid(row=0, column=1, sticky="nsew")
        config_card.columnconfigure(0, weight=1)

        rec_row = tk.Frame(config_card, bg=self.colors["panel"])
        rec_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(6, 0))
        rec_row.columnconfigure(0, weight=1)
        rec_text = tk.Frame(rec_row, bg=self.colors["panel"])
        rec_text.grid(row=0, column=0, sticky="w")
        tk.Label(rec_text, text="Recursivo", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(rec_text, text="Procesar todos los archivos de la carpeta raiz", bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        self._toggle_switch_cls(rec_row, variable=self.recursive, bg=self.colors["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        delay_lbl = tk.Frame(config_card, bg=self.colors["panel"])
        delay_lbl.grid(row=2, column=0, sticky="ew", padx=20, pady=(18, 6))
        tk.Label(delay_lbl, text="Pausa entre tests (s)", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Spinbox(config_card, from_=0, to=120, textvariable=self.delay).grid(row=3, column=0, sticky="ew", padx=20)

        suffix_lbl = tk.Frame(config_card, bg=self.colors["panel"])
        suffix_lbl.grid(row=4, column=0, sticky="ew", padx=20, pady=(18, 6))
        suffix_lbl.columnconfigure(0, weight=1)
        suffix_text = tk.Frame(suffix_lbl, bg=self.colors["panel"])
        suffix_text.grid(row=0, column=0, sticky="w")
        tk.Label(suffix_text, text="Sufijo simbolos", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(suffix_text, text="Ejemplo: .a convierte XAUUSD en XAUUSD.a", bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        self._toggle_switch_cls(suffix_lbl, variable=self.symbol_suffix_enabled, bg=self.colors["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))
        ttk.Entry(config_card, textvariable=self.symbol_suffix).grid(row=5, column=0, sticky="ew", padx=20)

        map_lbl = tk.Frame(config_card, bg=self.colors["panel"])
        map_lbl.grid(row=6, column=0, sticky="ew", padx=20, pady=(18, 6))
        map_lbl.columnconfigure(0, weight=1)
        map_text = tk.Frame(map_lbl, bg=self.colors["panel"])
        map_text.grid(row=0, column=0, sticky="w")
        tk.Label(map_text, text="Correspondencia simbolos", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(map_text, text="Ejemplo: XTIUSD=USOIL, GER40=DAX", bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        self._toggle_switch_cls(map_lbl, variable=self.symbol_map_enabled, bg=self.colors["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))
        ttk.Entry(config_card, textvariable=self.symbol_map).grid(row=7, column=0, sticky="ew", padx=20)

        tg_row = tk.Frame(config_card, bg=self.colors["panel"])
        tg_row.grid(row=8, column=0, sticky="ew", padx=20, pady=(18, 0))
        tg_row.columnconfigure(0, weight=1)
        tg_text = tk.Frame(tg_row, bg=self.colors["panel"])
        tg_text.grid(row=0, column=0, sticky="w")
        tk.Label(tg_text, text="Notificaciones Telegram", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(tg_text, text="Alerta al finalizar o fallar un proceso", bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        self._toggle_switch_cls(tg_row, variable=self.telegram_enabled, command=self._write_ui_settings,
                     bg=self.colors["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        save_cfg = self._rounded_button_cls(
            config_card, text="Guardar configuracion",
            bg=self.colors["primary_container"], hover_bg=self.colors["primary"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=self.colors["panel"],
            command=self._save_config_clicked,
        )
        save_cfg.grid(row=9, column=0, sticky="ew", padx=20, pady=(20, 10))

        del_btn = self._rounded_button_cls(
            config_card, text="🗑  Eliminar datos historicos",
            bg=self.colors["panel"], fg=self.colors["danger"],
            hover_bg=self.colors["danger_soft"], hover_fg=self.colors["danger"],
            border=self.colors["danger"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=14,
            parent_bg=self.colors["panel"],
            command=self._delete_historical_data,
        )
        del_btn.grid(row=10, column=0, sticky="ew", padx=20, pady=(0, 22))

    # ------------------------------------------------------------------

