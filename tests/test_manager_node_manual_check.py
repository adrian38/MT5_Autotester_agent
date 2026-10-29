"""Comprobacion manual: preparacion del area, ordenes y ruta del nodo."""
from __future__ import annotations

import configparser
import json
import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.manual_check import (
    DEFAULT_RETRIES,
    MODEL_STAGES,
    STAGE_MODELS,
    build_manual_check_command,
    manual_check_summary,
    normalize_manual_check,
    pending_set_files,
    prepare_manual_check,
)
from manager_node_runtime.node import JobController, NodeHandler
from run_tests_symbols import apply_symbol_suffix, load_symbol_suffix_universe


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

    def test_the_check_sends_the_broker_universe_with_its_suffixes(self) -> None:
        # Mismo fallo que en el historico OHLC manual: los sufijos viajaban sin
        # el universo y `run_tests.py` los aplicaba a ciegas, asi que en Vantage
        # el oro acababa como `XAUUSD.r` en vez de `XAUUSD+`.
        assets = self.project / "assets" / "vantage_assets.ini"
        assets.parent.mkdir()
        assets.write_text(
            "[Forex]\nsymbols=EURUSD.r\n\n[Metals]\nsymbols=XAUUSD+\n", encoding="utf-8",
        )
        self.config["broker"] = "VANTAGE"
        self.cfg.read_dict({"General": {
            "symbol_suffix_enabled": "1", "symbol_suffix": ".r",
            "symbol_futures_suffix": "ft.r", "symbol_shares_suffix": "+",
        }})
        request = prepare_manual_check(self.config, self.runtime, self.payload())

        command, _ = build_manual_check_command(self.config, self.cfg, request, "ohlc")

        self.assertEqual(command[command.index("--symbol-universe") + 1], str(assets))
        universe = load_symbol_suffix_universe(assets, ".r", "ft.r", "+")
        self.assertEqual(apply_symbol_suffix("XAUUSD", ".r", "ft.r", "+", universe), "XAUUSD+")

    def test_the_check_omits_the_universe_when_there_are_no_suffixes(self) -> None:
        # Sin sufijos que resolver, mandarlo solo añadiria el filtro por
        # inventario: `run_tests.py` omite los sets cuyo Symbol no este listado.
        (self.project / "assets").mkdir()
        (self.project / "assets" / "ictrading_assets.ini").write_text(
            "[Forex]\nsymbols=EURUSD\n", encoding="utf-8",
        )
        request = prepare_manual_check(self.config, self.runtime, self.payload())

        command, _ = build_manual_check_command(self.config, self.cfg, request, "ohlc")

        self.assertNotIn("--symbol-universe", command)

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

    def test_the_retries_default_to_three_and_are_editable(self) -> None:
        self.assertEqual(DEFAULT_RETRIES, 3)
        self.assertEqual(normalize_manual_check(self.payload())["retries"], 3)
        self.assertEqual(normalize_manual_check(self.payload(retries=0))["retries"], 0)
        self.assertEqual(normalize_manual_check(self.payload(retries=10))["retries"], 10)

    def test_a_retry_only_carries_the_sets_that_have_no_report(self) -> None:
        """Repetir lo que ya salio costaria las mismas horas para lo mismo.

        Es el caso real del 2026-10-21: de tres estrategias en tick a tick,
        MT5 se colgo en una y las otras dos ya tenian su `.htm` al lado.
        """
        payload = self.payload()
        second = self.project / "outputs" / "USDJPY_M30_demo_g001_s002_v001.set"
        second.write_text(SET_TEXT, encoding="utf-8")
        payload["strategies"].append({
            "set_path": str(second), "set_name": second.name,
            "symbol": "USDJPY", "timeframe": "M30", "candidate_id": "ICTRADING/STANDARD:237",
        })
        request = prepare_manual_check(self.config, self.runtime, payload)
        work = Path(request["work_dir"])
        (work / "reports" / "tick").mkdir(parents=True)
        done = request["strategies"][0]["report_stem"]
        (work / "reports" / "tick" / f"{done}.htm").write_text("<html>", encoding="utf-8")

        self.assertEqual(
            [path.stem for path in pending_set_files(work, "tick")],
            [request["strategies"][1]["report_stem"]],
        )
        command, _ = build_manual_check_command(self.config, self.cfg, request, "tick", 2)
        carried = [command[index + 1] for index, value in enumerate(command) if value == "--set-file"]
        self.assertEqual([Path(path).stem for path in carried],
                         [request["strategies"][1]["report_stem"]])
        # El primer intento sigue llevandolos todos.
        first, _ = build_manual_check_command(self.config, self.cfg, request, "tick")
        self.assertEqual(first.count("--set-file"), 2)

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
            ({"retries": 11}, "entre 0 y 10"),
            ({"retries": -1}, "entre 0 y 10"),
            ({"retries": "muchos"}, "numero entero"),
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


class ManualCheckQueueTests(unittest.TestCase):
    """Una comprobacion fallida no puede dejar la cola parada.

    El 2026-10-21 la comprobacion del #62 acabo `failed`, la siguiente se
    quedo en «pendiente» para siempre y el usuario tuvo que darle a «Detener»
    para que arrancara: `failed` esta en RESUMABLE_STATUSES, asi que el nodo
    se daba por ocupado y `_drain_queue` volvia sin hacer nada.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.controller = JobController(
            {"node_id": "nodo", "project_dir": str(self.project)},
            self.project / "manager_node.json",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _state(self, job_type: str, status: str) -> None:
        self.controller.state = {
            "job_type": job_type, "status": status,
            "pipeline": [{"action": "manual_check_ohlc"}, {"action": "manual_check_tick"}],
            "current_step_index": 1, "log_path": str(self.project / "trabajo.log"),
        }

    def test_a_failed_manual_check_frees_the_node(self) -> None:
        self._state("manual_check", "failed")

        self.assertFalse(self.controller._is_resumable())
        self.assertFalse(self.controller._busy())

    def test_an_interrupted_manual_check_frees_it_too(self) -> None:
        self._state("manual_check", "interrupted")

        self.assertFalse(self.controller._busy())

    def test_a_paused_manual_check_still_reserves_it(self) -> None:
        # La pausa es del usuario: arrancar otro trabajo encima dejaria la
        # comprobacion a medias sin forma de reanudarla.
        self._state("manual_check", "paused")

        self.assertTrue(self.controller._is_resumable())
        self.assertTrue(self.controller._busy())

    def test_a_failed_generation_keeps_reserving_the_node(self) -> None:
        # Ahi si hay algo que continuar, y son horas de trabajo.
        self._state("generation", "failed")

        self.assertTrue(self.controller._is_resumable())

    def test_the_queue_starts_what_was_waiting_behind_a_failed_check(self) -> None:
        self._state("manual_check", "failed")
        started: list[dict] = []
        self.controller._start_manual_check = lambda payload: started.append(payload)
        self.controller.queue = [{
            "id": "uno", "type": "manual_check", "payload": {"portfolio_id": 62},
        }]

        self.controller._drain_queue()

        self.assertEqual(started, [{"portfolio_id": 62}])
        self.assertEqual(self.controller.queue, [])


class ManualCheckRetryTests(unittest.TestCase):
    """Quien decide el reintento es el pipeline, que ve el codigo de salida.

    El 2026-10-21 el XAUUSD H1 del #62 se colgo dos veces en tick a tick y
    tumbo la comprobacion entera con los otros dos informes ya escritos al
    lado. El usuario pidio reintentos, 3 por defecto y editables.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.work = self.project / "runtime" / "nodo" / "manual_checks" / "check_1"
        (self.work / "sets").mkdir(parents=True)
        (self.work / "reports" / "tick").mkdir(parents=True)
        for name in ("check_001_uno", "check_002_dos"):
            (self.work / "sets" / f"{name}.set").write_text(SET_TEXT, encoding="utf-8")
        (self.work / "reports" / "tick" / "check_002_dos.htm").write_text("<html>", encoding="utf-8")
        self.log = self.project / "trabajo.log"
        self.log.write_text("", encoding="utf-8")
        self.controller = JobController(
            {"node_id": "nodo", "project_dir": str(self.project)},
            self.project / "manager_node.json",
        )
        self.launched: list[int] = []
        self.controller._launch_next_runnable = lambda index, _path: (
            self.launched.append(index) or True
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _state(self, retries: int = 3) -> dict:
        step = {"action": "manual_check_tick", "cycle": None, "run_id": None}
        self.controller.state = {
            "log_path": str(self.log),
            "pipeline": [step],
            "request": {"work_dir": str(self.work), "retries": retries},
        }
        return step

    def test_a_blocked_strategy_is_tried_again_and_only_that_one(self) -> None:
        step = self._state()

        self.assertTrue(self.controller._retry_manual_check(step, 0))

        self.assertEqual(self.launched, [0])
        self.assertEqual(step["manual_attempt"], 2)
        self.assertIn("Reintento 1 de 3", self.log.read_text(encoding="utf-8"))
        self.assertEqual(
            [path.stem for path in pending_set_files(self.work, "tick")], ["check_001_uno"],
        )

    def test_the_retries_run_out(self) -> None:
        step = self._state(retries=2)
        step["manual_attempt"] = 3

        self.assertFalse(self.controller._retry_manual_check(step, 0))
        self.assertEqual(self.launched, [])

    def test_zero_retries_is_the_old_behaviour(self) -> None:
        step = self._state(retries=0)

        self.assertFalse(self.controller._retry_manual_check(step, 0))
        self.assertEqual(self.launched, [])

    def test_nothing_missing_is_nothing_to_repeat(self) -> None:
        # El codigo de salida no era 0 pero estan todos los informes: repetir
        # la tanda entera seria no entender el fallo.
        (self.work / "reports" / "tick" / "check_001_uno.htm").write_text("<html>", encoding="utf-8")
        step = self._state()

        self.assertFalse(self.controller._retry_manual_check(step, 0))
        self.assertEqual(self.launched, [])

    def test_another_kind_of_stage_is_none_of_its_business(self) -> None:
        step = self._state()
        step["action"] = "generation"

        self.assertFalse(self.controller._retry_manual_check(step, 0))
        self.assertEqual(self.launched, [])
