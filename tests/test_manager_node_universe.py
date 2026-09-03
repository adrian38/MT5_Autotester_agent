from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from manager_node_runtime.node import JobController, NodeServer
from manager_node_runtime.universe_service import build_history_command
from ubs.mt5_symbol_extract import ExtractedSymbol, SymbolExtractionResult
from ubs.tester_diagnostics import save_trade_mode_snapshot
from ubs.universe import load_disabled_symbols, load_seed_enabled_disabled_symbols, save_disabled_symbols


class ManagerNodeUniverseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "outputs").mkdir()
        self.assets = self.root / "assets/ictrading_assets.ini"
        self.assets.write_text("[Forex]\nsymbols=EURUSD,OLD,OLDSEED,GBPUSD\n[CommonAliases]\nOLD.A=OLD\n", encoding="utf-8")
        self.policy = self.root / "outputs/ubs_disabled_symbols_ICTRADING_STANDARD.json"
        save_disabled_symbols(self.policy, {"OLDSEED"}, {"OLDSEED"})
        self.memory = self.root / "outputs/ubs_memory_ICTRADING_STANDARD.sqlite"
        with closing(sqlite3.connect(self.memory)) as conn:
            conn.execute("create table candidates (id integer primary key, target_symbol text, status text, policy text)")
        (self.root / "ui_settings.ini").write_text(
            f"[Paths]\nset_files_root={self.root / 'sets'}\nubs_generation_output={self.root / 'outputs/agent'}\n"
            "ubs_ex5_file=expert.ex5\n[General]\nubs_broker=ICTRADING\nubs_account_type=STANDARD\n"
            "ubs_agent_from_date=2024.02.29\n[Multiterminal]\nenabled=0\n", encoding="utf-8")
        # Actual child process, but only a harmless stub, never MT5.
        from manager_node_runtime.node import VALUE_OPTIONS
        flags = sorted(VALUE_OPTIONS | {"--probe-universe-history", "--probe-history-timeframe", "--execute-backtests"})
        (self.root / "ubs_agent.py").write_text(
            f"FLAGS = {flags!r}\nimport sys\nassert '--probe-universe-history' in sys.argv\n"
            "print('Todos los backtests han terminado', flush=True)\n", encoding="utf-8")
        self.config = {"project_dir": str(self.root), "node_id": "ic", "broker": "ICTRADING", "token": "test-only",
                       "account_type": "STANDARD", "memory_path": str(self.memory)}
        self.controller = JobController(self.config, self.root / "manager_node.json")
        self.service = self.controller._universe_service()

    def verdict(self, symbol, status, policy="history_probe"):
        with closing(sqlite3.connect(self.memory)) as conn:
            conn.execute("insert into candidates(target_symbol,status,policy) values(?,?,?)", (symbol, status, policy))
            conn.commit()

    def test_sync_uses_saved_session_and_retires_symbols_with_backups(self):
        extraction = SymbolExtractionResult(
            (ExtractedSymbol("EURUSD", trade_mode=4), ExtractedSymbol("NEW", trade_mode=3)),
            None,
            None,
            "",
        )
        with patch("manager_node_runtime.universe_service.extract_symbols_from_mt5", return_value=extraction) as extract:
            result = self.controller.universe_action("sync", {})
        extract.assert_called_once_with(terminal_path=None, login=None, password="", server="")
        self.assertEqual((result["total"], result["added"], result["removed"], result["newly_disabled"]), (2, 1, 3, 2))
        self.assertEqual(load_disabled_symbols(self.policy), {"OLD", "OLDSEED", "GBPUSD"})
        self.assertEqual(load_seed_enabled_disabled_symbols(self.policy), set())
        self.assertTrue(Path(result["universe_backup"]).is_file())
        self.assertTrue(Path(result["policy_backup"]).is_file())
        self.assertEqual(result["trade_blocked"], 1)
        self.assertTrue(Path(result["trade_mode_snapshot"]).is_file())
        self.assertEqual(self.service.trade_disabled_preview()["symbols"], ["NEW"])
        self.assertIsNone(self.controller.process)
        self.assertNotIn("password", json.dumps(self.controller.state))

    def test_empty_extraction_and_corrupt_policy_do_not_rewrite_universe(self):
        original = self.assets.read_bytes()
        with patch("manager_node_runtime.universe_service.extract_symbols_from_mt5",
                   return_value=SymbolExtractionResult((), None, None, "")):
            with self.assertRaisesRegex(ValueError, "vacio"):
                self.service.sync({})
        self.assertEqual(self.assets.read_bytes(), original)
        self.policy.write_text("invalid", encoding="utf-8")
        with patch("manager_node_runtime.universe_service.extract_symbols_from_mt5") as extract:
            with self.assertRaises(ValueError):
                self.service.sync({})
            extract.assert_not_called()

    def test_credentials_are_not_recorded_even_when_mt5_fails(self):
        with patch("manager_node_runtime.universe_service.extract_symbols_from_mt5", side_effect=RuntimeError("secret123")):
            with self.assertRaisesRegex(ValueError, "REDACTED") as caught:
                self.controller.universe_action("sync", {"password": "secret123"})
        self.assertNotIn("secret123", str(caught.exception))
        self.assertNotIn("secret123", json.dumps(self.controller.state))
        self.assertFalse(self.controller.universe_operation_running)

    def test_history_uses_last_probe_verdict_and_only_enabled_symbols(self):
        self.verdict("EURUSD", "no_history")
        self.verdict("EURUSD", "history_ok")
        self.verdict("OLD.A", "no_history")
        self.verdict("GBPUSD", "no_history", policy="generation")
        preview = self.service.history_preview()
        self.assertEqual(preview["pending"], 1)
        self.assertEqual(preview["from_date"], "2024.02.29")
        self.assertEqual(preview["to_date"], "2025.02.28")
        self.assertEqual(self.service.disable_preview()["symbols"], ["OLD"])

    def test_disable_never_expands_confirmed_set_and_rechecks_latest_verdict(self):
        self.verdict("OLD.A", "no_history")
        approved = self.service.disable_preview()["symbols"]
        self.verdict("GBPUSD", "no_history")
        result = self.service.disable({"symbols": approved})
        self.assertEqual(result["newly_disabled"], 1)
        self.assertNotIn("GBPUSD", load_disabled_symbols(self.policy))
        self.verdict("GBPUSD", "history_ok")
        self.assertEqual(self.service.disable({"symbols": ["GBPUSD"]})["newly_disabled"], 0)

    def test_trade_disabled_preview_refreshes_mt5_and_safe_confirmation_does_not_expand(self):
        self.verdict("OLD", "trade_disabled")
        self.assertEqual(self.service.trade_disabled_preview()["symbols"], [])

        extraction = SymbolExtractionResult(
            (
                ExtractedSymbol("GBPUSD", trade_mode=3),
                ExtractedSymbol("EURUSD", trade_mode=4),
            ),
            None,
            11637157,
            "Broker-MT5",
        )
        with patch("manager_node_runtime.universe_service.extract_symbols_from_mt5", return_value=extraction) as extract:
            preview = self.service.trade_disabled_preview({})

        extract.assert_called_once_with(terminal_path=None, login=None, password="", server="")
        self.assertEqual(preview["symbols"], ["GBPUSD"])
        self.assertEqual(preview["terminal_total"], 1)
        self.assertNotIn("journal_total", preview)
        save_trade_mode_snapshot(
            self.service.trade_modes,
            (
                ExtractedSymbol("GBPUSD", trade_mode=3),
                ExtractedSymbol("EURUSD", trade_mode=0),
            ),
            account_login=11637157,
            server="Broker-MT5",
            terminal_path=None,
        )
        result = self.service.disable_trade_disabled({"symbols": preview["symbols"]})

        self.assertEqual(result["newly_disabled"], 1)
        self.assertIn("GBPUSD", load_disabled_symbols(self.policy))
        self.assertNotIn("EURUSD", load_disabled_symbols(self.policy))

    def test_busy_and_paused_nodes_do_not_mutate_or_start_probe(self):
        for busy in ("process", "queue", "paused", "ui", "audit"):
            with self.subTest(busy=busy):
                self.controller.process = object() if busy == "process" else None
                self.controller.queue = [{}] if busy == "queue" else []
                self.controller.state.update(status="paused" if busy == "paused" else "idle",
                    pipeline=[{"action": "generation"}], current_step_index=0, log_path="old.log")
                self.controller.ui_busy = lambda: busy == "ui"
                with patch.object(self.controller.live_audits, "is_running", return_value=busy == "audit"):
                    with self.assertRaises(RuntimeError):
                        self.controller.universe_action("sync", {})
                    with self.assertRaises(RuntimeError):
                        self.controller.start_universe_history()

    def test_probe_command_has_scoped_memory_dates_and_h1(self):
        command, cwd = build_history_command(self.config, self.service.history_dates())
        self.assertEqual(cwd, self.root)
        self.assertIn("--probe-universe-history", command)
        for flag, expected in (("--memory", str(self.memory)), ("--from-date", "2024.02.29"),
                               ("--to-date", "2025.02.28"), ("--probe-history-timeframe", "H1")):
            self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertIn("--execute-backtests", command)
        self.assertNotIn("--dry-run", command)

    def test_outside_project_memory_is_rejected_before_launch(self):
        config = {**self.config, "memory_path": str(self.root.parent / "foreign.sqlite")}
        with self.assertRaisesRegex(ValueError, "fuera del proyecto"):
            build_history_command(config, self.service.history_dates())

    def test_probe_runs_through_node_http_and_finishes_in_existing_log(self):
        server = NodeServer(("127.0.0.1", 0), self.controller)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def post(path):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}",
                data=b"{}", headers={"Authorization": "Bearer test-only", "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        status, preview = post("/api/v1/universe/history-preview")
        self.assertEqual(status, 200)
        self.assertEqual(preview["pending"], 3)
        extraction = SymbolExtractionResult(
            (ExtractedSymbol("GBPUSD", trade_mode=3),), None, 11637157, "Broker-MT5"
        )
        with patch("manager_node_runtime.universe_service.extract_symbols_from_mt5", return_value=extraction):
            status, trade_preview = post("/api/v1/universe/trade-disabled-preview")
        self.assertEqual((status, trade_preview["symbols"]), (200, ["GBPUSD"]))
        status, job = post("/api/v1/jobs/universe-history")
        self.assertEqual(status, 202)
        self.assertEqual(job["current_stage"], "universe_history")
        deadline = time.monotonic() + 5
        while self.controller.process is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.controller.state["status"], "completed")
        self.assertIn("Todos los backtests han terminado", Path(job["log_path"]).read_text(encoding="utf-8"))

    def test_probe_resume_reuses_the_history_command_instead_of_generation(self):
        self.controller.state.update(status="paused", pipeline=[{"action":"universe_history", "cycle":1}],
                                     current_step_index=0, log_path=str(self.root / "paused.log"),
                                     request=self.service.history_dates())
        with patch.object(self.controller, "_launch_step") as launch:
            self.controller.resume()
        self.assertIn("--probe-universe-history", launch.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
