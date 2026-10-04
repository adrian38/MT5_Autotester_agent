import unittest
from pathlib import Path
import tempfile

from ubs.memory import AgentMemory
from ubs.models import Seed, Variant

from ubs.account import (
    account_disabled_symbols_path,
    account_memory_path,
    account_output_dir,
    account_seed_dir,
    account_timeframe_universe_path,
    axi_cash_future_family_targets,
    broker_asset_universe_path,
    broker_asset_universe_path_with_fallback,
    default_symbol_map_for_broker,
    load_account_timeframe_universe,
    normalize_account_type,
    normalize_broker,
)
from tests.ubs_account_fixtures import (
    _CollectingTree,
    _FakeAgent,
    _FakeMonthlyPortfolio,
    _FakeMultiterminal,
    _FakePortfolio,
    _FakeSearch,
    _FakeTree,
    _FakeUniverse,
    _FakeVar,
    _RefreshUniverse,
)
from ubs.universe import load_disabled_symbols, load_seed_enabled_disabled_symbols
from ui.ubs_agent_logic import UBSAgentLogicMixin
from ui.multiterminal_logic import MultiterminalLogicMixin
from ui.ubs_search_logic import UBSSearchLogicMixin
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin
from ui.ubs_monthly_portfolio_logic import UBSMonthlyPortfolioLogicMixin
from ui.ubs_universe_logic import UBSUniverseLogicMixin



class UBSAccountTests(unittest.TestCase):
    def test_universe_refresh_does_not_render_memory_only_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_path = root / "memory.sqlite"
            memory = AgentMemory(memory_path)
            try:
                run_id = memory.create_run(root, root / "output", 1, 2, 1, False, True)
                seed = Seed(root / "seed.set", "ACTIVE+", "H1", "family", "1")
                memory.record_variant(
                    run_id,
                    1,
                    Variant(root / "active.set", seed, "ACTIVE+", "H1", (), (), "test"),
                )
                memory.record_variant(
                    run_id,
                    1,
                    Variant(root / "removed.set", seed, "REMOVED+", "H1", (), (), "test"),
                )
            finally:
                memory.close()

            universe = _RefreshUniverse(memory_path)
            universe._refresh_ubs_universe()

            rows = tuple(universe.ubs_universe_assets_tree.rows.values())
            self.assertEqual([row[4] for row in rows], ["ACTIVE+"])
            self.assertEqual([row[3] for row in rows], ["Stocks"])
            self.assertNotIn("Memoria", universe.ubs_universe_summary.get())
            self.assertIn("fuera de universo ignorados: 1", universe.ubs_universe_summary.get())

    def test_normalize_account_type_defaults_to_ecn(self) -> None:
        self.assertEqual(normalize_account_type("pro"), "PRO")
        self.assertEqual(normalize_account_type("ECN"), "ECN")
        self.assertEqual(normalize_account_type(""), "ECN")
        self.assertEqual(normalize_account_type("demo"), "ECN")
        self.assertEqual(normalize_account_type("premium", "AXI"), "PREMIUM")
        self.assertEqual(normalize_account_type("ECN", "AXI"), "STANDARD")
        self.assertEqual(normalize_account_type("", "ICTRADING"), "STANDARD")

    def test_account_paths_are_scoped_per_account(self) -> None:
        base = Path("project")

        self.assertEqual(account_memory_path(base, "PRO"), base / "outputs" / "ubs_memory_ROBOFOREX_PRO.sqlite")
        self.assertEqual(account_output_dir(base, "ECN"), base / "outputs" / "ubs_agent" / "ROBOFOREX" / "ECN")
        self.assertEqual(account_seed_dir(base, "PRO"), base / "sets" / "ubs_ready" / "ROBOFOREX" / "PRO")
        self.assertEqual(account_memory_path(base, "PREMIUM", "AXI"), base / "outputs" / "ubs_memory_AXI_PREMIUM.sqlite")

    def test_universe_policy_is_broker_scoped_and_timeframes_are_shared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            axi_assets = broker_asset_universe_path(base, "AXI")
            axi_assets.parent.mkdir(parents=True, exist_ok=True)
            axi_assets.write_text("[Forex]\nEURUSD=\n", encoding="utf-8")
            tf_path = account_timeframe_universe_path(base, "PRO")
            tf_path.parent.mkdir(parents=True, exist_ok=True)
            tf_path.write_text('{"timeframes": ["H1", "D1"]}', encoding="utf-8")

            self.assertEqual(
                account_disabled_symbols_path(base, "ECN"),
                base / "outputs" / "ubs_disabled_symbols_ROBOFOREX_ECN.json",
            )
            self.assertEqual(
                account_disabled_symbols_path(base, "PRO"),
                base / "outputs" / "ubs_disabled_symbols_ROBOFOREX_PRO.json",
            )
            self.assertEqual(account_timeframe_universe_path(base, "ECN"), account_timeframe_universe_path(base, "PRO"))
            self.assertEqual(account_timeframe_universe_path(base, "STANDARD", "AXI"), account_timeframe_universe_path(base, "PRO"))
            self.assertEqual(load_account_timeframe_universe(base, "PRO"), ("H1", "D1"))
            self.assertEqual(load_account_timeframe_universe(base, "ECN"), ("H1", "D1"))
            self.assertEqual(broker_asset_universe_path_with_fallback(base, "AXI"), axi_assets)
            self.assertEqual(
                broker_asset_universe_path_with_fallback(base, "ICTRADING"),
                broker_asset_universe_path(base, "ICTRADING"),
            )

    def test_symbol_map_defaults_are_broker_scoped(self) -> None:
        self.assertIn("CRUDEOIL=WTI", default_symbol_map_for_broker("ROBOFOREX"))
        self.assertIn("TSLA.NAS=TSLA", default_symbol_map_for_broker("ROBOFOREX"))
        self.assertIn("US100=USTEC", default_symbol_map_for_broker("ICTRADING"))
        self.assertIn("DAX=DE40", default_symbol_map_for_broker("ICTRADING"))
        self.assertIn("WTI=XTIUSD", default_symbol_map_for_broker("ICTRADING"))
        axi_map = default_symbol_map_for_broker("AXI")
        self.assertIn("US100=USTECH", axi_map)
        self.assertIn("USTEC=USTECH", axi_map)
        self.assertIn("NAS100=NAS100.fs", axi_map)
        self.assertIn("USOIL=USOIL", axi_map)
        self.assertIn("WTI=WTI.fs", axi_map)
        self.assertIn("UKOIL=UKOIL", axi_map)
        self.assertIn("BRENT=BRENT.fs", axi_map)
        self.assertIn("GOLD=XAUUSD", axi_map)
        self.assertNotIn(".sa", axi_map)

    def test_axi_cash_future_family_targets_are_filtered_to_loaded_universe(self) -> None:
        universe = ("GER40.sa", "DAX40.fs", "USOIL.sa", "WTI.fs", "BTCUSD.sa")

        self.assertEqual(axi_cash_future_family_targets("DE40", universe), ("GER40.sa", "DAX40.fs"))
        self.assertEqual(axi_cash_future_family_targets("GER40.sa", universe), ("GER40.sa", "DAX40.fs"))
        self.assertEqual(axi_cash_future_family_targets("CRUDEOIL", universe), ("USOIL.sa", "WTI.fs"))
        self.assertEqual(axi_cash_future_family_targets("BTCUSD", universe), ())

    def test_symbol_map_switch_keeps_values_per_broker(self) -> None:
        agent = _FakeAgent("ECN", "", "", broker="ROBOFOREX")
        agent.symbol_map = _FakeVar("XTIUSD=WTI")
        agent._ubs_symbol_maps_by_broker = {
            "ROBOFOREX": "XTIUSD=WTI",
            "AXI": "GER40=GER40.cash",
            "ICTRADING": "",
        }
        agent._ubs_symbol_map_active_broker = "ROBOFOREX"
        agent.ubs_broker.set("AXI")

        agent._sync_ubs_symbol_map_for_broker("AXI")

        self.assertEqual(agent._ubs_symbol_maps_by_broker["ROBOFOREX"], "XTIUSD=WTI")
        self.assertEqual(agent.symbol_map.get(), "GER40=GER40.cash")

    def test_symbol_map_switch_fills_empty_broker_default(self) -> None:
        agent = _FakeAgent("STANDARD", "", "", broker="ICTRADING")
        agent.symbol_map = _FakeVar("")
        agent._ubs_symbol_maps_by_broker = {
            "ROBOFOREX": "",
            "AXI": "",
            "ICTRADING": "",
        }
        agent._ubs_symbol_map_active_broker = "ICTRADING"

        agent._sync_ubs_symbol_map_for_broker("ICTRADING")

        self.assertIn("US100=USTEC", agent.symbol_map.get())
        self.assertIn("US100=USTEC", agent._ubs_symbol_maps_by_broker["ICTRADING"])

    def test_account_context_refresh_updates_multiterminal_tree(self) -> None:
        agent = _FakeAgent("STANDARD", "", "", broker="ICTRADING")
        agent.status_text = _FakeVar("")
        refreshed: list[str] = []

        def safe_refresh(label: str, callback) -> None:
            refreshed.append(label)
            callback()

        agent._write_ui_settings = lambda: None
        agent._safe_refresh = safe_refresh
        agent._refresh_multiterminal_tree = lambda: refreshed.append("multiterminal_tree")

        agent._refresh_ubs_account_context()

        self.assertIn("multiterminal", refreshed)
        self.assertIn("multiterminal_tree", refreshed)

    def test_ubs_portfolio_sources_are_limited_to_active_broker(self) -> None:
        import ui.ubs_portfolio_schema as portfolio_logic
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            account_memory_path(base, "ECN", "ROBOFOREX").parent.mkdir(parents=True, exist_ok=True)
            account_memory_path(base, "ECN", "ROBOFOREX").write_text("", encoding="utf-8")
            account_memory_path(base, "PRO", "ROBOFOREX").write_text("", encoding="utf-8")
            account_memory_path(base, "STANDARD", "AXI").write_text("", encoding="utf-8")

            with patch.object(portfolio_logic, "BASE_DIR", base):
                robo_sources = _FakePortfolio("ROBOFOREX")._ubs_portfolio_source_paths()
                axi_sources = _FakePortfolio("AXI")._ubs_portfolio_source_paths()

            self.assertEqual([label for label, _path in robo_sources], ["ROBOFOREX/ECN", "ROBOFOREX/PRO"])
            self.assertEqual([label for label, _path in axi_sources], ["AXI/STANDARD"])

    def test_multiterminal_visible_profiles_are_limited_to_active_broker(self) -> None:
        robo = _FakeMultiterminal("ROBOFOREX")
        axi = _FakeMultiterminal("AXI")

        self.assertEqual([profile["name"] for _index, profile in robo._broker_multiterminal_profile_items()], ["Robo 1", "Robo legacy"])
        self.assertEqual([profile["name"] for _index, profile in axi._broker_multiterminal_profile_items()], ["Axi 1"])

    def test_multiterminal_summary_counts_enabled_profiles_only(self) -> None:
        robo = _FakeMultiterminal("ROBOFOREX")
        robo.multiterminal_profiles.append({"name": "Robo disabled", "broker": "ROBOFOREX", "enabled": False})
        robo.multiterminal_workers = _FakeVar("1")
        robo.multiterminal_enabled = _FakeVar("1")
        robo.multiterminal_summary = _FakeVar("")

        robo._update_multiterminal_summary()

        self.assertEqual(robo.multiterminal_summary.get(), "ROBOFOREX: 2 perfiles / usando hasta 1 / on")
        self.assertIn("Terminales disponibles: 2", robo._multiterminal_execution_details())

    def test_multiterminal_workers_above_one_use_broker_profiles_regardless_enabled(self) -> None:
        ic = _FakeMultiterminal("ICTRADING")
        ic.multiterminal_profiles = [
            {"name": "IC 1", "broker": "ICTRADING", "enabled": True},
            {"name": "IC 2", "broker": "ICTRADING", "enabled": False},
            {"name": "IC 3", "broker": "ICTRADING", "enabled": False},
        ]
        ic.multiterminal_workers = _FakeVar("5")
        ic.multiterminal_enabled = _FakeVar("1")
        ic.multiterminal_summary = _FakeVar("")

        ic._update_multiterminal_summary()

        self.assertEqual([profile["name"] for profile in ic._active_multiterminal_profiles()], ["IC 1", "IC 2", "IC 3"])
        self.assertEqual(ic.multiterminal_summary.get(), "ICTRADING: 3 perfiles / usando hasta 3 / on")

    def test_multiterminal_select_ignores_filtered_tree_item(self) -> None:
        axi = _FakeMultiterminal("AXI")
        axi.multiterminal_tree = _FakeTree()
        axi.mt_selected_index = None
        axi.mt_profile_enabled = _FakeVar("")
        axi.mt_profile_portable = _FakeVar("")
        axi.mt_profile_broker = _FakeVar("")
        axi.mt_profile_name = _FakeVar("")
        axi.mt_profile_mt5_path = _FakeVar("")
        axi.mt_profile_data_dir = _FakeVar("")
        axi.mt_profile_experts_root = _FakeVar("")
        axi.mt_profile_ubs_ex5_file = _FakeVar("")

        axi._select_multiterminal_profile(0)

        self.assertEqual(axi.mt_selected_index, 0)

    def test_search_audit_contexts_are_limited_to_active_broker(self) -> None:
        robo = _FakeSearch("ROBOFOREX")
        axi = _FakeSearch("AXI")

        self.assertEqual(robo._ubs_active_broker_account_contexts(), (("ROBOFOREX", "ECN"), ("ROBOFOREX", "PRO")))
        self.assertEqual(axi._ubs_active_broker_account_contexts(), (("AXI", "STANDARD"), ("AXI", "PREMIUM")))
        self.assertEqual(robo._ubs_account_context_file_label("ROBOFOREX/ECN"), "ROBOFOREX_ECN")
        self.assertIsNone(axi._parse_ubs_account_context("ROBOFOREX/ECN"))
        self.assertEqual(axi._parse_ubs_account_context("PREMIUM"), ("AXI", "PREMIUM"))

    def test_search_audit_detects_report_broker_and_account_headers(self) -> None:
        search = _FakeSearch("ROBOFOREX")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            robo_report = base / "robo.htm"
            axi_report = base / "axi.htm"
            ic_report = base / "ic.htm"
            robo_report.write_text("<html>RoboForex-ECN (Build 5120)</html>", encoding="utf-8")
            axi_report.write_text("<html>AXI Premium (Build 5120)</html>", encoding="utf-8")
            ic_report.write_text("<html>ICTrading-Live (Build 5120)</html>", encoding="utf-8")

            self.assertEqual(
                search._detect_ubs_report_account_header(robo_report, "ROBOFOREX")[1:],
                ("ROBOFOREX", "ECN"),
            )
            self.assertEqual(
                search._detect_ubs_report_account_header(axi_report, "AXI")[1:],
                ("AXI", "PREMIUM"),
            )
            self.assertEqual(
                search._detect_ubs_report_account_header(ic_report, "ICTRADING")[1:],
                ("ICTRADING", "STANDARD"),
            )

    def test_monthly_margin_profile_defaults_to_active_broker_unless_ttp_is_selected(self) -> None:
        self.assertEqual(_FakeMonthlyPortfolio("ROBOFOREX")._monthly_margin_profile(), "roboforex")
        self.assertEqual(_FakeMonthlyPortfolio("AXI")._monthly_margin_profile(), "axi")
        self.assertEqual(_FakeMonthlyPortfolio("ICTRADING")._monthly_margin_profile(), "ictrading")
        self.assertEqual(_FakeMonthlyPortfolio("ROBOFOREX", margin_enabled=False)._monthly_margin_profile(), "roboforex")
        self.assertEqual(
            _FakeMonthlyPortfolio("ROBOFOREX", margin_enabled=False, ttp_enabled=True)
            ._monthly_margin_profile(),
            "ttp",
        )
        self.assertTrue(_FakeMonthlyPortfolio("ROBOFOREX")._monthly_roboforex_margin_enabled())
        self.assertTrue(_FakeMonthlyPortfolio("AXI")._monthly_roboforex_margin_enabled())
        self.assertTrue(_FakeMonthlyPortfolio("ICTRADING")._monthly_roboforex_margin_enabled())
        self.assertTrue(_FakeMonthlyPortfolio("ROBOFOREX", margin_enabled=False)._monthly_roboforex_margin_enabled())
        self.assertFalse(
            _FakeMonthlyPortfolio("ROBOFOREX", margin_enabled=False, ttp_enabled=True)
            ._monthly_roboforex_margin_enabled()
        )

    def test_symbol_suffix_args_can_send_only_axi_share_suffix(self) -> None:
        agent = _FakeAgent("STANDARD", "", "", broker="AXI")
        agent.symbol_suffix_enabled = _FakeVar(True)
        agent.symbol_suffix = _FakeVar("")
        agent.symbol_futures_suffix = _FakeVar("")
        agent.symbol_shares_suffix = _FakeVar("+")

        self.assertEqual(agent._effective_symbol_suffix_args(), ["--symbol-shares-suffix", "+"])

    def test_symbol_suffix_args_still_respect_disabled_toggle(self) -> None:
        agent = _FakeAgent("STANDARD", "", "", broker="AXI")
        agent.symbol_suffix_enabled = _FakeVar(False)
        agent.symbol_suffix = _FakeVar("")
        agent.symbol_futures_suffix = _FakeVar(".fs")
        agent.symbol_shares_suffix = _FakeVar("+")

        self.assertEqual(agent._effective_symbol_suffix_args(), [])

    def test_disabling_generation_clears_stale_seed_permission(self) -> None:
        universe = _FakeUniverse()

        universe._set_checked_universe_symbols_enabled(False)

        self.assertEqual(universe.saved, ({".USTECHCASH"}, set()))

    @staticmethod
    def _canonical_axi_symbol(universe, symbol: str, symbol_map, suffix_universe) -> str:
        return universe._canonical_ubs_symbol(
            symbol,
            {},
            symbol_map=symbol_map,
            suffix_universe=suffix_universe,
            symbol_suffix=".sa",
            futures_suffix=".fs",
            shares_suffix="+",
        )

    def test_axi_universe_resolves_memory_symbols_to_broker_symbols(self) -> None:
        universe = _FakeUniverse("AXI")
        universe.symbol_suffix_enabled = _FakeVar(True)
        universe.symbol_suffix = _FakeVar(".sa")
        universe.symbol_futures_suffix = _FakeVar(".fs")
        universe.symbol_shares_suffix = _FakeVar("+")
        symbol_map = universe._ubs_universe_symbol_map()
        suffix_universe = {
            "XAUUSD": ".sa",
            "US30": ".sa",
            "USOIL": ".sa",
            "USTECH": ".sa",
            "NAS100": ".fs",
        }

        expected = {
            "XAUUSD": "XAUUSD.SA",
            "USTEC": "USTECH.SA",
            "XTIUSD": "USOIL.SA",
            "NAS100": "NAS100.FS",
        }
        for source, target in expected.items():
            with self.subTest(source=source):
                self.assertEqual(
                    self._canonical_axi_symbol(
                        universe, source, symbol_map, suffix_universe,
                    ),
                    target,
                )

    def test_axi_universe_signal_aliases_match_broker_symbols(self) -> None:
        universe = _FakeUniverse("AXI")
        symbol_map = universe._ubs_universe_symbol_map()
        suffix_universe = {
            "XAUUSD": ".sa",
            "US30": ".sa",
            "USOIL": ".sa",
            "USTECH": ".sa",
            "NAS100": ".fs",
        }

        aliases = universe._ubs_universe_signal_aliases(
            {},
            symbol_map,
            suffix_universe,
            ".sa",
            ".fs",
            "+",
        )

        self.assertEqual(aliases["XAUUSD"], "XAUUSD.SA")
        self.assertEqual(aliases["US30"], "US30.SA")
        self.assertEqual(aliases["USTEC"], "USTECH.SA")
        self.assertEqual(aliases["US100"], "USTECH.SA")
        self.assertEqual(aliases["XTIUSD"], "USOIL.SA")
        self.assertEqual(aliases["NAS100"], "NAS100.FS")

    def test_axi_seed_row_can_feed_cash_and_future_symbols(self) -> None:
        universe = _FakeUniverse("AXI")
        universe.symbol_suffix_enabled = _FakeVar(True)
        universe.symbol_suffix = _FakeVar(".sa")
        universe.symbol_futures_suffix = _FakeVar(".fs")
        universe.symbol_shares_suffix = _FakeVar("+")
        symbol_map = universe._ubs_universe_symbol_map()
        suffix_universe = {
            "GER40": ".sa",
            "DAX40": ".fs",
            "USOIL": ".sa",
            "WTI": ".fs",
        }

        dax_symbols = universe._ubs_seed_row_canonical_symbols(
            {"symbol": "DE40", "metrics_json": '{"symbol": "GER40.sa"}'},
            ("GER40.sa", "DAX40.fs", "USOIL.sa", "WTI.fs"),
            {},
            symbol_map,
            suffix_universe,
            ".sa",
            ".fs",
            "+",
        )
        oil_symbols = universe._ubs_seed_row_canonical_symbols(
            {"symbol": "XTIUSD", "metrics_json": '{"symbol": "USOIL.sa"}'},
            ("GER40.sa", "DAX40.fs", "USOIL.sa", "WTI.fs"),
            {},
            symbol_map,
            suffix_universe,
            ".sa",
            ".fs",
            "+",
        )

        self.assertEqual(dax_symbols, ("GER40.SA", "DAX40.FS"))
