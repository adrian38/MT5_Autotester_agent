from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSRobustnessViewMixin:
    def _build_ubs_robustness(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Robustez OOS UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        tk.Label(
            bar,
            textvariable=self.ubs_robust_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))
        tk.Button(
            bar,
            text="Continuar robustez",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._run_ubs_robustness_for_latest_run,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            bar,
            text="Reprobar robustez",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._rerun_ubs_robustness_for_latest_run,
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            bar,
            text="Continuar Final Tick",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._run_ubs_final_tick_for_latest_run,
        ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            bar,
            text="Actualizar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._refresh_ubs_robustness_panel,
        ).grid(row=0, column=4, sticky="e", padx=(0, 10), pady=(5, 3))

        row1 = tk.Frame(bar, bg=self.colors["panel_alt"])
        row1.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 5))
        row1.columnconfigure(2, weight=1)
        tk.Label(
            row1,
            text="Run:",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.ubs_robust_run_combo = ttk.Combobox(
            row1,
            textvariable=self.ubs_robust_run_id,
            state="readonly",
            width=36,
        )
        self.ubs_robust_run_combo.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.ubs_robust_run_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_robustness())
        tk.Button(
            row1,
            text="Abrir set",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_robust_set,
        ).grid(row=0, column=3, sticky="e", padx=(0, 4))
        tk.Button(
            row1,
            text="Abrir reporte OOS",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_robust_report,
        ).grid(row=0, column=4, sticky="e", padx=(0, 4))

        ttk.Label(panel, textvariable=self.ubs_robust_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=20, pady=(4, 4)
        )

        crit = ttk.Frame(panel, style="Panel.TFrame")
        crit.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))
        ttk.Label(crit, text="Criterios robustez:", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        fields = [
            ("Net >", self.ubs_robust_pass_min_net_profit),
            ("PF >=", self.ubs_robust_pass_min_profit_factor),
            ("Trades >=", self.ubs_robust_pass_min_trades),
            ("DD <= %", self.ubs_robust_pass_max_drawdown_pct),
            ("Recovery >=", self.ubs_robust_pass_min_recovery_factor),
            ("Bonus OK", self.ubs_robust_positive_bonus),
            ("Bonus FAIL", self.ubs_robust_negative_bonus),
        ]
        for col, (label, var) in enumerate(fields, start=1):
            ttk.Label(crit, text=label, style="Muted.TLabel").grid(row=0, column=col * 2 - 1, sticky="w", padx=(0, 4))
            ttk.Entry(crit, textvariable=var, width=8, state="readonly").grid(
                row=0, column=col * 2, sticky="w", padx=(0, 12)
            )

        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = (
            "mark", "run", "id", "gen", "status", "cause", "symbol", "period", "train_score",
            "robust_score", "bonus", "profit", "profit_norm", "pf", "dd", "trades", "dates", "set",
        )
        self.ubs_robust_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10, selectmode="extended")
        headings = {
            "mark": "SEL",
            "run": "RUN",
            "id": "ID",
            "gen": "GEN",
            "status": "ROBUST",
            "cause": "CAUSA",
            "symbol": "SYMBOL",
            "period": "TF",
            "train_score": "SCORE GEN",
            "robust_score": "SCORE OOS",
            "bonus": "BONUS",
            "profit": "NET OOS",
            "profit_norm": "NET NORM",
            "pf": "PF",
            "dd": "DD %",
            "trades": "TRADES",
            "dates": "FECHAS",
            "set": "SET",
        }
        widths = {
            "mark": 48,
            "run": 50,
            "id": 58,
            "gen": 44,
            "status": 96,
            "cause": 210,
            "symbol": 90,
            "period": 52,
            "train_score": 82,
            "robust_score": 82,
            "bonus": 68,
            "profit": 84,
            "profit_norm": 92,
            "pf": 66,
            "dd": 66,
            "trades": 68,
            "dates": 170,
            "set": 260,
        }
        for column in columns:
            self.ubs_robust_tree.heading(column, text=headings[column])
            self.ubs_robust_tree.column(column, width=widths[column], minwidth=42, anchor="center", stretch=False)
        self.ubs_robust_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_robust_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_robust_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_robust_tree)
        self.ubs_robust_tree.bind("<Button-1>", self._on_ubs_robust_tree_click)
        self.ubs_robust_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_robust_report())
        self._attach_tree_scrollbars(table_frame, self.ubs_robust_tree, 0)
