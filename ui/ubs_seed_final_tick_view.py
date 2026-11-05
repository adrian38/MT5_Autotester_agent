"""Vista de UBS Seeds Final Tick."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


SEED_FINAL_TICK_COLUMNS = (
    ("sel", "SEL", 52), ("seed", "SEED", 230), ("symbol", "SIMBOLO", 110),
    ("period", "TF", 65), ("status", "ESTADO", 150), ("reason", "CAUSA", 270),
    ("hq", "HQ", 75), ("ohlc", "SCORE OHLC", 95), ("tick", "SCORE TICK", 95),
    ("from", "DESDE", 95), ("to", "HASTA", 95),
)


class UBSSeedFinalTickViewMixin:
    def _init_seed_final_tick_screen_state(self, stage: str) -> None:
        suffix = "_6m" if stage == "six_month" else ""
        if hasattr(self, f"ubs_seed_final_tick{suffix}_summary"):
            return
        label = "Seeds Final Tick 6M" if stage == "six_month" else "Seeds Final Tick"
        setattr(self, f"ubs_seed_final_tick{suffix}_summary", tk.StringVar(value=f"{label}: sin evaluar"))
        setattr(self, f"ubs_seed_final_tick{suffix}_status", tk.StringVar(value="Sin resultados"))
        setattr(self, f"ubs_seed_final_tick{suffix}_paths", {})
        setattr(self, f"ubs_seed_final_tick{suffix}_checked", set())

    @staticmethod
    def _seed_final_tick_action_names(stage: str) -> tuple[str, str, str, str]:
        suffix = "_6m" if stage == "six_month" else ""
        return (
            f"_run_ubs_seed_final_tick{suffix}",
            f"_rerun_ubs_seed_final_tick{suffix}",
            f"_retry_ubs_seed_final_tick{suffix}_quality",
            f"_reconcile_ubs_seed_final_tick{suffix}",
        )

    def _add_seed_final_tick_button(self, bar, text, command, column, *, primary=False, last=False):
        tk.Button(
            bar, text=text,
            bg=self.colors["accent"] if primary else self.colors["panel"],
            fg="#ffffff" if primary else self.colors["muted"],
            relief="flat" if primary else "solid", borderwidth=0 if primary else 1,
            padx=10 if primary else 8, pady=5,
            font=("Segoe UI", 9, "bold") if primary else ("Segoe UI", 9),
            cursor="hand2", command=command,
        ).grid(row=0, column=column, sticky="e", padx=(0, 10 if last else 6), pady=5)

    def _build_seed_final_tick_actions(self, panel, stage: str) -> None:
        suffix = "_6m" if stage == "six_month" else ""
        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        summary = getattr(self, f"ubs_seed_final_tick{suffix}_summary")
        tk.Label(
            bar, textvariable=summary, bg=self.colors["panel_alt"],
            fg=self.colors["muted"], font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        action_names = self._seed_final_tick_action_names(stage)
        labels = ("Continuar", "Reprobar todas", "Reintentar calidad", "Reconciliar")
        for column, (label, name) in enumerate(zip(labels, action_names), start=1):
            self._add_seed_final_tick_button(
                bar, label, getattr(self, name), column, primary=column == 1,
            )
        refresh_name = f"_refresh_ubs_seed_final_tick{suffix}"
        self._add_seed_final_tick_button(bar, "Guardar config", self._save_config_clicked, 5)
        self._add_seed_final_tick_button(bar, "Actualizar", getattr(self, refresh_name), 6, last=True)
        file_row = tk.Frame(bar, bg=self.colors["panel_alt"])
        file_row.grid(row=1, column=0, columnspan=7, sticky="e", padx=10, pady=(0, 5))
        for column, (label, kind) in enumerate((
            ("Abrir seed", "set"), ("Abrir OHLC", "ohlc"), ("Abrir Real Tick", "tick"),
        )):
            self._add_seed_final_tick_button(
                file_row, label,
                lambda selected_kind=kind: self._open_seed_final_tick_path(stage, selected_kind),
                column, last=column == 2,
            )

    def _build_seed_final_tick_dates(self, panel, stage: str) -> None:
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))
        if stage == "six_month":
            date_fields = (
                ("Desde", self.ubs_final_tick_6m_from_date),
                ("Hasta", self.ubs_final_tick_6m_to_date),
                ("Ops bajas desde", self.ubs_final_tick_6m_ohlc_from_date),
                ("Ops bajas hasta", self.ubs_final_tick_6m_ohlc_to_date),
            )
        else:
            date_fields = (
                ("Desde", self.ubs_final_tick_from_date),
                ("Hasta", self.ubs_final_tick_to_date),
            )
        threshold_fields = (
            ("Calidad >=", self.ubs_final_tick_min_history_quality),
            ("Min ops OHLC", self.ubs_final_tick_min_ohlc_trades),
            ("W1 FT ops", self.ubs_final_tick_min_trades_w1),
            ("MN FT ops", self.ubs_final_tick_min_trades_mn),
            ("PF delta %", self.ubs_final_tick_max_pf_delta_pct),
            ("DD delta %", self.ubs_final_tick_max_dd_delta_pct),
            ("Net delta %", self.ubs_final_tick_max_net_delta_pct),
            ("Trades delta %", self.ubs_final_tick_max_trades_delta_pct),
        )
        for index, (label, variable) in enumerate(date_fields):
            ttk.Label(row, text=f"{label}:", style="Panel.TLabel").grid(row=0, column=index * 2, padx=(0, 4))
            ttk.Entry(row, textvariable=variable, width=14).grid(row=0, column=index * 2 + 1, padx=(0, 12))
        for index, (label, variable) in enumerate(threshold_fields):
            field_row, field_index = divmod(index, 4)
            ttk.Label(row, text=label, style="Muted.TLabel").grid(row=field_row + 1, column=field_index * 2, padx=(0, 4), pady=(2, 0))
            ttk.Entry(row, textvariable=variable, width=8).grid(row=field_row + 1, column=field_index * 2 + 1, padx=(0, 12), pady=(2, 0))
        status = getattr(self, "ubs_seed_final_tick_6m_status" if stage == "six_month" else "ubs_seed_final_tick_status")
        ttk.Label(row, textvariable=status, style="Subtitle.TLabel").grid(row=3, column=0, columnspan=8, sticky="w", pady=(3, 0))

    def _build_seed_final_tick_table(self, panel, stage: str) -> None:
        suffix = "_6m" if stage == "six_month" else ""
        holder = ttk.Frame(panel, style="Panel.TFrame")
        holder.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 16))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        keys = tuple(column[0] for column in SEED_FINAL_TICK_COLUMNS)
        tree = ttk.Treeview(holder, columns=keys, show="headings", height=18)
        for key, heading, width in SEED_FINAL_TICK_COLUMNS:
            tree.heading(key, text=heading)
            tree.column(key, width=width, minwidth=42, anchor="center", stretch=False)
        tree.grid(row=0, column=0, sticky="nsew")
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])
        tree.tag_configure("pendiente", foreground=self.colors["muted"])
        self._make_tree_sortable(tree)
        self._attach_tree_scrollbars(holder, tree, 0)
        setattr(self, f"ubs_seed_final_tick{suffix}_tree", tree)
        tree.bind("<Button-1>", lambda event: self._on_seed_final_tick_click(event, stage), add="+")
        tree.bind("<Double-1>", lambda _event: self._open_seed_final_tick_path(stage, "set"))

    def _build_seed_final_tick_stage(self, parent, stage: str) -> None:
        self._init_seed_final_tick_screen_state(stage)
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        title = "UBS Seeds Final Tick 6M" if stage == "six_month" else "UBS Seeds Final Tick"
        panel = self._card(parent, title)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)
        self._build_seed_final_tick_actions(panel, stage)
        self._build_seed_final_tick_dates(panel, stage)
        self._build_seed_final_tick_table(panel, stage)

    def _build_ubs_seed_final_tick(self, parent) -> None:
        self._build_seed_final_tick_stage(parent, "probe")
