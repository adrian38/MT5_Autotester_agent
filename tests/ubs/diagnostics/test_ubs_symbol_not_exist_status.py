import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ubs_agent
import ubs.agent.generation.universe
import ubs.agent.generation.sets
import ubs.agent.run.config
import ubs.agent.evaluation.robustness
import ubs.agent.evaluation.evaluate
import ubs.agent.evaluation.retry
import ubs.agent.config
from ubs.models import Seed, Variant


def make_variant(target_symbol: str, set_name: str = "candidate.set") -> Variant:
    seed = Seed(Path("seed.set"), "XAUUSD", "M30", "generic", "generic")
    return Variant(
        path=Path(set_name),
        seed=seed,
        target_symbol=target_symbol,
        target_period="M30",
        mutated_keys=(),
        missing_lot_keys=(),
        policy="test",
    )


def write_universe(path: Path, symbols: list[str]) -> None:
    path.write_text(
        "\n".join(["[Forex]", f"symbols={','.join(symbols)}", ""]),
        encoding="utf-8",
    )


class VariantSymbolNotOfferedTests(unittest.TestCase):
    def test_flags_symbol_absent_from_universe(self) -> None:
        universe = {"EEX.NYSE-24", "XAUUSD", "USTEC"}

        self.assertTrue(ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("EEX.NYSE"), universe, {}))
        self.assertTrue(ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("LBRDK.NAS"), universe, {}))

    def test_symbol_in_universe_is_never_flagged(self) -> None:
        universe = {"EEX.NYSE-24", "XAUUSD", "USTEC"}

        self.assertFalse(ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("XAUUSD"), universe, {}))
        self.assertFalse(ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("xauusd"), universe, {}))
        self.assertFalse(
            ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("EEX.NYSE-24"), universe, {})
        )
        # El mapa de simbolos del broker se aplica antes de comparar.
        self.assertFalse(
            ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("US100"), universe, {"US100": "USTEC"})
        )

    def test_empty_universe_and_empty_symbol_are_not_flagged(self) -> None:
        self.assertFalse(ubs.agent.generation.universe.variant_symbol_not_offered(make_variant("EEX.NYSE"), set(), {}))
        self.assertFalse(ubs.agent.generation.universe.variant_symbol_not_offered(make_variant(""), {"XAUUSD"}, {}))


class EvaluateVariantWithoutReportTests(unittest.TestCase):
    def test_records_symbol_not_exist_for_retired_symbol(self) -> None:
        memory = Mock()
        variant = make_variant("EEX.NYSE")

        with patch.object(ubs.agent.evaluation.evaluate, "find_report_for_set", return_value=None):
            status, result = ubs.agent.evaluation.evaluate.evaluate_variant(
                memory,
                variant,
                Mock(),
                {},
                "ICTRADING",
                universe_symbols={"XAUUSD", "EEX.NYSE-24"},
            )

        self.assertEqual(status, ubs.agent.config.SYMBOL_NOT_EXIST_STATUS)
        self.assertIsNone(result)
        memory.record_score.assert_called_once_with(
            variant.path, None, ubs.agent.config.SYMBOL_NOT_EXIST_STATUS, None
        )

    def test_manually_disabled_symbol_stays_retryable(self) -> None:
        """Deshabilitar un simbolo a mano no lo saca del universo del broker.

        Ese candidato debe seguir como no_report (retryable) para que la
        reparacion lo vuelva a intentar; solo la ausencia del universo significa
        que el broker lo retiro."""
        memory = Mock()
        variant = make_variant("USDRUB")

        with patch.object(ubs.agent.evaluation.evaluate, "find_report_for_set", return_value=None):
            status, _result = ubs.agent.evaluation.evaluate.evaluate_variant(
                memory,
                variant,
                Mock(),
                {},
                "ICTRADING",
                universe_symbols={"XAUUSD", "USDRUB"},
            )

        self.assertEqual(status, "no_report")
        memory.record_score.assert_called_once_with(variant.path, None, "no_report", None)

    def test_without_universe_nothing_is_marked_terminal(self) -> None:
        memory = Mock()

        with patch.object(ubs.agent.evaluation.evaluate, "find_report_for_set", return_value=None):
            status, _result = ubs.agent.evaluation.evaluate.evaluate_variant(
                memory,
                make_variant("EEX.NYSE"),
                Mock(),
                {},
                "ICTRADING",
            )

        self.assertEqual(status, "no_report")

    def test_status_is_not_retryable(self) -> None:
        self.assertNotIn(ubs.agent.config.SYMBOL_NOT_EXIST_STATUS, ubs.agent.config.FINAL_TICK_RETRYABLE_STATUSES)
        self.assertNotIn(
            ubs.agent.config.SYMBOL_NOT_EXIST_STATUS,
            ubs.agent.config.FINAL_TICK_DATE_RETRYABLE_STATUSES,
        )


class AliasTargetsAreNotRetiredTests(unittest.TestCase):
    """Un candidato puede llevar el alias como target_symbol (US100, CRUDEOIL...).

    El .ini se genera con el nombre canonico, asi que el backtest corre; si el
    universo no incluyera las claves de alias, esos candidatos acabarian con el
    estado terminal en cuanto se quedaran sin reporte por un fallo transitorio."""

    def test_alias_keys_are_part_of_the_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets = Path(temp_dir) / "assets.ini"
            assets.write_text(
                "\n".join(
                    [
                        "[Indices]",
                        "symbols=USTEC,US500",
                        "",
                        "[CommonAliases]",
                        "US100=USTEC",
                        "CRUDEOIL=XTIUSD",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

            universe = ubs.agent.generation.universe.broker_universe_symbols(argparse.Namespace(assets=str(assets)))

            self.assertEqual(universe, {"USTEC", "US500", "US100", "CRUDEOIL"})
            ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

    def test_alias_target_is_never_flagged_with_empty_symbol_map(self) -> None:
        # symbol_map vacio es el caso real de ICTrading (symbol_map_enabled=0).
        universe = {"USTEC", "XTIUSD", "US100", "CRUDEOIL"}

        for alias in ("US100", "CRUDEOIL"):
            with self.subTest(alias=alias):
                self.assertFalse(
                    ubs.agent.generation.universe.variant_symbol_not_offered(make_variant(alias), universe, {})
                )


class MissingReportStatusTests(unittest.TestCase):
    """Estado que graban las etapas (robustez, final tick, seeds, regresiva)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.assets = Path(self.temp.name) / "assets.ini"
        write_universe(self.assets, ["XAUUSD", "USDRUB"])
        self.args = argparse.Namespace(assets=str(self.assets), symbol_map="")
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

    def tearDown(self) -> None:
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()
        self.temp.cleanup()

    def test_retired_symbol_gets_terminal_status(self) -> None:
        self.assertEqual(
            ubs.agent.generation.universe.missing_report_status("EEX.NYSE", self.args),
            ubs.agent.config.SYMBOL_NOT_EXIST_STATUS,
        )

    def test_live_and_manually_disabled_symbols_stay_retryable(self) -> None:
        self.assertEqual(ubs.agent.generation.universe.missing_report_status("XAUUSD", self.args), "no_report")
        # USDRUB esta deshabilitado a mano en IC pero sigue en el universo.
        self.assertEqual(ubs.agent.generation.universe.missing_report_status("USDRUB", self.args), "no_report")

    def test_without_universe_stays_retryable(self) -> None:
        self.assertEqual(
            ubs.agent.generation.universe.missing_report_status("EEX.NYSE", argparse.Namespace(assets="")),
            "no_report",
        )

    def test_broken_symbol_map_does_not_raise(self) -> None:
        args = argparse.Namespace(assets=str(self.assets), symbol_map="esto=no=es=valido,,")
        self.assertIn(
            ubs.agent.generation.universe.missing_report_status("XAUUSD", args),
            {"no_report", ubs.agent.config.SYMBOL_NOT_EXIST_STATUS},
        )


class SplitRetiredSymbolsTests(unittest.TestCase):
    """Las etapas no deben encolar jobs de simbolos fuera del universo."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        assets = Path(self.temp.name) / "assets.ini"
        write_universe(assets, ["XAUUSD", "USDRUB"])
        self.args = argparse.Namespace(assets=str(assets), symbol_map="")
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

    def tearDown(self) -> None:
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()
        self.temp.cleanup()

    def test_splits_plain_rows(self) -> None:
        rows = [
            {"id": 1, "target_symbol": "XAUUSD"},
            {"id": 2, "target_symbol": "EEX.NYSE"},
            {"id": 3, "target_symbol": "USDRUB"},
        ]

        kept, retired = ubs.agent.generation.universe.split_retired_symbols(rows, self.args)

        self.assertEqual([row["id"] for row in kept], [1, 3])
        self.assertEqual([row["id"] for row in retired], [2])

    def test_splits_row_path_pairs(self) -> None:
        pairs = [
            ({"id": 1, "target_symbol": "EEX.NYSE"}, Path("a.set")),
            ({"id": 2, "target_symbol": "XAUUSD"}, Path("b.set")),
        ]

        kept, retired = ubs.agent.generation.universe.split_retired_symbols(
            pairs, self.args, row_of=lambda item: item[0]
        )

        self.assertEqual([row["id"] for row, _path in kept], [2])
        self.assertEqual([row["id"] for row in retired], [1])

    def test_empty_universe_keeps_everything(self) -> None:
        rows = [{"id": 1, "target_symbol": "EEX.NYSE"}]

        kept, retired = ubs.agent.generation.universe.split_retired_symbols(rows, argparse.Namespace(assets=""))

        self.assertEqual(kept, rows)
        self.assertEqual(retired, [])

    def test_summary_groups_by_symbol(self) -> None:
        retired = [
            {"target_symbol": "EEX.NYSE"},
            {"target_symbol": "EEX.NYSE"},
            {"target_symbol": "Corn_U6"},
        ]

        self.assertEqual(
            ubs.agent.generation.universe.format_retired_symbol_rows(retired), "Corn_U6 x1, EEX.NYSE x2"
        )


class RegressionRuntimeHookTests(unittest.TestCase):
    def test_hook_reports_terminal_status_only_for_retired_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets = Path(temp_dir) / "assets.ini"
            write_universe(assets, ["XAUUSD"])
            args = argparse.Namespace(assets=str(assets), symbol_map="")
            ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

            runtime = ubs.agent.run.config.regression_runtime(args)

            self.assertIsNotNone(runtime.missing_report_status)
            self.assertEqual(runtime.missing_report_status("XAUUSD"), "no_report")
            self.assertEqual(
                runtime.missing_report_status("EEX.NYSE"), ubs.agent.config.SYMBOL_NOT_EXIST_STATUS
            )
            ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

    def test_runtime_without_args_keeps_previous_behaviour(self) -> None:
        self.assertIsNone(ubs.agent.run.config.regression_runtime().missing_report_status)

    def test_runtime_exposes_the_stage_copy_that_repairs_spelling(self) -> None:
        self.assertIs(ubs.agent.run.config.regression_runtime().write_stage_set, ubs.agent.generation.sets.write_retry_set)




class StageRetrySetsTests(unittest.TestCase):
    def test_status_is_terminal_in_every_retry_set(self) -> None:
        from manager_node_runtime import node
        from ubs.regression_rules import REGRESSION_RETRYABLE_STATUSES

        status = ubs.agent.config.SYMBOL_NOT_EXIST_STATUS
        for name, retryable in (
            ("agent final tick", ubs.agent.config.FINAL_TICK_RETRYABLE_STATUSES),
            ("agent final tick date", ubs.agent.config.FINAL_TICK_DATE_RETRYABLE_STATUSES),
            ("agent robustness", ubs.agent.evaluation.robustness.ROBUST_RETRYABLE_STATUSES),
            ("regression rules", REGRESSION_RETRYABLE_STATUSES),
            ("node robustness", node.ROBUST_RETRYABLE_STATUSES),
            ("node final tick", node.FINAL_TICK_RETRYABLE_STATUSES),
        ):
            with self.subTest(retry_set=name):
                self.assertNotIn(status, retryable)


class MismatchRetryRetiredSymbolTests(unittest.TestCase):
    """El retry de problemas tecnicos tampoco encola simbolos retirados.

    Era la unica etapa sin el filtro: la copia de etapa es estricta en ICTrading
    y abortaba la pasada entera del auditor con un ValueError al no poder
    resolver la ortografia MT5 de un nombre que ya no esta en el universo."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        assets = root / "assets.ini"
        write_universe(assets, ["XAUUSD", "USDRUB"])
        self.args = argparse.Namespace(
            assets=str(assets),
            symbol_map="",
            broker="ICTRADING",
            expert=True,
            multi_terminal=False,
            dry_run=False,
            retry_run_id=7,
        )
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        self.rows = []
        for candidate_id, symbol in ((1, "SSSS.NAS"), (2, "XAUUSD")):
            set_path = self.run_dir / f"candidate_{candidate_id}.set"
            set_path.write_text("ForceSymbol=XAUUSD\n", encoding="utf-8")
            self.rows.append(
                {
                    "id": candidate_id,
                    "run_id": 7,
                    "generation": 2,
                    "target_symbol": symbol,
                    "set_path": str(set_path),
                }
            )
        self.memory = Mock()
        self.memory.run_by_id.return_value = {"id": 7, "output_dir": str(self.run_dir)}
        self.memory.mismatch_candidates_for_run.return_value = self.rows
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

    def tearDown(self) -> None:
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()
        self.temp.cleanup()

    def test_retired_candidate_is_marked_and_not_prepared(self) -> None:
        prepared = Mock(return_value=None)
        with patch.object(ubs.agent.evaluation.retry, "_prepare_mismatch_retry", prepared), \
             patch.object(ubs.agent.evaluation.retry, "recreate_work_dir", side_effect=lambda path: path):
            ubs.agent.evaluation.retry.retry_run_mismatches(self.args, self.memory, Mock())

        self.memory.record_score.assert_called_once_with(
            Path(self.rows[0]["set_path"]), None, ubs.agent.config.SYMBOL_NOT_EXIST_STATUS, None
        )
        rows_with_paths = prepared.call_args.args[1]
        self.assertEqual([row["id"] for row, _set_path in rows_with_paths], [2])

    def test_batch_of_only_retired_candidates_does_not_launch_mt5(self) -> None:
        self.memory.mismatch_candidates_for_run.return_value = [self.rows[0]]
        run_backtests = Mock()
        with patch.object(ubs.agent.evaluation.retry, "run_backtests", run_backtests), \
             patch.object(ubs.agent.evaluation.retry, "recreate_work_dir", side_effect=lambda path: path):
            code = ubs.agent.evaluation.retry.retry_run_mismatches(self.args, self.memory, Mock())

        # Cero: se ha resuelto lo que habia que resolver, no es un fallo de etapa.
        self.assertEqual(code, 0)
        run_backtests.assert_not_called()

    def test_single_candidate_retry_stops_before_the_stage_copy(self) -> None:
        self.memory.candidate_by_id.return_value = self.rows[0]
        write_retry_set = Mock()
        with patch.object(ubs.agent.evaluation.retry, "write_retry_set", write_retry_set):
            code = ubs.agent.evaluation.retry._retry_single_candidate(
                1, self.args, self.memory, Mock()
            )

        self.assertEqual(code, 0)
        write_retry_set.assert_not_called()
        self.memory.record_score.assert_called_once_with(
            Path(self.rows[0]["set_path"]), None, ubs.agent.config.SYMBOL_NOT_EXIST_STATUS, None
        )

    def test_full_rerun_skips_retired_without_touching_its_score(self) -> None:
        """Reprobar completo arrastra aceptados: apartarlos no debe puntuarlos."""
        self.memory.candidates_for_run.return_value = self.rows
        self.args.retry_candidate_id = []

        rows_with_paths, _requested = ubs.agent.evaluation.retry._retry_full_rows(
            self.args, self.memory, 7
        )

        self.assertEqual([row["id"] for row, _set_path in rows_with_paths], [2])
        self.memory.record_score.assert_not_called()


class BrokerUniverseSymbolsTests(unittest.TestCase):
    def test_reads_assets_ini_and_refreshes_on_mtime_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assets = Path(temp_dir) / "ictrading_assets.ini"
            write_universe(assets, ["EURUSD", "usdrub"])
            args = argparse.Namespace(assets=str(assets))
            ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

            self.assertEqual(ubs.agent.generation.universe.broker_universe_symbols(args), {"EURUSD", "USDRUB"})
            # Cache hit con el mismo mtime.
            self.assertEqual(ubs.agent.generation.universe.broker_universe_symbols(args), {"EURUSD", "USDRUB"})

            write_universe(assets, ["EURUSD"])
            stat = assets.stat()
            os.utime(assets, (stat.st_atime, stat.st_mtime + 10))

            self.assertEqual(ubs.agent.generation.universe.broker_universe_symbols(args), {"EURUSD"})
            ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()

    def test_missing_or_unset_assets_yields_empty_set(self) -> None:
        ubs.agent.generation.universe._BROKER_UNIVERSE_SYMBOLS_CACHE.clear()
        self.assertEqual(ubs.agent.generation.universe.broker_universe_symbols(argparse.Namespace(assets="")), set())
        self.assertEqual(ubs.agent.generation.universe.broker_universe_symbols(argparse.Namespace()), set())
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "nope.ini"
            self.assertEqual(
                ubs.agent.generation.universe.broker_universe_symbols(argparse.Namespace(assets=str(missing))), set()
            )


if __name__ == "__main__":
    unittest.main()
