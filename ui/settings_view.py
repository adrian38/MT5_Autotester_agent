from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class SettingsViewMixin:
    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        canvas = tk.Canvas(parent, bg=self.colors["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=(0, 0, 10, 0))
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        content.columnconfigure(0, weight=1)

        # Mouse wheel scrolling: only active while pointer is over the settings section
        def _on_wheel(event):
            delta = -1 if event.delta > 0 else 1
            if hasattr(event, "num"):
                if event.num == 4:
                    delta = -1
                elif event.num == 5:
                    delta = 1
            canvas.yview_scroll(delta, "units")

        def _bind_wheel(_e=None):
            self.bind_all("<MouseWheel>", _on_wheel)
            self.bind_all("<Button-4>", _on_wheel)
            self.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_e=None):
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        for widget in (canvas, parent, content):
            widget.bind("<Enter>", _bind_wheel)
            widget.bind("<Leave>", _unbind_wheel)

        paths = self._card(content, "Rutas")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        paths.columnconfigure(1, weight=1)
        self._path_row(paths, "Carpeta .mq5", self.compile_root, 1, self._browse_dir)
        self._path_row(paths, "Archivo .mq5", self.compile_file, 2, self._browse_mq5_file)
        self._path_row(paths, "Carpeta .ex5", self.experts_root, 3, self._browse_dir)
        self._path_row(paths, "Archivo .set UBS", self.ubs_set_file, 4, self._browse_set_file)
        self._path_row(paths, "Template tester", self.template_path, 5, self._browse_template_file)
        self._rounded_button_cls(
            paths, text="Guardar rutas",
            bg=self.colors["accent"], hover_bg=self.colors["accent_hover"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=10,
            parent_bg=self.colors["panel"],
            command=self._save_paths_clicked,
        ).grid(row=6, column=0, columnspan=3, sticky="ew", padx=20, pady=(12, 18))

        tester = self._card(content, "Tester")
        tester.grid(row=1, column=0, sticky="ew")
        for column in (1, 3):
            tester.columnconfigure(column, weight=1)

        fields = [
            ("Symbol", "Simbolo"),
            ("Period", "Timeframe"),
            ("Model", "Modelo"),
            ("FromDate", "Desde"),
            ("ToDate", "Hasta"),
            ("Deposit", "Deposito"),
            ("Currency", "Divisa"),
            ("Leverage", "Apalancamiento"),
            ("Optimization", "Optimizacion"),
            ("Visual", "Visual"),
            ("ReplaceReport", "Reemplazar reporte"),
            ("ShutdownTerminal", "Cerrar MT5"),
        ]
        for index, (key, label) in enumerate(fields):
            row = 1 + index // 2
            col = (index % 2) * 2
            self.tester_vars[key] = tk.StringVar()
            left_pad = 20 if col == 0 else 10
            right_pad = 10 if col == 0 else 20
            ttk.Label(tester, text=label, style="Panel.TLabel").grid(row=row, column=col, sticky="w", padx=(left_pad, 10), pady=7)
            ttk.Entry(tester, textvariable=self.tester_vars[key]).grid(row=row, column=col + 1, sticky="ew", padx=(0, right_pad), pady=7)
        self._rounded_button_cls(
            tester, text="Cargar template.ini",
            bg=self.colors["primary"], fg=self.colors["primary_text"],
            hover_bg=self.colors["primary_container"], hover_fg=self.colors["primary_hover_text"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=10,
            parent_bg=self.colors["panel"],
            command=self._load_template_clicked,
        ).grid(row=7, column=0, columnspan=4, sticky="ew", padx=20, pady=(10, 0))
        self._rounded_button_cls(
            tester, text="Guardar tester_template.ini",
            bg=self.colors["accent"], hover_bg=self.colors["accent_hover"],
            font=("Segoe UI", 10, "bold"),
            radius=12, padx=18, pady=10,
            parent_bg=self.colors["panel"],
            command=self._save_template_clicked,
        ).grid(row=8, column=0, columnspan=4, sticky="ew", padx=20, pady=(8, 18))

