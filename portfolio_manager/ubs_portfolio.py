"""UBS discrete DD-constrained portfolio builder.

This module is intentionally pure: no Tkinter and no SQLite. It receives
robustness-accepted strategy sets, merges their 2020-2024 and 2025-2026 reports
into one 2020-2026 curve, then allocates lots in integer 0.01-lot units. Every
possible increment is evaluated against the complete portfolio curve before it
can be accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
import math
from pathlib import Path
import random
import re
import unicodedata
from typing import Callable, Iterable, Sequence

from .mt5_report import StrategyReport, parse_report
from ubs.account import AXI_CASH_FUTURE_SYMBOL_FAMILIES
from ubs.path_utils import resolve_workspace_path
from ubs.universe import load_asset_universe


ProgressCallback = Callable[[str], None]


DEFAULT_BOOTSTRAP_SIMULATIONS = 1_000
DEFAULT_BOOTSTRAP_SEED = 20260624
BOOTSTRAP_METHOD = "circular_moving_block"


PORTFOLIO_SYMBOL_ALIASES = {
    "US30": ".US30CASH",
    ".US30CASH": ".US30CASH",
    "US500": ".US500CASH",
    ".US500CASH": ".US500CASH",
    "USTEC": ".USTECHCASH",
    "US100": ".USTECHCASH",
    "NAS100": ".USTECHCASH",
    ".USTECHCASH": ".USTECHCASH",
    "DAX": ".DE40CASH",
    "DE40": ".DE40CASH",
    "GER40": ".DE40CASH",
    ".DE40CASH": ".DE40CASH",
    "XTIUSD": "WTI",
    "USOIL": "WTI",
    "CRUDEOIL": "WTI",
    "WTI": "WTI",
}

# AXI lists the same underlying twice (cash `.sa` and future `.fs`). For
# per-symbol portfolio caps both legs must share one symbol key; only the
# suffixed AXI broker names are mapped so other brokers keep their keys.
AXI_FAMILY_KEY_BY_BROKER_SYMBOL = {
    str(target).strip().upper(): family_names[0]
    for family_names, targets in AXI_CASH_FUTURE_SYMBOL_FAMILIES
    for target in targets
}

PORTFOLIO_GROUP_BY_SYMBOL = {
    **{
        symbol: "Forex"
        for symbol in (
            "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "CHFJPY",
            "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "EURAUD", "EURCAD",
            "EURCHF", "EURGBP", "EURJPY", "EURNZD", "EURUSD", "NZDCAD", "NZDCHF", "NZDJPY",
            "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
        )
    },
    **{symbol: "Metals" for symbol in ("XAGUSD", "XAUUSD", "XAUEUR")},
    **{
        symbol: "Indices"
        for symbol in (".DE40CASH", ".JP225CASH", ".US500CASH", ".USTECHCASH", ".US30CASH")
    },
    **{symbol: "Energies" for symbol in ("BRENT", "WTI")},
    **{symbol: "Crypto" for symbol in ("BTCUSD", "ETHUSD")},
    **{
        symbol: "Stocks"
        for symbol in (
            "GOOGL", "MSFT", "IBM", "VZ", "INTC", "LLY", "HPE", "PFE", "JNJ", "EA", "BA",
            "ORCL", "NVDA", "CAT", "CSCO", "MMM", "ADBE", "GE", "TSLA", "NKE", "CMCSA",
            "GM", "DIS", "PM", "PG", "PEP", "FOXA", "KO", "AAPL", "AMZN", "UPS", "NFLX",
            "BRK.B", "MCD", "PRU", "SBUX", "PYPL", "GS", "WMT", "V", "DAL", "WFC", "C",
            "XOM", "CVX", "NEM", "JPM", "BAC", "EBAY", "META",
        )
    },
}

PORTFOLIO_UNIVERSE_FILES = (
    "assets/roboforex_assets.ini",
    "assets/axi_assets.ini",
    "assets/ictrading_assets.ini",
)


def _normalized_universe_group(group: str, symbol_key: str) -> str:
    if group == "Commodities":
        return "Softs"
    if group != "IndicesEnergies":
        return group
    energy_tokens = ("BRENT", "WTI", "OIL", "XTI", "XBR", "XNG", "GAS")
    return "Energies" if any(token in symbol_key for token in energy_tokens) else "Indices"


def _portfolio_universe_files_key(universe_files: Iterable[str | Path] | None = None) -> tuple[str, ...]:
    files = universe_files if universe_files is not None else PORTFOLIO_UNIVERSE_FILES
    return tuple(str(path) for path in files)


@lru_cache(maxsize=16)
def _portfolio_universe_group_maps_for_files(
    universe_files: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the portfolio classifier from the broker universe files."""
    canonical: dict[str, str] = {}
    exact: dict[str, str] = {}
    alias_rows: list[tuple[str, str]] = []
    for relative_path in universe_files:
        groups, aliases = load_asset_universe(
            resolve_workspace_path(relative_path),
            include_disabled=True,
        )
        for group, symbols in groups.items():
            for symbol in symbols:
                raw_key = str(symbol).strip().upper()
                symbol_key = portfolio_symbol_key(symbol)
                normalized_group = _normalized_universe_group(group, symbol_key)
                exact[raw_key] = normalized_group
                if group == "Stocks" and "." in raw_key:
                    canonical.setdefault(symbol_key, normalized_group)
                else:
                    canonical[symbol_key] = normalized_group
        alias_rows.extend(aliases.items())
    for alias, target in alias_rows:
        target_group = exact.get(str(target).strip().upper()) or canonical.get(portfolio_symbol_key(target))
        if target_group:
            exact[str(alias).strip().upper()] = target_group
            canonical[portfolio_symbol_key(alias)] = target_group
    return canonical, exact


def _portfolio_universe_group_maps(
    universe_files: Iterable[str | Path] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    return _portfolio_universe_group_maps_for_files(_portfolio_universe_files_key(universe_files))


def _portfolio_universe_group_by_symbol(
    universe_files: Iterable[str | Path] | None = None,
) -> dict[str, str]:
    return _portfolio_universe_group_maps(universe_files)[0]


class PortfolioType(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class PortfolioGroupLimits:
    max_units_pct: float | None
    max_sets: int | None
    bootstrap_units: int = 10


DEFAULT_GROUP_LIMITS = {
    PortfolioType.CONSERVATIVE: PortfolioGroupLimits(max_units_pct=0.40, max_sets=2, bootstrap_units=2),
    PortfolioType.BALANCED: PortfolioGroupLimits(max_units_pct=0.55, max_sets=3, bootstrap_units=2),
    PortfolioType.AGGRESSIVE: PortfolioGroupLimits(max_units_pct=0.70, max_sets=4, bootstrap_units=5),
}


@dataclass(frozen=True)
class ClosedTrade:
    open_time: datetime | None
    close_time: datetime
    symbol: str
    volume: float
    profit: float
    commission: float = 0.0
    swap: float = 0.0
    open_price: float | None = None
    close_price: float | None = None

    @property
    def net_profit(self) -> float:
        return self.profit + self.commission + self.swap


@dataclass
class PeriodReport:
    period_name: str
    start_year: int
    end_year: int
    symbol: str
    timeframe: str
    pnl_curve_001: list[float]
    net_profit_001: float
    valley_dd_001: float
    point_dd_001: float
    profit_factor: float
    return_dd_ratio: float
    trades: int
    gross_profit: float | None = None
    gross_loss: float | None = None
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    pnl_points_001: list[tuple[datetime, float]] = field(default_factory=list)
    source_path: str = ""
    start_date: str = ""
    end_date: str = ""


@dataclass
class RobustStrategySet:
    set_id: str
    candidate_id: str
    symbol: str
    timeframe: str | None
    strategy_family: str | None
    robustness_status: str
    already_used: bool
    report_2020_2024: PeriodReport
    report_2025_2026: PeriodReport
    curve_2020_2026_001: list[float]
    net_profit_2020_2026_001: float
    valley_dd_2020_2026_001: float
    point_dd_2020_2026_001: float
    profit_factor_2020_2026: float
    return_dd_2020_2026: float
    trades_2020_2026: int
    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    curve_points_2020_2026_001: list[tuple[datetime, float]] = field(default_factory=list)
    target_month: int | None = None
    month_years: tuple[int, ...] = ()
    positive_month_years: tuple[int, ...] = ()


@dataclass
class PortfolioEvaluation:
    allocations: dict[str, int]
    equity_curve_2020_2026: list[float]
    total_net_profit: float
    valley_dd: float
    point_dd: float
    target_valley_dd: float
    target_point_dd: float
    valley_usage_pct: float
    point_usage_pct: float
    total_units: int
    total_lot: float
    active_strategies: int
    daily_dd: float = 0.0
    target_daily_dd: float | None = None
    daily_usage_pct: float = 0.0
    daily_dd_full_history: bool = False
    enforce_point_dd: bool = True


@dataclass(frozen=True)
class BootstrapDrawdownAnalysis:
    method: str
    simulations: int
    seed: int
    observations: int
    block_size: int
    valley_dd_p50: float
    valley_dd_p95: float
    nominal_valley_dd_limit: float
    effective_valley_dd_limit: float
    probability_exceed_nominal_pct: float
    probability_exceed_effective_pct: float
    alert: bool


@dataclass
class StrategyAllocation:
    set_id: str
    candidate_id: str
    symbol: str
    units: int
    lot: float
    net_profit_contribution: float
    standalone_valley_dd: float
    standalone_point_dd: float
    timeframe: str | None = None
    set_path: str = ""
    is_report_path: str = ""
    oos_report_path: str = ""
    lot_size_step: float | None = None
    margin_required: float = 0.0
    margin_pct: float = 0.0
    margin_leverage: float = 0.0
    margin_contract_size: float = 0.0
    margin_price: float = 0.0
    max_balance_dd_001: float = 0.0
    max_equity_dd_001: float = 0.0
    floating_dd_source: str = ""
    standalone_floating_dd: float = 0.0
    recent_net_profit_001: float = 0.0
    recent_equity_dd_001: float = 0.0
    has_recent_performance: bool = False
    final_tick_report_path: str = ""
    full_history_report_path: str = ""


@dataclass
class OptimizationDecision:
    step: int
    action: str
    set_id: str | None
    from_set_id: str | None
    to_set_id: str | None
    gain: float
    valley_cost: float
    point_cost: float
    score: float
    portfolio_net_profit_after: float
    portfolio_valley_dd_after: float
    portfolio_point_dd_after: float
    reason: str


@dataclass
class UnusedSetInfo:
    set_id: str
    symbol: str
    score: float
    reason: str


@dataclass(frozen=True)
class CorrelationPair:
    set_id_a: str
    set_id_b: str
    symbol_a: str
    symbol_b: str
    pearson_corr: float
    downside_corr: float
    dd_overlap: float
    observations: int


@dataclass
class PortfolioResult:
    allocations: list[StrategyAllocation]
    equity_curve_2020_2026: list[float]
    total_net_profit: float
    actual_valley_dd: float
    actual_point_dd: float
    target_valley_dd: float
    target_point_dd: float
    valley_usage_pct: float
    point_usage_pct: float
    total_lot: float
    total_units: int
    active_strategies: int
    stop_reason: str
    warnings: list[str]
    decision_log: list[OptimizationDecision]
    unused_sets: list[UnusedSetInfo] = field(default_factory=list)
    correlation_rejections: int = 0
    group_summary: dict[str, dict[str, float | int]] = field(default_factory=dict)
    stress_bootstrap: BootstrapDrawdownAnalysis | None = None
    seasonal_coverage: dict[str, dict[str, object]] = field(default_factory=dict)
    seasonal_validation: dict[str, object] = field(default_factory=dict)
    margin_summary: dict[str, object] = field(default_factory=dict)
    max_daily_dd: float = 0.0
    target_daily_dd: float | None = None
    daily_dd_summary: dict[str, object] = field(default_factory=dict)
    daily_dd_full_history: bool = False
    enforce_point_dd: bool = True
    actual_closed_valley_dd: float = 0.0
    floating_dd_buffer: float = 0.0
    floating_overlap_audit: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioAvailability:
    robust_accepted: int
    already_used: int
    available: int
    symbols_available: int
    by_symbol: dict[str, int]


def _ascii_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_symbol(symbol: str) -> str:
    value = (symbol or "").strip()
    if value.startswith("."):
        return value.upper()
    return re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", value).upper()


def portfolio_symbol_key(symbol: str) -> str:
    family = AXI_FAMILY_KEY_BY_BROKER_SYMBOL.get(str(symbol or "").strip().upper())
    if family is not None:
        return PORTFOLIO_SYMBOL_ALIASES.get(family, family)
    normalized = _normalize_symbol(symbol)
    return PORTFOLIO_SYMBOL_ALIASES.get(normalized, normalized)


# Reexportados para no cambiar a los consumidores de `ubs_portfolio`.
from .ubs_portfolio_candidates import (  # noqa: F401
    _limit_candidates_with_group_reserve,
    filter_eligible_sets,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    recent_positive_month_count,
    select_top_k_per_symbol,
    summarize_robust_rows,
)
from .ubs_portfolio_constraints import (  # noqa: F401
    _allocations_respect_constraints,
    _candidate_group_count,
    _target_group_units_pct_allowed,
    can_add_unit,
    score_increment,
    violates_correlation_limits,
)
from .ubs_portfolio_curves import (  # noqa: F401
    _linear_percentile,
    bootstrap_valley_drawdown,
    build_correlation_pairs,
    calc_point_dd,
    calc_valley_dd,
    curve_increment_correlation,
    daily_pnl_series,
    merge_accumulated_curves,
    merge_incremental_curves,
    pearson_correlation,
    portfolio_daily_closed_floating_dd,
    strategy_correlation_pair,
    strategy_daily_closed_floating_dd,
    to_accumulated_curve,
)
from .ubs_portfolio_evaluate import (  # noqa: F401
    _evaluation_violates_dd_limits,
    _evaluation_violation_ratio,
    evaluate_portfolio,
    portfolio_group_summary,
)
from .ubs_portfolio_greedy import (  # noqa: F401
    build_portfolio_greedy,
)
from .ubs_portfolio_margin import (  # noqa: F401
    allocation_margin_required,
    allocations_respect_margin_limit,
    margin_contract_size_for_profile,
    margin_leverage_for_profile,
    margin_profile_label,
    normalize_margin_profile,
    portfolio_margin_summary,
    roboforex_contract_size,
    roboforex_margin_leverage,
    strategy_reference_price,
)
from .ubs_portfolio_monthly import (  # noqa: F401
    _limit_sorted_candidates_with_symbol_reserve,
    _strict_monthly_candidate_score,
    _strict_monthly_candidate_validation,
    _strict_monthly_candidate_variants,
    _strict_monthly_violation_score,
    _strict_validation_for_allocations,
)
from .ubs_portfolio_monthly_repair import (  # noqa: F401
    _repair_allocations_to_strict_monthly,
    _strict_monthly_deep_refine_allocations,
    _strict_monthly_safe_refill_allocations,
)
from .ubs_portfolio_optimize import (  # noqa: F401
    optimize_portfolio,
)
from .ubs_portfolio_optimize_monthly import (  # noqa: F401
    optimize_strict_monthly_portfolio,
)
from .ubs_portfolio_refine import (  # noqa: F401
    _deep_refine_allocations,
)
from .ubs_portfolio_reports import (  # noqa: F401
    build_equity_curve_from_closed_trades,
    build_robust_strategy_set,
    calc_combined_profit_factor,
    extract_period_info,
    filter_rows_grid_off,
    parse_mt5_html_report,
    period_report_from_strategy_report,
    set_file_has_enabled_grid,
    slice_strategy_set_to_month,
    slice_strategy_sets_to_month,
)
from .ubs_portfolio_search import (  # noqa: F401
    improve_with_local_search,
    improve_with_multi_start_search,
)
from .ubs_portfolio_strict import (  # noqa: F401
    validate_strict_monthly_portfolio,
)
from .ubs_portfolio_utils import (  # noqa: F401
    _active_unit_allocations,
    _build_unused_sets,
    _coerce_month_end,
    _curve_points_from_closed_trades,
    _evaluate_portfolio_on_time_axis,
    _execution_plan_allocations,
    _first_existing_report_path,
    _first_metric,
    _latest_month_from_monthly,
    _logical_stem,
    _looks_like_forex_pair,
    _lot_size_step,
    _merge_curve_points,
    _metric_amount,
    _month_window,
    _norm_path,
    _parse_report_date,
    _period_years,
    _portfolio_active_count,
    _portfolio_corr_allowed,
    _row_int,
    _row_value,
    _step_for_max_units,
    _to_float,
    _validate_curve_against_net,
    _validate_period_order,
    apply_portfolio_lot_text,
    execution_units_from_step,
    group_limits_for_portfolio_type,
    portfolio_display_symbol,
    portfolio_group_key,
    score_set_for_portfolio,
    set_current_value,
)
