from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class UBSResultsViewMixin:
    def _build_ubs_results(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        results = self._card(parent, "Resultados Agente UBS")
        results.grid(row=0, column=0, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(4, weight=1)

        results_bar = tk.Frame(results, bg=self.colors["panel_alt"])
        results_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))
        results_bar.columnconfigure(0, weight=1)

        # ── Fila 0: resumen + acciones globales del run ──
        tk.Label(
            results_bar,
            textvariable=self.ubs_results_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 3))
        tk.Button(
            results_bar, text="⬇  Exportar run",
            bg=self.colors["accent"], fg="#ffffff",
            relief="flat", borderwidth=0, padx=10, pady=5,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
            command=self._export_ubs_results_run,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            results_bar, text="Actualizar",
            bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=8, pady=5,
            font=("Segoe UI", 9), cursor="hand2",
            command=self._refresh_ubs_results_panel,
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=(5, 3))
        tk.Button(
            results_bar, text="Limpiar vista",
            bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=8, pady=5,
            font=("Segoe UI", 9), cursor="hand2",
            command=self._hide_latest_ubs_results,
        ).grid(row=0, column=3, sticky="e", padx=(0, 10), pady=(5, 3))

        # ── Fila 1: acciones sobre la fila seleccionada ──
        row1 = tk.Frame(results_bar, bg=self.colors["panel_alt"])
        row1.grid(row=1, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 5))
        row1.columnconfigure(0, weight=1)
        tk.Label(row1, text="Fila seleccionada:", bg=self.colors["panel_alt"],
                 fg=self.colors["muted"], font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w")
        for col, (label, cmd) in enumerate([
            ("Abrir output",  self._open_ubs_output_dir),
            ("Abrir set",     self._open_selected_ubs_set),
            ("Abrir reporte", self._open_selected_ubs_report),
        ], start=1):
            tk.Button(row1, text=label, bg=self.colors["panel"], fg=self.colors["muted"],
                      relief="solid", borderwidth=1, padx=8, pady=5,
                      font=("Segoe UI", 9), cursor="hand2", command=cmd,
                      ).grid(row=0, column=col, sticky="e", padx=(0, 4))
        tk.Label(row1, text="|", bg=self.colors["panel_alt"],
                 fg=self.colors["border"], font=("Segoe UI", 9)).grid(row=0, column=4, padx=(4, 4))
        for col, (label, cmd) in enumerate([
            ("Reprobar mismatch", self._retry_selected_ubs_mismatch),
            ("Reprobar run",      self._retry_visible_ubs_run_mismatches),
        ], start=5):
            tk.Button(row1, text=label, bg=self.colors["panel"], fg=self.colors["muted"],
                      relief="solid", borderwidth=1, padx=8, pady=5,
                      font=("Segoe UI", 9), cursor="hand2", command=cmd,
                      ).grid(row=0, column=col, sticky="e", padx=(0, 4))

        ttk.Label(results, textvariable=self.ubs_results_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 4)
        )

        # ── Criterios de aceptación (read-only, refleja config del Agente) ──
        crit = ttk.Frame(results, style="Panel.TFrame")
        crit.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))
        ttk.Label(crit, text="Criterios agente:", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        _threshold_fields = [
            ("Net profit >", self.ubs_pass_min_net_profit),
            ("PF ≥",          self.ubs_pass_min_profit_factor),
            ("Trades ≥",      self.ubs_pass_min_trades),
            ("DD ≤ %",        self.ubs_pass_max_drawdown_pct),
            ("Recovery ≥",    self.ubs_pass_min_recovery_factor),
        ]
        for col, (label, var) in enumerate(_threshold_fields, start=1):
            ttk.Label(crit, text=label, style="Muted.TLabel").grid(row=0, column=col * 2 - 1, sticky="w", padx=(0, 4))
            ttk.Entry(crit, textvariable=var, width=8, state="readonly").grid(
                row=0, column=col * 2, sticky="w", padx=(0, 12)
            )

        table_frame = ttk.Frame(results, style="Panel.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("mark", "run", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "trades", "reason", "set")
        self.ubs_results_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10, selectmode="extended")
        headings = {
            "mark": "SEL",
            "run": "RUN",
            "gen": "GEN",
            "status": "ESTADO",
            "symbol": "SYMBOL",
            "period": "TF",
            "score": "SCORE",
            "profit": "NET",
            "pf": "PF",
            "dd": "DD %",
            "trades": "TRADES",
            "reason": "MOTIVO",
            "set": "SET",
        }
        widths = {
            "mark": 48,
            "run": 50,
            "gen": 44,
            "status": 86,
            "symbol": 90,
            "period": 52,
            "score": 78,
            "profit": 84,
            "pf": 66,
            "dd": 66,
            "trades": 68,
            "reason": 220,
            "set": 220,
        }
        for column in columns:
            self.ubs_results_tree.heading(column, text=headings[column])
            self.ubs_results_tree.column(
                column,
                width=widths[column],
                minwidth=42,
                anchor="center",
                stretch=False,
            )
        self.ubs_results_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_results_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_results_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_results_tree)
        self.ubs_results_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_report())
        self.ubs_results_tree.bind("<Button-1>", self._on_ubs_result_tree_click)
        self._attach_tree_scrollbars(table_frame, self.ubs_results_tree, 0)
    def _build_ubs_history(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Historico SQLite UBS")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        tk.Label(bar, textvariable=self.ubs_history_summary, bg=self.colors["panel_alt"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(
            bar, text="Actualizar", bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=8, pady=5, font=("Segoe UI", 9),
            cursor="hand2", command=self._refresh_ubs_history_panel,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=5)

        runs_frame = ttk.Frame(panel, style="Panel.TFrame")
        runs_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))
        runs_frame.columnconfigure(0, weight=1)
        run_columns = ("mark", "id", "created", "gens", "variants", "seeds", "backtests", "hidden", "total", "accepted", "rejected", "output")
        self.ubs_history_runs_tree = ttk.Treeview(runs_frame, columns=run_columns, show="headings",
                                                   height=6, selectmode="extended")
        run_headings = {
            "mark": "SEL", "id": "RUN", "created": "FECHA", "gens": "GENS", "variants": "VAR/SET",
            "seeds": "SEEDS", "backtests": "BT", "hidden": "ARCH", "total": "TOTAL",
            "accepted": "OK", "rejected": "BAD", "output": "OUTPUT",
        }
        run_widths = {"mark": 48, "id": 50, "created": 148, "gens": 50, "variants": 66, "seeds": 66, "backtests": 46, "hidden": 52, "total": 66, "accepted": 52, "rejected": 52, "output": 360}
        for column in run_columns:
            self.ubs_history_runs_tree.heading(column, text=run_headings[column])
            self.ubs_history_runs_tree.column(column, width=run_widths[column], anchor="center", stretch=False)
        self._make_tree_sortable(self.ubs_history_runs_tree)
        self._attach_tree_scrollbars(runs_frame, self.ubs_history_runs_tree, 0, vertical=False)
        self.ubs_history_runs_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_ubs_history_candidates())
        self.ubs_history_runs_tree.bind("<Button-1>", self._on_ubs_history_run_click)

        candidates_panel = ttk.Frame(panel, style="Panel.TFrame")
        candidates_panel.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))
        candidates_panel.columnconfigure(0, weight=1)
        candidates_panel.rowconfigure(1, weight=1)
        ttk.Label(candidates_panel, textvariable=self.ubs_history_candidate_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        cand_columns = ("mark", "id", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "trades", "set")
        self.ubs_history_candidates_tree = ttk.Treeview(candidates_panel, columns=cand_columns, show="headings",
                                                         height=12, selectmode="extended")
        cand_headings = {"mark": "SEL", "id": "ID", "gen": "GEN", "status": "ESTADO", "symbol": "SYMBOL", "period": "TF", "score": "SCORE", "profit": "NET", "pf": "PF", "dd": "DD %", "trades": "TRADES", "set": "SET"}
        cand_widths = {"mark": 48, "id": 54, "gen": 46, "status": 82, "symbol": 90, "period": 54, "score": 78, "profit": 84, "pf": 66, "dd": 66, "trades": 68, "set": 220}
        for column in cand_columns:
            self.ubs_history_candidates_tree.heading(column, text=cand_headings[column])
            self.ubs_history_candidates_tree.column(column, width=cand_widths[column], anchor="center", stretch=False)
        self.ubs_history_candidates_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_history_candidates_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_history_candidates_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_history_candidates_tree)
        self._attach_tree_scrollbars(candidates_panel, self.ubs_history_candidates_tree, 1)
        self.ubs_history_candidates_tree.bind("<Button-1>", self._on_ubs_history_candidate_click)
    def _build_ubs_comparison(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Comparar resultados contra seed")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        bar = tk.Frame(panel, bg=self.colors["panel_alt"])
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        bar.columnconfigure(0, weight=1)
        tk.Label(bar, textvariable=self.ubs_compare_summary, bg=self.colors["panel_alt"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(bar, text="Run", style="MutedBg.TLabel").grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        self.ubs_compare_run_combo = ttk.Combobox(
            bar,
            textvariable=self.ubs_compare_run_id,
            state="readonly",
            width=12,
        )
        self.ubs_compare_run_combo.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=4)
        self.ubs_compare_run_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_ubs_comparison())
        for col, (label, cmd) in enumerate([
            ("Abrir seed",       self._open_selected_ubs_compare_seed),
            ("Abrir set",        self._open_selected_ubs_compare_set),
            ("Reporte completo", self._generate_ubs_compare_report),
            ("Actualizar",       self._refresh_ubs_comparison_panel),
        ], start=3):
            padx = (0, 10) if col >= 5 else (0, 4)
            tk.Button(bar, text=label, bg=self.colors["panel"], fg=self.colors["muted"],
                      relief="solid", borderwidth=1, padx=8, pady=5, font=("Segoe UI", 9),
                      cursor="hand2", command=cmd,
                      ).grid(row=0, column=col, sticky="e", padx=padx, pady=5)

        body = ttk.Frame(panel, style="Panel.TFrame")
        body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        accepted_frame = ttk.Frame(body, style="Panel.TFrame")
        accepted_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        accepted_frame.columnconfigure(0, weight=1)
        accepted_frame.rowconfigure(1, weight=1)
        ttk.Label(accepted_frame, text="Resultados", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        accepted_columns = ("mark", "run", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "set")
        self.ubs_compare_sets_tree = ttk.Treeview(accepted_frame, columns=accepted_columns, show="headings",
                                                   height=18, selectmode="extended")
        accepted_headings = {"mark": "SEL", "run": "RUN", "gen": "GEN", "status": "ESTADO", "symbol": "SYMBOL", "period": "TF", "score": "SCORE", "profit": "NET", "pf": "PF", "dd": "DD %", "set": "SET"}
        accepted_widths = {"mark": 48, "run": 46, "gen": 40, "status": 78, "symbol": 78, "period": 44, "score": 68, "profit": 78, "pf": 54, "dd": 58, "set": 200}
        for column in accepted_columns:
            self.ubs_compare_sets_tree.heading(column, text=accepted_headings[column])
            self.ubs_compare_sets_tree.column(column, width=accepted_widths[column], anchor="center", stretch=False)
        self.ubs_compare_sets_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_compare_sets_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self._make_tree_sortable(self.ubs_compare_sets_tree)
        self.ubs_compare_sets_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_ubs_comparison_diff())
        self.ubs_compare_sets_tree.bind("<Button-1>", self._on_ubs_compare_click)
        self._attach_tree_scrollbars(accepted_frame, self.ubs_compare_sets_tree, 1)

        diff_panel = ttk.Frame(body, style="Panel.TFrame")
        diff_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        diff_panel.columnconfigure(0, weight=1)
        diff_panel.rowconfigure(1, weight=1)
        ttk.Label(diff_panel, textvariable=self.ubs_compare_detail, style="Muted.TLabel", wraplength=760).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        diff_columns = ("key", "seed", "accepted")
        self.ubs_compare_diff_tree = ttk.Treeview(diff_panel, columns=diff_columns, show="headings", height=18)
        for column, heading, width in (("key", "PARAMETRO", 210), ("seed", "SEED", 240), ("accepted", "ACEPTADO", 240)):
            self.ubs_compare_diff_tree.heading(column, text=heading)
            self.ubs_compare_diff_tree.column(column, width=width, anchor="center", stretch=False)
        self._make_tree_sortable(self.ubs_compare_diff_tree)
        self._attach_tree_scrollbars(diff_panel, self.ubs_compare_diff_tree, 1)

