from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class UBSUniverseViewMixin:
    def _fit_ubs_universe_summary(self, event) -> None:
        """Ajusta el wraplength del resumen al ancho disponible de la barra.

        Cambiar wraplength puede cambiar el alto de la etiqueta y disparar otro
        <Configure>, asi que solo se reescribe cuando el ancho se mueve de forma
        apreciable."""
        width = max(200, int(event.width) - 20)
        if abs(width - getattr(self, "_ubs_universe_summary_wrap", 0)) < 8:
            return
        self._ubs_universe_summary_wrap = width
        label = getattr(self, "_ubs_universe_summary_label", None)
        if label is not None:
            label.configure(wraplength=width)

    def _build_ubs_universe(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Universo, scores y pesos UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        # El resumen ocupa su propia fila: son ~1400px de texto y no cabe junto a
        # los botones en ninguna ventana. Compartiendo fila la etiqueta se
        # encogia y, con anchor centrado, el texto se recortaba por los dos
        # lados. anchor="w" ademas garantiza que si alguna vez falta sitio se
        # corte solo por la derecha.
        self._ubs_universe_summary_label = tk.Label(
            bar,
            textvariable=self.ubs_universe_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        self._ubs_universe_summary_label.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        self._ubs_universe_summary_wrap = 0
        bar.bind("<Configure>", self._fit_ubs_universe_summary)
        # Dos filas de botones: arriba el flujo de sincronizacion en orden de uso
        # (Sincronizacion -> Probar history GEN -> Deshabilitar sin history) y
        # abajo las acciones sobre marcados y pesos. En una sola fila median
        # 1651px y recortaban el ultimo boton.
        flow_buttons = [
            ("Actualizar",          self.colors["muted"],          False, self._refresh_ubs_universe_panel),
            ("Sincronizacion de simbolos", self.colors["accent_soft_text"], True, self._sync_mt5_universe_symbols),
            ("Probar history GEN",  self.colors["accent_soft_text"], True,  self._run_ubs_universe_history_probe),
            ("Deshabilitar simbolos sin history", self.colors["danger"], True, self._disable_no_history_universe_symbols),
            ("Extraer MT5",         self.colors["accent_soft_text"], True,  self._extract_mt5_universe_symbols),
        ]
        selection_buttons = [
            ("Deshabilitar marcados", self.colors["danger"],        True,  lambda: self._set_checked_universe_symbols_enabled(False)),
            ("Habilitar marcados",  self.colors["accent_soft_text"], True, lambda: self._set_checked_universe_symbols_enabled(True)),
            ("Permitir seeds",      self.colors["accent_soft_text"], False, lambda: self._set_checked_universe_symbols_seed_enabled(True)),
            ("Bloquear seeds",      self.colors["muted"],          False, lambda: self._set_checked_universe_symbols_seed_enabled(False)),
            ("Limpiar marcados",    self.colors["muted"],          False, self._clear_selected_weights),
            ("Reset pesos activos", self.colors["muted"],          False, self._clear_all_asset_weights),
            ("Reset pesos TF",      self.colors["muted"],          False, self._clear_all_tf_weights),
        ]

        # Un frame por fila: con una sola rejilla compartida cada columna se
        # ensancha con el boton mas largo de esa columna y las dos filas quedan
        # desalineadas.
        def build_button_row(row: int, buttons: list, top_pad: int) -> tk.Frame:
            holder = tk.Frame(bar, bg=self.colors["panel_alt"])
            holder.grid(row=row, column=0, sticky="e", padx=(0, 10), pady=(top_pad, 5))
            for col, (label, fg, bold, cmd) in enumerate(buttons):
                font = ("Segoe UI", 9, "bold") if bold else ("Segoe UI", 9)
                tk.Button(holder, text=label, bg=self.colors["panel"], fg=fg,
                          relief="solid", borderwidth=1, padx=8, pady=5,
                          font=font, cursor="hand2", command=cmd,
                          ).grid(row=0, column=col, sticky="e", padx=(0, 6))
            return holder

        build_button_row(1, flow_buttons, top_pad=2)
        selection_holder = build_button_row(2, selection_buttons, top_pad=0)
        self._ubs_calc_weights_btn = tk.Button(
            selection_holder, text="Calcular pesos",
            bg=self.colors["accent"], fg="#ffffff",
            relief="flat", borderwidth=0, padx=10, pady=5,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
            command=self._ubs_apply_weights,
        )
        self._ubs_calc_weights_btn.grid(row=0, column=len(selection_buttons), sticky="e")

        filter_bar = ttk.Frame(panel, style="Panel.TFrame")
        filter_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
        filter_bar.columnconfigure(1, weight=1)
        filter_bar.columnconfigure(4, weight=1)
        ttk.Label(filter_bar, text="Buscar activos:", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.ubs_universe_asset_search.trace_add("write", lambda *_: self._refresh_ubs_universe())
        ttk.Entry(filter_bar, textvariable=self.ubs_universe_asset_search, width=28).grid(
            row=0, column=1, sticky="ew", padx=(0, 14)
        )
        ttk.Label(filter_bar, text="Buscar TF:", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.ubs_universe_tf_search.trace_add("write", lambda *_: self._refresh_ubs_universe())
        ttk.Entry(filter_bar, textvariable=self.ubs_universe_tf_search, width=12).grid(
            row=0, column=3, sticky="w", padx=(0, 14)
        )
        ttk.Button(filter_bar, text="Limpiar busqueda", style="Tool.TButton", command=self._clear_ubs_universe_search).grid(
            row=0, column=4, sticky="w"
        )

        body = ttk.PanedWindow(panel, orient="horizontal")
        body.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))

        asset_frame = ttk.Frame(body, style="Panel.TFrame")
        asset_frame.columnconfigure(0, weight=1)
        asset_frame.rowconfigure(1, weight=1)
        body.add(asset_frame, weight=3)
        ttk.Label(asset_frame, text="Activos del broker", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        asset_columns = ("mark", "enabled", "seed_enabled", "group", "symbol", "aliases", "weight", "probability", "confidence", "final_trials", "regression_trials", "avg", "best", "tests", "accepted", "pending")
        self.ubs_universe_assets_tree = ttk.Treeview(asset_frame, columns=asset_columns, show="headings", height=18, selectmode="extended")
        asset_headings = {
            "mark": "SEL",
            "enabled": "GEN",
            "seed_enabled": "SEEDS",
            "group": "GRUPO",
            "symbol": "ACTIVO",
            "aliases": "ALIAS",
            "weight": "PESO REL",
            "probability": "P FINAL %",
            "confidence": "CONF %",
            "final_trials": "N 6M",
            "regression_trials": "N REG",
            "avg": "AVG",
            "best": "BEST",
            "tests": "TESTS",
            "accepted": "OK",
            "pending": "PEND",
        }
        asset_widths = {"mark": 48, "enabled": 50, "seed_enabled": 58, "group": 110, "symbol": 110, "aliases": 150, "weight": 82, "probability": 82, "confidence": 72, "final_trials": 58, "regression_trials": 58, "avg": 80, "best": 80, "tests": 62, "accepted": 54, "pending": 58}
        for column in asset_columns:
            self.ubs_universe_assets_tree.heading(column, text=asset_headings[column])
            self.ubs_universe_assets_tree.column(column, width=asset_widths[column], minwidth=42, anchor="center", stretch=False)
        self.ubs_universe_assets_tree.tag_configure("positive", foreground=self.colors["accent_soft_text"])
        self.ubs_universe_assets_tree.tag_configure("negative", foreground=self.colors["danger"])
        self.ubs_universe_assets_tree.tag_configure("neutral", foreground=self.colors["muted"])
        self.ubs_universe_assets_tree.tag_configure("disabled", foreground=self.colors["muted"])
        self.ubs_universe_assets_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_universe_assets_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_universe_assets_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_universe_assets_tree)
        self.ubs_universe_assets_tree.bind("<Button-1>", self._on_ubs_universe_tree_click)
        self._attach_tree_scrollbars(asset_frame, self.ubs_universe_assets_tree, 1, vertical=True)

        tf_frame = ttk.Frame(body, style="Panel.TFrame")
        tf_frame.columnconfigure(0, weight=1)
        tf_frame.rowconfigure(2, weight=1)
        body.add(tf_frame, weight=2)
        ttk.Label(tf_frame, text="Timeframes", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(tf_frame, textvariable=self.ubs_timeframe_summary, style="Muted.TLabel", wraplength=520).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        tf_columns = ("mark", "period", "weight", "probability", "confidence", "final_trials", "regression_trials", "avg", "best", "tests", "accepted", "pending")
        self.ubs_timeframes_tree = ttk.Treeview(tf_frame, columns=tf_columns, show="headings",
                                                height=18, selectmode="extended")
        tf_headings = {"mark": "SEL", "period": "TF", "weight": "PESO REL", "probability": "P FINAL %", "confidence": "CONF %", "final_trials": "N 6M", "regression_trials": "N REG", "avg": "AVG", "best": "BEST", "tests": "TESTS", "accepted": "OK", "pending": "PEND"}
        tf_widths = {"mark": 48, "period": 66, "weight": 84, "probability": 82, "confidence": 72, "final_trials": 58, "regression_trials": 58, "avg": 84, "best": 84, "tests": 62, "accepted": 52, "pending": 56}
        for column in tf_columns:
            self.ubs_timeframes_tree.heading(column, text=tf_headings[column])
            self.ubs_timeframes_tree.column(column, width=tf_widths[column], minwidth=42, anchor="center", stretch=False)
        self.ubs_timeframes_tree.tag_configure("positive", foreground=self.colors["accent_soft_text"])
        self.ubs_timeframes_tree.tag_configure("negative", foreground=self.colors["danger"])
        self.ubs_timeframes_tree.tag_configure("neutral", foreground=self.colors["muted"])
        self.ubs_timeframes_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_timeframes_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_timeframes_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_timeframes_tree)
        self.ubs_timeframes_tree.bind("<Button-1>", self._on_ubs_timeframe_tree_click)
        self._attach_tree_scrollbars(tf_frame, self.ubs_timeframes_tree, 2, vertical=True)
