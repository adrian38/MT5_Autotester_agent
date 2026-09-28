from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from ubs.account import BROKERS, account_types_for_broker

from run_tests import REPORT_DIR
from ui.ubs_agent_view_cards import UBSAgentViewCardsMixin


class UBSAgentViewMixin(UBSAgentViewCardsMixin):
    def _build_ubs_agent(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        # ── Scrollable wrapper ──────────────────────────────────────────────
        canvas = tk.Canvas(parent, bg=self.colors["bg"], highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")

        inner = ttk.Frame(canvas)
        inner.columnconfigure(0, weight=1)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_resize(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_resize(event):
            canvas.itemconfig(win_id, width=event.width)

        def _on_scroll(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        inner.bind("<Configure>", _on_inner_resize)
        canvas.bind("<Configure>", _on_canvas_resize)
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_scroll))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        # ── Rutas ───────────────────────────────────────────────────────────
        paths = self._card(inner, "Rutas Agente UBS")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        paths.columnconfigure(1, weight=1)
        account_row = ttk.Frame(paths, style="Panel.TFrame")
        account_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=20, pady=7)
        account_row.columnconfigure(5, weight=1)
        ttk.Label(account_row, text="Broker", style="CardDesc.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        broker_combo = ttk.Combobox(
            account_row,
            textvariable=self.ubs_broker,
            values=BROKERS,
            width=12,
            state="readonly",
        )
        broker_combo.grid(row=0, column=1, sticky="w", padx=(0, 14))
        broker_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_ubs_broker_changed())
        ttk.Label(account_row, text="Cuenta", style="CardDesc.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        account_combo = ttk.Combobox(
            account_row,
            textvariable=self.ubs_account_type,
            values=account_types_for_broker(self.ubs_broker.get()),
            width=10,
            state="readonly",
        )
        self.ubs_account_combo = account_combo
        account_combo.grid(row=0, column=3, sticky="w", padx=(0, 14))
        account_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_ubs_account_type_changed())
        ttk.Button(
            account_row,
            text="Ajustar",
            command=self._apply_ubs_account_type_to_app,
        ).grid(row=0, column=4, sticky="w")
        self._path_row(paths, "Archivo .ex5 UBS", self.ubs_ex5_file, 2, self._browse_ex5_file)
        self._path_row(paths, "Carpeta seeds UBS", self.set_files_root, 3, self._browse_dir)
        self._path_row(paths, "Salida Agente UBS", self.ubs_generation_output, 4, self._browse_dir)
        seed_eval_row = ttk.Frame(paths, style="Panel.TFrame")
        seed_eval_row.grid(row=5, column=0, columnspan=3, sticky="ew", padx=20, pady=(10, 18))
        seed_eval_row.columnconfigure(0, weight=1)
        ttk.Label(seed_eval_row, textvariable=self.ubs_seed_eval_summary, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        ttk.Button(
            seed_eval_row,
            text="Evaluar semillas",
            style="Primary.TButton",
            command=self._run_ubs_seed_evaluation,
        ).grid(row=0, column=1, sticky="e")

        # ── Configuracion ───────────────────────────────────────────────────
        agent = self._card(inner, "Configuracion Agente UBS")
        agent.grid(row=1, column=0, sticky="ew")
        for column in (1, 3, 5):
            agent.columnconfigure(column, weight=1)

        gen_fields = [
            ("Generaciones", self.ubs_generation_count, 1, 100),
            ("Variantes por set", self.ubs_variants_per_seed, 1, 100),
            ("Max seeds/gen", self.ubs_max_seeds, 0, 5000),
        ]
        for index, (label, variable, from_value, to_value) in enumerate(gen_fields):
            column = index * 2
            left_pad = 20 if index == 0 else 10
            right_pad = 10 if index < len(gen_fields) - 1 else 20
            ttk.Label(agent, text=label, style="Panel.TLabel").grid(
                row=1, column=column, sticky="w", padx=(left_pad, 10), pady=7
            )
            ttk.Spinbox(agent, from_=from_value, to=to_value, textvariable=variable, width=8).grid(
                row=1, column=column + 1, sticky="ew", padx=(0, right_pad), pady=7
            )

        dates_row = ttk.Frame(agent, style="Panel.TFrame")
        dates_row.grid(row=2, column=0, columnspan=6, sticky="ew", padx=20, pady=(4, 0))
        _date_tip = (
            "Formato: YYYY.MM.DD  (ej. 2020.01.01)\n"
            "Sobreescribe FromDate/ToDate del template para este proceso.\n"
            "Dejar vacío para usar las fechas del template tester."
        )
        ttk.Label(dates_row, text="Desde", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        _from_entry = ttk.Entry(dates_row, textvariable=self.ubs_agent_from_date, width=14)
        _from_entry.grid(row=0, column=1, sticky="w", padx=(0, 4))
        self._tooltip_cls(_from_entry, _date_tip)
        ttk.Label(dates_row, text="Hasta", style="Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(8, 6))
        _to_entry = ttk.Entry(dates_row, textvariable=self.ubs_agent_to_date, width=14)
        _to_entry.grid(row=0, column=3, sticky="w", padx=(0, 12))
        self._tooltip_cls(_to_entry, _date_tip)
        def _fill_agent_dates(*_):
            fd = self.tester_vars.get("FromDate")
            td = self.tester_vars.get("ToDate")
            if fd and not self.ubs_agent_from_date.get().strip():
                self.ubs_agent_from_date.set(fd.get().strip())
            if td and not self.ubs_agent_to_date.get().strip():
                self.ubs_agent_to_date.set(td.get().strip())

        self.after(200, _fill_agent_dates)
        self.template_path.trace_add("write", lambda *_: self.after(300, _fill_agent_dates))

        exec_row = tk.Frame(agent, bg=self.colors["panel"])
        exec_row.grid(row=3, column=0, columnspan=6, sticky="ew", padx=20, pady=(12, 6))
        exec_row.columnconfigure(0, weight=1)
        exec_text = tk.Frame(exec_row, bg=self.colors["panel"])
        exec_text.grid(row=0, column=0, sticky="w")
        tk.Label(exec_text, text="Ejecutar backtests", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(exec_text, text="Activa feedback real; apagado solo genera variantes.", bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w")
        self._toggle_switch_cls(exec_row, variable=self.ubs_agent_execute, bg=self.colors["panel"], width=34, height=18).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        explore_row = tk.Frame(agent, bg=self.colors["panel"])
        explore_row.grid(row=4, column=0, columnspan=6, sticky="ew", padx=20, pady=(6, 6))
        explore_row.columnconfigure(0, weight=1)
        explore_text = tk.Frame(explore_row, bg=self.colors["panel"])
        explore_text.grid(row=0, column=0, sticky="w")
        tk.Label(
            explore_text,
            text="Modo de generacion",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            explore_text,
            text="production prioriza evidencia existente; discovery reserva cobertura sin seed.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w")
        ttk.Combobox(
            explore_row,
            textvariable=self.ubs_generation_mode,
            values=("production", "discovery"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="e", pady=(4, 0))

        long_tf_row = tk.Frame(agent, bg=self.colors["panel"])
        long_tf_row.grid(row=5, column=0, columnspan=6, sticky="ew", padx=20, pady=(6, 6))
        long_tf_row.columnconfigure(0, weight=1)
        long_tf_text = tk.Frame(long_tf_row, bg=self.colors["panel"])
        long_tf_text.grid(row=0, column=0, sticky="w")
        tk.Label(
            long_tf_text,
            text="Experimentar W1/MN",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            long_tf_text,
            text="Incluye semanal y mensual como targets experimentales; apagado en runs normales.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w")
        long_tf_inputs = tk.Frame(long_tf_row, bg=self.colors["panel"])
        long_tf_inputs.grid(row=0, column=1, sticky="e", padx=(10, 10))
        long_tf_fields = [
            ("W1 base", self.ubs_long_tf_min_trades_w1),
            ("MN base", self.ubs_long_tf_min_trades_mn),
            ("W1 FT", self.ubs_final_tick_min_trades_w1),
            ("MN FT", self.ubs_final_tick_min_trades_mn),
        ]
        for index, (label, variable) in enumerate(long_tf_fields):
            ttk.Label(long_tf_inputs, text=label, style="Muted.TLabel").grid(
                row=0,
                column=index * 2,
                sticky="w",
                padx=(0 if index == 0 else 8, 3),
            )
            ttk.Spinbox(long_tf_inputs, from_=0, to=100000, textvariable=variable, width=8).grid(
                row=0,
                column=index * 2 + 1,
                sticky="w",
            )
        self._toggle_switch_cls(
            long_tf_row,
            variable=self.ubs_experimental_long_timeframes,
            bg=self.colors["panel"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="ne", pady=(4, 0))

        self._build_ubs_multiterminal_row(agent, row=6)

        buttons = ttk.Frame(agent, style="Panel.TFrame")
        buttons.grid(row=7, column=0, columnspan=6, sticky="ew", padx=20, pady=(14, 22))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)
        self._rounded_button_cls(
            buttons, text="Guardar config",
            bg=self.colors["primary_container"], hover_bg=self.colors["primary"],
            font=("Segoe UI", 10, "bold"),
            radius=10, padx=14, pady=10,
            parent_bg=self.colors["panel"],
            command=self._save_ubs_agent_clicked,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._rounded_button_cls(
            buttons, text="Lanzar Agente UBS",
            bg=self.colors["accent"], hover_bg=self.colors["accent_hover"],
            font=("Segoe UI", 10, "bold"),
            radius=10, padx=14, pady=10,
            parent_bg=self.colors["panel"],
            command=self._run_ubs_generator,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 6))
        self.ubs_continue_button = self._rounded_button_cls(
            buttons, text="Continuar iteracion",
            bg=self.colors["primary"], fg=self.colors["primary_text"],
            hover_bg=self.colors["primary_container"], hover_fg=self.colors["primary_hover_text"],
            font=("Segoe UI", 10, "bold"),
            radius=10, padx=14, pady=10,
            parent_bg=self.colors["panel"],
            command=self._run_ubs_continue,
        )
        self.ubs_continue_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        ttk.Label(agent, textvariable=self.ubs_continue_status, style="Muted.TLabel").grid(
            row=8, column=0, columnspan=6, sticky="w", padx=20, pady=(0, 14)
        )

        self._build_ubs_agent_filter_cards(inner)
