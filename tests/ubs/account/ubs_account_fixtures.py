"""Dobles de prueba compartidos por los tests de cuenta UBS."""
from __future__ import annotations

from pathlib import Path

from ubs.account import normalize_account_type, normalize_broker
from ui.multiterminal_logic import MultiterminalLogicMixin
from ui.ubs_agent_logic import UBSAgentLogicMixin
from ui.ubs_monthly_portfolio_logic import UBSMonthlyPortfolioLogicMixin
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin
from ui.ubs_search_logic import UBSSearchLogicMixin
from ui.ubs_universe_logic import UBSUniverseLogicMixin


class _FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _FakeAgent(UBSAgentLogicMixin):
    def __init__(self, account_type: str, source: str, output: str, set_file: str = "", broker: str = "ROBOFOREX") -> None:
        self.ubs_broker = _FakeVar(broker)
        self.ubs_account_type = _FakeVar(account_type)
        self.set_files_root = _FakeVar(source)
        self.ubs_generation_output = _FakeVar(output)
        self.ubs_set_file = _FakeVar(set_file)

    def _ubs_broker(self) -> str:
        return normalize_broker(self.ubs_broker.get())

    def _ubs_account_type(self) -> str:
        return normalize_account_type(self.ubs_account_type.get(), self._ubs_broker())


class _FakePortfolio(UBSPortfolioLogicMixin):
    def __init__(self, broker: str) -> None:
        self.ubs_broker = _FakeVar(broker)


class _FakeMultiterminal(MultiterminalLogicMixin):
    def __init__(self, broker: str) -> None:
        self.ubs_broker = _FakeVar(broker)
        self.multiterminal_profiles = [
            {"name": "Robo 1", "broker": "ROBOFOREX", "enabled": True},
            {"name": "Axi 1", "broker": "AXI", "enabled": True},
            {"name": "IC 1", "broker": "ICTRADING", "enabled": False},
            {"name": "Robo legacy", "enabled": True},
        ]


class _FakeSearch(UBSSearchLogicMixin):
    def __init__(self, broker: str) -> None:
        self.ubs_broker = _FakeVar(broker)
        self.ubs_account_type = _FakeVar("")

    def _ubs_broker(self) -> str:
        return normalize_broker(self.ubs_broker.get())

    def _ubs_account_type(self) -> str:
        return normalize_account_type(self.ubs_account_type.get(), self._ubs_broker())


class _FakeMonthlyPortfolio(UBSMonthlyPortfolioLogicMixin):
    def __init__(self, broker: str, margin_enabled: bool = True, ttp_enabled: bool = False) -> None:
        self.ubs_broker = _FakeVar(broker)
        self.ubs_monthly_portfolio_validate_roboforex_margin = _FakeVar(margin_enabled)
        self.ubs_monthly_portfolio_validate_ttp_margin = _FakeVar(ttp_enabled)

    def _ubs_broker(self) -> str:
        return normalize_broker(self.ubs_broker.get())


class _FakeUniverse(UBSUniverseLogicMixin):
    def __init__(self, broker: str = "ROBOFOREX") -> None:
        self.ubs_broker = _FakeVar(broker)
        self.ubs_universe_checked = {"US100"}
        self.disabled_symbols = set()
        self.seed_enabled = {"US100"}
        self.saved: tuple[set[str], set[str]] | None = None
        self.status_text = _FakeVar("")
        self.symbol_suffix_enabled = _FakeVar(False)
        self.symbol_suffix = _FakeVar("")
        self.symbol_futures_suffix = _FakeVar("")
        self.symbol_shares_suffix = _FakeVar("")
        self.symbol_map_enabled = _FakeVar(False)
        self.symbol_map = _FakeVar("")

    def _ubs_broker(self) -> str:
        return normalize_broker(self.ubs_broker.get())

    def _load_ubs_asset_universe(self):
        return [], {"US100": ".USTECHCASH"}

    def _load_disabled_ubs_symbols(self) -> set[str]:
        return set(self.disabled_symbols)

    def _load_seed_enabled_disabled_ubs_symbols(self) -> set[str]:
        return set(self.seed_enabled)

    def _save_disabled_ubs_symbols(self, symbols: set, seed_enabled_when_disabled: set | None = None) -> None:
        self.saved = (set(symbols), set(seed_enabled_when_disabled or set()))

    def _refresh_ubs_universe(self) -> None:
        pass


class _FakeTree:
    def exists(self, _iid: str) -> bool:
        return False

    def selection_set(self, _iid: str) -> None:
        raise AssertionError("selection_set should not be called for missing tree items")

    def focus(self, _iid: str) -> None:
        raise AssertionError("focus should not be called for missing tree items")


class _CollectingTree:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[object, ...]] = {}

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.rows)

    def delete(self, iid: str) -> None:
        self.rows.pop(iid, None)

    def insert(self, _parent: str, _index: str, *, values, tags=()) -> str:
        iid = f"row-{len(self.rows) + 1}"
        self.rows[iid] = tuple(values)
        return iid


class _RefreshUniverse(UBSUniverseLogicMixin):
    def __init__(self, memory_path: Path) -> None:
        self._memory_path = memory_path
        self.ubs_broker = _FakeVar("AXI")
        self.ubs_universe_checked: set[str] = set()
        self.ubs_timeframe_checked: set[str] = set()
        self.ubs_universe_paths: dict[str, dict[str, str]] = {}
        self.ubs_universe_assets_tree = _CollectingTree()
        self.ubs_timeframes_tree = _CollectingTree()
        self.ubs_universe_summary = _FakeVar("")
        self.ubs_timeframe_summary = _FakeVar("")
        self.symbol_suffix_enabled = _FakeVar(False)
        self.symbol_suffix = _FakeVar("")
        self.symbol_futures_suffix = _FakeVar("")
        self.symbol_shares_suffix = _FakeVar("")
        self.symbol_map_enabled = _FakeVar(False)
        self.symbol_map = _FakeVar("")

    def _ubs_broker(self) -> str:
        return "AXI"

    def _ubs_account_type(self) -> str:
        return "STANDARD"

    def _ubs_memory_path(self) -> Path:
        return self._memory_path

    def _load_ubs_asset_universe(self):
        return [("Stocks", "ACTIVE+", [])], {}

    def _load_disabled_ubs_symbols(self) -> set[str]:
        return set()

    def _load_seed_enabled_disabled_ubs_symbols(self) -> set[str]:
        return set()

    @staticmethod
    def _checkbox_text(checked: bool) -> str:
        return "[x]" if checked else "[ ]"

    @staticmethod
    def _format_ubs_number(value, decimals: int = 2) -> str:
        return "" if value is None else f"{float(value):.{decimals}f}"
