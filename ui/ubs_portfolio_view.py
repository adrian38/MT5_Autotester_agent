from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSPortfolioViewMixin:
    def _build_ubs_portfolio(self, parent: ttk.Frame) -> None:
        colors = self.colors
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Portafolio UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(6, weight=1)

        # --- Barra de inputs --------------------------------------------------------
        inputs = tk.Frame(panel, bg=colors["panel_alt"])
        inputs.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 6))
        for col in range(12):
            inputs.columnconfigure(col, weight=0)

        def _label(text, col):
            tk.Label(inputs, text=text, bg=colors["panel_alt"], fg=colors["muted"],
                     font=("Segoe UI", 9)).grid(row=0, column=col, sticky="w", padx=(10 if col == 0 else 6, 4), pady=8)

        _label("Nº símbolos", 0)
        ttk.Spinbox(inputs, from_=1, to=50, width=5, textvariable=self.ubs_portfolio_num_symbols).grid(row=0, column=1, sticky="w", pady=8)
        _label("Tipo", 2)
        self.ubs_portfolio_type_combo = ttk.Combobox(
            inputs, textvariable=self.ubs_portfolio_type, state="readonly", width=12,
            values=("Conservador", "Equilibrado", "Agresivo"),
        )
        self.ubs_portfolio_type_combo.grid(row=0, column=3, sticky="w", pady=8)
        _label("DD valle %", 4)
        ttk.Entry(inputs, textvariable=self.ubs_portfolio_valley_pct, width=7).grid(row=0, column=5, sticky="w", pady=8)
        _label("DD puntual %", 6)
        ttk.Entry(inputs, textvariable=self.ubs_portfolio_point_pct, width=7).grid(row=0, column=7, sticky="w", pady=8)
        _label("Capital", 8)
        ttk.Entry(inputs, textvariable=self.ubs_portfolio_capital, width=10).grid(row=0, column=9, sticky="w", pady=8)

        generate_btn = tk.Button(
            inputs, text="Generar portafolio", bg=colors["accent"], fg="#ffffff",
            relief="flat", borderwidth=0, padx=12, pady=6, font=("Segoe UI", 9, "bold"),
            cursor="hand2", command=self._run_ubs_portfolio_build,
        )
        generate_btn.grid(row=0, column=10, sticky="e", padx=(12, 6), pady=8)
        refresh_btn = tk.Button(
            inputs, text="Actualizar", bg=colors["panel"], fg=colors["muted"],
            relief="solid", borderwidth=1, padx=8, pady=6, font=("Segoe UI", 9),
            cursor="hand2", command=self._refresh_ubs_portfolios,
        )
        refresh_btn.grid(row=0, column=11, sticky="e", padx=(0, 10), pady=8)
        self.ubs_portfolio_buttons = [generate_btn, refresh_btn]

        # --- Progress + estado ------------------------------------------------------
        self.ubs_portfolio_progress = ttk.Progressbar(panel, mode="indeterminate")
        self.ubs_portfolio_progress.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 4))
        ttk.Label(panel, textvariable=self.ubs_portfolio_status, style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", padx=20, pady=(0, 6)
        )

        # --- Tira de métricas totales ----------------------------------------------
        metrics = ttk.Frame(panel, style="Panel.TFrame")
        metrics.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 8))
        for col in range(4):
            metrics.columnconfigure(col, weight=1)
        self._metric(metrics, 0, "Net profit total", self.ubs_portfolio_metric_net)
        self._metric(metrics, 1, "DD valle real", self.ubs_portfolio_metric_valley)
        self._metric(metrics, 2, "DD puntual real", self.ubs_portfolio_metric_point)
        self._metric(metrics, 3, "Estrategias", self.ubs_portfolio_metric_count)

        # --- Portafolios guardados --------------------------------------------------
        saved_bar = tk.Frame(panel, bg=colors["panel_alt"])
        saved_bar.grid(row=5, column=0, sticky="ew", padx=20, pady=(2, 2))
        saved_bar.columnconfigure(0, weight=1)
        tk.Label(saved_bar, text="Portafolios guardados", bg=colors["panel_alt"],
                 fg=colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=4)
        tk.Button(
            saved_bar, text="Exportar sets", bg=colors["accent"], fg="#ffffff",
            relief="flat", borderwidth=0, padx=10, pady=4, font=("Segoe UI", 9, "bold"),
            cursor="hand2", command=self._export_ubs_portfolio_sets,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            saved_bar, text="Borrar", bg=colors["panel"], fg=colors["danger"],
            relief="solid", borderwidth=1, padx=8, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._delete_selected_ubs_portfolio,
        ).grid(row=0, column=2, sticky="e", padx=(0, 10), pady=4)

        saved_frame = ttk.Frame(panel, style="Panel.TFrame")
        saved_frame.grid(row=6, column=0, sticky="nsew", padx=20, pady=(0, 6))
        saved_frame.columnconfigure(0, weight=1)
        saved_frame.rowconfigure(0, weight=1)
        saved_columns = ("id", "created", "type", "symbols", "capital", "net", "valley", "point", "binding")
        self.ubs_portfolio_saved_tree = ttk.Treeview(
            saved_frame, columns=saved_columns, show="headings", height=6, selectmode="browse"
        )
        saved_headings = {
            "id": "ID", "created": "CREADO", "type": "TIPO", "symbols": "SÍMB.",
            "capital": "CAPITAL", "net": "NET TOTAL", "valley": "DD VALLE",
            "point": "DD PUNTUAL", "binding": "TOPE",
        }
        saved_widths = {
            "id": 46, "created": 130, "type": 96, "symbols": 56, "capital": 90,
            "net": 100, "valley": 90, "point": 90, "binding": 80,
        }
        for column in saved_columns:
            self.ubs_portfolio_saved_tree.heading(column, text=saved_headings[column])
            self.ubs_portfolio_saved_tree.column(column, width=saved_widths[column], minwidth=42, anchor="center", stretch=False)
        self._make_tree_sortable(self.ubs_portfolio_saved_tree)
        self.ubs_portfolio_saved_tree.bind("<<TreeviewSelect>>", self._on_ubs_portfolio_select)
        self._attach_tree_scrollbars(saved_frame, self.ubs_portfolio_saved_tree, 0)

        # --- Detalle por estrategia -------------------------------------------------
        tk.Label(panel, text="Estrategias del portafolio (doble clic: abrir reporte OOS)",
                 bg=colors["panel"], fg=colors["text"], font=("Segoe UI", 10, "bold")).grid(
            row=7, column=0, sticky="w", padx=20, pady=(4, 2)
        )
        members_frame = ttk.Frame(panel, style="Panel.TFrame")
        members_frame.grid(row=8, column=0, sticky="nsew", padx=20, pady=(0, 18))
        members_frame.columnconfigure(0, weight=1)
        members_frame.rowconfigure(0, weight=1)
        panel.rowconfigure(8, weight=2)
        member_columns = ("symbol", "period", "lot", "step", "mult", "dd", "quality", "net", "set")
        self.ubs_portfolio_members_tree = ttk.Treeview(
            members_frame, columns=member_columns, show="headings", height=10, selectmode="browse"
        )
        member_headings = {
            "symbol": "SÍMBOLO", "period": "TF", "lot": "LOTE", "step": "$/0.01 (Risk2)",
            "mult": "MULT.", "dd": "DD EQUITY", "quality": "CALIDAD", "net": "NET", "set": "SET",
        }
        member_widths = {
            "symbol": 100, "period": 52, "lot": 70, "step": 110, "mult": 70,
            "dd": 90, "quality": 90, "net": 100, "set": 300,
        }
        for column in member_columns:
            self.ubs_portfolio_members_tree.heading(column, text=member_headings[column])
            self.ubs_portfolio_members_tree.column(column, width=member_widths[column], minwidth=42, anchor="center", stretch=False)
        self._make_tree_sortable(self.ubs_portfolio_members_tree)
        self.ubs_portfolio_members_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_portfolio_member())
        self._attach_tree_scrollbars(members_frame, self.ubs_portfolio_members_tree, 0)
