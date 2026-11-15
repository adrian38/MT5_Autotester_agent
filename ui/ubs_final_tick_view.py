"""Vista de UBS Final Tick, parametrizada por etapa (corto y 6M)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.ubs_final_tick_shared import (
    UBSFinalTickSharedMixin,
    final_tick_columns,
    stage_label,
    stage_suffix,
)


class UBSFinalTickViewMixin(UBSFinalTickSharedMixin):
    def _final_tick_screen_var(self, stage: str, name: str):
        return getattr(self, f"ubs_final_tick{stage_suffix(stage)}_{name}")

    def _build_final_tick_panel(self, parent, stage: str):
        """Panel de la pantalla y su barra de resumen."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, f"{stage_label(stage)} UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        bar.columnconfigure(0, weight=1)
        return panel, bar

    @staticmethod
    def _final_tick_action_names(stage: str) -> tuple[str, str, str, str]:
        suffix = stage_suffix(stage)
        return (
            f"_run_ubs_final_tick{suffix}_for_latest_run",
            f"_rerun_ubs_final_tick{suffix}_for_latest_run",
            f"_retry_ubs_final_tick{suffix}_pending_quality",
            f"_refresh_ubs_final_tick{suffix}_panel",
        )

    def _build_final_tick_actions(self, bar, stage: str) -> None:
        """Resumen y botones de accion de la barra superior."""
        self._final_tick_summary_label(bar, self._final_tick_screen_var(stage, "summary"))
        run_name, rerun_name, retry_name, refresh_name = self._final_tick_action_names(stage)
        short_label = "6M" if self._final_tick_is_six_month(stage) else "Final Tick"
        for column, (label, name) in enumerate(
            (
                (f"Continuar {short_label}", run_name),
                (f"Reprobar {short_label}", rerun_name),
                ("Reintentar calidad baja", retry_name),
            ),
            start=1,
        ):
            self._final_tick_action_button(
                bar, label, getattr(self, name), column, primary=column == 1,
            )
        self._final_tick_action_button(bar, "Guardar config", self._save_config_clicked, 4)
        self._final_tick_action_button(
            bar, "Actualizar", getattr(self, refresh_name), 5, last=True,
        )

    def _build_final_tick_run_row(self, bar, stage: str) -> None:
        """Selector de run y accesos a los artefactos del candidato."""
        suffix = stage_suffix(stage)
        row1 = tk.Frame(bar, bg=self.colors["panel_alt"])
        row1.grid(row=1, column=0, columnspan=6, sticky="ew", padx=10, pady=(0, 5))
        row1.columnconfigure(1, weight=1)
        tk.Label(
            row1,
            text="Run:",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 4))
        combo = ttk.Combobox(
            row1,
            textvariable=self._final_tick_screen_var(stage, "run_id"),
            state="readonly",
            width=58,
        )
        combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_final_tick_stage(stage),
        )
        setattr(self, f"ubs_final_tick{suffix}_run_combo", combo)
        actions = (
            ("Abrir set", "set"),
            ("Abrir OHLC", "ohlc_report"),
            ("Abrir Real Tick", "real_report"),
        )
        for column, (label, kind) in enumerate(actions, start=3):
            self._final_tick_row_button(
                row1, label,
                lambda selected_kind=kind: self._open_final_tick_path(stage, selected_kind),
                column,
            )
        for column, (label, status) in enumerate(
            (("Manual OK", "accepted"), ("Manual FAIL", "rejected")), start=6,
        ):
            self._final_tick_row_button(
                row1, label,
                lambda marked_status=status: self._manual_mark_final_tick(stage, marked_status),
                column,
            )

    def _build_final_tick_criteria_block(self, panel, stage: str) -> None:
        """Estado y criterios de aceptacion de la etapa."""
        ttk.Label(
            panel,
            textvariable=self._final_tick_screen_var(stage, "status"),
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="ew", padx=20, pady=(6, 6))

        crit = ttk.Frame(panel, style="Panel.TFrame")
        crit.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))
        self._build_final_tick_criteria(crit, stage, title=f"{stage_label(stage)}:")

    def _build_final_tick_table(self, panel, stage: str) -> None:
        """Tabla de candidatos con sus columnas y etiquetas."""
        table_frame = ttk.Frame(panel, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        tree = self._build_final_tick_tree(table_frame, final_tick_columns(stage))
        tree.bind("<Button-1>", lambda event: self._on_final_tick_tree_click(event, stage))
        tree.bind(
            "<Double-1>",
            lambda _event: self._open_final_tick_path(stage, "real_report"),
        )
        setattr(self, f"ubs_final_tick{stage_suffix(stage)}_tree", tree)

    def _build_final_tick_stage(self, parent: ttk.Frame, stage: str) -> None:
        panel, bar = self._build_final_tick_panel(parent, stage)
        self._build_final_tick_actions(bar, stage)
        self._build_final_tick_run_row(bar, stage)
        self._build_final_tick_criteria_block(panel, stage)
        self._build_final_tick_table(panel, stage)

    def _build_ubs_final_tick(self, parent: ttk.Frame) -> None:
        self._build_final_tick_stage(parent, "probe")
