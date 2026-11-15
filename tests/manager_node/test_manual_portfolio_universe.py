"""El historico OHLC manual manda el universo del broker con sus sufijos."""
from __future__ import annotations

import base64
import configparser
import tempfile
import unittest
from pathlib import Path

from backtest.symbols import apply_symbol_suffix, load_symbol_suffix_universe
from manager_node_runtime.manual_portfolio import create_manual_portfolio
from manager_node_runtime.manual_portfolio_history import build_history_command


class ManualHistoryUniverseTests(unittest.TestCase):
    """Area temporal propia; de la orden solo mira el universo y los sufijos."""

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

    def test_history_sends_the_broker_universe_so_the_suffix_is_not_applied_blind(self) -> None:
        # Vantage escribe el oro `XAUUSD+` y casi todo lo demas `.r`. La orden
        # llevaba los tres sufijos pero no el universo, asi que `run_tests.py`
        # aplicaba el general a ciegas: «ForceSymbol: XAUUSD -> XAUUSD.r», un
        # simbolo que ese servidor no ofrece, y el backtest salia sin historico.
        template = self.root / "tester_template.ini"
        template.write_text("[Tester]\nFromDate=2020.01.01\nToDate=2026.05.22\n", encoding="utf-8")
        assets = self.root / "assets" / "vantage_assets.ini"
        assets.parent.mkdir()
        assets.write_text(
            "[Forex]\nsymbols=EURUSD.r,GBPUSD.r\n\n"
            "[Metals]\nsymbols=XAGUSD.r,XAUUSD+\n\n"
            "[Indices]\nsymbols=NAS100.r,NAS100ft.r\n",
            encoding="utf-8",
        )
        settings = self.root / "ui_settings.ini"
        settings.write_text(
            f"[Paths]\ntemplate_path={template}\n"
            "[General]\nsymbol_suffix_enabled=1\nsymbol_suffix=.r\n"
            "symbol_futures_suffix=ft.r\nsymbol_shares_suffix=+\n",
            encoding="utf-8",
        )
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(settings, encoding="utf-8")
        saved = create_manual_portfolio(self.memory, self.runtime, {
            "name": "Oro", "portfolio_type": "balanced",
            "files": [self.uploaded("oro.set", "ForceSymbol=XAUUSD\nTimeFrame=H1\nStartLots=0.01\n")],
        })
        config = {"project_dir": str(self.root), "settings_file": str(settings), "broker": "VANTAGE"}

        _normalized, command, _cwd = build_history_command(
            config, cfg, self.memory, {"portfolio_id": saved["portfolio_id"]},
        )

        self.assertEqual(command[command.index("--symbol-universe") + 1], str(assets))
        # Con el codigo del propio runner: lo que esa orden hara con el simbolo.
        universe = load_symbol_suffix_universe(
            Path(command[command.index("--symbol-universe") + 1]),
            command[command.index("--symbol-suffix") + 1],
            command[command.index("--symbol-futures-suffix") + 1],
            command[command.index("--symbol-shares-suffix") + 1],
        )
        self.assertEqual(apply_symbol_suffix("XAUUSD", ".r", "ft.r", "+", universe), "XAUUSD+")
        self.assertEqual(apply_symbol_suffix("EURUSD", ".r", "ft.r", "+", universe), "EURUSD.r")

    def test_history_without_suffixes_does_not_filter_by_the_broker_universe(self) -> None:
        # El universo es tambien la señal de «el broker ya no ofrece esto»:
        # `run_tests.py` omite los sets cuyo Symbol no este listado. Donde no hay
        # sufijos que resolver no se manda, para no convertir un inventario
        # ausente o viejo en backtests saltados en silencio.
        template = self.root / "tester_template.ini"
        template.write_text("[Tester]\nFromDate=2020.01.01\nToDate=2026.05.22\n", encoding="utf-8")
        assets = self.root / "assets" / "roboforex_assets.ini"
        assets.parent.mkdir()
        assets.write_text("[Forex]\nsymbols=EURUSD\n", encoding="utf-8")
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_dict({"Paths": {"template_path": str(template)}})
        saved = create_manual_portfolio(self.memory, self.runtime, {
            "name": "Sin sufijos", "portfolio_type": "balanced",
            "files": [self.uploaded("uno.set", "ForceSymbol=EURUSD\nTimeFrame=H1\nStartLots=0.01\n")],
        })

        _normalized, command, _cwd = build_history_command(
            {"project_dir": str(self.root), "broker": "ROBOFOREX"}, cfg, self.memory,
            {"portfolio_id": saved["portfolio_id"]},
        )

        self.assertNotIn("--symbol-universe", command)
