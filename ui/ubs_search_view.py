from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSSearchViewMixin:
    def _build_ubs_search(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        card = self._card(parent, "Buscador UBS")
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        bar = tk.Frame(card, bg=self.colors["panel_alt"])
        bar.grid(row=0, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(1, weight=1)

        tk.Label(
            bar,
            text="Nombre set",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(10, 8), pady=6)
        entry = ttk.Entry(bar, textvariable=self.ubs_search_query)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=6)
        entry.bind("<Return>", lambda _event: self._run_ubs_search())

        tk.Button(
            bar,
            text="Buscar",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._run_ubs_search,
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=5)
        for col, (label, command) in enumerate(
            [
                ("Exportar set", self._export_selected_ubs_search_set),
                ("Abrir set", self._open_selected_ubs_search_set),
                ("Abrir reporte", self._open_selected_ubs_search_report),
                ("Limpiar", self._clear_ubs_search),
            ],
            start=3,
        ):
            tk.Button(
                bar,
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
            ).grid(row=0, column=col, sticky="e", padx=(0, 10 if col == 6 else 6), pady=5)

        ttk.Label(
            card,
            textvariable=self.ubs_search_status,
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 6))

        table_frame = ttk.Frame(card, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 12))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "account",
            "candidate",
            "status",
            "robust",
            "final_tick",
            "symbol",
            "tf",
            "score",
            "set_name",
            "run",
        )
        self.ubs_search_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=18,
            selectmode="extended",
        )
        headings = {
            "account": "CUENTA",
            "candidate": "CAND.",
            "status": "BASE",
            "robust": "ROBUST",
            "final_tick": "FINAL",
            "symbol": "SIMBOLO",
            "tf": "TF",
            "score": "SCORE",
            "set_name": "SET",
            "run": "RUN",
        }
        widths = {
            "account": 70,
            "candidate": 70,
            "status": 90,
            "robust": 90,
            "final_tick": 90,
            "symbol": 120,
            "tf": 55,
            "score": 85,
            "set_name": 520,
            "run": 70,
        }
        for column in columns:
            self.ubs_search_tree.heading(column, text=headings[column])
            anchor = "w" if column == "set_name" else "center"
            self.ubs_search_tree.column(column, width=widths[column], minwidth=42, anchor=anchor, stretch=False)
        self.ubs_search_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_search_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_search_tree.tag_configure("pending", foreground=self.colors["muted"])
        self.ubs_search_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_search_set())
        self._make_tree_sortable(self.ubs_search_tree)
        self._attach_tree_scrollbars(table_frame, self.ubs_search_tree, 0)
