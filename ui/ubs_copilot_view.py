from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSCopilotViewMixin:
    def _build_ubs_copilot(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Copiloto IA UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(3, weight=1)

        tk.Label(
            bar,
            text="Cuenta",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(10, 6), pady=(6, 3))
        self.ubs_copilot_account_combo = ttk.Combobox(
            bar,
            textvariable=self.ubs_copilot_account,
            values=(),
            state="readonly",
            width=18,
        )
        self.ubs_copilot_account_combo.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(6, 3))
        self.ubs_copilot_account_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_copilot_run_combo())

        tk.Label(
            bar,
            text="Run",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=2, sticky="w", padx=(0, 6), pady=(6, 3))
        self.ubs_copilot_run_combo = ttk.Combobox(
            bar,
            textvariable=self.ubs_copilot_run_id,
            values=(),
            state="readonly",
            width=48,
        )
        self.ubs_copilot_run_combo.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=(6, 3))

        tk.Label(
            bar,
            text="Proveedor",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=4, sticky="w", padx=(0, 6), pady=(6, 3))
        ttk.Combobox(
            bar,
            textvariable=self.ubs_copilot_provider,
            values=("local", "openai"),
            state="readonly",
            width=10,
        ).grid(row=0, column=5, sticky="w", padx=(0, 8), pady=(6, 3))

        tk.Label(
            bar,
            text="Modelo",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=6, sticky="w", padx=(0, 6), pady=(6, 3))
        ttk.Entry(bar, textvariable=self.ubs_copilot_model, width=16).grid(
            row=0, column=7, sticky="w", padx=(0, 10), pady=(6, 3)
        )

        row1 = tk.Frame(bar, bg=self.colors["panel_alt"])
        row1.grid(row=1, column=0, columnspan=8, sticky="ew", padx=10, pady=(0, 6))
        row1.columnconfigure(0, weight=1)
        tk.Checkbutton(
            row1,
            text="Usar manual local",
            variable=self.ubs_copilot_include_manual,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            activebackground=self.colors["panel_alt"],
            activeforeground=self.colors["text"],
            selectcolor=self.colors["panel"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        tk.Label(
            row1,
            text="Keys manual",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=1, sticky="w", padx=(0, 4))
        ttk.Entry(row1, textvariable=self.ubs_copilot_max_manual_keys, width=8).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )

        for col, (label, command, accent) in enumerate(
            [
                ("Diagnosticar", self._run_ubs_copilot, True),
                ("Actualizar runs", self._refresh_ubs_copilot_run_combo, False),
                ("Abrir informe", self._open_ubs_copilot_report, False),
                ("Abrir carpeta", self._open_ubs_copilot_folder, False),
            ],
            start=3,
        ):
            tk.Button(
                row1,
                text=label,
                bg=self.colors["accent"] if accent else self.colors["panel"],
                fg="#ffffff" if accent else self.colors["muted"],
                relief="flat" if accent else "solid",
                borderwidth=0 if accent else 1,
                padx=10 if accent else 8,
                pady=5,
                font=("Segoe UI", 9, "bold" if accent else "normal"),
                cursor="hand2",
                command=command,
            ).grid(row=0, column=col, sticky="e", padx=(0, 6))

        ttk.Label(panel, textvariable=self.ubs_copilot_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="ew", padx=20, pady=(0, 8)
        )

        panes = tk.PanedWindow(
            panel,
            orient=tk.VERTICAL,
            bg=self.colors["border"],
            bd=0,
            sashwidth=7,
            sashrelief="raised",
            showhandle=True,
        )
        panes.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))

        table_pane = tk.Frame(panes, bg=self.colors["panel"])
        detail_pane = tk.Frame(panes, bg=self.colors["panel"])
        panes.add(table_pane, minsize=260, stretch="always")
        panes.add(detail_pane, minsize=150, stretch="always")
        table_pane.columnconfigure(0, weight=1)
        table_pane.rowconfigure(0, weight=1)
        detail_pane.columnconfigure(0, weight=1)
        detail_pane.rowconfigure(1, weight=1)

        columns = ("kind", "severity", "action", "title", "affected", "risk", "evidence")
        self.ubs_copilot_tree = ttk.Treeview(
            table_pane,
            columns=columns,
            show="headings",
            height=14,
            selectmode="browse",
        )
        headings = {
            "kind": "TIPO",
            "severity": "NIVEL",
            "action": "ACCION",
            "title": "TITULO",
            "affected": "N",
            "risk": "RIESGO",
            "evidence": "EVIDENCIA",
        }
        widths = {
            "kind": 110,
            "severity": 90,
            "action": 150,
            "title": 520,
            "affected": 60,
            "risk": 80,
            "evidence": 360,
        }
        for column in columns:
            self.ubs_copilot_tree.heading(column, text=headings[column])
            anchor = "w" if column in {"title", "evidence"} else "center"
            self.ubs_copilot_tree.column(column, width=widths[column], minwidth=42, anchor=anchor, stretch=False)
        self.ubs_copilot_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_copilot_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_copilot_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_copilot_tree)
        self._attach_tree_scrollbars(table_pane, self.ubs_copilot_tree, 0)
        self.ubs_copilot_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_ubs_copilot_select())
        self.ubs_copilot_tree.bind("<Double-1>", lambda _event: self._show_ubs_copilot_detail_window())

        tk.Label(
            detail_pane,
            text="Detalle seleccionado",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        detail_frame = ttk.Frame(detail_pane, style="Panel.TFrame")
        detail_frame.grid(row=1, column=0, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.ubs_copilot_detail_text = tk.Text(
            detail_frame,
            height=8,
            wrap="word",
            bg=self.colors["tree_bg"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 9),
        )
        self.ubs_copilot_detail_text.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.ubs_copilot_detail_text.yview)
        self.ubs_copilot_detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.ubs_copilot_detail_text.configure(state="disabled")

        self._refresh_ubs_copilot_account_values()
        self._refresh_ubs_copilot_run_combo()
