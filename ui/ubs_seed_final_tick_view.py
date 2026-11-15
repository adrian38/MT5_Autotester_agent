"""Vista de UBS Seeds Final Tick, parametrizada por etapa (corto y 6M)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.ubs_final_tick_shared import (
    SEED_FINAL_TICK_COLUMNS,
    UBSFinalTickSharedMixin,
    stage_label,
    stage_suffix,
)


class UBSSeedFinalTickViewMixin(UBSFinalTickSharedMixin):
    def _init_seed_final_tick_screen_state(self, stage: str) -> None:
        suffix = stage_suffix(stage)
        if hasattr(self, f"ubs_seed_final_tick{suffix}_summary"):
            return
        label = stage_label(stage, "Seeds Final Tick")
        setattr(self, f"ubs_seed_final_tick{suffix}_summary", tk.StringVar(value=f"{label}: sin evaluar"))
        setattr(self, f"ubs_seed_final_tick{suffix}_status", tk.StringVar(value="Sin resultados"))
        setattr(self, f"ubs_seed_final_tick{suffix}_paths", {})
        setattr(self, f"ubs_seed_final_tick{suffix}_checked", set())

    @staticmethod
    def _seed_final_tick_action_names(stage: str) -> tuple[str, str, str, str]:
        suffix = stage_suffix(stage)
        return (
            f"_run_ubs_seed_final_tick{suffix}",
            f"_rerun_ubs_seed_final_tick{suffix}",
            f"_retry_ubs_seed_final_tick{suffix}_quality",
            f"_reconcile_ubs_seed_final_tick{suffix}",
        )

    def _build_seed_final_tick_actions(self, panel, stage: str) -> None:
        suffix = stage_suffix(stage)
        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        self._final_tick_summary_label(bar, getattr(self, f"ubs_seed_final_tick{suffix}_summary"))
        short_label = "6M" if self._final_tick_is_six_month(stage) else "Final Tick"
        labels = (
            f"Continuar {short_label}", f"Reprobar {short_label}",
            "Reintentar calidad baja", "Reconciliar",
        )
        for column, (label, name) in enumerate(
            zip(labels, self._seed_final_tick_action_names(stage)), start=1,
        ):
            self._final_tick_action_button(
                bar, label, getattr(self, name), column, primary=column == 1,
            )
        self._final_tick_action_button(bar, "Guardar config", self._save_config_clicked, 5)
        self._final_tick_action_button(
            bar, "Actualizar",
            getattr(self, f"_refresh_ubs_seed_final_tick{suffix}"), 6, last=True,
        )
        file_row = tk.Frame(bar, bg=self.colors["panel_alt"])
        file_row.grid(row=1, column=0, columnspan=7, sticky="ew", padx=10, pady=(0, 5))
        file_row.columnconfigure(0, weight=1)
        for column, (label, kind) in enumerate((
            ("Abrir seed", "set"), ("Abrir OHLC", "ohlc"), ("Abrir Real Tick", "tick"),
        ), start=1):
            self._final_tick_row_button(
                file_row, label,
                lambda selected_kind=kind: self._open_seed_final_tick_path(stage, selected_kind),
                column,
            )

    def _build_seed_final_tick_table(self, panel, stage: str) -> None:
        holder = ttk.Frame(panel, style="Panel.TFrame")
        holder.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        tree = self._build_final_tick_tree(holder, SEED_FINAL_TICK_COLUMNS)
        tree.bind("<Button-1>", lambda event: self._on_seed_final_tick_click(event, stage), add="+")
        tree.bind("<Double-1>", lambda _event: self._open_seed_final_tick_path(stage, "set"))
        setattr(self, f"ubs_seed_final_tick{stage_suffix(stage)}_tree", tree)

    def _build_seed_final_tick_stage(self, parent, stage: str) -> None:
        self._init_seed_final_tick_screen_state(stage)
        suffix = stage_suffix(stage)
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        panel = self._card(parent, f"{stage_label(stage, 'Seeds Final Tick')} UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)
        self._build_seed_final_tick_actions(panel, stage)
        ttk.Label(
            panel,
            textvariable=getattr(self, f"ubs_seed_final_tick{suffix}_status"),
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="ew", padx=20, pady=(6, 6))
        criteria = ttk.Frame(panel, style="Panel.TFrame")
        criteria.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))
        self._build_final_tick_criteria(
            criteria, stage, title=f"{stage_label(stage, 'Seed Final Tick')}:",
        )
        self._build_seed_final_tick_table(panel, stage)

    def _build_ubs_seed_final_tick(self, parent) -> None:
        self._build_seed_final_tick_stage(parent, "probe")
