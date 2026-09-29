from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from manager_node_runtime.live_audit import LiveAuditController
from tests.manager_node_live_audit_fixtures import FakeOwner, LiveAuditTestBase, request


class LiveAuditTerminalTests(LiveAuditTestBase, unittest.TestCase):
    def test_active_pipeline_is_paused_and_only_that_pipeline_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running")
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "completed")
            self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))

    def test_real_account_membership_uses_symbol_and_lot_not_magic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            now = datetime.now(timezone.utc)
            matching = {
                "strategy": "magic-can-differ", "symbol": "EURUSD", "side": "buy",
                "open_time": now, "close_time": now, "open_price": 1.1,
                "close_price": 1.1, "volume": .01, "profit": 1.0,
            }
            wrong_lot = {**matching, "strategy": "one", "volume": .02}
            controller._extract_real = lambda *_args: (
                [matching, wrong_lot], {"EURUSD": .00001},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller.start(request())
            state = self._wait(controller)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["portfolio_closures"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["foreign_closures_ignored"], 1)

    def test_real_account_filter_uses_effective_broker_lot_not_invalid_saved_lot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            owner.portfolio_detail = lambda *_args: {"portfolio": {"id": 9, "members": [{
                "variant_key": "balanced", "candidate_id": "de40", "symbol": "DE40",
                "lot": .03, "units": 3,
            }]}}
            controller._broker_volume_rules = lambda: {"de40": (.1, .1)}
            now = datetime.now(timezone.utc)
            base = {
                "strategy": "real", "symbol": "DE40", "side": "buy", "open_time": now,
                "close_time": now, "open_price": 100.0, "close_price": 100.0, "profit": 1.0,
            }
            controller._extract_real = lambda *_args: (
                [{**base, "volume": .1}, {**base, "volume": .3}], {"DE40": 1.0},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller.start(request())
            state = self._wait(controller)

        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["portfolio_closures"], 1)

    def test_real_account_filter_uses_the_configured_lot_for_each_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            owner.portfolio_detail = lambda *_args: {"portfolio": {"id": 9, "members": [{
                "variant_key": "balanced", "candidate_id": "eth-grid", "symbol": "ETHUSD", "lot": .7,
            }]}}
            now = datetime.now(timezone.utc)
            base = {
                "strategy": "real", "symbol": "ETHUSD", "side": "buy", "open_time": now,
                "close_time": now, "open_price": 100.0, "close_price": 100.0, "profit": 1.0,
            }
            controller._extract_real = lambda *_args: (
                [{**base, "volume": .6}, {**base, "volume": .7}], {"ETHUSD": .01},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller._run_tester = lambda *_args: (
                [{**base, "strategy": "eth-grid", "volume": .6}], [99.0], {"eth-grid": 1}, [], {},
            )
            controller.start({**request(), "real_strategy_lots": {"eth-grid": .6}})
            state = self._wait(controller)

        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["matched_trades"], 1)
        self.assertEqual(state["last_result"]["real_history_detail"]["foreign_closures_ignored"], 1)

    def test_real_account_filter_uses_the_symbol_reported_by_the_tester(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "idle")
            owner.portfolio_detail = lambda *_args: {"portfolio": {"id": 9, "members": [{
                "variant_key": "balanced", "candidate_id": "nas-one", "symbol": "NAS100", "lot": .01,
            }]}}
            now = datetime.now(timezone.utc)
            trade = {
                "strategy": "nas-one", "symbol": "NAS100.fs", "side": "buy", "open_time": now,
                "close_time": now, "open_price": 100.0, "close_price": 100.0,
                "volume": .01, "profit": 1.0,
            }
            controller._extract_real = lambda *_args: (
                [dict(trade)], {"NAS100.fs": .01},
                {"login": "111", "native_report": {"filename": "real.html", "native_terminal_report": True},
                 "history_detail": {}},
            )
            controller._run_tester = lambda *_args: ([dict(trade)], [99.0], {"nas-one": 1}, [], {})
            controller.start(request())
            state = self._wait(controller)

        self.assertEqual(state["last_result"]["real_trades"], 1)
        self.assertEqual(state["last_result"]["matched_trades"], 1)

    def test_pipeline_already_paused_by_user_stays_paused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "paused")
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "completed")
            self.assertEqual((owner.pause_calls, owner.resume_calls), (0, 0))
            self.assertEqual(owner.state["status"], "paused")

    def _assert_restore_login(self, state: dict, owner, initialize_calls: list, launches: list,
                              closed_gracefully: list) -> None:
        self.assertEqual(state["status"], "completed")
        self.assertEqual(len(initialize_calls), 2)
        self.assertEqual(set(initialize_calls[0]), {"path", "timeout"})
        self.assertEqual(set(initialize_calls[1]), {"path", "timeout", "login", "server"})
        self.assertEqual(initialize_calls[1]["path"], "C:\\IC\\terminal64.exe")
        self.assertEqual(initialize_calls[1]["login"], 333)
        self.assertEqual(initialize_calls[1]["server"], "CapitalPoint-Live")
        self.assertNotIn("password", initialize_calls[1])
        # Solo el arranque con el INI es manual. La reapertura normal la hace
        # initialize(path=...) para no competir con una segunda instancia MT5.
        self.assertEqual(len(launches), 1)
        self.assertIn("KeepPrivate = 1", launches[0][1] or "")
        self.assertIn("Login = 333", launches[0][1] or "")
        self.assertIn("Password = restore-secret", launches[0][1] or "")
        self.assertEqual(closed_gracefully, [set(), set(), set()])
        restore = state["terminal_restore"]
        self.assertEqual(len(restore), 1)
        self.assertEqual(restore[0]["terminal"], "MT5_IC_1")
        self.assertEqual((restore[0]["login"], restore[0]["server"]), ("333", "CapitalPoint-Live"))
        self.assertTrue(restore[0]["restored"])
        self.assertTrue(restore[0]["password_persisted"])
        self.assertTrue(restore[0]["reopened_without_password"])
        self.assertEqual(state["last_result"]["terminal_restore"], restore)
        self.assertNotIn("tester-secret", str(state))
        self.assertNotIn("restore-secret", str(state))
        # La restauración precede a la reanudación: el pipeline no puede reabrir
        # el terminal en la cuenta real.
        self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))
        self.assertTrue(any("MT5_IC_1 → 333 (CapitalPoint-Live)" in line for line in state["log_lines"]))

    def test_the_terminal_is_left_on_the_configured_restore_account_and_the_result_proves_it(self) -> None:
        # El auditor loguea la cuenta real con initialize(login=...) y MT5 recuerda
        # la última cuenta del terminal: sin restaurar, el siguiente backtest del
        # pipeline probaría cada estrategia contra la cuenta real.
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running")
            initialize_calls: list[dict[str, object]] = []
            launches: list[tuple[str, str | None]] = []
            closed_gracefully: list[set[int]] = []

            class FakeMt5:
                @staticmethod
                def initialize(**kwargs) -> bool:
                    initialize_calls.append(dict(kwargs))
                    return True

                @staticmethod
                def account_info() -> SimpleNamespace:
                    return SimpleNamespace(login=333, server="CapitalPoint-Live", currency="EUR")

                @staticmethod
                def terminal_info() -> SimpleNamespace:
                    return SimpleNamespace(connected=True)

                @staticmethod
                def shutdown() -> None:
                    pass

            self._remember_on_extraction(controller)
            controller._terminal_pids_for_path = lambda _path: set()
            controller._launch_terminal = lambda path, config_path=None: (
                launches.append((
                    path, config_path.read_text(encoding="utf-8") if config_path else None,
                )) or {101}
            )
            controller._close_terminal_pids_gracefully = closed_gracefully.append
            with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": FakeMt5}):
                controller.start(request())
                state = self._wait(controller)
        self._assert_restore_login(
            state, owner, initialize_calls, launches, closed_gracefully,
        )

    def test_a_terminal_left_on_another_account_is_reported_without_hiding_the_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            attempts = 0

            class RefusingMt5:
                @staticmethod
                def initialize(**_kwargs) -> bool:
                    nonlocal attempts
                    attempts += 1
                    return attempts == 1

                @staticmethod
                def account_info() -> SimpleNamespace:
                    return SimpleNamespace(login=333, server="CapitalPoint-Live")

                @staticmethod
                def terminal_info() -> SimpleNamespace:
                    return SimpleNamespace(connected=True)

                @staticmethod
                def last_error() -> tuple[int, str]:
                    return -6, "Authorization failed"

                @staticmethod
                def shutdown() -> None:
                    pass

            self._remember_on_extraction(controller)
            controller._terminal_pids_for_path = lambda _path: set()
            controller._launch_terminal = lambda _path, _config_path=None: {101}
            controller._close_terminal_pids_gracefully = lambda _pids: None
            with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": RefusingMt5}):
                controller.start(request())
                state = self._wait(controller)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(attempts, 2)
        self.assertFalse(state["terminal_restore"][0]["restored"])
        self.assertFalse(state["terminal_restore"][0]["password_persisted"])
        self.assertFalse(state["terminal_restore"][0]["reopened_without_password"])
        self.assertIn("Authorization failed", state["terminal_restore"][0]["error"])
        self.assertIn("no quedó en la cuenta configurada 333", state["progress_text"])

    def test_the_same_terminal_is_only_restored_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = LiveAuditController(FakeOwner("idle"), Path(temp))
            for section in ("Terminal.2", "Terminal.2", "Terminal.3"):
                controller._remember_real_account_terminal(
                    "9", section,
                    {"name": section, "mt5_path": rf"C:\IC\{section}\terminal64.exe"},
                )
            controller._remember_real_account_terminal(
                "9", "Terminal.9", {"name": "sin ruta", "mt5_path": ""},
            )
            touched = controller.real_account_terminals["9"]

        self.assertEqual([row["section"] for row in touched], ["Terminal.2", "Terminal.3"])

    def test_tester_login_is_confirmed_independently_in_every_selected_terminal(self) -> None:
        controller = LiveAuditController(FakeOwner("idle"), Path(tempfile.gettempdir()))
        initialized: list[str] = []
        closed: list[set[int]] = []

        class FakeMt5:
            @staticmethod
            def initialize(**kwargs) -> bool:
                initialized.append(str(kwargs["path"]))
                return True

            @staticmethod
            def account_info() -> SimpleNamespace:
                return SimpleNamespace(login=222, server="IC-Demo")

            @staticmethod
            def terminal_info() -> SimpleNamespace:
                return SimpleNamespace(connected=True)

            @staticmethod
            def shutdown() -> None:
                pass

        controller._terminal_pids = lambda: set()
        controller._close_terminal_pids_gracefully = closed.append
        profiles = [
            ("Terminal.2", {"name": "MT5_IC_1", "mt5_path": r"C:\IC1\terminal64.exe"}),
            ("Terminal.3", {"name": "MT5_IC_2", "mt5_path": r"C:\IC2\terminal64.exe"}),
        ]
        with unittest.mock.patch.dict(sys.modules, {"MetaTrader5": FakeMt5}):
            rows = controller._verify_tester_terminals(request(), profiles)

        self.assertEqual(initialized, [r"C:\IC1\terminal64.exe", r"C:\IC2\terminal64.exe"])
        self.assertEqual(closed, [set(), set()])
        self.assertTrue(all(row["verified"] for row in rows))
        self.assertEqual({row["login"] for row in rows}, {"222"})
        self.assertEqual({row["server"] for row in rows}, {"IC-Demo"})

    def test_main_journal_capture_keeps_only_new_lines_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "terminal-data"
            journal = data_dir / "logs" / "20260829.log"
            journal.parent.mkdir(parents=True)
            journal.write_bytes(b"\xff\xfe" + "old line\r\n".encode("utf-16-le"))
            profiles = [("Terminal.2", {
                "name": "MT5_IC_1", "data_dir": str(data_dir),
                "mt5_path": r"C:\IC1\terminal64.exe",
            })]
            snapshot = LiveAuditController._main_journal_snapshot(profiles)
            with journal.open("ab") as handle:
                handle.write(
                    "222: authorized on IC-Demo; tester-secret\r\n".encode("utf-16-le")
                )
            validations = [{
                "section": "Terminal.2", "terminal": "MT5_IC_1", "login": "222",
                "server": "IC-Demo", "connected": True, "verified": True, "error": None,
            }]
            controller = LiveAuditController(FakeOwner("idle"), root / "runtime")
            output_dir = root / "audit-logs"
            controller._capture_main_journals(
                profiles, snapshot, output_dir, validations, request()
            )
            captured = (output_dir / "main_journal_MT5_IC_1.txt").read_text(encoding="utf-8")

        self.assertNotIn("old line", captured)
        self.assertIn("222: authorized on IC-Demo", captured)
        self.assertNotIn("tester-secret", captured)
        self.assertIn("[REDACTED]", captured)
        self.assertTrue(validations[0]["journal_captured"])
        self.assertTrue(validations[0]["journal_login_seen"])
        self.assertTrue(validations[0]["journal_server_seen"])

    def test_no_terminal_is_ever_force_killed_outside_the_graceful_close(self) -> None:
        # `taskkill /F` mata MT5 antes de que guarde su configuración y le borra
        # la cuenta. El terminal vuelve a abrirse sin sesión, el Strategy Tester
        # se queda en «not synchronized with trade server» y NINGÚN backtest
        # genera informe: el 2026-08-21 el auditor mató así el terminal del
        # pipeline (`tester_pids`) y dejó dos días de discovery puntuando 0
        # supervivientes. El único uso legítimo es el último recurso dentro de
        # `_close_terminal_pids_gracefully`, para los que ignoran WM_CLOSE.
        # La auditoria vive repartida en varios modulos; la guarda los revisa
        # todos para que dividir el fichero no la apague en silencio.
        modules = sorted(
            (Path(__file__).resolve().parents[1] / "manager_node_runtime")
            .glob("live_audit*.py")
        )
        self.assertGreaterEqual(len(modules), 2, msg=str(modules))
        sources = {path.name: path.read_text(encoding="utf-8") for path in modules}
        source = "\n".join(sources.values())
        # El único uso permitido es el último recurso del cierre ordenado, para
        # los terminales que ignoran WM_CLOSE.
        fallback = "self._close_terminal_pids(pids & self._terminal_pids())"
        calls = [
            f"{name}:{number}: {line.strip()}"
            for name, text in sources.items()
            for number, line in enumerate(text.splitlines(), start=1)
            if "self._close_terminal_pids(" in line and fallback not in line
        ]
        self.assertEqual(
            calls, [],
            msg=(
                "Estas llamadas fuerzan el cierre del terminal fuera de "
                "`_close_terminal_pids_gracefully` y le borran la cuenta "
                f"guardada; usar el cierre ordenado: {calls}"
            ),
        )
        # Y el último recurso sigue existiendo: sin él, un terminal colgado
        # bloquearía la auditoría para siempre.
        self.assertIn(fallback, source)

    def test_missing_tick_quality_makes_the_result_not_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            owner, controller = self._controller(Path(temp), "running", quality=None)
            controller.start(request())
            state = self._wait(controller)
            self.assertEqual(state["status"], "not_comparable")
            self.assertIsNone(state["last_result"]["history_quality_pct"])
            self.assertEqual((owner.pause_calls, owner.resume_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
