from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class UBSParamsViewMixin:
    def _build_ubs_params(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        card = self._card(parent, "UBS Parámetros")
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(3, weight=1)

        # row 1 — toolbar (solo Guardar, es una vista global no de archivo específico)
        toolbar = ttk.Frame(card, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", padx=20, pady=(10, 4))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="Parámetros globales del agente UBS — doble click para editar valor",
                  style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Toggle inamovible/mutable", style="TButton",
                   command=self._ubs_params_toggle_mutability).grid(row=0, column=1, sticky="e", padx=(0, 8))
        ttk.Button(toolbar, text="Guardar", style="Primary.TButton",
                   command=self._ubs_params_save).grid(row=0, column=2, sticky="e")

        # row 2 — filter bar
        filter_bar = ttk.Frame(card, style="Panel.TFrame")
        filter_bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
        filter_bar.columnconfigure(4, weight=1)
        self._ubs_params_filter = tk.StringVar(value="all")
        ttk.Radiobutton(filter_bar, text="Todos", variable=self._ubs_params_filter, value="all",
                        style="TRadiobutton",
                        command=self._ubs_params_apply_filter).grid(row=0, column=0, padx=(0, 8))
        ttk.Radiobutton(filter_bar, text="Mutables por agente", variable=self._ubs_params_filter, value="mutable",
                        style="TRadiobutton",
                        command=self._ubs_params_apply_filter).grid(row=0, column=1, padx=(0, 8))
        ttk.Radiobutton(filter_bar, text="Inamovibles", variable=self._ubs_params_filter, value="frozen",
                        style="TRadiobutton",
                        command=self._ubs_params_apply_filter).grid(row=0, column=2, padx=(0, 20))
        ttk.Label(filter_bar, text="Buscar:", style="Muted.TLabel").grid(row=0, column=3, padx=(0, 4))
        self._ubs_params_search = tk.StringVar()
        self._ubs_params_search.trace_add("write", lambda *_: self._ubs_params_apply_filter())
        ttk.Entry(filter_bar, textvariable=self._ubs_params_search, width=26).grid(row=0, column=4, sticky="ew")

        # row 3 — tree
        tree_frame = ttk.Frame(card, style="Panel.TFrame")
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 4))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        cols = ("key", "description", "value", "range", "agent")
        self.ubs_params_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=18, selectmode="browse")
        headings = {
            "key": "CLAVE", "description": "DESCRIPCIÓN", "value": "VALOR",
            "range": "RANGO", "agent": "AGENTE",
        }
        widths = {"key": 230, "description": 420, "value": 120, "range": 140, "agent": 110}
        for col in cols:
            self.ubs_params_tree.heading(col, text=headings[col])
            self.ubs_params_tree.column(col, width=widths[col], minwidth=42,
                                        stretch=False, anchor="center")
        self.ubs_params_tree.tag_configure("section",
                                           background=self.colors["panel_alt"],
                                           foreground=self.colors["muted"],
                                           font=("Segoe UI", 9, "bold"))
        self.ubs_params_tree.tag_configure("mutable", foreground=self.colors["accent_soft_text"])
        self.ubs_params_tree.tag_configure("frozen", foreground=self.colors["text"])
        self.ubs_params_tree.tag_configure("overridden_frozen", foreground=self.colors["danger"])
        self.ubs_params_tree.tag_configure("overridden_mutable", foreground="#f59e0b")
        self.ubs_params_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_params_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_params_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_params_tree)
        self._attach_tree_scrollbars(tree_frame, self.ubs_params_tree, 0)
        self.ubs_params_tree.bind("<<TreeviewSelect>>", self._ubs_params_on_select)
        self.ubs_params_tree.bind("<Double-1>", lambda _e: self._ubs_params_edit_selected())

        # row 4 — description bar
        desc_bar = ttk.Frame(card, style="Panel.TFrame")
        desc_bar.grid(row=4, column=0, sticky="ew", padx=20, pady=(2, 14))
        desc_bar.columnconfigure(1, weight=1)
        ttk.Label(desc_bar, text="ℹ", style="Muted.TLabel", font=("Segoe UI", 11)).grid(row=0, column=0, padx=(0, 6))
        ttk.Label(desc_bar, textvariable=self.ubs_params_desc_var,
                  style="Muted.TLabel", wraplength=900, justify="left").grid(row=0, column=1, sticky="w")

        # Auto-load first available seed
        self.after(100, self._ubs_params_auto_load)

