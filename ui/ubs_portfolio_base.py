"""Constantes y rutas del portafolio UBS."""
from __future__ import annotations

import sys
from pathlib import Path

from portfolio_manager.ubs_portfolio import PortfolioType


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


PORTFOLIO_TYPE_LABELS = {
    "Conservative": PortfolioType.CONSERVATIVE,
    "Balanced": PortfolioType.BALANCED,
    "Aggressive": PortfolioType.AGGRESSIVE,
    # Backward-compatible labels from the previous Spanish UI.
    "Conservador": PortfolioType.CONSERVATIVE,
    "Equilibrado": PortfolioType.BALANCED,
    "Moderado": PortfolioType.BALANCED,
    "Agresivo": PortfolioType.AGGRESSIVE,
}
PORTFOLIO_TYPE_DISPLAY = {
    PortfolioType.CONSERVATIVE.value: "Conservador",
    PortfolioType.BALANCED.value: "Moderado",
    PortfolioType.AGGRESSIVE.value: "Agresivo",
    "bundle": "A/M/C",
}
PORTFOLIO_BUNDLE_DISPLAY = "A/M/C"
PORTFOLIO_TYPE_BATCH_SPECS = (
    ("aggressive", "Agresivo", PortfolioType.AGGRESSIVE),
    ("balanced", "Moderado", PortfolioType.BALANCED),
    ("conservative", "Conservador", PortfolioType.CONSERVATIVE),
)
PORTFOLIO_ASSET_GROUP_FLAGS = (
    ("Forex", "allow_forex"),
    ("Metals", "allow_metals"),
    ("Indices", "allow_indices"),
    ("Energies", "allow_energies"),
    ("Crypto", "allow_crypto"),
    ("Stocks", "allow_stocks"),
    ("Bonds", "allow_bonds"),
    ("Softs", "allow_softs"),
)
PORTFOLIO_MARGIN_PROFILE_DISPLAY = {
    "roboforex": "ROBOFOREX",
    "axi": "AXI",
    "ictrading": "ICTRADING",
    "ttp": "TTP",
}

DEFAULT_PORTFOLIO_FORM = {
    "capital": "10000",
    "valley_dd_pct": "10",
    "point_dd_pct": "4",
    "portfolio_type": "Moderado",
    "top_k_per_symbol": 3,
    "max_total_candidates": 30,
    "min_trades_2020_2026": 100,
    "max_units_per_set": "",
    "max_total_units": "",
    "max_units_per_symbol": "",
    "max_sets_per_symbol": 1,
    "run_local_search": True,
    "deep_optimization": True,
    "use_correlation": True,
    "require_3_positive_months_6m": False,
    "exclude_used_sets": True,
    "dd_reserve_pct": "10",
    "search_restarts": 4,
    "max_pair_corr": "0.35",
    "max_downside_corr": "0.25",
    "max_dd_overlap": "0.35",
    "max_portfolio_corr": "0.50",
}
