import unittest
from dataclasses import dataclass

from run_tests_parallel import run_parallel_jobs


@dataclass(frozen=True)
class FakeJob:
    index: int


@dataclass(frozen=True)
class FakeProfile:
    name: str
    mt5_path: str


class FakeLogger:
    def __init__(self):
        self.lines = []

    def write(self, line):
        self.lines.append(line)


class FatalWorkerError(RuntimeError):
    pass


class ParallelRunnerTests(unittest.TestCase):
    def test_counts_failures_and_accepts_skipped_jobs(self):
        jobs = [FakeJob(1), FakeJob(2), FakeJob(3)]
        profile = FakeProfile("MT5_1", "terminal64.exe")
        exit_codes = iter([0, 5, 1])
        calls = []

        def execute(job, selected_profile, context):
            calls.append((job.index, selected_profile.name, context))
            return next(exit_codes)

        failures = run_parallel_jobs(
            jobs,
            [profile],
            lambda selected_profile: f"settings:{selected_profile.name}",
            execute,
            FakeLogger(),
            success_exit_codes=(0, 5),
            fatal_exception=FatalWorkerError,
        )

        self.assertEqual(failures, 1)
        self.assertEqual([call[0] for call in calls], [1, 2, 3])

    def test_fatal_error_stops_that_worker(self):
        jobs = [FakeJob(1), FakeJob(2)]
        profile = FakeProfile("MT5_1", "terminal64.exe")
        calls = []

        def execute(job, _profile, _context):
            calls.append(job.index)
            raise FatalWorkerError("busy")

        failures = run_parallel_jobs(
            jobs,
            [profile],
            lambda _profile: object(),
            execute,
            FakeLogger(),
            success_exit_codes=(0, 5),
            fatal_exception=FatalWorkerError,
        )

        self.assertEqual(failures, 1)
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
