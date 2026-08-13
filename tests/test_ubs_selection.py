import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ubs.memory import AgentMemory
from ubs.selection import (
    SelectionFitnessModel,
    _batch_logistic_gradients,
    _sigmoid,
    descendant_fitness_predictions,
    finalized_six_month_label,
    finalized_robustness_label,
    estimate_discovery_source_mix,
    estimate_discovery_target_policy_mix,
)


def metrics(*, profit_factor: float = 1.6, recovery: float = 5.0, drawdown: float = 5.0) -> str:
    return json.dumps(
        {
            "profit_factor": profit_factor,
            "recovery_factor": recovery,
            "drawdown_pct": drawdown,
            "trades": 300,
            "positive_month_ratio": 0.65,
            "max_month_concentration": 0.08,
            "sqn": 3.0,
        }
    )


class UBSSelectionFitnessTests(unittest.TestCase):
    def test_discovery_history_excludes_production_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AgentMemory(Path(tmp) / "memory.sqlite")
            try:
                discovery_id = memory.create_run(
                    Path("seeds"),
                    Path("output"),
                    1,
                    1,
                    1,
                    False,
                    True,
                    config={"args": {"generation_mode": "discovery"}},
                )
                memory.create_run(
                    Path("seeds"),
                    Path("output"),
                    1,
                    1,
                    1,
                    False,
                    True,
                    config={"args": {"generation_mode": "production"}},
                )

                self.assertEqual(memory._discovery_run_ids(limit=10), [discovery_id])
            finally:
                memory.close()

    def test_descendant_fitness_ranks_sources_by_accepted_6m_children(self) -> None:
        rows = []
        for run_id in range(1, 6):
            rows.append(
                {
                    "run_id": run_id,
                    "generation": 1,
                    "seed_path": "winner.set",
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "accepted",
                    "final_tick_6m_status": "accepted",
                }
            )
            rows.append(
                {
                    "run_id": run_id,
                    "generation": 1,
                    "seed_path": "loser.set",
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "accepted",
                    "final_tick_6m_status": "rejected",
                }
            )

        predictions = descendant_fitness_predictions(
            rows,
            ["winner.set", "loser.set", "unknown.set"],
        )

        self.assertGreater(predictions["winner.set"].weight, 0.0)
        self.assertLess(predictions["loser.set"].weight, 0.0)
        self.assertEqual(predictions["unknown.set"].weight, 0.0)
        self.assertEqual(predictions["unknown.set"].evidence, 0.0)

    def test_discovery_target_policy_favors_feedback_and_scales_weak_unseeded(self) -> None:
        def outcome_row(policy: str, accepted: bool) -> dict[str, object]:
            return {
                "run_id": 1,
                "policy": policy,
                "status": "accepted" if accepted else "rejected",
                "robust_status": "accepted" if accepted else "",
                "final_tick_status": "accepted" if accepted else "",
                "final_tick_6m_status": "accepted" if accepted else "",
            }

        rows = []
        rows.extend(
            outcome_row("asset_unseeded_group_feedback", i < 2)
            for i in range(40)
        )
        rows.extend(
            outcome_row("exploit", i < 140)
            for i in range(200)
        )
        rows.extend(
            outcome_row("asset_universe_feedback", i < 30)
            for i in range(50)
        )
        rows.extend(
            outcome_row("asset_universe_explore", i < 5)
            for i in range(50)
        )

        mix = estimate_discovery_target_policy_mix(rows)

        self.assertTrue(mix.adaptive_unseeded)
        self.assertGreaterEqual(mix.unseeded_multiplier, 0.25)
        self.assertLess(mix.unseeded_multiplier, 0.50)
        self.assertTrue(mix.adaptive_universe_feedback)
        self.assertGreater(mix.universe_feedback_probability, 0.55)
        self.assertLessEqual(mix.universe_feedback_probability, 0.85)
        self.assertTrue(mix.adaptive_current_target)
        self.assertGreater(mix.current_target_probability, 0.55)
        self.assertFalse(mix.adaptive_current_timeframe)
        self.assertEqual(mix.current_timeframe_probability, 0.60)

    def test_current_target_routing_uses_lifecycle_not_base_acceptance(self) -> None:
        rows = []
        for index in range(6):
            common = {
                "run_id": 1,
                "generation": 1,
                "status": "accepted",
                "robust_status": "accepted",
                "final_tick_status": "accepted",
            }
            rows.append(
                {
                    **common,
                    "seed_path": f"current_{index}.set",
                    "policy": "exploit+tf_exploit",
                    "final_tick_6m_status": "rejected",
                }
            )
            rows.append(
                {
                    **common,
                    "seed_path": f"cross_{index}.set",
                    "policy": "asset_universe_explore+tf_exploit",
                    "final_tick_6m_status": "accepted",
                }
            )

        mix = estimate_discovery_target_policy_mix(
            rows,
            minimum_trials=1,
            minimum_benchmark_trials=1,
        )

        self.assertTrue(mix.adaptive_current_target)
        self.assertGreater(
            mix.cross_target_lifecycle_probability,
            mix.current_target_lifecycle_probability,
        )
        self.assertEqual(mix.current_target_probability, 0.55)

    def test_current_timeframe_routing_uses_lifecycle_evidence(self) -> None:
        rows = []
        for index in range(6):
            common = {
                "run_id": 1,
                "generation": 1,
                "status": "accepted",
                "robust_status": "accepted",
                "final_tick_status": "accepted",
                "source_period": "H1",
            }
            rows.append(
                {
                    **common,
                    "seed_path": f"current_tf_{index}.set",
                    "policy": "exploit+tf_exploit",
                    "target_period": "H1",
                    "final_tick_6m_status": "accepted",
                }
            )
            rows.append(
                {
                    **common,
                    "seed_path": f"changed_tf_{index}.set",
                    "policy": "exploit+tf_explore",
                    "target_period": "M30",
                    "final_tick_6m_status": "rejected",
                }
            )

        mix = estimate_discovery_target_policy_mix(rows)

        self.assertTrue(mix.adaptive_current_timeframe)
        self.assertGreater(
            mix.current_timeframe_lifecycle_probability,
            mix.changed_timeframe_lifecycle_probability,
        )
        self.assertGreater(mix.current_timeframe_probability, 0.60)
        self.assertLessEqual(mix.current_timeframe_probability, 0.80)

    def test_discovery_target_policy_keeps_defaults_without_evidence(self) -> None:
        mix = estimate_discovery_target_policy_mix([])

        self.assertFalse(mix.adaptive_unseeded)
        self.assertFalse(mix.adaptive_universe_feedback)
        self.assertFalse(mix.adaptive_current_target)
        self.assertEqual(mix.unseeded_multiplier, 1.0)
        self.assertEqual(mix.universe_feedback_probability, 0.55)
        self.assertEqual(mix.current_target_probability, 0.70)

    def test_universe_feedback_routing_uses_lifecycle_when_final_evidence_exists(self) -> None:
        rows = []
        for index in range(3):
            rows.append(
                {
                    "run_id": index + 1,
                    "generation": 1,
                    "seed_path": f"feedback-{index}",
                    "policy": "asset_universe_feedback+tf_exploit",
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "accepted",
                    "final_tick_6m_status": "accepted",
                    "regression_status": "accepted",
                }
            )
        for index in range(3):
            rows.append(
                {
                    "run_id": index + 1,
                    "generation": 1,
                    "seed_path": f"explore-{index}",
                    "policy": "asset_universe_explore+tf_exploit",
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "accepted",
                    "final_tick_6m_status": "rejected",
                    "regression_status": "",
                }
            )

        mix = estimate_discovery_target_policy_mix(rows, minimum_trials=100)

        self.assertTrue(mix.universe_feedback_lifecycle_adaptive)
        self.assertGreater(
            mix.universe_feedback_probability,
            0.55,
        )
        self.assertGreater(
            mix.universe_feedback_lifecycle_probability,
            mix.universe_explore_lifecycle_probability,
        )
        self.assertEqual(
            mix.to_dict()["universe_feedback"]["routing_basis"],
            "lifecycle",
        )
        self.assertEqual(mix.current_timeframe_probability, 0.60)

    def test_discovery_source_mix_adapts_from_source_level_success(self) -> None:
        rows = []
        for run_id in range(1, 11):
            for index in range(3):
                for status in ("rejected", "accepted" if index < 2 else "rejected"):
                    rows.append(
                        {
                            "run_id": run_id,
                            "generation": 1,
                            "seed_path": f"live_{run_id}_{index}.set",
                            "exploitable": True,
                            "status": status,
                            "robust_status": "accepted" if status == "accepted" else "",
                            "final_tick_status": "accepted" if status == "accepted" else "",
                            "final_tick_6m_status": "accepted" if status == "accepted" else "",
                        }
                    )
                rows.append(
                    {
                        "run_id": run_id,
                        "generation": 1,
                        "seed_path": f"cross_{run_id}_{index}.set",
                        "exploitable": False,
                        "status": "accepted" if index == 0 else "rejected",
                        "robust_status": "accepted" if index == 0 else "",
                        "final_tick_status": "accepted" if index == 0 else "",
                        "final_tick_6m_status": "accepted" if index == 0 else "",
                    }
                )

        mix = estimate_discovery_source_mix(rows)

        self.assertTrue(mix.adaptive)
        self.assertEqual(mix.exploitable_trials, 30)
        self.assertEqual(mix.exploitable_successes, 20)
        self.assertEqual(mix.cross_asset_trials, 30)
        self.assertEqual(mix.cross_asset_successes, 10)
        self.assertGreater(mix.exploitable_ratio, 0.60)
        self.assertLessEqual(mix.exploitable_ratio, 0.85)

    def test_discovery_source_mix_does_not_count_base_accept_without_6m_accept(self) -> None:
        rows = []
        for run_id in range(1, 11):
            for index in range(3):
                rows.append(
                    {
                        "run_id": run_id,
                        "generation": 1,
                        "seed_path": f"live_{run_id}_{index}.set",
                        "exploitable": True,
                        "status": "accepted",
                        "robust_status": "accepted",
                        "final_tick_status": "accepted",
                        "final_tick_6m_status": "rejected",
                    }
                )
                rows.append(
                    {
                        "run_id": run_id,
                        "generation": 1,
                        "seed_path": f"cross_{run_id}_{index}.set",
                        "exploitable": False,
                        "status": "accepted",
                        "robust_status": "accepted",
                        "final_tick_status": "accepted",
                        "final_tick_6m_status": "accepted" if index == 0 else "rejected",
                    }
                )

        mix = estimate_discovery_source_mix(rows)

        self.assertEqual(mix.exploitable_successes, 0)
        self.assertEqual(mix.cross_asset_successes, 10)
        self.assertEqual(mix.exploitable_ratio, 0.60)

    def test_discovery_source_mix_keeps_floor_without_enough_evidence(self) -> None:
        mix = estimate_discovery_source_mix(
            [
                {
                    "run_id": 1,
                    "generation": 1,
                    "seed_path": "live.set",
                    "exploitable": True,
                    "status": "accepted",
                }
            ]
        )

        self.assertFalse(mix.adaptive)
        self.assertEqual(mix.reason, "insufficient_evidence")
        self.assertEqual(mix.exploitable_ratio, 0.60)

    def test_discovery_source_mix_excludes_technical_outcomes(self) -> None:
        mix = estimate_discovery_source_mix(
            [
                {
                    "run_id": 1,
                    "generation": 1,
                    "seed_path": f"technical_{index}.set",
                    "exploitable": False,
                    "status": "report_mismatch",
                }
                for index in range(30)
            ],
            minimum_trials=0,
        )

        self.assertEqual(mix.cross_asset_trials, 0)
        self.assertEqual(mix.cross_asset_successes, 0)

    def test_selection_feature_rows_batch_latest_candidates_then_seed_scores(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            create table candidates (
                id integer primary key,
                set_path text,
                score real,
                metrics_json text,
                period text
            );
            create table seed_scores (
                seed_path text primary key,
                active integer,
                score real,
                metrics_json text,
                period text
            );
            """
        )
        connection.executemany(
            "insert into candidates values (?, ?, ?, ?, ?)",
            [
                (1, "candidate.set", 10.0, metrics(), "H1"),
                (2, "candidate.set", 20.0, metrics(), "H4"),
                (3, "invalid_latest.set", 30.0, metrics(), "M30"),
                (4, "invalid_latest.set", None, metrics(), "D1"),
            ],
        )
        connection.executemany(
            "insert into seed_scores values (?, ?, ?, ?, ?)",
            [
                ("seed.set", 1, 40.0, metrics(), "D1"),
                ("inactive.set", 0, 50.0, metrics(), "M15"),
            ],
        )
        memory = AgentMemory.__new__(AgentMemory)
        memory.conn = connection

        rows = memory._selection_feature_rows(
            ["candidate.set", "invalid_latest.set", "seed.set", "inactive.set", "missing.set"]
        )

        self.assertEqual(rows["candidate.set"]["score"], 20.0)
        self.assertEqual(rows["candidate.set"]["period"], "H4")
        self.assertEqual(rows["invalid_latest.set"]["score"], 30.0)
        self.assertEqual(rows["seed.set"]["score"], 40.0)
        self.assertNotIn("inactive.set", rows)
        self.assertNotIn("missing.set", rows)
        connection.close()

    def test_sparse_batch_gradient_matches_standardized_reference(self) -> None:
        samples = [
            ((1.0, 3.0, 0.0, 1.0, 2.0, 0.0, 4.0, 1.0, 0.0, 1.0), 1),
            ((2.0, 1.0, 1.0, 0.0, 3.0, 2.0, 0.0, 2.0, 1.0, 0.0), 0),
            ((4.0, 2.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 0.0, 0.0), 1),
        ]
        feature_count = len(samples[0][0])
        means = [
            sum(features[index] for features, _label in samples) / len(samples)
            for index in range(feature_count)
        ]
        scales = [
            max(
                math.sqrt(
                    sum((features[index] - means[index]) ** 2 for features, _label in samples)
                    / len(samples)
                ),
                1e-6,
            )
            for index in range(feature_count)
        ]
        coefficients = [-0.4, 0.2, -0.1, 0.3, -0.25, 0.15, -0.05, 0.4, -0.2, 0.1, -0.3]
        sparse = [
            tuple((index, features[index]) for index in range(8, feature_count) if features[index])
            for features, _label in samples
        ]

        optimized = _batch_logistic_gradients(samples, sparse, means, scales, coefficients)

        reference = [0.0] * len(coefficients)
        for features, label in samples:
            standardized = [
                (features[index] - means[index]) / scales[index]
                for index in range(len(features))
            ]
            prediction = _sigmoid(
                coefficients[0]
                + sum(value * coefficient for value, coefficient in zip(standardized, coefficients[1:]))
            )
            error = label - prediction
            reference[0] += error
            for index, value in enumerate(standardized, start=1):
                reference[index] += error * value

        for actual, expected in zip(optimized, reference):
            self.assertAlmostEqual(actual, expected, places=10)

    def test_fitness_model_uses_only_runs_strictly_before_excluded_run(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = []
        memory = AgentMemory.__new__(AgentMemory)
        memory.conn = connection
        memory._selection_fitness_models = {}

        model = memory.selection_fitness_model(exclude_run_id=7)

        self.assertIsNone(model)
        query, params = connection.execute.call_args.args
        self.assertIn("and c.run_id < ?", query)
        self.assertNotIn("and c.run_id != ?", query)
        self.assertEqual(params, (7,))

    def test_final_label_accepts_probe_pending_operations_when_six_month_passes(self) -> None:
        row = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "pending_ohlc_trades",
            "final_tick_6m_status": "accepted",
        }
        self.assertEqual(finalized_six_month_label(row), 1)

    def test_final_label_excludes_unresolved_technical_rows(self) -> None:
        row = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "parse_error",
            "final_tick_6m_status": "",
        }
        self.assertIsNone(finalized_six_month_label(row))

    def test_robustness_label_uses_only_statistical_terminal_rows(self) -> None:
        accepted = {"status": "accepted", "robust_status": "accepted"}
        rejected = {"status": "accepted", "robust_status": "rejected"}
        technical = {"status": "accepted", "robust_status": "report_mismatch"}

        self.assertEqual(finalized_robustness_label(accepted), 1)
        self.assertEqual(finalized_robustness_label(rejected), 0)
        self.assertIsNone(finalized_robustness_label(technical))

    def test_robustness_model_trains_without_waiting_for_six_month_positives(self) -> None:
        rows = []
        for index in range(80):
            rows.append(
                {
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "",
                    "final_tick_6m_status": "",
                    "score": 150.0 + index % 10,
                    "metrics_json": metrics(recovery=12.0),
                    "period": "H4",
                }
            )
        for index in range(240):
            rows.append(
                {
                    "status": "accepted",
                    "robust_status": "rejected",
                    "final_tick_status": "",
                    "final_tick_6m_status": "",
                    "score": 90.0 + index % 10,
                    "metrics_json": metrics(recovery=3.0),
                    "period": "H4",
                }
            )

        model = SelectionFitnessModel.train(rows, target="robustness")

        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.target, "robustness")
        self.assertEqual(model.training_rows, 320)
        self.assertEqual(model.positive_rows, 80)
        self.assertGreater(
            model.predict(155.0, metrics(recovery=12.0), "H4").probability,
            model.predict(95.0, metrics(recovery=3.0), "H4").probability,
        )

    def test_model_learns_final_fitness_separately_from_raw_score(self) -> None:
        rows = []
        for index in range(100):
            rows.append(
                {
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "accepted",
                    "final_tick_6m_status": "accepted",
                    "score": 80.0 + index % 10,
                    "metrics_json": metrics(),
                    "period": "H4",
                }
            )
        for index in range(300):
            rows.append(
                {
                    "status": "accepted",
                    "robust_status": "rejected",
                    "final_tick_status": "",
                    "final_tick_6m_status": "",
                    "score": 190.0 + index % 10,
                    "metrics_json": metrics(profit_factor=3.5, recovery=15.0, drawdown=1.0),
                    "period": "H4",
                }
            )

        model = SelectionFitnessModel.train(rows)

        self.assertIsNotNone(model)
        assert model is not None
        compatible = model.predict(85.0, metrics(), "H4")
        extreme = model.predict(195.0, metrics(profit_factor=3.5, recovery=15.0, drawdown=1.0), "H4")
        self.assertGreater(compatible.probability, extreme.probability)
        self.assertGreater(compatible.weight, extreme.weight)


if __name__ == "__main__":
    unittest.main()
