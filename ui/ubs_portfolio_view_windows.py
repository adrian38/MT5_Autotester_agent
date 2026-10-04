from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSPortfolioWindowsMixin:
    """Ventanas de detalle, vista previa de completado y propuestas."""

    def _standard_ubs_portfolio_tree(self, tree: ttk.Treeview) -> None:
        self._make_tree_sortable(tree)
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])

    def _create_ubs_portfolio_detail_window(self, portfolio_id: int) -> None:
        existing = getattr(self, "ubs_portfolio_detail_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()

        master = self._portfolio_window_master()
        window = tk.Toplevel(master)
        self.ubs_portfolio_detail_window = window
        window.title(f"Portafolio #{portfolio_id}")
        window.geometry("1320x560")
        window.minsize(900, 420)
        window.configure(bg=self.colors["bg"])
        window.transient(master)
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
        self._build_ubs_portfolio_detail_buttons(bar)
        self._build_ubs_portfolio_detail_tree(window)

    def _build_ubs_portfolio_detail_buttons(self, bar) -> None:
        """Cuarentena, completar, reoptimizar y deshacer de la ventana de detalle."""
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
        self._build_ubs_portfolio_detail_rework_buttons(bar)
        self._build_ubs_portfolio_detail_open_button(bar)

    def _build_ubs_portfolio_detail_rework_buttons(self, bar) -> None:
        """Botones de reoptimizar y deshacer de la ventana de detalle."""
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
        self._build_ubs_portfolio_detail_open_button(bar)

    def _build_ubs_portfolio_detail_open_button(self, bar) -> None:
        """Boton de abrir carpeta y registro de los botones de la ventana."""
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

    def _build_ubs_portfolio_detail_tree(self, window) -> None:
        """Arbol de asignaciones de la ventana de detalle."""
        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = (
            "variant", "set", "account", "candidate", "symbol", "tf", "month", "years", "positive_years",
            "units", "lot", "net", "valley", "point",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14, selectmode="browse")
        self.ubs_portfolio_detail_tree = tree
        specs = (
            ("variant", "PERFIL", 86), ("set", "SET", 260), ("account", "CUENTA", 70), ("candidate", "CANDIDATE", 84),
            ("symbol", "SIMBOLO", 90), ("tf", "TF", 52), ("month", "MES", 58),
            ("years", "AÑOS", 90), ("positive_years", "POS.", 58), ("units", "UNID.", 58),
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
        rows: list[tuple[str, str, str, str, int, int, int, str, str, str]],
    ) -> None:
        existing = getattr(self, "ubs_portfolio_preview_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        detail_parent = getattr(self, "ubs_portfolio_detail_window", None)
        parent = detail_parent if detail_parent is not None and detail_parent.winfo_exists() else self._portfolio_window_master()
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
        self._build_ubs_portfolio_preview_buttons(bar)
        self._build_ubs_portfolio_preview_tree(window)

    def _build_ubs_portfolio_preview_buttons(self, bar) -> None:
        """Botones de aplicar y cancelar de la vista previa."""
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

    def _build_ubs_portfolio_preview_tree(self, window) -> None:
        """Arbol de la vista previa de completado."""
        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = (
            "set", "candidate", "symbol", "lot_after", "before", "after", "delta",
            "lot_before", "lot_delta", "state",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14, selectmode="browse")
        specs = (
            ("set", "SET", 300),
            ("candidate", "CANDIDATE", 92),
            ("symbol", "SIMBOLO", 90),
            ("lot_after", "LOTE", 72),
            ("before", "UNID. ANTES", 86),
            ("after", "UNID. DESPUES", 96),
            ("delta", "DELTA", 70),
            ("lot_before", "LOTE ANTES", 86),
            ("lot_delta", "DELTA LOTE", 86),
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
        parent = detail_parent if detail_parent is not None and detail_parent.winfo_exists() else self._portfolio_window_master()
        window = tk.Toplevel(parent)
        self.ubs_portfolio_proposals_window = window
        mode = getattr(self, "ubs_portfolio_proposals_mode", "")
        if mode == "generate_monthly":
            month_label = ""
            try:
                first_proposal = next(iter(getattr(self, "ubs_portfolio_proposals", {}).values()))
                first_inputs = first_proposal.get("inputs", {})
                month_label = str(first_inputs.get("target_month_label") or "")
            except Exception:
                month_label = ""
            title_target = "Nuevo portafolio mensual"
            if month_label:
                title_target += f" - {month_label}"
        elif mode == "generate":
            title_target = "Nuevo portafolio"
        else:
            title_target = f"Portafolio #{portfolio_id}"
        window.title(f"Propuestas comparables - {title_target}")
        window.geometry("1450x760")
        window.minsize(1080, 600)
        window.configure(bg=self.colors["bg"])
        window.transient(parent)
        window.grab_set()
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._cancel_ubs_portfolio_proposals_preview)
        self._build_ubs_portfolio_proposals_bar(window)
        self._build_ubs_portfolio_proposals_compare(window)
        self._build_ubs_portfolio_proposals_diff(window)

    def _build_ubs_portfolio_proposals_bar(self, window) -> None:
        """Barra de resumen y acciones de la ventana de propuestas."""
        bar = tk.Frame(window, bg=self.colors["panel_alt"])
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        bar.columnconfigure(0, weight=1)
        self.ubs_portfolio_proposals_summary = tk.StringVar(value="Selecciona una propuesta.")
        summary_label = tk.Label(
            bar,
            textvariable=self.ubs_portfolio_proposals_summary,
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        self.ubs_portfolio_proposals_summary_label = summary_label
        summary_label.grid(row=0, column=0, sticky="ew", padx=10, pady=6)
        if mode == "generate_monthly":
            action_text = "Guardar mensual"
        elif mode == "generate":
            action_text = "Usar propuesta"
        else:
            action_text = "Aplicar propuesta"
        tk.Button(
            bar,
            text=action_text,
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

    def _build_ubs_portfolio_proposals_compare(self, window) -> None:
        """Tabla comparativa de las propuestas."""
        compare_frame = ttk.Frame(window, style="Panel.TFrame")
        compare_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        compare_frame.columnconfigure(0, weight=1)
        compare_columns = (
            "profile", "net", "valley", "point", "daily", "p50", "p95",
            "prob_nominal", "prob_effective", "margin", "reserve",
            "real_margin", "real_margin_pct", "units", "strategies", "group", "changes", "stress",
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
            ("daily", "DD DIA", 120),
            ("p50", "DD P50", 90),
            ("p95", "DD P95", 90),
            ("prob_nominal", "P(> NOM.)", 90),
            ("prob_effective", "P(> EFEC.)", 95),
            ("margin", "MARGEN DD", 90),
            ("reserve", "RESERVA", 80),
            ("real_margin", "MARGEN", 105),
            ("real_margin_pct", "% MARG.", 78),
            ("units", "UNID.", 70),
            ("strategies", "ESTR.", 70),
            ("group", "MAX GRUPO", 90),
            ("changes", "CAMBIOS", 75),
            ("stress", "ESTRES", 85),
        )
        for column, heading, width in specs:
            compare_tree.heading(column, text=heading)
            compare_tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(compare_tree)
        for row in comparison_rows:
            key = str(row[0])
            tag = "rejected" if str(row[-1]) == "ALERTA" else "accepted"
            compare_tree.insert("", "end", iid=key, values=row[1:], tags=(tag,))
        compare_tree.bind("<<TreeviewSelect>>", self._on_ubs_portfolio_proposal_select)
        self._attach_tree_scrollbars(compare_frame, compare_tree, 0)

    def _build_ubs_portfolio_proposals_diff(self, window) -> None:
        """Tabla de diferencias entre la propuesta y el portafolio actual."""
        diff_frame = ttk.Frame(window, style="Panel.TFrame")
        diff_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        diff_frame.columnconfigure(0, weight=1)
        diff_frame.rowconfigure(0, weight=1)
        diff_columns = (
            "set", "candidate", "symbol", "lot_after", "before", "after", "delta",
            "lot_before", "lot_delta", "state",
        )
        diff_tree = ttk.Treeview(
            diff_frame,
            columns=diff_columns,
            show="headings",
            height=14,
            selectmode="browse",
        )
        self.ubs_portfolio_proposals_diff_tree = diff_tree
        diff_specs = (
            ("set", "SET", 300),
            ("candidate", "CANDIDATE", 92),
            ("symbol", "SIMBOLO", 90),
            ("lot_after", "LOTE", 72),
            ("before", "UNID. ANTES", 86),
            ("after", "UNID. DESPUES", 96),
            ("delta", "DELTA", 70),
            ("lot_before", "LOTE ANTES", 86),
            ("lot_delta", "DELTA LOTE", 86),
            ("state", "CAMBIO", 100),
        )
        for column, heading, width in diff_specs:
            diff_tree.heading(column, text=heading)
            diff_tree.column(column, width=width, minwidth=42, anchor="center", stretch=False)
        self._standard_ubs_portfolio_tree(diff_tree)
        self._attach_tree_scrollbars(diff_frame, diff_tree, 0)
