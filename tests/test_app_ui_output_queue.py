import queue
import unittest
from unittest.mock import patch

import app_ui


class DrainHarness:
    def __init__(self, items: int) -> None:
        self.output_queue = queue.Queue()
        for index in range(items):
            self.output_queue.put(f"line {index}\n")
        self.batches: list[list[tuple[str, str | None]]] = []
        self.progress_lines: list[str] = []
        self.after_calls: list[tuple[int, object]] = []

    def _append_console_batch(self, entries: list[tuple[str, str | None]]) -> None:
        if entries:
            self.batches.append(list(entries))

    def _tag_for_line(self, _line: str) -> str:
        return "info"

    def _update_progress_from_line(self, line: str) -> None:
        self.progress_lines.append(line)

    def after(self, interval: int, callback: object) -> None:
        self.after_calls.append((interval, callback))

    def _drain_output_queue(self) -> None:
        pass


class OutputQueueDrainTests(unittest.TestCase):
    def test_drain_yields_after_configured_batch_limit(self) -> None:
        harness = DrainHarness(app_ui.OUTPUT_DRAIN_MAX_ITEMS + 50)

        with patch.object(app_ui.time, "perf_counter", return_value=0.0):
            app_ui.MT5AutotesterUI._drain_output_queue(harness)

        self.assertEqual(len(harness.progress_lines), app_ui.OUTPUT_DRAIN_MAX_ITEMS)
        self.assertEqual(len(harness.batches), 1)
        self.assertEqual(len(harness.batches[0]), app_ui.OUTPUT_DRAIN_MAX_ITEMS)
        self.assertEqual(harness.output_queue.qsize(), 50)
        self.assertEqual(harness.after_calls[0][0], app_ui.OUTPUT_DRAIN_BUSY_INTERVAL_MS)


if __name__ == "__main__":
    unittest.main()
