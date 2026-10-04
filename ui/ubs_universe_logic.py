from __future__ import annotations

import sys
from pathlib import Path

from ubs.account import broker_asset_universe_path_with_fallback
from ubs.tester_diagnostics import trade_mode_snapshot_path
from ubs.universe import asset_rows_from_groups, load_asset_universe


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_universe_actions import UBSUniverseActionsMixin
from ui.ubs_universe_mt5 import UBSUniverseMT5Mixin
from ui.ubs_universe_repair import UBSUniverseRepairMixin
from ui.ubs_universe_risk_repair import UBSUniverseRiskRepairMixin
from ui.ubs_universe_stats import UBSUniverseStatsMixin
from ui.ubs_universe_symbols import UBSUniverseSymbolsMixin
from ui.ubs_universe_table import UBSUniverseTableMixin


class UBSUniverseLogicMixin(
    UBSUniverseSymbolsMixin,
    UBSUniverseMT5Mixin,
    UBSUniverseActionsMixin,
    UBSUniverseStatsMixin,
    UBSUniverseTableMixin,
    UBSUniverseRepairMixin,
    UBSUniverseRiskRepairMixin,
):
    def _ubs_trade_mode_snapshot_path(self) -> Path:
        return trade_mode_snapshot_path(BASE_DIR, self._ubs_broker(), self._ubs_account_type())

    def _refresh_ubs_universe_panel(self) -> None:
        for label, callback in (
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _load_ubs_asset_universe(self) -> tuple[list[tuple[str, str, list[str]]], dict[str, str]]:
        path = broker_asset_universe_path_with_fallback(BASE_DIR, self._ubs_broker())
        groups, aliases = load_asset_universe(path, include_disabled=True)
        return asset_rows_from_groups(groups, aliases), aliases
