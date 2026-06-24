from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSPortfolioViewMixin:
    def _build_ubs_portfolio(self, parent: ttk.Frame) -> None:
        colors = self.colors
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Portfolio Builder")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(7, weight=1)

        form = tk.Frame(panel, bg=colors["panel_alt"])
        form.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 6))
        for col in range(12):
            form.columnconfigure(col, weight=0)
        form.columnconfigure(11, weight=1)

        def label(row: int, col: int, text: str) -> None:
            tk.Label(
                form,
                text=text,
                bg=colors["panel_alt"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
            ).grid(row=row, column=col, sticky="w", padx=(10 if col == 0 else 8, 4), pady=5)

        label(0, 0, "Capital")
        ttk.Entry(form, textvariable=self.ubs_portfolio_capital, width=10).grid(row=0, column=1, sticky="w", pady=5)
        label(0, 2, "DD valle %")
        ttk.Entry(form, textvariable=self.ubs_portfolio_valley_pct, width=8).grid(row=0, column=3, sticky="w", pady=5)
        label(0, 4, "DD puntual %")
        ttk.Entry(form, textvariable=self.ubs_portfolio_point_pct, width=8).grid(row=0, column=5, sticky="w", pady=5)
        label(0, 6, "Tipo")
        self.ubs_portfolio_type_combo = ttk.Combobox(
            form,
            textvariable=self.ubs_portfolio_type,
            state="readonly",
            width=12,
            values=("Conservative", "Balanced", "Aggressive"),
        )
        self.ubs_portfolio_type_combo.grid(row=0, column=7, sticky="w", pady=5)
        label(0, 8, "Top K")
        ttk.Spinbox(form, from_=1, to=50, width=8, textvariable=self.ubs_portfolio_top_k).grid(
            row=0, column=9, sticky="w", pady=5
        )
        label(0, 10, "Max cand.")
        ttk.Spinbox(form, from_=1, to=500, width=8, textvariable=self.ubs_portfolio_max_candidates).grid(
            row=0, column=11, sticky="w", pady=5
        )

        label(1, 0, "Min trades")
        ttk.Spinbox(form, from_=0, to=10000, width=8, textvariable=self.ubs_portfolio_min_trades).grid(
            row=1, column=1, sticky="w", pady=5
        )
        label(1, 2, "Max unidades/set")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_units_per_set, width=8).grid(
            row=1, column=3, sticky="w", pady=5
        )
        label(1, 4, "Max unidades")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_total_units, width=8).grid(
            row=1, column=5, sticky="w", pady=5
        )
        label(1, 6, "Max unidades/simbolo")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_units_per_symbol, width=8).grid(
            row=1, column=7, sticky="w", pady=5
        )
        label(1, 8, "Max sets/simbolo")
        ttk.Spinbox(form, from_=1, to=50, width=8, textvariable=self.ubs_portfolio_max_sets_per_symbol).grid(
            row=1, column=9, sticky="w", pady=5
        )
        ttk.Checkbutton(
            form,
            text="Mejora local",
            variable=self.ubs_portfolio_run_local_search,
        ).grid(row=1, column=10, columnspan=2, sticky="w", padx=(8, 10), pady=5)

        ttk.Checkbutton(
            form,
            text="Filtro correlacion",
            variable=self.ubs_portfolio_use_correlation,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=(10, 4), pady=5)
        label(2, 2, "Max corr")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_pair_corr, width=8).grid(
            row=2, column=3, sticky="w", pady=5
        )
        label(2, 4, "Max downside")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_downside_corr, width=8).grid(
            row=2, column=5, sticky="w", pady=5
        )
        label(2, 6, "Max overlap DD")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_dd_overlap, width=8).grid(
            row=2, column=7, sticky="w", pady=5
        )
        label(2, 8, "Max corr portfolios")
        ttk.Entry(form, textvariable=self.ubs_portfolio_max_portfolio_corr, width=8).grid(
            row=2, column=9, sticky="w", pady=5
        )
        recent_months_check = ttk.Checkbutton(
            form,
            text="3/6 meses +",
            variable=self.ubs_portfolio_require_3_positive_months_6m,
        )
        recent_months_check.grid(row=2, column=10, columnspan=2, sticky="w", padx=(8, 10), pady=5)
        self._tooltip_cls(
            recent_months_check,
            "Si esta activo, el portafolio solo usa Final Tick 6M accepted con al menos 3 meses positivos en los ultimos 6.",
        )

        label(3, 0, "Reserva DD %")
        reserve_entry = ttk.Entry(form, textvariable=self.ubs_portfolio_dd_reserve_pct, width=8)
        reserve_entry.grid(row=3, column=1, sticky="w", pady=5)
        self._tooltip_cls(
            reserve_entry,
            "Margen de seguridad sin utilizar. Con 10%, un limite DD de 350 optimiza hasta 315.",
        )
        label(3, 2, "Reinicios busqueda")
        restart_spin = ttk.Spinbox(
            form,
            from_=0,
            to=20,
            width=8,
            textvariable=self.ubs_portfolio_search_restarts,
        )
        restart_spin.grid(row=3, column=3, sticky="w", pady=5)
        self._tooltip_cls(
            restart_spin,
            "Perturbaciones validas para escapar del optimo local. 0 desactiva; 4 es el valor recomendado.",
        )

        actions = tk.Frame(panel, bg=colors["panel_alt"])
        actions.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
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
            text="Generar portafolio",
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
            text="Guardar portafolio",
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
        self.ubs_portfolio_buttons = [generate_btn, reset_btn, refresh_btn]

        self.ubs_portfolio_progress = ttk.Progressbar(panel, mode="indeterminate")
        self.ubs_portfolio_progress.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))

        metrics = tk.Frame(panel, bg=colors["panel_alt"])
        metrics.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 6))
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

        body = ttk.PanedWindow(panel, orient="horizontal")
        body.grid(row=7, column=0, sticky="nsew", padx=20, pady=(0, 18))

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

        saved_bar = tk.Frame(left_bottom, bg=colors["panel_alt"])
        saved_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        saved_bar.columnconfigure(0, weight=1)
        tk.Label(saved_bar, text="Portafolios guardados", bg=colors["panel_alt"], fg=colors["text"],
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

        quarantine_bar = tk.Frame(left_quarantine, bg=colors["panel_alt"])
        quarantine_bar.grid(row=0, column=0, sticky="ew", pady=(4, 4))
        quarantine_bar.columnconfigure(0, weight=1)
        tk.Label(
            quarantine_bar,
            text="Sets en cuarentena",
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

        tk.Label(right, text="Asignaciones del portafolio", bg=colors["panel"], fg=colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        members_frame = ttk.Frame(right, style="Panel.TFrame")
        members_frame.grid(row=1, column=0, sticky="nsew")
        members_frame.columnconfigure(0, weight=1)
        members_frame.rowconfigure(0, weight=1)
        member_columns = ("set", "account", "candidate", "symbol", "tf", "units", "lot", "net", "valley", "point", "step")
        self.ubs_portfolio_members_tree = ttk.Treeview(
            members_frame, columns=member_columns, show="headings", height=8, selectmode="browse"
        )
        member_headings = {
            "set": "SET ID", "account": "CUENTA", "candidate": "CANDIDATE", "symbol": "SIMBOLO", "tf": "TF",
            "units": "UNID.", "lot": "LOTE", "net": "NET", "valley": "DD VALLE",
            "point": "DD PUNT.", "step": "$/0.01",
        }
        member_widths = {
            "set": 230, "account": 70, "candidate": 84, "symbol": 90, "tf": 52, "units": 58,
            "lot": 62, "net": 90, "valley": 82, "point": 82, "step": 88,
        }
        for column in member_columns:
            self.ubs_portfolio_members_tree.heading(column, text=member_headings[column])
            self.ubs_portfolio_members_tree.column(column, width=member_widths[column], minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_members_tree)
        self.ubs_portfolio_members_tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_portfolio_member())
        self._attach_tree_scrollbars(members_frame, self.ubs_portfolio_members_tree, 0)

    def _standard_ubs_portfolio_tree(self, tree: ttk.Treeview) -> None:
        self._make_tree_sortable(tree)
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])

    def _create_ubs_portfolio_detail_window(self, portfolio_id: int) -> None:
        existing = getattr(self, "ubs_portfolio_detail_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()

        window = tk.Toplevel(self)
        self.ubs_portfolio_detail_window = window
        window.title(f"Portafolio #{portfolio_id}")
        window.geometry("1320x560")
        window.minsize(900, 420)
        window.configure(bg=self.colors["bg"])
        window.transient(self)
        window.grab_set()
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        bar = tk.Frame(window, bg=self.colors["panel_alt"])
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        bar.columnconfigure(0, weight=1)
        self.ubs_portfolio_detail_status = tk.StringVar(value=f"Portafolio #{portfolio_id}")
        tk.Label(
            bar,
            textvariable=self.ubs_portfolio_detail_status,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        quarantine_btn = tk.Button(
            bar,
            text="Poner en cuarentena",
            bg=self.colors["danger"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=lambda: self._quarantine_selected_ubs_portfolio_member(portfolio_id),
        )
        quarantine_btn.grid(row=0, column=1, padx=(0, 6), pady=6)
        complete_btn = tk.Button(
            bar,
            text="Completar portafolio",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=lambda: self._complete_saved_ubs_portfolio(portfolio_id),
        )
        complete_btn.grid(row=0, column=2, padx=(0, 6), pady=6)
        reoptimize_btn = tk.Button(
            bar,
            text="Revalidar / optimizar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=lambda: self._reoptimize_saved_ubs_portfolio(portfolio_id),
        )
        reoptimize_btn.grid(row=0, column=3, padx=(0, 6), pady=6)
        undo_btn = tk.Button(
            bar,
            text="Deshacer recomposicion",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=lambda: self._undo_latest_ubs_portfolio_completion(portfolio_id),
        )
        undo_btn.grid(row=0, column=4, padx=(0, 6), pady=6)
        open_btn = tk.Button(
            bar,
            text="Abrir reporte",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._open_selected_ubs_portfolio_detail_member,
        )
        open_btn.grid(row=0, column=5, padx=(0, 10), pady=6)
        self.ubs_portfolio_detail_buttons = [
            quarantine_btn,
            complete_btn,
            reoptimize_btn,
            undo_btn,
            open_btn,
        ]

        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("set", "account", "candidate", "symbol", "tf", "units", "lot", "net", "valley", "point")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14, selectmode="browse")
        self.ubs_portfolio_detail_tree = tree
        specs = (
            ("set", "SET", 260), ("account", "CUENTA", 70), ("candidate", "CANDIDATE", 84),
            ("symbol", "SIMBOLO", 90), ("tf", "TF", 52), ("units", "UNID.", 58),
            ("lot", "LOTE", 62), ("net", "NET", 90), ("valley", "DD VALLE", 82),
            ("point", "DD PUNT.", 82),
        )
        for column, heading, width in specs:
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(tree)
        tree.bind("<Double-1>", lambda _event: self._open_selected_ubs_portfolio_detail_member())
        self._attach_tree_scrollbars(frame, tree, 0)

    def _create_ubs_portfolio_completion_preview(
        self,
        portfolio_id: int,
        summary: str,
        rows: list[tuple[str, str, str, int, int, int, str]],
    ) -> None:
        existing = getattr(self, "ubs_portfolio_preview_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        detail_parent = getattr(self, "ubs_portfolio_detail_window", None)
        parent = detail_parent if detail_parent is not None and detail_parent.winfo_exists() else self
        window = tk.Toplevel(parent)
        self.ubs_portfolio_preview_window = window
        window.title(f"Vista previa - Portafolio #{portfolio_id}")
        window.geometry("1040x560")
        window.minsize(820, 420)
        window.configure(bg=self.colors["bg"])
        window.transient(parent)
        window.grab_set()
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._cancel_ubs_portfolio_completion_preview)

        bar = tk.Frame(window, bg=self.colors["panel_alt"])
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        bar.columnconfigure(0, weight=1)
        tk.Label(
            bar,
            text=summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=700,
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=6)
        apply_btn = tk.Button(
            bar,
            text="Aplicar cambios",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._apply_ubs_portfolio_completion_preview,
        )
        apply_btn.grid(row=0, column=1, padx=(0, 6), pady=6)
        cancel_btn = tk.Button(
            bar,
            text="Cancelar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._cancel_ubs_portfolio_completion_preview,
        )
        cancel_btn.grid(row=0, column=2, padx=(0, 10), pady=6)

        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("set", "candidate", "symbol", "before", "after", "delta", "state")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14, selectmode="browse")
        specs = (
            ("set", "SET", 330),
            ("candidate", "CANDIDATE", 100),
            ("symbol", "SIMBOLO", 90),
            ("before", "UNID. ANTES", 90),
            ("after", "UNID. DESPUES", 100),
            ("delta", "DELTA", 70),
            ("state", "CAMBIO", 100),
        )
        for column, heading, width in specs:
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(tree)
        for values in rows:
            state = values[-1]
            tag = "accepted" if state == "NUEVA" else "rejected" if state == "RETIRADA" else "pending"
            tree.insert("", "end", values=values, tags=(tag,))
        self._attach_tree_scrollbars(frame, tree, 0)

    def _create_ubs_portfolio_proposals_window(
        self,
        portfolio_id: int,
        comparison_rows: list[tuple],
    ) -> None:
        existing = getattr(self, "ubs_portfolio_proposals_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        detail_parent = getattr(self, "ubs_portfolio_detail_window", None)
        parent = detail_parent if detail_parent is not None and detail_parent.winfo_exists() else self
        window = tk.Toplevel(parent)
        self.ubs_portfolio_proposals_window = window
        mode = getattr(self, "ubs_portfolio_proposals_mode", "")
        title_target = "Nuevo portafolio" if mode == "generate" else f"Portafolio #{portfolio_id}"
        window.title(f"Propuestas comparables - {title_target}")
        window.geometry("1250x720")
        window.minsize(980, 560)
        window.configure(bg=self.colors["bg"])
        window.transient(parent)
        window.grab_set()
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._cancel_ubs_portfolio_proposals_preview)

        bar = tk.Frame(window, bg=self.colors["panel_alt"])
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        bar.columnconfigure(0, weight=1)
        self.ubs_portfolio_proposals_summary = tk.StringVar(value="Selecciona una propuesta.")
        tk.Label(
            bar,
            textvariable=self.ubs_portfolio_proposals_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=6)
        tk.Button(
            bar,
            text="Usar propuesta" if mode == "generate" else "Aplicar propuesta",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self._apply_selected_ubs_portfolio_proposal,
        ).grid(row=0, column=1, padx=(0, 6), pady=6)
        tk.Button(
            bar,
            text="Cancelar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._cancel_ubs_portfolio_proposals_preview,
        ).grid(row=0, column=2, padx=(0, 10), pady=6)

        compare_frame = ttk.Frame(window, style="Panel.TFrame")
        compare_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        compare_frame.columnconfigure(0, weight=1)
        compare_columns = (
            "profile", "net", "valley", "point", "margin", "reserve",
            "units", "strategies", "group", "changes",
        )
        compare_tree = ttk.Treeview(
            compare_frame,
            columns=compare_columns,
            show="headings",
            height=4,
            selectmode="browse",
        )
        self.ubs_portfolio_proposals_tree = compare_tree
        specs = (
            ("profile", "PROPUESTA", 150),
            ("net", "NET", 100),
            ("valley", "DD VALLE", 130),
            ("point", "DD PUNT.", 130),
            ("margin", "MARGEN DD", 90),
            ("reserve", "RESERVA", 80),
            ("units", "UNID.", 70),
            ("strategies", "ESTR.", 70),
            ("group", "MAX GRUPO", 90),
            ("changes", "CAMBIOS", 75),
        )
        for column, heading, width in specs:
            compare_tree.heading(column, text=heading)
            compare_tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(compare_tree)
        for row in comparison_rows:
            key = str(row[0])
            compare_tree.insert("", "end", iid=key, values=row[1:], tags=("accepted",))
        compare_tree.bind("<<TreeviewSelect>>", self._on_ubs_portfolio_proposal_select)
        self._attach_tree_scrollbars(compare_frame, compare_tree, 0)

        diff_frame = ttk.Frame(window, style="Panel.TFrame")
        diff_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        diff_frame.columnconfigure(0, weight=1)
        diff_frame.rowconfigure(0, weight=1)
        diff_columns = ("set", "candidate", "symbol", "before", "after", "delta", "state")
        diff_tree = ttk.Treeview(
            diff_frame,
            columns=diff_columns,
            show="headings",
            height=14,
            selectmode="browse",
        )
        self.ubs_portfolio_proposals_diff_tree = diff_tree
        diff_specs = (
            ("set", "SET", 360),
            ("candidate", "CANDIDATE", 100),
            ("symbol", "SIMBOLO", 90),
            ("before", "UNID. ANTES", 90),
            ("after", "UNID. DESPUES", 100),
            ("delta", "DELTA", 70),
            ("state", "CAMBIO", 100),
        )
        for column, heading, width in diff_specs:
            diff_tree.heading(column, text=heading)
            diff_tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(diff_tree)
        self._attach_tree_scrollbars(diff_frame, diff_tree, 0)
