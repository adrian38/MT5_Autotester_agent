from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSFinalTickViewMixin:
    def _build_ubs_final_tick(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Final Tick UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        tk.Label(
            bar,
            textvariable=self.ubs_final_tick_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))
        tk.Button(
            bar,
            text="Continuar Final Tick",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._run_ubs_final_tick_for_latest_run,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            bar,
            text="Reprobar Final Tick",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._rerun_ubs_final_tick_for_latest_run,
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            bar,
            text="Guardar config",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._save_config_clicked,
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
            command=self._refresh_ubs_final_tick_panel,
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
        self.ubs_final_tick_run_combo = ttk.Combobox(
            row1,
            textvariable=self.ubs_final_tick_run_id,
            state="readonly",
            width=36,
        )
        self.ubs_final_tick_run_combo.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.ubs_final_tick_run_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_final_tick())
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
            command=self._open_selected_ubs_final_tick_set,
        ).grid(row=0, column=3, sticky="e", padx=(0, 4))
        tk.Button(
            row1,
            text="Abrir OHLC",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_final_tick_ohlc_report,
        ).grid(row=0, column=4, sticky="e", padx=(0, 4))
        tk.Button(
            row1,
            text="Abrir Real Tick",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_final_tick_real_report,
        ).grid(row=0, column=5, sticky="e", padx=(0, 4))

        ttk.Label(panel, textvariable=self.ubs_final_tick_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=20, pady=(4, 4)
        )

        crit = ttk.Frame(panel, style="Panel.TFrame")
        crit.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))
        fields = [
            (0, "Desde", self.ubs_final_tick_from_date, 14),
            (0, "Hasta", self.ubs_final_tick_to_date, 14),
            (0, "Calidad >", self.ubs_final_tick_min_history_quality, 8),
            (0, "Net delta %", self.ubs_final_tick_max_net_delta_pct, 8),
            (1, "PF delta %", self.ubs_final_tick_max_pf_delta_pct, 8),
            (1, "DD delta %", self.ubs_final_tick_max_dd_delta_pct, 8),
            (1, "Trades delta %", self.ubs_final_tick_max_trades_delta_pct, 8),
        ]
        ttk.Label(crit, text="Final Tick:", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        per_row_counts = {0: 0, 1: 0}
        for row, label, var, width in fields:
            per_row_counts[row] += 1
            col_index = per_row_counts[row]
            ttk.Label(crit, text=label, style="Muted.TLabel").grid(
                row=row, column=col_index * 2 - 1, sticky="w", padx=(0, 4), pady=(0, 2)
            )
            ttk.Entry(crit, textvariable=var, width=width).grid(
                row=row, column=col_index * 2, sticky="w", padx=(0, 12), pady=(0, 2)
            )

        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = (
            "mark", "run", "id", "gen", "status", "cause", "symbol", "period",
            "quality", "ohlc_score", "tick_score", "net_ohlc", "net_tick",
            "pf_ohlc", "pf_tick", "dd_ohlc", "dd_tick", "trades_ohlc",
            "trades_tick", "dates", "set",
        )
        self.ubs_final_tick_tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=10, selectmode="extended"
        )
        headings = {
            "mark": "SEL",
            "run": "RUN",
            "id": "ID",
            "gen": "GEN",
            "status": "FINAL",
            "cause": "CAUSA",
            "symbol": "SYMBOL",
            "period": "TF",
            "quality": "CALIDAD",
            "ohlc_score": "SCORE OHLC",
            "tick_score": "SCORE TICK",
            "net_ohlc": "NET OHLC",
            "net_tick": "NET TICK",
            "pf_ohlc": "PF OHLC",
            "pf_tick": "PF TICK",
            "dd_ohlc": "DD OHLC",
            "dd_tick": "DD TICK",
            "trades_ohlc": "TR OHLC",
            "trades_tick": "TR TICK",
            "dates": "FECHAS",
            "set": "SET",
        }
        widths = {
            "mark": 48,
            "run": 50,
            "id": 58,
            "gen": 44,
            "status": 96,
            "cause": 230,
            "symbol": 90,
            "period": 52,
            "quality": 78,
            "ohlc_score": 88,
            "tick_score": 88,
            "net_ohlc": 84,
            "net_tick": 84,
            "pf_ohlc": 72,
            "pf_tick": 72,
            "dd_ohlc": 72,
            "dd_tick": 72,
            "trades_ohlc": 72,
            "trades_tick": 72,
            "dates": 170,
            "set": 260,
        }
        for column in columns:
            self.ubs_final_tick_tree.heading(column, text=headings[column])
            self.ubs_final_tick_tree.column(column, width=widths[column], minwidth=42, anchor="center", stretch=False)
        self.ubs_final_tick_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_final_tick_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_final_tick_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_final_tick_tree)
        self.ubs_final_tick_tree.bind("<Button-1>", self._on_ubs_final_tick_tree_click)
        self.ubs_final_tick_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_final_tick_real_report())
        self._attach_tree_scrollbars(table_frame, self.ubs_final_tick_tree, 0)
