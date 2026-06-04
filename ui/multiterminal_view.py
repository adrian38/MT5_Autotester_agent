from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import ttk

from run_tests import REPORT_DIR


class MultiterminalViewMixin:
    def _build_multiterminal_inline(self, parent: ttk.Frame) -> None:
        bar = tk.Frame(parent, bg=self.colors["panel_alt"], highlightthickness=1, highlightbackground=self.colors["border"])
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=(4, 14))
        bar.columnconfigure(1, weight=1)
        tk.Label(bar, text="Multiterminal", bg=self.colors["panel_alt"], fg=self.colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(12, 10), pady=8)
        tk.Label(bar, textvariable=self.multiterminal_summary, bg=self.colors["panel_alt"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=8)
        self._toggle_switch_cls(
            bar,
            variable=self.multiterminal_enabled,
            command=self._on_multiterminal_changed,
            bg=self.colors["panel_alt"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="e", padx=(6, 8), pady=8)
        worker_spin = ttk.Spinbox(
            bar,
            from_=1,
            to=32,
            width=8,
            textvariable=self.multiterminal_workers,
            command=self._on_multiterminal_changed,
        )
        worker_spin.grid(row=0, column=3, sticky="e", padx=(0, 8), pady=8)
        worker_spin.bind("<FocusOut>", lambda _event: self._on_multiterminal_changed())
        worker_spin.bind("<Return>", lambda _event: self._on_multiterminal_changed())
        ttk.Button(
            bar,
            text="Configurar",
            style="Tool.TButton",
            command=lambda: self._show_section("multiterminal"),
        ).grid(row=0, column=4, sticky="e", padx=(0, 12), pady=8)
    def _build_multiterminal(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Multiterminales MT5")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        top = tk.Frame(panel, bg=self.colors["panel_alt"])
        top.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 12))
        top.columnconfigure(1, weight=1)
        tk.Label(top, text="Modo multiterminal", bg=self.colors["panel_alt"], fg=self.colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(10, 10), pady=8)
        tk.Label(top, textvariable=self.multiterminal_summary, bg=self.colors["panel_alt"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=8)
        self._toggle_switch_cls(
            top,
            variable=self.multiterminal_enabled,
            command=self._on_multiterminal_changed,
            bg=self.colors["panel_alt"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="e", padx=(0, 8), pady=8)
        ttk.Label(top, text="Terminales a usar", style="MutedBg.TLabel").grid(row=0, column=3, sticky="e", padx=(8, 6), pady=8)
        worker_spin = ttk.Spinbox(
            top,
            from_=1,
            to=32,
            width=6,
            textvariable=self.multiterminal_workers,
            command=self._on_multiterminal_changed,
        )
        worker_spin.grid(row=0, column=4, sticky="e", padx=(0, 8), pady=8)
        worker_spin.bind("<FocusOut>", lambda _event: self._on_multiterminal_changed())
        worker_spin.bind("<Return>", lambda _event: self._on_multiterminal_changed())
        tk.Button(top, text="Validar",
                  bg=self.colors["panel"], fg=self.colors["muted"],
                  relief="solid", borderwidth=1, padx=8, pady=5,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=self._validate_multiterminal_profiles,
                  ).grid(row=0, column=5, sticky="e", padx=(0, 6), pady=5)
        tk.Button(top, text="Guardar",
                  bg=self.colors["accent"], fg="#ffffff",
                  relief="flat", borderwidth=0, padx=10, pady=5,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._save_multiterminal_clicked,
                  ).grid(row=0, column=6, sticky="e", padx=(0, 10), pady=5)

        # ── PanedWindow: tabla izquierda | editor derecho (arrastra el divisor) ──
        paned = ttk.PanedWindow(panel, orient="horizontal")
        paned.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))

        # ── Panel izquierdo: tabla + botones ─────────────────────────────────
        left = ttk.Frame(paned, style="Panel.TFrame")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        paned.add(left, weight=7)

        table_frame = ttk.Frame(left, style="Panel.TFrame")
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("mark", "enabled", "name", "mt5_path", "data_dir", "experts_root", "ubs_ex5_file", "portable")
        self.multiterminal_tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                               height=14, selectmode="extended")
        headings = {
            "mark":         "SEL",
            "enabled":      "ON",
            "name":         "NOMBRE",
            "mt5_path":     "TERMINAL64.EXE",
            "data_dir":     "DATOS MT5",
            "experts_root": "MQL5\\EXPERTS",
            "ubs_ex5_file": "UBS .EX5",
            "portable":     "PORTABLE",
        }
        widths = {
            "mark":         48,
            "enabled":      52,
            "name":         140,
            "mt5_path":     250,
            "data_dir":     250,
            "experts_root": 220,
            "ubs_ex5_file": 200,
            "portable":     72,
        }
        for column in columns:
            self.multiterminal_tree.heading(column, text=headings[column])
            self.multiterminal_tree.column(column, width=widths[column], minwidth=42,
                                           anchor="center", stretch=False)
        self._attach_tree_scrollbars(table_frame, self.multiterminal_tree, 0)
        self._make_tree_sortable(self.multiterminal_tree)
        self.multiterminal_tree.bind("<<TreeviewSelect>>", self._on_multiterminal_tree_select)
        self.multiterminal_tree.bind("<Button-1>", self._on_multiterminal_tree_click)

        table_buttons = ttk.Frame(left, style="Panel.TFrame")
        table_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for col in range(5):
            table_buttons.columnconfigure(col, weight=1)
        ttk.Button(table_buttons, text="Añadir",    style="Tool.TButton",        command=self._add_multiterminal_profile).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Duplicar",  style="Tool.TButton",        command=self._duplicate_multiterminal_profile).grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Eliminar",  style="DangerOutline.TButton", command=self._delete_multiterminal_profile).grid(row=0, column=2, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Validar",   style="Tool.TButton",        command=self._validate_multiterminal_profiles).grid(row=0, column=3, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Guardar",   style="Primary.TButton",     command=self._save_multiterminal_clicked).grid(row=0, column=4, sticky="ew")

        # ── Panel derecho: editor con scroll horizontal ───────────────────────
        right = tk.Frame(paned, bg=self.colors["panel"],
                         highlightthickness=1, highlightbackground=self.colors["border"])
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        paned.add(right, weight=5)

        tk.Label(right, text="Editor de terminal",
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        # Canvas scrollable horizontalmente para las path rows largas
        e_canvas = tk.Canvas(right, bg=self.colors["panel"], highlightthickness=0)
        h_scroll = ttk.Scrollbar(right, orient="horizontal", command=e_canvas.xview)
        e_canvas.configure(xscrollcommand=h_scroll.set)
        e_canvas.grid(row=1, column=0, sticky="nsew")
        h_scroll.grid(row=2, column=0, sticky="ew")

        editor = tk.Frame(e_canvas, bg=self.colors["panel"])
        editor.columnconfigure(1, weight=1)
        win_id = e_canvas.create_window((0, 0), window=editor, anchor="nw")

        def _sync_canvas(event=None):
            e_canvas.configure(scrollregion=e_canvas.bbox("all"))

        def _fit_canvas(event=None):
            e_canvas.itemconfig(win_id, height=event.height)

        editor.bind("<Configure>", _sync_canvas)
        e_canvas.bind("<Configure>", _fit_canvas)

        state_row = tk.Frame(editor, bg=self.colors["panel"])
        state_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 8))
        state_row.columnconfigure(0, weight=1)
        ttk.Checkbutton(state_row, text="Habilitada", variable=self.mt_profile_enabled, style="Panel.TCheckbutton").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(state_row, text="Portable",   variable=self.mt_profile_portable, style="Panel.TCheckbutton").grid(row=0, column=1, sticky="e")
        ttk.Label(editor, text="Nombre", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(16, 10), pady=7)
        ttk.Entry(editor, textvariable=self.mt_profile_name).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=7)
        self._path_row(editor, "Terminal MT5",     self.mt_profile_mt5_path,     2, self._browse_file)
        self._path_row(editor, "Carpeta datos MT5",self.mt_profile_data_dir,     3, self._browse_dir)
        self._path_row(editor, "MQL5\\Experts",    self.mt_profile_experts_root, 4, self._browse_dir)
        self._path_row(editor, "Archivo UBS .ex5", self.mt_profile_ubs_ex5_file, 5, self._browse_profile_ex5_file)
        ttk.Button(editor, text="Aplicar fila", style="Primary.TButton",
                   command=self._apply_multiterminal_editor).grid(
            row=6, column=0, columnspan=3, sticky="ew", padx=16, pady=(12, 8))
        ttk.Label(editor,
                  text="La cantidad es un límite: se usan hasta N terminales habilitadas. Compilar sigue siendo secuencial.",
                  style="Muted.TLabel", wraplength=380, justify="left",
                  ).grid(row=7, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 14))

        self._refresh_multiterminal_tree()
        if self.multiterminal_profiles:
            self._select_multiterminal_profile(0)
        self._update_multiterminal_summary()
    def _build_ubs_multiterminal_row(self, parent: ttk.Frame, *, row: int) -> None:
        mt_row = tk.Frame(parent, bg=self.colors["panel_alt"], highlightthickness=1, highlightbackground=self.colors["border"])
        mt_row.grid(row=row, column=0, columnspan=6, sticky="ew", padx=20, pady=(10, 0))
        mt_row.columnconfigure(1, weight=1)
        tk.Label(
            mt_row,
            text="Multiterminal",
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(12, 10), pady=8)
        tk.Label(
            mt_row,
            textvariable=self.multiterminal_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=1, sticky="w", padx=(0, 10), pady=8)
        self._toggle_switch_cls(
            mt_row,
            variable=self.multiterminal_enabled,
            command=self._on_multiterminal_changed,
            bg=self.colors["panel_alt"],
            width=34,
            height=18,
        ).grid(row=0, column=2, sticky="e", padx=(0, 8), pady=8)
        ttk.Label(mt_row, text="Terminales a usar", style="MutedBg.TLabel").grid(row=0, column=3, sticky="e", padx=(0, 6), pady=8)
        worker_spin = ttk.Spinbox(
            mt_row,
            from_=1,
            to=32,
            width=6,
            textvariable=self.multiterminal_workers,
            command=self._on_multiterminal_changed,
        )
        worker_spin.grid(row=0, column=4, sticky="e", padx=(0, 8), pady=8)
        worker_spin.bind("<FocusOut>", lambda _event: self._on_multiterminal_changed())
        worker_spin.bind("<Return>", lambda _event: self._on_multiterminal_changed())
        ttk.Button(
            mt_row,
            text="Configurar",
            style="Tool.TButton",
            command=lambda: self._show_section("multiterminal"),
        ).grid(row=0, column=5, sticky="e", padx=(0, 12), pady=8)

