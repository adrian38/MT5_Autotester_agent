from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from ubs.account import ACCOUNT_TYPES

from run_tests import REPORT_DIR


class UBSAgentViewMixin:
    def _build_ubs_agent(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        # ── Scrollable wrapper ──────────────────────────────────────────────
        canvas = tk.Canvas(parent, bg=self.colors["bg"], highlightthickness=0, bd=0)
        vscroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.grid(row=0, column=1, sticky="ns")
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
        ttk.Label(paths, text="Tipo de cuenta", style="CardDesc.TLabel").grid(
            row=1, column=0, sticky="w", padx=20, pady=7
        )
        account_combo = ttk.Combobox(
            paths,
            textvariable=self.ubs_account_type,
            values=ACCOUNT_TYPES,
            width=10,
            state="readonly",
        )
        account_combo.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=7)
        account_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_ubs_account_type_changed())
        ttk.Button(
            paths,
            text="Ajustar cuenta",
            command=self._apply_ubs_account_type_to_app,
        ).grid(row=1, column=2, sticky="w", padx=(0, 20), pady=7)
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
            text="Poblar universo sin seed",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            explore_text,
            text="Reserva exploracion para activos/TF del universo que no existen en las seeds actuales.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w")
        self._toggle_switch_cls(
            explore_row,
            variable=self.ubs_force_unseeded_universe,
            bg=self.colors["panel"],
            width=34,
            height=18,
        ).grid(row=0, column=1, sticky="ne", pady=(4, 0))

        self._build_ubs_multiterminal_row(agent, row=5)

        buttons = ttk.Frame(agent, style="Panel.TFrame")
        buttons.grid(row=6, column=0, columnspan=6, sticky="ew", padx=20, pady=(14, 22))
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
            row=7, column=0, columnspan=6, sticky="w", padx=20, pady=(0, 14)
        )

        # ── Filtros ─────────────────────────────────────────────────────────
        pass_config = self._card(inner, "Filtros de aceptacion")
        pass_config.grid(row=2, column=0, sticky="ew", pady=(16, 24))
        for column in (1, 3, 5):
            pass_config.columnconfigure(column, weight=1)
        pass_fields = [
            ("Profit neto min", self.ubs_pass_min_net_profit, "entry"),
            ("Profit factor min", self.ubs_pass_min_profit_factor, "entry"),
            ("Trades min", self.ubs_pass_min_trades, "spin"),
            ("DD max %", self.ubs_pass_max_drawdown_pct, "entry"),
            ("Recovery min", self.ubs_pass_min_recovery_factor, "entry"),
        ]
        for index, (label, variable, kind) in enumerate(pass_fields):
            row = 1 + index // 3
            column = (index % 3) * 2
            left_pad = 20 if column == 0 else 10
            right_pad = 10 if column < 4 else 20
            ttk.Label(pass_config, text=label, style="Panel.TLabel").grid(
                row=row, column=column, sticky="w", padx=(left_pad, 10), pady=7
            )
            if kind == "spin":
                ttk.Spinbox(pass_config, from_=0, to=100000, textvariable=variable, width=8).grid(
                    row=row, column=column + 1, sticky="ew", padx=(0, right_pad), pady=7
                )
            else:
                ttk.Entry(pass_config, textvariable=variable).grid(
                    row=row, column=column + 1, sticky="ew", padx=(0, right_pad), pady=7
                )
        ttk.Label(
            pass_config,
            text="Profit neto min es moneda de la cuenta. Con deposito 1000, default 100 = 10%. Estabilidad mensual: score, no filtro hard.",
            style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=5, sticky="w", padx=20, pady=(4, 14))
        ttk.Button(
            pass_config,
            text="Guardar configuracion Agente UBS",
            style="Primary.TButton",
            command=self._save_ubs_agent_clicked,
        ).grid(row=3, column=5, sticky="e", padx=20, pady=(4, 14))

        robust = self._card(inner, "Robustez OOS")
        robust.grid(row=3, column=0, sticky="ew", pady=(0, 24))
        for column in (1, 3, 5):
            robust.columnconfigure(column, weight=1)

        robust_date_tip = (
            "Formato: YYYY.MM.DD.\n"
            "Ventana fuera de muestra para candidatos accepted del agente.\n"
            "Dejar vacio para usar las fechas del template tester."
        )
        ttk.Label(robust, text="Desde", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(20, 10), pady=7
        )
        robust_from = ttk.Entry(robust, textvariable=self.ubs_robust_from_date, width=14)
        robust_from.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=7)
        self._tooltip_cls(robust_from, robust_date_tip)
        ttk.Label(robust, text="Hasta", style="Panel.TLabel").grid(
            row=1, column=2, sticky="w", padx=(10, 10), pady=7
        )
        robust_to = ttk.Entry(robust, textvariable=self.ubs_robust_to_date, width=14)
        robust_to.grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=7)
        self._tooltip_cls(robust_to, robust_date_tip)

        auto_row = tk.Frame(robust, bg=self.colors["panel"])
        auto_row.grid(row=1, column=4, columnspan=2, sticky="ew", padx=(10, 20), pady=7)
        auto_row.columnconfigure(0, weight=1)
        tk.Label(
            auto_row,
            text="Auto robustez",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self._toggle_switch_cls(
            auto_row,
            variable=self.ubs_robust_auto,
            bg=self.colors["panel"],
            width=34,
            height=18,
        ).grid(row=0, column=1, sticky="e")

        robust_fields = [
            ("Net min", self.ubs_robust_pass_min_net_profit, "entry"),
            ("PF min", self.ubs_robust_pass_min_profit_factor, "entry"),
            ("Trades min", self.ubs_robust_pass_min_trades, "spin"),
            ("DD max %", self.ubs_robust_pass_max_drawdown_pct, "entry"),
            ("Recovery min", self.ubs_robust_pass_min_recovery_factor, "entry"),
            ("Bonus OK", self.ubs_robust_positive_bonus, "entry"),
            ("Bonus FAIL", self.ubs_robust_negative_bonus, "entry"),
        ]
        for index, (label, variable, kind) in enumerate(robust_fields):
            row = 2 + index // 3
            column = (index % 3) * 2
            left_pad = 20 if column == 0 else 10
            ttk.Label(robust, text=label, style="Panel.TLabel").grid(
                row=row, column=column, sticky="w", padx=(left_pad, 10), pady=7
            )
            if kind == "spin":
                ttk.Spinbox(robust, from_=0, to=100000, textvariable=variable, width=8).grid(
                    row=row, column=column + 1, sticky="ew", padx=(0, 10), pady=7
                )
            else:
                ttk.Entry(robust, textvariable=variable, width=8).grid(
                    row=row, column=column + 1, sticky="ew", padx=(0, 10 if column < 4 else 20), pady=7
                )

        ttk.Label(
            robust,
            text="Solo los candidatos accepted del agente pasan a OOS. Accepted suma bonus; rejected suma bonus negativo; sin reporte/mismatch queda neutro.",
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=5, sticky="w", padx=20, pady=(4, 14))
        ttk.Button(
            robust,
            text="Guardar robustez",
            style="Primary.TButton",
            command=self._save_ubs_agent_clicked,
        ).grid(row=5, column=5, sticky="e", padx=20, pady=(4, 14))
