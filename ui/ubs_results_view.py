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
        results.rowconfigure(3, weight=1)

        results_bar = tk.Frame(results, bg=self.colors["panel_alt"])
        results_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        results_bar.columnconfigure(0, weight=1)
        tk.Label(
            results_bar,
            textvariable=self.ubs_results_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(
            results_bar,
            text="Abrir output",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_ubs_output_dir,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Abrir set",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_set,
        ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Abrir reporte",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_report,
        ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Reprobar mismatch",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._retry_selected_ubs_mismatch,
        ).grid(row=0, column=4, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Reprobar run",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._retry_visible_ubs_run_mismatches,
        ).grid(row=0, column=5, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Limpiar vista",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._hide_latest_ubs_results,
        ).grid(row=0, column=6, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            results_bar,
            text="Actualizar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._refresh_ubs_results_panel,
        ).grid(row=0, column=7, sticky="e", padx=(0, 10), pady=4)

        ttk.Label(results, textvariable=self.ubs_results_status, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 6)
        )

        table_frame = ttk.Frame(results, style="Panel.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("run", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "trades", "set")
        self.ubs_results_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        headings = {
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
            "set": "SET",
        }
        widths = {
            "run": 56,
            "gen": 50,
            "status": 86,
            "symbol": 96,
            "period": 58,
            "score": 82,
            "profit": 90,
            "pf": 72,
            "dd": 72,
            "trades": 74,
            "set": 240,
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
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._refresh_ubs_history_panel,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=4)

        runs_frame = ttk.Frame(panel, style="Panel.TFrame")
        runs_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))
        runs_frame.columnconfigure(0, weight=1)
        run_columns = ("id", "created", "gens", "variants", "seeds", "backtests", "hidden", "total", "accepted", "rejected", "output")
        self.ubs_history_runs_tree = ttk.Treeview(runs_frame, columns=run_columns, show="headings", height=6)
        run_headings = {
            "id": "RUN", "created": "FECHA", "gens": "GENS", "variants": "VAR/SET",
            "seeds": "SEEDS", "backtests": "BT", "hidden": "ARCH", "total": "TOTAL",
            "accepted": "OK", "rejected": "BAD", "output": "OUTPUT",
        }
        run_widths = {"id": 56, "created": 150, "gens": 54, "variants": 70, "seeds": 70, "backtests": 50, "hidden": 55, "total": 70, "accepted": 55, "rejected": 55, "output": 380}
        for column in run_columns:
            self.ubs_history_runs_tree.heading(column, text=run_headings[column])
            self.ubs_history_runs_tree.column(column, width=run_widths[column], anchor="center", stretch=False)
        self._make_tree_sortable(self.ubs_history_runs_tree)
        self._attach_tree_scrollbars(runs_frame, self.ubs_history_runs_tree, 0, vertical=False)
        self.ubs_history_runs_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_ubs_history_candidates())

        candidates_panel = ttk.Frame(panel, style="Panel.TFrame")
        candidates_panel.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 18))
        candidates_panel.columnconfigure(0, weight=1)
        candidates_panel.rowconfigure(1, weight=1)
        ttk.Label(candidates_panel, textvariable=self.ubs_history_candidate_summary, style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        cand_columns = ("id", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "trades", "set")
        self.ubs_history_candidates_tree = ttk.Treeview(candidates_panel, columns=cand_columns, show="headings", height=12)
        cand_headings = {"id": "ID", "gen": "GEN", "status": "ESTADO", "symbol": "SYMBOL", "period": "TF", "score": "SCORE", "profit": "NET", "pf": "PF", "dd": "DD %", "trades": "TRADES", "set": "SET"}
        cand_widths = {"id": 60, "gen": 50, "status": 86, "symbol": 96, "period": 58, "score": 82, "profit": 90, "pf": 72, "dd": 72, "trades": 74, "set": 240}
        for column in cand_columns:
            self.ubs_history_candidates_tree.heading(column, text=cand_headings[column])
            self.ubs_history_candidates_tree.column(column, width=cand_widths[column], anchor="center", stretch=False)
        self.ubs_history_candidates_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_history_candidates_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self.ubs_history_candidates_tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(self.ubs_history_candidates_tree)
        self._attach_tree_scrollbars(candidates_panel, self.ubs_history_candidates_tree, 1)
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
        tk.Button(
            bar, text="Abrir seed", bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._open_selected_ubs_compare_seed,
        ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            bar, text="Abrir set", bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._open_selected_ubs_compare_set,
        ).grid(row=0, column=4, sticky="e", padx=(0, 6), pady=4)
        tk.Button(
            bar, text="Reporte completo", bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._generate_ubs_compare_report,
        ).grid(row=0, column=5, sticky="e", padx=(0, 10), pady=4)
        tk.Button(
            bar, text="Actualizar", bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=10, pady=4, font=("Segoe UI", 9),
            cursor="hand2", command=self._refresh_ubs_comparison_panel,
        ).grid(row=0, column=6, sticky="e", padx=(0, 10), pady=4)

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
        accepted_columns = ("run", "gen", "status", "symbol", "period", "score", "profit", "pf", "dd", "set")
        self.ubs_compare_sets_tree = ttk.Treeview(accepted_frame, columns=accepted_columns, show="headings", height=18)
        accepted_headings = {"run": "RUN", "gen": "GEN", "status": "ESTADO", "symbol": "SYMBOL", "period": "TF", "score": "SCORE", "profit": "NET", "pf": "PF", "dd": "DD %", "set": "SET"}
        accepted_widths = {"run": 50, "gen": 44, "status": 82, "symbol": 82, "period": 46, "score": 72, "profit": 82, "pf": 58, "dd": 62, "set": 220}
        for column in accepted_columns:
            self.ubs_compare_sets_tree.heading(column, text=accepted_headings[column])
            self.ubs_compare_sets_tree.column(column, width=accepted_widths[column], anchor="center", stretch=False)
        self.ubs_compare_sets_tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        self.ubs_compare_sets_tree.tag_configure("rejected", foreground=self.colors["danger"])
        self._make_tree_sortable(self.ubs_compare_sets_tree)
        self.ubs_compare_sets_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_ubs_comparison_diff())
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

