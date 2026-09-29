"""Lectura y reseteo del formulario del portafolio UBS."""
from __future__ import annotations


from portfolio_manager.ubs_portfolio import PortfolioType
from ui.ubs_portfolio_base import (
    DEFAULT_PORTFOLIO_FORM,
    PORTFOLIO_ASSET_GROUP_FLAGS,
    PORTFOLIO_TYPE_DISPLAY,
    PORTFOLIO_TYPE_LABELS,
)


class UBSPortfolioInputsMixin:
    """Lectura y reseteo del formulario del portafolio UBS."""

    def _parse_float_setting(self, value: str, label: str) -> float:
        try:
            return float(str(value).strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{label} debe ser numerico.") from exc

    def _parse_int_setting(self, value: object, label: str, *, minimum: int) -> int:
        try:
            parsed = int(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"{label} debe ser entero.") from exc
        if parsed < minimum:
            raise ValueError(f"{label} debe ser >= {minimum}.")
        return parsed

    def _parse_optional_int_setting(self, value: str, label: str) -> int | None:
        text = str(value).strip()
        if not text:
            return None
        return self._parse_int_setting(text, label, minimum=1)

    def _read_ubs_portfolio_inputs(self) -> dict[str, object]:
        capital = self._parse_float_setting(self.ubs_portfolio_capital.get(), "Capital")
        valley_pct = self._parse_float_setting(self.ubs_portfolio_valley_pct.get(), "DD valle")
        point_pct = valley_pct
        if capital <= 0 or valley_pct <= 0:
            raise ValueError("Capital y DD valle deben ser mayores que 0.")

        top_k = self._parse_int_setting(self.ubs_portfolio_top_k.get(), "Top K sets por simbolo", minimum=1)
        max_candidates = self._parse_int_setting(
            self.ubs_portfolio_max_candidates.get(),
            "Maximo total de candidatos",
            minimum=1,
        )
        min_trades = self._parse_int_setting(
            self.ubs_portfolio_min_trades.get(),
            "Minimo de trades 2020-2026",
            minimum=0,
        )
        max_sets_per_symbol = self._parse_int_setting(
            self.ubs_portfolio_max_sets_per_symbol.get(),
            "Maximo de sets por simbolo",
            minimum=1,
        )
        type_label = self.ubs_portfolio_type.get().strip()
        portfolio_type = PORTFOLIO_TYPE_LABELS.get(type_label, PortfolioType.BALANCED)
        values: dict[str, object] = {
            "capital": capital,
            "valley_dd_pct": valley_pct,
            "point_dd_pct": point_pct,
            "portfolio_type": portfolio_type.value,
            "portfolio_type_label": PORTFOLIO_TYPE_DISPLAY[portfolio_type.value],
            "enforce_point_dd": False,
            "top_k_per_symbol": top_k,
            "max_total_candidates": max_candidates,
            "min_trades_2020_2026": min_trades,
            "max_units_per_set": self._parse_optional_int_setting(
                self.ubs_portfolio_max_units_per_set.get(),
                "Maximo de unidades por set",
            ),
            "max_total_units": self._parse_optional_int_setting(
                self.ubs_portfolio_max_total_units.get(),
                "Maximo total de unidades",
            ),
            "max_units_per_symbol": self._parse_optional_int_setting(
                self.ubs_portfolio_max_units_per_symbol.get(),
                "Maximo de unidades por simbolo",
            ),
            "max_sets_per_symbol": max_sets_per_symbol,
            "run_local_search": bool(self.ubs_portfolio_run_local_search.get()),
            "deep_optimization": bool(getattr(self, "ubs_portfolio_deep_optimization").get())
            if hasattr(self, "ubs_portfolio_deep_optimization")
            else False,
            "use_correlation": bool(self.ubs_portfolio_use_correlation.get()),
            "require_3_positive_months_6m": bool(self.ubs_portfolio_require_3_positive_months_6m.get()),
            "grid_off": bool(getattr(self, "ubs_portfolio_grid_off").get())
            if hasattr(self, "ubs_portfolio_grid_off")
            else False,
            "exclude_used_sets": bool(getattr(self, "ubs_portfolio_exclude_used_sets").get())
            if hasattr(self, "ubs_portfolio_exclude_used_sets")
            else True,
            "dd_reserve_pct": self._parse_float_setting(
                self.ubs_portfolio_dd_reserve_pct.get(),
                "Reserva DD",
            ),
            "search_restarts": self._parse_int_setting(
                self.ubs_portfolio_search_restarts.get(),
                "Reinicios de busqueda",
                minimum=0,
            ),
            "max_pair_corr": self._parse_optional_float_setting(
                self.ubs_portfolio_max_pair_corr.get(),
                "Max correlacion",
            ),
            "max_downside_corr": self._parse_optional_float_setting(
                self.ubs_portfolio_max_downside_corr.get(),
                "Max correlacion downside",
            ),
            "max_dd_overlap": self._parse_optional_float_setting(
                self.ubs_portfolio_max_dd_overlap.get(),
                "Max solapamiento DD",
            ),
            "max_portfolio_corr": self._parse_optional_float_setting(
                self.ubs_portfolio_max_portfolio_corr.get(),
                "Max corr portafolios",
            ),
        }
        allowed_groups = self._ubs_portfolio_allowed_asset_groups()
        if not allowed_groups:
            raise ValueError(
                "Selecciona al menos un grupo permitido: Forex, Metales, Indices, "
                "Energias, Crypto, Stocks, Bonds o Softs."
            )
        values["allowed_asset_groups"] = sorted(allowed_groups)
        margin_profile = self._portfolio_margin_profile()
        values["margin_profile"] = margin_profile
        margin_profile_var = getattr(self, "ubs_portfolio_margin_profile", None)
        margin_pct_var = getattr(self, "ubs_portfolio_max_margin_pct", None)
        if margin_profile_var is not None or margin_pct_var is not None:
            if margin_pct_var is None:
                raise ValueError("Max margen no esta configurado.")
            max_margin_pct = self._parse_float_setting(margin_pct_var.get(), "Max margen")
            if max_margin_pct <= 0:
                raise ValueError("Max margen debe ser mayor que 0.")
            values["validate_margin"] = True
            values["validate_roboforex_margin"] = margin_profile != "ttp"
            values["validate_ttp_margin"] = margin_profile == "ttp"
            values["max_margin_pct"] = max_margin_pct
        else:
            values["validate_margin"] = False
            values["validate_roboforex_margin"] = False
            values["validate_ttp_margin"] = False
            values["max_margin_pct"] = None
        if not values["use_correlation"]:
            values["max_pair_corr"] = None
            values["max_downside_corr"] = None
            values["max_dd_overlap"] = None
            values["max_portfolio_corr"] = None
        for key, label in (
            ("max_pair_corr", "Max correlacion"),
            ("max_downside_corr", "Max correlacion downside"),
            ("max_dd_overlap", "Max solapamiento DD"),
            ("max_portfolio_corr", "Max corr portafolios"),
        ):
            value = values[key]
            if value is not None and not (0 <= float(value) <= 1):
                raise ValueError(f"{label} debe estar entre 0 y 1.")
        if not (0 <= float(values["dd_reserve_pct"]) < 100):
            raise ValueError("Reserva DD debe estar entre 0 y menos de 100.")
        return values

    def _parse_optional_float_setting(self, value: str, label: str) -> float | None:
        text = str(value).strip()
        if not text:
            return None
        parsed = self._parse_float_setting(text, label)
        return parsed

    def _set_ubs_portfolio_running(self, running: bool) -> None:
        self.ubs_portfolio_running = running
        state = "disabled" if running else "normal"
        for button in getattr(self, "ubs_portfolio_buttons", []):
            try:
                button.configure(state=state)
            except Exception:
                pass
        self._set_ubs_portfolio_save_enabled(
            (not running) and getattr(self, "ubs_portfolio_pending_result", None) is not None
        )
        if hasattr(self, "ubs_portfolio_progress"):
            if running:
                self.ubs_portfolio_progress.start(12)
            else:
                self.ubs_portfolio_progress.stop()

    def _set_ubs_portfolio_save_enabled(self, enabled: bool) -> None:
        button = getattr(self, "ubs_portfolio_save_button", None)
        if button is None:
            return
        try:
            button.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _reset_ubs_portfolio_form(self) -> None:
        self.ubs_portfolio_capital.set(DEFAULT_PORTFOLIO_FORM["capital"])
        self.ubs_portfolio_valley_pct.set(DEFAULT_PORTFOLIO_FORM["valley_dd_pct"])
        self.ubs_portfolio_point_pct.set(DEFAULT_PORTFOLIO_FORM["point_dd_pct"])
        self.ubs_portfolio_type.set(DEFAULT_PORTFOLIO_FORM["portfolio_type"])
        self.ubs_portfolio_top_k.set(DEFAULT_PORTFOLIO_FORM["top_k_per_symbol"])
        self.ubs_portfolio_max_candidates.set(DEFAULT_PORTFOLIO_FORM["max_total_candidates"])
        self.ubs_portfolio_min_trades.set(DEFAULT_PORTFOLIO_FORM["min_trades_2020_2026"])
        self.ubs_portfolio_max_units_per_set.set(DEFAULT_PORTFOLIO_FORM["max_units_per_set"])
        self.ubs_portfolio_max_total_units.set(DEFAULT_PORTFOLIO_FORM["max_total_units"])
        self.ubs_portfolio_max_units_per_symbol.set(DEFAULT_PORTFOLIO_FORM["max_units_per_symbol"])
        self.ubs_portfolio_max_sets_per_symbol.set(DEFAULT_PORTFOLIO_FORM["max_sets_per_symbol"])
        self.ubs_portfolio_run_local_search.set(DEFAULT_PORTFOLIO_FORM["run_local_search"])
        if hasattr(self, "ubs_portfolio_deep_optimization"):
            self.ubs_portfolio_deep_optimization.set(DEFAULT_PORTFOLIO_FORM["deep_optimization"])
        self.ubs_portfolio_use_correlation.set(DEFAULT_PORTFOLIO_FORM["use_correlation"])
        self.ubs_portfolio_require_3_positive_months_6m.set(DEFAULT_PORTFOLIO_FORM["require_3_positive_months_6m"])
        if hasattr(self, "ubs_portfolio_grid_off"):
            self.ubs_portfolio_grid_off.set(False)
        if hasattr(self, "ubs_portfolio_exclude_used_sets"):
            self.ubs_portfolio_exclude_used_sets.set(True)
        margin_profile_var = getattr(self, "ubs_portfolio_margin_profile", None)
        if margin_profile_var is not None:
            margin_profile_var.set(self._portfolio_margin_profile_display())
        margin_pct_var = getattr(self, "ubs_portfolio_max_margin_pct", None)
        if margin_pct_var is not None:
            margin_pct_var.set("100")
        for _group, suffix in PORTFOLIO_ASSET_GROUP_FLAGS:
            var = getattr(self, f"ubs_portfolio_{suffix}", None)
            if var is not None:
                var.set(True)
        self.ubs_portfolio_dd_reserve_pct.set(DEFAULT_PORTFOLIO_FORM["dd_reserve_pct"])
        self.ubs_portfolio_search_restarts.set(DEFAULT_PORTFOLIO_FORM["search_restarts"])
        self.ubs_portfolio_max_pair_corr.set(DEFAULT_PORTFOLIO_FORM["max_pair_corr"])
        self.ubs_portfolio_max_downside_corr.set(DEFAULT_PORTFOLIO_FORM["max_downside_corr"])
        self.ubs_portfolio_max_dd_overlap.set(DEFAULT_PORTFOLIO_FORM["max_dd_overlap"])
        self.ubs_portfolio_max_portfolio_corr.set(DEFAULT_PORTFOLIO_FORM["max_portfolio_corr"])
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self.ubs_portfolio_pending_proposals = []
        self._set_ubs_portfolio_save_enabled(False)
        self._clear_ubs_portfolio_result_tables()
        self.ubs_portfolio_status.set("Formulario restaurado.")
