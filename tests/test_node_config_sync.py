from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.sync_node_configuration import (
    _rewrite_for_clone,
    _safe_relative_path,
    _strategy_set_specs,
    build_manifest,
    parse_terminal_profiles,
    parse_universe,
)


class NodeConfigurationSyncTests(unittest.TestCase):
    def test_manifest_scopes_broker_and_account_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ui_settings.ini").write_text(
                "[Paths]\ntemplate_path=tester_template.ini\nubs_set_file=\n",
                encoding="utf-8",
            )
            context = {
                "node_id": "axi-1",
                "broker": "AXI",
                "account_type": "STANDARD",
                "display_name": "AXI",
            }
            specs = {
                spec.key: spec
                for spec in build_manifest(
                    root,
                    context,
                    {"project_dir": "F:\\source", "settings_file": "ui_settings.ini"},
                )
            }
            self.assertEqual(specs["asset_universe"].relative_path, "assets/axi_assets.ini")
            self.assertEqual(specs["asset_universe"].scope, "broker")
            self.assertEqual(
                specs["disabled_symbols"].relative_path,
                "outputs/ubs_disabled_symbols_AXI_STANDARD.json",
            )
            self.assertEqual(specs["disabled_symbols"].scope, "account")
            self.assertTrue(specs["manager_node"].encrypted)
            self.assertTrue(specs["environment"].encrypted)

    def test_manifest_maps_windows_project_paths_inside_linux_container(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ui_settings.ini").write_text(
                "[Paths]\ntemplate_path=F:\\source\\tester_template.ini\nubs_set_file=\n",
                encoding="utf-8",
            )
            context = {
                "node_id": "axi-1",
                "broker": "AXI",
                "account_type": "STANDARD",
                "display_name": "AXI",
            }
            specs = {
                spec.key: spec
                for spec in build_manifest(
                    root,
                    context,
                    {"project_dir": "F:\\source", "settings_file": "ui_settings.ini"},
                )
            }
            self.assertEqual(specs["tester_template"].source_path, root / "tester_template.ini")
            self.assertEqual(specs["tester_template"].relative_path, "tester_template.ini")

    def test_parses_terminal_addresses_and_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = root / "ui_settings.ini"
            settings.write_text(
                "[Terminal.2]\n"
                "enabled=true\nbroker=AXI\nname=Worker 2\n"
                "mt5_path=C:\\\\AXI\\\\terminal64.exe\n"
                "data_dir=D:\\\\AXI2\nexperts_root=D:\\\\AXI2\\\\MQL5\\\\Experts\n"
                "ubs_ex5_file=EA.ex5\nportable=false\n",
                encoding="utf-8",
            )
            profiles = parse_terminal_profiles(settings, "AXI")
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]["profile_index"], 2)
            self.assertEqual(profiles[0]["broker"], "AXI")
            self.assertIn("terminal64.exe", profiles[0]["mt5_path"])

            universe = root / "axi_assets.ini"
            universe.write_text(
                "[Forex]\nsymbols=EURUSD.sa,GBPUSD.sa\n\n[Metals]\nsymbols=XAUUSD.sa\n",
                encoding="utf-8",
            )
            self.assertEqual(
                parse_universe(universe),
                [("Forex", "EURUSD.sa", 1), ("Forex", "GBPUSD.sa", 2), ("Metals", "XAUUSD.sa", 1)],
            )

    def test_restore_rewrites_clone_root_and_node_identity(self) -> None:
        manager = json.dumps(
            {"node_id": "old", "project_dir": "F:\\old", "host": "0.0.0.0", "port": 8762}
        ).encode()
        target = "D:\\clone"
        rewritten = _rewrite_for_clone(
            "manager_node", manager, "F:\\old", target, "new", "127.0.0.1", 9000
        )
        data = json.loads(rewritten)
        self.assertEqual(data["node_id"], "new")
        self.assertEqual(data["project_dir"], target)
        self.assertEqual(data["host"], "127.0.0.1")
        self.assertEqual(data["port"], 9000)

        settings = b"[Paths]\ntemplate_path=F:\\old\\tester_template.ini\n"
        rewritten_settings = _rewrite_for_clone(
            "ui_settings", settings, "F:\\old", target, "", None, None
        ).decode()
        self.assertIn(target, rewritten_settings)
        self.assertNotIn("F:\\old", rewritten_settings)

    def test_strategy_sets_include_complete_workspace_regardless_of_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed = root / "sets" / "ubs_ready" / "AXI" / "STANDARD" / "seed.set"
            accepted = root / "outputs" / "ubs_agent" / "AXI" / "STANDARD" / "accepted.set"
            rejected = accepted.with_name("rejected.set")
            for path in (seed, accepted, rejected):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Lots=0.01||0.01||0.01||0.1||N\n", encoding="utf-8")
            memory = root / "outputs" / "ubs_memory_AXI_STANDARD.sqlite"
            conn = sqlite3.connect(memory)
            try:
                conn.execute("CREATE TABLE seed_scores(seed_path TEXT, active INTEGER)")
                conn.execute("CREATE TABLE candidates(set_path TEXT, status TEXT)")
                conn.execute("INSERT INTO seed_scores VALUES(?,1)", (str(seed),))
                conn.execute("INSERT INTO candidates VALUES(?,'accepted')", (str(accepted),))
                conn.execute("INSERT INTO candidates VALUES(?,'rejected')", (str(rejected),))
                conn.commit()
            finally:
                conn.close()
            context = {
                "node_id": "axi-1",
                "broker": "AXI",
                "account_type": "STANDARD",
                "display_name": "AXI",
            }
            specs = _strategy_set_specs(root, context, {"project_dir": str(root)})
            paths = {spec.source_path for spec in specs}
            self.assertEqual(paths, {seed.resolve(), accepted.resolve(), rejected.resolve()})

    def test_restore_target_rejects_escape_and_absolute_paths(self) -> None:
        self.assertEqual(_safe_relative_path("assets\\axi_assets.ini"), "assets/axi_assets.ini")
        for value in ("../secret", "assets/../../secret", "/absolute/secret", "C:\\secret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _safe_relative_path(value)


if __name__ == "__main__":
    unittest.main()
