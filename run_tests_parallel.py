from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable


def _run_worker(
    job_queue: queue.Queue[Any],
    profile: Any,
    prepare_profile: Callable[[Any], Any],
    run_job: Callable[[Any, Any, Any], int],
    logger: Any,
    success_exit_codes: tuple[int, ...],
    fatal_exception: type[BaseException],
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


def run_parallel_jobs(
    jobs: Iterable[Any],
    profiles: list[Any],
    prepare_profile: Callable[[Any], Any],
    run_job: Callable[[Any, Any, Any], int],
    logger: Any,
    *,
    success_exit_codes: tuple[int, ...],
    fatal_exception: type[BaseException],
) -> int:
    job_queue: queue.Queue[Any] = queue.Queue()
    for job in jobs:
        job_queue.put(job)
    with ThreadPoolExecutor(max_workers=len(profiles)) as executor:
        futures = [
            executor.submit(
                _run_worker,
                job_queue,
                profile,
                prepare_profile,
                run_job,
                logger,
                success_exit_codes,
                fatal_exception,
            )
            for profile in profiles
        ]
        return sum(future.result() for future in futures)
