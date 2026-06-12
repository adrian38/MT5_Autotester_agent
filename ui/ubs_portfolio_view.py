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
        left.rowconfigure(3, weight=1)
        right.rowconfigure(1, weight=1)

        availability_bar = tk.Frame(left, bg=colors["panel_alt"])
        availability_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        availability_bar.columnconfigure(0, weight=1)
        tk.Label(
            availability_bar,
            textvariable=self.ubs_portfolio_availability,
            bg=colors["panel_alt"],
            fg=colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)

        availability_frame = ttk.Frame(left, style="Panel.TFrame")
        availability_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        availability_frame.columnconfigure(0, weight=1)
        availability_columns = ("symbol", "count")
        self.ubs_portfolio_availability_tree = ttk.Treeview(
            availability_frame, columns=availability_columns, show="headings", height=4
        )
        for column, heading, width in (("symbol", "SIMBOLO", 110), ("count", "SETS DISP.", 90)):
            self.ubs_portfolio_availability_tree.heading(column, text=heading)
            self.ubs_portfolio_availability_tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_availability_tree)
        self._attach_tree_scrollbars(availability_frame, self.ubs_portfolio_availability_tree, 0)

        saved_bar = tk.Frame(left, bg=colors["panel_alt"])
        saved_bar.grid(row=2, column=0, sticky="ew", pady=(0, 4))
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

        saved_frame = ttk.Frame(left, style="Panel.TFrame")
        saved_frame.grid(row=3, column=0, sticky="nsew")
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
        self._attach_tree_scrollbars(saved_frame, self.ubs_portfolio_saved_tree, 0)

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

        curve_frame = tk.Frame(right, bg=colors["panel"])
        curve_frame.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        curve_frame.columnconfigure(0, weight=1)
        tk.Label(curve_frame, text="Equity Curve 2020-2026", bg=colors["panel"], fg=colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.ubs_portfolio_curve_canvas = tk.Canvas(
            curve_frame,
            height=120,
            bg=colors["tree_bg"],
            highlightthickness=1,
            highlightbackground=colors["border"],
        )
        self.ubs_portfolio_curve_canvas.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        lower = ttk.PanedWindow(right, orient="horizontal")
        lower.grid(row=3, column=0, sticky="nsew")
        right.rowconfigure(3, weight=1)
        decision_wrap = ttk.Frame(lower, style="Panel.TFrame")
        unused_wrap = ttk.Frame(lower, style="Panel.TFrame")
        lower.add(decision_wrap, weight=3)
        lower.add(unused_wrap, weight=1)
        decision_wrap.columnconfigure(0, weight=1)
        decision_wrap.rowconfigure(1, weight=1)
        unused_wrap.columnconfigure(0, weight=1)
        unused_wrap.rowconfigure(1, weight=1)

        tk.Label(decision_wrap, text="Decision Log", bg=colors["panel"], fg=colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        decision_columns = ("step", "action", "set", "from", "to", "gain", "valley", "point", "score", "net_after", "valley_after", "point_after", "reason")
        self.ubs_portfolio_decision_tree = ttk.Treeview(
            decision_wrap, columns=decision_columns, show="headings", height=6
        )
        decision_headings = {
            "step": "STEP", "action": "ACTION", "set": "SET", "from": "FROM", "to": "TO",
            "gain": "GAIN", "valley": "VALLEY COST", "point": "POINT COST", "score": "SCORE",
            "net_after": "NET AFTER", "valley_after": "VALLEY AFTER",
            "point_after": "POINT AFTER", "reason": "REASON",
        }
        decision_widths = {
            "step": 52, "action": 82, "set": 160, "from": 120, "to": 120,
            "gain": 78, "valley": 90, "point": 90, "score": 82,
            "net_after": 92, "valley_after": 100, "point_after": 100, "reason": 260,
        }
        for column in decision_columns:
            self.ubs_portfolio_decision_tree.heading(column, text=decision_headings[column])
            self.ubs_portfolio_decision_tree.column(column, width=decision_widths[column], minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_decision_tree)
        self._attach_tree_scrollbars(decision_wrap, self.ubs_portfolio_decision_tree, 1)

        tk.Label(unused_wrap, text="Sets no usados", bg=colors["panel"], fg=colors["text"],
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        unused_columns = ("set", "symbol", "score", "reason")
        self.ubs_portfolio_unused_tree = ttk.Treeview(
            unused_wrap, columns=unused_columns, show="headings", height=6
        )
        unused_headings = {"set": "SET", "symbol": "SIMBOLO", "score": "SCORE", "reason": "MOTIVO"}
        unused_widths = {"set": 220, "symbol": 90, "score": 78, "reason": 150}
        for column in unused_columns:
            self.ubs_portfolio_unused_tree.heading(column, text=unused_headings[column])
            self.ubs_portfolio_unused_tree.column(column, width=unused_widths[column], minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(self.ubs_portfolio_unused_tree)
        self._attach_tree_scrollbars(unused_wrap, self.ubs_portfolio_unused_tree, 1)

    def _standard_ubs_portfolio_tree(self, tree: ttk.Treeview) -> None:
        self._make_tree_sortable(tree)
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])
