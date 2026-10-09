import ubs_agent_final_tick_entry
import ubs_agent_sets
import ubs_agent_final_tick
import ubs_agent_final_tick_ohlc
import ubs_agent_final_tick_pass
import ubs_agent_universe
import contextlib
import io
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs_agent import (
    evaluate_candidate_final_tick,
    final_tick_row_pending_for_dates,
    final_tick_ohlc_retry_needed_for_dates,
    final_tick_ohlc_retry_exhausted_for_dates,
    final_tick_stage_prefixes,
    run_backtests,
    validate_final_tick_stage_dates,
)
from tests.ubs_agent_files_fixtures import score


class UBSAgentFinalTickPassTests(unittest.TestCase):
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

    def test_empty_legacy_ohlc_shell_remains_retryable_on_same_dates(self) -> None:
        row = {
            "final_tick_status": "pending_ohlc_trades",
            "final_tick_from_date": "2025.09.01",
            "final_tick_to_date": "2026.06.30",
            "ft_ohlc_metrics_json": json.dumps({"trades": 0, "history_quality": 0.0}),
        }

        self.assertTrue(
            final_tick_row_pending_for_dates(
                row,
                "2025.09.01",
                "2026.06.30",
                final_tick_stage="six_month",
            )
        )
        self.assertFalse(
            final_tick_ohlc_retry_exhausted_for_dates(
                row,
                "2025.09.01",
                "2026.06.30",
            )
        )

    def test_empty_ohlc_report_is_stored_as_retryable_technical_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "ohlc.htm"
            report.write_text(
                "<td>Bars:</td><td><b>0</b></td><td>Ticks:</td><td><b>0</b></td>",
                encoding="utf-8",
            )
            variant = Variant(
                path=root / "candidate.set",
                seed=Seed(root / "seed.set", "BTCUSD", "H1", "family", "1"),
                target_symbol="BTCUSD",
                target_period="H1",
                mutated_keys=(),
                missing_lot_keys=(),
                policy="test",
            )
            args = SimpleNamespace(
                broker="ICTRADING",
                symbol_suffix="",
                final_tick_min_trades_w1=2,
                final_tick_min_trades_mn=0,
            )
            recorder = Mock()
            with (
                patch.object(ubs_agent_final_tick_ohlc, "find_report_for_set", return_value=report),
                patch.object(
                    ubs_agent_final_tick_ohlc, "score_report_file",
                    return_value=score(
                        -75.0, symbol="BTCUSD", timeframe="H1", trades=0,
                        history_quality=0.0, accepted=False,
                    ),
                ),
                patch.object(
                    ubs_agent_final_tick_ohlc, "tester_log_no_history_metadata",
                    return_value={"reasons": ["no_history_data"]},
                ),
            ):
                result = ubs_agent_final_tick_ohlc._fresh_ohlc_result(
                    args, ScoreConfig(), {}, {"id": 85666}, variant, 0.0, 4, recorder,
                )

            self.assertIsNone(result)
            self.assertEqual(recorder.write.call_args.args[:2], (85666, "no_report"))
            self.assertNotIn(-75.0, recorder.write.call_args.args)
            payload = json.loads(recorder.write.call_args.kwargs["details"])
            self.assertEqual(payload["reasons"], ["empty_ohlc_context"])
            self.assertTrue(payload["technical_failure"])

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

        with patch("ubs_agent_final_tick_entry._evaluate_candidate_final_tick_pass", side_effect=fake_pass):
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
            "ubs_agent_final_tick_entry._evaluate_candidate_final_tick_pass", return_value=0
        ) as pass_mock:
            self.assertEqual(evaluate_candidate_final_tick(args, Mock(), ScoreConfig()), 0)
        self.assertEqual(pass_mock.call_count, 1)

        def failing_pass(_args, _memory, _score, *, allow_ohlc_retry=True, deferred_out=None):
            if deferred_out is not None:
                deferred_out.append({"id": 1})
            return 1

        with patch(
            "ubs_agent_final_tick_entry._evaluate_candidate_final_tick_pass", side_effect=failing_pass
        ) as pass_mock:
            self.assertEqual(evaluate_candidate_final_tick(args, Mock(), ScoreConfig()), 1)
        self.assertEqual(pass_mock.call_count, 1)

    @staticmethod
    def _quality_retry_rows(root: Path) -> list[dict]:
        """Dos filas pendientes por calidad con pata OHLC y dos que no lo estan."""
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

        return [
            candidate_row(1, "pending_history_quality", with_ohlc=True),
            candidate_row(2, "pending_history_quality", with_ohlc=True),
            candidate_row(3, "", with_ohlc=False),
            candidate_row(4, "report_mismatch", with_ohlc=False),
        ]

    @staticmethod
    def _quality_retry_args() -> SimpleNamespace:
        return SimpleNamespace(
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

    def test_quality_retry_selects_only_pending_history_quality_rows(self) -> None:
        """``--final-tick-retry-pending-quality`` restringe, no amplia.

        Va siempre con ``--final-tick-skip-ohlc``, que solo puede servir filas
        con la pata OHLC guardada; el manager cuenta exactamente ese conjunto
        para decidir si lanza la etapa.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            rows = self._quality_retry_rows(root)
            memory = SimpleNamespace(
                active_final_tick_stage="six_month",
                run_by_id=lambda _run_id: {"id": 437, "output_dir": str(root)},
                latest_run=lambda: None,
                accepted_candidates_for_final_tick=Mock(return_value=rows),
                record_candidate_final_tick=Mock(),
                path=root / "memory.sqlite",
            )
            args = self._quality_retry_args()
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
                patch("ubs_agent_final_tick_pass.split_retired_symbols", side_effect=lambda r, _a: (r, [])),
                patch("ubs_agent_final_tick_pass.variant_from_candidate_row", return_value=variant),
                patch("ubs_agent_sets.write_set_use_every_tick"),
                patch(
                    "ubs_agent_final_tick._read_ohlc_report_cfg_dates",
                    return_value=("2026.01.01", "2026.06.30"),
                ),
                contextlib.redirect_stdout(output),
            ):
                code = evaluate_candidate_final_tick(args, memory, ScoreConfig())

            printed = output.getvalue()
            self.assertEqual(code, 0)
            self.assertNotIn("faltan ohlc_metrics_json", printed)
            self.assertIn("candidatos=2", printed)
