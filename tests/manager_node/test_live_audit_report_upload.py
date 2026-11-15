from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from manager_node_runtime.live_audit_report_upload import replace_tester_reports


SOURCE = "XAUUSD_H1_GOLD_Old_Optimizatio_c778ab20_g001_s016_v002.set"
EXPECTED = SOURCE.removesuffix(".set") + ".htm"
STRATEGY = "AXI/STANDARD:3535"


class Controller:
    def __init__(self, root: Path) -> None:
        import threading
        self.lock = threading.RLock()
        self.runtime_dir = root / "live_audits"
        self.runtime_dir.mkdir()
        self.persisted = 0
        self.states = {"audit-105": {
            "audit_key": "audit-105", "audit_id": "run-1", "status": "completed",
            "last_result": {"stale": True}, "log_lines": [],
            "last_payload": {
                "audit_id": "run-1",
                "tester_trades": [{"strategy": STRATEGY, "profit": -1}],
                "qualities": [40.0], "strategies": {STRATEGY: 1},
                "selected_members": [{"candidate_id": STRATEGY, "timeframe": "H1"}],
                "strategy_artifacts": [{
                    "strategy": STRATEGY, "symbol": "XAUUSD", "report_symbol": "XAUUSD",
                    "source_set": SOURCE, "runtime_start_lots": 0.06,
                    "tester_trades": 1, "history_quality_pct": 40.0,
                }],
            },
        }}

    def _report_trades(self, strategy, report):
        return [{
            "strategy": strategy, "symbol": report.symbol, "side": "buy",
            "open_time": datetime(2026, 10, 2, tzinfo=timezone.utc),
            "close_time": datetime(2026, 10, 3, tzinfo=timezone.utc),
            "open_price": 1.0, "close_price": 2.0, "volume": 0.06, "profit": 3.0,
        }]

    def _persist(self):
        self.persisted += 1

    def state(self, audit_key):
        return dict(self.states[str(audit_key)])


def report(quality="100% real ticks"):
    trade = SimpleNamespace(size=0.06)
    return SimpleNamespace(
        symbol="XAUUSD", timeframe="H1", trades=[trade],
        metrics={"History Quality": quality},
    )


def body(filename=EXPECTED):
    return {"reports": [{
        "filename": filename,
        "content_base64": base64.b64encode(b"<html>MT5</html>").decode("ascii"),
    }]}


class ManualAuditReportTests(unittest.TestCase):
    def test_a_100_pct_report_replaces_only_its_strategy_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch(
            "manager_node_runtime.live_audit_report_upload.parse_report", return_value=report(),
        ):
            controller = Controller(Path(temp))
            state = replace_tester_reports(controller, "audit-105", body())

        payload = state["last_payload"]
        artifact = payload["strategy_artifacts"][0]
        self.assertEqual(artifact["history_quality_pct"], 100.0)
        self.assertEqual(artifact["report_source"], "manual_upload")
        self.assertEqual(artifact["manual_report_original_file"], EXPECTED)
        self.assertEqual(payload["tester_trades"][0]["strategy"], STRATEGY)
        self.assertEqual(payload["tester_trades"][0]["profit"], 3.0)
        self.assertEqual(payload["qualities"], [100.0])
        self.assertIsNone(state["last_result"])
        self.assertEqual(controller.persisted, 1)

    def test_the_filename_must_be_the_exact_source_set_name_with_html_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            controller = Controller(Path(temp))
            with self.assertRaisesRegex(ValueError, "no coincide con ningun set"):
                replace_tester_reports(controller, "audit-105", body("cuenta_017_" + EXPECTED))

    def test_a_manual_report_without_100_pct_quality_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch(
            "manager_node_runtime.live_audit_report_upload.parse_report",
            return_value=report("99% real ticks"),
        ):
            controller = Controller(Path(temp))
            with self.assertRaisesRegex(ValueError, "debe ser 100%"):
                replace_tester_reports(controller, "audit-105", body())
        self.assertEqual(controller.persisted, 0)


if __name__ == "__main__":
    unittest.main()
