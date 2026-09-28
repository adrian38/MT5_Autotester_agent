import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs.memory import AgentMemory
from ubs_agent import (
    classify_zero_trade_robustness,
    create_history_probe_variant,
    evaluate_history_probe,
    evaluate_seed_report,
    evaluate_variant_report,
    reconcile_seed_eval_reports,
    tester_log_no_history_metadata,
)
from tests.ubs_agent_files_fixtures import score


class UBSAgentStatusTests(unittest.TestCase):
    def test_memory_migrates_legacy_no_trades_with_trade_block_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_path = root / "memory.sqlite"
            report = root / "GBPTRY_M30_report.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "failed buy stop 0.01 GBPTRY [Only position closing is allowed]",
                encoding="utf-8",
            )
            memory = AgentMemory(memory_path)
            run_id = memory.create_run(root / "source", root / "output", 1, 1, 10, True, False)
            seed = Seed(root / "seed.set", "GBPTRY", "M30", "family", "1")
            variant = Variant(root / "candidate.set", seed, "GBPTRY", "M30", (), (), "test")
            memory.record_variant(run_id, 1, variant)
            memory.record_score(
                variant.path,
                score(-55.0, symbol="GBPTRY", timeframe="M30", trades=0),
                "no_trades",
                report,
            )
            memory.close()

            migrated = AgentMemory(memory_path)
            try:
                row = migrated.conn.execute(
                    "select status, score, accepted, metrics_json from candidates where set_path=?",
                    (str(variant.path),),
                ).fetchone()
                self.assertEqual(row["status"], "trade_disabled")
                self.assertIsNone(row["score"])
                self.assertIsNone(row["accepted"])
                self.assertEqual(json.loads(row["metrics_json"])["trade_mode"], "close_only")
            finally:
                migrated.close()
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

    def test_robust_zero_trades_with_invalid_stops_is_rejected_with_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "robust_PLUGPOWER_H1.htm"
            report.write_text("<html></html>", encoding="utf-8")
            report.with_name(f"{report.stem}.mt5log.txt").write_text(
                "\n".join(
                    [
                        "Tester\tPlugPower+,H1: testing of Experts\\Advisors\\EA.ex5",
                        "Core 01\tPlugPower+,H1: testing of Experts\\Advisors\\EA.ex5 started with inputs:",
                        "Core 01\tfailed sell stop 1 PlugPower+ at 4.02 sl: 23.02 tp: -7.98 [Invalid stops]",
                        "Core 01\tfailed sell stop 1 PlugPower+ at 4.02 sl: 23.02 tp: -7.98 [Invalid stops]",
                        "Core 01\tPlugPower+,H1: 184444 ticks, 2454 bars generated. Test passed",
                    ]
                ),
                encoding="utf-8",
            )
            variant = Variant(
                root / "candidate.set",
                Seed(root / "seed.set", "EURUSD", "H1", "family", "1"),
                "PlugPower+",
                "H1",
                (),
                (),
                "test+robustness",
            )

            status, metadata = classify_zero_trade_robustness(report, variant)

            self.assertEqual(status, "rejected")
            self.assertEqual(metadata["failure_type"], "invalid_stops")
            self.assertEqual(metadata["reasons"], ["invalid_stops"])
            self.assertEqual(metadata["invalid_order_count"], 2)
        self.assertFalse(metadata["retryable"])

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
