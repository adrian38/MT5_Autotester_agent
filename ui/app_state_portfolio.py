"""Estado inicial de las pantallas de portafolio y de los resumenes UBS."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ui.app_base import _display_margin_profile, _display_portfolio_type
from ui.app_widgets import RoundedButton
from ui.ubs_monthly_portfolio_logic import MONTH_LABELS


class AppPortfolioStateMixin:
    """Estado inicial de las pantallas de portafolio y de los resumenes UBS."""

    def _init_portfolio_inputs(self, saved_general) -> None:
        """Entradas del portafolio UBS: tamano, capital y limites."""
        saved_portfolio_type = _display_portfolio_type(saved_general.get("ubs_portfolio_type", "Moderado"))
        self.ubs_portfolio_num_symbols = tk.IntVar(value=self._saved_int(saved_general.get("ubs_portfolio_num_symbols"), 5))
        self.ubs_portfolio_type = tk.StringVar(value=saved_portfolio_type)
        self.ubs_portfolio_valley_pct = tk.StringVar(value=saved_general.get("ubs_portfolio_valley_pct", "10"))
        self.ubs_portfolio_point_pct = tk.StringVar(value=saved_general.get("ubs_portfolio_point_pct", "4"))
        self.ubs_portfolio_capital = tk.StringVar(value=saved_general.get("ubs_portfolio_capital", "10000"))
        self.ubs_portfolio_top_k = tk.IntVar(value=self._saved_int(saved_general.get("ubs_portfolio_top_k"), 3))
        self.ubs_portfolio_max_candidates = tk.IntVar(
            value=self._saved_int(saved_general.get("ubs_portfolio_max_candidates"), 30)
        )
        self.ubs_portfolio_min_trades = tk.IntVar(
            value=self._saved_int(saved_general.get("ubs_portfolio_min_trades"), 100)
        )
        self.ubs_portfolio_max_units_per_set = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_units_per_set", "")
        )
        self.ubs_portfolio_max_total_units = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_total_units", "")
        )
        self.ubs_portfolio_max_units_per_symbol = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_units_per_symbol", "")
        )
        self.ubs_portfolio_max_sets_per_symbol = tk.IntVar(
            value=self._saved_int(saved_general.get("ubs_portfolio_max_sets_per_symbol"), 1)
        )
        self.ubs_portfolio_run_local_search = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_run_local_search"), True)
        )
        self.ubs_portfolio_deep_optimization = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_deep_optimization"), True)
        )
        self.ubs_portfolio_use_correlation = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_use_correlation"), True)
        )
        self.ubs_portfolio_require_3_positive_months_6m = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_require_3_positive_months_6m"), False)
        )
        self.ubs_portfolio_grid_off = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_grid_off"), False)
        )
        self.ubs_portfolio_exclude_used_sets = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_exclude_used_sets"), True)
        )


    def _init_portfolio_filters(self, saved_general) -> None:
        """Filtros por activo, margen y correlacion del portafolio."""
        self.ubs_portfolio_margin_profile = tk.StringVar(
            value=_display_margin_profile(
                saved_general.get("ubs_portfolio_margin_profile"),
                self.ubs_broker.get(),
            )
        )
        self.ubs_portfolio_max_margin_pct = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_margin_pct", "100")
        )
        self.ubs_portfolio_allow_forex = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_allow_forex"), True)
        )
        self.ubs_portfolio_allow_metals = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_allow_metals"), True)
        )
        self.ubs_portfolio_allow_indices = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get("ubs_portfolio_allow_indices"),
                self._bool_setting(saved_general.get("ubs_portfolio_allow_indices_energies"), True),
            )
        )
        self.ubs_portfolio_allow_energies = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get("ubs_portfolio_allow_energies"),
                self._bool_setting(saved_general.get("ubs_portfolio_allow_indices_energies"), True),
            )
        )
        self.ubs_portfolio_allow_crypto = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_allow_crypto"), True)
        )
        self.ubs_portfolio_allow_stocks = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_allow_stocks"), True)
        )
        self.ubs_portfolio_allow_bonds = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_allow_bonds"), True)
        )
        self.ubs_portfolio_allow_softs = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_portfolio_allow_softs"), True)
        )
        self.ubs_portfolio_dd_reserve_pct = tk.StringVar(
            value=saved_general.get("ubs_portfolio_dd_reserve_pct", "10")
        )
        self.ubs_portfolio_search_restarts = tk.IntVar(
            value=self._saved_int(saved_general.get("ubs_portfolio_search_restarts"), 4)
        )
        self.ubs_portfolio_max_pair_corr = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_pair_corr", "0.35")
        )
        self.ubs_portfolio_max_downside_corr = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_downside_corr", "0.25")
        )
        self.ubs_portfolio_max_dd_overlap = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_dd_overlap", "0.35")
        )
        self.ubs_portfolio_max_portfolio_corr = tk.StringVar(
            value=saved_general.get("ubs_portfolio_max_portfolio_corr", "0.50")
        )


    def _init_portfolio_state(self) -> None:
        """Metricas y estado en memoria del portafolio UBS."""
        self.ubs_portfolio_status = tk.StringVar(value="Sin portafolios generados todavia")
        self.ubs_portfolio_availability = tk.StringVar(value="Disponibilidad: sin datos")
        self.ubs_portfolio_metric_net = tk.StringVar(value="—")
        self.ubs_portfolio_metric_valley = tk.StringVar(value="—")
        self.ubs_portfolio_metric_point = tk.StringVar(value="—")
        self.ubs_portfolio_metric_count = tk.StringVar(value="—")
        self.ubs_portfolio_metric_lot = tk.StringVar(value="—")
        self.ubs_portfolio_metric_units = tk.StringVar(value="—")
        self.ubs_portfolio_running = False
        self.ubs_portfolio_buttons: list = []
        self.ubs_portfolio_member_paths: dict[str, dict[str, str]] = {}
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self.ubs_portfolio_pending_proposals = []


    def _init_monthly_portfolio_inputs(self, saved_general) -> None:
        """Entradas del portafolio mensual."""
        monthly_prefix = "ubs_monthly_portfolio_"
        self.ubs_monthly_portfolio_target_month = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}target_month", MONTH_LABELS[0])
        )
        self.ubs_monthly_portfolio_type = tk.StringVar(
            value=_display_portfolio_type(saved_general.get(f"{monthly_prefix}type", saved_portfolio_type))
        )
        self.ubs_monthly_portfolio_valley_pct = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}valley_pct", "10")
        )
        self.ubs_monthly_portfolio_point_pct = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}point_pct", "4")
        )
        self.ubs_monthly_portfolio_max_daily_dd = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_daily_dd", "150")
        )
        self.ubs_monthly_portfolio_daily_dd_full_history = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}daily_dd_full_history"), False)
        )
        self.ubs_monthly_portfolio_capital = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}capital", "10000")
        )
        self.ubs_monthly_portfolio_top_k = tk.IntVar(
            value=self._saved_int(saved_general.get(f"{monthly_prefix}top_k"), 3)
        )
        self.ubs_monthly_portfolio_max_candidates = tk.IntVar(
            value=self._saved_int(saved_general.get(f"{monthly_prefix}max_candidates"), 30)
        )
        self.ubs_monthly_portfolio_min_trades = tk.IntVar(
            value=self._saved_int(saved_general.get(f"{monthly_prefix}min_trades"), 15)
        )
        self.ubs_monthly_portfolio_max_units_per_set = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_units_per_set", "")
        )
        self.ubs_monthly_portfolio_max_total_units = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_total_units", "")
        )
        self.ubs_monthly_portfolio_max_units_per_symbol = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_units_per_symbol", "")
        )
        self.ubs_monthly_portfolio_max_sets_per_symbol = tk.IntVar(
            value=self._saved_int(saved_general.get(f"{monthly_prefix}max_sets_per_symbol"), 1)
        )
        self.ubs_monthly_portfolio_run_local_search = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}run_local_search"), True)
        )
        self.ubs_monthly_portfolio_use_correlation = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}use_correlation"), True)
        )
        self.ubs_monthly_portfolio_require_3_positive_months_6m = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}require_3_positive_months_6m"), False)
        )
        self.ubs_monthly_portfolio_grid_off = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}grid_off"), False)
        )


    def _init_monthly_portfolio_filters(self, saved_general) -> None:
        """Filtros por activo y validaciones del portafolio mensual."""
        monthly_prefix = "ubs_monthly_portfolio_"
        self.ubs_monthly_portfolio_allow_forex = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}allow_forex"), True)
        )
        self.ubs_monthly_portfolio_allow_metals = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}allow_metals"), True)
        )
        self.ubs_monthly_portfolio_allow_indices = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}allow_indices"),
                self._bool_setting(saved_general.get(f"{monthly_prefix}allow_indices_energies"), True),
            )
        )
        self.ubs_monthly_portfolio_allow_energies = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}allow_energies"),
                self._bool_setting(saved_general.get(f"{monthly_prefix}allow_indices_energies"), True),
            )
        )
        self.ubs_monthly_portfolio_allow_crypto = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}allow_crypto"), True)
        )
        self.ubs_monthly_portfolio_allow_stocks = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}allow_stocks"), True)
        )
        self.ubs_monthly_portfolio_allow_bonds = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}allow_bonds"), True)
        )
        self.ubs_monthly_portfolio_allow_softs = tk.BooleanVar(
            value=self._bool_setting(saved_general.get(f"{monthly_prefix}allow_softs"), True)
        )
        self.ubs_monthly_portfolio_exclude_monthly_used = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}exclude_monthly_used"),
                False,
            )
        )
        self.ubs_monthly_portfolio_corr_with_monthly_portfolios = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}corr_with_monthly_portfolios"),
                False,
            )
        )
        self.ubs_monthly_portfolio_strict_yearly_month_validation = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}strict_yearly_month_validation"),
                False,
            )
        )
        self.ubs_monthly_portfolio_deep_optimization = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}deep_optimization"),
                False,
            )
        )


    def _init_monthly_portfolio_margin(self, saved_general) -> None:
        """Margen y correlacion del portafolio mensual."""
        monthly_prefix = "ubs_monthly_portfolio_"
        self.ubs_monthly_portfolio_validate_roboforex_margin = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}validate_roboforex_margin"),
                True,
            )
        )
        self.ubs_monthly_portfolio_validate_ttp_margin = tk.BooleanVar(
            value=self._bool_setting(
                saved_general.get(f"{monthly_prefix}validate_ttp_margin"),
                False,
            )
        )
        self.ubs_monthly_portfolio_max_margin_pct = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_margin_pct", "100")
        )
        self.ubs_monthly_portfolio_dd_reserve_pct = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}dd_reserve_pct", "10")
        )
        self.ubs_monthly_portfolio_search_restarts = tk.IntVar(
            value=self._saved_int(saved_general.get(f"{monthly_prefix}search_restarts"), 4)
        )
        self.ubs_monthly_portfolio_max_pair_corr = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_pair_corr", "0.35")
        )
        self.ubs_monthly_portfolio_max_downside_corr = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_downside_corr", "0.25")
        )
        self.ubs_monthly_portfolio_max_dd_overlap = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_dd_overlap", "0.35")
        )
        self.ubs_monthly_portfolio_max_portfolio_corr = tk.StringVar(
            value=saved_general.get(f"{monthly_prefix}max_portfolio_corr", "0.50")
        )


    def _init_monthly_portfolio_state(self) -> None:
        """Metricas y estado en memoria del portafolio mensual."""
        self.ubs_monthly_portfolio_status = tk.StringVar(value="Selecciona un mes objetivo.")
        self.ubs_monthly_portfolio_availability = tk.StringVar(value="Disponibilidad: sin datos")
        self.ubs_monthly_portfolio_metric_net = tk.StringVar(value="—")
        self.ubs_monthly_portfolio_metric_valley = tk.StringVar(value="—")
        self.ubs_monthly_portfolio_metric_point = tk.StringVar(value="—")
        self.ubs_monthly_portfolio_metric_count = tk.StringVar(value="—")
        self.ubs_monthly_portfolio_metric_lot = tk.StringVar(value="—")
        self.ubs_monthly_portfolio_metric_units = tk.StringVar(value="—")
        self.ubs_monthly_portfolio_running = False
        self.ubs_monthly_portfolio_buttons: list = []
        self.ubs_monthly_portfolio_member_paths: dict[str, dict[str, str]] = {}
        self.ubs_monthly_portfolio_pending_result = None
        self.ubs_monthly_portfolio_pending_inputs = None


    def _init_screen_texts(self) -> None:
        """Resumenes y estados que muestran las pantallas UBS."""
        self.ubs_search_query = tk.StringVar(value="")
        self.ubs_search_status = tk.StringVar(value="Escribe parte del nombre de un set UBS.")
        self.ubs_audit_account = tk.StringVar(value=f"{self._ubs_broker()}/{self._ubs_account_type()}")
        self.ubs_audit_run_id = tk.StringVar(value="")
        self.ubs_audit_status = tk.StringVar(value="Selecciona cuenta/run y genera auditoria.")
        self.ubs_search_paths: dict[str, dict[str, str]] = {}
        self.ubs_audit_report_path = tk.StringVar(value="")
        self.ubs_results_summary = tk.StringVar(value="Sin resultados UBS")
        self.ubs_results_status = tk.StringVar(value="Memoria UBS no cargada")
        self.ubs_history_summary = tk.StringVar(value="Sin historico UBS")
        self.ubs_history_candidate_summary = tk.StringVar(value="Selecciona un run")
        self.ubs_seed_eval_summary = tk.StringVar(value="Semillas: sin evaluar")
        self.ubs_robust_summary = tk.StringVar(value="Robustez: sin evaluar")
        self.ubs_robust_status = tk.StringVar(value="Sin resultados de robustez")
        self.ubs_final_tick_summary = tk.StringVar(value="Final Tick: sin evaluar")
        self.ubs_final_tick_status = tk.StringVar(value="Sin resultados Final Tick")
        self.ubs_final_tick_6m_summary = tk.StringVar(value="Final Tick 6M: sin evaluar")
        self.ubs_final_tick_6m_status = tk.StringVar(value="Sin resultados Final Tick 6M")
        self.ubs_regression_summary = tk.StringVar(value="Regresiva: sin evaluar")
        self.ubs_regression_status = tk.StringVar(value="Sin resultados de la prueba regresiva")
        self.ubs_universe_summary = tk.StringVar(value="Sin universo UBS")
        self.ubs_timeframe_summary = tk.StringVar(value="Sin pesos de timeframe")
        self.ubs_universe_asset_search = tk.StringVar(value="")
        self.ubs_universe_tf_search = tk.StringVar(value="")
        self.ubs_compare_summary = tk.StringVar(value="Sin resultados UBS")
        self.ubs_compare_detail = tk.StringVar(value="Selecciona un resultado para comparar contra su seed.")
        self.ubs_compare_run_id = tk.StringVar(value="")
        self.ubs_results_run_id = tk.StringVar(value="")
        self.ubs_robust_run_id = tk.StringVar(value="")
        self.ubs_final_tick_run_id = tk.StringVar(value="")
        self.ubs_final_tick_6m_run_id = tk.StringVar(value="")
        self.ubs_regression_run_id = tk.StringVar(value="")
        self.ubs_seed_detail = tk.StringVar(value="Selecciona una semilla")
        self.ubs_seed_override_symbol = tk.StringVar(value="")
        self.ubs_weights_locked = tk.BooleanVar(value=False)
        self.ubs_params_file_label = tk.StringVar(value="Sin archivo cargado")
        self.ubs_params_desc_var = tk.StringVar(value="Selecciona un parámetro para ver su descripción")
        self.ubs_params_modified: bool = False
        self.ubs_params_data: list[dict] = []
        self.ubs_params_current_path: Path | None = None
        self.ubs_seed_override_period = tk.StringVar(value="")
        self.ubs_continue_status = tk.StringVar(value="Continuar: sin memoria UBS")


    def _init_dashboard_state(self) -> None:
        """Progreso, tarea activa y secciones del panel."""
        self.mode_text = tk.StringVar(value="Real")
        self.last_log_text = tk.StringVar(value="Sin log reciente")
        self.active_task_text = tk.StringVar(value="Sin tarea activa")
        self.active_task_detail = tk.StringVar(value="Pulsa una accion para empezar")
        self.engine_status_text = tk.StringVar(value="Engine Ready")
        self.progress_var = tk.DoubleVar(value=0)
        self._progress_total = 0
        self._progress_done = 0
        self._progress_target = 0.0
        self._progress_running = False
        self.portfolio_running = False
        self.portfolio_buttons: list[ttk.Button] = []
        self.nav_buttons: dict[str, tk.Button] = {}
        self.section_frames: dict[str, ttk.Frame] = {}


    def _init_table_state(self) -> None:
        """Rutas y marcados de las tablas de cada pantalla."""
        self.ubs_result_paths: dict[str, dict[str, str]] = {}
        self.ubs_result_checked: set[str] = set()
        self.ubs_history_run_checked: set[str] = set()
        self.ubs_history_candidate_paths: dict[str, dict[str, str]] = {}
        self.ubs_history_candidate_checked: set[str] = set()
        self.ubs_compare_paths: dict[str, dict[str, str]] = {}
        self.ubs_compare_checked: set[str] = set()
        self._ubs_compare_latest_seen_run_id = 0
        self._ubs_results_latest_seen_run_id = 0
        self._ubs_robust_latest_seen_run_id = 0
        self._ubs_final_tick_latest_seen_run_id = 0
        self.multiterminal_checked: set[str] = set()
        self.ubs_seed_paths: dict[str, dict[str, str]] = {}
        self.ubs_seed_checked: set[str] = set()
        self.ubs_robust_paths: dict[str, dict[str, str]] = {}
        self.ubs_robust_checked: set[str] = set()
        self.ubs_final_tick_paths: dict[str, dict[str, str]] = {}
        self.ubs_final_tick_checked: set[str] = set()
        self.ubs_final_tick_6m_paths: dict[str, dict[str, str]] = {}
        self.ubs_regression_paths: dict[str, dict[str, str]] = {}
        self.ubs_regression_checked: set[str] = set()
        self.ubs_universe_paths: dict[str, dict[str, str]] = {}
        self.ubs_universe_checked: set[str] = set()
        self.ubs_timeframe_checked: set[str] = set()
        self._tree_sort_reverse: dict[tuple[str, str], bool] = {}
        self.ubs_continue_button: RoundedButton | None = None
        self.current_section = "panel"
