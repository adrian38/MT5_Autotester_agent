"""Comprobacion manual: preparacion del area, ordenes y ruta del nodo."""
from __future__ import annotations

import configparser
import json
import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.manual_check import (
    MODEL_STAGES,
    STAGE_MODELS,
    build_manual_check_command,
    manual_check_summary,
    normalize_manual_check,
    prepare_manual_check,
)
from manager_node_runtime.node import NodeHandler


SET_TEXT = "ForceSymbol=XAUUSD||0||0||0||N\nTimeFrame=H1||0||0||0||N\nStartLots=0.07||0||0||0||N\n"


class ManualCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        (self.project / "outputs").mkdir(parents=True)
        self.set_path = self.project / "outputs" / "XAUUSD_H1_demo_g001_s001_v001.set"
        self.set_path.write_text(SET_TEXT, encoding="utf-8")
        (self.project / "tester_template.ini").write_text(
            "[Tester]\nFromDate=2024.01.01\nToDate=2025.01.01\n", encoding="utf-8",
        )
        self.runtime = self.root / "runtime" / "nodo"
        self.config = {
            "project_dir": str(self.project),
            "node_id": "nodo",
            "broker": "ICTRADING",
            "python_executable": "python.exe",
            "settings_file": "ui_settings.ini",
        }
        self.cfg = configparser.ConfigParser()
        self.cfg.read_dict({"Multiterminal": {"enabled": "1", "workers": "4"}})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self, **changes) -> dict:
        request = {
            "portfolio_id": 62,
            "scope": "full_history",
            "from_date": "2025.01.01",
            "to_date": "2026.01.01",
            "models": ["tick", "ohlc"],
            "use_multiterminal": True,
            "strategies": [{
                "set_path": str(self.set_path),
                "set_name": self.set_path.name,
                "symbol": "XAUUSD",
                "timeframe": "H1",
                "candidate_id": "ICTRADING/STANDARD:236",
            }],
        }
        request.update(changes)
        return request

    def test_the_two_data_models_are_two_stages_of_the_same_job(self) -> None:
        request = normalize_manual_check(self.payload())

        self.assertEqual(request["models"], ["ohlc", "tick"])
        self.assertEqual(
            [MODEL_STAGES[model] for model in request["models"]],
            ["manual_check_ohlc", "manual_check_tick"],
        )
        self.assertEqual(STAGE_MODELS["manual_check_tick"], "tick")

    def test_the_lot_defaults_to_the_minimum_and_is_written_into_each_copy(self) -> None:
        request = prepare_manual_check(self.config, self.runtime, self.payload())

        self.assertEqual(request["lot"], 0.01)
        copy = Path(request["work_dir"]) / "sets" / request["strategies"][0]["runtime_set"]
        self.assertIn("StartLots=0.01||0||0||0||N", copy.read_text(encoding="utf-8"))
        # El original no se toca: es el set que el portafolio tiene guardado.
        self.assertIn("StartLots=0.07", self.set_path.read_text(encoding="utf-8"))

    def test_an_edited_lot_reaches_the_copy(self) -> None:
        request = prepare_manual_check(self.config, self.runtime, self.payload(lot=0.5))

        copy = Path(request["work_dir"]) / "sets" / request["strategies"][0]["runtime_set"]
        self.assertIn("StartLots=0.5||0||0||0||N", copy.read_text(encoding="utf-8"))

    def test_every_tick_is_marked_in_each_copy(self) -> None:
        """Las dos corridas tienen que diferenciarse solo en el modelo de datos.

        Un set que llega con `UseEveryTick=false` hace que OHLC M1 y tick a
        tick miren lo mismo por dentro, y la comparacion de la pantalla deja
        de medir lo que dice medir.
        """
        self.set_path.write_text(
            SET_TEXT + "UseEveryTick=false||false||0||true||N\n", encoding="utf-8",
        )

        request = prepare_manual_check(self.config, self.runtime, self.payload())

        copy = Path(request["work_dir"]) / "sets" / request["strategies"][0]["runtime_set"]
        self.assertIn("UseEveryTick=true||false||0||true||N", copy.read_text(encoding="utf-8"))
        # El original no se toca: es el set que el portafolio tiene guardado.
        self.assertIn("UseEveryTick=false", self.set_path.read_text(encoding="utf-8"))

    def test_a_set_without_the_parameter_still_gets_it(self) -> None:
        request = prepare_manual_check(self.config, self.runtime, self.payload())

        copy = Path(request["work_dir"]) / "sets" / request["strategies"][0]["runtime_set"]
        text = copy.read_text(encoding="utf-8")
        self.assertIn("UseEveryTick=true", text)
        self.assertIn("StartLots=0.01||0||0||0||N", text)

    def test_each_model_runs_with_its_own_tester_model_and_report_folder(self) -> None:
        request = prepare_manual_check(self.config, self.runtime, self.payload())

        ohlc, cwd = build_manual_check_command(self.config, self.cfg, request, "ohlc")
        tick, _ = build_manual_check_command(self.config, self.cfg, request, "tick")

        self.assertEqual(cwd, self.project.resolve())
        self.assertEqual(ohlc[ohlc.index("--model") + 1], "1")
        self.assertEqual(tick[tick.index("--model") + 1], "4")
        # El envoltorio lleva las rutas como literales de Python, asi que se
        # comparan con su `repr`: en Windows es la misma ruta con las barras
        # escapadas.
        self.assertIn(repr(str(Path(request["work_dir"]) / "reports" / "ohlc")), ohlc[3])
        self.assertIn(repr(str(Path(request["work_dir"]) / "reports" / "tick")), tick[3])
        self.assertEqual(ohlc[ohlc.index("--from-date") + 1], "2025.01.01")
        self.assertEqual(ohlc[ohlc.index("--to-date") + 1], "2026.01.01")
        self.assertEqual(
            ohlc[ohlc.index("--set-file") + 1],
            str(Path(request["work_dir"]) / "sets" / request["strategies"][0]["runtime_set"]),
        )

    def test_a_single_terminal_run_keeps_one_worker(self) -> None:
        request = prepare_manual_check(
            self.config, self.runtime, self.payload(use_multiterminal=False),
        )

        command, _ = build_manual_check_command(self.config, self.cfg, request, "ohlc")

        self.assertEqual(command[command.index("--max-workers") + 1], "1")

    def test_multiterminal_takes_the_workers_the_agent_has_configured(self) -> None:
        request = prepare_manual_check(self.config, self.runtime, self.payload())

        command, _ = build_manual_check_command(self.config, self.cfg, request, "ohlc")

        self.assertEqual(command[command.index("--max-workers") + 1], "4")

    def test_a_missing_set_fails_before_the_job_is_queued(self) -> None:
        payload = self.payload()
        payload["strategies"][0]["set_path"] = str(self.project / "no_existe.set")

        with self.assertRaises(FileNotFoundError):
            prepare_manual_check(self.config, self.runtime, payload)

    def test_the_request_is_rejected_when_it_makes_no_sense(self) -> None:
        for changes, message in (
            ({"models": []}, "tipo de datos"),
            ({"models": ["renko"]}, "desconocidos"),
            ({"strategies": []}, "estrategias nuevas"),
            ({"from_date": "01/01/2025"}, "YYYY.MM.DD"),
            ({"from_date": "2026.06.01", "to_date": "2026.01.01"}, "posterior"),
            ({"lot": 0}, "entre 0.01 y 100"),
            ({"portfolio_id": 0}, "portafolio"),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError) as error:
                    normalize_manual_check(self.payload(**changes))
                self.assertIn(message, str(error.exception))

    def test_the_summary_reads_the_folder_and_not_the_running_job(self) -> None:
        request = prepare_manual_check(self.config, self.runtime, self.payload())
        work = Path(request["work_dir"])
        (work / "reports" / "ohlc").mkdir(parents=True)
        (work / "reports" / "ohlc" / "check_001_demo.htm").write_text("x", encoding="utf-8")
        (work / "reports" / "ohlc" / "check_001_demo-hst.png").write_bytes(b"x")

        summary = manual_check_summary(work)

        self.assertEqual(summary["check_id"], work.name)
        self.assertEqual(summary["request"]["portfolio_id"], 62)
        self.assertEqual(
            summary["reports"]["ohlc"], ["check_001_demo-hst.png", "check_001_demo.htm"],
        )
        self.assertEqual(json.loads((work / "check.json").read_text(encoding="utf-8"))["lot"], 0.01)

    def test_the_node_publishes_the_route_the_manager_calls(self) -> None:
        source = Path(NodeHandler.__module__.replace(".", "/") + ".py")
        self.assertIn('"/api/v1/jobs/manual-check"', source.read_text(encoding="utf-8"))
