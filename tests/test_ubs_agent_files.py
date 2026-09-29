import ubs_agent_evaluate
import ubs_agent_final_tick_rescore
import ubs_agent_config
import ubs_agent_final_tick
import ubs_agent_reports
import json
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs.memory import AgentMemory
from ubs_agent import (
    copy_seed_for_backtest,
    evaluate_variant_report,
    find_report_for_set,
    find_watchdog_snapshot_for_set,
    generation_random_stream,
    generation_feedback_terminal_stage,
    generation_fitness_target,
    generation_seed_fitness_predictions,
    reconcile_final_tick_reports,
    reconcile_final_tick_reports,
    paths_belong_to_workspace,
    report_matches_variant,
    select_next_generation_survivors,
)
from run_tests import normalize_set_symbol, parse_symbol_map
from tests.ubs_agent_files_fixtures import score


class UBSSetsFileTests(unittest.TestCase):
    def test_generation_fitness_targets_six_month_in_discovery_and_production(self) -> None:
        self.assertEqual(generation_fitness_target(True), "final_tick_6m")
        self.assertEqual(generation_fitness_target(False), "final_tick_6m")

    def test_discovery_feedback_stops_at_six_month_but_production_can_use_regression(self) -> None:
        self.assertEqual(generation_feedback_terminal_stage(True), "six_month")
        self.assertIsNone(generation_feedback_terminal_stage(False))

    def test_discovery_seed_fitness_uses_descendant_yield_but_production_uses_metrics(self) -> None:
        memory = Mock()
        memory.discovery_seed_descendant_predictions.return_value = {"discovery": Mock()}
        memory.seed_selection_predictions.return_value = {"production": Mock()}
        seeds = [Seed(Path("seed.set"), "XAUUSD", "H1", "generic", "1")]

        discovery = generation_seed_fitness_predictions(
            memory,
            seeds,
            run_id=7,
            force_unseeded_universe=True,
        )
        production = generation_seed_fitness_predictions(
            memory,
            seeds,
            run_id=7,
            force_unseeded_universe=False,
        )

        self.assertIn("discovery", discovery)
        self.assertIn("production", production)
        memory.discovery_seed_descendant_predictions.assert_called_once_with(
            seeds,
            exclude_run_id=7,
        )
        memory.seed_selection_predictions.assert_called_once_with(
            seeds,
            exclude_run_id=7,
            target="final_tick_6m",
        )

    def test_next_generation_survivors_forward_mode_specific_fitness_target(self) -> None:
        memory = Mock()
        memory.seed_selection_predictions.return_value = {}
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "generic", "generic")
        variant = Variant(Path("candidate.set"), seed, "XAUUSD", "H1", (), (), "exploit")

        selected = select_next_generation_survivors(
            memory,
            17,
            [(variant, score(150.0))],
            20.0,
            1,
            fitness_target="robustness",
        )

        self.assertEqual(len(selected), 1)
        memory.seed_selection_predictions.assert_called_once()
        self.assertEqual(
            memory.seed_selection_predictions.call_args.kwargs,
            {"exclude_run_id": 17, "target": "robustness"},
        )

    def test_generation_random_streams_isolate_routing_from_mutation_draws(self) -> None:
        selection_a = generation_random_stream(20260812, 1, "selection")
        mutation_a = generation_random_stream(20260812, 1, "mutation", 1, 1)
        first_a = selection_a.random()
        for _ in range(100):
            mutation_a.random()
        second_a = selection_a.random()

        selection_b = generation_random_stream(20260812, 1, "selection")
        self.assertEqual((first_a, second_a), (selection_b.random(), selection_b.random()))

    def test_generation_random_streams_are_generation_scoped(self) -> None:
        selection_1 = generation_random_stream(7, 1, "selection")
        mutation_1 = generation_random_stream(7, 1, "mutation", 1, 1)
        selection_1_again = generation_random_stream(7, 1, "selection")
        mutation_1_again = generation_random_stream(7, 1, "mutation", 1, 1)
        selection_2 = generation_random_stream(7, 2, "selection")
        mutation_2 = generation_random_stream(7, 2, "mutation", 1, 1)

        values_1 = (selection_1.random(), mutation_1.random())
        self.assertEqual(values_1, (selection_1_again.random(), mutation_1_again.random()))
        self.assertNotEqual(values_1, (selection_2.random(), mutation_2.random()))

    def test_generation_random_streams_isolate_adjacent_variants(self) -> None:
        first = generation_random_stream(11, 1, "mutation", 3, 1)
        second = generation_random_stream(11, 1, "mutation", 3, 2)
        expected_second = second.random()
        for _ in range(100):
            first.random()

        self.assertEqual(
            expected_second,
            generation_random_stream(11, 1, "mutation", 3, 2).random(),
        )

    def test_workspace_storage_detection_rejects_external_temp_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(
                paths_belong_to_workspace(
                    Path(temp_dir) / "out",
                    Path(temp_dir) / "memory.sqlite",
                )
            )

    def test_workspace_storage_detection_accepts_checkout_paths(self) -> None:
        from ubs_agent import BASE_DIR

        self.assertTrue(
            paths_belong_to_workspace(
                BASE_DIR / "outputs" / "ubs_agent",
                BASE_DIR / "outputs" / "ubs_memory.sqlite",
            )
        )

    def test_report_discovery_excludes_watchdog_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports = root / "reports"
            reports.mkdir()
            set_path = root / "candidate.set"
            snapshot = reports / "candidate.watchdog_attempt_1.mt5log.txt"
            snapshot.write_text("watchdog evidence", encoding="utf-8")

            with patch("ubs_agent_reports.BASE_DIR", root):
                self.assertIsNone(find_report_for_set(set_path))
                self.assertEqual(find_watchdog_snapshot_for_set(set_path), snapshot)

                report = reports / "candidate.HTM"
                report.write_text("<html></html>", encoding="utf-8")
                self.assertEqual(find_report_for_set(set_path), report)

    def test_final_tick_reconcile_uses_explicit_broker_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_set = root / "candidate.set"
            source_set.write_text("test", encoding="utf-8")
            ohlc_report = root / "ohlc6m_000001_candidate.htm"
            tick_report = root / "tick6m_000001_candidate.htm"
            row = {
                "id": 1,
                "set_path": str(source_set),
                "final_tick_status": "pending_history_quality",
            }
            memory = SimpleNamespace(
                active_final_tick_stage="probe",
                accepted_candidates_for_final_tick=Mock(return_value=[row]),
                record_candidate_final_tick=Mock(),
            )
            seed = Seed(
                path=source_set,
                symbol="S&P.fs",
                period="H1",
                family="test",
                run_strategy="1",
            )
            variant = Variant(
                path=source_set,
                seed=seed,
                target_symbol="S&P.fs",
                target_period="H1",
                mutated_keys=(),
                missing_lot_keys=(),
                policy="test",
            )
            def find_report(path: Path) -> Path:
                return tick_report if path.name.startswith("tick6m_") else ohlc_report

            # El mismo doble cubre los dos modulos que puntuan aqui.
            score_report = Mock(side_effect=[
                score(10.0, symbol="S&P.fs", timeframe="H1", trades=20),
                score(5.0, symbol="S&P.fs", timeframe="H1", trades=20)])
            with (
                patch("ubs_agent_final_tick_rescore.variant_from_candidate_row", return_value=variant),
                patch("ubs_agent_final_tick_rescore.find_report_for_set", side_effect=find_report),
                patch(
                    "ubs_agent_final_tick_rescore._read_ohlc_report_cfg_dates",
                    return_value=("2026.01.01", "2026.06.30"),
                ),
                patch("ubs_agent_final_tick_rescore.score_report_file", new=score_report),
                patch("ubs_agent_final_tick.score_report_file", new=score_report),
                patch("ubs_agent_final_tick_rescore.report_matches_variant", return_value=(True, "")),
                patch(
                    "ubs_agent_final_tick.final_tick_similarity",
                    return_value={"accepted": False, "reasons": ["profit_factor_floor"]},
                ),
            ):
                counts = reconcile_final_tick_reports(
                    memory,
                    30,
                    ScoreConfig(),
                    {},
                    broker="AXI",
                    final_tick_stage="six_month",
                )

            self.assertEqual(counts, {"rejected": 1})
            self.assertEqual(
                [call.kwargs["broker"] for call in score_report.call_args_list],
                ["AXI", "AXI"],
            )
            memory.record_candidate_final_tick.assert_called_once()

    def test_normalize_set_symbol_preserves_exchange_suffixes(self) -> None:
        self.assertEqual(normalize_set_symbol("WULF.NAS-24"), "WULF.NAS-24")
        self.assertEqual(normalize_set_symbol("DPZ.NAS"), "DPZ.NAS")
        self.assertEqual(normalize_set_symbol("UBER.NYSE"), "UBER.NYSE")
        self.assertEqual(normalize_set_symbol("EURUSD.a"), "EURUSD")
        self.assertEqual(normalize_set_symbol("COCOA.fs"), "COCOA")
        self.assertEqual(normalize_set_symbol("COCOA.FS"), "COCOA.FS")

    def test_uppercase_broker_suffix_matches_report_and_symbol_map(self) -> None:
        variant = Variant(
            path=Path("candidate.set"),
            seed=Seed(Path("seed.set"), "COCOA", "H3", "family", "1"),
            target_symbol="COCOA.FS",
            target_period="H3",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="test",
        )

        matches, reason = report_matches_variant(
            variant,
            score(0.0, symbol="COCOA.fs", timeframe="H3"),
            parse_symbol_map("COCOA=COCOA.fs"),
            broker="AXI",
        )

        self.assertTrue(matches, reason)

        other_broker_matches, _ = report_matches_variant(
            variant,
            score(0.0, symbol="COCOA.fs", timeframe="H3"),
            parse_symbol_map("COCOA=COCOA.fs"),
            broker="ICTRADING",
        )
        self.assertFalse(other_broker_matches)

    def test_report_match_preserves_ictrading_stock_symbol(self) -> None:
        variant = Variant(
            path=Path("candidate.set"),
            seed=Seed(Path("seed.set"), "BTCUSD", "M1", "family", "1"),
            target_symbol="WULF.NAS-24",
            target_period="M1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="test",
        )

        matches, reason = report_matches_variant(
            variant,
            score(0.0, symbol="WULF.NAS-24", timeframe="M1", trades=0),
            {},
        )

        self.assertTrue(matches, reason)

    def test_report_match_accepts_configured_symbol_suffix(self) -> None:
        variant = Variant(
            path=Path("candidate.set"),
            seed=Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1"),
            target_symbol="XAUUSD",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="test",
        )

        matches, reason = report_matches_variant(
            variant,
            score(0.0, symbol="XAUUSD.sa", timeframe="H1", trades=0),
            {},
            ".sa",
        )

        self.assertTrue(matches, reason)

    def test_report_match_keeps_explicit_axi_future_symbol(self) -> None:
        variant = Variant(
            path=Path("candidate.set"),
            seed=Seed(Path("seed.set"), "USTECH", "H1", "family", "1"),
            target_symbol="NAS100.fs",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="asset_unseeded_force",
        )
        symbol_map = parse_symbol_map("NAS100=USTECH")

        matches, reason = report_matches_variant(
            variant,
            score(0.0, symbol="NAS100.fs", timeframe="H1", trades=0),
            symbol_map,
            ".sa",
        )

        self.assertTrue(matches, reason)

    def test_seed_backtest_copy_writes_force_symbol_with_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "seed.set"
            destination = root / "eval.set"
            source.write_text(
                "\n".join([
                    "Run_Strategy=1||1||0||2||N",
                    "ST1_Timeframe=16385||0||0||49153||N",
                ]),
                encoding="utf-8",
            )
            seed = Seed(source, "XAUUSD", "H1", "family", "1")

            copy_seed_for_backtest(seed, destination, {}, ".sa")

            self.assertIn("ForceSymbol=XAUUSD.sa", destination.read_text(encoding="utf-8"))

    def test_empty_tester_report_is_pending_tester_context_not_no_trades(self) -> None:
        class Memory:
            def __init__(self) -> None:
                self.calls = []

            def record_score(self, set_path, result, status, report_path=None) -> None:
                self.calls.append((set_path, result, status, report_path))

        variant = Variant(
            path=Path("candidate.set"),
            seed=Seed(Path("seed.set"), "BTCUSD", "M1", "family", "1"),
            target_symbol="DPZ.NAS-24",
            target_period="M1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="test",
        )
        memory = Memory()

        with patch("ubs_agent_evaluate.score_report_file", return_value=score(-55.0, symbol="", timeframe="M0", trades=0)):
            status, _result = evaluate_variant_report(
                memory,
                variant,
                Path("empty.htm"),
                ScoreConfig(),
                {},
                "ICTRADING",
            )

        self.assertEqual(status, "pending_tester_context")
        self.assertEqual(memory.calls[0][2], "pending_tester_context")

    def test_empty_tester_report_with_no_history_log_is_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "SPRY.NAS_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tSPRY.NAS: found history data from 2026.02.23 00:00 to 2026.06.30 00:00, specified period is out of this range",
                        "Tester\tSPRY.NAS: no history data from 2020.01.01 00:00 to 2024.12.31 00:00",
                        "Tester\tno history data, stop testing",
                    ]
                ),
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(
                    root / "source", root / "output", 1, 1, 10, True, False
                )
                seed = Seed(root / "seed.set", "BTCUSD", "H1", "family", "1")
                variant = Variant(root / "candidate.set", seed, "SPRY.NAS", "H1", (), (), "test")
                memory.record_variant(run_id, 1, variant)

                with patch(
                    "ubs_agent_evaluate.score_report_file",
                    return_value=score(-55.0, symbol="", timeframe="M0", trades=0),
                ):
                    status, _result = evaluate_variant_report(
                        memory,
                        variant,
                        report,
                        ScoreConfig(),
                        {},
                        "ICTRADING",
                    )

                row = memory.conn.execute("select status, score, accepted, metrics_json from candidates where set_path=?", (str(variant.path),)).fetchone()
                data = json.loads(row["metrics_json"])
                self.assertEqual(status, "no_history")
                self.assertEqual(row["status"], "no_history")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertIsNone(data["score"])
                self.assertEqual(data["reasons"], ["no_history_data"])
                self.assertEqual(data["history_available_from"], "2026.02.23 00:00")
                self.assertEqual(data["history_requested_from"], "2020.01.01 00:00")
            finally:
                memory.close()

    def test_zero_trade_report_with_close_only_journal_is_trade_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "GBPTRY_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "failed sell stop 0.01 GBPTRY [Only position closing is allowed]\n"
                "CTrade::OrderSend [unknown retcode 10044]",
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
                seed = Seed(root / "seed.set", "GBPTRY", "H1", "family", "1")
                variant = Variant(root / "candidate.set", seed, "GBPTRY", "H1", (), (), "test")
                memory.record_variant(run_id, 1, variant)

                with patch(
                    "ubs_agent_evaluate.score_report_file",
                    return_value=score(-55.0, symbol="GBPTRY", timeframe="H1", trades=0),
                ):
                    status, _result = evaluate_variant_report(
                        memory, variant, report, ScoreConfig(), {}, "ICTRADING"
                    )

                row = memory.conn.execute(
                    "select status, score, accepted, metrics_json from candidates where set_path=?",
                    (str(variant.path),),
                ).fetchone()
                data = json.loads(row["metrics_json"])
                self.assertEqual(status, "trade_disabled")
                self.assertEqual(row["status"], "trade_disabled")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertEqual(data["trade_mode"], "close_only")
                self.assertEqual(data["trade_retcode"], 10044)
                self.assertFalse(data["retryable"])
            finally:
                memory.close()

    def test_generic_order_error_keeps_strategy_no_trades_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "EURUSD_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "CTrade::OrderSend [unknown retcode 4756]",
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
                seed = Seed(root / "seed.set", "EURUSD", "H1", "family", "1")
                variant = Variant(root / "candidate.set", seed, "EURUSD", "H1", (), (), "test")
                memory.record_variant(run_id, 1, variant)
                with patch(
                    "ubs_agent_evaluate.score_report_file",
                    return_value=score(-55.0, symbol="EURUSD", timeframe="H1", trades=0),
                ):
                    status, _result = evaluate_variant_report(
                        memory, variant, report, ScoreConfig(), {}, "ICTRADING"
                    )
                self.assertEqual(status, "no_trades")
            finally:
                memory.close()
