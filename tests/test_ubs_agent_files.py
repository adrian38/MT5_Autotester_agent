import argparse
import contextlib
import io
import json
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig, ScoreResult
from ubs.account import account_disabled_symbols_path
from ubs.memory import AgentMemory
from ubs.universe import (
    augment_aliases_with_symbol_map,
    load_disabled_symbols,
    load_seed_enabled_disabled_symbols,
    save_disabled_symbols,
    seed_symbol_disabled,
)
from ubs_agent import (
    DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
    PRODUCTION_SEED_SYMBOL_CAP_RATIO,
    PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
    TargetDiversityLimiter,
    apply_reserved_timeframe,
    choose_diverse_target,
    choose_target_period,
    choose_target_symbol,
    copy_seed_for_backtest,
    copy_accepted,
    create_history_probe_variant,
    create_variant,
    discovery_ranked_seed_selection,
    discovery_seed_pool,
    evaluate_candidate_final_tick,
    evaluate_history_probe,
    evaluate_seed_report,
    evaluate_variant_report,
    find_report_for_set,
    find_watchdog_snapshot_for_set,
    final_tick_row_pending_for_dates,
    final_tick_ohlc_retry_needed_for_dates,
    final_tick_ohlc_retry_exhausted_for_dates,
    final_tick_stage_prefixes,
    final_tick_similarity,
    generation_random_stream,
    generation_feedback_terminal_stage,
    generation_fitness_target,
    generation_seed_fitness_predictions,
    _relative_delta_pct,
    _evaluate_final_tick_tick_report,
    reconcile_final_tick_reports,
    reconcile_seed_eval_reports,
    recreate_work_dir,
    reconcile_final_tick_reports,
    related_timeframes,
    resolve_workspace_path,
    robust_status_pending_for_retry,
    score_config_for_period,
    target_symbol_disabled,
    target_timeframe_universe,
    tester_log_no_history_metadata,
    min_trades_for_period,
    paths_belong_to_workspace,
    probability_argument,
    production_viable_source_seeds,
    production_seed_pool,
    ranked_seed_selection,
    reserved_timeframe_plan,
    repair_seed_backtest_set,
    report_matches_variant,
    restore_run_unseeded_probabilities,
    restored_discovery_exploitable_ratio,
    restored_discovery_current_target_probability,
    restored_discovery_current_timeframe_probability,
    restored_discovery_universe_feedback_probability,
    rescore_final_tick_only,
    unseeded_asset_force_probability,
    unseeded_timeframe_force_probability,
    run_backtests,
    select_next_seed_survivors,
    select_next_generation_survivors,
    select_survivors,
    validate_final_tick_stage_dates,
    validate_seed_backtest_set,
    variant_as_next_seed,
    write_set_force_symbol,
)
from run_tests import infer_period_from_set, load_set_params, normalize_set_symbol, parse_symbol_map


def score(
    value: float,
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "H1",
    net_profit: float = 100.0,
    profit_factor: float = 2.0,
    drawdown_pct: float = 1.0,
    trades: int = 100,
    history_quality: float | None = 100.0,
    accepted: bool = True,
) -> ScoreResult:
    return ScoreResult(
        report_path="report.htm",
        name="report",
        symbol=symbol,
        timeframe=timeframe,
        score=value,
        accepted=accepted,
        net_profit=net_profit,
        raw_net_profit=net_profit,
        normalized_net_profit=net_profit,
        net_profit_factor=1.0,
        net_profit_basis="test",
        normalization_group="test",
        history_quality=history_quality,
        profit_factor=profit_factor,
        recovery_factor=2.0,
        drawdown=10.0,
        drawdown_pct=drawdown_pct,
        trades=trades,
        positive_month_ratio=1.0,
        max_month_concentration=0.1,
        avg_trade=1.0,
        sqn=1.0,
        reasons=(),
    )


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

            with patch("ubs_agent.BASE_DIR", root):
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
            results = [
                score(10.0, symbol="S&P.fs", timeframe="H1", trades=20),
                score(5.0, symbol="S&P.fs", timeframe="H1", trades=20),
            ]

            def find_report(path: Path) -> Path:
                return tick_report if path.name.startswith("tick6m_") else ohlc_report

            with (
                patch("ubs_agent.variant_from_candidate_row", return_value=variant),
                patch("ubs_agent.find_report_for_set", side_effect=find_report),
                patch(
                    "ubs_agent._read_ohlc_report_cfg_dates",
                    return_value=("2026.01.01", "2026.06.30"),
                ),
                patch("ubs_agent.score_report_file", side_effect=results) as score_report,
                patch("ubs_agent.report_matches_variant", return_value=(True, "")),
                patch(
                    "ubs_agent.final_tick_similarity",
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

        with patch("ubs_agent.score_report_file", return_value=score(-55.0, symbol="", timeframe="M0", trades=0)):
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
                    "ubs_agent.score_report_file",
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

    def test_empty_axi_share_report_with_missing_conversion_history_is_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "NATIONGRID_M30_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tNationGrid+,M30 (Axi-US51-Live): testing of Experts\\Advisors\\EA.ex5",
                        "Core 01\tNationGrid+,M30: testing of Experts\\Advisors\\EA.ex5 started with inputs:",
                        "Core 01\tGBXUSD.sa: no data synchronized, 42 bytes read",
                        "Core 01\tsymbol GBXUSD.sa history synchronization error",
                        "Core 01\t2024.01.02 12:30:00 no prices for symbol GBXUSD.sa",
                        "Tester\tautomatic testing finished",
                    ]
                ),
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
                seed = Seed(root / "seed.set", "EURUSD", "M30", "family", "1")
                variant = Variant(root / "candidate.set", seed, "NationGrid+", "M30", (), (), "test")
                memory.record_variant(run_id, 1, variant)

                with patch(
                    "ubs_agent.score_report_file",
                    return_value=score(-75.0, symbol="NationGrid+", timeframe="M30", trades=0),
                ):
                    status, _result = evaluate_variant_report(
                        memory,
                        variant,
                        report,
                        ScoreConfig(),
                        {},
                        "AXI",
                    )

                row = memory.conn.execute(
                    "select status, score, accepted, metrics_json from candidates where set_path=?",
                    (str(variant.path),),
                ).fetchone()
                data = json.loads(row["metrics_json"])
                self.assertEqual(status, "no_history")
                self.assertEqual(row["status"], "no_history")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertEqual(data["reasons"], ["no_history_data", "dependent_symbol_history"])
                self.assertEqual(data["failed_history_symbols"], ["GBXUSD.sa"])
                self.assertEqual(data["failure_type"], "dependent_symbol_history")
            finally:
                memory.close()

    def test_trade_server_sync_failure_is_retryable_not_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "AUDJPY_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tnot synchronized with trade server",
                        "Tester\tAUDJPY,H1 (Broker-Demo): testing of Experts\\EA.ex5",
                        "Core 01\tAUDJPY: no data synchronized, 504 bytes read",
                        "Core 01\tcannot get history AUDJPY,H1",
                    ]
                ),
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
                seed = Seed(root / "seed.set", "BTCUSD", "H1", "family", "1")
                variant = Variant(root / "candidate.set", seed, "AUDJPY", "H1", (), (), "test")
                memory.record_variant(run_id, 1, variant)

                with patch("ubs_agent.score_report_file", return_value=score(-55.0, symbol="", timeframe="M0", trades=0)):
                    status, _result = evaluate_variant_report(
                        memory,
                        variant,
                        report,
                        ScoreConfig(),
                        {},
                        "ICTRADING",
                    )

                row = memory.conn.execute(
                    "select status, score, accepted from candidates where set_path=?",
                    (str(variant.path),),
                ).fetchone()
                self.assertEqual(status, "pending_tester_context")
                self.assertEqual(row["status"], "pending_tester_context")
                self.assertEqual(row["score"], -55.0)
                self.assertEqual(row["accepted"], 0)
            finally:
                memory.close()

    def test_explicit_no_history_remains_authoritative_after_trade_server_sync_warning(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "SPRY.NAS_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tnot synchronized with trade server",
                        "Tester\tSPRY.NAS: no history data from 2020.01.01 00:00 to 2024.12.31 00:00",
                        "Tester\tno history data, stop testing",
                    ]
                ),
                encoding="utf-8",
            )
            variant = Variant(
                root / "candidate.set",
                Seed(root / "seed.set", "BTCUSD", "H1", "family", "1"),
                "SPRY.NAS",
                "H1",
                (),
                (),
                "test",
            )

            metadata = tester_log_no_history_metadata(report, variant)

            self.assertIsNotNone(metadata)
            self.assertEqual(metadata["reasons"], ["no_history_data"])
            self.assertEqual(metadata["history_requested_from"], "2020.01.01 00:00")

    def test_out_of_range_history_signal_is_no_history_without_generic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "SPRY.NAS_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "Tester\tSPRY.NAS: found history data from 2026.02.23 00:00 "
                "to 2026.06.30 00:00, specified period is out of this range",
                encoding="utf-8",
            )
            variant = Variant(
                root / "candidate.set",
                Seed(root / "seed.set", "BTCUSD", "H1", "family", "1"),
                "SPRY.NAS",
                "H1",
                (),
                (),
                "test",
            )

            metadata = tester_log_no_history_metadata(report, variant)

            self.assertIsNotNone(metadata)
            self.assertTrue(metadata["no_score"])
            self.assertEqual(metadata["reasons"], ["no_history_data"])
            self.assertEqual(metadata["history_available_from"], "2026.02.23 00:00")
            self.assertEqual(metadata["history_available_to"], "2026.06.30 00:00")

    def test_generation_current_no_history_overrides_history_probe_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "US30_H1_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tUS30: no history data from 2020.01.01 00:00 to 2024.12.31 00:00",
                        "Tester\tno history data, stop testing",
                    ]
                ),
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
                seed = Seed(root / "seed.set", "BTCUSD", "H1", "family", "1")
                probe = Variant(root / "probe.set", seed, "US30", "H1", (), (), "history_probe")
                variant = Variant(root / "candidate.set", seed, "US30", "H1", (), (), "test")
                memory.record_variant(0, 0, probe, status="history_ok")
                memory.record_variant(run_id, 1, variant)

                with patch("ubs_agent.score_report_file", return_value=score(-55.0, symbol="", timeframe="M0", trades=0)):
                    status, _result = evaluate_variant_report(
                        memory,
                        variant,
                        report,
                        ScoreConfig(),
                        {},
                        "ICTRADING",
                    )

                row = memory.conn.execute(
                    "select status, score, accepted, metrics_json from candidates where set_path=?",
                    (str(variant.path),),
                ).fetchone()
                data = json.loads(row["metrics_json"])
                self.assertEqual(status, "no_history")
                self.assertEqual(row["status"], "no_history")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertEqual(data["reasons"], ["no_history_data"])
            finally:
                memory.close()

    def test_history_probe_cannot_get_history_is_no_history_with_report_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "NSLR.NAS_H1_history_probe.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Core 01\tNSLR.NAS: history downloading stopped due to timeout",
                        "Core 01\tNSLR.NAS: no data synchronized, 40 bytes read",
                        "Core 01\tcannot get history NSLR.NAS,H1",
                    ]
                ),
                encoding="utf-8",
            )
            memory = AgentMemory(root / "memory.sqlite")
            try:
                seed = Seed(root / "seed.set", "XAUUSD", "H1", "family", "1")
                probe = Variant(root / "probe.set", seed, "NSLR.NAS", "H1", (), (), "history_probe")
                memory.record_variant(0, 0, probe)

                with patch(
                    "ubs_agent.score_report_file",
                    return_value=score(-55.0, symbol="NSLR.NAS", timeframe="H1", trades=0),
                ):
                    status, _result = evaluate_history_probe(
                        memory,
                        probe,
                        ScoreConfig(),
                        {},
                        "ICTRADING",
                        report_path=report,
                    )

                row = memory.conn.execute(
                    "select status, score, accepted, metrics_json from candidates where set_path=?",
                    (str(probe.path),),
                ).fetchone()
                data = json.loads(row["metrics_json"])
                self.assertEqual(status, "no_history")
                self.assertEqual(row["status"], "no_history")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertEqual(data["reasons"], ["no_history_data"])
                self.assertTrue(data["history_probe"])
            finally:
                memory.close()

    def test_history_probe_ok_is_neutral_for_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "META_H1_history_probe_0001.htm"
            report.write_text("<html></html>", encoding="utf-8")
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root / "source", root / "output", 1, 1, 1, True, False)
                seed_path = root / "seed.set"
                seed_path.write_text(
                    "\n".join(
                        [
                            "ForceSymbol=XAUUSD",
                            "Run_Strategy=1||1||0||2||N",
                            "ST1_Timeframe=16385||0||0||49153||N",
                            "Entry_Timing=16385||0||0||49153||N",
                            "ATR_Timeframe=16385||0||0||49153||N",
                        ]
                    ),
                    encoding="utf-8",
                )
                seed = Seed(seed_path, "XAUUSD", "H1", "family", "1")
                variant = create_history_probe_variant(seed, "META", "H1", root / "probe", 1)
                memory.record_variant(run_id, 1, variant, status="history_probe")

                with patch("ubs_agent.find_report_for_set", return_value=report), patch(
                    "ubs_agent.score_report_file",
                    return_value=score(42.0, symbol="META", timeframe="H1", trades=12),
                ):
                    status, _result = evaluate_history_probe(
                        memory,
                        variant,
                        ScoreConfig(),
                        {},
                        "ICTRADING",
                    )

                row = memory.conn.execute(
                    "select status, score, accepted, metrics_json from candidates where set_path=?",
                    (str(variant.path),),
                ).fetchone()
                data = json.loads(row["metrics_json"])
                self.assertEqual(status, "history_ok")
                self.assertEqual(row["status"], "history_ok")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertTrue(data["history_probe"])
                self.assertEqual(memory.asset_feedback({}), {})
            finally:
                memory.close()

    def test_existing_empty_context_no_trades_are_migrated_to_pending_tester_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "memory.sqlite"
            memory = AgentMemory(db_path)
            run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
            seed = Seed(root / "seed.set", "XAUUSD", "H1", "family", "1")
            seed.path.write_text("set", encoding="utf-8")
            empty_variant = Variant(root / "empty.set", seed, "DPZ.NAS-24", "M1", (), (), "test")
            valid_zero_variant = Variant(root / "valid_zero.set", seed, "WULF.NAS-24", "M1", (), (), "test")
            for variant in (empty_variant, valid_zero_variant):
                memory.record_variant(run_id, 1, variant)
            memory.record_score(
                empty_variant.path,
                score(-55.0, symbol="", timeframe="M0", trades=0),
                "no_trades",
                Path("empty.htm"),
            )
            memory.record_score(
                valid_zero_variant.path,
                score(-55.0, symbol="WULF.NAS-24", timeframe="M1", trades=0),
                "no_trades",
                Path("valid_zero.htm"),
            )
            memory.prepare_single_seed_evaluation(seed, force=True)
            memory.record_seed_score(
                seed,
                score(-55.0, symbol="", timeframe="M0", trades=0),
                "no_trades",
                Path("seed_empty.htm"),
            )
            memory.close()

            memory = AgentMemory(db_path)
            try:
                empty_row = memory.conn.execute("select status from candidates where set_path=?", (str(empty_variant.path),)).fetchone()
                valid_row = memory.conn.execute("select status from candidates where set_path=?", (str(valid_zero_variant.path),)).fetchone()
                seed_row = memory.conn.execute("select status from seed_scores where seed_path=?", (str(seed.path),)).fetchone()

                self.assertEqual(empty_row["status"], "pending_tester_context")
                self.assertEqual(valid_row["status"], "no_trades")
                self.assertEqual(seed_row["status"], "pending_tester_context")
                retryable = memory.retryable_problem_candidates_for_run(run_id)
                self.assertEqual([row["set_path"] for row in retryable], [str(empty_variant.path)])
            finally:
                memory.close()

    def test_seed_empty_tester_context_remains_pending_for_retry(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        report = Path("empty.htm")
        result = score(-75.0, symbol="", timeframe="M0", trades=0, accepted=False)
        memory = Mock()

        status, parsed = evaluate_seed_report(
            memory,
            seed,
            report,
            ScoreConfig(),
            {},
            "ICTRADING",
            parsed_result=result,
        )

        self.assertEqual(status, "pending_tester_context")
        self.assertIs(parsed, result)
        memory.record_seed_score.assert_called_once_with(
            seed,
            result,
            "pending_tester_context",
            report,
        )

    def test_seed_reconcile_does_not_consume_empty_tester_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_root = root / "output"
            eval_dir = output_root / "seed_eval" / "eval_20260810_191052"
            eval_dir.mkdir(parents=True)
            source = root / "seed.set"
            copied = eval_dir / "seed_0001_XAUUSD_H1_seed.set"
            source.write_text("ForceSymbol=XAUUSD\n", encoding="utf-8")
            copied.write_text("ForceSymbol=XAUUSD\n", encoding="utf-8")
            report = root / "empty.htm"
            report.write_text("<html></html>", encoding="utf-8")
            seed = Seed(source, "XAUUSD", "H1", "family", "1")
            memory = Mock()
            memory.seed_score_row.return_value = {"status": "pending"}
            empty_result = score(
                -75.0,
                symbol="",
                timeframe="M0",
                trades=0,
                accepted=False,
            )

            with (
                patch("ubs_agent.find_report_for_set", return_value=report),
                patch("ubs_agent.score_report_file", return_value=empty_result),
            ):
                counts, processed = reconcile_seed_eval_reports(
                    memory,
                    [seed],
                    output_root,
                    ScoreConfig(),
                    {},
                    "ICTRADING",
                )

            self.assertEqual(counts, {})
            self.assertEqual(processed, set())
            memory.record_seed_score.assert_not_called()

    def test_run_backtests_forwards_model_override(self) -> None:
        args = SimpleNamespace(
            expert="Ultimate Breakout System.ex5",
            multi_terminal=False,
            template="tester_template.ini",
            delay=0,
            mt5_path="",
            data_dir="",
            max_workers=1,
            terminals_config="",
            symbol_map="",
            symbol_suffix=".sa",
            symbol_futures_suffix=".fs",
            symbol_shares_suffix="+",
            assets=str(Path("assets") / "axi_assets.ini"),
            dry_run=True,
            from_date="",
            to_date="",
        )
        completed = SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            set_dir = Path(temp_dir)
            with patch("ubs_agent.subprocess.run", return_value=completed) as run_mock:
                code = run_backtests(args, set_dir, model="1")

        self.assertEqual(code, 0)
        command = run_mock.call_args.args[0]
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "1")
        self.assertIn("--symbol-suffix", command)
        self.assertEqual(command[command.index("--symbol-suffix") + 1], ".sa")
        self.assertIn("--symbol-futures-suffix", command)
        self.assertEqual(command[command.index("--symbol-futures-suffix") + 1], ".fs")
        self.assertIn("--symbol-shares-suffix", command)
        self.assertEqual(command[command.index("--symbol-shares-suffix") + 1], "+")
        self.assertIn("--symbol-universe", command)
        self.assertEqual(command[command.index("--symbol-universe") + 1], str(Path("assets") / "axi_assets.ini"))

    def test_final_tick_6m_requires_at_least_180_days(self) -> None:
        message = validate_final_tick_stage_dates("six_month", "2026.01.01", "2026.06.01")

        self.assertIsNotNone(message)
        self.assertIn("rango actual 151 dias", message)
        self.assertIn("Hasta >= 2026.06.30", message)
        self.assertIsNone(validate_final_tick_stage_dates("six_month", "2026.01.01", "2026.06.30"))

    def test_short_final_tick_does_not_require_180_days(self) -> None:
        self.assertIsNone(validate_final_tick_stage_dates("probe", "2026.01.01", "2026.06.01"))

    def test_short_final_tick_does_not_retry_pending_ohlc_trades(self) -> None:
        row = {
            "final_tick_status": "pending_ohlc_trades",
            "final_tick_from_date": "2026.05.01",
            "final_tick_to_date": "2026.05.31",
        }

        self.assertFalse(
            final_tick_row_pending_for_dates(
                row,
                "2026.01.01",
                "2026.06.30",
                final_tick_stage="probe",
            )
        )

    def test_six_month_final_tick_retries_pending_ohlc_trades_with_new_dates(self) -> None:
        row = {
            "final_tick_status": "pending_ohlc_trades",
            "final_tick_from_date": "2026.01.01",
            "final_tick_to_date": "2026.06.30",
        }

        self.assertTrue(
            final_tick_row_pending_for_dates(
                row,
                "2025.11.01",
                "2026.06.30",
                final_tick_stage="six_month",
            )
        )

    def test_six_month_ohlc_retry_not_needed_when_pending_row_already_used_retry_dates(self) -> None:
        rows = [
            {
                "final_tick_status": "pending_ohlc_trades",
                "final_tick_from_date": "2025.09.01",
                "final_tick_to_date": "2026.06.30",
            },
            {
                "final_tick_status": "report_mismatch",
                "final_tick_from_date": "2026.01.01",
                "final_tick_to_date": "2026.06.30",
            },
        ]

        self.assertFalse(
            final_tick_ohlc_retry_needed_for_dates(
                rows,
                "2025.09.01",
                "2026.06.30",
                final_tick_stage="six_month",
            )
        )
        self.assertTrue(
            final_tick_ohlc_retry_exhausted_for_dates(
                rows[0],
                "2025.09.01",
                "2026.06.30",
            )
        )

    def test_six_month_ohlc_retry_uses_separate_report_prefix(self) -> None:
        self.assertEqual(final_tick_stage_prefixes("six_month"), ("ohlc6m", "tick6m"))
        self.assertEqual(
            final_tick_stage_prefixes("six_month", ohlc_retry=True),
            ("ohlc6m_retry", "tick6m"),
        )

    def test_ohlc_retry_pass_continues_with_main_dates(self) -> None:
        """La rama OHLC retry solo corre su scope y aparta el resto.

        El pipeline del manager avanza a ``final_tick_*_quality`` en cuanto la
        etapa devuelve 0, asi que sin esta continuacion las filas apartadas se
        quedan sin evaluar con la etapa dada por terminada.
        """
        args = SimpleNamespace(
            final_tick_stage="six_month",
            from_date="2026.01.01",
            to_date="2026.06.30",
        )
        calls: list[tuple[bool, str, str]] = []

        def fake_pass(pass_args, _memory, _score, *, allow_ohlc_retry=True, deferred_out=None):
            calls.append((allow_ohlc_retry, pass_args.from_date, pass_args.to_date))
            if allow_ohlc_retry:
                # La pasada de retry muta las fechas al rango alternativo.
                pass_args.from_date = "2025.09.01"
                if deferred_out is not None:
                    deferred_out.extend([{"id": 1}, {"id": 2}])
            return 0

        with patch("ubs_agent._evaluate_candidate_final_tick_pass", side_effect=fake_pass):
            code = evaluate_candidate_final_tick(args, Mock(), ScoreConfig())

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [(True, "2026.01.01", "2026.06.30"), (False, "2026.01.01", "2026.06.30")],
        )

    def test_final_tick_runs_single_pass_without_deferred_rows(self) -> None:
        args = SimpleNamespace(
            final_tick_stage="six_month",
            from_date="2026.01.01",
            to_date="2026.06.30",
        )

        with patch(
            "ubs_agent._evaluate_candidate_final_tick_pass", return_value=0
        ) as pass_mock:
            self.assertEqual(evaluate_candidate_final_tick(args, Mock(), ScoreConfig()), 0)
        self.assertEqual(pass_mock.call_count, 1)

        def failing_pass(_args, _memory, _score, *, allow_ohlc_retry=True, deferred_out=None):
            if deferred_out is not None:
                deferred_out.append({"id": 1})
            return 1

        with patch(
            "ubs_agent._evaluate_candidate_final_tick_pass", side_effect=failing_pass
        ) as pass_mock:
            self.assertEqual(evaluate_candidate_final_tick(args, Mock(), ScoreConfig()), 1)
        self.assertEqual(pass_mock.call_count, 1)

    def test_quality_retry_selects_only_pending_history_quality_rows(self) -> None:
        """``--final-tick-retry-pending-quality`` restringe, no amplia.

        Va siempre con ``--final-tick-skip-ohlc``, que solo puede servir filas
        con la pata OHLC guardada; el manager cuenta exactamente ese conjunto
        para decidir si lanza la etapa.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def candidate_row(candidate_id: int, status: str, *, with_ohlc: bool) -> dict:
                set_path = root / f"candidate_{candidate_id}.set"
                set_path.write_text("test", encoding="utf-8")
                return {
                    "id": candidate_id,
                    "set_path": str(set_path),
                    "final_tick_status": status,
                    "final_tick_from_date": "2026.01.01" if status else None,
                    "final_tick_to_date": "2026.06.30" if status else None,
                    "ft_ohlc_report_path": str(root / f"ohlc6m_{candidate_id}.htm") if with_ohlc else None,
                    "ft_ohlc_metrics_json": "{}" if with_ohlc else None,
                }

            rows = [
                candidate_row(1, "pending_history_quality", with_ohlc=True),
                candidate_row(2, "pending_history_quality", with_ohlc=True),
                candidate_row(3, "", with_ohlc=False),
                candidate_row(4, "report_mismatch", with_ohlc=False),
            ]
            memory = SimpleNamespace(
                active_final_tick_stage="six_month",
                run_by_id=lambda _run_id: {"id": 437, "output_dir": str(root)},
                latest_run=lambda: None,
                accepted_candidates_for_final_tick=Mock(return_value=rows),
                record_candidate_final_tick=Mock(),
                path=root / "memory.sqlite",
            )
            args = SimpleNamespace(
                final_tick_stage="six_month",
                final_tick_reconcile_only=False,
                final_tick_run_id=437,
                final_tick_pending_only=True,
                final_tick_retry_pending_quality=True,
                final_tick_skip_ohlc=True,
                final_tick_ohlc_from_date="2025.09.01",
                final_tick_ohlc_to_date="2026.06.30",
                from_date="2026.01.01",
                to_date="2026.06.30",
                dry_run=True,
                expert=None,
                multi_terminal=False,
                broker="ICTRADING",
                symbol_map="",
                symbol_suffix="",
                final_tick_min_history_quality=80.0,
                final_tick_min_ohlc_trades=4,
                final_tick_min_trades_w1=7,
                final_tick_min_trades_mn=3,
                final_tick_max_net_delta_pct=35.0,
                final_tick_max_pf_delta_pct=35.0,
                final_tick_max_dd_delta_pct=35.0,
                final_tick_max_trades_delta_pct=35.0,
            )
            variant = Variant(
                path=root / "candidate_1.set",
                seed=Seed(Path("seed.set"), "GBPUSD", "M30", "family", "1"),
                target_symbol="GBPUSD",
                target_period="M30",
                mutated_keys=(),
                missing_lot_keys=(),
                policy="test",
            )
            output = io.StringIO()
            with (
                patch("ubs_agent.split_retired_symbols", side_effect=lambda r, _a: (r, [])),
                patch("ubs_agent.variant_from_candidate_row", return_value=variant),
                patch("ubs_agent.write_set_use_every_tick"),
                patch(
                    "ubs_agent._read_ohlc_report_cfg_dates",
                    return_value=("2026.01.01", "2026.06.30"),
                ),
                contextlib.redirect_stdout(output),
            ):
                code = evaluate_candidate_final_tick(args, memory, ScoreConfig())

            printed = output.getvalue()
            self.assertEqual(code, 0)
            self.assertNotIn("faltan ohlc_metrics_json", printed)
            self.assertIn("candidatos=2", printed)

    def test_crudeoil_seed_is_disabled_when_wti_is_disabled(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertTrue(seed_symbol_disabled(seed, {"WTI"}, symbol_map))

    def test_seed_enabled_symbol_allows_disabled_seed(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI,XTIUSD=WTI")

        self.assertFalse(seed_symbol_disabled(seed, {"WTI"}, symbol_map, {"WTI"}))

    def test_axi_suffix_policy_blocks_normalized_seed_unless_seeds_enabled(self) -> None:
        seed = Seed(Path("Gold.set"), "XAUUSD", "H1", "GOLD", "1")
        symbol_map = parse_symbol_map("XAUUSD=XAUUSD.sa")

        self.assertTrue(seed_symbol_disabled(seed, {"XAUUSD.SA"}, symbol_map))
        self.assertFalse(
            seed_symbol_disabled(seed, {"XAUUSD.SA"}, symbol_map, {"XAUUSD.SA"})
        )

    def test_disabled_symbols_json_preserves_seed_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ubs_disabled_symbols.json"

            save_disabled_symbols(path, {"WTI", "XAUUSD"}, {"WTI"})

            self.assertEqual(load_disabled_symbols(path), {"WTI", "XAUUSD"})
            self.assertEqual(load_seed_enabled_disabled_symbols(path), {"WTI"})

    def test_disabled_seed_source_generates_enabled_target(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=WTI")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("EURUSD", "WTI"),
            {},
            symbol_map=symbol_map,
            disabled_symbols={"WTI"},
        )

        self.assertEqual(target, "EURUSD")
        self.assertNotEqual(policy, "exploit")

    def test_alias_seed_exploit_returns_canonical_universe_symbol(self) -> None:
        seed = Seed(Path("Crude_D__CrudeOil_Optimization.set"), "CRUDEOIL", "D1", "family", "1")
        symbol_map = parse_symbol_map("CRUDEOIL=XTIUSD")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XTIUSD", "XBRUSD"),
            {"CRUDEOIL": "XTIUSD"},
            symbol_map=symbol_map,
            disabled_symbols=set(),
        )

        self.assertEqual(target, "XTIUSD")
        self.assertEqual(policy, "exploit")

    def test_axi_cash_seed_can_exploit_enabled_future_equivalent(self) -> None:
        seed = Seed(Path("DAX_M15_a.set"), "DAX", "M15", "family", "1")
        symbol_map = parse_symbol_map("DAX=GER40,DE40=GER40")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("GER40.sa", "DAX40.fs"),
            {},
            symbol_map=symbol_map,
            disabled_symbols={"GER40.SA"},
        )

        self.assertEqual(target, "DAX40.fs")
        self.assertEqual(policy, "exploit")

    def test_target_disabled_without_policy_does_not_read_default_account_file(self) -> None:
        self.assertFalse(target_symbol_disabled("WTI", ("WTI",), {}, disabled_symbols=None))

    def test_axi_suffix_policy_blocks_normalized_generation_target(self) -> None:
        symbol_map = parse_symbol_map("XAUUSD=XAUUSD.sa")

        self.assertTrue(
            target_symbol_disabled(
                "XAUUSD",
                (),
                {},
                symbol_map=symbol_map,
                disabled_symbols={"XAUUSD.SA"},
            )
        )

    def test_disabled_symbols_policy_is_account_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)

            self.assertEqual(
                account_disabled_symbols_path(base_dir, "ECN"),
                base_dir / "outputs" / "ubs_disabled_symbols_ROBOFOREX_ECN.json",
            )
            self.assertEqual(
                account_disabled_symbols_path(base_dir, "PRO"),
                base_dir / "outputs" / "ubs_disabled_symbols_ROBOFOREX_PRO.json",
            )
            self.assertEqual(
                account_disabled_symbols_path(base_dir, "PREMIUM", "AXI"),
                base_dir / "outputs" / "ubs_disabled_symbols_AXI_PREMIUM.json",
            )

    def test_seed_validation_rejects_incomplete_ubs_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.set"
            path.write_text(
                "\n".join(
                    [
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=60||5||0||16385||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(path, "XAUUSD", "H1", "family", "")

            issues = validate_seed_backtest_set(seed)

            self.assertIn("sin ForceSymbol", issues)
            self.assertIn("sin Run_Strategy valido", issues)
            self.assertTrue(
                any(issue.startswith("Entry_Timing=60 fuera del universo soportado") for issue in issues),
                issues,
            )

    def test_seed_validation_accepts_bound_ubs_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "good.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "ST1_Timeframe=16385||0||0||49153||N",
                        "Entry_Timing=16385||5||0||16385||N",
                        "ATR_Timeframe=16385||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(path, "XAUUSD", "H1", "family", "1")

            self.assertEqual(validate_seed_backtest_set(seed), [])

    def test_seed_validation_accepts_range_strategy_and_uses_rng_timeframe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DE40_range.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=DE40",
                        "Run_Strategy=3||1||0||3||N",
                        "RNG_Timeframe=16385||0||0||49153||N",
                        "RNG_ATR_Timeframe=16385||0||0||49153||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )
            params = load_set_params(path)
            seed = Seed(path, "DE40", "H1", "family", "3")

            self.assertEqual(infer_period_from_set(path, params), "H1")
            self.assertEqual(validate_seed_backtest_set(seed), [])

    def test_repair_seed_backtest_set_sets_range_strategy_timeframe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DE40_range.set"
            path.write_text(
                "\n".join(
                    [
                        "ForceSymbol=DE40",
                        "Run_Strategy=3||1||0||3||N",
                        "RNG_Timeframe=0||0||0||49153||N",
                        "RNG_ATR_Timeframe=0||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "DE40", "H1")

            self.assertIn("RNG_Timeframe", result["changed"])
            self.assertIn("RNG_ATR_Timeframe", result["changed"])
            self.assertIn("RNG_Timeframe=16385||0||0||49153||N", path.read_text(encoding="utf-8"))
            self.assertIn("RNG_ATR_Timeframe=16385||0||0||49153||N", path.read_text(encoding="utf-8"))

    def test_write_set_force_symbol_adds_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "seed.set"
            path.write_text("Run_Strategy=1||1||0||2||N\nST1_Timeframe=16385||0||0||49153||N", encoding="utf-8")

            write_set_force_symbol(path, path, "XAUUSD")

            self.assertIn("ForceSymbol=XAUUSD", path.read_text(encoding="utf-8"))
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_repair_seed_backtest_set_fixes_bitcoin_reaper_st1_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "BTC_H1__Bitcoin_Reaper__updated__k.set"
            path.write_text(
                "\n".join(
                    [
                        "ATR_Timeframe=16408||0||0||49153||N",
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=16385||0||0||49153||N",
                        "EA_Comment=Ultimate Breakout System_k",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "BTCUSD", "H1")
            text = path.read_text(encoding="utf-8")

            self.assertEqual(result["run_strategy"], "1")
            self.assertIn("ForceSymbol=BTCUSD", text)
            self.assertIn("Run_Strategy=1||1||0||2||N", text)
            self.assertIn("ST1_Timeframe=16385||0||0||49153||N", text)
            self.assertEqual(validate_seed_backtest_set(Seed(path, "BTCUSD", "H1", "family", "1")), [])

    def test_repair_seed_backtest_set_uses_volatility_strategy_when_vol_key_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Volatility_Breakout__BTCUSD__H1_A.set"
            path.write_text(
                "\n".join(
                    [
                        "ATR_Timeframe=16408||0||0||49153||N",
                        "VolTimeframe=16385||0||0||49153||N",
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=0||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "BTCUSD", "H1")
            text = path.read_text(encoding="utf-8")

            self.assertEqual(result["run_strategy"], "2")
            self.assertIn("ForceSymbol=BTCUSD", text)
            self.assertIn("Run_Strategy=2||1||0||2||N", text)
            self.assertEqual(validate_seed_backtest_set(Seed(path, "BTCUSD", "H1", "family", "2")), [])

    def test_repair_seed_backtest_set_converts_legacy_timeframe_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Old_Optimizations_H1__MT4A.set"
            path.write_text(
                "\n".join(
                    [
                        "ST1_Timeframe=0||0||0||49153||N",
                        "Entry_Timing=60||0||0||49153||N",
                        "ATR_Timeframe=16408||0||0||49153||N",
                    ]
                ),
                encoding="utf-8",
            )

            result = repair_seed_backtest_set(path, "XAUUSD", "H1")
            text = path.read_text(encoding="utf-8")

            self.assertIn("Entry_Timing=16385||0||0||49153||N", text)
            self.assertEqual(result["run_strategy"], "1")
            self.assertEqual(validate_seed_backtest_set(Seed(path, "XAUUSD", "H1", "family", "1")), [])

    def test_copy_accepted_replaces_previous_copy_for_same_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = recreate_work_dir(Path(temp_dir))
            source = root / "candidate.set"
            source.write_text("set", encoding="utf-8")
            accepted_dir = root / "accepted"
            seed = Seed(source, "XAUUSD", "H1", "family", "1")
            variant = Variant(source, seed, "XAUUSD", "H1", (), (), "test")

            first = copy_accepted([(variant, score(10.0))], accepted_dir)
            second = copy_accepted([(variant, score(20.0))], accepted_dir)

            files = sorted(accepted_dir.glob("*.set"))
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(files, second)
            self.assertEqual(files[0].name, "score_0020.00__candidate.set")

    def test_variant_as_next_seed_preserves_target_timeframe(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        variant = Variant(Path("candidate.set"), seed, "XAUUSD", "D1", ("Exit_stop",), (), "tf_feedback")

        next_seed = variant_as_next_seed(variant)

        self.assertEqual(next_seed.path, variant.path)
        self.assertEqual(next_seed.symbol, "XAUUSD")
        self.assertEqual(next_seed.period, "D1")
        self.assertEqual(next_seed.family, seed.family)
        self.assertEqual(next_seed.run_strategy, seed.run_strategy)

    def test_unseeded_force_probabilities_drop_after_generation_one(self) -> None:
        self.assertEqual(unseeded_asset_force_probability(1, 10), 0.12)
        self.assertEqual(unseeded_asset_force_probability(2, 10), 0.08)
        self.assertEqual(unseeded_asset_force_probability(3, 10), 0.05)
        self.assertEqual(unseeded_asset_force_probability(1, 0), 0.0)
        self.assertEqual(unseeded_timeframe_force_probability(1, 2), 0.20)
        self.assertEqual(unseeded_timeframe_force_probability(3, 2), 0.08)

    def test_unseeded_force_probabilities_accept_run_specific_schedule(self) -> None:
        self.assertEqual(unseeded_asset_force_probability(1, 10, {1: 0.3, 2: 0.2}, 0.1), 0.3)
        self.assertEqual(unseeded_asset_force_probability(4, 10, {1: 0.3, 2: 0.2}, 0.1), 0.1)
        self.assertEqual(probability_argument("0.25"), 0.25)
        with self.assertRaises(argparse.ArgumentTypeError):
            probability_argument("1.1")

    def test_unseeded_asset_selection_uses_group_lifecycle_feedback(self) -> None:
        stocks = tuple(f"STOCK_{index}" for index in range(100))
        metals = tuple(f"METAL_{index}" for index in range(4))
        universe = (*stocks, *metals)
        groups = {
            **{symbol: "Stocks" for symbol in stocks},
            **{symbol: "Metals" for symbol in metals},
        }
        rng = random.Random(20260812)

        selected = [
            choose_target_symbol(
                Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1"),
                {},
                rng,
                universe,
                {},
                force_unseeded_universe=True,
                unseeded_universe_symbols=universe,
                force_unseeded_probability=1.0,
                group_by_symbol=groups,
                asset_group_feedback={"Stocks": -24.0, "Metals": 24.0},
            )
            for _ in range(400)
        ]

        metal_count = sum(target.startswith("METAL_") for target, _policy in selected)
        self.assertGreater(metal_count, 320)
        self.assertTrue(all(policy == "asset_unseeded_group_feedback" for _target, policy in selected))

    def test_asset_feedback_with_groups_separates_lifecycle_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory = AgentMemory(root / "memory.sqlite")
            try:
                run_id = memory.create_run(root, root / "output", 1, 1, 2, True, False)
                seed = Seed(root / "seed.set", "XAUUSD", "H1", "family", "1")
                for name, symbol, value, status in (
                    ("metal.set", "XAUUSD", 120.0, "accepted"),
                    ("stock.set", "AAPL", -80.0, "rejected"),
                    ("obsolete.set", "OLD+", 5000.0, "accepted"),
                ):
                    variant = Variant(root / name, seed, symbol, "H1", (), (), "test")
                    memory.record_variant(run_id, 1, variant)
                    memory.record_score(
                        variant.path,
                        score(value, symbol=symbol),
                        status,
                        root / f"{name}.htm",
                    )

                assets, groups = memory.asset_feedback_with_groups(
                    {},
                    {"XAUUSD": "Metals", "AAPL": "Stocks"},
                )

                self.assertIn("XAUUSD", assets)
                self.assertIn("AAPL", assets)
                self.assertNotIn("OLD+", assets)
                self.assertGreater(groups["Metals"], groups["Stocks"])
                filtered_signals = memory.asset_feedback_signals(
                    {},
                    allowed_symbols={"XAUUSD", "AAPL"},
                )
                self.assertEqual(set(filtered_signals), {"XAUUSD", "AAPL"})
            finally:
                memory.close()

    def test_resume_restores_persisted_unseeded_schedule(self) -> None:
        args = SimpleNamespace(
            asset_unseeded_prob_gen1=0.12,
            asset_unseeded_prob_gen2=0.08,
            asset_unseeded_prob_late=0.05,
            timeframe_unseeded_prob_gen1=0.20,
            timeframe_unseeded_prob_gen2=0.12,
            timeframe_unseeded_prob_late=0.08,
        )
        restore_run_unseeded_probabilities(
            args,
            json.dumps(
                {
                    "generation": {
                        "asset_unseeded_force_probability": {
                            "generation_1": 0.35,
                            "generation_2": 0.25,
                            "late": 0.15,
                        },
                        "timeframe_unseeded_force_probability": {
                            "generation_1": 0.18,
                            "generation_2": 0.11,
                            "late": 0.07,
                        },
                    }
                }
            ),
        )

        self.assertEqual(args.asset_unseeded_prob_gen1, 0.35)
        self.assertEqual(args.asset_unseeded_prob_gen2, 0.25)
        self.assertEqual(args.asset_unseeded_prob_late, 0.15)
        self.assertEqual(args.timeframe_unseeded_prob_gen1, 0.18)
        self.assertEqual(args.timeframe_unseeded_prob_gen2, 0.11)
        self.assertEqual(args.timeframe_unseeded_prob_late, 0.07)

    def test_resume_restores_persisted_discovery_source_ratio(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "seed_selection_diversity_caps": {
                        "discovery_exploitable_seed_min_ratio": 0.74,
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_exploitable_ratio(config), 0.74)
        self.assertEqual(restored_discovery_exploitable_ratio("{}"), 0.60)

    def test_resume_restores_persisted_discovery_feedback_probability(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "target_policy": {
                        "discovery_adaptive_policy": {
                            "universe_feedback": {"probability": 0.79}
                        }
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_universe_feedback_probability(config), 0.79)
        self.assertEqual(restored_discovery_universe_feedback_probability("{}"), 0.55)

    def test_resume_restores_persisted_discovery_current_target_probability(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "target_policy": {
                        "discovery_adaptive_policy": {
                            "current_target": {"probability": 0.82}
                        }
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_current_target_probability(config), 0.82)
        self.assertEqual(restored_discovery_current_target_probability("{}"), 0.70)

    def test_resume_restores_persisted_discovery_current_timeframe_probability(self) -> None:
        config = json.dumps(
            {
                "generation": {
                    "target_policy": {
                        "discovery_adaptive_policy": {
                            "current_timeframe": {"probability": 0.73}
                        }
                    }
                }
            }
        )

        self.assertEqual(restored_discovery_current_timeframe_probability(config), 0.73)
        self.assertEqual(restored_discovery_current_timeframe_probability("{}"), 0.60)

    def test_current_timeframe_probability_controls_discovery_branch(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            current_timeframe_probability=1.0,
        )
        _changed_target, changed_policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            current_timeframe_probability=0.0,
        )

        self.assertEqual(target, "H1")
        self.assertEqual(policy, "tf_exploit")
        self.assertNotEqual(changed_policy, "tf_exploit")

    def test_current_target_probability_controls_discovery_exploit_branch(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XAUUSD", "EURUSD"),
            {},
            current_target_probability=1.0,
        )
        _cross_target, cross_policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XAUUSD", "EURUSD"),
            {},
            current_target_probability=0.0,
        )

        self.assertEqual(target, "XAUUSD")
        self.assertEqual(policy, "exploit")
        self.assertNotEqual(cross_policy, "exploit")

    def test_target_timeframe_universe_keeps_long_timeframes_experimental(self) -> None:
        normal = target_timeframe_universe(False)
        experimental = target_timeframe_universe(True)

        self.assertIn("M1", normal)
        self.assertIn("M5", normal)
        self.assertNotIn("W1", normal)
        self.assertNotIn("MN", normal)
        self.assertEqual(experimental[-2:], ("W1", "MN"))

    def test_related_timeframes_filter_experimental_long_timeframes(self) -> None:
        self.assertEqual(related_timeframes("D1", target_timeframe_universe(False)), ("H4", "D1"))
        self.assertEqual(related_timeframes("D1", target_timeframe_universe(True)), ("H4", "D1", "W1", "MN"))

    def test_choose_target_period_does_not_emit_long_timeframes_by_default(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "D1", "family", "1")

        for idx in range(40):
            target, _policy = choose_target_period(
                seed,
                {},
                random.Random(idx),
                timeframe_universe=target_timeframe_universe(False),
            )
            self.assertNotIn(target, {"W1", "MN"})

    def test_choose_target_period_can_force_experimental_long_timeframes(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "D1", "family", "1")

        target, policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            timeframe_universe=target_timeframe_universe(True),
            force_unseeded_timeframes=True,
            unseeded_timeframes=("W1", "MN"),
            force_unseeded_probability=1.0,
        )

        self.assertIn(target, {"W1", "MN"})
        self.assertEqual(policy, "tf_unseeded_force")

    def test_long_timeframe_min_trades_only_overrides_w1_mn(self) -> None:
        config = ScoreConfig(min_trades=48)

        self.assertEqual(score_config_for_period(config, "H4", min_trades_w1=12, min_trades_mn=4).min_trades, 48)
        self.assertEqual(score_config_for_period(config, "W1", min_trades_w1=12, min_trades_mn=4).min_trades, 12)
        self.assertEqual(score_config_for_period(config, "MN", min_trades_w1=12, min_trades_mn=4).min_trades, 4)

    def test_final_tick_min_trades_can_be_lower_for_long_timeframes(self) -> None:
        self.assertEqual(min_trades_for_period("H1", 5, 2, 1), 5)
        self.assertEqual(min_trades_for_period("W1", 5, 2, 1), 2)
        self.assertEqual(min_trades_for_period("MN", 5, 2, 1), 1)

    def test_zero_unseeded_probability_disables_forced_symbol_branch(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        for idx in range(20):
            _target, policy = choose_target_symbol(
                seed,
                {},
                random.Random(idx),
                ("EURUSD", "GBPUSD"),
                {},
                force_unseeded_universe=True,
                unseeded_universe_symbols=("EURUSD", "GBPUSD"),
                force_unseeded_probability=0.0,
            )
            self.assertNotEqual(policy, "asset_unseeded_force")

    def test_zero_unseeded_probability_disables_forced_timeframe_branch(self) -> None:
        seed = Seed(Path("seed.set"), "MYSTERY", "H1", "family", "1")

        for idx in range(20):
            _target, policy = choose_target_period(
                seed,
                {},
                random.Random(idx),
                force_unseeded_timeframes=True,
                unseeded_timeframes=("D1",),
                force_unseeded_probability=0.0,
            )
            self.assertNotEqual(policy, "tf_unseeded_force")

    def test_production_symbol_selection_does_not_random_walk_universe(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        allowed = {"XAUUSD", "XAGUSD", "XAUEUR"}

        observed = {
            choose_target_symbol(
                seed,
                {},
                random.Random(idx),
                ("AAPL.NAS", "TSLA.NAS", "XAGUSD", "XAUEUR"),
                {},
                production_mode=True,
            )[0]
            for idx in range(50)
        }

        self.assertTrue(observed)
        self.assertTrue(observed <= allowed)

    def test_discovery_current_target_probability_does_not_change_production(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_symbol(
            seed,
            {},
            random.Random(1),
            ("XAUUSD", "EURUSD"),
            {},
            production_mode=True,
            current_target_probability=0.0,
        )

        self.assertEqual(target, "XAUUSD")
        self.assertEqual(policy, "production_exploit")

    def test_discovery_current_timeframe_probability_does_not_change_production(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_period(
            seed,
            {},
            random.Random(1),
            production_mode=True,
            current_timeframe_probability=0.0,
        )

        self.assertEqual(target, "H1")
        self.assertEqual(policy, "tf_production_exploit")

    def test_production_symbol_selection_uses_positive_evidence_fallback(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target, policy = choose_target_symbol(
            seed,
            {"EURUSD": 5.0, "AAPL.NAS": -10.0},
            random.Random(1),
            ("EURUSD", "AAPL.NAS"),
            {},
            disabled_symbols={"XAUUSD"},
            production_mode=True,
        )

        self.assertEqual(target, "EURUSD")
        self.assertEqual(policy, "production_asset_feedback")

    def test_production_symbol_selection_blocks_cross_group_feedback_when_group_known(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target = choose_target_symbol(
            seed,
            {"EURUSD": 50.0},
            random.Random(1),
            ("EURUSD",),
            {},
            disabled_symbols={"XAUUSD"},
            production_mode=True,
            group_by_symbol={"XAUUSD": "Metals", "EURUSD": "Forex"},
        )

        self.assertIsNone(target)

    def test_production_symbol_selection_uses_same_group_when_current_disabled(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        target = choose_target_symbol(
            seed,
            {"EURUSD": 50.0},
            random.Random(1),
            ("XAGUSD", "EURUSD"),
            {},
            disabled_symbols={"XAUUSD"},
            production_mode=True,
            group_by_symbol={"XAUUSD": "Metals", "XAGUSD": "Metals", "EURUSD": "Forex"},
        )

        self.assertIsNotNone(target)
        self.assertEqual(target[0], "XAGUSD")
        self.assertNotEqual(target[0], "EURUSD")

    def test_production_timeframe_selection_avoids_unexplored_timeframes(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")

        observed = {
            choose_target_period(
                seed,
                {},
                random.Random(idx),
                timeframe_universe=("M1", "M5", "M15", "M30", "H1", "H4", "D1"),
                production_mode=True,
            )[0]
            for idx in range(50)
        }

        self.assertEqual(observed, {"H1"})

    def test_production_diverse_target_fallback_stays_near_seed(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1")
        limiter = TargetDiversityLimiter(4)
        limiter.record("XAUUSD", "H1")
        limiter.record("XAUUSD", "H4")

        target_symbol, target_period, policy = choose_diverse_target(
            seed,
            {},
            {},
            random.Random(2),
            limiter,
            ("AAPL.NAS", "TSLA.NAS", "XAGUSD", "XAUEUR"),
            {},
            production_mode=True,
        )

        self.assertIn(target_symbol, {"XAGUSD", "XAUEUR"})
        self.assertEqual(target_period, "H1")
        self.assertNotIn("asset_universe_explore", policy)

    def test_production_diverse_target_returns_none_instead_of_overflow(self) -> None:
        seed = Seed(Path("seed.set"), "ONLY", "H1", "family", "1")
        limiter = TargetDiversityLimiter(1)
        limiter.record("ONLY", "H1")

        target = choose_diverse_target(
            seed,
            {},
            {},
            random.Random(2),
            limiter,
            ("ONLY",),
            {},
            timeframe_universe=("H1",),
            production_mode=True,
        )

        self.assertIsNone(target)

    def test_target_diversity_limiter_caps_pair_and_symbol(self) -> None:
        limiter = TargetDiversityLimiter(10)

        for _ in range(3):
            self.assertTrue(limiter.allows("META", "H4"))
            limiter.record("META", "H4")

        self.assertFalse(limiter.allows("META", "H4"))
        self.assertTrue(limiter.allows("META", "H1"))
        limiter.record("META", "H1")
        limiter.record("META", "D1")
        self.assertFalse(limiter.allows("META", "M30"))
        self.assertTrue(limiter.allows("AMZN", "H4"))

    def test_target_diversity_limiter_caps_universe_group(self) -> None:
        limiter = TargetDiversityLimiter(
            10,
            group_by_symbol={
                "XAUUSD": "Metals",
                "XAGUSD": "Metals",
                "XAUEUR": "Metals",
                "META": "Stocks",
            },
        )

        for symbol in ("XAUUSD", "XAGUSD", "XAUEUR", "XAUUSD"):
            self.assertTrue(limiter.allows(symbol, "H4"))
            limiter.record(symbol, "H4")

        self.assertFalse(limiter.allows("XAUUSD", "H1"))
        self.assertFalse(limiter.allows("XAGUSD", "H1"))
        self.assertTrue(limiter.allows("META", "H4"))

    def test_symbol_map_aliases_share_feedback_identity_and_group_cap(self) -> None:
        aliases = augment_aliases_with_symbol_map(
            {"GOLD": "XAUUSD"},
            {"XAUUSD": "XAUUSD.sa", "ORPHAN": "MISSING.sa"},
            ("XAUUSD.sa", "XAGUSD.sa"),
        )

        self.assertEqual(aliases["XAUUSD"], "XAUUSD.sa")
        self.assertEqual(aliases["GOLD"], "XAUUSD.sa")
        self.assertNotIn("ORPHAN", aliases)

        limiter = TargetDiversityLimiter(
            5,
            aliases,
            group_by_symbol={"XAUUSD.SA": "Metals", "XAGUSD.SA": "Metals"},
            group_cap_ratios={"Metals": 0.2},
        )
        self.assertTrue(limiter.allows("XAUUSD", "H1"))
        limiter.record("XAUUSD", "H1")
        self.assertFalse(limiter.allows("GOLD", "H4"))
        self.assertFalse(limiter.allows("XAGUSD.sa", "M30"))

    def test_target_diversity_limiter_uses_group_specific_caps(self) -> None:
        limiter = TargetDiversityLimiter(
            10,
            group_by_symbol={
                "EURUSD": "Forex",
                "GBPUSD": "Forex",
                "USDJPY": "Forex",
                "AUDUSD": "Forex",
                "USDCAD": "Forex",
                "USDCHF": "Forex",
                "NZDUSD": "Forex",
                "XAUUSD": "Metals",
            },
        )

        for symbol in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"):
            self.assertTrue(limiter.allows(symbol, "H1"))
            limiter.record(symbol, "H1")

        self.assertFalse(limiter.allows("NZDUSD", "H4"))
        self.assertTrue(limiter.allows("XAUUSD", "H4"))

    def test_ranked_seed_selection_keeps_diverse_alternatives(self) -> None:
        seeds = [
            *(Seed(Path(f"xau_{idx}.set"), "XAUUSD", "H4", "family", "1") for idx in range(12)),
            *(Seed(Path(f"meta_{idx}.set"), "META", "H1", "family", "1") for idx in range(4)),
            *(Seed(Path(f"eur_{idx}.set"), "EURUSD", "D1", "family", "1") for idx in range(4)),
            *(Seed(Path(f"btc_{idx}.set"), "BTCUSD", "M30", "family", "1") for idx in range(4)),
        ]

        selected = ranked_seed_selection(
            seeds,
            10,
            {"XAUUSD": 100.0, "META": 20.0, "EURUSD": 15.0, "BTCUSD": 10.0},
            {"H4": 10.0, "H1": 5.0, "D1": 4.0, "M30": 3.0},
            random.Random(7),
            {},
            {"XAUUSD": "Metals", "META": "Stocks", "EURUSD": "Forex", "BTCUSD": "Crypto"},
        )

        pairs = {(seed.symbol, seed.period) for _score, seed, _asset, _tf, _div in selected}
        self.assertIn(("XAUUSD", "H4"), pairs)
        self.assertGreater(len(pairs), 1)
        self.assertLess(sum(1 for _score, seed, _asset, _tf, _div in selected if seed.symbol == "XAUUSD"), 10)

    def test_ranked_seed_selection_symbol_reserve_keeps_discovery_broad(self) -> None:
        seeds = [
            *(Seed(Path(f"xau_{idx}.set"), "XAUUSD", "H4", "family", "1") for idx in range(20)),
            Seed(Path("xti.set"), "XTIUSD", "D1", "family", "1"),
            Seed(Path("eur.set"), "EURUSD", "H1", "family", "1"),
            Seed(Path("btc.set"), "BTCUSD", "M15", "family", "1"),
            Seed(Path("us30.set"), "US30", "M30", "family", "1"),
        ]

        selected = ranked_seed_selection(
            seeds,
            10,
            {"XAUUSD": 100.0, "XTIUSD": 10.0, "EURUSD": 9.0, "BTCUSD": 8.0, "US30": 7.0},
            {"H4": 10.0, "D1": 1.0, "H1": 1.0, "M15": 1.0, "M30": 1.0},
            random.Random(11),
            {},
            {"XAUUSD": "Metals", "XTIUSD": "Energies", "EURUSD": "Forex", "BTCUSD": "Crypto", "US30": "Indices"},
            {},
            0.40,
        )

        selected_symbols = {seed.symbol for _score, seed, _asset, _tf, _div in selected}
        self.assertGreaterEqual(len(selected_symbols), 4)
        self.assertIn("XTIUSD", selected_symbols)

    def test_discovery_seed_selection_budgets_exploitation_and_cross_asset_search(self) -> None:
        live_symbols = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "NZDUSD")
        exploitable = [
            Seed(Path(f"live_{idx}.set"), symbol, "H1", "family", "1")
            for idx, symbol in enumerate(live_symbols)
        ]
        cross_asset = [
            Seed(Path(f"legacy_{idx}.set"), f"LEGACY{idx}", "H1", "family", "1")
            for idx in range(6)
        ]
        feedback = {
            **{seed.symbol: 1.0 for seed in exploitable},
            **{seed.symbol: 100.0 for seed in cross_asset},
        }

        selected = discovery_ranked_seed_selection(
            exploitable + cross_asset,
            5,
            feedback,
            {},
            random.Random(17),
            tuple(seed.symbol for seed in exploitable),
        )
        selected_symbols = [seed.symbol for _score, seed, _asset, _tf, _div in selected]

        self.assertEqual(len(selected_symbols), 5)
        self.assertEqual(sum(symbol in live_symbols for symbol in selected_symbols), 3)
        self.assertEqual(sum(symbol.startswith("LEGACY") for symbol in selected_symbols), 2)

    def test_ranked_seed_selection_applies_final_fitness_softly(self) -> None:
        ordinary = Seed(Path("ordinary.set"), "XAUUSD", "H4", "family", "1")
        compatible = Seed(Path("compatible.set"), "XAUUSD", "H4", "family", "1")

        observed = ranked_seed_selection(
            [ordinary, compatible],
            2,
            {},
            {},
            random.Random(4),
            {},
            {},
            {str(ordinary.path): -15.0, str(compatible.path): 15.0},
        )
        neutral = ranked_seed_selection(
            [ordinary, compatible],
            2,
            {},
            {},
            random.Random(4),
            {},
            {},
            {},
        )

        self.assertNotEqual(observed, neutral)
        self.assertEqual(observed[0][1], compatible)

    def test_next_seed_survivors_are_diversified_without_changing_accepted_copy_pool(self) -> None:
        dominant = [
            (
                Variant(Path(f"xau_{idx}.set"), Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"), "XAUUSD", "H4", (), (), "", (), ()),
                score(100.0 - idx),
            )
            for idx in range(12)
        ]
        alternatives = [
            (
                Variant(Path("meta.set"), Seed(Path("seed.set"), "META", "H1", "family", "1"), "META", "H1", (), (), "", (), ()),
                score(40.0, trades=100),
            ),
            (
                Variant(Path("eur.set"), Seed(Path("seed.set"), "EURUSD", "D1", "family", "1"), "EURUSD", "D1", (), (), "", (), ()),
                score(39.0, trades=100),
            ),
        ]

        selected = select_next_seed_survivors(
            dominant + alternatives,
            20.0,
            8,
            {},
            {"XAUUSD": "Metals", "META": "Stocks", "EURUSD": "Forex"},
        )

        self.assertEqual(len(selected), 8)
        self.assertTrue(any(variant.target_symbol != "XAUUSD" for variant, _result in selected))

    def test_production_survivors_do_not_backfill_rejected_scores(self) -> None:
        seed = Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1")
        rejected = [
            (
                Variant(Path("high.set"), seed, "XAUUSD", "H4", (), (), "", (), ()),
                score(100.0, accepted=False),
            ),
            (
                Variant(Path("low.set"), seed, "XAUUSD", "H1", (), (), "", (), ()),
                score(50.0, accepted=False),
            ),
        ]

        self.assertEqual(
            select_survivors(rejected, 50.0, allow_rejected_fallback=False),
            [],
        )
        self.assertEqual(
            select_next_seed_survivors(rejected, 50.0, 10, allow_rejected_fallback=False),
            [],
        )
        self.assertEqual(len(select_survivors(rejected, 50.0)), 1)

    def test_discovery_target_symbol_cap_is_stricter_than_default(self) -> None:
        limiter = TargetDiversityLimiter(
            20,
            symbol_cap_ratio=DISCOVERY_TARGET_SYMBOL_CAP_RATIO,
        )

        self.assertTrue(limiter.allows("XAUUSD", "H1"))
        limiter.record("XAUUSD", "H1")
        self.assertTrue(limiter.allows("XAUUSD", "H4"))
        limiter.record("XAUUSD", "H4")
        self.assertFalse(limiter.allows("XAUUSD", "D1"))

    def test_production_target_symbol_cap_is_tighter_than_legacy_default(self) -> None:
        limiter = TargetDiversityLimiter(
            20,
            symbol_cap_ratio=PRODUCTION_TARGET_SYMBOL_CAP_RATIO,
        )

        for period in ("M1", "M5", "M15", "M30", "H1", "H4"):
            self.assertTrue(limiter.allows("XAUUSD", period))
            limiter.record("XAUUSD", period)

        self.assertFalse(limiter.allows("XAUUSD", "D1"))

    def test_production_seed_selection_uses_strict_symbol_cap_without_overflow(self) -> None:
        symbols = (("XAUUSD", "M1"), ("BTCUSD", "M5"), ("EURUSD", "M15"), ("US30", "M30"))
        seeds = [
            Seed(Path(f"{symbol}_{idx}.set"), symbol, period, "family", "1")
            for symbol, period in symbols
            for idx in range(10)
        ]

        selected = ranked_seed_selection(
            seeds,
            12,
            {"XAUUSD": 100.0, "BTCUSD": 90.0, "EURUSD": 80.0, "US30": 70.0},
            {},
            random.Random(5),
            {},
            {},
            {},
            0.0,
            symbol_cap_ratio=PRODUCTION_SEED_SYMBOL_CAP_RATIO,
            allow_overflow=False,
        )

        counts: dict[str, int] = {}
        for _score, seed, _asset, _tf, _div in selected:
            counts[seed.symbol] = counts.get(seed.symbol, 0) + 1

        self.assertEqual(len(selected), 12)
        self.assertLessEqual(max(counts.values()), 3)

    def test_production_viable_source_seeds_filters_dead_cross_group_sources(self) -> None:
        dead = Seed(Path("xau.set"), "XAUUSD", "H1", "family", "1")
        viable = Seed(Path("eur.set"), "EURUSD", "H1", "family", "1")

        selected = production_viable_source_seeds(
            [dead, viable],
            ("EURUSD",),
            {},
            disabled_symbols={"XAUUSD"},
            group_by_symbol={"XAUUSD": "Metals", "EURUSD": "Forex"},
        )

        self.assertEqual(selected, [viable])

    def test_discovery_seed_pool_reinjects_source_seeds_without_duplicates(self) -> None:
        survivor = Seed(Path("survivor.set"), "EURUSD", "H1", "family", "1")
        source_a = Seed(Path("source_a.set"), "XTIUSD", "D1", "family", "1")
        source_b = Seed(Path("survivor.set"), "EURUSD", "H1", "family", "1")

        pool = discovery_seed_pool([survivor], [source_a, source_b])

        self.assertEqual([seed.path.name for seed in pool], ["survivor.set", "source_a.set"])
        self.assertIn("XTIUSD", {seed.symbol for seed in pool})

    def test_production_seed_pool_backfills_only_when_survivors_are_sparse(self) -> None:
        survivors = [Seed(Path(f"survivor_{idx}.set"), "EURUSD", "H1", "family", "1") for idx in range(18)]
        sources = [Seed(Path(f"source_{idx}.set"), "XAUUSD", "H1", "family", "1") for idx in range(30)]

        self.assertEqual(production_seed_pool(survivors, sources, 30), survivors)

        sparse = survivors[:3]
        pool = production_seed_pool(sparse, sources, 30)

        self.assertEqual(pool[:3], sparse)
        self.assertEqual(len(pool), 30)
        self.assertEqual(len({str(seed.path).lower() for seed in pool}), 30)

    def test_next_seed_survivors_apply_final_fitness_softly(self) -> None:
        higher_score = Variant(
            Path("higher.set"),
            Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"),
            "XAUUSD", "H4", (), (), "", (), (),
        )
        lower_score = Variant(
            Path("lower.set"),
            Seed(Path("seed.set"), "EURUSD", "H1", "family", "1"),
            "EURUSD", "H1", (), (), "", (), (),
        )

        selected = select_next_seed_survivors(
            [(higher_score, score(100.0)), (lower_score, score(50.0))],
            20.0,
            1,
            {},
            {},
            {str(higher_score.path): -15.0, str(lower_score.path): 15.0},
        )

        self.assertEqual(selected[0][0], higher_score)

    def test_next_seed_survivors_allow_fitness_to_nudge_close_scores(self) -> None:
        slightly_higher_score = Variant(
            Path("slightly_higher.set"),
            Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"),
            "XAUUSD", "H4", (), (), "", (), (),
        )
        slightly_lower_score = Variant(
            Path("slightly_lower.set"),
            Seed(Path("seed.set"), "EURUSD", "H1", "family", "1"),
            "EURUSD", "H1", (), (), "", (), (),
        )

        selected = select_next_seed_survivors(
            [(slightly_higher_score, score(52.0)), (slightly_lower_score, score(50.0))],
            20.0,
            1,
            {},
            {},
            {str(slightly_higher_score.path): -15.0, str(slightly_lower_score.path): 15.0},
        )

        self.assertEqual(selected[0][0], slightly_lower_score)

    def test_relative_delta_is_symmetric(self) -> None:
        forward = _relative_delta_pct(100.0, 135.0)
        reverse = _relative_delta_pct(135.0, 100.0)

        self.assertAlmostEqual(forward, reverse)
        self.assertAlmostEqual(forward, 35.0 / 135.0 * 100.0)

    def test_relative_delta_uses_explicit_symmetric_threshold_semantics(self) -> None:
        self.assertAlmostEqual(_relative_delta_pct(100.0, 150.0), 100.0 / 3.0)
        self.assertLessEqual(_relative_delta_pct(100.0, 150.0), 35.0)
        self.assertGreater(_relative_delta_pct(100.0, 160.0), 35.0)

    def test_reserved_timeframe_plan_targets_missing_allowed_timeframes(self) -> None:
        selected = [Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1")]

        plan = reserved_timeframe_plan(selected, ("M1", "M5", "H4", "D1"), 10)

        self.assertEqual(plan, ["M1", "M5", "D1"])

    def test_reserved_timeframe_plan_applies_minimum_intraday_quotas(self) -> None:
        selected = [
            Seed(Path("h1.set"), "META", "H1", "family", "1"),
            Seed(Path("h4.set"), "XAGUSD", "H4", "family", "1"),
            Seed(Path("d1.set"), "USDJPY", "D1", "family", "1"),
        ]

        plan = reserved_timeframe_plan(selected, ("M1", "M5", "M15", "M30", "H1", "H4", "D1"), 300)

        self.assertEqual(plan.count("M1"), 6)
        self.assertEqual(plan.count("M5"), 6)
        self.assertEqual(plan.count("M15"), 9)
        self.assertEqual(plan.count("M30"), 15)

    def test_apply_reserved_timeframe_overrides_target_when_allowed(self) -> None:
        reserved = ["M1"]
        limiter = TargetDiversityLimiter(10)

        symbol, period, policy = apply_reserved_timeframe(
            reserved_timeframes=reserved,
            target_symbol="XAUUSD",
            target_period="H4",
            policy="exploit",
            seed=Seed(Path("seed.set"), "XAUUSD", "H4", "family", "1"),
            target_limiter=limiter,
            universe_symbols=("META", "EURUSD"),
            disabled_symbols=set(),
            aliases={},
            rng=random.Random(1),
        )

        self.assertEqual(symbol, "XAUUSD")
        self.assertEqual(period, "M1")
        self.assertEqual(policy, "exploit+tf_reserved")
        self.assertEqual(reserved, [])

    def test_choose_diverse_target_avoids_capped_symbol(self) -> None:
        seed = Seed(Path("seed.set"), "META", "H4", "family", "1")
        limiter = TargetDiversityLimiter(4)
        limiter.record("META", "H4")
        limiter.record("META", "H1")
        limiter.record("META", "D1")

        target_symbol, target_period, _policy = choose_diverse_target(
            seed,
            {"META": 100.0, "AMZN": 10.0, "MSFT": 8.0},
            {"H4": 10.0, "H1": 8.0},
            random.Random(3),
            limiter,
            ("META", "AMZN", "MSFT"),
            {},
            disabled_symbols=set(),
        )

        self.assertNotEqual(target_symbol, "META")
        self.assertTrue(limiter.allows(target_symbol, target_period))

    def test_create_variant_separates_timeframe_keys_from_mutated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "seed.set"
            source.write_text(
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "ST1_Timeframe=16385||0||1||16408||Y",
                        "Entry_Timing=16385||0||1||16408||Y",
                        "ATR_Timeframe=16385||0||1||16408||Y",
                        "Exit_stop=100||50||10||150||Y",
                        "Risk=2||2||0||10||N",
                        "StartLots=0.01||0.01||0.01||1||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(source, "XAUUSD", "H1", "family", "1")

            variant = create_variant(
                seed,
                "XAUUSD",
                "D1",
                Path(temp_dir) / "out",
                1,
                1,
                1,
                1,
                {},
                {},
                "test",
                random.Random(2),
            )

            self.assertIn("ST1_Timeframe", variant.timeframe_keys)
            self.assertIn("Entry_Timing", variant.timeframe_keys)
            self.assertIn("ATR_Timeframe", variant.timeframe_keys)
            self.assertNotIn("ST1_Timeframe", variant.mutated_keys)
            self.assertEqual(len(variant.mutated_keys), 1)
            self.assertEqual(variant.mutation_details[0]["key"], variant.mutated_keys[0])

    def test_create_variant_at_lower_bound_uses_local_valid_direction_without_wrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "seed.set"
            source.write_text(
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "Run_Strategy=1||1||0||2||N",
                        "Exit_stop=50||50||10||150||Y",
                        "Risk=2||2||0||10||N",
                        "StartLots=0.01||0.01||0.01||1||N",
                    ]
                ),
                encoding="utf-8",
            )
            seed = Seed(source, "XAUUSD", "H1", "family", "1")

            variant = create_variant(
                seed,
                "XAUUSD",
                "H1",
                Path(temp_dir) / "out",
                1,
                1,
                1,
                1,
                {"Exit_stop": 10.0},
                {"Exit_stop": -10.0},
                "test",
                random.Random(2),
            )

            detail = variant.mutation_details[0]
            self.assertEqual(detail["key"], "Exit_stop")
            self.assertIn(detail["delta"], (10.0, 20.0))
            self.assertFalse(detail["wrapped"])
            self.assertEqual(detail["direction_bias_strength"], 1.0)

    def test_mutation_direction_feedback_uses_lifecycle_probability(self) -> None:
        good_rows = [
            {
                "run_id": index,
                "seed_path": f"good_{index}.set",
                "target_symbol": "XAUUSD",
                "period": "H1",
                "family": "family",
                "mutated_keys": "Exit_stop",
                "mutation_details_json": json.dumps([{"key": "Exit_stop", "delta": 10.0, "wrapped": False}]),
                "status": "accepted",
                "robust_status": "accepted",
                "final_tick_status": "accepted",
                "final_tick_6m_status": "accepted",
                "regression_status": "accepted",
            }
            for index in range(20)
        ]
        bad_rows = [
            {
                "run_id": 100 + index,
                "seed_path": f"bad_{index}.set",
                "target_symbol": "XAUUSD",
                "period": "H1",
                "family": "family",
                "mutated_keys": "Exit_stop",
                "mutation_details_json": json.dumps([{"key": "Exit_stop", "delta": -10.0, "wrapped": False}]),
                "status": "rejected",
                "robust_status": "",
                "final_tick_status": "",
                "final_tick_6m_status": "",
                "regression_status": "",
            }
            for index in range(20)
        ]
        wrapped_rows = [
            {
                "run_id": 200 + index,
                "seed_path": f"wrapped_{index}.set",
                "target_symbol": "XAUUSD",
                "period": "H1",
                "family": "family",
                "mutated_keys": "Exit_stop",
                "mutation_details_json": json.dumps(
                    [{"key": "Exit_stop", "delta": 50.0, "wrapped": True}]
                ),
                "status": "rejected",
                "robust_status": "",
                "final_tick_status": "",
                "final_tick_6m_status": "",
                "regression_status": "",
            }
            for index in range(40)
        ]
        memory = object.__new__(AgentMemory)
        memory._candidate_feedback_rows = lambda: good_rows + bad_rows + wrapped_rows

        signals = memory.mutation_direction_feedback_signals()
        feedback = memory.mutation_direction_feedback()

        self.assertGreater(signals["Exit_stop"]["up"].effective_score, 0.0)
        self.assertLess(signals["Exit_stop"]["down"].effective_score, 0.0)
        self.assertGreater(feedback["Exit_stop"], 0.0)

    def test_recreate_work_dir_removes_previous_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "work"
            path.mkdir()
            (path / "old.set").write_text("old", encoding="utf-8")

            recreated = recreate_work_dir(path)

            self.assertEqual(recreated, path)
            self.assertTrue(path.exists())
            self.assertEqual(list(path.iterdir()), [])

    def test_resolve_workspace_path_finds_relocated_outputs_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_root = root / "MT5_Autotester_agent_AXI"
            old_path = root / "MT5_Autotester_agent" / "outputs" / "ubs_agent" / "AXI" / "STANDARD" / "run_1" / "candidate.set"
            current_path = current_root / "outputs" / "ubs_agent" / "AXI" / "STANDARD" / "run_1" / "candidate.set"
            current_path.parent.mkdir(parents=True)
            current_path.write_text("set", encoding="utf-8")

            with patch("ubs.path_utils.BASE_DIR", current_root):
                self.assertEqual(resolve_workspace_path(old_path), current_path)

    def test_final_tick_similarity_requires_history_quality(self) -> None:
        result = final_tick_similarity(
            score(10.0),
            score(10.0, history_quality=None),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertFalse(result["accepted"])
        self.assertIn("history_quality", result["reasons"])

    def test_final_tick_similarity_keeps_net_profit_drift_informational(self) -> None:
        result = final_tick_similarity(
            score(10.0, net_profit=100.0),
            score(10.0, net_profit=200.0),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertTrue(result["accepted"])
        self.assertNotIn("net_profit", result["reasons"])
        self.assertFalse(result["checks"]["net_profit"]["checked"])

    def test_final_tick_similarity_rejects_large_profit_factor_drift(self) -> None:
        result = final_tick_similarity(
            score(10.0, profit_factor=2.0),
            score(10.0, profit_factor=1.0),
            min_history_quality=80.0,
            max_net_delta_pct=35.0,
            max_pf_delta_pct=35.0,
            max_dd_delta_pct=35.0,
            max_trades_delta_pct=35.0,
        )

        self.assertFalse(result["accepted"])
        self.assertIn("profit_factor", result["reasons"])

    def test_empty_real_tick_report_with_high_quality_is_pending_history_not_mismatch(self) -> None:
        class Memory:
            active_final_tick_stage = "six_month"

            def __init__(self) -> None:
                self.calls = []

            def record_candidate_final_tick(self, *args) -> None:
                self.calls.append(args)

        args = SimpleNamespace(
            broker="AXI",
            symbol_suffix=".sa",
            final_tick_min_history_quality=80.0,
            from_date="2026.01.01",
            to_date="2026.06.30",
            final_tick_max_net_delta_pct=35.0,
            final_tick_max_pf_delta_pct=35.0,
            final_tick_max_dd_delta_pct=35.0,
            final_tick_max_trades_delta_pct=35.0,
            final_tick_min_trades_w1=2,
            final_tick_min_trades_mn=1,
        )
        variant = Variant(
            path=Path("tick.set"),
            seed=Seed(Path("seed.set"), "BTCUSD", "H1", "family", "1"),
            target_symbol="BTCUSD",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="final_tick_real",
        )
        memory = Memory()
        status_counts: dict[str, int] = {}

        with patch(
            "ubs_agent.score_report_file",
            # MT5 may retain 99% quality even though the actual tester context
            # is empty.  The empty symbol/M0 pair must take precedence.
            return_value=score(-55.0, symbol="", timeframe="M0", trades=0, history_quality=99.0),
        ):
            handled = _evaluate_final_tick_tick_report(
                memory,
                args,
                ScoreConfig(),
                {},
                5,
                3672,
                variant,
                Path("ohlc.htm"),
                score(90.0, symbol="BTCUSD.sa", timeframe="H1", trades=44),
                Path("tick.htm"),
                status_counts,
            )

        self.assertTrue(handled)
        self.assertEqual(memory.calls[0][2], "pending_history_quality")
        self.assertIsNone(memory.calls[0][4])
        self.assertEqual(status_counts, {"pending_history_quality": 1})
        similarity = json.loads(memory.calls[0][7])
        self.assertEqual(similarity["reasons"], ["empty_tester_context"])
        self.assertEqual(similarity["history_quality"], 99.0)
        self.assertEqual(similarity["min_history_quality"], 80.0)

    def test_final_tick_rescore_forwards_broker_to_real_tick_parser(self) -> None:
        class Cursor:
            def __init__(self, rows) -> None:
                self.rows = rows

            def fetchall(self):
                return self.rows

        class Connection:
            def __init__(self, rows) -> None:
                self.rows = rows

            def execute(self, _query):
                return Cursor(self.rows)

        class Memory:
            def __init__(self, rows) -> None:
                self.conn = Connection(rows)
                self.path = Path("memory.sqlite")
                self.active_final_tick_stage = "probe"

        with tempfile.TemporaryDirectory() as temp_dir:
            ohlc_report = Path(temp_dir) / "ohlc.htm"
            tick_report = Path(temp_dir) / "tick.htm"
            ohlc_report.touch()
            tick_report.touch()
            row = {
                "id": 42,
                "run_id": 7,
                "ft_run_id": 7,
                "ft_ohlc_report_path": str(ohlc_report),
                "ft_real_tick_report_path": str(tick_report),
                "ft_from_date": "2026.05.01",
                "ft_to_date": "2026.05.31",
            }
            args = SimpleNamespace(
                broker="ICTRADING",
                symbol_map="",
                symbol_suffix="",
                rescore_from_reports=True,
                final_tick_stage="probe",
                from_date="2026.05.01",
                to_date="2026.05.31",
                final_tick_min_history_quality=80.0,
                final_tick_min_ohlc_trades=4,
                final_tick_min_trades_w1=2,
                final_tick_min_trades_mn=0,
                final_tick_max_net_delta_pct=35.0,
                final_tick_max_pf_delta_pct=35.0,
                final_tick_max_dd_delta_pct=35.0,
                final_tick_max_trades_delta_pct=35.0,
            )
            variant = Variant(
                path=Path("candidate.set"),
                seed=Seed(Path("seed.set"), "EURUSD", "H1", "family", "1"),
                target_symbol="EURUSD",
                target_period="H1",
                mutated_keys=(),
                missing_lot_keys=(),
                policy="generated",
            )
            with (
                patch("ubs_agent.variant_from_candidate_row", return_value=variant),
                patch("ubs_agent._read_ohlc_report_cfg_dates", return_value=("", "")),
                patch("ubs_agent.score_report_file", return_value=score(80.0, symbol="EURUSD", timeframe="H1", trades=10)),
                patch("ubs_agent.report_matches_variant", return_value=(True, "")),
                patch("ubs_agent._evaluate_final_tick_tick_report") as evaluate_tick,
            ):
                rescore_final_tick_only(args, Memory([row]), ScoreConfig())

            forwarded_args = evaluate_tick.call_args.args[1]
            self.assertEqual(forwarded_args.broker, "ICTRADING")

    def test_final_tick_reconcile_forwards_broker_and_period_thresholds(self) -> None:
        class Memory:
            active_final_tick_stage = "probe"

            def __init__(self, set_path: Path) -> None:
                self.set_path = set_path

            def accepted_candidates_for_final_tick(self, _run_id, *, final_tick_stage):
                self.active_final_tick_stage = final_tick_stage
                return [{
                    "id": 42,
                    "set_path": str(self.set_path),
                    "final_tick_status": "",
                }]

        with tempfile.TemporaryDirectory() as temp_dir:
            source_set = Path(temp_dir) / "candidate.set"
            ohlc_report = Path(temp_dir) / "ohlc.htm"
            tick_report = Path(temp_dir) / "tick.htm"
            source_set.touch()
            ohlc_report.touch()
            tick_report.touch()
            variant = Variant(
                path=source_set,
                seed=Seed(Path("seed.set"), "EURUSD", "H1", "family", "1"),
                target_symbol="EURUSD",
                target_period="H1",
                mutated_keys=(),
                missing_lot_keys=(),
                policy="generated",
            )
            with (
                patch("ubs_agent.find_report_for_set", side_effect=[ohlc_report, tick_report]),
                patch(
                    "ubs_agent._read_ohlc_report_cfg_dates",
                    return_value=("2026.01.01", "2026.06.30"),
                ),
                patch("ubs_agent.variant_from_candidate_row", return_value=variant),
                patch(
                    "ubs_agent.score_report_file",
                    return_value=score(80.0, symbol="EURUSD", timeframe="H1", trades=10),
                ) as score_report,
                patch("ubs_agent.report_matches_variant", return_value=(True, "")),
                patch("ubs_agent._evaluate_final_tick_tick_report", return_value=True) as evaluate_tick,
            ):
                reconcile_final_tick_reports(
                    Memory(source_set),
                    7,
                    ScoreConfig(),
                    {},
                    broker="ICTRADING",
                    min_ohlc_trades=4,
                    min_trades_w1=7,
                    min_trades_mn=3,
                    symbol_suffix=".a",
                )

            self.assertEqual(score_report.call_args.kwargs["broker"], "ICTRADING")
            forwarded_args = evaluate_tick.call_args.args[1]
            self.assertEqual(forwarded_args.broker, "ICTRADING")
            self.assertEqual(forwarded_args.final_tick_min_trades_w1, 7)
            self.assertEqual(forwarded_args.final_tick_min_trades_mn, 3)
            self.assertEqual(forwarded_args.symbol_suffix, ".a")

    def test_final_tick_reconcile_cli_forwards_all_parser_context(self) -> None:
        args = SimpleNamespace(
            final_tick_stage="six_month",
            final_tick_reconcile_only=True,
            final_tick_run_id=7,
            broker="ICTRADING",
            symbol_map="EURUSD=EURUSD.a",
            symbol_suffix=".a",
            final_tick_min_history_quality=91.0,
            final_tick_min_ohlc_trades=8,
            final_tick_min_trades_w1=7,
            final_tick_min_trades_mn=3,
            final_tick_max_net_delta_pct=31.0,
            final_tick_max_pf_delta_pct=29.0,
            final_tick_max_dd_delta_pct=27.0,
            final_tick_max_trades_delta_pct=25.0,
        )
        memory = SimpleNamespace(
            run_by_id=lambda _run_id: {"id": 7},
            latest_run=lambda: None,
            path=Path("memory.sqlite"),
        )
        with patch("ubs_agent.reconcile_final_tick_reports", return_value={}) as reconcile:
            code = evaluate_candidate_final_tick(args, memory, ScoreConfig())

        self.assertEqual(code, 0)
        kwargs = reconcile.call_args.kwargs
        self.assertEqual(kwargs["broker"], "ICTRADING")
        self.assertEqual(kwargs["final_tick_stage"], "six_month")
        self.assertEqual(kwargs["min_trades_w1"], 7)
        self.assertEqual(kwargs["min_trades_mn"], 3)
        self.assertEqual(kwargs["symbol_suffix"], ".a")

    def test_empty_real_tick_report_with_no_history_log_stays_pending(self) -> None:
        class Memory:
            active_final_tick_stage = "six_month"

            def __init__(self) -> None:
                self.calls = []

            def record_candidate_final_tick(self, *args) -> None:
                self.calls.append(args)

        args = SimpleNamespace(
            broker="AXI",
            symbol_suffix=".sa",
            final_tick_min_history_quality=80.0,
            from_date="2026.01.01",
            to_date="2026.06.30",
            final_tick_max_net_delta_pct=35.0,
            final_tick_max_pf_delta_pct=35.0,
            final_tick_max_dd_delta_pct=35.0,
            final_tick_max_trades_delta_pct=35.0,
            final_tick_min_trades_w1=2,
            final_tick_min_trades_mn=1,
        )
        variant = Variant(
            path=Path("tick.set"),
            seed=Seed(Path("seed.set"), "BTCUSD", "H1", "family", "1"),
            target_symbol="MSFT",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="final_tick_real",
        )
        memory = Memory()
        status_counts: dict[str, int] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "tick.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name("tick.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tMSFT.sa: preliminary downloading of history ticks started, it may take quite a long time",
                        "Tester\tMSFT.sa: preliminary downloading of history ticks canceled",
                        "Tester\tno history data, stop testing",
                    ]
                ),
                encoding="utf-8",
            )
            with patch(
                "ubs_agent.score_report_file",
                return_value=score(-55.0, symbol="", timeframe="M0", trades=0, history_quality=0.0),
            ):
                handled = _evaluate_final_tick_tick_report(
                    memory,
                    args,
                    ScoreConfig(),
                    {},
                    31,
                    21393,
                    variant,
                    Path("ohlc.htm"),
                    score(175.0, symbol="MSFT.sa", timeframe="H1", trades=24),
                    report,
                    status_counts,
                )

        self.assertTrue(handled)
        self.assertEqual(memory.calls[0][2], "pending_history_quality")
        self.assertEqual(status_counts, {"pending_history_quality": 1})
        similarity = json.loads(memory.calls[0][7])
        self.assertEqual(similarity["reasons"], ["real_tick_no_history"])
        self.assertTrue(similarity["technical_failure"])
        self.assertEqual(similarity["history"]["failure_type"], "tick_history_sync")
        self.assertTrue(similarity["history"]["retryable"])

    def test_zero_trade_real_tick_with_valid_context_is_rejected(self) -> None:
        class Memory:
            active_final_tick_stage = "six_month"

            def __init__(self) -> None:
                self.calls = []

            def record_candidate_final_tick(self, *args) -> None:
                self.calls.append(args)

        args = SimpleNamespace(
            broker="AXI",
            symbol_suffix=".sa",
            final_tick_min_history_quality=80.0,
            from_date="2026.01.01",
            to_date="2026.06.30",
            final_tick_max_net_delta_pct=35.0,
            final_tick_max_pf_delta_pct=35.0,
            final_tick_max_dd_delta_pct=35.0,
            final_tick_max_trades_delta_pct=35.0,
            final_tick_min_trades_w1=2,
            final_tick_min_trades_mn=1,
        )
        variant = Variant(
            path=Path("tick.set"),
            seed=Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1"),
            target_symbol="XAUUSD",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="final_tick_real",
        )
        memory = Memory()
        status_counts: dict[str, int] = {}

        with patch(
            "ubs_agent.score_report_file",
            return_value=score(
                -55.0,
                symbol="XAUUSD.sa",
                timeframe="H1",
                trades=0,
                net_profit=0.0,
                profit_factor=0.0,
                history_quality=99.0,
            ),
        ):
            handled = _evaluate_final_tick_tick_report(
                memory,
                args,
                ScoreConfig(min_net_profit=20.0, min_profit_factor=1.2, min_trades=46),
                {},
                5,
                3850,
                variant,
                Path("ohlc.htm"),
                score(90.0, symbol="XAUUSD.sa", timeframe="H1", trades=44, profit_factor=2.0),
                Path("tick.htm"),
                status_counts,
            )

        self.assertTrue(handled)
        self.assertEqual(memory.calls[0][2], "rejected")
        self.assertEqual(status_counts, {"rejected": 1})
        similarity = json.loads(memory.calls[0][7])
        self.assertFalse(similarity["accepted"])
        self.assertIn("trades", similarity["reasons"])

    def test_robust_pending_retry_includes_diagnostic_statuses(self) -> None:
        for status in ("", None, "no_report", "parse_error", "report_mismatch", "no_trades"):
            self.assertTrue(robust_status_pending_for_retry(status))
        for status in ("accepted", "rejected"):
            self.assertFalse(robust_status_pending_for_retry(status))


if __name__ == "__main__":
    unittest.main()
