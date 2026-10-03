from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class UBSAgentViewCardsMixin:
    """Tarjetas de filtros, robustez, Final Tick y regresiva del Agente UBS."""

    def _build_ubs_agent_filter_cards(self, inner: ttk.Frame) -> None:
        self._build_ubs_agent_pass_card(inner)
        self._build_ubs_agent_robustness_card(inner)
        self._build_ubs_agent_final_tick_card(inner)
        self._build_ubs_agent_final_tick_6m_card(inner)
        self._build_ubs_agent_regression_card(inner)

    def _agent_card(self, inner: ttk.Frame, title: str, row: int, *, pady) -> tk.Widget:
        card = self._card(inner, title)
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        for column in (1, 3, 5):
            card.columnconfigure(column, weight=1)
        return card

    def _agent_card_dates(self, card, from_variable, to_variable, tip: str) -> None:
        ttk.Label(card, text="Desde", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(20, 10), pady=7
        )
        from_entry = ttk.Entry(card, textvariable=from_variable, width=14)
        from_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=7)
        self._tooltip_cls(from_entry, tip)
        ttk.Label(card, text="Hasta", style="Panel.TLabel").grid(
            row=1, column=2, sticky="w", padx=(10, 10), pady=7
        )
        to_entry = ttk.Entry(card, textvariable=to_variable, width=14)
        to_entry.grid(row=1, column=3, sticky="ew", padx=(0, 10), pady=7)
        self._tooltip_cls(to_entry, tip)

    def _agent_card_auto(self, card, text: str, variable, tip: str = "") -> None:
        auto_row = tk.Frame(card, bg=self.colors["panel"])
        auto_row.grid(row=1, column=4, columnspan=2, sticky="ew", padx=(10, 20), pady=7)
        auto_row.columnconfigure(0, weight=1)
        tk.Label(
            auto_row,
            text=text,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self._toggle_switch_cls(
            auto_row,
            variable=variable,
            bg=self.colors["panel"],
            width=34,
            height=18,
        ).grid(row=0, column=1, sticky="e")
        if tip:
            self._tooltip_cls(auto_row, tip)

    def _agent_card_fields(
        self,
        card,
        fields,
        *,
        first_row: int,
        entry_width: int | None = 8,
        lead_pair: bool = False,
        spin_right_pad: int | None = 10,
        date_tip: str = "",
    ) -> None:
        for index, (label, variable, kind) in enumerate(fields):
            if lead_pair and index < 2:
                row, column = first_row - 1, index * 2
            elif lead_pair:
                row = first_row + (index - 2) // 3
                column = ((index - 2) % 3) * 2
            else:
                row = first_row + index // 3
                column = (index % 3) * 2
            right_pad = 10 if column < 4 else 20
            ttk.Label(card, text=label, style="Panel.TLabel").grid(
                row=row, column=column, sticky="w", padx=(20 if column == 0 else 10, 10), pady=7
            )
            if kind == "spin":
                widget = ttk.Spinbox(card, from_=0, to=100000, textvariable=variable, width=8)
                pad = right_pad if spin_right_pad is None else spin_right_pad
            else:
                width = {} if entry_width is None else {"width": 14 if kind == "date" else entry_width}
                widget = ttk.Entry(card, textvariable=variable, **width)
                pad = right_pad
            widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, pad), pady=7)
            if kind == "date" and date_tip:
                self._tooltip_cls(widget, date_tip)

    def _agent_card_footer(self, card, row: int, note: str, button_text: str) -> None:
        ttk.Label(card, text=note, style="Muted.TLabel").grid(
            row=row, column=0, columnspan=5, sticky="w", padx=20, pady=(4, 14)
        )
        ttk.Button(
            card,
            text=button_text,
            style="Primary.TButton",
            command=self._save_ubs_agent_clicked,
        ).grid(row=row, column=5, sticky="e", padx=20, pady=(4, 14))

    def _build_ubs_agent_pass_card(self, inner: ttk.Frame) -> None:
        pass_config = self._agent_card(inner, "Filtros de aceptacion", 2, pady=(16, 24))
        self._agent_card_fields(
            pass_config,
            [
                ("Profit neto min", self.ubs_pass_min_net_profit, "entry"),
                ("Profit factor min", self.ubs_pass_min_profit_factor, "entry"),
                ("Trades min", self.ubs_pass_min_trades, "spin"),
                ("DD max %", self.ubs_pass_max_drawdown_pct, "entry"),
                ("Recovery min", self.ubs_pass_min_recovery_factor, "entry"),
            ],
            first_row=1,
            entry_width=None,
            spin_right_pad=None,
        )
        self._agent_card_footer(
            pass_config,
            3,
            "Profit neto min es moneda de la cuenta. Con deposito 1000, default 100 = 10%. Estabilidad mensual: score, no filtro hard.",
            "Guardar configuracion Agente UBS",
        )

    def _build_ubs_agent_robustness_card(self, inner: ttk.Frame) -> None:
        robust = self._agent_card(inner, "Robustez OOS", 3, pady=(0, 24))
        self._agent_card_dates(
            robust,
            self.ubs_robust_from_date,
            self.ubs_robust_to_date,
            "Formato: YYYY.MM.DD.\n"
            "Ventana fuera de muestra para candidatos accepted del agente.\n"
            "Dejar vacio para usar las fechas del template tester.",
        )
        self._agent_card_auto(robust, "Auto robustez", self.ubs_robust_auto)
        self._agent_card_fields(
            robust,
            [
                ("Net min", self.ubs_robust_pass_min_net_profit, "entry"),
                ("PF min", self.ubs_robust_pass_min_profit_factor, "entry"),
                ("Trades min", self.ubs_robust_pass_min_trades, "spin"),
                ("DD max %", self.ubs_robust_pass_max_drawdown_pct, "entry"),
                ("Recovery min", self.ubs_robust_pass_min_recovery_factor, "entry"),
                ("Ret. net >=", self.ubs_robust_min_net_retention, "entry"),
                ("Ret. edge PF >=", self.ubs_robust_min_pf_edge_retention, "entry"),
                ("Ret. recovery >=", self.ubs_robust_min_recovery_retention, "entry"),
                ("Inflacion DD <= x", self.ubs_robust_max_dd_inflation, "entry"),
                ("Bonus OK legacy", self.ubs_robust_positive_bonus, "entry"),
                ("Bonus FAIL legacy", self.ubs_robust_negative_bonus, "entry"),
            ],
            first_row=2,
        )
        self._agent_card_footer(
            robust,
            6,
            "Pasa si cumple los limites OOS absolutos y conserva el edge frente a Resultados. 0 desactiva cada limite relativo; datos no disponibles quedan neutros.",
            "Guardar robustez",
        )

    def _build_ubs_agent_final_tick_card(self, inner: ttk.Frame) -> None:
        final_tick = self._agent_card(inner, "Final Tick (Every Tick)", 4, pady=(0, 24))
        self._agent_card_dates(
            final_tick,
            self.ubs_final_tick_from_date,
            self.ubs_final_tick_to_date,
            "Formato: YYYY.MM.DD.\n"
            "Mismo tramo para OHLC (Model=1) y Every Tick real (Model=4).",
        )
        self._agent_card_auto(
            final_tick,
            "Auto Final Tick",
            self.ubs_final_tick_auto,
            "Al terminar la robustez OOS, lanza Final Tick automaticamente\nsobre los robust accepted pendientes.",
        )
        self._agent_card_fields(
            final_tick,
            [
                ("HQ min %", self.ubs_final_tick_min_history_quality, "entry"),
                ("Min ops OHLC", self.ubs_final_tick_min_ohlc_trades, "spin"),
                ("Net delta %", self.ubs_final_tick_max_net_delta_pct, "entry"),
                ("PF delta %", self.ubs_final_tick_max_pf_delta_pct, "entry"),
                ("DD delta %", self.ubs_final_tick_max_dd_delta_pct, "entry"),
                ("Trades delta %", self.ubs_final_tick_max_trades_delta_pct, "entry"),
            ],
            first_row=2,
        )
        self._agent_card_footer(
            final_tick,
            5,
            "Compara OHLC vs Every Tick real en el mismo tramo. PF/DD/trades son los criterios activos; net es informativo. Accepted +120 al peso; rejected -160 menos causas.",
            "Guardar Final Tick",
        )

    def _build_ubs_agent_final_tick_6m_card(self, inner: ttk.Frame) -> None:
        final_tick_6m = self._agent_card(inner, "Final Tick 6M", 5, pady=(0, 24))
        self._agent_card_auto(
            final_tick_6m,
            "Auto Final Tick 6M",
            self.ubs_final_tick_6m_auto,
            "Al terminar Final Tick corto, lanza Final Tick 6M automaticamente\nsobre los corto accepted y pending_ohlc_trades pendientes.",
        )
        self._agent_card_fields(
            final_tick_6m,
            [
                ("Desde", self.ubs_final_tick_6m_from_date, "date"),
                ("Hasta", self.ubs_final_tick_6m_to_date, "date"),
                ("Ops bajas desde", self.ubs_final_tick_6m_ohlc_from_date, "date"),
                ("Ops bajas hasta", self.ubs_final_tick_6m_ohlc_to_date, "date"),
                ("HQ min %", self.ubs_final_tick_min_history_quality, "entry"),
                ("Min ops OHLC", self.ubs_final_tick_min_ohlc_trades, "spin"),
                ("Net delta %", self.ubs_final_tick_max_net_delta_pct, "entry"),
                ("PF delta %", self.ubs_final_tick_max_pf_delta_pct, "entry"),
                ("DD delta %", self.ubs_final_tick_max_dd_delta_pct, "entry"),
                ("Trades delta %", self.ubs_final_tick_max_trades_delta_pct, "entry"),
                ("W1 FT ops", self.ubs_final_tick_min_trades_w1, "spin"),
                ("MN FT ops", self.ubs_final_tick_min_trades_mn, "spin"),
            ],
            first_row=2,
            lead_pair=True,
            spin_right_pad=None,
            date_tip="Formato: YYYY.MM.DD.\n"
            "Tramo principal de 6M para OHLC vs Every Tick real.\n"
            "Ops bajas desde/hasta solo se usan para reintentar filas con pocas operaciones OHLC.",
        )
        self._agent_card_footer(
            final_tick_6m,
            6,
            "Valida el tramo largo para uso real. Portfolio solo usa Final Tick 6M accepted. Ops bajas reintenta solo pendientes por pocas operaciones OHLC.",
            "Guardar Final Tick 6M",
        )

    def _build_ubs_agent_regression_card(self, inner: ttk.Frame) -> None:
        regression = self._agent_card(inner, "Prueba Regresiva", 6, pady=(0, 24))
        self._agent_card_auto(
            regression,
            "Auto Regresiva",
            self.ubs_regression_auto,
            "Al terminar Final Tick 6M, lanza la prueba regresiva automaticamente\nsobre los 6M accepted pendientes.",
        )
        self._agent_card_fields(
            regression,
            [
                ("Desde", self.ubs_regression_from_date, "date"),
                ("Hasta", self.ubs_regression_to_date, "date"),
                ("Net >", self.ubs_regression_min_net_profit, "entry"),
                ("PF >=", self.ubs_regression_min_profit_factor, "entry"),
                ("Ops >=", self.ubs_regression_min_trades, "spin"),
                ("DD % <=", self.ubs_regression_max_drawdown_pct, "entry"),
                ("Recovery >=", self.ubs_regression_min_recovery_factor, "entry"),
                ("Meses + >=", self.ubs_regression_min_positive_month_ratio, "entry"),
                ("PF ef >=", self.ubs_regression_min_pf_efficiency, "entry"),
                ("DD x <=", self.ubs_regression_max_dd_ratio, "entry"),
                ("Puntos OK", self.ubs_regression_positive_points, "entry"),
                ("Puntos FAIL", self.ubs_regression_negative_points, "entry"),
                ("W1 REG ops", self.ubs_regression_min_trades_w1, "spin"),
                ("MN REG ops", self.ubs_regression_min_trades_mn, "spin"),
            ],
            first_row=2,
            lead_pair=True,
            spin_right_pad=None,
            date_tip="Formato: YYYY.MM.DD.\n"
            "Holdout historico OHLC 1 minuto (Model=1) hacia atras.\n"
            "El reporte debe cubrir exactamente el rango configurado.",
        )
        self._agent_card_footer(
            regression,
            6,
            "Holdout OHLC historico sobre Final Tick 6M accepted. Fallos tecnicos (historico, reporte, fechas) son neutros: 0 puntos.",
            "Guardar Regresiva",
        )
