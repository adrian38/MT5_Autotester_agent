from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class UBSUniverseViewMixin:
    def _build_ubs_universe(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Universo, scores y pesos UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        tk.Label(
            bar,
            textvariable=self.ubs_universe_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
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
            command=self._refresh_ubs_universe_panel,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            bar,
            text="Deshabilitar marcados",
            bg=self.colors["panel"],
            fg=self.colors["danger"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=lambda: self._set_checked_universe_symbols_enabled(False),
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            bar,
            text="Habilitar marcados",
            bg=self.colors["panel"],
            fg=self.colors["accent_soft_text"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=lambda: self._set_checked_universe_symbols_enabled(True),
        ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=4)
        self._ubs_calc_weights_btn = tk.Button(
            bar,
            text="Calcular pesos",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._ubs_apply_weights,
        )
        self._ubs_calc_weights_btn.grid(row=0, column=4, sticky="e", padx=(0, 10), pady=4)

        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        asset_frame = ttk.Frame(body, style="Panel.TFrame")
        asset_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        asset_frame.columnconfigure(0, weight=1)
        asset_frame.rowconfigure(1, weight=1)
        ttk.Label(asset_frame, text="Activos RoboForex", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        asset_columns = ("mark", "enabled", "group", "symbol", "aliases", "weight", "avg", "best", "tests", "accepted", "pending")
        self.ubs_universe_assets_tree = ttk.Treeview(asset_frame, columns=asset_columns, show="headings", height=18, selectmode="extended")
        asset_headings = {
            "mark": "SEL",
            "enabled": "ON",
            "group": "GRUPO",
            "symbol": "ACTIVO",
            "aliases": "ALIAS",
            "weight": "PESO",
            "avg": "AVG",
            "best": "BEST",
            "tests": "TESTS",
            "accepted": "OK",
            "pending": "PEND",
        }
        asset_widths = {"mark": 48, "enabled": 48, "group": 110, "symbol": 110, "aliases": 150, "weight": 80, "avg": 80, "best": 80, "tests": 62, "accepted": 54, "pending": 58}
        for column in asset_columns:
            self.ubs_universe_assets_tree.heading(column, text=asset_headings[column])
            self.ubs_universe_assets_tree.column(column, width=asset_widths[column], anchor="center", stretch=False)
        self.ubs_universe_assets_tree.tag_configure("positive", foreground=self.colors["accent_soft_text"])
        self.ubs_universe_assets_tree.tag_configure("negative", foreground=self.colors["danger"])
        self.ubs_universe_assets_tree.tag_configure("neutral", foreground=self.colors["muted"])
        self.ubs_universe_assets_tree.tag_configure("disabled", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_universe_assets_tree)
        self.ubs_universe_assets_tree.bind("<Button-1>", self._on_ubs_universe_tree_click)
        self._attach_tree_scrollbars(asset_frame, self.ubs_universe_assets_tree, 1)

        tf_frame = ttk.Frame(body, style="Panel.TFrame")
        tf_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        tf_frame.columnconfigure(0, weight=1)
        tf_frame.rowconfigure(2, weight=1)
        ttk.Label(tf_frame, text="Timeframes", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(tf_frame, textvariable=self.ubs_timeframe_summary, style="Muted.TLabel", wraplength=520).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        tf_columns = ("period", "weight", "avg", "best", "tests", "accepted", "pending")
        self.ubs_timeframes_tree = ttk.Treeview(tf_frame, columns=tf_columns, show="headings", height=18)
        tf_headings = {"period": "TF", "weight": "PESO", "avg": "AVG", "best": "BEST", "tests": "TESTS", "accepted": "OK", "pending": "PEND"}
        tf_widths = {"period": 70, "weight": 88, "avg": 88, "best": 88, "tests": 65, "accepted": 55, "pending": 60}
        for column in tf_columns:
            self.ubs_timeframes_tree.heading(column, text=tf_headings[column])
            self.ubs_timeframes_tree.column(column, width=tf_widths[column], anchor="center", stretch=False)
        self.ubs_timeframes_tree.tag_configure("positive", foreground=self.colors["accent_soft_text"])
        self.ubs_timeframes_tree.tag_configure("negative", foreground=self.colors["danger"])
        self.ubs_timeframes_tree.tag_configure("neutral", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_timeframes_tree)
        self._attach_tree_scrollbars(tf_frame, self.ubs_timeframes_tree, 2)

