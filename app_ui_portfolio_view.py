from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class PortfolioViewMixin:
    def _build_portfolio(self, parent: ttk.Frame) -> None:
        colors = self.colors
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        panel = self._card(parent, "Portfolio Manager")
        panel.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="Carpeta de reportes", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(panel, textvariable=self.portfolio_input).grid(row=1, column=1, sticky="ew", pady=7)
        ttk.Button(panel, text="Elegir", style="Tool.TButton", command=self._browse_portfolio_input).grid(row=1, column=2, padx=(8, 20), pady=7)

        ttk.Label(panel, textvariable=self.portfolio_count, style="Muted.TLabel").grid(row=2, column=1, sticky="w", pady=(0, 10))
        ttk.Button(panel, text="Recontar", style="Tool.TButton", command=self._refresh_portfolio_count).grid(row=2, column=2, sticky="e", padx=(8, 20), pady=(0, 10))

        ttk.Label(panel, text="Excel de salida", style="Panel.TLabel").grid(row=3, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(panel, textvariable=self.portfolio_output).grid(row=3, column=1, sticky="ew", pady=7)
        ttk.Button(panel, text="Guardar como", style="Tool.TButton", command=self._browse_portfolio_output).grid(row=3, column=2, padx=(8, 20), pady=7)

        ttk.Label(panel, text="Umbral DD diario", style="Panel.TLabel").grid(row=4, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(panel, textvariable=self.portfolio_threshold, width=12).grid(row=4, column=1, sticky="w", pady=7)
        ttk.Label(panel, text="Ejemplo: 50 para filtrar <= -50", style="Muted.TLabel").grid(row=4, column=1, sticky="w", padx=(110, 0), pady=7)

        actions = self._card(parent, "Generadores Excel")
        actions.grid(row=1, column=0, sticky="nsew")
        for column in range(3):
            actions.columnconfigure(column, weight=1)

        self.portfolio_buttons = []
        specs = [
            ("ALL_STRATEGIES", "Genera el workbook base de estrategias.", "all"),
            ("ALL_STRATEGIES_DD", "Genera hojas de drawdown por estrategia.", "dd"),
            ("PORTFOLIO_DD", "Genera desglose de drawdown del portfolio.", "portfolio_dd"),
            ("DD_VALLE_TOTAL", "Calcula drawdown de valles del portfolio.", "portfolio_valley"),
            ("5 PEORES VALLES", "Exporta los peores valles del portfolio.", "top_valleys"),
            ("FILTRAR DD", "Filtra estrategias por umbral de DD diario.", "threshold"),
        ]
        for index, (title, desc, action) in enumerate(specs):
            row = 1 + index // 3
            column = index % 3
            card = tk.Frame(actions, bg=colors["panel_alt"], highlightthickness=1, highlightbackground=colors["border"])
            card.grid(row=row, column=column, sticky="nsew", padx=(20 if column == 0 else 8, 20 if column == 2 else 8), pady=(0, 14))
            card.columnconfigure(0, weight=1)
            tk.Label(card, text=title, bg=colors["panel_alt"], fg=colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 2))
            tk.Label(card, text=desc, bg=colors["panel_alt"], fg=colors["muted"], font=("Segoe UI", 9), wraplength=250, justify="left").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))
            btn = ttk.Button(card, text="Generar", style="Primary.TButton", command=lambda a=action: self._run_portfolio_action(a))
            btn.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
            self.portfolio_buttons.append(btn)

        self.portfolio_progress = ttk.Progressbar(actions, mode="indeterminate")
        self.portfolio_progress.grid(row=3, column=0, columnspan=3, sticky="ew", padx=20, pady=(4, 12))
        status = ttk.Label(actions, textvariable=self.portfolio_status, style="CardDesc.TLabel", wraplength=900, justify="left")
        status.grid(row=4, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 18))
