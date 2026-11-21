"""Pantalla de terminales MT4: los mismos datos que MT5 menos los del EA."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ubs.account import BROKERS


class MT4TerminalViewMixin:
    """Tabla y editor de los perfiles de terminal MT4."""

    def _build_mt4_terminal_panel(self, parent):
        """Panel de la pantalla y su barra de acciones."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Terminales MT4")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)

        top = tk.Frame(panel, bg=self.colors["panel_alt"])
        top.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 12))
        top.columnconfigure(1, weight=1)
        tk.Label(top, text="Solo lectura de cuenta", bg=self.colors["panel_alt"],
                 fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=(10, 10), pady=8)
        tk.Label(top, textvariable=self.mt4_terminal_summary, bg=self.colors["panel_alt"],
                 fg=self.colors["muted"], font=("Segoe UI", 9)).grid(
            row=0, column=1, sticky="w", padx=(0, 10), pady=8)
        tk.Button(top, text="Validar",
                  bg=self.colors["panel"], fg=self.colors["muted"],
                  relief="solid", borderwidth=1, padx=8, pady=5,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=self._validate_mt4_terminal_profiles,
                  ).grid(row=0, column=2, sticky="e", padx=(0, 6), pady=5)
        tk.Button(top, text="Guardar",
                  bg=self.colors["accent"], fg="#ffffff",
                  relief="flat", borderwidth=0, padx=10, pady=5,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._save_mt4_terminal_clicked,
                  ).grid(row=0, column=3, sticky="e", padx=(0, 10), pady=5)
        return panel

    def _build_mt4_terminal_table(self, paned):
        """Tabla de perfiles MT4 y sus botones."""
        left = ttk.Frame(paned, style="Panel.TFrame")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        paned.add(left, weight=5)

        table_frame = ttk.Frame(left, style="Panel.TFrame")
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("mark", "enabled", "broker", "name", "mt4_path", "data_dir")
        self.mt4_terminal_tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                              height=14, selectmode="browse")
        headings = {
            "mark":     "SEL",
            "enabled":  "ON",
            "broker":   "BROKER",
            "name":     "NOMBRE",
            "mt4_path": "TERMINAL.EXE",
            "data_dir": "DATOS MT4",
        }
        widths = {
            "mark":     48,
            "enabled":  52,
            "broker":   100,
            "name":     160,
            "mt4_path": 300,
            "data_dir": 300,
        }
        for column in columns:
            self.mt4_terminal_tree.heading(column, text=headings[column])
            self.mt4_terminal_tree.column(column, width=widths[column], minwidth=42,
                                          anchor="center", stretch=False)
        self._attach_tree_scrollbars(table_frame, self.mt4_terminal_tree, 0)
        self._make_tree_sortable(self.mt4_terminal_tree)
        self.mt4_terminal_tree.bind("<<TreeviewSelect>>", self._on_mt4_terminal_tree_select)
        self.mt4_terminal_tree.bind("<Button-1>", self._on_mt4_terminal_tree_click)

        self._build_mt4_terminal_table_buttons(left)

    def _build_mt4_terminal_table_buttons(self, left):
        """Alta, copia, borrado, validacion y guardado de la tabla."""
        table_buttons = ttk.Frame(left, style="Panel.TFrame")
        table_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for col in range(5):
            table_buttons.columnconfigure(col, weight=1)
        ttk.Button(table_buttons, text="Añadir",   style="Tool.TButton",
                   command=self._add_mt4_terminal_profile).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Duplicar", style="Tool.TButton",
                   command=self._duplicate_mt4_terminal_profile).grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Eliminar", style="DangerOutline.TButton",
                   command=self._delete_mt4_terminal_profile).grid(row=0, column=2, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Validar",  style="Tool.TButton",
                   command=self._validate_mt4_terminal_profiles).grid(row=0, column=3, sticky="ew", padx=(0, 4))
        ttk.Button(table_buttons, text="Guardar",  style="Primary.TButton",
                   command=self._save_mt4_terminal_clicked).grid(row=0, column=4, sticky="ew")

    def _build_mt4_terminal_editor_fields(self, editor):
        """Campos del perfil: estado, nombre, broker y rutas."""
        state_row = tk.Frame(editor, bg=self.colors["panel"])
        state_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 8))
        state_row.columnconfigure(0, weight=1)
        _cb_activa = ttk.Checkbutton(state_row, text="Activa",
                                     variable=self.mt4_profile_enabled,
                                     style="Panel.TCheckbutton")
        _cb_activa.grid(row=0, column=0, sticky="w")
        self._tooltip_cls(_cb_activa,
            "Marca la terminal MT4 como activa para su broker.\n"
            "Pueden estar activas varias a la vez: solo se leen cuentas,\n"
            "no se lanza ninguna pasada.")
        ttk.Label(editor, text="Nombre", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(16, 10), pady=7)
        ttk.Entry(editor, textvariable=self.mt4_profile_name).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=7)
        ttk.Label(editor, text="Broker", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", padx=(16, 10), pady=7)
        ttk.Combobox(
            editor,
            textvariable=self.mt4_profile_broker,
            values=BROKERS,
            state="readonly",
            width=16,
        ).grid(row=2, column=1, columnspan=2, sticky="w", padx=(0, 16), pady=7)
        self._path_row(editor, "Terminal MT4",      self.mt4_profile_mt4_path, 3, self._browse_file)
        self._path_row(editor, "Carpeta datos MT4", self.mt4_profile_data_dir, 4, self._browse_dir)
        ttk.Button(editor, text="Aplicar fila", style="Primary.TButton",
                   command=self._apply_mt4_terminal_editor).grid(
            row=5, column=0, columnspan=3, sticky="ew", padx=16, pady=(12, 8))
        ttk.Label(editor,
                  text="Estas terminales solo sirven para leer la cuenta: no hay carpeta "
                       "MQL4\\Experts ni archivo .ex4 porque aqui no se usa el EA.",
                  style="Muted.TLabel", wraplength=380, justify="left",
                  ).grid(row=6, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 14))

    def _build_mt4_terminal_editor(self, paned):
        """Editor desplazable del perfil MT4 seleccionado."""
        right = tk.Frame(paned, bg=self.colors["panel"],
                         highlightthickness=1, highlightbackground=self.colors["border"])
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        paned.add(right, weight=6)

        tk.Label(right, text="Editor de terminal MT4",
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        e_canvas = tk.Canvas(right, bg=self.colors["panel"], highlightthickness=0)
        h_scroll = ttk.Scrollbar(right, orient="horizontal", command=e_canvas.xview)
        e_canvas.configure(xscrollcommand=h_scroll.set)
        e_canvas.grid(row=1, column=0, sticky="nsew")
        h_scroll.grid(row=2, column=0, sticky="ew")

        editor = tk.Frame(e_canvas, bg=self.colors["panel"])
        editor.columnconfigure(1, weight=1)
        win_id = e_canvas.create_window((0, 0), window=editor, anchor="nw")

        def _sync_scroll(event=None):
            e_canvas.configure(scrollregion=e_canvas.bbox("all"))

        def _fit_editor(event=None):
            e_canvas.itemconfig(win_id, width=event.width, height=event.height)

        editor.bind("<Configure>", _sync_scroll)
        e_canvas.bind("<Configure>", _fit_editor)

        self._build_mt4_terminal_editor_fields(editor)

    def _build_mt4_terminal(self, parent: ttk.Frame) -> None:
        panel = self._build_mt4_terminal_panel(parent)

        paned = ttk.PanedWindow(panel, orient="horizontal")
        paned.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 18))

        self._build_mt4_terminal_table(paned)
        self._build_mt4_terminal_editor(paned)

        self._refresh_mt4_terminal_tree()
        self._update_mt4_terminal_summary()
