from __future__ import annotations

import tkinter as tk
from tkinter import ttk


PORTFOLIO_GROUP_CHECKS = (
    ("Forex", "allow_forex"),
    ("Metales", "allow_metals"),
    ("Indices", "allow_indices"),
    ("Energias", "allow_energies"),
    ("Cripto", "allow_crypto"),
    ("Acciones", "allow_stocks"),
    ("Bonos", "allow_bonds"),
    ("Softs", "allow_softs"),
)


class UBSPortfolioMonthlyInputsMixin:
    """Tarjetas de riesgo, filtros, margen y grupos del portafolio mensual."""

    def _build_ubs_monthly_portfolio_input_groups(self, form: tk.Frame) -> None:
        colors = self.colors
        for col in range(12):
            form.columnconfigure(col, weight=0)
        for col in range(3):
            form.columnconfigure(col, weight=1, uniform="monthly_inputs")

        def section(row: int, col: int, title: str, *, colspan: int = 1) -> tk.Frame:
            box = tk.Frame(
                form,
                bg=colors["panel"],
                highlightthickness=1,
                highlightbackground=colors["border"],
                highlightcolor=colors["border"],
            )
            box.grid(row=row, column=col, columnspan=colspan, sticky="nsew", padx=6, pady=6)
            box.columnconfigure(1, weight=1)
            tk.Label(
                box,
                text=title,
                bg=colors["panel"],
                fg="#ffffff",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4))
            return box

        def label(parent: tk.Frame, row: int, col: int, text: str) -> None:
            tk.Label(
                parent,
                text=text,
                bg=colors["panel"],
                fg=colors["muted"],
                font=("Segoe UI", 9),
            ).grid(row=row, column=col, sticky="w", padx=(10 if col == 0 else 8, 4), pady=4)

        def entry(parent: tk.Frame, row: int, col: int, variable: tk.Variable, *, width: int = 8) -> ttk.Entry:
            widget = ttk.Entry(parent, textvariable=variable, width=width)
            widget.grid(row=row, column=col, sticky="w", pady=4)
            return widget

        self._monthly_risk_card(section, label, entry, colors)
        self._monthly_limits_card(section, label, entry, colors)
        self._monthly_correlation_card(section, label, entry, colors)
        self._monthly_filters_card(section, label, entry, colors)
        self._monthly_margin_card(section, label, entry, colors)
        groups = section(1, 2, "Grupos permitidos")
        self._grid_ubs_portfolio_group_controls(
            groups,
            columns=2,
            monthly=True,
            start_row=1,
        )

    def _monthly_risk_card(self, section, label, entry, colors) -> None:
        """Riesgo y objetivo, reserva y reinicios."""
        risk = section(0, 0, "Riesgo y objetivo")
        label(risk, 1, 0, "Capital")
        entry(risk, 1, 1, self.ubs_portfolio_capital, width=10)
        label(risk, 1, 2, "Tipo")
        self.ubs_portfolio_type_combo = ttk.Combobox(
            risk,
            textvariable=self.ubs_portfolio_type,
            state="readonly",
            width=12,
            values=("Conservador", "Moderado", "Agresivo"),
        )
        self.ubs_portfolio_type_combo.grid(row=1, column=3, sticky="w", padx=(0, 10), pady=4)
        label(risk, 2, 0, "DD valle %")
        entry(risk, 2, 1, self.ubs_portfolio_valley_pct)
        max_daily_var = getattr(self, "ubs_portfolio_max_daily_dd", None)
        if max_daily_var is not None:
            label(risk, 2, 2, "DD diario max")
            max_daily_entry = entry(risk, 2, 3, max_daily_var)
            self._tooltip_cls(
                max_daily_entry,
                "Limite monetario diario: DD cerrado del dia + flotante estimado de posiciones abiertas. Default 150.",
            )
        else:
            label(risk, 2, 2, "DD puntual %")
            entry(risk, 2, 3, self.ubs_portfolio_point_pct)
        label(risk, 4, 0, "Reserva DD %")
        reserve_entry = entry(risk, 4, 1, self.ubs_portfolio_dd_reserve_pct)
        self._tooltip_cls(
            reserve_entry,
            "Margen de seguridad sin utilizar. Con 10%, un limite DD de 350 optimiza hasta 315.",
        )
        label(risk, 4, 2, "Mes objetivo")
        self.ubs_portfolio_target_month_combo = ttk.Combobox(
            risk,
            textvariable=self.ubs_portfolio_target_month,
            state="readonly",
            width=12,
            values=(
                "01 - Enero", "02 - Febrero", "03 - Marzo", "04 - Abril",
                "05 - Mayo", "06 - Junio", "07 - Julio", "08 - Agosto",
                "09 - Septiembre", "10 - Octubre", "11 - Noviembre", "12 - Diciembre",
            ),
        )
        self.ubs_portfolio_target_month_combo.grid(row=4, column=3, sticky="w", padx=(0, 10), pady=4)
        self._tooltip_cls(
            self.ubs_portfolio_target_month_combo,
            "Evalua solamente este mes en cada año disponible del historico base y robustez.",
        )

    def _monthly_limits_card(self, section, label, entry, colors) -> None:
        """Busqueda y limites de unidades y candidatos."""
        limits = section(0, 1, "Busqueda y limites")
        label(limits, 1, 0, "Min trades")
        ttk.Spinbox(limits, from_=0, to=10000, width=8, textvariable=self.ubs_portfolio_min_trades).grid(
            row=1, column=1, sticky="w", pady=4
        )
        label(limits, 1, 2, "Top K")
        ttk.Spinbox(limits, from_=1, to=50, width=8, textvariable=self.ubs_portfolio_top_k).grid(
            row=1, column=3, sticky="w", padx=(0, 10), pady=4
        )
        label(limits, 2, 0, "Max cand.")
        ttk.Spinbox(limits, from_=1, to=500, width=8, textvariable=self.ubs_portfolio_max_candidates).grid(
            row=2, column=1, sticky="w", pady=4
        )
        label(limits, 2, 2, "Reinicios")
        restart_spin = ttk.Spinbox(limits, from_=0, to=20, width=8, textvariable=self.ubs_portfolio_search_restarts)
        restart_spin.grid(row=2, column=3, sticky="w", padx=(0, 10), pady=4)
        self._tooltip_cls(
            restart_spin,
            "Perturbaciones validas para escapar del optimo local. 0 desactiva; 4 es el valor recomendado.",
        )
        label(limits, 3, 0, "Max unidades/set")
        entry(limits, 3, 1, self.ubs_portfolio_max_units_per_set)
        label(limits, 3, 2, "Max unidades")
        entry(limits, 3, 3, self.ubs_portfolio_max_total_units)
        label(limits, 4, 0, "Max unid/simbolo")
        entry(limits, 4, 1, self.ubs_portfolio_max_units_per_symbol)
        label(limits, 4, 2, "Max sets/simbolo")
        ttk.Spinbox(limits, from_=1, to=50, width=8, textvariable=self.ubs_portfolio_max_sets_per_symbol).grid(
            row=4, column=3, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Checkbutton(limits, text="Mejora local", variable=self.ubs_portfolio_run_local_search).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 8)
        )
        deep_var = getattr(self, "ubs_portfolio_deep_optimization", None)
        if deep_var is not None:
            deep_check = ttk.Checkbutton(limits, text="Optimizacion profunda", variable=deep_var)
            deep_check.grid(row=5, column=2, columnspan=2, sticky="w", padx=(8, 10), pady=(4, 8))
            self._tooltip_cls(
                deep_check,
                "Si esta activo, refina la cartera estricta probando adiciones y swaps sin saltarse DD, margen, correlacion ni mejor mes 5A.",
            )

    def _monthly_correlation_card(self, section, label, entry, colors) -> None:
        """Limites de correlacion entre estrategias y portafolios."""
        corr = section(0, 2, "Correlacion")
        ttk.Checkbutton(corr, text="Filtro correlacion", variable=self.ubs_portfolio_use_correlation).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=10, pady=4
        )
        label(corr, 2, 0, "Max corr")
        entry(corr, 2, 1, self.ubs_portfolio_max_pair_corr)
        label(corr, 2, 2, "Downside")
        entry(corr, 2, 3, self.ubs_portfolio_max_downside_corr)
        label(corr, 3, 0, "Overlap DD")
        entry(corr, 3, 1, self.ubs_portfolio_max_dd_overlap)
        label(corr, 3, 2, "Corr portfolios")
        entry(corr, 3, 3, self.ubs_portfolio_max_portfolio_corr)
        corr_monthly_var = getattr(self, "ubs_portfolio_corr_with_monthly_portfolios", None)
        if corr_monthly_var is not None:
            corr_monthly_check = ttk.Checkbutton(corr, text="No corr mensual", variable=corr_monthly_var)
            corr_monthly_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 8))
            self._tooltip_cls(
                corr_monthly_check,
                "Si esta activo, el nuevo portafolio mensual debe respetar Max corr portfolios contra portafolios mensuales guardados.",
            )

    def _monthly_filters_card(self, section, label, entry, colors) -> None:
        """Filtros mensuales: meses positivos, grid, años y usados."""
        filters = section(1, 0, "Filtros mensuales")
        recent_months_check = ttk.Checkbutton(
            filters,
            text="3/6 meses +",
            variable=self.ubs_portfolio_require_3_positive_months_6m,
        )
        recent_months_check.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=4)
        self._tooltip_cls(
            recent_months_check,
            "Si esta activo, el portafolio solo usa Final Tick 6M accepted con al menos 3 meses positivos en los ultimos 6.",
        )
        grid_off_var = getattr(self, "ubs_portfolio_grid_off", None)
        if grid_off_var is not None:
            grid_off_check = ttk.Checkbutton(filters, text="Grid OFF", variable=grid_off_var)
            grid_off_check.grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 10), pady=4)
            self._tooltip_cls(
                grid_off_check,
                "Si esta activo, descarta candidatos cuyo .set tenga EnableGrid=true.",
            )
        strict_month_var = getattr(self, "ubs_portfolio_strict_yearly_month_validation", None)
        if strict_month_var is not None:
            strict_check = ttk.Checkbutton(
                filters,
                text="Validar años + mejor mes 5A",
                variable=strict_month_var,
            )
            strict_check.grid(row=2, column=0, columnspan=4, sticky="w", padx=10, pady=4)
            self._tooltip_cls(
                strict_check,
                "Si esta activo, todos los meses deben respetar el DD en los ultimos 5 años; el mes objetivo ademas debe pasar año a año y ser el mejor por net.",
            )
        daily_full_var = getattr(self, "ubs_portfolio_daily_dd_full_history", None)
        if daily_full_var is not None:
            daily_full_check = ttk.Checkbutton(
                filters,
                text="DD diario todo el año",
                variable=daily_full_var,
            )
            daily_full_check.grid(row=3, column=0, columnspan=4, sticky="w", padx=10, pady=4)
            self._tooltip_cls(
                daily_full_check,
                "Si esta activo, DD diario max se valida en todo el historico disponible; si no, solo en el mes objetivo.",
            )
        exclude_monthly_used_var = getattr(self, "ubs_portfolio_exclude_monthly_used", None)
        if exclude_monthly_used_var is not None:
            exclude_monthly_used_check = ttk.Checkbutton(
                filters,
                text="Excluir usados mensual",
                variable=exclude_monthly_used_var,
            )
            exclude_monthly_used_check.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 8))
            self._tooltip_cls(
                exclude_monthly_used_check,
                "Si esta activo, descarta sets ya usados en portafolios guardados de UBS Portafolio Mensual.",
            )

    def _monthly_margin_card(self, section, label, entry, colors) -> None:
        """Perfil de margen del broker o TTP y su tope."""
        margin = section(1, 1, "Margen")
        margin_var = getattr(self, "ubs_portfolio_validate_roboforex_margin", None)
        ttp_margin_var = getattr(self, "ubs_portfolio_validate_ttp_margin", None)
        margin_pct_var = getattr(self, "ubs_portfolio_max_margin_pct", None)
        if margin_var is not None and margin_pct_var is not None:
            broker_var = getattr(self, "ubs_broker", None)
            try:
                broker_label = str(broker_var.get()).strip().upper() or "Broker"
            except Exception:
                broker_label = "Broker"
            select_margin_profile = getattr(self, "_select_ubs_monthly_margin_profile", None)
            margin_check = ttk.Checkbutton(
                margin,
                text=broker_label if broker_label in {"ROBOFOREX", "AXI", "ICTRADING"} else "Broker",
                variable=margin_var,
                command=((lambda: select_margin_profile("broker")) if callable(select_margin_profile) else None),
            )
            margin_check.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=4)
            self._tooltip_cls(
                margin_check,
                "Valida margen estimado con el broker activo: Stocks 1:20 contract_size 100; resto 1:500 contract_size 1.",
            )
            if ttp_margin_var is not None:
                ttp_margin_check = ttk.Checkbutton(
                    margin,
                    text="TTP",
                    variable=ttp_margin_var,
                    command=((lambda: select_margin_profile("ttp")) if callable(select_margin_profile) else None),
                )
                ttp_margin_check.grid(row=1, column=2, columnspan=2, sticky="w", padx=(8, 10), pady=4)
                self._tooltip_cls(
                    ttp_margin_check,
                    "Valida margen TTP: forex 1:50, indices 1:15, commodities/metales/energias 1:10, stocks/crypto 1:2.",
                )
            label(margin, 2, 0, "Max margen %")
            entry(margin, 2, 1, margin_pct_var)
            tk.Label(
                margin,
                text="Si ambos estan apagados, genera con el broker activo por defecto.",
                bg=colors["panel"],
                fg=colors["muted"],
                font=("Segoe UI", 8),
            ).grid(row=3, column=0, columnspan=4, sticky="w", padx=10, pady=(2, 8))
