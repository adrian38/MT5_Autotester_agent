from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class UBSSeedsViewMixin:
    def _build_ubs_seeds(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        card = self._card(parent, "Semillas UBS")
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(4, weight=1)

        # ── Barra de acción principal (Tipo B — panel_alt) ──────────────
        toolbar = tk.Frame(card, bg=self.colors["panel_alt"])
        toolbar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        toolbar.columnconfigure(0, weight=1)

        # Fila 0: resumen + acciones principales
        tk.Label(toolbar, textvariable=self.ubs_seed_eval_summary,
                 bg=self.colors["panel_alt"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(toolbar, text="⬆  Importar seeds",
                  bg=self.colors["primary_container"], fg=self.colors["primary_hover_text"],
                  relief="flat", borderwidth=0,
                  padx=10, pady=5, font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._import_ubs_seeds).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=5)
        tk.Button(toolbar, text="Evaluar semillas",
                  bg=self.colors["accent"], fg="#ffffff", relief="flat", borderwidth=0,
                  padx=10, pady=5, font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._run_ubs_seed_evaluation).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=5)
        for col, (label, cmd) in enumerate([
            ("Abrir seed",      self._open_selected_ubs_seed),
            ("Abrir reporte",   self._open_selected_ubs_seed_report),
            ("Repetir backtest",self._retry_selected_ubs_seed),
            ("Guardar Symbol/TF", self._save_ubs_seed_override),
            ("Actualizar",      self._refresh_ubs_seeds_panel),
        ], start=3):
            padx = (0, 10) if col == 7 else (0, 6)
            tk.Button(toolbar, text=label,
                      bg=self.colors["panel"], fg=self.colors["muted"],
                      relief="solid", borderwidth=1, padx=8, pady=5,
                      font=("Segoe UI", 9), cursor="hand2", command=cmd,
                      ).grid(row=0, column=col, sticky="e", padx=padx, pady=5)

        # Fila 1: acciones destructivas (derecha)
        danger_frame = tk.Frame(toolbar, bg=self.colors["panel_alt"])
        danger_frame.grid(row=1, column=0, columnspan=8, sticky="e", pady=(0, 5))
        for col, (label, cmd) in enumerate([
            ("Eliminar seed",        self._delete_selected_ubs_seed),
            ("Eliminar rechazadas",  self._delete_rejected_ubs_seeds),
            ("Eliminar todas",       self._delete_all_ubs_seeds),
            ("Resetear evaluación",  self._reset_ubs_seed_evaluation),
        ]):
            padx = (0, 10) if col == 3 else (0, 6)
            tk.Button(danger_frame, text=label,
                      bg=self.colors["danger"], fg="#ffffff",
                      relief="flat", borderwidth=0, padx=8, pady=5,
                      font=("Segoe UI", 9, "bold"), cursor="hand2", command=cmd,
                      ).grid(row=0, column=col, sticky="e", padx=padx)

        criteria_bar = ttk.Frame(card, style="Panel.TFrame")
        criteria_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
        for column in (2, 4, 6, 8, 10):
            criteria_bar.columnconfigure(column, weight=1)
        ttk.Label(criteria_bar, text="Criterios Seeds:", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        criteria_items = [
            ("Net profit >", self.ubs_seed_pass_min_net_profit, "entry"),
            ("PF >=", self.ubs_seed_pass_min_profit_factor, "entry"),
            ("Trades >=", self.ubs_seed_pass_min_trades, "spin"),
            ("DD <=", self.ubs_seed_pass_max_drawdown_pct, "entry"),
            ("Recovery >=", self.ubs_seed_pass_min_recovery_factor, "entry"),
        ]
        for col, (label, var, kind) in enumerate(criteria_items, start=1):
            label_col = col * 2 - 1
            field_col = col * 2
            ttk.Label(criteria_bar, text=label, style="Muted.TLabel").grid(row=0, column=label_col, sticky="w", padx=(0, 4))
            if kind == "spin":
                ttk.Spinbox(criteria_bar, from_=0, to=100000, textvariable=var, width=8).grid(
                    row=0, column=field_col, sticky="ew", padx=(0, 10)
                )
            else:
                ttk.Entry(criteria_bar, textvariable=var, width=8).grid(
                    row=0, column=field_col, sticky="ew", padx=(0, 10)
                )
        ttk.Button(
            criteria_bar,
            text="Guardar",
            style="TButton",
            command=self._save_seed_criteria_clicked,
        ).grid(row=0, column=11, sticky="e", padx=(0, 8))
        ttk.Button(
            criteria_bar,
            text="Aplicar criterios",
            style="Primary.TButton",
            command=self._apply_seed_criteria_clicked,
        ).grid(row=0, column=12, sticky="e")

        _seed_date_tip = (
            "Formato: YYYY.MM.DD  (ej. 2020.01.01)\n"
            "Sobreescribe FromDate/ToDate del template solo para la evaluacion de seeds.\n"
            "Dejar vacío para usar las fechas del template tester."
        )
        dates_bar = ttk.Frame(card, style="Panel.TFrame")
        dates_bar.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 4))
        ttk.Label(dates_bar, text="Fechas Seeds:", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(dates_bar, text="Desde", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 4))
        _seed_from_e = ttk.Entry(dates_bar, textvariable=self.ubs_seed_from_date, width=14)
        _seed_from_e.grid(row=0, column=2, sticky="w", padx=(0, 16))
        self._tooltip_cls(_seed_from_e, _seed_date_tip)
        ttk.Label(dates_bar, text="Hasta", style="Muted.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 4))
        _seed_to_e = ttk.Entry(dates_bar, textvariable=self.ubs_seed_to_date, width=14)
        _seed_to_e.grid(row=0, column=4, sticky="w", padx=(0, 12))
        self._tooltip_cls(_seed_to_e, _seed_date_tip)
        def _fill_seed_dates(*_):
            fd = self.tester_vars.get("FromDate")
            td = self.tester_vars.get("ToDate")
            if fd and not self.ubs_seed_from_date.get().strip():
                self.ubs_seed_from_date.set(fd.get().strip())
            if td and not self.ubs_seed_to_date.get().strip():
                self.ubs_seed_to_date.set(td.get().strip())

        self.after(200, _fill_seed_dates)
        self.template_path.trace_add("write", lambda *_: self.after(300, _fill_seed_dates))
        ttk.Button(
            dates_bar,
            text="Guardar",
            style="TButton",
            command=self._save_seed_criteria_clicked,
        ).grid(row=0, column=6, sticky="e")

        table_frame = ttk.Frame(card, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 10))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("mark", "status", "symbol", "period", "score", "accepted", "override", "reason", "seed")
        self.ubs_seeds_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=12,
            selectmode="extended",
        )
        headings = {
            "mark": "SEL",
            "status": "ESTADO",
            "symbol": "SYMBOL",
            "period": "TF",
            "score": "SCORE",
            "accepted": "OK",
            "override": "OVERRIDE",
            "reason": "MOTIVO",
            "seed": "SEED",
        }
        widths = {"mark": 48, "status": 125, "symbol": 90, "period": 60, "score": 90, "accepted": 70, "override": 90, "reason": 220, "seed": 500}
        for column in columns:
            self.ubs_seeds_tree.heading(column, text=headings[column])
            self.ubs_seeds_tree.column(column, width=widths[column], minwidth=50, stretch=False, anchor="center")
        self._make_tree_sortable(self.ubs_seeds_tree)
        self._attach_tree_scrollbars(table_frame, self.ubs_seeds_tree, 0)
        self.ubs_seeds_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_seeds_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_seeds_tree.tag_configure("pending", foreground=self.colors["muted"])
        self.ubs_seeds_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_ubs_seed_select())
        self.ubs_seeds_tree.bind("<Button-1>", self._on_ubs_seed_tree_click)
        self.ubs_seeds_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_seed_report())

        editor = ttk.Frame(card, style="Panel.TFrame")
        editor.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 18))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        ttk.Label(editor, textvariable=self.ubs_seed_detail, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8)
        )
        ttk.Label(editor, text="Symbol correcto", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(editor, textvariable=self.ubs_seed_override_symbol, width=18).grid(
            row=1, column=1, sticky="ew", padx=(0, 16)
        )
        ttk.Label(editor, text="Timeframe correcto", style="Panel.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8))
        ttk.Combobox(
            editor,
            textvariable=self.ubs_seed_override_period,
            values=("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"),
            width=10,
            state="readonly",
        ).grid(row=1, column=3, sticky="w", padx=(0, 16))
        ttk.Button(editor, text="Guardar override", style="Primary.TButton", command=self._save_ubs_seed_override).grid(
            row=1, column=4, sticky="e"
        )

