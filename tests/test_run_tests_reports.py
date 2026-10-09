import tempfile
import unittest
from unittest.mock import Mock, patch

import run_tests
import run_tests_runner
import run_tests_watchdog
from tests.run_tests_report_fixtures import ListLogger


def model4_retry_fixture(root):
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
    return settings, first_report, second_report


class CopyReportsToProjectTests(unittest.TestCase):
    def setUp(self):
        # Process inventory is covered separately; report tests never query MT5.
        release = patch.object(run_tests_runner, "wait_for_terminal_release")
        self.release = release.start()
        self.addCleanup(release.stop)

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
            settings, first_report, second_report = model4_retry_fixture(root)
            first_process = Mock(pid=101)
            second_process = Mock(pid=102)

            with (
                patch.object(run_tests_runner, "tester_model_from_ini", return_value="4"),
                patch.object(run_tests_runner.subprocess, "Popen", side_effect=[first_process, second_process]) as popen,
                patch.object(
                    run_tests_runner,
                    "wait_for_mt5_process",
                    side_effect=[(0, False, 1.0), (0, False, 2.0)],
                ),
                patch.object(run_tests_runner, "delete_existing_report_files"),
                patch.object(
                    run_tests_runner,
                    "find_report_files",
                    side_effect=[[first_report], [second_report]],
                ),
                patch.object(run_tests_runner, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(
                    run_tests_runner,
                    "report_has_empty_tester_data",
                    side_effect=[True, False],
                ),
                patch.object(run_tests_runner, "copy_reports_to_project", return_value=[second_report]) as copy_reports,
                patch.object(run_tests_runner, "write_tester_journal_sidecars"),
                patch.object(run_tests_runner, "prepare_model4_history_preflight", return_value=[]) as preflight,
                patch.object(run_tests_runner, "finish_model4_history_preflight") as finish_preflight,
                patch.object(run_tests_runner, "log_ini_content"),
                patch.object(run_tests_runner.time, "sleep"),
                patch.object(run_tests_runner._WATCHDOG_RESTART_LIMITER, "wait_for_turn") as retry_wait,
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
            self.assertEqual(self.release.call_count, 2)
            self.assertEqual(preflight.call_count, 2)
            self.assertEqual(finish_preflight.call_count, 2)
            retry_wait.assert_not_called()
            copy_reports.assert_called_once()
            self.assertEqual(copy_reports.call_args.args[0], [second_report])
            self.assertTrue(any("0 barras / 0 ticks" in message for message in logger.messages))

    def test_run_test_retries_model1_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            logger = ListLogger()
            settings, first_report, second_report = model4_retry_fixture(root)

            with (
                patch.object(run_tests_runner, "tester_model_from_ini", return_value="1"),
                patch.object(
                    run_tests_runner.subprocess, "Popen",
                    side_effect=[Mock(pid=101), Mock(pid=102)],
                ) as popen,
                patch.object(
                    run_tests_runner, "wait_for_mt5_process",
                    side_effect=[(0, False, 1.0), (0, False, 2.0)],
                ),
                patch.object(run_tests_runner, "delete_existing_report_files"),
                patch.object(
                    run_tests_runner, "find_report_files",
                    side_effect=[[first_report], [second_report]],
                ),
                patch.object(
                    run_tests_runner, "filter_fresh_report_files",
                    side_effect=lambda paths, *_args: paths,
                ),
                patch.object(
                    run_tests_runner, "report_has_empty_tester_data",
                    side_effect=[True, False],
                ),
                patch.object(
                    run_tests_runner, "copy_reports_to_project", return_value=[second_report],
                ) as copy_reports,
                patch.object(run_tests_runner, "write_tester_journal_sidecars"),
                patch.object(run_tests_runner, "prepare_model4_history_preflight") as preflight,
                patch.object(run_tests_runner, "finish_model4_history_preflight"),
                patch.object(run_tests_runner, "log_ini_content"),
                patch.object(run_tests_runner.time, "sleep"),
            ):
                exit_code = run_tests.run_test(
                    root / "tester.ini", root / "report", settings, False, logger, [],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(popen.call_count, 2)
            preflight.assert_not_called()
            copy_reports.assert_called_once_with([second_report], logger)
            self.assertTrue(any("Model=1" in message for message in logger.messages))

    def test_run_test_retries_model1_normal_exit_without_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            logger = ListLogger()
            settings = run_tests.TesterSettings(
                mt5_path=root / "terminal64.exe",
                delay_seconds=0,
                portable=False,
                data_dir=None,
                tester_kick_after_seconds=0,
                tester_stall_after_seconds=0,
                tester_max_runtime_seconds=0,
                terminal_cooldown_seconds=0,
            )
            report = root / "attempt2.htm"
            report.write_text("valid", encoding="utf-8")
            first_process = Mock(pid=101)
            second_process = Mock(pid=102)

            with (
                patch.object(run_tests_runner, "tester_model_from_ini", return_value="1"),
                patch.object(run_tests_runner.subprocess, "Popen", side_effect=[first_process, second_process]) as popen,
                patch.object(
                    run_tests_runner,
                    "wait_for_mt5_process",
                    side_effect=[(0, False, 1.0), (0, False, 2.0)],
                ),
                patch.object(run_tests_runner, "delete_existing_report_files") as delete_reports,
                patch.object(run_tests_runner, "find_report_files", side_effect=[[], [report]]),
                patch.object(run_tests_runner, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(run_tests_runner, "copy_reports_to_project", return_value=[report]),
                patch.object(run_tests_runner, "write_tester_journal_sidecars"),
                patch.object(run_tests_runner, "finish_model4_history_preflight"),
                patch.object(run_tests_runner, "log_ini_content"),
                patch.object(run_tests_runner.time, "sleep"),
                patch.object(run_tests_runner._WATCHDOG_RESTART_LIMITER, "wait_for_turn") as retry_wait,
            ):
                exit_code = run_tests.run_test(
                    root / "tester.ini",
                    root / "report",
                    settings,
                    False,
                    logger,
                    [],
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
            patch.object(run_tests_watchdog.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests_watchdog.time, "sleep"),
            patch.object(run_tests_watchdog, "fresh_report_signature", return_value=signature),
            patch.object(run_tests_watchdog, "REPORT_SAVE_CHECK_INTERVAL", 0),
            patch.object(run_tests_watchdog, "terminate_process_tree") as terminate,
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
            patch.object(run_tests_watchdog.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests_watchdog.time, "sleep"),
            patch.object(run_tests_watchdog, "find_tester_journal_log", return_value=journal),
            patch.object(run_tests_watchdog, "read_tester_journal_tail", return_value=("testing started", 100)),
            patch.object(run_tests_watchdog, "terminate_process_tree") as terminate,
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
            patch.object(run_tests_watchdog.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests_watchdog.time, "sleep"),
            patch.object(run_tests_watchdog, "find_tester_journal_log", return_value=journal),
            patch.object(
                run_tests_runner,
                "read_tester_journal_tail",
                side_effect=[
                    ("testing started", 100),
                    ("testing started", 100),
                    ("testing advanced", 200),
                ],
            ),
            patch.object(run_tests_watchdog, "terminate_process_tree") as terminate,
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
            patch.object(run_tests_watchdog.time, "time", side_effect=lambda: next(clock)),
            patch.object(run_tests_watchdog.time, "sleep"),
            patch.object(run_tests_watchdog, "terminate_process_tree") as terminate,
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
                patch.object(run_tests_runner, "tester_model_from_ini", return_value="1"),
                patch.object(run_tests_runner.subprocess, "Popen", side_effect=[first_process, second_process]) as popen,
                patch.object(
                    run_tests_runner,
                    "wait_for_mt5_process",
                    side_effect=[(1, True, 40.0), (0, False, 2.0)],
                ) as wait_process,
                patch.object(run_tests_runner, "write_tester_journal_snapshot") as snapshot,
                patch.object(run_tests_runner, "delete_existing_report_files"),
                patch.object(run_tests_runner, "find_report_files", return_value=[report]),
                patch.object(run_tests_runner, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(run_tests_runner, "copy_reports_to_project", return_value=[report]),
                patch.object(run_tests_runner, "write_tester_journal_sidecars"),
                patch.object(run_tests_runner, "finish_model4_history_preflight"),
                patch.object(run_tests_runner, "log_ini_content"),
                patch.object(run_tests_runner.time, "sleep"),
                patch.object(run_tests_runner._WATCHDOG_RESTART_LIMITER, "wait_for_turn") as retry_wait,
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
