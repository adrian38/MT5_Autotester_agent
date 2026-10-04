"""Estilo, navegacion y piezas reutilizables de la ventana principal."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from tkinter import messagebox

from ui.app_base import COLORS
from ui.app_widgets import _ButtonImageCache, _CornerImageCache
from ui.app_widgets import RoundedButton, RoundedCard


SIDEBAR_ITEMS = (
    ("panel", "▦  Panel"), ("multiterminal", "MT5  Multiterminales"),
    ("portfolio", "▤  Portfolio"), ("configuracion", "⚙  Configuracion"),
    ("archivos", "▤  Archivos"), ("logs", "≣  Logs"),
    ("agente_ubs", "UBS  Agente UBS"), ("ubs_seeds", "UBS  Seeds"),
    ("ubs_resultados", "UBS  Resultados"), ("ubs_robustez", "UBS  Robustez"),
    ("ubs_final_tick", "UBS  Final Tick"), ("ubs_final_tick_6m", "UBS  Final Tick 6M"),
    ("ubs_regression", "UBS  Regresiva"), ("ubs_historico", "UBS  Historico"),
    ("ubs_universo", "UBS  Universo"), ("ubs_comparar", "UBS  Comparar"),
    ("ubs_params", "UBS  Parámetros"), ("portafolio_ubs", "UBS  Portafolio"),
    ("portafolio_ubs_mensual", "UBS  Portafolio Mensual"),
    ("buscador", "UBS  Buscador"),
)


class AppChromeMixin:
    """Estilo, navegacion y piezas reutilizables de la ventana principal."""

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        base_font = ("Segoe UI", 10)
        style.configure(".", font=base_font, background=COLORS["bg"], foreground=COLORS["text"])
        self._configure_frame_styles(style)
        self._configure_label_styles(style)
        self._configure_button_styles(style)
        self._configure_input_styles(style)
        self._configure_table_styles(style)

    def _configure_frame_styles(self, style) -> None:
        """Fondos de los contenedores de la ventana."""
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Alt.TFrame", background=COLORS["panel_alt"])
        style.configure("Sidebar.TFrame", background=COLORS["sidebar_bg"])
        style.configure("Topbar.TFrame", background=COLORS["topbar_bg"])
        style.configure("Card.TFrame", background=COLORS["panel"], relief="solid", borderwidth=1)
        style.configure("CardSoft.TFrame", background=COLORS["panel_alt"])
        style.configure("Log.TFrame", background=COLORS["log_bg"])

    def _configure_label_styles(self, style) -> None:
        """Tipografia y color de cada tipo de etiqueta."""
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Sidebar.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["text"])
        style.configure("Topbar.TLabel", background=COLORS["topbar_bg"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("MutedBg.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("LabelCaps.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9, "bold"))
        style.configure("Metric.TLabel", background=COLORS["panel"], foreground=COLORS["primary"], font=("Segoe UI", 26, "bold"))
        style.configure("MetricName.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9, "bold"))
        style.configure("MetricIcon.TLabel", background=COLORS["panel"], foreground=COLORS["accent"], font=("Segoe UI Symbol", 16))
        style.configure("Title.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["primary"], font=("Segoe UI", 18, "bold"))
        style.configure("Version.TLabel", background=COLORS["sidebar_bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("AppTitle.TLabel", background=COLORS["topbar_bg"], foreground=COLORS["primary"], font=("Segoe UI", 16, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("SectionTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 14, "bold"))
        style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 11, "bold"))
        style.configure("CardDesc.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Mono.TLabel", background=COLORS["panel"], foreground=COLORS["primary"], font=("Consolas", 9))
        style.configure("MonoAlt.TLabel", background=COLORS["panel_alt"], foreground=COLORS["primary"], font=("Consolas", 9))
        style.configure("Chip.TLabel", background=COLORS["panel_highest"], foreground=COLORS["primary"], font=("Segoe UI", 8, "bold"), padding=(8, 3))
        style.configure("ChipAccent.TLabel", background=COLORS["accent_soft"], foreground=COLORS["accent_soft_text"], font=("Segoe UI", 8, "bold"), padding=(8, 3))

    def _configure_button_styles(self, style) -> None:
        """Botones principales, de peligro y de accion."""
        bold_font = ("Segoe UI", 10, "bold")
        style.configure("TButton", padding=(12, 8), borderwidth=0, background=COLORS["panel_alt"], foreground=COLORS["text"], font=bold_font)
        style.map("TButton", background=[("active", COLORS["panel_high"])])
        style.configure("Primary.TButton", background=COLORS["accent"], foreground="#ffffff", padding=(14, 9), font=bold_font)
        style.map("Primary.TButton", background=[("active", COLORS["accent_hover"]), ("disabled", "#8ba59c")])
        style.configure("PrimaryDark.TButton", background=COLORS["primary"], foreground=COLORS["primary_text"], padding=(14, 9), font=bold_font)
        style.map("PrimaryDark.TButton",
                  background=[("active", COLORS["primary_container"]), ("disabled", "#5e6b7e")],
                  foreground=[("active", COLORS["primary_hover_text"]), ("disabled", COLORS["primary_text"])])
        style.configure("Danger.TButton", background=COLORS["danger"], foreground="#ffffff", padding=(14, 9), font=bold_font)
        style.map("Danger.TButton", background=[("active", "#8a0d0d")])
        style.configure("DangerOutline.TButton", background=COLORS["topbar_bg"], foreground=COLORS["danger"], padding=(14, 6), font=bold_font)
        style.map("DangerOutline.TButton", background=[("active", COLORS["danger_soft"])])
        style.configure("Tool.TButton", padding=(8, 6))
        style.configure("Action.TButton", background=COLORS["panel"], foreground=COLORS["text"], padding=(12, 10), borderwidth=1, font=bold_font, anchor="w")
        style.map("Action.TButton", background=[("active", COLORS["panel_alt"])])

    def _configure_input_styles(self, style) -> None:
        """Campos de texto, spinbox y desplegables."""
        style.configure("TEntry", fieldbackground=COLORS["entry_bg"], foreground=COLORS["text"],
                        insertcolor=COLORS["text"], bordercolor=COLORS["border"],
                        lightcolor=COLORS["border"], padding=7)
        style.configure("TSpinbox", fieldbackground=COLORS["entry_bg"], foreground=COLORS["text"],
                        insertcolor=COLORS["text"], bordercolor=COLORS["border"], padding=7)
        style.configure("TCombobox", fieldbackground=COLORS["entry_bg"], foreground=COLORS["text"],
                        background=COLORS["entry_bg"], insertcolor=COLORS["text"],
                        bordercolor=COLORS["border"], arrowcolor=COLORS["text"], padding=7)
        style.map("TCombobox",
                  fieldbackground=[("readonly", COLORS["entry_bg"]), ("disabled", COLORS["panel"])],
                  foreground=[("readonly", COLORS["text"]), ("disabled", COLORS["muted"])],
                  selectbackground=[("readonly", COLORS["entry_bg"])],
                  selectforeground=[("readonly", COLORS["text"])])
        # Style the Listbox popup used by every Combobox dropdown
        self.option_add("*TCombobox*Listbox.background", COLORS["entry_bg"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.borderWidth", "0")

    def _configure_table_styles(self, style) -> None:
        """Tablas, casillas, radios y barras de progreso."""
        style.configure("Treeview", background=COLORS["tree_bg"], fieldbackground=COLORS["tree_bg"],
                        foreground=COLORS["text"], rowheight=26, borderwidth=0)
        style.map("Treeview", background=[("selected", COLORS["panel_highest"])], foreground=[("selected", COLORS["text"])])
        style.configure("Treeview.Heading", background=COLORS["panel_alt"], foreground=COLORS["muted"], font=("Segoe UI", 8, "bold"), padding=(6, 4))
        style.configure("TCheckbutton", background=COLORS["panel_alt"], foreground=COLORS["text"], focuscolor=COLORS["panel_alt"])
        style.map(
            "TCheckbutton",
            background=[
                ("disabled", COLORS["panel_alt"]),
                ("pressed", COLORS["panel_alt"]),
                ("active", COLORS["panel_alt"]),
                ("focus", COLORS["panel_alt"]),
                ("selected", COLORS["panel_alt"]),
                ("!disabled", COLORS["panel_alt"]),
            ],
            foreground=[
                ("disabled", COLORS["muted"]),
                ("pressed", COLORS["text"]),
                ("active", COLORS["text"]),
                ("focus", COLORS["text"]),
                ("selected", COLORS["text"]),
                ("!disabled", COLORS["text"]),
            ],
        )
        style.configure("Panel.TCheckbutton", background=COLORS["panel"], foreground=COLORS["text"], focuscolor=COLORS["panel"])
        style.map(
            "Panel.TCheckbutton",
            background=[
                ("disabled", COLORS["panel"]),
                ("pressed", COLORS["panel"]),
                ("active", COLORS["panel"]),
                ("focus", COLORS["panel"]),
                ("selected", COLORS["panel"]),
                ("!disabled", COLORS["panel"]),
            ],
            foreground=[
                ("disabled", COLORS["muted"]),
                ("pressed", COLORS["text"]),
                ("active", COLORS["text"]),
                ("focus", COLORS["text"]),
                ("selected", COLORS["text"]),
                ("!disabled", COLORS["text"]),
            ],
        )
        style.configure("TRadiobutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Panel.TRadiobutton", background=COLORS["panel"], foreground=COLORS["text"])
        style.map("TRadiobutton",
                  background=[("active", COLORS["panel"]), ("!active", COLORS["panel"])],
                  foreground=[("active", COLORS["text"]), ("!active", COLORS["text"])])
        style.configure("Horizontal.TProgressbar", background=COLORS["accent"], troughcolor=COLORS["panel_high"], bordercolor=COLORS["panel_high"], lightcolor=COLORS["accent"], darkcolor=COLORS["accent"], thickness=10)

    def _attach_tree_scrollbars(
        self,
        parent: ttk.Frame,
        tree: ttk.Treeview,
        row: int,
        column: int = 0,
        *,
        vertical: bool = False,
        horizontal: bool = True,
    ) -> None:
        tree.grid(row=row, column=column, sticky="nsew")
        if vertical:
            y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
            y_scroll.grid(row=row, column=column + 1, sticky="ns")
            tree.configure(yscrollcommand=y_scroll.set)
        if horizontal:
            x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
            x_scroll.grid(row=row + 1, column=column, sticky="ew")
            tree.configure(xscrollcommand=x_scroll.set)

    def _make_tree_sortable(self, tree: ttk.Treeview) -> None:
        for column in tree["columns"]:
            title = str(tree.heading(column).get("text") or column)
            tree.heading(column, text=title, command=lambda col=column: self._sort_tree_by_column(tree, col))

    def _sort_tree_by_column(self, tree: ttk.Treeview, column: str) -> None:
        sort_id = (str(tree), column)
        reverse = self._tree_sort_reverse.get(sort_id, False)
        rows = [(self._tree_sort_value(tree.set(item, column)), item) for item in tree.get_children("")]
        rows.sort(key=lambda item: item[0], reverse=reverse)
        for index, (_, item) in enumerate(rows):
            tree.move(item, "", index)
        self._tree_sort_reverse[sort_id] = not reverse

    def _tree_sort_value(self, value: object) -> tuple[int, object]:
        raw = str(value or "").strip()
        if not raw or raw == "-":
            return (2, "")
        numeric = raw.rstrip("%").replace(",", "")
        try:
            return (0, float(numeric))
        except ValueError:
            return (1, raw.casefold())

    def _checkbox_text(self, checked: bool) -> str:
        return "[x]" if checked else "[ ]"

    def _tree_item_from_event(self, tree: ttk.Treeview, event: tk.Event) -> tuple[str, str]:
        return tree.identify_row(event.y), tree.identify_column(event.x)

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_sidebar()

        content_holder = ttk.Frame(self, padding=(24, 16, 24, 12))
        content_holder.grid(row=0, column=1, rowspan=2, sticky="nsew")
        content_holder.columnconfigure(0, weight=1)
        content_holder.rowconfigure(0, weight=1)

        for key in ("panel", "agente_ubs", "ubs_seeds", "ubs_resultados", "ubs_robustez", "ubs_final_tick", "ubs_final_tick_6m", "ubs_regression", "ubs_historico", "ubs_universo", "ubs_comparar", "ubs_params", "portfolio", "portafolio_ubs", "portafolio_ubs_mensual", "buscador", "multiterminal", "configuracion", "archivos", "logs"):
            frame = ttk.Frame(content_holder, padding=0)
            frame.grid(row=0, column=0, sticky="nsew")
            self.section_frames[key] = frame

        self._build_dashboard(self.section_frames["panel"])
        self._build_ubs_agent(self.section_frames["agente_ubs"])
        self._build_ubs_seeds(self.section_frames["ubs_seeds"])
        self._build_ubs_results(self.section_frames["ubs_resultados"])
        self._build_ubs_robustness(self.section_frames["ubs_robustez"])
        self._build_ubs_final_tick(self.section_frames["ubs_final_tick"])
        self._build_ubs_final_tick_6m(self.section_frames["ubs_final_tick_6m"])
        self._build_ubs_regression(self.section_frames["ubs_regression"])
        self._build_ubs_history(self.section_frames["ubs_historico"])
        self._build_ubs_universe(self.section_frames["ubs_universo"])
        self._build_ubs_comparison(self.section_frames["ubs_comparar"])
        self._build_ubs_params(self.section_frames["ubs_params"])
        self._build_portfolio(self.section_frames["portfolio"])
        self._build_ubs_portfolio(self.section_frames["portafolio_ubs"])
        self._build_ubs_monthly_portfolio(self.section_frames["portafolio_ubs_mensual"])
        self._build_ubs_search(self.section_frames["buscador"])
        self._build_multiterminal(self.section_frames["multiterminal"])
        self._build_settings(self.section_frames["configuracion"])
        self._build_files(self.section_frames["archivos"])
        self._build_logs(self.section_frames["logs"])

        self._show_section("panel")

        footer = ttk.Frame(self, padding=(24, 0, 24, 10))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(1, weight=1)
        dot = tk.Label(footer, text="●", bg=COLORS["bg"], fg=COLORS["accent"], font=("Segoe UI", 12))
        dot.grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(footer, textvariable=self.engine_status_text, style="Subtitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(footer, textvariable=self.status_text, style="Subtitle.TLabel").grid(row=0, column=2, sticky="e", padx=(0, 18))
        ttk.Label(footer, textvariable=self.running_text, style="Subtitle.TLabel").grid(row=0, column=3, sticky="e")

    def _build_sidebar(self) -> None:
        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(16, 18, 16, 16), width=240)
        sidebar.grid(row=0, column=0, rowspan=3, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.rowconfigure(1, weight=1)

        header = ttk.Frame(sidebar, style="Sidebar.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 24))
        ttk.Label(header, text="MT5 Autotester", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="v1.4.0", style="Version.TLabel").grid(row=1, column=0, sticky="w")

        nav_canvas = tk.Canvas(
            sidebar,
            bg=COLORS["sidebar_bg"],
            highlightthickness=0,
            bd=0,
            yscrollincrement=24,
        )
        nav_canvas.grid(row=1, column=0, sticky="nsew")
        nav = ttk.Frame(nav_canvas, style="Sidebar.TFrame")
        nav_window = nav_canvas.create_window((0, 0), window=nav, anchor="nw")

        def _sync_nav_scroll(_event=None) -> None:
            nav_canvas.configure(scrollregion=nav_canvas.bbox("all"))
            nav_canvas.itemconfigure(nav_window, width=nav_canvas.winfo_width())

        def _scroll_nav(event) -> str | None:
            if nav_canvas.bbox("all") is None:
                return None
            nav_canvas.yview_scroll(-1 * int(event.delta / 120), "units")
            return "break"

        nav.bind("<Configure>", _sync_nav_scroll)
        nav_canvas.bind("<Configure>", _sync_nav_scroll)
        nav_canvas.bind("<MouseWheel>", _scroll_nav)
        nav.columnconfigure(0, weight=1)
        for index, (key, label) in enumerate(SIDEBAR_ITEMS):
            btn = RoundedButton(
                nav, text=label, anchor="w",
                bg=COLORS["sidebar_bg"], fg=COLORS["nav_inactive_text"],
                hover_bg=COLORS["nav_hover_bg"], hover_fg=COLORS["text"],
                font=("Segoe UI", 10, "bold"),
                radius=10, padx=14, pady=10,
                parent_bg=COLORS["sidebar_bg"],
                command=lambda k=key: self._show_section(k),
            )
            btn.grid(row=index, column=0, sticky="ew", pady=2)
            btn.bind("<MouseWheel>", _scroll_nav)
            self.nav_buttons[key] = btn

        self._build_sidebar_status(sidebar)

    @staticmethod
    def _build_sidebar_status(sidebar) -> None:
        bottom = ttk.Frame(sidebar, style="Sidebar.TFrame")
        bottom.grid(row=2, column=0, sticky="sew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(
            bottom,
            text="ESTADO DEL SISTEMA",
            background=COLORS["sidebar_bg"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(8, 4))
        ttk.Label(
            bottom,
            text="● Engine Ready",
            background=COLORS["sidebar_bg"],
            foreground=COLORS["accent"],
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w")

    def _theme_button_text(self) -> str:
        return "Modo light" if self.theme_mode.get() == "dark" else "Modo dark"

    def _toggle_theme(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Proceso activo", "Espera a que termine el proceso antes de cambiar el tema.")
            return
        section = self.current_section
        self.theme_mode.set("light" if self.theme_mode.get() == "dark" else "dark")
        self._apply_theme_palette()
        _CornerImageCache._store.clear()
        _ButtonImageCache._store.clear()
        self.configure(bg=COLORS["bg"])
        for child in self.winfo_children():
            child.destroy()
        self.nav_buttons.clear()
        self.section_frames.clear()
        self._configure_style()
        self._build_ui()
        # _build_ui vuelve a crear los StringVar del Tester vacios. Sin recargar
        # el template, Configuracion se queda en blanco y las pantallas que leen
        # tester_vars (Symbol, FromDate, ToDate) pierden sus fechas, igual que
        # hace el arranque justo despues de construir la interfaz.
        try:
            self._load_template()
        except Exception:
            self.status_text.set("Template tester no cargado")
        self._refresh_all()
        self._show_section(section)
        try:
            self._write_ui_settings()
        except Exception:
            pass
        self.status_text.set(f"Tema {self.theme_mode.get()} aplicado")

    def _show_section(self, key: str) -> None:
        self.current_section = key
        frame = self.section_frames.get(key)
        if frame is not None:
            frame.tkraise()
        for k, btn in self.nav_buttons.items():
            if isinstance(btn, RoundedButton):
                if k == key:
                    btn.set_colors(bg=COLORS["nav_active_bg"], fg=COLORS["nav_active_text"],
                                   hover_bg=COLORS["nav_active_bg"], hover_fg=COLORS["nav_active_text"])
                else:
                    btn.set_colors(bg=COLORS["sidebar_bg"], fg=COLORS["nav_inactive_text"],
                                   hover_bg=COLORS["nav_hover_bg"], hover_fg=COLORS["text"])
            else:
                if k == key:
                    btn.configure(bg=COLORS["nav_active_bg"], fg=COLORS["nav_active_text"], activebackground=COLORS["nav_active_bg"])
                else:
                    btn.configure(bg=COLORS["sidebar_bg"], fg=COLORS["nav_inactive_text"], activebackground=COLORS["nav_hover_bg"])

    def _panel(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        return self._card(parent, title)

    def _card(self, parent, title: str, chip_text: str | None = None) -> tk.Frame:
        card = RoundedCard(parent, radius=14, bg=COLORS["panel"], border=COLORS["border"])
        card.columnconfigure(0, weight=1)
        header = tk.Frame(card, bg=COLORS["panel"])
        header.grid(row=0, column=0, columnspan=99, sticky="ew", padx=20, pady=(16, 4))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text=title, bg=COLORS["panel"], fg=COLORS["text"],
                 font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        if chip_text:
            tk.Label(header, text=chip_text, bg=COLORS["panel_highest"], fg=COLORS["primary"],
                     font=("Segoe UI", 8, "bold"), padx=8, pady=3).grid(row=0, column=1, sticky="e")
        return card

    def _metric(self, parent: ttk.Frame, column: int, label: str, value: tk.StringVar, icon: str = "") -> None:
        card = RoundedCard(parent, radius=12, bg=COLORS["panel"], border=COLORS["border"])
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 6 if column < 3 else 0))
        card.columnconfigure(0, weight=1)
        inner = tk.Frame(card, bg=COLORS["panel"])
        inner.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        inner.columnconfigure(0, weight=1)
        tk.Label(inner, text=label.upper(), bg=COLORS["panel"], fg=COLORS["muted"],
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", columnspan=2)
        tk.Label(inner, textvariable=value, bg=COLORS["panel"], fg=COLORS["primary"],
                 font=("Segoe UI", 26, "bold")).grid(row=1, column=0, sticky="w", pady=(6, 0))
        if icon:
            tk.Label(inner, text=icon, bg=COLORS["panel"], fg=COLORS["accent"],
                     font=("Segoe UI Symbol", 16)).grid(row=1, column=1, sticky="e", pady=(6, 0))

    def _action_card(self, parent: ttk.Frame, row: int, column: int, *, icon: str, title: str, description: str, command) -> None:
        card = RoundedCard(parent, radius=12, bg=COLORS["panel"], border=COLORS["border"])
        card.grid(row=row, column=column, sticky="nsew",
                  padx=(20 if column == 0 else 8, 20 if column == 1 else 8), pady=(0, 10),
                  ipady=4)
        card.configure(cursor="hand2")
        icon_lbl = tk.Label(card, text=icon, bg=COLORS["panel"], fg=COLORS["accent"], font=("Segoe UI", 16, "bold"))
        icon_lbl.pack(anchor="w", padx=16, pady=(14, 6))
        title_lbl = tk.Label(card, text=title, bg=COLORS["panel"], fg=COLORS["text"],
                             font=("Segoe UI", 10, "bold"), anchor="w")
        title_lbl.pack(anchor="w", padx=16, pady=(0, 3))
        desc_lbl = tk.Label(card, text=description, bg=COLORS["panel"], fg=COLORS["muted"],
                            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=240)
        desc_lbl.pack(anchor="w", padx=16, pady=(2, 14))

        def on_click(_event=None):
            command()
        for widget in (card, icon_lbl, title_lbl, desc_lbl):
            widget.bind("<Button-1>", on_click)
        def on_enter(_e):
            for w in (icon_lbl, title_lbl, desc_lbl):
                w.configure(bg=COLORS["panel_alt"])
        def on_leave(_e):
            for w in (icon_lbl, title_lbl, desc_lbl):
                w.configure(bg=COLORS["panel"])
        for widget in (card, icon_lbl, title_lbl, desc_lbl):
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, browse_func) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(20, 10), pady=7)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=7)
        ttk.Button(parent, text="Elegir", style="Tool.TButton", command=lambda: browse_func(variable)).grid(
            row=row, column=2, sticky="e", padx=(8, 20), pady=7
        )

    def _readonly_path(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int) -> None:
        ttk.Label(parent, text=label, style="CardDesc.TLabel").grid(row=row, column=0, sticky="w", padx=20, pady=(4, 2))
        path_frame = tk.Frame(parent, bg=COLORS["panel_alt"], highlightthickness=1, highlightbackground=COLORS["border"])
        path_frame.grid(row=row + 1, column=0, sticky="ew", padx=20, pady=(0, 10))
        path_frame.columnconfigure(0, weight=1)
        tk.Label(path_frame, textvariable=variable, bg=COLORS["panel_alt"], fg=COLORS["primary"],
                 font=("Consolas", 9), anchor="w", padx=8, pady=6).grid(row=0, column=0, sticky="ew")
