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
        card.rowconfigure(1, weight=1)

        panes = tk.PanedWindow(
            card,
            orient=tk.VERTICAL,
            bg=self.colors["border"],
            bd=0,
            sashwidth=7,
            sashrelief="raised",
            showhandle=True,
        )
        panes.grid(row=1, column=0, sticky="nsew", padx=20, pady=(4, 12))

        audit_pane = tk.Frame(panes, bg=self.colors["panel"])
        search_pane = tk.Frame(panes, bg=self.colors["panel"])
        panes.add(audit_pane, minsize=210, stretch="always")
        panes.add(search_pane, minsize=180, stretch="always")

        audit_pane.columnconfigure(0, weight=1)
        audit_pane.rowconfigure(3, weight=1)
        search_pane.columnconfigure(0, weight=1)
        search_pane.rowconfigure(3, weight=1)

        self._ubs_search_section_title(audit_pane, 0, "Auditoria de run")

        audit = tk.Frame(audit_pane, bg=self.colors["panel_alt"])
        audit.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        audit.columnconfigure(3, weight=1)
        tk.Label(
            audit,
            text="Cuenta",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(10, 8), pady=6)
        account_combo = ttk.Combobox(
            audit,
            textvariable=self.ubs_audit_account,
            values=(),
            state="readonly",
            width=18,
        )
        self.ubs_audit_account_combo = account_combo
        account_combo.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=6)
        account_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_audit_run_combo())
        tk.Label(
            audit,
            text="Run",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=2, sticky="w", padx=(0, 6), pady=6)
        self.ubs_audit_run_combo = ttk.Combobox(
            audit,
            textvariable=self.ubs_audit_run_id,
            state="readonly",
            width=44,
        )
        self.ubs_audit_run_combo.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=6)
        for col, (label, command, accent) in enumerate(
            [
                ("Auditar", self._run_ubs_audit_from_search, True),
                ("Abrir auditoria", self._open_ubs_audit_report, False),
            ],
            start=4,
        ):
            tk.Button(
                audit,
                text=label,
                bg=self.colors["accent"] if accent else self.colors["panel"],
                fg="#ffffff" if accent else self.colors["muted"],
                relief="solid" if not accent else "flat",
                borderwidth=1 if not accent else 0,
                padx=8,
                pady=5,
                font=("Segoe UI", 9, "bold" if accent else "normal"),
                cursor="hand2",
                command=command,
            ).grid(row=0, column=col, sticky="e", padx=(0, 6), pady=5)
        ttk.Label(audit, textvariable=self.ubs_audit_status, style="Muted.TLabel").grid(
            row=0, column=6, sticky="w", padx=(8, 10), pady=6
        )
        self._refresh_ubs_audit_account_values()
        self._refresh_ubs_audit_run_combo()

        self._ubs_search_section_title(audit_pane, 2, "Auditoria por test")
        audit_tables = tk.PanedWindow(
            audit_pane,
            orient=tk.VERTICAL,
            bg=self.colors["border"],
            bd=0,
            sashwidth=6,
            sashrelief="raised",
            showhandle=True,
        )
        audit_tables.grid(row=3, column=0, sticky="nsew")
        top_tables = tk.PanedWindow(
            audit_tables,
            orient=tk.HORIZONTAL,
            bg=self.colors["border"],
            bd=0,
            sashwidth=6,
            sashrelief="raised",
            showhandle=True,
        )
        bottom_tables = tk.PanedWindow(
            audit_tables,
            orient=tk.HORIZONTAL,
            bg=self.colors["border"],
            bd=0,
            sashwidth=6,
            sashrelief="raised",
            showhandle=True,
        )
        audit_tables.add(top_tables, minsize=105, stretch="always")
        audit_tables.add(bottom_tables, minsize=105, stretch="always")
        self.ubs_audit_trees = {}
        for key, title in [
            ("Generacion", "Base / Generacion"),
            ("Robustez", "Robustez"),
            ("Final Tick", "Final Tick corto"),
        ]:
            top_tables.add(self._create_ubs_audit_table(top_tables, key, title), minsize=260, stretch="always")
        for key, title in [
            ("Final Tick 6M", "Final Tick 6M"),
            ("Pesos", "Pesos / Hallazgos"),
        ]:
            bottom_tables.add(self._create_ubs_audit_table(bottom_tables, key, title), minsize=320, stretch="always")

        self._ubs_search_section_title(search_pane, 0, "Buscar set")

        bar = tk.Frame(search_pane, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
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
            search_pane,
            textvariable=self.ubs_search_status,
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="ew", pady=(0, 6))

        table_frame = ttk.Frame(search_pane, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = (
            "account",
            "candidate",
            "status",
            "robust",
            "final_tick",
            "final_tick_6m",
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
            "final_tick": "FT",
            "final_tick_6m": "FT 6M",
            "symbol": "SIMBOLO",
            "tf": "TF",
            "score": "SCORE",
            "set_name": "SET",
            "run": "RUN",
        }
        widths = {
            "account": 150,
            "candidate": 70,
            "status": 90,
            "robust": 90,
            "final_tick": 90,
            "final_tick_6m": 90,
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

    def _create_ubs_audit_table(
        self,
        parent: tk.Widget,
        key: str,
        title: str,
        row: int | None = None,
        column: int | None = None,
    ) -> tk.Frame:
        box = tk.Frame(parent, bg=self.colors["panel"])
        if row is not None and column is not None:
            box.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0), pady=(0, 10))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        tk.Label(
            box,
            text=title,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        frame = ttk.Frame(box, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("process", "status", "detail")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=4, selectmode="browse")
        tree.heading("process", text="PROCESO")
        tree.heading("status", text="ESTADO")
        tree.heading("detail", text="DETALLE")
        tree.column("process", width=125, minwidth=90, anchor="w", stretch=False)
        tree.column("status", width=80, minwidth=70, anchor="center", stretch=False)
        tree.column("detail", width=360, minwidth=180, anchor="w", stretch=False)
        self._standard_ubs_search_audit_tree(tree)
        tree.bind("<Double-1>", lambda event: self._on_ubs_audit_detail_double_click(event))
        self._attach_tree_scrollbars(frame, tree, 0)
        self.ubs_audit_trees[key] = tree
        return box

    def _ubs_search_section_title(self, parent: tk.Widget, row: int, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=0, sticky="w", pady=(4, 4))

    def _ubs_search_separator(self, parent: tk.Widget, row: int) -> None:
        tk.Frame(parent, bg=self.colors["border"], height=1).grid(
            row=row,
            column=0,
            sticky="ew",
            padx=20,
            pady=(4, 8),
        )

    def _standard_ubs_search_audit_tree(self, tree: ttk.Treeview) -> None:
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])
        tree.tag_configure("separator", background=self.colors["panel_alt"], foreground=self.colors["border"])
