import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.ubs_risk_profit_repair_fixtures import SCHEMA, metrics, stored
from ubs.risk_profit_repair import scan_risk_profit_restatements
from ui.ubs_universe_logic import UBSUniverseLogicMixin


class Repairer(UBSUniverseLogicMixin):
    """Solo el mixin: el boton de Universo no necesita widgets para decidir."""

    def __init__(self, memory_path: Path) -> None:
        self.memory_path = memory_path
        self.messages: list[str] = []
        self.refreshed: list[str] = []
        self.status_text = SimpleNamespace(set=self.messages.append)

    def _ubs_memory_path(self) -> Path:
        return self.memory_path

    def _safe_refresh(self, label: str, callback) -> None:
        self.refreshed.append(label)

    def _refresh_ubs_universe(self) -> None:
        pass


class RiskProfitRepairButtonTests(unittest.TestCase):
    """El boton: confirmar, escribir auditoria, aplicar y refrescar."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.folder.name) / "memory.sqlite"
        conn = sqlite3.connect(self.memory_path)
        conn.executescript(SCHEMA)
        result = metrics()
        conn.execute(
            """insert into candidates
                   (id, run_id, generation, symbol, target_symbol, period, report_path,
                    score, accepted, metrics_json, status)
               values (1, 7, 1, 'USDCAD', 'USDCAD', 'H1', 'base.htm', ?, 0, ?, 'rejected')""",
            (result.score, stored(result)),
        )
        conn.commit()
        conn.close()
        self.app = Repairer(self.memory_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def plan(self):
        conn = sqlite3.connect(self.memory_path)
        conn.row_factory = sqlite3.Row
        try:
            return scan_risk_profit_restatements(
                conn,
                read_equity=lambda path: (5.7, .56),
                read_oos_evidence=lambda path, share: {},
            )
        finally:
            conn.close()

    def stored_status(self) -> str:
        conn = sqlite3.connect(self.memory_path)
        try:
            return conn.execute("select status from candidates where id=1").fetchone()[0]
        finally:
            conn.close()

    def audits(self) -> list[Path]:
        return sorted((self.memory_path.parent / "diagnostics").glob("risk_profit_repair_*.json"))

    def test_confirming_applies_writes_the_audit_and_refreshes(self) -> None:
        plan = self.plan()
        with patch("ui.ubs_universe_risk_repair.messagebox") as box:
            box.askyesno.return_value = True
            self.app._finish_risk_profit_repair(self.memory_path, plan)
        self.assertEqual(self.stored_status(), "accepted")
        self.assertIn("ubs_universe", self.app.refreshed)
        audit = json.loads(self.audits()[0].read_text(encoding="utf-8"))
        self.assertEqual(audit["rule"], "risk_profit_v2")
        self.assertEqual(audit["policy"]["mode"], "enforce")
        self.assertEqual(audit["base"][0]["stored_status"], "rejected")
        self.assertIn("previous_metrics_json", audit["base"][0])

    def test_declining_writes_nothing(self) -> None:
        plan = self.plan()
        with patch("ui.ubs_universe_risk_repair.messagebox") as box:
            box.askyesno.return_value = False
            self.app._finish_risk_profit_repair(self.memory_path, plan)
        self.assertEqual(self.stored_status(), "rejected")
        self.assertEqual(self.audits(), [])
        self.assertEqual(self.app.refreshed, [])

    def test_rows_the_route_leaves_alone_are_reported_not_written(self) -> None:
        conn = sqlite3.connect(self.memory_path)
        conn.execute(
            "update candidates set metrics_json=? where id=1",
            (stored(metrics(active_months=12)),),
        )
        conn.commit()
        conn.close()
        plan = self.plan()
        with patch("ui.ubs_universe_risk_repair.messagebox") as box:
            self.app._finish_risk_profit_repair(self.memory_path, plan)
            box.askyesno.assert_not_called()
        self.assertEqual(self.stored_status(), "rejected")
        self.assertEqual(self.audits(), [], "nada que escribir, nada que auditar")

    def test_nothing_to_do_reports_and_skips_the_confirmation(self) -> None:
        conn = sqlite3.connect(self.memory_path)
        conn.execute("update candidates set status='no_trades' where id=1")
        conn.commit()
        conn.close()
        plan = self.plan()
        with patch("ui.ubs_universe_risk_repair.messagebox") as box:
            self.app._finish_risk_profit_repair(self.memory_path, plan)
            box.askyesno.assert_not_called()
            box.showinfo.assert_called_once()
        self.assertEqual(self.audits(), [])

    def test_missing_memory_never_reaches_the_scan(self) -> None:
        self.app.memory_path = self.memory_path.with_name("gone.sqlite")
        with patch("ui.ubs_universe_risk_repair.messagebox") as box:
            self.app._repair_risk_profit_states()
            box.showinfo.assert_called_once()
            box.askyesno.assert_not_called()


if __name__ == "__main__":
    unittest.main()
