"""Pantalla UBS Auditor: una tabla y cuatro acciones.

Las dos consultas (reglas movidas y estados pendientes) llenan la misma tabla,
porque las dos responden a lo mismo: que etapa esta fuera de sitio y a cuantas
semillas o candidatos arrastra. La tabla es un arbol de una fila por etapa, en
el orden del embudo, con el detalle colgando de ella. Los dos arreglos van
detras, en el mismo orden en que se usan.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


# La etapa es la columna de arbol (#0): cada etapa es una fila y su detalle
# cuelga de ella, asi que el nombre no se repite.
#
# Cada columna significa una sola cosa en todas las filas, y las que no
# significan nada en una consulta no se ensenan: las dos consultas comparten el
# arbol pero no las columnas, y antes compartian tambien los titulos, asi que
# «DETALLE» tenia que valer a la vez para «se juzgo con 1970.01.01 (2)» y para
# «error (12)». `displaycolumns` reparte cada juego de titulos a su consulta.
#
# Los anchos suman menos que el ancho util de la tabla a proposito: la columna
# ETAPA es la primera y se va con la barra horizontal, y una tabla que nace
# desplazada esconde justo lo que se viene a mirar. La columna ancha estira
# para quedarse con lo que sobre.
# Ancho al que envuelven las dos lineas de texto que van debajo de la barra, a
# lo ancho del panel: el resumen de la consulta y las reglas ciegas. Las dos
# crecen con lo que encuentre la auditoria, y sin envolver se cortaban por
# donde cayera.
AUDITOR_TEXT_WRAPLENGTH = 900
AUDITOR_STAGE_COLUMN_WIDTH = 140
AUDITOR_COLUMNS = ("kind", "item", "current", "stored", "affected", "judged")
AUDITOR_RULE_LAYOUT = (
    ("kind", "QUE PASA", 110, False),
    ("item", "REGLA", 130, False),
    ("current", "AHORA", 85, False),
    ("stored", "SE JUZGO CON", 180, True),
    ("affected", "A CUANTOS AFECTA", 165, False),
    ("judged", "YA JUZGADOS", 100, False),
)
AUDITOR_PENDING_LAYOUT = (
    ("kind", "QUE PASA", 150, False),
    ("stored", "EN QUE ESTADO SE QUEDARON", 330, True),
    ("affected", "CUANTOS FALTAN", 190, False),
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
        # La ayuda va en su propia fila: compartiendo fila con los botones se
        # quedaba recortada en cuanto la ventana se estrechaba.
        tk.Label(
            bar,
            text=(
                "Semillas y candidatos son .set: el de partida y el que genero el "
                "agente a partir de el. Auditar reglas compara los umbrales con los "
                "que se juzgo cada uno con los de Configuracion, y marca aparte la "
                "etapa cuya regla cambio sin mover un umbral; Consultar pendientes "
                "lista los que se quedaron a medias y los que, por haberse vuelto "
                "elegibles al repuntuar, no tienen fila todavia. «A cuantos afecta» "
                "son los que se juzgaron con otra regla, y «Ya juzgados», cuantos de "
                "ellos tienen veredicto: solo esos pueden cambiar al repuntuar."
            ),
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=2, column=0, columnspan=6, sticky="ew", padx=10, pady=(0, 6))
        actions = [
            ("Auditar reglas", self._audit_ubs_stage_rules_clicked, self.colors["accent"], "#ffffff"),
            ("Consultar pendientes", self._audit_ubs_pending_states_clicked,
             self.colors["panel"], self.colors["muted"]),
            ("Repuntuar afectados", self._rescore_ubs_affected_clicked,
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
        # Lo que el resumen deja para el final es justo lo que no se arregla
        # pulsando un boton -las etapas que hay que repetir enteras-, asi que
        # cortarlo por donde cayera escondia la unica parte accionable.
        ttk.Label(
            panel,
            textvariable=self.ubs_auditor_status,
            style="Muted.TLabel",
            wraplength=AUDITOR_TEXT_WRAPLENGTH,
            justify="left",
        ).grid(row=2, column=0, sticky="ew", padx=20, pady=(6, 6))

        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 6))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.ubs_auditor_tree = ttk.Treeview(
            table_frame,
            columns=AUDITOR_COLUMNS,
            show="tree headings",
            height=16,
            selectmode="browse",
        )
        self.ubs_auditor_tree.heading("#0", text="ETAPA")
        self.ubs_auditor_tree.column(
            "#0", width=AUDITOR_STAGE_COLUMN_WIDTH, minwidth=110, anchor="w", stretch=False
        )
        for column in AUDITOR_COLUMNS:
            self.ubs_auditor_tree.heading(column, text=column.upper())
            self.ubs_auditor_tree.column(column, width=120, minwidth=60, anchor="w")
        self.ubs_auditor_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_auditor_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_auditor_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_auditor_tree)
        self._show_ubs_auditor_columns(AUDITOR_RULE_LAYOUT)
        self._attach_tree_scrollbars(table_frame, self.ubs_auditor_tree, 0, vertical=True)

        # Las reglas ciegas no dependen de la auditoria: son siempre las mismas
        # y las comparten cuatro etapas. Como fila se repetian cuatro veces y
        # se cortaban en su columna; debajo de la tabla se dicen una vez.
        ttk.Label(
            panel,
            textvariable=self.ubs_auditor_blind_rules,
            style="Muted.TLabel",
            wraplength=AUDITOR_TEXT_WRAPLENGTH,
            justify="left",
        ).grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 16))

    def _show_ubs_auditor_columns(self, layout) -> None:
        """Ensena solo las columnas de la consulta que acaba de llenar la tabla."""
        tree = getattr(self, "ubs_auditor_tree", None)
        if tree is None:
            return
        tree.configure(displaycolumns=tuple(column for column, _t, _w, _s in layout))
        for column, heading, width, stretch in layout:
            # `heading(text=...)` solo toca el titulo, asi que el orden que
            # dejo `_make_tree_sortable` sigue colgando de la cabecera.
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=min(width, 70), stretch=stretch)

    def _build_ubs_auditor(self, parent: ttk.Frame) -> None:
        panel, bar = self._build_ubs_auditor_panel(parent)
        self._build_ubs_auditor_actions(bar)
        self._build_ubs_auditor_scope_row(bar)
        self._build_ubs_auditor_table(panel)
