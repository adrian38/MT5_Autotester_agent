import ubs_agent_final_tick
import json
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreConfig
from ubs.memory import AgentMemory
from ubs_agent import (
    TargetDiversityLimiter,
    choose_diverse_target,
    create_variant,
    final_tick_similarity,
    _evaluate_final_tick_tick_report,
    recreate_work_dir,
    resolve_workspace_path,
)
from tests.ubs_agent_files_fixtures import score


def _axi_tick_args() -> SimpleNamespace:
    """Argumentos AXI de la pata real tick."""
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


class _RecordingMemory:
    """Memoria falsa que solo apunta las llamadas a record_candidate_final_tick."""

    active_final_tick_stage = "six_month"

    def __init__(self) -> None:
        self.calls = []

    def record_candidate_final_tick(self, *args) -> None:
        self.calls.append(args)


class UBSAgentVariantTests(unittest.TestCase):

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

    @staticmethod
    def _mutation_direction_rows() -> list[dict]:
        """Veinte subidas aceptadas, veinte bajadas rechazadas y cuarenta con wrap."""
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
        return good_rows + bad_rows + wrapped_rows

    def test_mutation_direction_feedback_uses_lifecycle_probability(self) -> None:
        memory = object.__new__(AgentMemory)
        memory._candidate_feedback_rows = self._mutation_direction_rows

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
        args = _axi_tick_args()
        variant = Variant(
            path=Path("tick.set"),
            seed=Seed(Path("seed.set"), "BTCUSD", "H1", "family", "1"),
            target_symbol="BTCUSD",
            target_period="H1",
            mutated_keys=(),
            missing_lot_keys=(),
            policy="final_tick_real",
        )
        memory = _RecordingMemory()
        status_counts: dict[str, int] = {}

        with patch(
            "ubs_agent_final_tick.score_report_file",
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
