import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.ubs_account_fixtures import _FakeAgent
from ubs.account import (
    account_disabled_symbols_path,
    account_memory_path,
    account_output_dir,
    account_seed_dir,
    migrate_legacy_account_storage,
    migrate_legacy_seed_paths_in_memory,
)
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.universe import load_disabled_symbols, load_seed_enabled_disabled_symbols


class UBSAccountMigrationTests(unittest.TestCase):
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

    def test_migration_copies_legacy_account_symbol_policies_into_account_policy(self) -> None:
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

            ecn_new_policy = account_disabled_symbols_path(base, "ECN")
            pro_new_policy = account_disabled_symbols_path(base, "PRO")
            self.assertEqual(load_disabled_symbols(ecn_new_policy), {"XAUUSD"})
            self.assertEqual(load_seed_enabled_disabled_symbols(ecn_new_policy), {"XAUUSD"})
            self.assertEqual(load_disabled_symbols(pro_new_policy), {"WTI"})
            self.assertEqual(load_seed_enabled_disabled_symbols(pro_new_policy), set())

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

    def test_migration_does_not_rescan_existing_scoped_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_output = base / "outputs" / "ubs_agent" / "ECN" / "legacy_run" / "candidate.set"
            scoped_output = account_output_dir(base, "ECN")
            legacy_output.parent.mkdir(parents=True, exist_ok=True)
            legacy_output.write_text("legacy", encoding="utf-8")
            scoped_output.mkdir(parents=True, exist_ok=True)
            (scoped_output / "current_run").mkdir()

            copied = migrate_legacy_account_storage(base, "ECN")

            self.assertNotIn("outputs", "\n".join(copied))
            self.assertFalse((scoped_output / "legacy_run" / "candidate.set").exists())

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

    def test_migration_updates_seed_paths_after_workspace_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            new_seed = account_seed_dir(base, "ECN") / "BTCUSD" / "H1" / "seed.set"
            new_seed.parent.mkdir(parents=True, exist_ok=True)
            new_seed.write_text("seed", encoding="utf-8")
            old_seed = Path("C:/previous/MT5_Autotester_agent") / new_seed.relative_to(base)
            memory = account_memory_path(base, "ECN")
            memory.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(memory)
            try:
                conn.execute("create table seed_scores (seed_path text not null unique, status text)")
                conn.execute("create table seed_overrides (seed_path text primary key, symbol text, period text)")
                conn.execute("insert into seed_scores (seed_path, status) values (?, 'accepted')", (str(old_seed),))
                conn.execute("insert into seed_overrides (seed_path, symbol, period) values (?, 'BTCUSD', 'H1')", (str(old_seed),))
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
