"""Pantalla UBS Auditor: una tabla y cuatro acciones.

Las dos consultas (reglas movidas y estados pendientes) llenan la misma tabla,
porque las dos responden a lo mismo: que etapa esta fuera de sitio y cuantas
filas arrastra. Los dos arreglos van detras, en el mismo orden en que se usan.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


AUDITOR_COLUMNS = (
    ("stage", "ETAPA", 150),
    ("item", "REGLA / ESTADO", 210),
    ("current", "AHORA", 110),
    ("stored", "GUARDADO (FILAS)", 420),
    ("rows", "FILAS", 70),
    ("verdicts", "VEREDICTOS", 90),
)


class UBSAuditorViewMixin:
    def _build_ubs_auditor_panel(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Auditor UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        return panel, bar

    def _build_ubs_auditor_actions(self, bar):
        tk.Label(
            bar,
            text=(
                "Compara las reglas guardadas en cada fila con las de Configuracion "
                "y resume los estados que no son finales."
            ),
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))
        actions = [
            ("Auditar reglas", self._audit_ubs_stage_rules_clicked, self.colors["accent"], "#ffffff"),
            ("Consultar pendientes", self._audit_ubs_pending_states_clicked,
             self.colors["panel"], self.colors["muted"]),
            ("Arreglar reglas", self._fix_ubs_stage_rules_clicked,
             self.colors["panel"], self.colors["muted"]),
            ("Arreglar pendientes", self._fix_ubs_pending_states_clicked,
             self.colors["panel"], self.colors["muted"]),
            ("Actualizar", self._refresh_ubs_auditor, self.colors["panel"], self.colors["muted"]),
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

    def _build_ubs_auditor_scope_row(self, bar):
        row = tk.Frame(bar, bg=self.colors["panel_alt"])
        row.grid(row=1, column=0, columnspan=6, sticky="ew", padx=10, pady=(0, 5))
        row.columnconfigure(3, weight=1)
        tk.Label(row, text="Cuenta:", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(
            row=0, column=0, sticky="w", padx=(0, 4)
        )
        self.ubs_auditor_account_combo = ttk.Combobox(
            row, textvariable=self.ubs_auditor_account, state="readonly", width=22
        )
        self.ubs_auditor_account_combo.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.ubs_auditor_account_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._refresh_ubs_auditor_run_combo()
        )
        tk.Label(row, text="Run:", bg=self.colors["panel_alt"], fg=self.colors["muted"]).grid(
            row=0, column=2, sticky="w", padx=(0, 4)
        )
        self.ubs_auditor_run_combo = ttk.Combobox(
            row, textvariable=self.ubs_auditor_run_id, state="readonly", width=42
        )
        self.ubs_auditor_run_combo.grid(row=0, column=3, sticky="ew")

    def _build_ubs_auditor_table(self, panel):
        ttk.Label(panel, textvariable=self.ubs_auditor_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="ew", padx=20, pady=(6, 6)
        )

        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = tuple(column for column, _heading, _width in AUDITOR_COLUMNS)
        self.ubs_auditor_tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=16, selectmode="browse"
        )
        for column, heading, width in AUDITOR_COLUMNS:
            self.ubs_auditor_tree.heading(column, text=heading)
            self.ubs_auditor_tree.column(
                column, width=width, minwidth=60, anchor="center", stretch=False
            )
        self.ubs_auditor_tree.column("stored", anchor="w")
        self.ubs_auditor_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_auditor_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_auditor_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_auditor_tree)
        self._attach_tree_scrollbars(table_frame, self.ubs_auditor_tree, 0, vertical=True)

    def _build_ubs_auditor(self, parent: ttk.Frame) -> None:
        panel, bar = self._build_ubs_auditor_panel(parent)
        self._build_ubs_auditor_actions(bar)
        self._build_ubs_auditor_scope_row(bar)
        self._build_ubs_auditor_table(panel)
