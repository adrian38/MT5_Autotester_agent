import unittest
from pathlib import Path
import tempfile
import sqlite3

from ubs.account import (
    account_disabled_symbols_path,
    account_memory_path,
    account_output_dir,
    account_seed_dir,
    account_timeframe_universe_path,
    broker_asset_universe_path,
    broker_asset_universe_path_with_fallback,
    broker_disabled_symbols_path,
    load_account_timeframe_universe,
    migrate_legacy_account_storage,
    migrate_legacy_seed_paths_in_memory,
    normalize_account_type,
    normalize_broker,
)
from ubs.universe import load_disabled_symbols, load_seed_enabled_disabled_symbols
from ui.ubs_agent_logic import UBSAgentLogicMixin
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin


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


class UBSAccountTests(unittest.TestCase):
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

    def test_universe_policy_is_broker_scoped_and_timeframes_are_account_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            axi_assets = broker_asset_universe_path(base, "AXI")
            axi_assets.parent.mkdir(parents=True, exist_ok=True)
            axi_assets.write_text("[Forex]\nEURUSD=\n", encoding="utf-8")
            tf_path = account_timeframe_universe_path(base, "PRO")
            tf_path.parent.mkdir(parents=True, exist_ok=True)
            tf_path.write_text('{"timeframes": ["H1", "D1"]}', encoding="utf-8")

            self.assertEqual(account_disabled_symbols_path(base, "ECN"), broker_disabled_symbols_path(base, "ROBOFOREX"))
            self.assertEqual(account_disabled_symbols_path(base, "PRO"), broker_disabled_symbols_path(base, "ROBOFOREX"))
            self.assertNotEqual(account_timeframe_universe_path(base, "ECN"), account_timeframe_universe_path(base, "PRO"))
            self.assertEqual(load_account_timeframe_universe(base, "PRO"), ("H1", "D1"))
            self.assertEqual(load_account_timeframe_universe(base, "ECN"), ("M1", "M5", "M15", "M30", "H1", "H4", "D1"))
            self.assertEqual(broker_asset_universe_path_with_fallback(base, "AXI"), axi_assets)
            self.assertEqual(
                broker_asset_universe_path_with_fallback(base, "ICTRADING"),
                broker_asset_universe_path(base, "ROBOFOREX"),
            )

    def test_ubs_portfolio_sources_are_limited_to_active_broker(self) -> None:
        import ui.ubs_portfolio_logic as portfolio_logic
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

    def test_sync_switches_previous_account_defaults_to_active_account(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ROBOFOREX" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ROBOFOREX" / "ECN"),
        )

        agent._sync_ubs_account_paths()

        self.assertEqual(agent.set_files_root.get(), str(BASE_DIR / "sets" / "ubs_ready" / "ROBOFOREX" / "PRO"))
        self.assertEqual(agent.ubs_generation_output.get(), str(BASE_DIR / "outputs" / "ubs_agent" / "ROBOFOREX" / "PRO"))

    def test_sync_keeps_custom_paths(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        custom_source = str(BASE_DIR / "custom_sets")
        custom_output = str(BASE_DIR / "custom_output")
        agent = _FakeAgent("PRO", custom_source, custom_output)

        agent._sync_ubs_account_paths()

        self.assertEqual(agent.set_files_root.get(), custom_source)
        self.assertEqual(agent.ubs_generation_output.get(), custom_output)

    def test_force_sync_replaces_custom_paths(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "custom_sets"),
            str(BASE_DIR / "custom_output"),
        )

        agent._sync_ubs_account_paths(force=True)

        self.assertEqual(agent.set_files_root.get(), str(BASE_DIR / "sets" / "ubs_ready" / "ROBOFOREX" / "PRO"))
        self.assertEqual(agent.ubs_generation_output.get(), str(BASE_DIR / "outputs" / "ubs_agent" / "ROBOFOREX" / "PRO"))

    def test_maps_legacy_single_set_to_active_account(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
            str(BASE_DIR / "sets" / "ubs_ready" / "XAUUSD" / "H1" / "seed.set"),
        )

        mapped = agent._account_scoped_set_file_path(agent.ubs_set_file.get())

        self.assertEqual(
            mapped,
            BASE_DIR / "sets" / "ubs_ready" / "ROBOFOREX" / "PRO" / "XAUUSD" / "H1" / "seed.set",
        )

    def test_maps_previous_account_single_set_to_active_account(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN" / "XAUUSD" / "H1" / "seed.set"),
        )

        mapped = agent._account_scoped_set_file_path(agent.ubs_set_file.get())

        self.assertEqual(
            mapped,
            BASE_DIR / "sets" / "ubs_ready" / "ROBOFOREX" / "PRO" / "XAUUSD" / "H1" / "seed.set",
        )

    def test_force_sync_clears_missing_account_set_file(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN" / "XAUUSD" / "H1" / "missing.set"),
        )

        agent._sync_ubs_account_paths(force=True)

        self.assertEqual(agent.ubs_set_file.get(), "")

    def test_migrates_legacy_roboforex_storage_without_deleting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_memory = base / "outputs" / "ubs_memory_ECN.sqlite"
            legacy_disabled = base / "outputs" / "ubs_disabled_symbols_ECN.json"
            legacy_seed = base / "sets" / "ubs_ready" / "ECN" / "XAUUSD" / "seed.set"
            legacy_output = base / "outputs" / "ubs_agent" / "ECN" / "run_1" / "candidate.set"
            for path, text in (
                (legacy_memory, "sqlite"),
                (legacy_disabled, "{}"),
                (legacy_seed, "seed"),
                (legacy_output, "candidate"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

            copied = migrate_legacy_account_storage(base, "ECN")

            self.assertEqual(len(copied), 4)
            self.assertTrue(legacy_memory.exists())
            self.assertTrue((base / "outputs" / "ubs_memory_ROBOFOREX_ECN.sqlite").exists())
            self.assertTrue(account_disabled_symbols_path(base, "ECN").exists())
            self.assertTrue((account_seed_dir(base, "ECN") / "XAUUSD" / "seed.set").exists())
            self.assertTrue((account_output_dir(base, "ECN") / "run_1" / "candidate.set").exists())

    def test_migration_merges_legacy_account_symbol_policies_into_broker_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ecn_policy = base / "outputs" / "ubs_disabled_symbols_ECN.json"
            pro_policy = base / "outputs" / "ubs_disabled_symbols_PRO.json"
            ecn_policy.parent.mkdir(parents=True, exist_ok=True)
            ecn_policy.write_text(
                '{"disabled": ["XAUUSD"], "seed_enabled_when_disabled": ["XAUUSD"]}',
                encoding="utf-8",
            )
            pro_policy.write_text(
                '{"disabled": ["WTI"], "seed_enabled_when_disabled": []}',
                encoding="utf-8",
            )

            migrate_legacy_account_storage(base, "ECN")
            migrate_legacy_account_storage(base, "PRO")

            broker_policy = broker_disabled_symbols_path(base)
            self.assertEqual(load_disabled_symbols(broker_policy), {"WTI", "XAUUSD"})
            self.assertEqual(load_seed_enabled_disabled_symbols(broker_policy), {"XAUUSD"})

    def test_migration_does_not_overwrite_existing_new_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_memory = base / "outputs" / "ubs_memory_PRO.sqlite"
            new_memory = account_memory_path(base, "PRO")
            legacy_memory.parent.mkdir(parents=True, exist_ok=True)
            legacy_memory.write_text("old", encoding="utf-8")
            new_memory.parent.mkdir(parents=True, exist_ok=True)
            new_memory.write_text("new", encoding="utf-8")

            copied = migrate_legacy_account_storage(base, "PRO")

            self.assertNotIn("memory", "\n".join(copied))
            self.assertEqual(new_memory.read_text(encoding="utf-8"), "new")

    def test_migration_replaces_empty_new_sqlite_with_legacy_data_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_memory = base / "outputs" / "ubs_memory_ECN.sqlite"
            new_memory = account_memory_path(base, "ECN")
            legacy_memory.parent.mkdir(parents=True, exist_ok=True)
            new_memory.parent.mkdir(parents=True, exist_ok=True)
            for path, rows in ((legacy_memory, 3), (new_memory, 0)):
                conn = sqlite3.connect(path)
                try:
                    conn.execute("create table candidates (id integer primary key)")
                    for _ in range(rows):
                        conn.execute("insert into candidates default values")
                    conn.commit()
                finally:
                    conn.close()

            copied = migrate_legacy_account_storage(base, "ECN")

            self.assertIn("memory", "\n".join(copied))
            backups = list(new_memory.parent.glob(f"{new_memory.name}.pre_legacy_migration_*.bak"))
            self.assertEqual(len(backups), 1)
            conn = sqlite3.connect(new_memory)
            try:
                count = conn.execute("select count(*) from candidates").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 3)

    def test_migration_updates_legacy_seed_paths_inside_new_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            old_seed = base / "sets" / "ubs_ready" / "ECN" / "XAUUSD" / "H1" / "seed.set"
            new_seed = account_seed_dir(base, "ECN") / "XAUUSD" / "H1" / "seed.set"
            old_seed.parent.mkdir(parents=True, exist_ok=True)
            old_seed.write_text("seed", encoding="utf-8")
            new_seed.parent.mkdir(parents=True, exist_ok=True)
            new_seed.write_text("seed", encoding="utf-8")
            memory = account_memory_path(base, "ECN")
            memory.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(memory)
            try:
                conn.execute("create table seed_scores (seed_path text not null unique, status text)")
                conn.execute("create table seed_overrides (seed_path text primary key, symbol text, period text)")
                conn.execute("insert into seed_scores (seed_path, status) values (?, 'accepted')", (str(old_seed),))
                conn.execute("insert into seed_overrides (seed_path, symbol, period) values (?, 'XAUUSD', 'H1')", (str(old_seed),))
                conn.commit()
            finally:
                conn.close()

            changed = migrate_legacy_seed_paths_in_memory(base, "ECN")

            self.assertEqual(changed, 2)
            conn = sqlite3.connect(memory)
            try:
                seed_score_path = conn.execute("select seed_path from seed_scores").fetchone()[0]
                override_path = conn.execute("select seed_path from seed_overrides").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(seed_score_path, str(new_seed))
            self.assertEqual(override_path, str(new_seed))


if __name__ == "__main__":
    unittest.main()
