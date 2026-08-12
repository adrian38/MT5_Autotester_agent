import tempfile
import unittest
import configparser
from unittest.mock import Mock, patch

import run_tests


class ListLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)


class CopyReportsToProjectTests(unittest.TestCase):
    def test_detects_model4_report_shell_with_zero_bars_and_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_tests.Path(temp_dir) / "empty.htm"
            report.write_text(
                "\n".join(
                    [
                        "<td>Calidad del historial:</td><td><b>99%</b></td>",
                        "<td>Barras:</td><td><b>0</b></td>",
                        "<td>Ticks:</td><td><b>0</b></td>",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertTrue(run_tests.model4_report_has_empty_tester_data([report]))

    def test_does_not_treat_zero_trade_report_with_market_data_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_tests.Path(temp_dir) / "zero_trades.htm"
            report.write_text(
                "\n".join(
                    [
                        "<td>Bars:</td><td><b>2900</b></td>",
                        "<td>Ticks:</td><td><b>15389412</b></td>",
                        "<td>Total Trades:</td><td><b>0</b></td>",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertFalse(run_tests.model4_report_has_empty_tester_data([report]))

    def test_model4_history_preflight_rotates_all_target_symbol_years(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            ini_path = root / "tester.ini"
            ini_path.write_text(
                "\n".join(
                    [
                        "[Tester]",
                        "Symbol=S&P.fs",
                        "ToDate=2026.06.30",
                    ]
                ),
                encoding="utf-8",
            )
            wanted = root / "bases" / "Axi-Live" / "history" / "S&P.fs" / "2026.hcc"
            other_year = wanted.with_name("2025.hcc")
            other_symbol = root / "bases" / "Axi-Live" / "history" / "USDJPY" / "2026.hcc"
            for path in (wanted, other_year, other_symbol):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path.name.encode("ascii"))
            logger = ListLogger()

            rotations = run_tests.prepare_model4_history_preflight(ini_path, [root], logger)

            self.assertEqual(
                {rotation.original for rotation in rotations},
                {wanted, other_year},
            )
            self.assertFalse(wanted.exists())
            self.assertFalse(other_year.exists())
            self.assertTrue(all(rotation.backup.exists() for rotation in rotations))
            self.assertTrue(other_symbol.exists())

            run_tests.finish_model4_history_preflight(rotations, logger)

            self.assertTrue(wanted.exists())
            self.assertTrue(other_year.exists())
            self.assertTrue(all(not rotation.backup.exists() for rotation in rotations))
            self.assertTrue(any("anterior restaurada" in message for message in logger.messages))

    def test_model4_history_preflight_keeps_refreshed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            ini_path = root / "tester.ini"
            ini_path.write_text(
                "[Tester]\nSymbol=S&P.fs\nToDate=2026.06.30\n",
                encoding="utf-8",
            )
            cache = root / "bases" / "Axi-Live" / "history" / "S&P.fs" / "2026.hcc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"old")
            logger = ListLogger()

            rotations = run_tests.prepare_model4_history_preflight(ini_path, [root], logger)
            cache.write_bytes(b"refreshed")
            run_tests.finish_model4_history_preflight(rotations, logger)

            self.assertEqual(cache.read_bytes(), b"refreshed")
            self.assertFalse(rotations[0].backup.exists())
            self.assertTrue(any("cache M1 renovada" in message for message in logger.messages))

    def test_run_test_retries_model4_empty_report_even_without_kick_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            logger = ListLogger()
            settings = run_tests.TesterSettings(
                mt5_path=root / "terminal64.exe",
                delay_seconds=0,
                portable=False,
                data_dir=None,
                tester_kick_after_seconds=0,
                terminal_cooldown_seconds=0,
            )
            first_report = root / "attempt1.htm"
            second_report = root / "attempt2.htm"
            first_report.write_text("empty", encoding="utf-8")
            second_report.write_text("valid", encoding="utf-8")
            first_process = Mock(pid=101)
            second_process = Mock(pid=102)

            with (
                patch.object(run_tests, "tester_model_from_ini", return_value="4"),
                patch.object(run_tests.subprocess, "Popen", side_effect=[first_process, second_process]) as popen,
                patch.object(
                    run_tests,
                    "wait_for_mt5_process",
                    side_effect=[(0, False, 1.0), (0, False, 2.0)],
                ),
                patch.object(run_tests, "delete_existing_report_files"),
                patch.object(
                    run_tests,
                    "find_report_files",
                    side_effect=[[first_report], [second_report]],
                ),
                patch.object(run_tests, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(
                    run_tests,
                    "model4_report_has_empty_tester_data",
                    side_effect=[True, False],
                ),
                patch.object(run_tests, "copy_reports_to_project", return_value=[second_report]) as copy_reports,
                patch.object(run_tests, "write_tester_journal_sidecars"),
                patch.object(run_tests, "prepare_model4_history_preflight", return_value=[]) as preflight,
                patch.object(run_tests, "finish_model4_history_preflight") as finish_preflight,
                patch.object(run_tests, "log_ini_content"),
                patch.object(run_tests.time, "sleep"),
                patch.object(run_tests._WATCHDOG_RESTART_LIMITER, "wait_for_turn") as retry_wait,
            ):
                exit_code = run_tests.run_test(
                    root / "tester.ini",
                    root / "report",
                    settings,
                    False,
                    logger,
                    [],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(preflight.call_count, 2)
            self.assertEqual(finish_preflight.call_count, 2)
            retry_wait.assert_not_called()
            copy_reports.assert_called_once()
            self.assertEqual(copy_reports.call_args.args[0], [second_report])
            self.assertTrue(any("0 barras / 0 ticks" in message for message in logger.messages))

    def test_run_test_retries_model1_normal_exit_without_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            logger = ListLogger()
            settings = run_tests.TesterSettings(
                mt5_path=root / "terminal64.exe", delay_seconds=0, portable=False, data_dir=None,
                tester_kick_after_seconds=0, tester_stall_after_seconds=0,
                tester_max_runtime_seconds=0, terminal_cooldown_seconds=0,
            )
            report = root / "attempt2.htm"
            report.write_text("valid", encoding="utf-8")
            first_process = Mock(pid=101)
            second_process = Mock(pid=102)

            with (
                patch.object(run_tests, "tester_model_from_ini", return_value="1"),
                patch.object(run_tests.subprocess, "Popen", side_effect=[first_process, second_process]) as popen,
                patch.object(run_tests, "wait_for_mt5_process", side_effect=[(0, False, 1.0), (0, False, 2.0)]),
                patch.object(run_tests, "delete_existing_report_files") as delete_reports,
                patch.object(run_tests, "find_report_files", side_effect=[[], [report]]),
                patch.object(run_tests, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(run_tests, "copy_reports_to_project", return_value=[report]),
                patch.object(run_tests, "write_tester_journal_sidecars"),
                patch.object(run_tests, "finish_model4_history_preflight"),
                patch.object(run_tests, "log_ini_content"),
                patch.object(run_tests.time, "sleep"),
                patch.object(run_tests._WATCHDOG_RESTART_LIMITER, "wait_for_turn") as retry_wait,
            ):
                exit_code = run_tests.run_test(
                    root / "tester.ini", root / "report", settings, False, logger, [],
                    protected_set_name="candidate.set",
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(delete_reports.call_count, 2)
            self.assertTrue(all(call.kwargs["protected_set_name"] == "candidate.set" for call in delete_reports.call_args_list))
            retry_wait.assert_called_once_with(logger, "Reintento sin reporte")
            self.assertTrue(any("No se encontro reporte en Model=1" in message for message in logger.messages))

    def test_wait_closes_mt5_when_completed_report_stops_changing(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        process.pid = 123
        logger = ListLogger()
        signature = (("report.htm", 1024, 1),)
        clock = iter([0, 0, 0, 1, 1, 2, 2, 3, 3])

        with (
            patch.object(run_tests.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests.time, "sleep"),
            patch.object(run_tests, "fresh_report_signature", return_value=signature),
            patch.object(run_tests, "REPORT_SAVE_CHECK_INTERVAL", 0),
            patch.object(run_tests, "terminate_process_tree") as terminate,
        ):
            exit_code, restarted, _elapsed = run_tests.wait_for_mt5_process(
                process,
                logger,
                tester_log_dirs=[],
                report_path=run_tests.Path("report"),
                mt5_path=run_tests.Path("terminal64.exe"),
                report_stable_seconds=2,
            )

        self.assertEqual(exit_code, 0)
        self.assertFalse(restarted)
        terminate.assert_called_once_with(process, logger)
        self.assertTrue(any("se conserva el resultado" in message for message in logger.messages))

    def test_wait_restarts_model1_after_journal_stops_progressing(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        process.returncode = None
        logger = ListLogger()
        journal = run_tests.Path("Tester") / "logs" / "current.log"
        clock = iter([0, 0, 10, 20, 30, 40])

        with (
            patch.object(run_tests.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests.time, "sleep"),
            patch.object(run_tests, "find_tester_journal_log", return_value=journal),
            patch.object(run_tests, "read_tester_journal_tail", return_value=("testing started", 100)),
            patch.object(run_tests, "terminate_process_tree") as terminate,
        ):
            exit_code, restarted, elapsed = run_tests.wait_for_mt5_process(
                process,
                logger,
                stall_after_seconds=20,
                tester_model="1",
                tester_log_dirs=[run_tests.Path("data")],
                report_stable_seconds=0,
            )

        self.assertEqual(exit_code, 1)
        self.assertTrue(restarted)
        self.assertEqual(elapsed, 40)
        terminate.assert_called_once_with(process, logger)
        self.assertTrue(any("Model=1" in message and "sin progreso" in message for message in logger.messages))

    def test_wait_does_not_restart_model1_when_journal_resumes_progress(self) -> None:
        process = Mock()
        process.poll.side_effect = [None, None, None, None, 0]
        process.returncode = 0
        logger = ListLogger()
        journal = run_tests.Path("Tester") / "logs" / "current.log"
        clock = iter([0, 0, 10, 20, 30, 40])

        with (
            patch.object(run_tests.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests.time, "sleep"),
            patch.object(run_tests, "find_tester_journal_log", return_value=journal),
            patch.object(
                run_tests,
                "read_tester_journal_tail",
                side_effect=[
                    ("testing started", 100),
                    ("testing started", 100),
                    ("testing advanced", 200),
                ],
            ),
            patch.object(run_tests, "terminate_process_tree") as terminate,
        ):
            exit_code, restarted, elapsed = run_tests.wait_for_mt5_process(
                process,
                logger,
                stall_after_seconds=20,
                tester_model="1",
                tester_log_dirs=[run_tests.Path("data")],
                report_stable_seconds=0,
            )

        self.assertEqual(exit_code, 0)
        self.assertFalse(restarted)
        self.assertEqual(elapsed, 40)
        terminate.assert_not_called()

    def test_wait_enforces_absolute_runtime_without_tester_logs(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        process.returncode = None
        logger = ListLogger()
        clock = iter([0, 0, 10, 20, 30])

        with (
            patch.object(run_tests.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests.time, "sleep"),
            patch.object(run_tests, "terminate_process_tree") as terminate,
        ):
            exit_code, restarted, elapsed = run_tests.wait_for_mt5_process(
                process,
                logger,
                max_runtime_seconds=30,
                tester_model="1",
                tester_log_dirs=[],
                report_stable_seconds=0,
            )

        self.assertEqual(exit_code, 1)
        self.assertTrue(restarted)
        self.assertEqual(elapsed, 30)
        terminate.assert_called_once_with(process, logger)
        self.assertTrue(any("limite absoluto de 30s" in message for message in logger.messages))

    def test_run_test_applies_general_watchdog_to_model1_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            logger = ListLogger()
            settings = run_tests.TesterSettings(
                mt5_path=root / "terminal64.exe",
                delay_seconds=0,
                portable=False,
                data_dir=root / "data",
                tester_kick_after_seconds=30,
                tester_stall_after_seconds=20,
                tester_max_runtime_seconds=100,
                terminal_cooldown_seconds=0,
            )
            report = root / "report.htm"
            report.write_text("valid", encoding="utf-8")
            first_process = Mock(pid=101)
            second_process = Mock(pid=102)

            with (
                patch.object(run_tests, "tester_model_from_ini", return_value="1"),
                patch.object(run_tests.subprocess, "Popen", side_effect=[first_process, second_process]) as popen,
                patch.object(
                    run_tests,
                    "wait_for_mt5_process",
                    side_effect=[(1, True, 40.0), (0, False, 2.0)],
                ) as wait_process,
                patch.object(run_tests, "write_tester_journal_snapshot") as snapshot,
                patch.object(run_tests, "delete_existing_report_files"),
                patch.object(run_tests, "find_report_files", return_value=[report]),
                patch.object(run_tests, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(run_tests, "copy_reports_to_project", return_value=[report]),
                patch.object(run_tests, "write_tester_journal_sidecars"),
                patch.object(run_tests, "finish_model4_history_preflight"),
                patch.object(run_tests, "log_ini_content"),
                patch.object(run_tests.time, "sleep"),
                patch.object(run_tests._WATCHDOG_RESTART_LIMITER, "wait_for_turn") as retry_wait,
            ):
                exit_code = run_tests.run_test(
                    root / "tester.ini",
                    root / "report",
                    settings,
                    False,
                    logger,
                    [root / "data"],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(wait_process.call_count, 2)
            self.assertEqual(wait_process.call_args_list[0].kwargs["kick_after_seconds"], 0)
            self.assertEqual(wait_process.call_args_list[0].kwargs["stall_after_seconds"], 20)
            self.assertEqual(wait_process.call_args_list[0].kwargs["max_runtime_seconds"], 100)
            retry_wait.assert_called_once_with(logger, "Reinicio watchdog")
            snapshot.assert_called_once()
            self.assertTrue(any("Reintentando MT5 Model=1" in message for message in logger.messages))

    def test_watchdog_snapshot_is_saved_without_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            report_path = root / "reports" / "regression_000001"
            journal = root / "data" / "Tester" / "logs" / "20260730.log"
            journal.parent.mkdir(parents=True)
            journal.write_text("testing started\nwaiting for history\n", encoding="utf-16-le")
            logger = ListLogger()

            snapshot = run_tests.write_tester_journal_snapshot(
                report_path,
                [root / "data"],
                0.0,
                logger,
                label="watchdog_attempt_1",
            )

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertTrue(snapshot.exists())
            self.assertIn("waiting for history", snapshot.read_text(encoding="utf-8"))
            self.assertTrue(any("Diagnostico watchdog guardado" in message for message in logger.messages))

    def test_removes_copied_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            reports_dir = root / "reports"
            terminal_dir = root / "terminal"
            reports_dir.mkdir()
            terminal_dir.mkdir()
            source = terminal_dir / "sample.htm"
            source.write_text("report", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                copied = run_tests.copy_reports_to_project([source], logger)

            destination = reports_dir / source.name
            self.assertEqual(copied, [destination])
            self.assertEqual(destination.read_text(encoding="utf-8"), "report")
            self.assertFalse(source.exists())
            self.assertTrue(any("Reporte origen eliminado" in message for message in logger.messages))

    def test_keeps_local_project_report_when_it_was_generated_there(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reports_dir = run_tests.Path(temp_dir) / "reports"
            reports_dir.mkdir()
            source = reports_dir / "sample.htm"
            source.write_text("report", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                copied = run_tests.copy_reports_to_project([source], logger)

            self.assertEqual(copied, [source])
            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "report")
            self.assertTrue(any("Reporte ya estaba en reports" in message for message in logger.messages))

    def test_keeps_destination_when_external_report_overwrites_local_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            reports_dir = root / "reports"
            terminal_dir = root / "terminal"
            reports_dir.mkdir()
            terminal_dir.mkdir()
            local_source = reports_dir / "sample.htm"
            external_source = terminal_dir / "sample.htm"
            local_source.write_text("old", encoding="utf-8")
            external_source.write_text("new", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                copied = run_tests.copy_reports_to_project([local_source, external_source], logger)

            self.assertEqual(copied, [local_source])
            self.assertTrue(local_source.exists())
            self.assertEqual(local_source.read_text(encoding="utf-8"), "new")
            self.assertFalse(external_source.exists())

    def test_delete_existing_reports_keeps_active_tester_set_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            reports_dir = root / "reports"
            data_dir = root / "data"
            tester_dir = data_dir / "tester"
            reports_dir.mkdir()
            tester_dir.mkdir(parents=True)
            report_path = reports_dir / "sample"
            active_set = tester_dir / "sample.set"
            old_report = tester_dir / "sample.htm"
            active_set.write_text("params", encoding="utf-8")
            old_report.write_text("old", encoding="utf-8")
            logger = ListLogger()

            with patch.object(run_tests, "REPORT_DIR", reports_dir):
                run_tests.delete_existing_report_files(
                    report_path,
                    [data_dir],
                    root / "terminal64.exe",
                    logger,
                    protected_set_name=active_set.name,
                )

            self.assertTrue(active_set.exists())
            self.assertFalse(old_report.exists())
            self.assertTrue(any("Set activo conservado" in message for message in logger.messages))

    def test_recursive_set_loading_skips_run_auxiliary_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = run_tests.Path(temp_dir)
            wanted = run_dir / "gen_001" / "XAUUSD" / "H1" / "candidate.set"
            skipped_paths = [
                run_dir / "accepted_gen_001" / "score_10__candidate.set",
                run_dir / "retry_mismatch" / "run_1_all" / "candidate.set",
                run_dir / "robustness" / "run_1_pending" / "candidate.set",
                run_dir / "final_tick" / "run_1" / "real_tick_sets" / "candidate.set",
            ]
            for path in [wanted, *skipped_paths]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("set", encoding="utf-8")

            loaded = run_tests.load_set_files(run_dir, None, recursive=True)

            self.assertEqual(loaded, [wanted])

    def test_create_ini_can_override_tester_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            configs_dir = root / "configs"
            reports_dir = root / "reports"
            configs_dir.mkdir()
            reports_dir.mkdir()
            template = configparser.ConfigParser(interpolation=None)
            template.optionxform = str
            template.read_dict({"Tester": {"Expert": "", "Symbol": "XAUUSD", "Period": "H1", "Model": "1"}})

            with patch.object(run_tests, "CONFIG_DIR", configs_dir), patch.object(run_tests, "REPORT_DIR", reports_dir):
                ini_path, _report_path = run_tests.create_ini(
                    "Ultimate Breakout System_4.3.ex5",
                    1,
                    template,
                    tester_model="4",
                )

            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read(ini_path, encoding="utf-8")
            self.assertEqual(parser["Tester"]["Model"], "4")

    def test_mapped_set_text_applies_symbol_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            set_file = root / "seed.set"
            set_file.write_text(
                "\n".join([
                    "ForceSymbol=XAUUSD||1||0||2||N",
                    "Symbol=EURUSD",
                ]),
                encoding="utf-8",
            )

            text, changes = run_tests.mapped_set_text_for_tester(set_file, {}, ".sa")

            self.assertIsNotNone(text)
            self.assertIn("ForceSymbol=XAUUSD.sa||1||0||2||N", text)
            self.assertIn("Symbol=EURUSD.sa", text)
            self.assertIn("ForceSymbol: XAUUSD -> XAUUSD.sa", changes)
            self.assertIn("Symbol: EURUSD -> EURUSD.sa", changes)

    def test_mapped_set_text_uses_universe_specific_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            assets = root / "axi_assets.ini"
            assets.write_text(
                "\n".join([
                    "[Indices]",
                    "symbols=DAX40.fs,US500.sa",
                    "[Stocks]",
                    "symbols=Apple+",
                ]),
                encoding="utf-8",
            )
            set_file = root / "seed.set"
            set_file.write_text(
                "\n".join([
                    "ForceSymbol=DAX40||1||0||2||N",
                    "Symbol=Apple",
                ]),
                encoding="utf-8",
            )
            suffix_universe = run_tests.load_symbol_suffix_universe(assets, ".sa", ".fs", "+")

            text, changes = run_tests.mapped_set_text_for_tester(
                set_file,
                {},
                ".sa",
                ".fs",
                "+",
                suffix_universe,
            )

            self.assertIsNotNone(text)
            self.assertIn("ForceSymbol=DAX40.fs||1||0||2||N", text)
            self.assertIn("Symbol=Apple+", text)
            self.assertIn("ForceSymbol: DAX40 -> DAX40.fs", changes)
            self.assertIn("Symbol: Apple -> Apple+", changes)

    def test_symbol_map_preserves_explicit_broker_suffix(self) -> None:
        symbol_map = run_tests.parse_symbol_map("NAS100=USTECH,WTI=USOIL")

        self.assertEqual(run_tests.apply_symbol_map("NAS100.fs", symbol_map), "NAS100.fs")
        self.assertEqual(run_tests.apply_symbol_map("WTI.fs", symbol_map), "WTI.fs")
        self.assertEqual(run_tests.apply_symbol_map("Apple+", symbol_map), "Apple+")

    def test_axi_ustec_alias_resolves_to_cash_symbol(self) -> None:
        from ubs.account import default_symbol_map_for_broker

        symbol_map = run_tests.parse_symbol_map(default_symbol_map_for_broker("AXI"))
        suffix_universe = run_tests.load_symbol_suffix_universe(
            run_tests.Path("assets/axi_assets.ini"),
            ".sa",
            ".fs",
            "+",
        )

        mapped = run_tests.apply_symbol_map("USTEC", symbol_map)
        resolved = run_tests.apply_symbol_suffix(mapped, ".sa", ".fs", "+", suffix_universe)

        self.assertEqual(mapped, "USTECH")
        self.assertEqual(resolved, "USTECH.sa")

    def test_create_ini_fills_required_tester_defaults_when_template_has_blanks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            configs_dir = root / "configs"
            reports_dir = root / "reports"
            configs_dir.mkdir()
            reports_dir.mkdir()
            template = configparser.ConfigParser(interpolation=None)
            template.optionxform = str
            template.read_dict({
                "Tester": {
                    "Expert": "",
                    "Symbol": "XAUUSD",
                    "Period": "H1",
                    "Model": "1",
                    "Deposit": "",
                    "Currency": "",
                    "Leverage": "",
                    "Optimization": "",
                    "Visual": "",
                    "ReplaceReport": "",
                    "ShutdownTerminal": "",
                }
            })

            with patch.object(run_tests, "CONFIG_DIR", configs_dir), patch.object(run_tests, "REPORT_DIR", reports_dir):
                ini_path, _report_path = run_tests.create_ini(
                    "Ultimate Breakout System_4.3.ex5",
                    1,
                    template,
                )

            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read(ini_path, encoding="utf-8")
            self.assertEqual(parser["Tester"]["Deposit"], "1000")
            self.assertEqual(parser["Tester"]["Currency"], "EUR")
            self.assertEqual(parser["Tester"]["Leverage"], "1:500")
            self.assertEqual(parser["Tester"]["Optimization"], "0")
            self.assertEqual(parser["Tester"]["Visual"], "0")
            self.assertEqual(parser["Tester"]["ReplaceReport"], "1")
            self.assertEqual(parser["Tester"]["ShutdownTerminal"], "1")

    def test_multiterminal_ubs_profile_accepts_ubs_name_without_exact_expected_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            profile = run_tests.TerminalProfile(
                name="MT5",
                mt5_path=root / "terminal64.exe",
                data_dir=None,
                experts_root=root / "MQL5" / "Experts",
                ubs_ex5_file=root / "MQL5" / "Experts" / "Advisors" / "UBS" / "Ultimate Breakout System_4.3.ex5",
                portable=False,
            )
            errors = run_tests.validate_terminal_profiles(
                [profile],
                [run_tests.BacktestJob(1, "", root / "candidate.set")],
                set_mode=True,
                dry_run=True,
            )

            self.assertEqual(errors, [])

    def test_multiterminal_ubs_profile_allows_same_relative_expert_in_different_terminal_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            profile = run_tests.TerminalProfile(
                name="MT5",
                mt5_path=root / "TerminalB" / "terminal64.exe",
                data_dir=None,
                experts_root=root / "TerminalB" / "MQL5" / "Experts",
                ubs_ex5_file=(
                    root
                    / "TerminalB"
                    / "MQL5"
                    / "Experts"
                    / "Advisors"
                    / "Ultimate Breakout System_4.3_fix @LifeInDreamsWorld.ex5"
                ),
                portable=False,
            )

            errors = run_tests.validate_terminal_profiles(
                [profile],
                [run_tests.BacktestJob(1, "", root / "candidate.set")],
                set_mode=True,
                dry_run=True,
            )

            self.assertEqual(errors, [])

    def test_multiterminal_config_loads_only_selected_broker_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            config = root / "ui_settings.ini"
            config.write_text(
                "\n".join(
                    [
                        "[Multiterminal]",
                        "broker=AXI",
                        "[Terminal.1]",
                        "enabled=1",
                        "broker=ROBOFOREX",
                        "name=Robo",
                        f"mt5_path={root / 'Robo' / 'terminal64.exe'}",
                        f"experts_root={root / 'Robo' / 'MQL5' / 'Experts'}",
                        "[Terminal.2]",
                        "enabled=1",
                        "broker=AXI",
                        "name=Axi",
                        f"mt5_path={root / 'Axi' / 'terminal64.exe'}",
                        f"experts_root={root / 'Axi' / 'MQL5' / 'Experts'}",
                    ]
                ),
                encoding="utf-8",
            )

            profiles = run_tests.load_terminal_profiles(config)

            self.assertEqual([profile.name for profile in profiles], ["Axi"])

    def test_runner_tuning_loads_general_watchdog_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = run_tests.Path(temp_dir) / "ui_settings.ini"
            config.write_text(
                "\n".join(
                    [
                        "[Multiterminal]",
                        "tester_kick_after=45",
                        "tester_stall_after=420",
                        "tester_max_runtime=2400",
                        "terminal_cooldown=2",
                    ]
                ),
                encoding="utf-8",
            )

            values = run_tests.load_runner_tuning(
                config,
                tester_kick_after=None,
                tester_stall_after=None,
                tester_max_runtime=None,
                terminal_cooldown=None,
            )

            self.assertEqual(values, (45, 420, 2400, 2))

    def test_multiterminal_config_never_loads_disabled_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            config = root / "ui_settings.ini"
            config.write_text(
                "\n".join(
                    [
                        "[Multiterminal]",
                        "broker=ICTRADING",
                        "[Terminal.1]",
                        "enabled=1",
                        "broker=ICTRADING",
                        "name=IC enabled",
                        f"mt5_path={root / 'IC1' / 'terminal64.exe'}",
                        f"experts_root={root / 'IC1' / 'MQL5' / 'Experts'}",
                        "[Terminal.2]",
                        "enabled=0",
                        "broker=ICTRADING",
                        "name=IC disabled",
                        f"mt5_path={root / 'IC2' / 'terminal64.exe'}",
                        f"experts_root={root / 'IC2' / 'MQL5' / 'Experts'}",
                    ]
                ),
                encoding="utf-8",
            )

            profiles = run_tests.load_terminal_profiles(config)

            self.assertEqual([profile.name for profile in profiles], ["IC enabled"])


if __name__ == "__main__":
    unittest.main()
