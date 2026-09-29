import ubs_agent_final_tick_pass
import ubs_agent_final_tick
import ubs_agent_final_tick_rescore
import ubs_agent_reports
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs_agent import (
    evaluate_candidate_final_tick,
    _evaluate_final_tick_tick_report,
    reconcile_final_tick_reports,
    reconcile_final_tick_reports,
    robust_status_pending_for_retry,
    rescore_final_tick_only,
)
from tests.ubs_agent_files_fixtures import score


class _Cursor:
    def __init__(self, rows) -> None:
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows) -> None:
        self.rows = rows

    def execute(self, _query):
        return _Cursor(self.rows)


class _RowsMemory:
    """Memoria falsa que sirve filas ya preparadas por `conn.execute`."""

    def __init__(self, rows) -> None:
        self.conn = _Connection(rows)
        self.path = Path("memory.sqlite")
        self.active_final_tick_stage = "probe"


def _axi_tick_args() -> SimpleNamespace:
    """Argumentos AXI de la pata real tick usados por varias pruebas."""
    return SimpleNamespace(
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


class _PendingRowMemory:
    """Memoria falsa con un unico candidato pendiente de Final Tick."""

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


class _RecordingMemory:
    """Memoria falsa que solo apunta las llamadas a record_candidate_final_tick."""

    active_final_tick_stage = "six_month"

    def __init__(self) -> None:
        self.calls = []

    def record_candidate_final_tick(self, *args) -> None:
        self.calls.append(args)


class UBSAgentReconcileTests(unittest.TestCase):
    def test_final_tick_rescore_forwards_broker_to_real_tick_parser(self) -> None:
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
                patch("ubs_agent_final_tick_rescore.variant_from_candidate_row", return_value=variant),
                patch("ubs_agent_final_tick_rescore._read_ohlc_report_cfg_dates", return_value=("", "")),
                patch("ubs_agent_final_tick_rescore.score_report_file", return_value=score(80.0, symbol="EURUSD", timeframe="H1", trades=10)),
                patch("ubs_agent_final_tick_rescore.report_matches_variant", return_value=(True, "")),
                patch("ubs_agent_final_tick_rescore._evaluate_final_tick_tick_report") as evaluate_tick,
            ):
                rescore_final_tick_only(args, _RowsMemory([row]), ScoreConfig())

            forwarded_args = evaluate_tick.call_args.args[1]
            self.assertEqual(forwarded_args.broker, "ICTRADING")

    def test_final_tick_reconcile_forwards_broker_and_period_thresholds(self) -> None:
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
                patch("ubs_agent_final_tick_rescore.find_report_for_set", side_effect=[ohlc_report, tick_report]),
                patch(
                    "ubs_agent_final_tick_rescore._read_ohlc_report_cfg_dates",
                    return_value=("2026.01.01", "2026.06.30"),
                ),
                patch("ubs_agent_final_tick_rescore.variant_from_candidate_row", return_value=variant),
                patch(
                    "ubs_agent_final_tick_rescore.score_report_file",
                    return_value=score(80.0, symbol="EURUSD", timeframe="H1", trades=10),
                ) as score_report,
                patch("ubs_agent_final_tick_rescore.report_matches_variant", return_value=(True, "")),
                patch("ubs_agent_final_tick_rescore._evaluate_final_tick_tick_report", return_value=True) as evaluate_tick,
            ):
                reconcile_final_tick_reports(
                    _PendingRowMemory(source_set),
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
        with patch("ubs_agent_final_tick_pass.reconcile_final_tick_reports", return_value={}) as reconcile:
            code = evaluate_candidate_final_tick(args, memory, ScoreConfig())

        self.assertEqual(code, 0)
        kwargs = reconcile.call_args.kwargs
        self.assertEqual(kwargs["broker"], "ICTRADING")
        self.assertEqual(kwargs["final_tick_stage"], "six_month")
        self.assertEqual(kwargs["min_trades_w1"], 7)
        self.assertEqual(kwargs["min_trades_mn"], 3)
        self.assertEqual(kwargs["symbol_suffix"], ".a")

    def test_empty_real_tick_report_with_no_history_log_stays_pending(self) -> None:
        args = _axi_tick_args()
        variant = Variant(
            path=Path("tick.set"),
            seed=Seed(Path("seed.set"), "BTCUSD", "H1", "family", "1"),
            target_symbol="MSFT",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="final_tick_real",
        )
        memory = _RecordingMemory()
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
                "ubs_agent_final_tick.score_report_file",
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
        args = _axi_tick_args()
        variant = Variant(
            path=Path("tick.set"),
            seed=Seed(Path("seed.set"), "XAUUSD", "H1", "family", "1"),
            target_symbol="XAUUSD",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="final_tick_real",
        )
        memory = _RecordingMemory()
        status_counts: dict[str, int] = {}

        with patch(
            "ubs_agent_final_tick.score_report_file",
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
