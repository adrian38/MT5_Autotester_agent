import tempfile
import unittest
from pathlib import Path

from tests.ubs_selection_fixtures import metrics
from ubs.memory import AgentMemory
from ubs.selection import (
    descendant_fitness_predictions,
    estimate_discovery_source_mix,
    estimate_discovery_target_policy_mix,
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

    def test_descendant_fitness_credits_multigeneration_success_to_root_seed(self) -> None:
        rows = []
        for run_id in range(1, 6):
            child = f"run_{run_id}_generation_1.set"
            rows.extend(
                [
                    {
                        "run_id": run_id,
                        "generation": 1,
                        "seed_path": "root_winner.set",
                        "set_path": child,
                        "status": "accepted",
                        "robust_status": "rejected",
                    },
                    {
                        "run_id": run_id,
                        "generation": 2,
                        "seed_path": child,
                        "set_path": f"run_{run_id}_generation_2.set",
                        "status": "accepted",
                        "robust_status": "accepted",
                        "final_tick_status": "accepted",
                        "final_tick_6m_status": "accepted",
                    },
                    {
                        "run_id": run_id,
                        "generation": 1,
                        "seed_path": "root_loser.set",
                        "set_path": f"run_{run_id}_loser.set",
                        "status": "accepted",
                        "robust_status": "accepted",
                        "final_tick_status": "accepted",
                        "final_tick_6m_status": "rejected",
                    },
                ]
            )

        predictions = descendant_fitness_predictions(
            rows,
            ["root_winner.set", "root_loser.set", "run_1_generation_1.set"],
        )

        self.assertGreater(predictions["root_winner.set"].weight, 0.0)
        self.assertLess(predictions["root_loser.set"].weight, 0.0)
        self.assertGreater(predictions["root_winner.set"].evidence, 0.0)
        self.assertEqual(predictions["run_1_generation_1.set"].evidence, 0.0)

    def test_source_feedback_propagates_6m_success_to_selected_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AgentMemory(Path(tmp) / "memory.sqlite")
            try:
                run_id = memory.create_run(
                    Path("seeds"),
                    Path("output"),
                    2,
                    1,
                    1,
                    False,
                    True,
                    config={"args": {"generation_mode": "discovery"}},
                )
                memory.conn.execute(
                    """
                    insert into generation_seed_selection (
                        run_id, generation, rank, seed_path, symbol, period,
                        family, run_strategy, selection_score, asset_weight,
                        timeframe_weight, diversity, created_at
                    ) values (?, 1, 1, 'root.set', 'XAUUSD', 'H1',
                              'test', '1', 1, 0, 0, 0, 'now')
                    """,
                    (run_id,),
                )

                def candidate(generation: int, seed_path: str, set_path: str) -> int:
                    cursor = memory.conn.execute(
                        """
                        insert into candidates (
                            run_id, generation, seed_path, set_path, symbol,
                            target_symbol, period, family, run_strategy,
                            mutated_keys, missing_lot_keys, policy, status, created_at
                        ) values (?, ?, ?, ?, 'XAUUSD', 'XAUUSD', 'H1',
                                  'test', '1', '', '', 'exploit', 'accepted', 'now')
                        """,
                        (run_id, generation, seed_path, set_path),
                    )
                    return int(cursor.lastrowid)

                parent_id = candidate(1, "root.set", "generation_1.set")
                child_id = candidate(2, "generation_1.set", "generation_2.set")
                memory.conn.execute(
                    "insert into candidate_robustness (candidate_id, run_id, status, evaluated_at) values (?, ?, 'rejected', 'now')",
                    (parent_id, run_id),
                )
                memory.conn.execute(
                    "insert into candidate_robustness (candidate_id, run_id, status, evaluated_at) values (?, ?, 'accepted', 'now')",
                    (child_id, run_id),
                )
                memory.conn.execute(
                    "insert into candidate_final_tick (candidate_id, run_id, status, evaluated_at) values (?, ?, 'accepted', 'now')",
                    (child_id, run_id),
                )
                memory.conn.execute(
                    "insert into candidate_final_tick_6m (candidate_id, run_id, status, evaluated_at) values (?, ?, 'accepted', 'now')",
                    (child_id, run_id),
                )
                memory.conn.commit()

                feedback = memory.candidate_source_feedback_rows()

                self.assertEqual(len(feedback), 2)
                self.assertTrue(all(row["seed_path"] == "root.set" for row in feedback))
                self.assertIn("accepted", {row["final_tick_6m_status"] for row in feedback})
            finally:
                memory.close()

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
