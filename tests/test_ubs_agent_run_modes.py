import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ubs_agent_run_modes as run_modes


MODE_FLAGS = (
    "probe_universe_history",
    "evaluate_seeds",
    "evaluate_robustness",
    "evaluate_final_tick",
    "evaluate_regression",
    "rescore_seeds_only",
    "rescore_candidates_only",
    "rescore_robustness_only",
    "rescore_final_tick_only",
    "rescore_regression_only",
    "continue_last_run",
    "retry_candidate_id",
    "retry_seed_path",
    "retry_full_run",
    "retry_mismatch_run",
    "retry_mismatch_generation",
)


def mode_args(**overrides):
    values = {name: False for name in MODE_FLAGS}
    values.update(overrides)
    return SimpleNamespace(**values)


class StandaloneModeTests(unittest.TestCase):
    def test_selected_mode_runs_and_always_closes_memory(self):
        args = mode_args(evaluate_seeds=True, retry_candidate_id=42)
        memory = Mock()
        score_config = object()
        with patch.object(run_modes, "evaluate_seed_scores", return_value=7) as action:
            self.assertEqual(run_modes.run_standalone_mode(args, memory, score_config), 7)
        action.assert_called_once_with(args, memory, score_config)
        memory.close.assert_called_once_with()

        memory.reset_mock()
        with patch.object(run_modes, "evaluate_seed_scores", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                run_modes.run_standalone_mode(args, memory, score_config)
        memory.close.assert_called_once_with()

    def test_regression_configuration_is_built_before_action_and_closed(self):
        args = mode_args(evaluate_regression=True)
        memory = Mock()
        score_config = object()
        regression_config = object()
        runtime = object()
        with (
            patch.object(run_modes, "regression_score_config", return_value=regression_config),
            patch.object(run_modes, "regression_runtime", return_value=runtime),
            patch.object(run_modes, "evaluate_candidate_regression", return_value=3) as action,
        ):
            self.assertEqual(run_modes.run_standalone_mode(args, memory, score_config), 3)
        action.assert_called_once_with(args, memory, regression_config, runtime)
        memory.close.assert_called_once_with()

    def test_no_selected_mode_leaves_memory_open(self):
        memory = Mock()
        self.assertIsNone(run_modes.run_standalone_mode(mode_args(), memory, object()))
        memory.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
