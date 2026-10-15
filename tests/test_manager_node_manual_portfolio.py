from __future__ import annotations

import base64
import contextlib
import sqlite3
import tempfile
import unittest
import configparser
from pathlib import Path

from manager_node_runtime.manual_portfolio import create_manual_portfolio
from manager_node_runtime.manual_portfolio_history import build_history_command, history_defaults
from manager_node_runtime.node import NodeHandler


class ManualPortfolioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = self.root / "memory.sqlite"
        self.runtime = self.root / "runtime"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def uploaded(name: str, text: str) -> dict[str, str]:
        return {
            "name": f"cartera/{name}",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }

    def test_uploaded_sets_become_an_auditable_saved_portfolio(self) -> None:
        payload = {
            "name": "Cartera AXI externa",
            "portfolio_type": "balanced",
            "files": [
                self.uploaded("eurusd.set", "ForceSymbol=EURUSD||0||0||0||N\nTimeFrame=60||0||0||0||N\nStartLots=0.03||0||0||0||N\n"),
                self.uploaded("xauusd.set", "ForceSymbol=XAUUSD||0||0||0||N\nTimeFrame=H1||0||0||0||N\nStartLots=0.01||0||0||0||N\n"),
            ],
        }

        result = create_manual_portfolio(self.memory, self.runtime, payload)

        self.assertEqual(result["strategies"], 2)
        sets = Path(result["sets_folder"])
        self.assertEqual(sorted(path.name for path in sets.iterdir()), ["eurusd.set", "xauusd.set"])
        conn = sqlite3.connect(self.memory)
        try:
            conn.row_factory = sqlite3.Row
            portfolio = conn.execute("select * from portfolios where id=?", (result["portfolio_id"],)).fetchone()
            members = conn.execute(
                "select symbol,timeframe,lot,set_path from portfolio_allocations where portfolio_id=? order by symbol",
                (result["portfolio_id"],),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(portfolio["name"], "Cartera AXI externa")
        self.assertEqual(portfolio["portfolio_type"], "balanced")
        self.assertEqual([(row["symbol"], row["timeframe"], row["lot"]) for row in members], [
            ("EURUSD", "H1", 0.01), ("XAUUSD", "H1", 0.01),
        ])
        self.assertTrue(all(Path(row["set_path"]).is_file() for row in members))
        normalized = (sets / "eurusd.set").read_text(encoding="utf-8")
        self.assertIn("StartLots=0.01||0.01||0.001000||0.100000||N", normalized)
        self.assertIn("AdjustLotsizeToVariableValues=false||false||0||true||N", normalized)
        self.assertIn("UseEveryTick=false||false||0||true||N", normalized)

    def test_symbol_and_timeframe_use_the_standard_set_inference(self) -> None:
        payload = {
            "name": "Inferido", "portfolio_type": "balanced",
            "files": [self.uploaded(
                "gold.set",
                "ForceSymbol=XAUUSD\nRun_Strategy=1\nST1_Timeframe=16385\nStartLots=0.10\n",
            )],
        }

        result = create_manual_portfolio(self.memory, self.runtime, payload)

        conn = sqlite3.connect(self.memory)
        try:
            row = conn.execute(
                "select symbol,timeframe,lot from portfolio_allocations where portfolio_id=?",
                (result["portfolio_id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("XAUUSD", "H1", 0.01))

    def test_a_set_without_auditable_identity_is_rejected_and_cleaned(self) -> None:
        payload = {
            "name": "Incompleto", "portfolio_type": "balanced",
            "files": [self.uploaded("bad.set", "StartLots=0.01||0||0||0||N\n")],
        }
        with self.assertRaisesRegex(ValueError, "inferir el simbolo y el timeframe"):
            create_manual_portfolio(self.memory, self.runtime, payload)
        self.assertFalse(any((self.runtime / "manual_portfolios").glob("*/sets/*.set")))

    def test_history_uses_template_dates_and_forces_ohlc_model(self) -> None:
        template = self.root / "tester_template.ini"
        template.write_text("[Tester]\nFromDate=2020.01.01\nToDate=2026.05.22\n", encoding="utf-8")
        settings = self.root / "ui_settings.ini"
        settings.write_text(f"[Paths]\ntemplate_path={template}\n", encoding="utf-8")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(settings, encoding="utf-8")
        saved = create_manual_portfolio(self.memory, self.runtime, {
            "name": "OHLC", "portfolio_type": "balanced",
            "files": [self.uploaded("one.set", "ForceSymbol=EURUSD\nTimeFrame=H1\nStartLots=0.02\n")],
        })
        config = {"project_dir": str(self.root), "settings_file": str(settings)}

        defaults = history_defaults(config, cfg)
        normalized, command, cwd = build_history_command(
            config, cfg, self.memory, {"portfolio_id": saved["portfolio_id"]},
        )

        self.assertEqual(defaults, {"from_date": "2020.01.01", "to_date": "2026.05.22", "model": "1"})
        self.assertEqual(normalized["model"], 1)
        self.assertEqual(command[command.index("--model") + 1], "1")
        self.assertEqual(command[command.index("--from-date") + 1], "2020.01.01")
        self.assertEqual(command[command.index("--to-date") + 1], "2026.05.22")
        self.assertEqual(cwd, self.root)

    def test_history_repairs_sets_and_allocations_created_before_normalization(self) -> None:
        template = self.root / "tester_template.ini"
        template.write_text("[Tester]\nFromDate=2020.01.01\nToDate=2026.05.22\n", encoding="utf-8")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_dict({"Paths": {"template_path": str(template)}})
        saved = create_manual_portfolio(self.memory, self.runtime, {
            "name": "Anterior", "portfolio_type": "balanced",
            "files": [self.uploaded("old.set", "ForceSymbol=XAUUSD\nTimeFrame=H1\nStartLots=0.10\n")],
        })
        set_path = next(Path(saved["sets_folder"]).glob("*.set"))
        set_path.write_text(
            "ForceSymbol=XAUUSD\nTimeFrame=H1\nRisk=2\nStartLots=0.10\n"
            "AdjustLotsizeToVariableValues=true\nUseEveryTick=true\n",
            encoding="utf-8",
        )
        with contextlib.closing(sqlite3.connect(self.memory)) as conn:
            conn.execute(
                "update portfolio_allocations set units=10,lot=0.10 where portfolio_id=?",
                (saved["portfolio_id"],),
            )
            conn.execute(
                "update portfolio_members set lot_multiplier=10,lot=0.10 where portfolio_id=?",
                (saved["portfolio_id"],),
            )
            conn.execute(
                "update portfolios set total_lot=0.10,total_units=10 where id=?",
                (saved["portfolio_id"],),
            )
            conn.commit()

        build_history_command(
            {"project_dir": str(self.root)}, cfg, self.memory,
            {"portfolio_id": saved["portfolio_id"]},
        )

        repaired = set_path.read_text(encoding="utf-8")
        self.assertIn("Risk=0||0||0||20||N", repaired)
        self.assertIn("StartLots=0.01||0.01||0.001000||0.100000||N", repaired)
        self.assertIn("AdjustLotsizeToVariableValues=false||false||0||true||N", repaired)
        self.assertIn("UseEveryTick=false", repaired)
        with contextlib.closing(sqlite3.connect(self.memory)) as conn:
            allocation = conn.execute(
                "select units,lot from portfolio_allocations where portfolio_id=?",
                (saved["portfolio_id"],),
            ).fetchone()
            portfolio = conn.execute(
                "select total_units,total_lot from portfolios where id=?",
                (saved["portfolio_id"],),
            ).fetchone()
        self.assertEqual(allocation, (1, 0.01))
        self.assertEqual(portfolio, (1, 0.01))

    def test_http_dispatch_exposes_history_defaults_and_start(self) -> None:
        class Controller:
            def manual_portfolio_defaults(self):
                return {"model": "1"}

            def start_manual_history(self, payload):
                return {"payload": payload}

        class Request:
            path = "/api/v1/portfolios/manual-defaults"

            @staticmethod
            def _body(maximum=1_000_000):
                return {"portfolio_id": 4}

        request = Request()
        self.assertEqual(NodeHandler._dispatch_portfolio_post(request, Controller()), (200, {"model": "1"}))
        request.path = "/api/v1/portfolios/manual-history"
        self.assertEqual(
            NodeHandler._dispatch_portfolio_post(request, Controller()),
            (202, {"payload": {"portfolio_id": 4}}),
        )


if __name__ == "__main__":
    unittest.main()
