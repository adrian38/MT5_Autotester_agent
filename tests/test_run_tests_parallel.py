import unittest
from dataclasses import dataclass

from run_tests_parallel import run_parallel_jobs, runner_failure_summary


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
    def test_failure_summary_omits_unrelated_terminal_inventory(self):
        output = "\n".join([
            "DIAG PARALLEL_AFTER: terminal pid=1 path=None cmd=None",
            "[MT5_7] ERROR: MT5 Model=4 genero 0 barras / 0 ticks",
            "DIAG WORKER_JOB_DONE profile=MT5_7 job=7 exit_code=4 failures=1",
            "DIAG PARALLEL_AFTER: terminal pid=2 path=None cmd=None",
        ])

        summary = runner_failure_summary(output)

        self.assertIn("ERROR: MT5 Model=4", summary)
        self.assertIn("job=7 exit_code=4", summary)
        self.assertNotIn("path=None", summary)

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

    def test_model4_empty_result_fails_over_to_next_profile(self):
        profiles = [
            FakeProfile("MT5_1", "terminal1.exe"),
            FakeProfile("MT5_2", "terminal2.exe"),
        ]
        calls = []

        def execute(job, profile, _context):
            calls.append((job.index, profile.name))
            return 4 if len(calls) == 1 else 0

        failures = run_parallel_jobs(
            [FakeJob(7)],
            profiles,
            lambda _profile: object(),
            execute,
            FakeLogger(),
            success_exit_codes=(0, 5),
            fatal_exception=FatalWorkerError,
            retry_exit_code=4,
        )

        self.assertEqual(failures, 0)
        self.assertEqual([call[0] for call in calls], [7, 7])
        self.assertNotEqual(calls[0][1], calls[1][1])

    def test_generic_failure_is_not_retried(self):
        profile = FakeProfile("MT5_1", "terminal.exe")
        calls = []

        def execute(job, selected_profile, _context):
            calls.append((job.index, selected_profile.name))
            return 1

        failures = run_parallel_jobs(
            [FakeJob(7)],
            [profile],
            lambda _profile: object(),
            execute,
            FakeLogger(),
            success_exit_codes=(0, 5),
            fatal_exception=FatalWorkerError,
            retry_exit_code=4,
        )

        self.assertEqual(failures, 1)
        self.assertEqual(calls, [(7, "MT5_1")])

    def test_single_profile_retry_is_bounded(self):
        profile = FakeProfile("MT5_1", "terminal.exe")
        calls = []

        def execute(job, selected_profile, _context):
            calls.append((job.index, selected_profile.name))
            return 4

        failures = run_parallel_jobs(
            [FakeJob(7)],
            [profile],
            lambda _profile: object(),
            execute,
            FakeLogger(),
            success_exit_codes=(0, 5),
            fatal_exception=FatalWorkerError,
            retry_exit_code=4,
        )

        self.assertEqual(failures, 1)
        self.assertEqual(calls, [(7, "MT5_1"), (7, "MT5_1")])


if __name__ == "__main__":
    unittest.main()
