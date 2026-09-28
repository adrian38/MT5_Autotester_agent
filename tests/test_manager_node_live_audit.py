from __future__ import annotations

import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from manager_node_runtime.live_audit import (
    LiveAuditController, _audit_period, _read_set_text, _redact_log_files, _redact_runner_output,
    normalize_request,
)
from manager_node_runtime.mt5_native_history_report import (
    NativeHistoryReportError, validate_native_history_report,
)
from tests.manager_node_live_audit_fixtures import FakeOwner, LiveAuditTestBase, request


class LiveAuditEngineTests(LiveAuditTestBase, unittest.TestCase):
    def test_credentials_are_required_and_never_enter_public_state(self) -> None:
        payload = request()
        payload["source_password"] = ""
        with self.assertRaisesRegex(ValueError, "source_password"):
            normalize_request(payload)
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            controller.start(request())
            state = self._wait(controller)
            self.assertNotIn("password", str(state).casefold())
            self.assertNotIn("secret", (Path(temp) / "live_audits" / "state.json").read_text(encoding="utf-8"))

    def test_rolling_and_fixed_periods_use_complete_calendar_days(self) -> None:
        rolling = normalize_request(request())
        start, end = _audit_period(rolling, datetime(2026, 8, 30, 16, 45, tzinfo=timezone.utc))
        self.assertEqual(start.isoformat(), "2026-08-23T00:00:00+00:00")
        self.assertEqual(end.date().isoformat(), "2026-08-30")

        fixed = normalize_request({
            **request(), "period_mode": "fixed_dates",
            "period_start_date": "2026-08-23", "period_end_date": "2026-08-30",
        })
        start, end = _audit_period(fixed)
        self.assertEqual(start.isoformat(), "2026-08-23T00:00:00+00:00")
        self.assertEqual(end.date().isoformat(), "2026-08-30")

    def test_request_validates_real_lots_per_strategy(self) -> None:
        normalized = normalize_request({
            **request(), "real_strategy_lots": {"AXI/STANDARD:34173": "0.6"},
        })
        self.assertEqual(normalized["real_strategy_lots"], {"AXI/STANDARD:34173": 0.6})
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            normalize_request({**request(), "real_strategy_lots": []})
        with self.assertRaisesRegex(ValueError, "fuera"):
            normalize_request({**request(), "real_strategy_lots": {"bad": 0}})

    def test_runner_output_redacts_ini_and_incidental_secret_copies(self) -> None:
        text = "[Common]\nPassword=tester-secret\nerror tester-secret\nPassword=another-value\n"
        redacted = _redact_runner_output(text, "tester-secret")
        self.assertNotIn("tester-secret", redacted)
        self.assertNotIn("another-value", redacted)
        self.assertEqual(redacted.count("[REDACTED]"), 3)

    def test_run_tests_own_log_files_are_redacted_too(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "run.log").write_text("Password=tester-secret\n", encoding="utf-8")
            (root / "ignore.htm").write_text("Password=tester-secret\n", encoding="utf-8")
            _redact_log_files(root, "tester-secret")
            log = (root / "run.log").read_text(encoding="utf-8")
            report = (root / "ignore.htm").read_text(encoding="utf-8")

        self.assertNotIn("tester-secret", log)
        self.assertIn("tester-secret", report)

    def test_utf16_set_is_decoded_and_start_lots_is_replaced_without_nuls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "strategy.set"
            path.write_text(
                "EA_MagicNumber=1007||1000||1||10000||N\nStartLots=0.05||0.01||0.01||1||N\n",
                encoding="utf-16",
            )
            text, encoding = _read_set_text(path)
            changed = LiveAuditController._set_value(text, "StartLots", "0.02")
            target = Path(temp) / "changed.set"
            target.write_text(changed, encoding=encoding)
            reread, _ = _read_set_text(target)

        self.assertEqual(encoding, "utf-16")
        self.assertNotIn("\x00", reread)
        self.assertIn("StartLots=0.02||0.01||0.01||1||N", reread)
        self.assertEqual(LiveAuditController._set_parameter(reread, "EA_MagicNumber"), "1007")

    def test_saved_ustec_lot_is_raised_to_the_broker_minimum_for_the_tester(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            assets.mkdir()
            (assets / "ictrading_symbol_specs.json").write_text(
                '{"symbols":{"USTEC":{"volume_min":0.1,"volume_step":0.1}}}',
                encoding="utf-8",
            )
            owner = FakeOwner("idle")
            owner.config.update(project_dir=str(root), broker="ICTRADING")
            controller = LiveAuditController(owner, root / "runtime")
            rules = controller._broker_volume_rules()
            result = controller._tester_lot(
                {"symbol": "USTEC", "units": 1, "lot": 0.01}, rules,
            )

        self.assertEqual(result, (0.01, 0.1, 0.1, 0.1, 1))

    def test_portfolio_units_do_not_multiply_the_broker_minimum(self) -> None:
        result = LiveAuditController._tester_lot(
            {"symbol": "DE40", "units": 3, "lot": 0.03}, {"de40": (0.1, 0.1)},
        )
        self.assertEqual(result, (0.03, 0.1, 0.1, 0.1, 3))

    def test_real_account_report_must_be_the_native_terminal_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ReportHistory-111.html"
            path.write_text(
                '<html><head><title>111 - Trade History Report</title>'
                '<meta name="generator" content="client terminal"></head><body>'
                + ("x" * 600) + "</body></html>",
                encoding="utf-16",
            )
            artifact = validate_native_history_report(path, "111")
            localized = Path(temp) / "InformeHistorial-111.html"
            localized.write_text(
                '<html><head><title>111 - Informe del historial de trading</title>'
                '<meta name="generator" content="client terminal"></head><body>'
                + ("x" * 600) + "</body></html>",
                encoding="utf-16",
            )
            localized_artifact = validate_native_history_report(localized, "111")
            fake = Path(temp) / "reconstructed.html"
            fake.write_text("<html>Historial reconstruido</html>", encoding="utf-8")

            with self.assertRaises(NativeHistoryReportError):
                validate_native_history_report(fake, "111")

        self.assertTrue(artifact["native_terminal_report"])
        self.assertTrue(localized_artifact["native_terminal_report"])
        self.assertEqual(artifact["source"], "mt5_terminal_history_report")

    def test_artifact_path_only_exposes_reports_from_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = LiveAuditController(FakeOwner("idle"), Path(temp))
            controller.states["9"] = {"audit_key": "9", "audit_id": "run_1", "status": "completed"}
            reports = Path(temp) / "live_audits" / "audit_9" / "run_1" / "reports"
            reports.mkdir(parents=True)
            report = reports / "strategy.htm"
            report.write_text("report", encoding="utf-8")
            hidden_set = reports / "strategy.set"
            hidden_set.write_text("StartLots=0.06", encoding="utf-8")

            self.assertEqual(controller.artifact_path("9", "run_1", "strategy.htm"), report.resolve())
            with self.assertRaises(ValueError):
                controller.artifact_path("9", "run_1", "strategy.set")
            with self.assertRaises(ValueError):
                controller.artifact_path("9", "run_1", "../strategy.htm")
            with self.assertRaises(FileNotFoundError):
                controller.artifact_path("9", "old_run", "strategy.htm")

    def test_real_history_waits_for_sync_and_recovers_open_before_period(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = LiveAuditController(FakeOwner("idle"), Path(temp))
            controller.history_sync_attempts = 4
            controller.history_sync_delay_seconds = 0
            period_end = datetime.now(timezone.utc)
            period_start = period_end - timedelta(days=7)

            def deal(ticket: int, position: int, entry: int, moment: datetime, deal_type: int) -> SimpleNamespace:
                timestamp = int(moment.timestamp())
                return SimpleNamespace(
                    ticket=ticket, position_id=position, entry=entry, type=deal_type,
                    time=timestamp, time_msc=timestamp * 1000, magic=11008,
                    symbol="EURUSD", volume=.01, price=1.1, profit=1.0,
                    commission=0.0, swap=0.0, fee=0.0, comment="",
                )

            prior_open = deal(1, 10, 0, period_start - timedelta(days=1), 0)
            prior_close = deal(2, 10, 1, period_start + timedelta(hours=1), 1)
            current_open = deal(3, 20, 0, period_start + timedelta(days=1), 0)
            current_close = deal(4, 20, 1, period_start + timedelta(days=1, hours=1), 1)
            period_deals = [prior_close, current_open, current_close]

            class FakeMt5:
                def __init__(self) -> None:
                    self.period_calls = 0
                    self.shutdown_called = False

                @staticmethod
                def account_info() -> SimpleNamespace:
                    return SimpleNamespace(login=111, server="IC-Real", currency="USD")

                @staticmethod
                def terminal_info() -> SimpleNamespace:
                    return SimpleNamespace(connected=True)

                def history_deals_get(self, *_args, **kwargs):
                    if "position" in kwargs:
                        return [prior_open, prior_close] if kwargs["position"] == 10 else []
                    self.period_calls += 1
                    return [] if self.period_calls == 1 else period_deals

                @staticmethod
                def symbol_info(_symbol: str) -> SimpleNamespace:
                    return SimpleNamespace(point=.00001)

                @staticmethod
                def last_error() -> tuple[int, str]:
                    return 1, "Success"

                def shutdown(self) -> None:
                    self.shutdown_called = True

            mt5 = FakeMt5()
            controller._login_terminal = lambda *_args, **_kwargs: (
                mt5, "Terminal.2", {"name": "MT5_IC_1"}, set()
            )
            trades, points, account = controller._extract_real(request(), period_start, period_end)

        self.assertEqual(len(trades), 2)
        self.assertEqual(points, {"EURUSD": .00001})
        self.assertTrue(account["connected"])
        self.assertEqual(account["server"], "IC-Real")
        detail = account["history_detail"]
        self.assertEqual(detail["sync_snapshots"], [0, 3, 3])
        self.assertEqual(detail["period_raw_deals"], 3)
        self.assertEqual(detail["closing_deals"], 2)
        self.assertEqual(detail["positions_missing_open_in_period"], 1)
        self.assertEqual(detail["positions_recovered"], 1)
        self.assertEqual(detail["trades_reconstructed"], 2)
        self.assertTrue(mt5.shutdown_called)

    def test_portfolio_variant_is_required_and_selects_only_that_variant(self) -> None:
        payload = request()
        payload["portfolio_type"] = ""
        with self.assertRaisesRegex(ValueError, "portfolio_type"):
            normalize_request(payload)
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            _detail, members = controller._portfolio_members(9, "balanced")
            self.assertEqual([row["candidate_id"] for row in members], ["one"])

    def test_tester_uses_five_configured_broker_terminals_for_six_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _owner, controller = self._controller(Path(temp), "idle")
            profiles = [
                (f"Terminal.{index}", {"name": f"MT5_IC_{index}", "mt5_path": fr"C:\\IC{index}\\terminal64.exe"})
                for index in range(1, 6)
            ]
            controller._terminal_profiles = lambda *, include_disabled=False: (
                list(profiles) if include_disabled else list(profiles[:1])
            )
            selected = controller._tester_terminal_pool("Terminal.3", profiles[2][1], 6)

        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0][0], "Terminal.3")
        self.assertEqual({section for section, _profile in selected}, {section for section, _profile in profiles})

    def test_native_report_fallback_uses_only_the_active_broker_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary = root / "primary.exe"
            fallback = root / "fallback.exe"
            foreign = root / "foreign.exe"
            for path in (primary, fallback, foreign):
                path.touch()
            (root / "ui_settings.ini").write_text(
                "[Terminal.1]\nname=Primary\nenabled=1\nbroker=ICTRADING\nmt5_path=" + str(primary) + "\n"
                "[Terminal.2]\nname=Fallback\nenabled=0\nbroker=ICTRADING\nmt5_path=" + str(fallback) + "\n"
                "[Terminal.3]\nname=Foreign\nenabled=1\nbroker=ROBOFOREX\nmt5_path=" + str(foreign) + "\n",
                encoding="utf-8",
            )
            owner = FakeOwner("idle")
            owner.config.update(project_dir=str(root), settings_file="ui_settings.ini")
            controller = LiveAuditController(owner, root / "runtime")
            profiles = controller._native_report_profiles(primary)

        self.assertEqual([profile[1]["name"] for profile in profiles], ["Fallback"])

    def test_comparison_explains_missing_extra_and_deviation_reasons(self) -> None:
        now = datetime.now(timezone.utc)
        real = [{
            "strategy": "1007", "symbol": "EURUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 1.2, "volume": .02, "profit": -5.0,
        }]
        expected = [{
            "strategy": "one", "symbol": "EURUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 1.1, "volume": .01, "profit": 1.0,
        }, {
            "strategy": "two", "symbol": "XAUUSD", "side": "sell", "open_time": now,
            "close_time": now - timedelta(hours=1), "open_price": 1.0, "volume": .01, "profit": 1.0,
        }]
        result = LiveAuditController._compare(
            real, expected, {"EURUSD": .00001}, request(), {"one": 1, "two": 1},
        )
        self.assertEqual(result["comparison_detail"]["missing_by_strategy"], {"two": 1})
        self.assertEqual(result["comparison_detail"]["deviation_reasons"]["volume"], 1)
        self.assertEqual(result["comparison_detail"]["deviation_reasons"]["pnl"], 1)
        self.assertEqual(result["matched_trades"], 1)
        self.assertEqual(result["within_tolerance_trades"], 0)
        self.assertEqual(result["deviating_pairs"], 1)
        rows = result["comparison_detail"]["operation_comparisons"]
        self.assertEqual([row["status"] for row in rows], ["deviation", "missing"])
        self.assertEqual(rows[0]["real"]["strategy"], "1007")
        self.assertIsInstance(rows[0]["tester"]["open_time"], str)
        self.assertEqual(rows[0]["measurements"]["open_price_delta_points"], 10000.0)
        self.assertEqual(rows[1]["reasons"], ["no_real_same_symbol_and_side"])
        self.assertEqual(rows[1]["data_issues"], ["close_before_open"])
        self.assertEqual(result["comparison_detail"]["tester_data_issues"], {"close_before_open": 1})
        self.assertEqual(result["comparison_detail"]["strategy_summary"][0], {
            "strategy": "one", "tester_trades": 1, "aligned": 1,
            "within_tolerance": 0, "with_deviations": 1, "missing_real": 0,
        })
        self.assertIn("cada real se usa una vez", result["comparison_detail"]["methodology"]["alignment"])

    def test_only_adverse_pnl_differences_trigger_the_tolerance(self) -> None:
        now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        base = {
            "strategy": "pnl", "symbol": "EURUSD", "side": "buy",
            "open_time": now, "close_time": now, "open_price": 1.1, "volume": .1,
        }
        cases = (
            (28.69, 37.64, "favorable", "matched"),
            (-.45, 1.79, "favorable", "matched"),
            (-4.73, -1.25, "favorable", "matched"),
            (1.80, -1.12, "unfavorable", "deviation"),
            (-3.15, -3.35, "unfavorable", "matched"),
            (10.0, 9.0, "unfavorable", "matched"),
        )
        for tester_profit, real_profit, direction, status in cases:
            with self.subTest(tester=tester_profit, real=real_profit):
                result = LiveAuditController._compare(
                    [{**base, "profit": real_profit}],
                    [{**base, "profit": tester_profit}],
                    {"EURUSD": .00001}, request(), {"pnl": 1},
                )
                row = result["comparison_detail"]["operation_comparisons"][0]
                self.assertEqual(row["status"], status)
                self.assertEqual(row["measurements"]["pnl_direction"], direction)
                self.assertEqual("pnl" in row["reasons"], status == "deviation")
                if direction == "favorable":
                    self.assertEqual(row["measurements"]["pnl_adverse_delta"], 0)

        adverse = LiveAuditController._compare(
            [{**base, "profit": -1.12}], [{**base, "profit": 1.80}],
            {"EURUSD": .00001}, request(), {"pnl": 1},
        )["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(adverse["reasons"], ["pnl"])
        self.assertEqual(adverse["measurements"]["pnl_adverse_delta"], 2.92)
        self.assertEqual(adverse["measurements"]["pnl_adverse_delta_pct"], 162.222)

        favorable_but_late = LiveAuditController._compare(
            [{**base, "close_time": now + timedelta(seconds=384), "profit": 37.64}],
            [{**base, "profit": 28.69}],
            {"EURUSD": .00001}, request(), {"pnl": 1},
        )["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(favorable_but_late["status"], "deviation")
        self.assertEqual(favorable_but_late["reasons"], ["close_time"])
        self.assertEqual(favorable_but_late["measurements"]["pnl_direction"], "favorable")

    def test_xauusd_eleven_point_price_delta_is_within_default_tolerance(self) -> None:
        now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        real = [{
            "strategy": "real", "symbol": "XAUUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 4566.63, "volume": .03, "profit": 1.0,
        }]
        tester = [{
            "strategy": "xau", "symbol": "XAUUSD", "side": "buy", "open_time": now,
            "close_time": now, "open_price": 4566.74, "volume": .03, "profit": 1.0,
        }]

        result = LiveAuditController._compare(
            real, tester, {"XAUUSD": .01}, request(), {"xau": 1},
        )

        row = result["comparison_detail"]["operation_comparisons"][0]
        self.assertEqual(row["measurements"]["open_price_delta_points"], 11.0)
        self.assertEqual(row["limits"]["open_price_points"], 205)
        self.assertEqual(row["limits"]["open_price_absolute"], 2.05)
        self.assertEqual(row["limits"]["open_price_configured_points"], 15)
        self.assertEqual(row["limits"]["open_price_rule"], "adaptive_gold")
        self.assertEqual(row["status"], "matched")
