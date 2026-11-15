"""Reglas comunes a las cuatro pantallas Final Tick.

`UBS Final Tick`, `UBS Final Tick 6M`, `UBS Seeds Final Tick` y
`UBS Seeds Final Tick 6M` comparten umbrales, fechas, estilo de botonera,
etiquetas de estado y construccion de tabla. Este modulo es la unica fuente:
cambiar aqui un umbral, un ancho o un estilo se publica en las cuatro.

Lo que si depende de la etapa (tablas SQLite, fechas 6M, columnas propias de
seed) queda parametrizado por `stage`, nunca duplicado.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


SIX_MONTH_STAGES = {"six_month", "6m", "sixmonth"}


def is_six_month(stage: object) -> bool:
    return str(stage or "").strip().lower().replace("-", "_") in SIX_MONTH_STAGES


def stage_suffix(stage: object) -> str:
    """Sufijo con el que cada etapa nombra sus atributos y metodos."""
    return "_6m" if is_six_month(stage) else ""


def stage_label(stage: object, base: str = "Final Tick") -> str:
    return f"{base} 6M" if is_six_month(stage) else base


# Umbrales oficiales del Final Tick, en el orden en que se pintan. Las cuatro
# pantallas leen las mismas variables, asi que anadir uno aqui lo publica en
# todas y un valor editado en cualquiera vale para el resto.
FINAL_TICK_THRESHOLD_FIELDS = (
    ("Calidad >=", "ubs_final_tick_min_history_quality", 8),
    ("Min ops OHLC", "ubs_final_tick_min_ohlc_trades", 8),
    ("W1 FT ops", "ubs_final_tick_min_trades_w1", 8),
    ("MN FT ops", "ubs_final_tick_min_trades_mn", 8),
    ("PF delta %", "ubs_final_tick_max_pf_delta_pct", 8),
    ("DD delta %", "ubs_final_tick_max_dd_delta_pct", 8),
    ("DD banda pp", "ubs_final_tick_max_dd_gap_pp", 8),
    ("Net delta %", "ubs_final_tick_max_net_delta_pct", 8),
    ("Trades delta %", "ubs_final_tick_max_trades_delta_pct", 8),
)

# Fechas propias de cada etapa: el tramo corto solo tiene su rango; el 6M suma
# el rango de reintento por pocas operaciones OHLC.
FINAL_TICK_DATE_FIELDS = {
    "": (
        ("Desde", "ubs_final_tick_from_date", 14),
        ("Hasta", "ubs_final_tick_to_date", 14),
    ),
    "_6m": (
        ("Desde", "ubs_final_tick_6m_from_date", 14),
        ("Hasta", "ubs_final_tick_6m_to_date", 14),
        ("Ops bajas desde", "ubs_final_tick_6m_ohlc_from_date", 14),
        ("Ops bajas hasta", "ubs_final_tick_6m_ohlc_to_date", 14),
    ),
}

FINAL_TICK_CRITERIA_PER_ROW = 5

# Color de cada veredicto. `_ubs_result_tag` reduce cualquier estado a una de
# estas tres etiquetas, de modo que las cuatro tablas pintan igual.
FINAL_TICK_TREE_TAGS = (
    ("accepted", "accent_soft_text"),
    ("rejected", "danger"),
    ("pending", "muted"),
)

FINAL_TICK_TREE_HEIGHT = 12

# Tabla de las pantallas de candidatos. La cabecera de `status` es lo unico
# que cambia entre etapas.
FINAL_TICK_COLUMNS = (
    ("mark", "SEL", 48),
    ("run", "RUN", 50),
    ("id", "ID", 58),
    ("gen", "GEN", 44),
    ("status", "FINAL", 96),
    ("cause", "CAUSA", 230),
    ("symbol", "SYMBOL", 90),
    ("period", "TF", 52),
    ("quality", "CALIDAD", 78),
    ("ohlc_score", "SCORE OHLC", 88),
    ("tick_score", "SCORE TICK", 88),
    ("net_ohlc", "NET OHLC", 84),
    ("net_tick", "NET TICK", 84),
    ("pf_ohlc", "PF OHLC", 72),
    ("pf_tick", "PF TICK", 72),
    ("dd_ohlc", "DD OHLC", 72),
    ("dd_tick", "DD TICK", 72),
    ("trades_ohlc", "TR OHLC", 72),
    ("trades_tick", "TR TICK", 72),
    ("dates", "FECHAS", 180),
    ("set", "SET", 320),
)

# Tabla de las pantallas de seeds: una seed no tiene run, id de candidato ni
# generacion, por eso su juego de columnas es propio.
SEED_FINAL_TICK_COLUMNS = (
    ("sel", "SEL", 52),
    ("seed", "SEED", 230),
    ("symbol", "SIMBOLO", 110),
    ("period", "TF", 65),
    ("status", "ESTADO", 150),
    ("reason", "CAUSA", 270),
    ("hq", "HQ", 75),
    ("ohlc", "SCORE OHLC", 95),
    ("tick", "SCORE TICK", 95),
    ("from", "DESDE", 95),
    ("to", "HASTA", 95),
)


def final_tick_columns(stage: object) -> tuple[tuple[str, str, int], ...]:
    """Columnas de candidatos con la cabecera de veredicto de la etapa."""
    verdict = "6M" if is_six_month(stage) else "FINAL"
    return tuple(
        (key, verdict if key == "status" else heading, width)
        for key, heading, width in FINAL_TICK_COLUMNS
    )


class UBSFinalTickSharedMixin:
    """Botonera, criterios y tablas comunes a las pantallas Final Tick."""

    @staticmethod
    def _final_tick_stage_suffix(stage: object) -> str:
        return stage_suffix(stage)

    @staticmethod
    def _final_tick_is_six_month(stage: object) -> bool:
        return is_six_month(stage)

    def _final_tick_action_button(
        self, parent, text: str, command, column: int,
        *, primary: bool = False, last: bool = False, row: int = 0,
    ) -> None:
        """Boton de la barra de acciones: uno primario por pantalla."""
        tk.Button(
            parent,
            text=text,
            bg=self.colors["accent"] if primary else self.colors["panel"],
            fg="#ffffff" if primary else self.colors["muted"],
            relief="flat" if primary else "solid",
            borderwidth=0 if primary else 1,
            padx=10 if primary else 8,
            pady=5,
            font=("Segoe UI", 9, "bold") if primary else ("Segoe UI", 9),
            cursor="hand2",
            command=command,
        ).grid(
            row=row, column=column, sticky="e",
            padx=(0, 10 if last else 6), pady=(5, 3),
        )

    def _final_tick_row_button(self, parent, text: str, command, column: int) -> None:
        """Boton de la fila de artefactos, mas junto que el de la barra."""
        tk.Button(
            parent,
            text=text,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=command,
        ).grid(row=0, column=column, sticky="e", padx=(0, 4))

    def _final_tick_summary_label(self, parent, variable) -> None:
        tk.Label(
            parent,
            textvariable=variable,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))

    def _final_tick_criteria_fields(self, stage: object):
        """Fechas de la etapa y umbrales oficiales, ya resueltos a variables."""
        dates = tuple(
            (label, getattr(self, attribute), width)
            for label, attribute, width in FINAL_TICK_DATE_FIELDS[stage_suffix(stage)]
        )
        thresholds = tuple(
            (label, getattr(self, attribute), width)
            for label, attribute, width in FINAL_TICK_THRESHOLD_FIELDS
        )
        return dates, thresholds

    def _final_tick_criteria_field(
        self, container, row: int, index: int, label: str, variable, width: int,
    ) -> None:
        ttk.Label(container, text=label, style="Muted.TLabel").grid(
            row=row, column=index * 2 - 1, sticky="w", padx=(0, 4), pady=(0, 2)
        )
        ttk.Entry(container, textvariable=variable, width=width).grid(
            row=row, column=index * 2, sticky="w", padx=(0, 12), pady=(0, 2)
        )

    def _build_final_tick_criteria(self, container, stage: object, *, title: str) -> None:
        """Fechas en la primera fila y umbrales debajo, igual en las cuatro."""
        dates, thresholds = self._final_tick_criteria_fields(stage)
        ttk.Label(container, text=title, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 2)
        )
        for index, (label, variable, width) in enumerate(dates, start=1):
            self._final_tick_criteria_field(container, 0, index, label, variable, width)
        for position, (label, variable, width) in enumerate(thresholds):
            row, column = divmod(position, FINAL_TICK_CRITERIA_PER_ROW)
            self._final_tick_criteria_field(container, row + 1, column + 1, label, variable, width)

    def _build_final_tick_tree(
        self, holder, columns, *, height: int = FINAL_TICK_TREE_HEIGHT,
    ) -> ttk.Treeview:
        """Tabla con las columnas, los colores de veredicto y sus barras."""
        tree = ttk.Treeview(
            holder,
            columns=tuple(key for key, _heading, _width in columns),
            show="headings",
            height=height,
            selectmode="extended",
        )
        for key, heading, width in columns:
            tree.heading(key, text=heading)
            tree.column(key, width=width, minwidth=42, anchor="center", stretch=False)
        for tag, color in FINAL_TICK_TREE_TAGS:
            tree.tag_configure(tag, foreground=self.colors[color])
        self._make_tree_sortable(tree)
        self._attach_tree_scrollbars(holder, tree, 0, vertical=True)
        return tree
