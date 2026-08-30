import argparse
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_tests


class ListLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)


def write_ini(path, symbol: str, model: str = "1") -> None:
    path.write_text(
        "\n".join(
            [
                "[Tester]",
                "Expert=Advisors\\EA",
                f"Symbol={symbol}",
                "Period=M30",
                f"Model={model}",
            ]
        ),
        encoding="utf-8",
    )


class TesterAbortCodeTests(unittest.TestCase):
    def test_translates_unsigned_windows_code_for_missing_symbol(self) -> None:
        # 3294954938 es como Popen entrega el -1000012358 del journal.
        self.assertEqual(run_tests.signed_exit_code(3294954938), -1000012358)
        reason, transient = run_tests.mt5_tester_abort(3294954938)
        self.assertIn("Symbol", reason)
        self.assertFalse(transient)

    def test_not_synchronized_is_transient(self) -> None:
        reason, transient = run_tests.mt5_tester_abort(3294954934)
        self.assertIn("sincronizado", reason)
        self.assertTrue(transient)

    def test_normal_exit_codes_are_not_aborts(self) -> None:
        self.assertIsNone(run_tests.mt5_tester_abort(0))
        self.assertIsNone(run_tests.mt5_tester_abort(1))
        self.assertIsNone(run_tests.mt5_tester_abort(None))

    def test_missing_symbol_abort_does_not_retry_and_keeps_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            ini_path = root / "tester.ini"
            write_ini(ini_path, "LBRDK.NAS")
            logger = ListLogger()
            settings = run_tests.TesterSettings(
                mt5_path=root / "terminal64.exe",
                data_dir=None,
                portable=False,
                delay_seconds=0,
                tester_kick_after_seconds=0,
                tester_stall_after_seconds=0,
                tester_max_runtime_seconds=0,
                terminal_cooldown_seconds=0,
            )

            with (
                patch.object(run_tests.subprocess, "Popen", return_value=Mock(pid=1)) as popen,
                patch.object(run_tests, "wait_for_mt5_process", return_value=(3294954938, False, 5.8)),
                patch.object(run_tests, "delete_existing_report_files"),
                patch.object(run_tests, "write_tester_journal_snapshot") as snapshot,
                patch.object(run_tests, "log_ini_content"),
                patch.object(run_tests.time, "sleep"),
            ):
                exit_code = run_tests.run_test(
                    ini_path,
                    root / "report",
                    settings,
                    False,
                    logger,
                    [],
                )

            self.assertEqual(exit_code, 1)
            # Un simbolo inexistente es determinista: un solo arranque de MT5.
            self.assertEqual(popen.call_count, 1)
            snapshot.assert_called_once()
            self.assertTrue(any("LBRDK.NAS" in message for message in logger.messages))
            self.assertTrue(
                any("DIAG TESTER_ABORT" in message and "retry=no" in message for message in logger.messages)
            )

    def test_not_synchronized_abort_still_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            ini_path = root / "tester.ini"
            write_ini(ini_path, "XAUUSD")
            report = root / "report.htm"
            report.write_text("valid", encoding="utf-8")
            logger = ListLogger()
            settings = run_tests.TesterSettings(
                mt5_path=root / "terminal64.exe",
                data_dir=None,
                portable=False,
                delay_seconds=0,
                tester_kick_after_seconds=0,
                tester_stall_after_seconds=0,
                tester_max_runtime_seconds=0,
                terminal_cooldown_seconds=0,
            )

            with (
                patch.object(run_tests.subprocess, "Popen", side_effect=[Mock(pid=1), Mock(pid=2)]) as popen,
                patch.object(
                    run_tests,
                    "wait_for_mt5_process",
                    side_effect=[(3294954934, False, 45.0), (0, False, 30.0)],
                ),
                patch.object(run_tests, "delete_existing_report_files"),
                patch.object(run_tests, "write_tester_journal_snapshot"),
                patch.object(run_tests, "find_report_files", side_effect=[[], [report]]),
                patch.object(run_tests, "filter_fresh_report_files", side_effect=lambda paths, *_args: paths),
                patch.object(run_tests, "copy_reports_to_project", return_value=[report]),
                patch.object(run_tests, "write_tester_journal_sidecars"),
                patch.object(run_tests, "log_ini_content"),
                patch.object(run_tests.time, "sleep"),
                patch.object(run_tests._WATCHDOG_RESTART_LIMITER, "wait_for_turn"),
            ):
                exit_code = run_tests.run_test(
                    ini_path,
                    root / "report",
                    settings,
                    False,
                    logger,
                    [],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(popen.call_count, 2)


def write_universe(path, sections: dict[str, list[str]]) -> None:
    path.write_text(
        "\n".join(
            [
                line
                for section, symbols in sections.items()
                for line in (f"[{section}]", f"symbols={','.join(symbols)}", "")
            ]
        ),
        encoding="utf-8",
    )


class UniverseSkipTests(unittest.TestCase):
    def test_loads_every_section_except_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = run_tests.Path(temp_dir) / "assets.ini"
            path.write_text(
                "\n".join(
                    [
                        "[Forex]",
                        "symbols=EURUSD,GBPUSD",
                        "",
                        "[Stocks]",
                        "symbols=aapl.nas",
                        "",
                        "[CommonAliases]",
                        "US100=USTEC",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            universe = run_tests.load_universe_symbols(path)

            self.assertEqual(universe, {"EURUSD", "GBPUSD", "AAPL.NAS"})

    def test_missing_file_yields_empty_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(
                run_tests.load_universe_symbols(run_tests.Path(temp_dir) / "nope.ini"), set()
            )
            self.assertEqual(run_tests.load_universe_symbols(None), set())

    def test_flags_only_symbols_absent_from_the_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            retired = root / "retired.ini"
            alive = root / "alive.ini"
            write_ini(retired, "EEX.NYSE")
            write_ini(alive, "EEX.NYSE-24")
            universe = {"EEX.NYSE-24", "XAUUSD"}

            self.assertEqual(
                run_tests.ini_symbol_missing_from_universe(retired, universe), "EEX.NYSE"
            )
            self.assertEqual(run_tests.ini_symbol_missing_from_universe(alive, universe), "")

    def test_empty_universe_never_skips(self) -> None:
        # Sin inventario cargado es mejor gastar un arranque de MT5 que saltarse
        # un backtest valido.
        with tempfile.TemporaryDirectory() as temp_dir:
            ini = run_tests.Path(temp_dir) / "any.ini"
            write_ini(ini, "EEX.NYSE")

            self.assertEqual(run_tests.ini_symbol_missing_from_universe(ini, set()), "")

    def test_manually_disabled_symbol_still_runs(self) -> None:
        """Un simbolo deshabilitado a mano sigue en el universo: debe ejecutarse.

        La politica ubs_disabled_symbols_*.json solo frena la generacion de
        seeds; la reparacion de candidatos ya generados no la mira."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            ini = root / "manual.ini"
            write_ini(ini, "USDRUB")
            assets = root / "assets.ini"
            write_universe(assets, {"Forex": ["EURUSD", "USDRUB"]})

            universe = run_tests.load_universe_symbols(assets)

            self.assertIn("USDRUB", universe)
            self.assertEqual(run_tests.ini_symbol_missing_from_universe(ini, universe), "")

    def test_job_with_retired_symbol_never_opens_mt5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            ini_path = root / "candidate.ini"
            write_ini(ini_path, "EEX.NYSE")
            report_path = root / "candidate"
            logger = ListLogger()
            profile = run_tests.TerminalProfile(
                name="MT5_IC_1",
                mt5_path=root / "terminal64.exe",
                data_dir=None,
                experts_root=root / "MQL5" / "Experts",
                ubs_ex5_file=None,
                portable=False,
            )
            settings = run_tests.TesterSettings(
                mt5_path=profile.mt5_path,
                data_dir=None,
                portable=False,
                delay_seconds=0,
                tester_kick_after_seconds=0,
                tester_stall_after_seconds=0,
                tester_max_runtime_seconds=0,
                terminal_cooldown_seconds=0,
            )
            args = argparse.Namespace(
                symbol_suffix="",
                symbol_futures_suffix="",
                symbol_shares_suffix="",
                symbol_suffix_universe={},
                infer_tester_from_set=False,
                prefer_set_path_timeframe=False,
                model="",
                dry_run=False,
                universe_symbols={"EEX.NYSE-24", "XAUUSD"},
            )

            with (
                patch.object(run_tests, "terminal_data_dirs_for_profile", return_value=[]),
                patch.object(run_tests, "profile_expert_for_job", return_value="Advisors\\EA.ex5"),
                patch.object(run_tests, "create_ini", return_value=(ini_path, report_path)),
                patch.object(run_tests, "copy_set_file_to_tester_profiles") as copy_set,
                patch.object(run_tests, "run_test") as run_test,
                patch.object(run_tests, "delete_test_artifacts") as delete_artifacts,
            ):
                exit_code = run_tests.run_backtest_job(
                    run_tests.BacktestJob(1, "", root / "EEX.NYSE_M30_seed.set"),
                    profile,
                    settings,
                    Mock(),
                    args,
                    {},
                    logger,
                    set_mode=True,
                )

            self.assertEqual(exit_code, run_tests.SKIPPED_SYMBOL_EXIT_CODE)
            run_test.assert_not_called()
            copy_set.assert_not_called()
            delete_artifacts.assert_called_once()
            self.assertTrue(any("OMITIDO" in message for message in logger.messages))


if __name__ == "__main__":
    unittest.main()
