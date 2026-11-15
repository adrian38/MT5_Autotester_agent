from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable


def runner_failure_summary(output: str, max_lines: int = 20) -> str:
    lines = output.splitlines()
    relevant = []
    for line in lines:
        failed_job = "DIAG WORKER_JOB_DONE" in line and not any(
            f"exit_code={code}" in line for code in (0, 5)
        )
        if "ERROR:" in line or "WORKER_JOB_FAILOVER" in line or failed_job:
            relevant.append(line)
    return "\n".join((relevant or lines)[-max_lines:])


def _run_worker(
    job_queue: queue.Queue[Any],
    retry_queue: queue.Queue[tuple[Any, Any]],
    profile: Any,
    prepare_profile: Callable[[Any], Any],
    run_job: Callable[[Any, Any, Any], int],
    logger: Any,
    success_exit_codes: tuple[int, ...],
    fatal_exception: type[BaseException],
    retry_exit_code: int | None,
) -> int:
    failures = 0
    logger.write(
        f"DIAG WORKER_START profile={profile.name} thread={threading.current_thread().name} "
        f"mt5={profile.mt5_path}"
    )
    profile_context = prepare_profile(profile)
    while True:
        try:
            job = job_queue.get_nowait()
        except queue.Empty:
            break
        logger.write(
            f"DIAG WORKER_JOB_START profile={profile.name} thread={threading.current_thread().name} "
            f"job={job.index} remaining_queue={job_queue.qsize()}"
        )
        try:
            exit_code = run_job(job, profile, profile_context)
        except fatal_exception as exc:
            logger.write(f"[{profile.name}] ERROR: {exc}")
            job_queue.task_done()
            return failures + 1
        except Exception as exc:
            logger.write(f"[{profile.name}] ERROR inesperado: {exc}")
            exit_code = 1
        if exit_code not in success_exit_codes:
            failures += 1
        if retry_exit_code is not None and exit_code == retry_exit_code:
            retry_queue.put((job, profile))
        logger.write(
            f"DIAG WORKER_JOB_DONE profile={profile.name} thread={threading.current_thread().name} "
            f"job={job.index} exit_code={exit_code} failures={failures}"
        )
        job_queue.task_done()
    logger.write(
        f"DIAG WORKER_DONE profile={profile.name} thread={threading.current_thread().name} "
        f"failures={failures}"
    )
    return failures


def _run_failovers(
    retry_queue: queue.Queue[tuple[Any, Any]],
    profiles: list[Any],
    prepare_profile: Callable[[Any], Any],
    run_job: Callable[[Any, Any, Any], int],
    logger: Any,
    success_exit_codes: tuple[int, ...],
    fatal_exception: type[BaseException],
) -> int:
    recovered = 0
    while not retry_queue.empty():
        job, failed_profile = retry_queue.get_nowait()
        failed_index = profiles.index(failed_profile)
        retry_profile = profiles[(failed_index + 1) % len(profiles)]
        logger.write(
            f"DIAG WORKER_JOB_FAILOVER_START failed_profile={failed_profile.name} "
            f"retry_profile={retry_profile.name} job={job.index}"
        )
        try:
            context = prepare_profile(retry_profile)
            exit_code = run_job(job, retry_profile, context)
        except fatal_exception as exc:
            logger.write(f"[{retry_profile.name}] ERROR: {exc}")
            exit_code = 1
        except Exception as exc:
            logger.write(f"[{retry_profile.name}] ERROR inesperado: {exc}")
            exit_code = 1
        if exit_code in success_exit_codes:
            recovered += 1
        logger.write(
            f"DIAG WORKER_JOB_FAILOVER_DONE failed_profile={failed_profile.name} "
            f"retry_profile={retry_profile.name} job={job.index} exit_code={exit_code} "
            f"recovered={exit_code in success_exit_codes}"
        )
        retry_queue.task_done()
    return recovered


def run_parallel_jobs(
    jobs: Iterable[Any],
    profiles: list[Any],
    prepare_profile: Callable[[Any], Any],
    run_job: Callable[[Any, Any, Any], int],
    logger: Any,
    *,
    success_exit_codes: tuple[int, ...],
    fatal_exception: type[BaseException],
    retry_exit_code: int | None = None,
) -> int:
    job_queue: queue.Queue[Any] = queue.Queue()
    retry_queue: queue.Queue[tuple[Any, Any]] = queue.Queue()
    for job in jobs:
        job_queue.put(job)
    with ThreadPoolExecutor(max_workers=len(profiles)) as executor:
        futures = [
            executor.submit(
                _run_worker,
                job_queue,
                retry_queue,
                profile,
                prepare_profile,
                run_job,
                logger,
                success_exit_codes,
                fatal_exception,
                retry_exit_code,
            )
            for profile in profiles
        ]
        failures = sum(future.result() for future in futures)
    recovered = _run_failovers(
        retry_queue,
        profiles,
        prepare_profile,
        run_job,
        logger,
        success_exit_codes,
        fatal_exception,
    )
    return failures - recovered
