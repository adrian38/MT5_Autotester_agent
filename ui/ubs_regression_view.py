from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSRegressionViewMixin:
    def _build_ubs_regression(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Prueba regresiva UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        tk.Label(
            bar,
            textvariable=self.ubs_regression_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))
        actions = [
            ("Continuar regresiva", self._run_ubs_regression_for_latest_run, self.colors["accent"], "#ffffff"),
            ("Reprobar", self._rerun_ubs_regression_for_latest_run, self.colors["panel"], self.colors["muted"]),
            ("Aplicar criterios", self._rescore_ubs_regression, self.colors["panel"], self.colors["muted"]),
            ("Guardar config", self._save_config_clicked, self.colors["panel"], self.colors["muted"]),
            ("Actualizar", self._refresh_ubs_regression_panel, self.colors["panel"], self.colors["muted"]),
        ]
        for column, (label, command, bg, fg) in enumerate(actions, start=1):
            tk.Button(
                bar,
                text=label,
                bg=bg,
                fg=fg,
                relief="flat" if column == 1 else "solid",
                borderwidth=0 if column == 1 else 1,
                padx=9,
                pady=5,
                font=("Segoe UI", 9, "bold" if column == 1 else "normal"),
                cursor="hand2",
                command=command,
            ).grid(row=0, column=column, sticky="e", padx=(0, 6), pady=(5, 3))

        row1 = tk.Frame(bar, bg=self.colors["panel_alt"])
        row1.grid(row=1, column=0, columnspan=6, sticky="ew", padx=10, pady=(0, 5))
        row1.columnconfigure(1, weight=1)
        tk.Label(row1, text="Run:", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(
            row=0, column=0, sticky="w", padx=(0, 4)
        )
        self.ubs_regression_run_combo = ttk.Combobox(
            row1, textvariable=self.ubs_regression_run_id, state="readonly", width=58
        )
        self.ubs_regression_run_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.ubs_regression_run_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_regression())
        for column, (label, command) in enumerate(
            [
                ("Abrir set", self._open_selected_ubs_regression_set),
                ("Abrir reporte", self._open_selected_ubs_regression_report),
                ("Manual OK", self._manual_accept_selected_ubs_regression),
                ("Manual FAIL", self._manual_reject_selected_ubs_regression),
            ],
            start=3,
        ):
            tk.Button(
                row1,
                text=label,
                bg=self.colors["panel"],
                fg=self.colors["muted"],
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=5,
                font=("Segoe UI", 9),
                cursor="hand2",
                command=command,
            ).grid(row=0, column=column, sticky="e", padx=(0, 4))
        ttk.Checkbutton(row1, text="Auto despues de 6M", variable=self.ubs_regression_auto).grid(
            row=0, column=7, sticky="e", padx=(8, 0)
        )

        ttk.Label(panel, textvariable=self.ubs_regression_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="ew", padx=20, pady=(6, 6)
        )

        criteria = ttk.Frame(panel, style="Panel.TFrame")
        criteria.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))
        fields = [
            (0, "Desde", self.ubs_regression_from_date, 12),
            (0, "Hasta", self.ubs_regression_to_date, 12),
            (0, "Net >", self.ubs_regression_min_net_profit, 7),
            (0, "PF >=", self.ubs_regression_min_profit_factor, 7),
            (0, "Ops >=", self.ubs_regression_min_trades, 7),
            (1, "DD % <=", self.ubs_regression_max_drawdown_pct, 7),
            (1, "Recovery >=", self.ubs_regression_min_recovery_factor, 7),
            (1, "Meses + >=", self.ubs_regression_min_positive_month_ratio, 7),
            (1, "W1 ops", self.ubs_regression_min_trades_w1, 7),
            (1, "MN ops", self.ubs_regression_min_trades_mn, 7),
            (2, "Puntos OK", self.ubs_regression_positive_points, 7),
            (2, "Puntos FAIL", self.ubs_regression_negative_points, 7),
        ]
        counts = {0: 0, 1: 0, 2: 0}
        for row, label, variable, width in fields:
            counts[row] += 1
            column = counts[row] * 2 - 2
            ttk.Label(criteria, text=label, style="Muted.TLabel").grid(
                row=row, column=column, sticky="w", padx=(0, 4), pady=2
            )
            ttk.Entry(criteria, textvariable=variable, width=width).grid(
                row=row, column=column + 1, sticky="w", padx=(0, 12), pady=2
            )
        ttk.Label(
            criteria,
            text="Model=1 (OHLC 1 minuto). Errores de historico, fecha o reporte son neutros: 0 puntos.",
            style="Muted.TLabel",
        ).grid(row=2, column=4, columnspan=8, sticky="w", padx=(8, 0))

        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = (
            "mark", "run", "id", "gen", "status", "cause", "points", "symbol", "period",
            "score", "net", "pf", "dd", "trades", "recovery", "positive_months", "dates", "set",
        )
        self.ubs_regression_tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=12, selectmode="extended"
        )
        headings = {
            "mark": "SEL", "run": "RUN", "id": "ID", "gen": "GEN", "status": "REGRESIVA", "cause": "CAUSA",
            "points": "PUNTOS", "symbol": "SYMBOL", "period": "TF", "score": "SCORE",
            "net": "NET", "pf": "PF", "dd": "DD %", "trades": "TRADES", "recovery": "REC",
            "positive_months": "MESES +", "dates": "FECHAS", "set": "SET",
        }
        widths = {
            "mark": 48, "run": 50, "id": 58, "gen": 44, "status": 100, "cause": 250, "points": 72,
            "symbol": 90, "period": 52, "score": 78, "net": 82, "pf": 68, "dd": 68,
            "trades": 70, "recovery": 68, "positive_months": 82, "dates": 180, "set": 500,
        }
        for column in columns:
            self.ubs_regression_tree.heading(column, text=headings[column])
            self.ubs_regression_tree.column(
                column, width=widths[column], minwidth=42, anchor="center", stretch=False
            )
        self.ubs_regression_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_regression_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_regression_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_regression_tree)
        self.ubs_regression_tree.bind("<Button-1>", self._on_ubs_regression_tree_click)
        self.ubs_regression_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_regression_report())
        self._attach_tree_scrollbars(table_frame, self.ubs_regression_tree, 0, vertical=True)
