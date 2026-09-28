from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.ubs_portfolio_view_monthly import (  # noqa: F401  fachada del modulo
    PORTFOLIO_GROUP_CHECKS,
    UBSPortfolioMonthlyInputsMixin,
)
from ui.ubs_portfolio_view_panes import UBSPortfolioPanesMixin
from ui.ubs_portfolio_view_windows import UBSPortfolioWindowsMixin


class UBSPortfolioViewMixin(
    UBSPortfolioMonthlyInputsMixin,
    UBSPortfolioPanesMixin,
    UBSPortfolioWindowsMixin,
):
    def _portfolio_window_master(self):
        try:
            return object.__getattribute__(self, "_app")
        except Exception:
            return self

    def _ubs_portfolio_group_controls(self):
        return [
            (label, getattr(self, f"ubs_portfolio_{suffix}", None))
            for label, suffix in PORTFOLIO_GROUP_CHECKS
        ]

    def _grid_ubs_portfolio_group_controls(
        self,
        parent: tk.Misc,
        *,
        columns: int,
        monthly: bool,
        start_row: int = 0,
    ) -> None:
        controls = self._ubs_portfolio_group_controls()
        for column in range(columns):
            parent.columnconfigure(column, weight=1, uniform="portfolio_groups")
        for index, (label_text, variable) in enumerate(controls):
            if variable is None:
                continue
            group_check = ttk.Checkbutton(parent, text=label_text, variable=variable)
            group_check.grid(
                row=start_row + index // columns,
                column=index % columns,
                sticky="w",
                padx=(10, 8),
                pady=4,
            )
            self._tooltip_cls(
                group_check,
                "Si esta activo, permite este grupo de activos para formar el portafolio "
                + ("mensual." if monthly else "normal."),
            )

    def _build_ubs_portfolio(self, parent: ttk.Frame) -> None:
        colors = self.colors
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        panel = self._card(parent, "Portfolio Builder")
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        main_split = ttk.PanedWindow(panel, orient="vertical")
        main_split.grid(row=1, column=0, sticky="nsew", padx=20, pady=(4, 18))
        self.ubs_portfolio_config_split = main_split

        config_pane = ttk.Frame(main_split, style="Panel.TFrame")
        content_pane = ttk.Frame(main_split, style="Panel.TFrame")
        main_split.add(config_pane, weight=0)
        main_split.add(content_pane, weight=1)
        config_pane.columnconfigure(0, weight=1)
        content_pane.columnconfigure(0, weight=1)
        content_pane.rowconfigure(4, weight=1)

        form = tk.Frame(config_pane, bg=colors["panel_alt"])
        form.grid(row=0, column=0, sticky="ew", pady=(0, 6))
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

        target_month_var = getattr(self, "ubs_portfolio_target_month", None)
        grid_off_var = getattr(self, "ubs_portfolio_grid_off", None)
        exclude_used_var = getattr(self, "ubs_portfolio_exclude_used_sets", None)

        label(0, 0, "Capital")
        ttk.Entry(form, textvariable=self.ubs_portfolio_capital, width=10).grid(row=0, column=1, sticky="w", pady=5)
        label(0, 2, "DD valle %")
        ttk.Entry(form, textvariable=self.ubs_portfolio_valley_pct, width=8).grid(row=0, column=3, sticky="w", pady=5)
        if target_month_var is not None:
            label(0, 4, "DD puntual %")
            ttk.Entry(form, textvariable=self.ubs_portfolio_point_pct, width=8).grid(
                row=0, column=5, sticky="w", pady=5
            )
            type_label_col = 6
            type_input_col = 7
        else:
            type_label_col = 4
            type_input_col = 5
        label(0, type_label_col, "Base")
        self.ubs_portfolio_type_combo = ttk.Combobox(
            form,
            textvariable=self.ubs_portfolio_type,
            state="readonly",
            width=12,
            values=("Conservador", "Moderado", "Agresivo"),
        )
        self.ubs_portfolio_type_combo.grid(row=0, column=type_input_col, sticky="w", pady=5)
        self._tooltip_cls(
            self.ubs_portfolio_type_combo,
            "Perfil usado para elegir la composicion comun. El guardado crea un solo portafolio A/M/C con esos mismos sets.",
        )
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
        if target_month_var is not None:
            label(3, 4, "Mes objetivo")
            self.ubs_portfolio_target_month_combo = ttk.Combobox(
                form,
                textvariable=target_month_var,
                state="readonly",
                width=12,
                values=(
                    "01 - Enero", "02 - Febrero", "03 - Marzo", "04 - Abril",
                    "05 - Mayo", "06 - Junio", "07 - Julio", "08 - Agosto",
                    "09 - Septiembre", "10 - Octubre", "11 - Noviembre", "12 - Diciembre",
                ),
            )
            self.ubs_portfolio_target_month_combo.grid(
                row=3,
                column=5,
                columnspan=2,
                sticky="w",
                pady=5,
            )
            self._tooltip_cls(
                self.ubs_portfolio_target_month_combo,
                "Evalua solamente este mes en cada año disponible del historico base y robustez.",
            )
            if grid_off_var is not None:
                grid_off_check = ttk.Checkbutton(
                    form,
                    text="Grid OFF",
                    variable=grid_off_var,
                )
                grid_off_check.grid(row=3, column=7, sticky="w", padx=(8, 4), pady=5)
                self._tooltip_cls(
                    grid_off_check,
                    "Si esta activo, descarta candidatos cuyo .set tenga EnableGrid=true.",
                )
            strict_month_var = getattr(self, "ubs_portfolio_strict_yearly_month_validation", None)
            if strict_month_var is not None:
                strict_check = ttk.Checkbutton(
                    form,
                    text="Validar años + mejor mes 5A",
                    variable=strict_month_var,
                )
                strict_check.grid(row=3, column=8, columnspan=4, sticky="w", padx=(8, 10), pady=5)
                self._tooltip_cls(
                    strict_check,
                    "Si esta activo, todos los meses deben respetar el DD en los ultimos 5 años; el mes objetivo ademas debe pasar año a año y ser el mejor por net.",
                )
            daily_full_var = getattr(self, "ubs_portfolio_daily_dd_full_history", None)
            if daily_full_var is not None:
                daily_full_check = ttk.Checkbutton(
                    form,
                    text="DD diario todo el año",
                    variable=daily_full_var,
                )
                daily_full_check.grid(row=4, column=4, columnspan=2, sticky="w", padx=(8, 4), pady=5)
                self._tooltip_cls(
                    daily_full_check,
                    "Si esta activo, DD diario max se valida en todo el historico disponible; si no, solo en el mes objetivo.",
                )
            deep_var = getattr(self, "ubs_portfolio_deep_optimization", None)
            if deep_var is not None:
                deep_check = ttk.Checkbutton(
                    form,
                    text="Optimización profunda",
                    variable=deep_var,
                )
                deep_check.grid(row=4, column=6, columnspan=2, sticky="w", padx=(8, 10), pady=5)
                self._tooltip_cls(
                    deep_check,
                    "Si esta activo, refina la cartera estricta probando adiciones y swaps sin saltarse DD, margen, correlacion ni mejor mes 5A.",
                )
            exclude_monthly_used_var = getattr(self, "ubs_portfolio_exclude_monthly_used", None)
            if exclude_monthly_used_var is not None:
                exclude_monthly_used_check = ttk.Checkbutton(
                    form,
                    text="Excluir usados mensual",
                    variable=exclude_monthly_used_var,
                )
                exclude_monthly_used_check.grid(row=4, column=8, columnspan=2, sticky="w", padx=(8, 4), pady=5)
                self._tooltip_cls(
                    exclude_monthly_used_check,
                    "Si esta activo, descarta sets ya usados en portafolios guardados de UBS Portafolio Mensual.",
                )
            corr_monthly_var = getattr(self, "ubs_portfolio_corr_with_monthly_portfolios", None)
            if corr_monthly_var is not None:
                corr_monthly_check = ttk.Checkbutton(
                    form,
                    text="No corr mensual",
                    variable=corr_monthly_var,
                )
                corr_monthly_check.grid(row=4, column=10, columnspan=2, sticky="w", padx=(8, 10), pady=5)
                self._tooltip_cls(
                    corr_monthly_check,
                    "Si esta activo, el nuevo portafolio mensual debe respetar Max corr portfolios contra portafolios mensuales guardados.",
                )
            margin_var = getattr(self, "ubs_portfolio_validate_roboforex_margin", None)
            ttp_margin_var = getattr(self, "ubs_portfolio_validate_ttp_margin", None)
            margin_pct_var = getattr(self, "ubs_portfolio_max_margin_pct", None)
            if margin_var is not None and margin_pct_var is not None:
                select_margin_profile = getattr(self, "_select_ubs_monthly_margin_profile", None)
                margin_check = ttk.Checkbutton(
                    form,
                    text="Margen broker",
                    variable=margin_var,
                    command=(
                        (lambda: select_margin_profile("roboforex"))
                        if callable(select_margin_profile)
                        else None
                    ),
                )
                margin_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=(10, 4), pady=5)
                self._tooltip_cls(
                    margin_check,
                    "Valida margen estimado con Stocks 1:20 contract_size 100; resto 1:500 contract_size 1.",
                )
                if ttp_margin_var is not None:
                    ttp_margin_check = ttk.Checkbutton(
                        form,
                        text="Margen TTP",
                        variable=ttp_margin_var,
                        command=(
                            (lambda: select_margin_profile("ttp"))
                            if callable(select_margin_profile)
                            else None
                        ),
                    )
                    ttp_margin_check.grid(row=4, column=2, columnspan=2, sticky="w", padx=(8, 4), pady=5)
                    self._tooltip_cls(
                        ttp_margin_check,
                        "Valida margen TTP: forex 1:50, indices 1:15, commodities/metales/energias 1:10, stocks/crypto 1:2.",
                    )
                    margin_label_col = 4
                    margin_entry_col = 5
                else:
                    margin_label_col = 2
                    margin_entry_col = 3
                label(4, margin_label_col, "Max margen %")
                ttk.Entry(form, textvariable=margin_pct_var, width=8).grid(
                    row=4, column=margin_entry_col, sticky="w", pady=5
                )
            allow_group_vars = self._ubs_portfolio_group_controls()
            if any(var is not None for _label_text, var in allow_group_vars):
                label(5, 0, "Grupos permitidos")
                group_grid = tk.Frame(form, bg=colors["panel"])
                group_grid.grid(
                    row=5,
                    column=1,
                    columnspan=11,
                    sticky="ew",
                    padx=(4, 10),
                    pady=5,
                )
                self._grid_ubs_portfolio_group_controls(
                    group_grid,
                    columns=4,
                    monthly=True,
                )
        else:
            if exclude_used_var is not None:
                exclude_used_check = ttk.Checkbutton(
                    form,
                    text="Excluir usados",
                    variable=exclude_used_var,
                    command=getattr(self, "_refresh_ubs_portfolio_availability", None),
                )
                exclude_used_check.grid(
                    row=3,
                    column=4,
                    sticky="w",
                    padx=(8, 4),
                    pady=5,
                )
                self._tooltip_cls(
                    exclude_used_check,
                    "Activado: no reutiliza sets guardados en otros portafolios. "
                    "Desactivado: permite reutilizarlos si pasan DD, correlacion y los demas filtros.",
                )
            if grid_off_var is not None:
                grid_off_check = ttk.Checkbutton(
                    form,
                    text="Grid OFF",
                    variable=grid_off_var,
                )
                grid_off_check.grid(
                    row=3,
                    column=5 if exclude_used_var is not None else 4,
                    sticky="w",
                    padx=(8, 4),
                    pady=5,
                )
                self._tooltip_cls(
                    grid_off_check,
                    "Si esta activo, descarta candidatos cuyo .set tenga EnableGrid=true.",
                )
            deep_var = getattr(self, "ubs_portfolio_deep_optimization", None)
            if deep_var is not None:
                deep_check = ttk.Checkbutton(
                    form,
                    text="Optimizacion profunda",
                    variable=deep_var,
                )
                deep_check.grid(row=3, column=10, columnspan=2, sticky="w", padx=(8, 10), pady=5)
                self._tooltip_cls(
                    deep_check,
                    "Refina la cartera ampliando candidatos y probando adiciones/swaps sin romper DD valle, margen, correlacion ni grupos.",
                )
            margin_profile_var = getattr(self, "ubs_portfolio_margin_profile", None)
            margin_pct_var = getattr(self, "ubs_portfolio_max_margin_pct", None)
            if margin_profile_var is not None:
                label(3, 6, "Perfil margen")
                margin_combo = ttk.Combobox(
                    form,
                    textvariable=margin_profile_var,
                    state="readonly",
                    width=12,
                    values=("ROBOFOREX", "AXI", "ICTRADING", "TTP"),
                )
                margin_combo.grid(row=3, column=7, sticky="w", pady=5)
                self._tooltip_cls(
                    margin_combo,
                    "Perfil para validar margen. ROBOFOREX/AXI/ICTRADING usan Stocks 1:20 y resto 1:500; TTP usa reglas prop.",
                )
            if margin_pct_var is not None:
                label(3, 8, "Max margen %")
                ttk.Entry(form, textvariable=margin_pct_var, width=8).grid(
                    row=3, column=9, sticky="w", pady=5
                )
            allow_group_vars = self._ubs_portfolio_group_controls()
            if any(var is not None for _label_text, var in allow_group_vars):
                label(4, 0, "Grupos permitidos")
                group_grid = tk.Frame(form, bg=colors["panel"])
                group_grid.grid(
                    row=4,
                    column=1,
                    columnspan=11,
                    sticky="ew",
                    padx=(4, 10),
                    pady=5,
                )
                self._grid_ubs_portfolio_group_controls(
                    group_grid,
                    columns=4,
                    monthly=False,
                )

        if target_month_var is not None:
            for child in form.winfo_children():
                child.destroy()
            self._build_ubs_monthly_portfolio_input_groups(form)

        resize_hint = tk.Label(
            config_pane,
            text="↕ arrastra la barra para redimensionar configuracion",
            bg=colors["panel"],
            fg=colors["muted"],
            font=("Segoe UI", 8),
        )
        resize_hint.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        self._build_ubs_portfolio_actions(content_pane, colors, target_month_var)
        self._build_ubs_portfolio_panes(content_pane, colors, target_month_var)
