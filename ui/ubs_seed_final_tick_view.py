"""Pantallas Seeds Tick y Seeds Tick 6M.

Un solo constructor sirve a las dos etapas: lo unico que cambia es el titulo,
las dos fechas propias y a que envoltorio llaman los botones. Los umbrales que
se editan son los mismos del Final Tick de candidatos -las mismas variables-
para que el criterio no pueda quedar en dos sitios con valores distintos.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


SEED_FINAL_TICK_HEADINGS = {
    "status": "ESTADO",
    "cause": "CAUSA",
    "symbol": "SYMBOL",
    "period": "TF",
    "quality": "CALIDAD",
    "seed_score": "SCORE SEED",
    "ohlc_score": "SCORE OHLC",
    "tick_score": "SCORE TICK",
    "net_ohlc": "NET OHLC",
    "net_tick": "NET TICK",
    "pf_ohlc": "PF OHLC",
    "pf_tick": "PF TICK",
    "dd_ohlc": "DD OHLC",
    "dd_tick": "DD TICK",
    "trades_ohlc": "TR OHLC",
    "trades_tick": "TR TICK",
    "dates": "FECHAS",
    "seed": "SEED",
}
SEED_FINAL_TICK_WIDTHS = {
    "status": 96,
    "cause": 230,
    "symbol": 90,
    "period": 52,
    "quality": 78,
    "seed_score": 88,
    "ohlc_score": 88,
    "tick_score": 88,
    "net_ohlc": 84,
    "net_tick": 84,
    "pf_ohlc": 72,
    "pf_tick": 72,
    "dd_ohlc": 72,
    "dd_tick": 72,
    "trades_ohlc": 72,
    "trades_tick": 72,
    "dates": 180,
    "seed": 520,
}
SEED_FINAL_TICK_COLUMNS = tuple(SEED_FINAL_TICK_HEADINGS)


class UBSSeedFinalTickViewMixin:
    def _seed_final_tick_button(self, parent, text, command, column, *, accent=False, padx=(0, 6)):
        tk.Button(
            parent,
            text=text,
            bg=self.colors["accent"] if accent else self.colors["panel"],
            fg="#ffffff" if accent else self.colors["muted"],
            relief="flat" if accent else "solid",
            borderwidth=0 if accent else 1,
            padx=10 if accent else 8,
            pady=5,
            font=("Segoe UI", 9, "bold") if accent else ("Segoe UI", 9),
            cursor="hand2",
            command=command,
        ).grid(row=0, column=column, sticky="e", padx=padx, pady=(5, 3))

    def _build_seed_final_tick_bar(self, bar, *, summary_var, continue_command, rerun_command, refresh_command):
        tk.Label(
            bar,
            textvariable=summary_var,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))
        self._seed_final_tick_button(bar, "Continuar", continue_command, 1, accent=True)
        self._seed_final_tick_button(bar, "Reprobar todas", rerun_command, 2)
        self._seed_final_tick_button(bar, "Guardar config", self._save_config_clicked, 3)
        self._seed_final_tick_button(bar, "Actualizar", refresh_command, 4, padx=(0, 10))

    def _build_seed_final_tick_file_row(self, bar, commands):
        row1 = tk.Frame(bar, bg=self.colors["panel_alt"])
        row1.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 5))
        row1.columnconfigure(0, weight=1)
        for column, (label, command) in enumerate(commands, start=1):
            tk.Button(
                row1,
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
            ).grid(row=0, column=column, sticky="e", padx=(0, 4))

    def _build_seed_final_tick_criteria(self, panel, *, status_var, title, from_var, to_var):
        ttk.Label(panel, textvariable=status_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="ew", padx=20, pady=(6, 6)
        )
        crit = ttk.Frame(panel, style="Panel.TFrame")
        crit.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))
        fields = [
            (0, "Desde", from_var, 14),
            (0, "Hasta", to_var, 14),
            (0, "Calidad >=", self.ubs_final_tick_min_history_quality, 8),
            (0, "Min ops OHLC", self.ubs_final_tick_min_ohlc_trades, 8),
            (1, "PF delta %", self.ubs_final_tick_max_pf_delta_pct, 8),
            (1, "DD delta %", self.ubs_final_tick_max_dd_delta_pct, 8),
            (1, "Net delta %", self.ubs_final_tick_max_net_delta_pct, 8),
            (1, "Trades delta %", self.ubs_final_tick_max_trades_delta_pct, 8),
            (2, "W1 FT ops", self.ubs_final_tick_min_trades_w1, 8),
            (2, "MN FT ops", self.ubs_final_tick_min_trades_mn, 8),
        ]
        ttk.Label(crit, text=title, style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        per_row_counts = {0: 0, 1: 0, 2: 0}
        for row, label, var, width in fields:
            per_row_counts[row] += 1
            col_index = per_row_counts[row]
            ttk.Label(crit, text=label, style="Muted.TLabel").grid(
                row=row, column=col_index * 2 - 1, sticky="w", padx=(0, 4), pady=(0, 2)
            )
            ttk.Entry(crit, textvariable=var, width=width).grid(
                row=row, column=col_index * 2, sticky="w", padx=(0, 12), pady=(0, 2)
            )

    def _build_seed_final_tick_table(self, panel, *, attr, open_real_report):
        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            table_frame,
            columns=SEED_FINAL_TICK_COLUMNS,
            show="headings",
            height=12,
            selectmode="extended",
        )
        setattr(self, attr, tree)
        for column in SEED_FINAL_TICK_COLUMNS:
            tree.heading(column, text=SEED_FINAL_TICK_HEADINGS[column])
            tree.column(
                column,
                width=SEED_FINAL_TICK_WIDTHS[column],
                minwidth=42,
                anchor="center",
                stretch=False,
            )
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(tree)
        tree.bind("<Double-1>", lambda _event: open_real_report())
        self._attach_tree_scrollbars(table_frame, tree, 0, vertical=True)

    def _build_seed_final_tick_stage(
        self,
        parent: ttk.Frame,
        *,
        title: str,
        summary_var,
        status_var,
        from_var,
        to_var,
        tree_attr: str,
        continue_command,
        rerun_command,
        refresh_command,
        file_commands,
        open_real_report,
    ) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, title)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)

        self._build_seed_final_tick_bar(
            bar,
            summary_var=summary_var,
            continue_command=continue_command,
            rerun_command=rerun_command,
            refresh_command=refresh_command,
        )
        self._build_seed_final_tick_file_row(bar, file_commands)
        self._build_seed_final_tick_criteria(
            panel, status_var=status_var, title=f"{title}:", from_var=from_var, to_var=to_var,
        )
        self._build_seed_final_tick_table(panel, attr=tree_attr, open_real_report=open_real_report)

    def _build_ubs_seed_final_tick(self, parent: ttk.Frame) -> None:
        self._build_seed_final_tick_stage(
            parent,
            title="Seeds Tick UBS",
            summary_var=self.ubs_seed_final_tick_summary,
            status_var=self.ubs_seed_final_tick_status,
            from_var=self.ubs_seed_final_tick_from_date,
            to_var=self.ubs_seed_final_tick_to_date,
            tree_attr="ubs_seed_final_tick_tree",
            continue_command=self._run_ubs_seed_final_tick,
            rerun_command=self._rerun_ubs_seed_final_tick,
            refresh_command=self._refresh_ubs_seed_final_tick_panel,
            file_commands=[
                ("Abrir seed", self._open_selected_ubs_seed_final_tick_set),
                ("Abrir OHLC", self._open_selected_ubs_seed_final_tick_ohlc_report),
                ("Abrir Real Tick", self._open_selected_ubs_seed_final_tick_real_report),
                ("Manual OK", self._manual_accept_ubs_seed_final_tick),
                ("Manual FAIL", self._manual_reject_ubs_seed_final_tick),
            ],
            open_real_report=self._open_selected_ubs_seed_final_tick_real_report,
        )

    def _build_ubs_seed_final_tick_6m(self, parent: ttk.Frame) -> None:
        self._build_seed_final_tick_stage(
            parent,
            title="Seeds Tick 6M UBS",
            summary_var=self.ubs_seed_final_tick_6m_summary,
            status_var=self.ubs_seed_final_tick_6m_status,
            from_var=self.ubs_seed_final_tick_6m_from_date,
            to_var=self.ubs_seed_final_tick_6m_to_date,
            tree_attr="ubs_seed_final_tick_6m_tree",
            continue_command=self._run_ubs_seed_final_tick_6m,
            rerun_command=self._rerun_ubs_seed_final_tick_6m,
            refresh_command=self._refresh_ubs_seed_final_tick_6m_panel,
            file_commands=[
                ("Abrir seed", self._open_selected_ubs_seed_final_tick_6m_set),
                ("Abrir OHLC", self._open_selected_ubs_seed_final_tick_6m_ohlc_report),
                ("Abrir Real Tick", self._open_selected_ubs_seed_final_tick_6m_real_report),
                ("Manual OK", self._manual_accept_ubs_seed_final_tick_6m),
                ("Manual FAIL", self._manual_reject_ubs_seed_final_tick_6m),
            ],
            open_real_report=self._open_selected_ubs_seed_final_tick_6m_real_report,
        )
