"""Modos sueltos del agente: evaluar, repuntuar, reintentar o reanudar."""
from __future__ import annotations


from ubs.regression import evaluate_candidate_regression, rescore_regression_only
from ubs_agent_final_tick_entry import evaluate_candidate_final_tick
from ubs_agent_final_tick_rescore import (
    rescore_final_tick_only,
)
from ubs_agent_history import (
    probe_universe_history,
)
from ubs_agent_rescore import (
    rescore_candidate_scores_only,
    rescore_robustness_only,
)
from ubs_agent_resume import (
    resume_last_run,
)
from ubs_agent_retry import (
    retry_candidate,
    retry_full_run,
    retry_generation_mismatches,
    retry_run_mismatches,
    retry_seed,
)
from ubs_agent_robustness import (
    evaluate_candidate_robustness,
)
from ubs_agent_run_config import regression_runtime
from ubs_agent_seed_scores import (
    evaluate_seed_scores,
    rescore_seed_scores_only,
)
from ubs_agent_universe import regression_score_config


def _run_and_close(action, args, memory, score_config) -> int:
    try:
        return action(args, memory, score_config)
    finally:
        memory.close()


def _run_regression_and_close(action, args, memory) -> int:
    try:
        return action(
            args,
            memory,
            regression_score_config(args),
            regression_runtime(args),
        )
    finally:
        memory.close()


def run_standalone_mode(args, memory, score_config) -> int | None:
    """Devuelve el codigo de salida del modo pedido, o None si no hay ninguno."""
    if args.probe_universe_history:
        return _run_and_close(probe_universe_history, args, memory, score_config)
    if args.evaluate_seeds:
        return _run_and_close(evaluate_seed_scores, args, memory, score_config)
    if args.evaluate_robustness:
        return _run_and_close(evaluate_candidate_robustness, args, memory, score_config)
    if args.evaluate_final_tick:
        return _run_and_close(evaluate_candidate_final_tick, args, memory, score_config)
    if args.evaluate_regression:
        return _run_regression_and_close(evaluate_candidate_regression, args, memory)
    if args.rescore_seeds_only:
        return _run_and_close(rescore_seed_scores_only, args, memory, score_config)
    if args.rescore_candidates_only:
        return _run_and_close(rescore_candidate_scores_only, args, memory, score_config)
    if args.rescore_robustness_only:
        return _run_and_close(rescore_robustness_only, args, memory, score_config)
    if args.rescore_final_tick_only:
        return _run_and_close(rescore_final_tick_only, args, memory, score_config)
    if args.rescore_regression_only:
        return _run_regression_and_close(rescore_regression_only, args, memory)
    if args.continue_last_run:
        return _run_and_close(resume_last_run, args, memory, score_config)
    if args.retry_candidate_id:
        return _run_and_close(retry_candidate, args, memory, score_config)
    if args.retry_seed_path:
        return _run_and_close(retry_seed, args, memory, score_config)
    if args.retry_full_run:
        return _run_and_close(retry_full_run, args, memory, score_config)
    if args.retry_mismatch_run:
        return _run_and_close(retry_run_mismatches, args, memory, score_config)
    if args.retry_mismatch_generation:
        return _run_and_close(retry_generation_mismatches, args, memory, score_config)
    return None
