"""El camino MT4 del sondeo de cuentas reales."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from manager_node_runtime import live_account_mt4, live_account_snapshot


class RequestTests(unittest.TestCase):
    def test_mt4_does_not_require_a_restore_account(self) -> None:
        request = live_account_snapshot.normalize_snapshot_request({
            "accounts": [{
                "login": "60090762", "server": "Axi-US50-Live",
                "password": "secret", "terminal": "mt4",
            }],
        })
        self.assertEqual(request["accounts"][0]["terminal"], "mt4")
        self.assertEqual(request["restore_login"], "")

    def test_mt5_still_requires_a_restore_account(self) -> None:
        with self.assertRaisesRegex(ValueError, "cuenta final"):
            live_account_snapshot.normalize_snapshot_request({
                "accounts": [{
                    "login": "60090762", "server": "Axi-US50-Live",
                    "password": "secret", "terminal": "mt5",
                }],
            })


class Mt4ReadingTests(unittest.TestCase):
    def test_the_configured_mt4_is_initialized_and_left_on_the_read_account(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            terminal = root / "terminal.exe"
            terminal.write_bytes(b"")
            settings = root / "ui_settings.ini"
            settings.write_text(
                "\n".join((
                    "[MT4Terminal.1]", "enabled=1", "broker=AXI",
                    "name=MT4 AXI", f"mt4_path={terminal}", "portable=0",
                )),
                encoding="utf-8",
            )
            reports = root / "reports"
            controller = SimpleNamespace(
                owner=SimpleNamespace(config={"broker": "AXI"}),
                runtime_dir=root,
                _settings_path=lambda: settings,
                _snapshot_reports_dir=lambda _snapshot: reports,
            )
            captured: dict[str, str] = {}

            def launch(_controller, _profile, config_path):
                captured["config"] = config_path.read_text(encoding="utf-8")
                return {42}

            def export(**kwargs):
                destination = kwargs["destination"]
                destination.write_text(
                    "<html><body><table>"
                    "<tr><td>Account: 60090762 Currency: USD</td></tr>"
                    "<tr><td>Closed Transactions:</td></tr>"
                    "<tr><td>Summary:</td></tr>"
                    "<tr><td>Balance:</td><td>1234.50</td>"
                    "<td>Equity:</td><td>1240.25</td></tr>"
                    "</table></body></html>",
                    encoding="utf-8",
                )
                return {"filename": destination.name, "terminal": "mt4"}

            account = {
                "account_key": "60090762@Axi-US50-Live", "login": "60090762",
                "server": "Axi-US50-Live", "password": "secret", "terminal": "mt4",
            }
            with mock.patch.object(live_account_mt4, "_launch", side_effect=launch), mock.patch.object(
                live_account_mt4, "export_native_mt4_history_report", side_effect=export,
            ):
                result = live_account_mt4.read_mt4_account(
                    controller, account, "snapshot",
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 2, tzinfo=timezone.utc),
                )

            self.assertIn("Login=60090762", captured["config"])
            self.assertIn("Password=secret", captured["config"])
            self.assertEqual(result["terminal"], "mt4")
            self.assertEqual(result["balance"], 1234.5)
            self.assertEqual(result["equity"], 1240.25)
            self.assertEqual(result["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
