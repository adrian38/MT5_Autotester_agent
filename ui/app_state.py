"""Estado inicial de la ventana principal, declarado por areas."""
from __future__ import annotations

import tkinter as tk

from compile_mq5 import find_metaeditor_path, load_compile_root
from mt5_env import env_value, metaeditor_path_from_env, terminal_path_from_env
from run_tests import REPORT_DIR, TEMPLATE_FILE, find_mt5_path, load_experts_root
from ubs.account import (
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    default_symbol_map_for_broker,
    migrate_legacy_roboforex_storage,
    normalize_account_type,
    normalize_broker,
    symbol_map_setting_key,
)
from ubs.regression_rules import (
    DEFAULT_REGRESSION_FROM_DATE,
    DEFAULT_REGRESSION_TO_DATE,
)
from ubs.weights import DEFAULT_ROBUST_NEGATIVE_BONUS, DEFAULT_ROBUST_POSITIVE_BONUS
from ui.app_base import BASE_DIR


class AppStateMixin:
    """Declaracion de las variables Tk que comparten todas las pantallas."""

    def _init_path_vars(self, saved_paths, saved_general) -> None:
        """Rutas de MT5, sets, plantillas y salidas guardadas."""
        default_ubs_ready = BASE_DIR / "sets" / "ubs_ready"
        self.mt5_path = tk.StringVar(value=saved_paths.get("mt5_path", str(terminal_path_from_env() or find_mt5_path(None))))
        self.mt5_data_root = tk.StringVar(value=saved_paths.get("mt5_data_root", ""))
        self.metaeditor_path = tk.StringVar(
            value=saved_paths.get("metaeditor_path", str(metaeditor_path_from_env() or find_metaeditor_path(None, None)))
        )
        self.compile_root = tk.StringVar(value=saved_paths.get("compile_root", str(load_compile_root() or "")))
        self.compile_file = tk.StringVar(value=saved_paths.get("compile_file", ""))
        self.experts_root = tk.StringVar(value=saved_paths.get("experts_root", str(load_experts_root() or "")))
        self.ubs_ex5_file = tk.StringVar(value=saved_paths.get("ubs_ex5_file", ""))
        self.set_files_root = tk.StringVar(
            value=saved_paths.get(
                "set_files_root",
                str(default_ubs_ready) if default_ubs_ready.exists() else "",
            )
        )
        self.ubs_set_file = tk.StringVar(value=saved_paths.get("ubs_set_file", ""))
        self.template_path = tk.StringVar(value=saved_paths.get("template_path", str(TEMPLATE_FILE)))
        self.ubs_generation_output = tk.StringVar(
            value=saved_paths.get("ubs_generation_output", str(BASE_DIR / "outputs" / "ubs_agent"))
        )
        self.portfolio_input = tk.StringVar(value=saved_paths.get("portfolio_input", str(REPORT_DIR)))
        self.portfolio_output = tk.StringVar(
            value=saved_paths.get("portfolio_output", str(BASE_DIR / "outputs" / "ALL_STRATEGIES.xlsx"))
        )
        self.portfolio_threshold = tk.StringVar(value=saved_general.get("portfolio_threshold", "50"))


    def _init_generation_vars(self, saved_general) -> None:
        """Parametros de la generacion y cuenta del agente."""
        self.recursive = tk.BooleanVar(value=saved_general.get("recursive", "0") in {"1", "true", "yes", "on"})
        self.delay = tk.IntVar(value=self._saved_int(saved_general.get("delay"), 5))
        self.ubs_generation_count = tk.IntVar(value=self._saved_int(saved_general.get("ubs_generation_count"), 1))
        self.ubs_variants_per_seed = tk.IntVar(value=self._saved_int(saved_general.get("ubs_variants_per_seed"), 3))
        self.ubs_max_seeds = tk.IntVar(value=self._saved_int(saved_general.get("ubs_max_seeds"), 50))
        self.ubs_agent_execute = tk.BooleanVar(value=saved_general.get("ubs_agent_execute", "0") in {"1", "true", "yes", "on"})
        self.ubs_force_unseeded_universe = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_force_unseeded_universe"), False)
        )
        saved_generation_mode = saved_general.get("ubs_generation_mode", "").strip().lower()
        if saved_generation_mode not in {"production", "discovery"}:
            saved_generation_mode = "discovery" if self.ubs_force_unseeded_universe.get() else "production"
        self.ubs_generation_mode = tk.StringVar(value=saved_generation_mode)
        self.ubs_experimental_long_timeframes = tk.BooleanVar(
            value=self._bool_setting(saved_general.get("ubs_experimental_long_timeframes"), False)
        )
        self.ubs_long_tf_min_trades_w1 = tk.StringVar(value=saved_general.get("ubs_long_tf_min_trades_w1", "12"))
        self.ubs_long_tf_min_trades_mn = tk.StringVar(value=saved_general.get("ubs_long_tf_min_trades_mn", "4"))
        self.ubs_broker = tk.StringVar(value=normalize_broker(saved_general.get("ubs_broker", DEFAULT_BROKER)))
        self.ubs_account_type = tk.StringVar(
            value=normalize_account_type(saved_general.get("ubs_account_type", DEFAULT_ACCOUNT_TYPE), self.ubs_broker.get())
        )
        self._startup_progress.advance("Migracion legacy")
        self._legacy_ubs_migrations = migrate_legacy_roboforex_storage(BASE_DIR)


    def _init_score_thresholds(self, saved_general) -> None:
        """Umbrales de aceptacion de candidatos y semillas."""
        self.ubs_pass_min_net_profit = tk.StringVar(value=saved_general.get("ubs_pass_min_net_profit", "100"))
        self.ubs_pass_min_profit_factor = tk.StringVar(value=saved_general.get("ubs_pass_min_profit_factor", "1.20"))
        self.ubs_pass_min_trades = tk.IntVar(value=self._saved_int(saved_general.get("ubs_pass_min_trades"), 50))
        self.ubs_pass_max_drawdown_pct = tk.StringVar(value=saved_general.get("ubs_pass_max_drawdown_pct", "25"))
        self.ubs_pass_min_recovery_factor = tk.StringVar(value=saved_general.get("ubs_pass_min_recovery_factor", "1.0"))
        self.ubs_seed_pass_min_net_profit = tk.StringVar(value=saved_general.get("ubs_seed_pass_min_net_profit", "0"))
        self.ubs_seed_pass_min_profit_factor = tk.StringVar(value=saved_general.get("ubs_seed_pass_min_profit_factor", "1.20"))
        self.ubs_seed_pass_min_trades = tk.IntVar(value=self._saved_int(saved_general.get("ubs_seed_pass_min_trades"), 50))
        self.ubs_seed_pass_max_drawdown_pct = tk.StringVar(value=saved_general.get("ubs_seed_pass_max_drawdown_pct", "25"))
        self.ubs_seed_pass_min_recovery_factor = tk.StringVar(value=saved_general.get("ubs_seed_pass_min_recovery_factor", "1.0"))


    def _init_robustness_thresholds(self, saved_general) -> None:
        """Umbrales y bonus de la robustez OOS."""
        self.ubs_robust_pass_min_net_profit = tk.StringVar(
            value=saved_general.get("ubs_robust_pass_min_net_profit", self.ubs_pass_min_net_profit.get())
        )
        self.ubs_robust_pass_min_profit_factor = tk.StringVar(
            value=saved_general.get("ubs_robust_pass_min_profit_factor", self.ubs_pass_min_profit_factor.get())
        )
        self.ubs_robust_pass_min_trades = tk.IntVar(
            value=self._saved_int(saved_general.get("ubs_robust_pass_min_trades"), self.ubs_pass_min_trades.get())
        )
        self.ubs_robust_pass_max_drawdown_pct = tk.StringVar(
            value=saved_general.get("ubs_robust_pass_max_drawdown_pct", self.ubs_pass_max_drawdown_pct.get())
        )
        self.ubs_robust_pass_min_recovery_factor = tk.StringVar(
            value=saved_general.get("ubs_robust_pass_min_recovery_factor", self.ubs_pass_min_recovery_factor.get())
        )
        self.ubs_robust_min_net_retention = tk.StringVar(
            value=saved_general.get("ubs_robust_min_net_retention", "0.50")
        )
        self.ubs_robust_min_pf_edge_retention = tk.StringVar(
            value=saved_general.get("ubs_robust_min_pf_edge_retention", "0.50")
        )
        self.ubs_robust_min_recovery_retention = tk.StringVar(
            value=saved_general.get("ubs_robust_min_recovery_retention", "0.50")
        )
        self.ubs_robust_max_dd_inflation = tk.StringVar(
            value=saved_general.get("ubs_robust_max_dd_inflation", "2.0")
        )
        saved_robust_positive_bonus = saved_general.get(
            "ubs_robust_positive_bonus", str(int(DEFAULT_ROBUST_POSITIVE_BONUS))
        )
        saved_robust_negative_bonus = saved_general.get(
            "ubs_robust_negative_bonus", str(int(DEFAULT_ROBUST_NEGATIVE_BONUS))
        )
        if saved_robust_positive_bonus.strip() == "30":
            saved_robust_positive_bonus = str(int(DEFAULT_ROBUST_POSITIVE_BONUS))
        if saved_robust_negative_bonus.strip() == "-30":
            saved_robust_negative_bonus = str(int(DEFAULT_ROBUST_NEGATIVE_BONUS))
        self.ubs_robust_positive_bonus = tk.StringVar(value=saved_robust_positive_bonus)
        self.ubs_robust_negative_bonus = tk.StringVar(value=saved_robust_negative_bonus)
        self.ubs_robust_auto = tk.BooleanVar(value=self._bool_setting(saved_general.get("ubs_robust_auto"), False))
        self.ubs_final_tick_auto = tk.BooleanVar(value=self._bool_setting(saved_general.get("ubs_final_tick_auto"), False))
        self.ubs_final_tick_6m_auto = tk.BooleanVar(value=self._bool_setting(saved_general.get("ubs_final_tick_6m_auto"), False))
        self.ubs_regression_auto = tk.BooleanVar(value=self._bool_setting(saved_general.get("ubs_regression_auto"), False))


    def _init_stage_dates(self, saved_general) -> None:
        """Fechas de cada etapa del pipeline."""
        self.ubs_agent_from_date = tk.StringVar(value=saved_general.get("ubs_agent_from_date", ""))
        self.ubs_agent_to_date = tk.StringVar(value=saved_general.get("ubs_agent_to_date", ""))
        self.ubs_seed_from_date = tk.StringVar(value=saved_general.get("ubs_seed_from_date", ""))
        self.ubs_seed_to_date = tk.StringVar(value=saved_general.get("ubs_seed_to_date", ""))
        self.ubs_robust_from_date = tk.StringVar(value=saved_general.get("ubs_robust_from_date", ""))
        self.ubs_robust_to_date = tk.StringVar(value=saved_general.get("ubs_robust_to_date", ""))
        self.ubs_final_tick_from_date = tk.StringVar(value=saved_general.get("ubs_final_tick_from_date", "2026.05.01"))
        self.ubs_final_tick_to_date = tk.StringVar(value=saved_general.get("ubs_final_tick_to_date", "2026.05.31"))
        self.ubs_final_tick_ohlc_from_date = tk.StringVar(value=saved_general.get("ubs_final_tick_ohlc_from_date", ""))
        self.ubs_final_tick_ohlc_to_date = tk.StringVar(value=saved_general.get("ubs_final_tick_ohlc_to_date", ""))
        self.ubs_final_tick_6m_from_date = tk.StringVar(value=saved_general.get("ubs_final_tick_6m_from_date", "2026.01.01"))
        self.ubs_final_tick_6m_to_date = tk.StringVar(value=saved_general.get("ubs_final_tick_6m_to_date", "2026.06.30"))
        self.ubs_final_tick_6m_ohlc_from_date = tk.StringVar(value=saved_general.get("ubs_final_tick_6m_ohlc_from_date", ""))
        self.ubs_final_tick_6m_ohlc_to_date = tk.StringVar(value=saved_general.get("ubs_final_tick_6m_ohlc_to_date", ""))
        self.ubs_regression_from_date = tk.StringVar(
            value=saved_general.get("ubs_regression_from_date", DEFAULT_REGRESSION_FROM_DATE)
        )
        self.ubs_regression_to_date = tk.StringVar(
            value=saved_general.get("ubs_regression_to_date", DEFAULT_REGRESSION_TO_DATE)
        )


    def _init_regression_thresholds(self, saved_general) -> None:
        """Umbrales y puntos de la regresiva OHLC."""
        self.ubs_regression_min_net_profit = tk.StringVar(value=saved_general.get("ubs_regression_min_net_profit", "0"))
        self.ubs_regression_min_profit_factor = tk.StringVar(value=saved_general.get("ubs_regression_min_profit_factor", "1.10"))
        self.ubs_regression_min_trades = tk.StringVar(value=saved_general.get("ubs_regression_min_trades", "36"))
        self.ubs_regression_min_trades_w1 = tk.StringVar(value=saved_general.get("ubs_regression_min_trades_w1", "12"))
        self.ubs_regression_min_trades_mn = tk.StringVar(value=saved_general.get("ubs_regression_min_trades_mn", "4"))
        self.ubs_regression_max_drawdown_pct = tk.StringVar(value=saved_general.get("ubs_regression_max_drawdown_pct", "30"))
        self.ubs_regression_min_recovery_factor = tk.StringVar(value=saved_general.get("ubs_regression_min_recovery_factor", "0.75"))
        self.ubs_regression_min_positive_month_ratio = tk.StringVar(
            value=saved_general.get("ubs_regression_min_positive_month_ratio", "0.50")
        )
        self.ubs_regression_min_pf_efficiency = tk.StringVar(
            value=saved_general.get("ubs_regression_min_pf_efficiency", "0.50")
        )
        self.ubs_regression_max_dd_ratio = tk.StringVar(
            value=saved_general.get("ubs_regression_max_dd_ratio", "2.0")
        )
        self.ubs_regression_positive_points = tk.StringVar(value=saved_general.get("ubs_regression_positive_points", "80"))
        self.ubs_regression_negative_points = tk.StringVar(value=saved_general.get("ubs_regression_negative_points", "-100"))


    def _init_final_tick_thresholds(self, saved_general) -> None:
        """Umbrales y desvios maximos del Final Tick."""
        self.ubs_final_tick_min_history_quality = tk.StringVar(
            value=saved_general.get("ubs_final_tick_min_history_quality", "80")
        )
        self.ubs_final_tick_min_ohlc_trades = tk.StringVar(
            value=saved_general.get("ubs_final_tick_min_ohlc_trades", "5")
        )
        self.ubs_final_tick_min_trades_w1 = tk.StringVar(
            value=saved_general.get("ubs_final_tick_min_trades_w1", "2")
        )
        self.ubs_final_tick_min_trades_mn = tk.StringVar(
            value=saved_general.get("ubs_final_tick_min_trades_mn", "1")
        )
        self.ubs_final_tick_max_net_delta_pct = tk.StringVar(
            value=saved_general.get("ubs_final_tick_max_net_delta_pct", "35")
        )
        self.ubs_final_tick_max_pf_delta_pct = tk.StringVar(
            value=saved_general.get("ubs_final_tick_max_pf_delta_pct", "35")
        )
        self.ubs_final_tick_max_dd_delta_pct = tk.StringVar(
            value=saved_general.get("ubs_final_tick_max_dd_delta_pct", "35")
        )
        self.ubs_final_tick_max_trades_delta_pct = tk.StringVar(
            value=saved_general.get("ubs_final_tick_max_trades_delta_pct", "35")
        )


    def _init_symbol_vars(self, saved_general) -> None:
        """Sufijos y mapa de simbolos por broker."""
        self.symbol_suffix_enabled = tk.BooleanVar(value=saved_general.get("symbol_suffix_enabled", "0") in {"1", "true", "yes", "on"})
        self.symbol_suffix = tk.StringVar(value=saved_general.get("symbol_suffix", ""))
        self.symbol_futures_suffix = tk.StringVar(value=saved_general.get("symbol_futures_suffix", ".fs"))
        self.symbol_shares_suffix = tk.StringVar(value=saved_general.get("symbol_shares_suffix", "+"))
        self.symbol_map_enabled = tk.BooleanVar(value=saved_general.get("symbol_map_enabled", "0") in {"1", "true", "yes", "on"})
        self._ubs_symbol_maps_by_broker = {
            broker: saved_general.get(symbol_map_setting_key(broker), default_symbol_map_for_broker(broker))
            for broker in BROKERS
        }
        if not saved_general.get(symbol_map_setting_key(DEFAULT_BROKER)):
            self._ubs_symbol_maps_by_broker[DEFAULT_BROKER] = saved_general.get(
                "symbol_map",
                default_symbol_map_for_broker(DEFAULT_BROKER),
            )
        self._ubs_symbol_map_active_broker = self.ubs_broker.get()
        self.symbol_map = tk.StringVar(value=self._ubs_symbol_maps_by_broker.get(
            self._ubs_symbol_map_active_broker,
            default_symbol_map_for_broker(self._ubs_symbol_map_active_broker),
        ))


    def _init_notification_vars(self, saved_general) -> None:
        """Credenciales y activacion de los avisos Telegram."""
        _tg_default = "1" if (env_value("TELEGRAM_BOT_TOKEN") and env_value("TELEGRAM_CHAT_ID")) else "0"
        self.telegram_enabled = tk.BooleanVar(value=self._bool_setting(saved_general.get("telegram_enabled", _tg_default)))
        self.telegram_bot_token = tk.StringVar(value=env_value("TELEGRAM_BOT_TOKEN") or "")
        self.telegram_chat_id = tk.StringVar(value=env_value("TELEGRAM_CHAT_ID") or "")


    def _init_multiterminal_vars(self, ui_settings, saved_multi) -> None:
        """Perfiles y estado del multiterminal."""
        self.multiterminal_enabled = tk.BooleanVar(value=self._bool_setting(saved_multi.get("enabled"), False))
        self.multiterminal_workers = tk.IntVar(value=max(1, self._saved_int(saved_multi.get("workers"), 1)))
        self.multiterminal_profiles = self._read_multiterminal_profiles(ui_settings)
        self.mt_selected_index: int | None = None
        self.mt_profile_enabled = tk.BooleanVar(value=True)
        self.mt_profile_portable = tk.BooleanVar(value=False)
        self.mt_profile_broker = tk.StringVar(value=self.ubs_broker.get())
        self.mt_profile_name = tk.StringVar(value="")
        self.mt_profile_mt5_path = tk.StringVar(value="")
        self.mt_profile_data_dir = tk.StringVar(value="")
        self.mt_profile_experts_root = tk.StringVar(value="")
        self.mt_profile_ubs_ex5_file = tk.StringVar(value="")
        self.multiterminal_summary = tk.StringVar(value="")


    def _init_status_vars(self) -> None:
        """Textos de estado y contadores de la cabecera."""
        self.tester_vars: dict[str, tk.StringVar] = {}
        self.status_text = tk.StringVar(value="Listo")
        self.running_text = tk.StringVar(value="Sin proceso activo")
        self.experts_count = tk.StringVar(value="0")
        self.reports_count = tk.StringVar(value="0")
        self.portfolio_count = tk.StringVar(value="Reports encontrados: 0")
        self.portfolio_status = tk.StringVar(value="Selecciona una carpeta de reportes y genera el Excel.")



