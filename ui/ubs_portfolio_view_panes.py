from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSPortfolioPanesMixin:
    """Barra de acciones, metricas y los paneles de tablas del portafolio."""

    def _build_ubs_portfolio_actions(self, content_pane, colors, target_month_var) -> None:
        actions = tk.Frame(content_pane, bg=colors["panel_alt"])
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        actions.columnconfigure(0, weight=1)
        tk.Label(
            actions,
            textvariable=self.ubs_portfolio_status,
            bg=colors["panel_alt"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)

        generate_btn = tk.Button(
            actions,
            text="Generar mensual" if target_month_var is not None else "Generar portafolio",
            bg=colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._run_ubs_portfolio_build,
        )
        generate_btn.grid(row=0, column=1, sticky="e", padx=(0, 6), pady=6)
        self.ubs_portfolio_save_button = tk.Button(
            actions,
            text="Guardar mensual" if target_month_var is not None else "Guardar portafolio",
            bg=colors["panel"],
            fg=colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._save_pending_ubs_portfolio,
            state="disabled",
        )
        self.ubs_portfolio_save_button.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=6)
        self.ubs_portfolio_buttons = [generate_btn]
        self._build_ubs_portfolio_reset_buttons(actions, colors)
        self.ubs_portfolio_progress = ttk.Progressbar(content_pane, mode="indeterminate")
        self.ubs_portfolio_progress.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._build_ubs_portfolio_metrics(content_pane, colors)

    def _build_ubs_portfolio_reset_buttons(self, actions, colors) -> None:
        """Botones de reinicio y refresco de la barra de acciones."""
        reset_btn = tk.Button(
            actions,
            text="Limpiar formulario",
            bg=colors["panel"],
            fg=colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._reset_ubs_portfolio_form,
        )
        reset_btn.grid(row=0, column=3, sticky="e", padx=(0, 6), pady=6)
        refresh_btn = tk.Button(
            actions,
            text="Actualizar",
            bg=colors["panel"],
            fg=colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._refresh_ubs_portfolios,
        )
        refresh_btn.grid(row=0, column=4, sticky="e", padx=(0, 10), pady=6)
        self.ubs_portfolio_buttons.extend([reset_btn, refresh_btn])

    def _build_ubs_portfolio_metrics(self, content_pane, colors) -> None:
        """Fila de metricas resumidas del portafolio."""
        metrics = tk.Frame(content_pane, bg=colors["panel_alt"])
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        for col in range(6):
            metrics.columnconfigure(col, weight=1)

        def metric(col: int, title: str, variable: tk.StringVar) -> None:
            box = tk.Frame(metrics, bg=colors["panel_alt"])
            box.grid(row=0, column=col, sticky="ew", padx=(10 if col == 0 else 4, 10 if col == 5 else 4), pady=8)
            tk.Label(box, text=title.upper(), bg=colors["panel_alt"], fg=colors["muted"],
                     font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(box, textvariable=variable, bg=colors["panel_alt"], fg=colors["text"],
                     font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(2, 0))

        metric(0, "Net profit", self.ubs_portfolio_metric_net)
        metric(1, "DD valle", self.ubs_portfolio_metric_valley)
        metric(2, "DD puntual", self.ubs_portfolio_metric_point)
        metric(3, "Lote total", self.ubs_portfolio_metric_lot)
        metric(4, "Unidades", self.ubs_portfolio_metric_units)
        metric(5, "Estrategias", self.ubs_portfolio_metric_count)

    def _build_ubs_portfolio_panes(self, content_pane, colors, target_month_var) -> None:
        body = ttk.PanedWindow(content_pane, orient="horizontal")
        body.grid(row=4, column=0, sticky="nsew")

        left = ttk.Frame(body, style="Panel.TFrame")
        right = ttk.Frame(body, style="Panel.TFrame")
        body.add(left, weight=1)
        body.add(right, weight=3)
        left.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        left_split = ttk.PanedWindow(left, orient="vertical")
        left_split.grid(row=0, column=0, sticky="nsew")
        left_top = ttk.Frame(left_split, style="Panel.TFrame")
        left_bottom = ttk.Frame(left_split, style="Panel.TFrame")
        left_quarantine = ttk.Frame(left_split, style="Panel.TFrame")
        left_split.add(left_top, weight=1)
        left_split.add(left_bottom, weight=3)
        left_split.add(left_quarantine, weight=2)
        left_top.columnconfigure(0, weight=1)
        left_top.rowconfigure(1, weight=1)
        left_bottom.columnconfigure(0, weight=1)
        left_bottom.rowconfigure(1, weight=1)
        left_quarantine.columnconfigure(0, weight=1)
        left_quarantine.rowconfigure(1, weight=1)
        self._build_ubs_portfolio_availability_pane(left_top, colors)
        self._build_ubs_portfolio_saved_pane(left_bottom, colors, target_month_var)
        self._build_ubs_portfolio_quarantine_pane(left_quarantine, colors, target_month_var)
        self._build_ubs_portfolio_members_pane(right, colors, target_month_var)

    def _build_ubs_portfolio_availability_pane(self, left_top, colors) -> None:
        """Arbol de disponibilidad y su barra."""
        availability_bar = tk.Frame(left_top, bg=colors["panel_alt"])
        availability_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        availability_bar.columnconfigure(0, weight=1)
        tk.Label(
            availability_bar,
            textvariable=self.ubs_portfolio_availability,
            bg=colors["panel_alt"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)

        availability_frame = ttk.Frame(left_top, style="Panel.TFrame")
        availability_frame.grid(row=1, column=0, sticky="nsew")
        availability_frame.columnconfigure(0, weight=1)
        availability_frame.rowconfigure(0, weight=1)
        availability_columns = ("symbol", "count")
        self.ubs_portfolio_availability_tree = ttk.Treeview(
            availability_frame, columns=availability_columns, show="headings", height=4
        )
        for column, heading, width in (("symbol", "SIMBOLO", 110), ("count", "SETS DISP.", 90)):
            self.ubs_portfolio_availability_tree.heading(column, text=heading)
            self.ubs_portfolio_availability_tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_availability_tree)
        self._attach_tree_scrollbars(availability_frame, self.ubs_portfolio_availability_tree, 0)

    def _build_ubs_portfolio_saved_pane(self, left_bottom, colors, target_month_var) -> None:
        """Barra y acciones de los portafolios guardados."""
        saved_bar = tk.Frame(left_bottom, bg=colors["panel_alt"])
        saved_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        saved_bar.columnconfigure(0, weight=1)
        tk.Label(saved_bar, text=("Portafolios mensuales guardados" if target_month_var is not None else "Portafolios guardados"), bg=colors["panel_alt"], fg=colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        export_btn = tk.Button(
            saved_bar,
            text="Exportar sets",
            bg=colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._export_ubs_portfolio_sets,
        )
        export_btn.grid(row=0, column=1, sticky="e", padx=(0, 6), pady=5)
        delete_btn = tk.Button(
            saved_bar,
            text="Borrar",
            bg=colors["panel"],
            fg=colors["danger"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._delete_selected_ubs_portfolio,
        )
        delete_btn.grid(row=0, column=2, sticky="e", padx=(0, 10), pady=5)
        self.ubs_portfolio_buttons.extend([export_btn, delete_btn])
        self._build_ubs_portfolio_saved_tree(left_bottom)

    def _build_ubs_portfolio_saved_tree(self, left_bottom) -> None:
        """Arbol de portafolios guardados."""
        saved_frame = ttk.Frame(left_bottom, style="Panel.TFrame")
        saved_frame.grid(row=1, column=0, sticky="nsew")
        saved_frame.columnconfigure(0, weight=1)
        saved_frame.rowconfigure(0, weight=1)
        saved_columns = ("id", "created", "type", "capital", "net", "valley", "valley_pct", "point", "point_pct", "units", "active")
        self.ubs_portfolio_saved_tree = ttk.Treeview(
            saved_frame, columns=saved_columns, show="headings", height=10, selectmode="browse"
        )
        saved_headings = {
            "id": "ID", "created": "CREADO", "type": "TIPO", "capital": "CAPITAL",
            "net": "NET", "valley": "DD VALLE", "valley_pct": "% VALLE",
            "point": "DD PUNT.", "point_pct": "% PUNT.", "units": "UNID.", "active": "ESTR.",
        }
        saved_widths = {
            "id": 46, "created": 132, "type": 90, "capital": 84, "net": 88,
            "valley": 82, "valley_pct": 72, "point": 82, "point_pct": 72,
            "units": 58, "active": 58,
        }
        for column in saved_columns:
            self.ubs_portfolio_saved_tree.heading(column, text=saved_headings[column])
            self.ubs_portfolio_saved_tree.column(column, width=saved_widths[column], minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_saved_tree)
        self.ubs_portfolio_saved_tree.bind("<<TreeviewSelect>>", self._on_ubs_portfolio_select)
        self.ubs_portfolio_saved_tree.bind("<Double-1>", self._open_selected_ubs_portfolio_detail)
        self._attach_tree_scrollbars(saved_frame, self.ubs_portfolio_saved_tree, 0)

    def _build_ubs_portfolio_quarantine_pane(self, left_quarantine, colors, target_month_var) -> None:
        """Barra y arbol de la cuarentena."""
        quarantine_bar = tk.Frame(left_quarantine, bg=colors["panel_alt"])
        quarantine_bar.grid(row=0, column=0, sticky="ew", pady=(4, 4))
        quarantine_bar.columnconfigure(0, weight=1)
        tk.Label(
            quarantine_bar,
            text=("Cuarentena (informativa; no excluye)" if target_month_var is not None else "Sets en cuarentena"),
            bg=colors["panel_alt"],
            fg=colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        release_btn = tk.Button(
            quarantine_bar,
            text="Reintegrar",
            bg=colors["panel"],
            fg=colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._release_selected_ubs_portfolio_quarantine,
        )
        release_btn.grid(row=0, column=1, sticky="e", padx=(0, 10), pady=5)
        self.ubs_portfolio_buttons.append(release_btn)

        quarantine_frame = ttk.Frame(left_quarantine, style="Panel.TFrame")
        quarantine_frame.grid(row=1, column=0, sticky="nsew")
        quarantine_frame.columnconfigure(0, weight=1)
        quarantine_frame.rowconfigure(0, weight=1)
        quarantine_columns = ("set", "account", "symbol", "tf", "date")
        self.ubs_portfolio_quarantine_tree = ttk.Treeview(
            quarantine_frame,
            columns=quarantine_columns,
            show="headings",
            height=6,
            selectmode="browse",
        )
        quarantine_specs = (
            ("set", "SET", 210),
            ("account", "CUENTA", 68),
            ("symbol", "SIMBOLO", 90),
            ("tf", "TF", 52),
            ("date", "DESDE", 132),
        )
        for column, heading, width in quarantine_specs:
            self.ubs_portfolio_quarantine_tree.heading(column, text=heading)
            self.ubs_portfolio_quarantine_tree.column(
                column, width=width, minwidth=42, anchor="center", stretch=False
            )
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_quarantine_tree)
        self._attach_tree_scrollbars(quarantine_frame, self.ubs_portfolio_quarantine_tree, 0)

    def _build_ubs_portfolio_members_pane(self, right, colors, target_month_var) -> None:
        """Cabecera y arbol de miembros del portafolio seleccionado."""
        tk.Label(right, text=("Asignaciones del portafolio mensual" if target_month_var is not None else "Asignaciones del portafolio"), bg=colors["panel"], fg=colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        members_frame = ttk.Frame(right, style="Panel.TFrame")
        members_frame.grid(row=1, column=0, sticky="nsew")
        members_frame.columnconfigure(0, weight=1)
        members_frame.rowconfigure(0, weight=1)
        member_columns = (
            "variant", "set", "account", "candidate", "symbol", "tf", "units", "lot", "net",
            "valley", "point", "step", "margin", "margin_pct", "lev",
        )
        self.ubs_portfolio_members_tree = ttk.Treeview(
            members_frame, columns=member_columns, show="headings", height=8, selectmode="browse"
        )
        member_headings = {
            "variant": "PERFIL", "set": "SET ID", "account": "CUENTA", "candidate": "CANDIDATE", "symbol": "SIMBOLO", "tf": "TF",
            "units": "UNID.", "lot": "LOTE", "net": "NET", "valley": "DD VALLE",
            "point": "DD PUNT.", "step": "$/0.01", "margin": "MARGEN", "margin_pct": "% BAL.", "lev": "LEV.",
        }
        member_widths = {
            "variant": 86, "set": 230, "account": 70, "candidate": 84, "symbol": 90, "tf": 52, "units": 58,
            "lot": 62, "net": 90, "valley": 82, "point": 82, "step": 88,
            "margin": 88, "margin_pct": 70, "lev": 58,
        }
        for column in member_columns:
            self.ubs_portfolio_members_tree.heading(column, text=member_headings[column])
            self.ubs_portfolio_members_tree.column(column, width=member_widths[column], minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_members_tree)
        self.ubs_portfolio_members_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_portfolio_member())
        self._attach_tree_scrollbars(members_frame, self.ubs_portfolio_members_tree, 0)
