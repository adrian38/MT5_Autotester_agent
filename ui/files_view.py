from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class FilesViewMixin:
    def _build_files(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        experts_panel = self._card(parent, "Expert Advisors detectados")
        experts_panel.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        experts_panel.columnconfigure(0, weight=1)
        experts_frame = ttk.Frame(experts_panel, style="Panel.TFrame")
        experts_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))
        experts_frame.columnconfigure(0, weight=1)
        self.experts_tree = ttk.Treeview(experts_frame, columns=("name",), show="headings", height=6)
        self.experts_tree.heading("name", text="ARCHIVO")
        self.experts_tree.column("name", stretch=False, minwidth=200)
        self.experts_tree.tag_configure("odd", background=self.colors["tree_odd"])
        self._make_tree_sortable(self.experts_tree)
        self._attach_tree_scrollbars(experts_frame, self.experts_tree, 0, horizontal=False)

        reports_panel = self._card(parent, "Generated Reports")
        reports_panel.grid(row=1, column=0, sticky="nsew")
        reports_panel.columnconfigure(0, weight=1)
        reports_panel.rowconfigure(2, weight=1)

        actions_bar = tk.Frame(reports_panel, bg=self.colors["panel_alt"])
        actions_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        actions_bar.columnconfigure(0, weight=1)
        tk.Label(actions_bar, text=f"Mostrando reportes de {REPORT_DIR.name}/",
                 bg=self.colors["panel_alt"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        tk.Button(actions_bar, text="📂  Abrir carpeta", bg=self.colors["panel"], fg=self.colors["muted"],
                  relief="solid", borderwidth=1, padx=8, pady=5, font=("Segoe UI", 9), cursor="hand2",
                  command=lambda: subprocess.Popen(["explorer", str(REPORT_DIR)]) if REPORT_DIR.exists() else None
                  ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=5)
        tk.Button(actions_bar, text="🗘  Actualizar", bg=self.colors["panel"], fg=self.colors["muted"],
                  relief="solid", borderwidth=1, padx=8, pady=5, font=("Segoe UI", 9), cursor="hand2",
                  command=self._refresh_reports
                  ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=5)
        tk.Button(actions_bar, text="Borrar antiguos", bg=self.colors["danger"], fg="#ffffff",
                  relief="flat", borderwidth=0, padx=8, pady=5, font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._delete_old_reports
                  ).grid(row=0, column=3, sticky="e", padx=(0, 10), pady=5)

        reports_frame = ttk.Frame(reports_panel, style="Panel.TFrame")
        reports_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))
        reports_frame.columnconfigure(0, weight=1)
        reports_frame.rowconfigure(0, weight=1)
        self.reports_tree = ttk.Treeview(reports_frame, columns=("name", "date", "size"), show="headings", height=10)
        self.reports_tree.heading("name", text="REPORT NAME")
        self.reports_tree.heading("date", text="DATE")
        self.reports_tree.heading("size", text="SIZE (KB)")
        self.reports_tree.column("name", stretch=False, minwidth=200)
        self.reports_tree.column("date", width=160, anchor="center", stretch=False)
        self.reports_tree.column("size", width=100, anchor="center", stretch=False)
        self.reports_tree.tag_configure("odd", background=self.colors["tree_odd"])
        self.reports_tree.tag_configure("even", background=self.colors["tree_even"])
        self._make_tree_sortable(self.reports_tree)
        self._attach_tree_scrollbars(reports_frame, self.reports_tree, 0)
    def _build_logs(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        term_bg = self.colors["primary_container"]

        terminal = self._rounded_card_cls(parent, radius=14, bg=term_bg, border="#334155",
                               parent_bg=self.colors["bg"])
        terminal.grid(row=0, column=0, sticky="nsew")
        terminal.columnconfigure(0, weight=1)
        terminal.rowconfigure(1, weight=1)

        header = tk.Frame(terminal, bg=term_bg)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 4))
        tk.Label(header, text="●", bg=term_bg, fg=self.colors["log_info"], font=("Segoe UI", 10)).grid(row=0, column=0, padx=(0, 6))
        tk.Label(header, text="LIVE LOG OUTPUT", bg=term_bg, fg=self.colors["log_muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")

        self.console = tk.Text(
            terminal,
            bg=term_bg,
            fg=self.colors["log_text"],
            insertbackground=self.colors["log_text"],
            relief="flat",
            padx=20, pady=10,
            wrap="word",
            font=("Consolas", 10),
            borderwidth=0,
        )
        self.console.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.console.tag_configure("info", foreground=self.colors["log_info"])
        self.console.tag_configure("error", foreground=self.colors["log_error"])
        self.console.tag_configure("warn", foreground="#ffb95f")
        self.console.tag_configure("debug", foreground=self.colors["log_debug"])
        self.console.tag_configure("muted", foreground=self.colors["log_muted"])
        self.console.tag_configure("telegram", foreground=self.colors["log_info"])

