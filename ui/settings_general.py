"""Las entradas de la seccion `General` de `ui_settings.ini`.

`settings_logic.py` se paso de las 600 lineas por una tabla que crece sola:
cada ajuste nuevo de la pantalla es una entrada mas en uno de estos siete
diccionarios. Lo que se queda alli es lo que **hace** algo -cargar y guardar la
plantilla, los dialogos de fichero, el `.env` y el resto de secciones-; aqui
solo esta la traduccion de cada `StringVar` a su clave del INI.

Es un mixin de `SettingsLogicMixin`, asi que `app_ui.py` no cambia de bases y
`_write_general_settings` sigue llamando a estos metodos por `self`.
"""
from __future__ import annotations

from ubs.account import DEFAULT_BROKER, default_symbol_map_for_broker


class SettingsGeneralMixin:
    def _general_agent_settings(self, symbol_maps) -> dict[str, str]:
        """Ajustes del agente: generacion, cuenta y umbrales base."""
        return {
        "recursive": "1" if self.recursive.get() else "0",
        "delay": str(self.delay.get()),
        "ubs_generation_count": str(self.ubs_generation_count.get()),
        "ubs_variants_per_seed": str(self.ubs_variants_per_seed.get()),
        "ubs_max_seeds": str(self.ubs_max_seeds.get()),
        "ubs_agent_execute": "1" if self.ubs_agent_execute.get() else "0",
        "ubs_generation_mode": self.ubs_generation_mode.get().strip().lower(),
        "ubs_force_unseeded_universe": "1" if self.ubs_generation_mode.get().strip().lower() == "discovery" else "0",
        "ubs_experimental_long_timeframes": "1" if self.ubs_experimental_long_timeframes.get() else "0",
        "ubs_long_tf_min_trades_w1": self.ubs_long_tf_min_trades_w1.get().strip(),
        "ubs_long_tf_min_trades_mn": self.ubs_long_tf_min_trades_mn.get().strip(),
        "ubs_broker": self.ubs_broker.get().strip().upper(),
        "ubs_account_type": self.ubs_account_type.get().strip().upper(),
        "ubs_pass_min_net_profit": self.ubs_pass_min_net_profit.get().strip(),
        "ubs_pass_min_profit_factor": self.ubs_pass_min_profit_factor.get().strip(),
        "ubs_pass_min_trades": str(self.ubs_pass_min_trades.get()),
        "ubs_pass_max_drawdown_pct": self.ubs_pass_max_drawdown_pct.get().strip(),
        "ubs_pass_min_recovery_factor": self.ubs_pass_min_recovery_factor.get().strip(),
        }

    def _general_stage_settings(self, symbol_maps) -> dict[str, str]:
        """Umbrales de semillas, robustez, Final Tick y regresiva."""
        return {
        "ubs_seed_pass_min_net_profit": self.ubs_seed_pass_min_net_profit.get().strip(),
        "ubs_seed_pass_min_profit_factor": self.ubs_seed_pass_min_profit_factor.get().strip(),
        "ubs_seed_pass_min_trades": str(self.ubs_seed_pass_min_trades.get()),
        "ubs_seed_pass_max_drawdown_pct": self.ubs_seed_pass_max_drawdown_pct.get().strip(),
        "ubs_seed_pass_min_recovery_factor": self.ubs_seed_pass_min_recovery_factor.get().strip(),
        "ubs_robust_pass_min_net_profit": self.ubs_robust_pass_min_net_profit.get().strip(),
        "ubs_robust_pass_min_profit_factor": self.ubs_robust_pass_min_profit_factor.get().strip(),
        "ubs_robust_pass_min_trades": str(self.ubs_robust_pass_min_trades.get()),
        "ubs_robust_pass_max_drawdown_pct": self.ubs_robust_pass_max_drawdown_pct.get().strip(),
        "ubs_robust_pass_min_recovery_factor": self.ubs_robust_pass_min_recovery_factor.get().strip(),
        "ubs_robust_min_net_retention": self.ubs_robust_min_net_retention.get().strip(),
        "ubs_robust_min_pf_edge_retention": self.ubs_robust_min_pf_edge_retention.get().strip(),
        "ubs_robust_min_recovery_retention": self.ubs_robust_min_recovery_retention.get().strip(),
        "ubs_robust_max_dd_inflation": self.ubs_robust_max_dd_inflation.get().strip(),
        "ubs_robust_positive_bonus": self.ubs_robust_positive_bonus.get().strip(),
        "ubs_robust_negative_bonus": self.ubs_robust_negative_bonus.get().strip(),
        "ubs_robust_auto": "1" if self.ubs_robust_auto.get() else "0",
        "ubs_final_tick_auto": "1" if self.ubs_final_tick_auto.get() else "0",
        "ubs_final_tick_6m_auto": "1" if self.ubs_final_tick_6m_auto.get() else "0",
        "ubs_regression_auto": "1" if self.ubs_regression_auto.get() else "0",
        "ubs_agent_from_date": self.ubs_agent_from_date.get().strip(),
        "ubs_agent_to_date": self.ubs_agent_to_date.get().strip(),
        "ubs_seed_from_date": self.ubs_seed_from_date.get().strip(),
        "ubs_seed_to_date": self.ubs_seed_to_date.get().strip(),
        "ubs_robust_from_date": self.ubs_robust_from_date.get().strip(),
        "ubs_robust_to_date": self.ubs_robust_to_date.get().strip(),
        "ubs_final_tick_from_date": self.ubs_final_tick_from_date.get().strip(),
        "ubs_final_tick_to_date": self.ubs_final_tick_to_date.get().strip(),
        "ubs_final_tick_ohlc_from_date": self.ubs_final_tick_ohlc_from_date.get().strip(),
        "ubs_final_tick_ohlc_to_date": self.ubs_final_tick_ohlc_to_date.get().strip(),
        "ubs_final_tick_6m_from_date": self.ubs_final_tick_6m_from_date.get().strip(),
        "ubs_final_tick_6m_to_date": self.ubs_final_tick_6m_to_date.get().strip(),
        "ubs_final_tick_6m_ohlc_from_date": self.ubs_final_tick_6m_ohlc_from_date.get().strip(),
        "ubs_final_tick_6m_ohlc_to_date": self.ubs_final_tick_6m_ohlc_to_date.get().strip(),
        "ubs_regression_from_date": self.ubs_regression_from_date.get().strip(),
        "ubs_regression_to_date": self.ubs_regression_to_date.get().strip(),
        "ubs_regression_min_net_profit": self.ubs_regression_min_net_profit.get().strip(),
        "ubs_regression_min_profit_factor": self.ubs_regression_min_profit_factor.get().strip(),
        "ubs_regression_min_trades": self.ubs_regression_min_trades.get().strip(),
        "ubs_regression_min_trades_w1": self.ubs_regression_min_trades_w1.get().strip(),
        "ubs_regression_min_trades_mn": self.ubs_regression_min_trades_mn.get().strip(),
        "ubs_regression_max_drawdown_pct": self.ubs_regression_max_drawdown_pct.get().strip(),
        "ubs_regression_min_recovery_factor": self.ubs_regression_min_recovery_factor.get().strip(),
        "ubs_regression_min_positive_month_ratio": self.ubs_regression_min_positive_month_ratio.get().strip(),
        "ubs_regression_min_pf_efficiency": self.ubs_regression_min_pf_efficiency.get().strip(),
        "ubs_regression_max_dd_ratio": self.ubs_regression_max_dd_ratio.get().strip(),
        "ubs_regression_positive_points": self.ubs_regression_positive_points.get().strip(),
        "ubs_regression_negative_points": self.ubs_regression_negative_points.get().strip(),
        **self._general_final_tick_settings(),
        }

    def _general_final_tick_settings(self) -> dict[str, str]:
        """Criterios de comparacion OHLC vs Real Tick."""
        return {
        "ubs_final_tick_min_history_quality": self.ubs_final_tick_min_history_quality.get().strip(),
        "ubs_final_tick_min_ohlc_trades": self.ubs_final_tick_min_ohlc_trades.get().strip(),
        "ubs_final_tick_min_trades_w1": self.ubs_final_tick_min_trades_w1.get().strip(),
        "ubs_final_tick_min_trades_mn": self.ubs_final_tick_min_trades_mn.get().strip(),
        "ubs_final_tick_max_net_delta_pct": self.ubs_final_tick_max_net_delta_pct.get().strip(),
        "ubs_final_tick_max_pf_delta_pct": self.ubs_final_tick_max_pf_delta_pct.get().strip(),
        "ubs_final_tick_max_dd_delta_pct": self.ubs_final_tick_max_dd_delta_pct.get().strip(),
        "ubs_final_tick_max_dd_gap_pp": self.ubs_final_tick_max_dd_gap_pp.get().strip(),
        "ubs_final_tick_max_trades_delta_pct": self.ubs_final_tick_max_trades_delta_pct.get().strip(),
        }

    def _general_symbol_settings(self, symbol_maps) -> dict[str, str]:
        """Sufijos, correspondencia de simbolos y avisos."""
        return {
        "symbol_suffix_enabled": "1" if self.symbol_suffix_enabled.get() else "0",
        "symbol_suffix": self.symbol_suffix.get().strip(),
        "symbol_futures_suffix": self.symbol_futures_suffix.get().strip(),
        "symbol_shares_suffix": self.symbol_shares_suffix.get().strip(),
        "symbol_map_enabled": "1" if self.symbol_map_enabled.get() else "0",
        "symbol_map": symbol_maps.get(DEFAULT_BROKER, default_symbol_map_for_broker(DEFAULT_BROKER)),
        "telegram_enabled": "1" if self.telegram_enabled.get() else "0",
        }

    def _general_portfolio_settings(self, symbol_maps) -> dict[str, str]:
        """Entradas del portafolio UBS normal."""
        return {
        "portfolio_threshold": self.portfolio_threshold.get().strip(),
        "ubs_portfolio_num_symbols": str(self.ubs_portfolio_num_symbols.get()),
        "ubs_portfolio_type": self.ubs_portfolio_type.get().strip(),
        "ubs_portfolio_valley_pct": self.ubs_portfolio_valley_pct.get().strip(),
        "ubs_portfolio_point_pct": self.ubs_portfolio_point_pct.get().strip(),
        "ubs_portfolio_capital": self.ubs_portfolio_capital.get().strip(),
        "ubs_portfolio_top_k": str(self.ubs_portfolio_top_k.get()),
        "ubs_portfolio_max_candidates": str(self.ubs_portfolio_max_candidates.get()),
        "ubs_portfolio_min_trades": str(self.ubs_portfolio_min_trades.get()),
        "ubs_portfolio_max_units_per_set": self.ubs_portfolio_max_units_per_set.get().strip(),
        "ubs_portfolio_max_total_units": self.ubs_portfolio_max_total_units.get().strip(),
        "ubs_portfolio_max_units_per_symbol": self.ubs_portfolio_max_units_per_symbol.get().strip(),
        "ubs_portfolio_max_sets_per_symbol": str(self.ubs_portfolio_max_sets_per_symbol.get()),
        "ubs_portfolio_run_local_search": "1" if self.ubs_portfolio_run_local_search.get() else "0",
        "ubs_portfolio_deep_optimization": "1" if self.ubs_portfolio_deep_optimization.get() else "0",
        "ubs_portfolio_use_correlation": "1" if self.ubs_portfolio_use_correlation.get() else "0",
        "ubs_portfolio_require_3_positive_months_6m": (
            "1" if self.ubs_portfolio_require_3_positive_months_6m.get() else "0"
        ),
        "ubs_portfolio_grid_off": "1" if self.ubs_portfolio_grid_off.get() else "0",
        "ubs_portfolio_exclude_used_sets": (
            "1" if self.ubs_portfolio_exclude_used_sets.get() else "0"
        ),
        "ubs_portfolio_margin_profile": self.ubs_portfolio_margin_profile.get().strip().upper(),
        "ubs_portfolio_max_margin_pct": self.ubs_portfolio_max_margin_pct.get().strip(),
        "ubs_portfolio_allow_forex": "1" if self.ubs_portfolio_allow_forex.get() else "0",
        "ubs_portfolio_allow_metals": "1" if self.ubs_portfolio_allow_metals.get() else "0",
        "ubs_portfolio_allow_indices": "1" if self.ubs_portfolio_allow_indices.get() else "0",
        "ubs_portfolio_allow_energies": "1" if self.ubs_portfolio_allow_energies.get() else "0",
        "ubs_portfolio_allow_crypto": "1" if self.ubs_portfolio_allow_crypto.get() else "0",
        "ubs_portfolio_allow_stocks": "1" if self.ubs_portfolio_allow_stocks.get() else "0",
        "ubs_portfolio_allow_bonds": "1" if self.ubs_portfolio_allow_bonds.get() else "0",
        "ubs_portfolio_allow_softs": "1" if self.ubs_portfolio_allow_softs.get() else "0",
        "ubs_portfolio_dd_reserve_pct": self.ubs_portfolio_dd_reserve_pct.get().strip(),
        "ubs_portfolio_search_restarts": str(self.ubs_portfolio_search_restarts.get()),
        "ubs_portfolio_max_pair_corr": self.ubs_portfolio_max_pair_corr.get().strip(),
        "ubs_portfolio_max_downside_corr": self.ubs_portfolio_max_downside_corr.get().strip(),
        "ubs_portfolio_max_dd_overlap": self.ubs_portfolio_max_dd_overlap.get().strip(),
        "ubs_portfolio_max_portfolio_corr": self.ubs_portfolio_max_portfolio_corr.get().strip(),
        }

    def _general_monthly_filters(self) -> dict[str, str]:
        """Filtros por activo, margen y correlacion del portafolio mensual."""
        return {
        "ubs_monthly_portfolio_allow_forex": (
            "1" if self.ubs_monthly_portfolio_allow_forex.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_metals": (
            "1" if self.ubs_monthly_portfolio_allow_metals.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_indices": (
            "1" if self.ubs_monthly_portfolio_allow_indices.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_energies": (
            "1" if self.ubs_monthly_portfolio_allow_energies.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_crypto": (
            "1" if self.ubs_monthly_portfolio_allow_crypto.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_stocks": (
            "1" if self.ubs_monthly_portfolio_allow_stocks.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_bonds": (
            "1" if self.ubs_monthly_portfolio_allow_bonds.get() else "0"
        ),
        "ubs_monthly_portfolio_allow_softs": (
            "1" if self.ubs_monthly_portfolio_allow_softs.get() else "0"
        ),
        "ubs_monthly_portfolio_exclude_monthly_used": (
            "1" if self.ubs_monthly_portfolio_exclude_monthly_used.get() else "0"
        ),
        "ubs_monthly_portfolio_corr_with_monthly_portfolios": (
            "1" if self.ubs_monthly_portfolio_corr_with_monthly_portfolios.get() else "0"
        ),
        "ubs_monthly_portfolio_strict_yearly_month_validation": (
            "1" if self.ubs_monthly_portfolio_strict_yearly_month_validation.get() else "0"
        ),
        "ubs_monthly_portfolio_deep_optimization": (
            "1" if self.ubs_monthly_portfolio_deep_optimization.get() else "0"
        ),
        "ubs_monthly_portfolio_validate_roboforex_margin": (
            "1" if self.ubs_monthly_portfolio_validate_roboforex_margin.get() else "0"
        ),
        "ubs_monthly_portfolio_validate_ttp_margin": (
            "1" if self.ubs_monthly_portfolio_validate_ttp_margin.get() else "0"
        ),
        "ubs_monthly_portfolio_max_margin_pct": self.ubs_monthly_portfolio_max_margin_pct.get().strip(),
        "ubs_monthly_portfolio_dd_reserve_pct": self.ubs_monthly_portfolio_dd_reserve_pct.get().strip(),
        "ubs_monthly_portfolio_search_restarts": str(self.ubs_monthly_portfolio_search_restarts.get()),
        "ubs_monthly_portfolio_max_pair_corr": self.ubs_monthly_portfolio_max_pair_corr.get().strip(),
        "ubs_monthly_portfolio_max_downside_corr": self.ubs_monthly_portfolio_max_downside_corr.get().strip(),
        "ubs_monthly_portfolio_max_dd_overlap": self.ubs_monthly_portfolio_max_dd_overlap.get().strip(),
        "ubs_monthly_portfolio_max_portfolio_corr": self.ubs_monthly_portfolio_max_portfolio_corr.get().strip(),
        "theme": self.theme_mode.get(),
        }

    def _general_monthly_settings(self, symbol_maps) -> dict[str, str]:
        """Entradas del portafolio UBS mensual y el tema."""
        return {
        "ubs_monthly_portfolio_target_month": self.ubs_monthly_portfolio_target_month.get().strip(),
        "ubs_monthly_portfolio_type": self.ubs_monthly_portfolio_type.get().strip(),
        "ubs_monthly_portfolio_valley_pct": self.ubs_monthly_portfolio_valley_pct.get().strip(),
        "ubs_monthly_portfolio_point_pct": self.ubs_monthly_portfolio_point_pct.get().strip(),
        "ubs_monthly_portfolio_max_daily_dd": self.ubs_monthly_portfolio_max_daily_dd.get().strip(),
        "ubs_monthly_portfolio_daily_dd_full_history": (
            "1" if self.ubs_monthly_portfolio_daily_dd_full_history.get() else "0"
        ),
        "ubs_monthly_portfolio_capital": self.ubs_monthly_portfolio_capital.get().strip(),
        "ubs_monthly_portfolio_top_k": str(self.ubs_monthly_portfolio_top_k.get()),
        "ubs_monthly_portfolio_max_candidates": str(self.ubs_monthly_portfolio_max_candidates.get()),
        "ubs_monthly_portfolio_min_trades": str(self.ubs_monthly_portfolio_min_trades.get()),
        "ubs_monthly_portfolio_max_units_per_set": self.ubs_monthly_portfolio_max_units_per_set.get().strip(),
        "ubs_monthly_portfolio_max_total_units": self.ubs_monthly_portfolio_max_total_units.get().strip(),
        "ubs_monthly_portfolio_max_units_per_symbol": self.ubs_monthly_portfolio_max_units_per_symbol.get().strip(),
        "ubs_monthly_portfolio_max_sets_per_symbol": str(self.ubs_monthly_portfolio_max_sets_per_symbol.get()),
        "ubs_monthly_portfolio_run_local_search": "1" if self.ubs_monthly_portfolio_run_local_search.get() else "0",
        "ubs_monthly_portfolio_use_correlation": "1" if self.ubs_monthly_portfolio_use_correlation.get() else "0",
        "ubs_monthly_portfolio_require_3_positive_months_6m": (
            "1" if self.ubs_monthly_portfolio_require_3_positive_months_6m.get() else "0"
        ),
        "ubs_monthly_portfolio_grid_off": "1" if self.ubs_monthly_portfolio_grid_off.get() else "0",
            **self._general_monthly_filters(),
        }
