import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import run_tests


class RunLoggerTests(unittest.TestCase):
    def test_async_logger_flushes_both_files_in_order_on_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = run_tests.Path(temp_dir)
            log_path = root / "run.log"
            stdout = io.StringIO()

            with patch.object(run_tests, "LOG_DIR", root), redirect_stdout(stdout):
                logger = run_tests.RunLogger(log_path)
                logger.write("uno")
                logger.write_many(["dos", "tres"])
                logger.close()

            run_lines = log_path.read_text(encoding="utf-8").splitlines()
            last_lines = (root / "last_run.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(run_lines, last_lines)
            self.assertEqual([line.split("] ", 1)[1] for line in run_lines], ["uno", "dos", "tres"])
            self.assertEqual(stdout.getvalue().splitlines(), ["uno", "dos", "tres"])


class ActionRateLimiterTests(unittest.TestCase):
    def test_reserves_separate_slots_for_simultaneous_actions(self) -> None:
        limiter = run_tests.ActionRateLimiter(3.0)
        logger = Mock()

        with (
            patch.object(run_tests.time, "monotonic", return_value=100.0),
            patch.object(run_tests.time, "sleep") as sleep,
        ):
            first_delay = limiter.wait_for_turn(logger, "Reinicio watchdog")
            second_delay = limiter.wait_for_turn(logger, "Reinicio watchdog")

        self.assertEqual(first_delay, 0.0)
        self.assertEqual(second_delay, 3.0)
        sleep.assert_called_once_with(3.0)
        logger.write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
