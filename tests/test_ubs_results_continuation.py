import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ui.ubs_results_logic import UBSResultsLogicMixin, ubs_run_base_dates


class _ContinuationHarness(UBSResultsLogicMixin):
    def __init__(self, memory_path: Path) -> None:
        self._memory = memory_path

    def _ubs_memory_path(self) -> Path:
        return self._memory

    def _ensure_ubs_memory_schema(self, _conn: sqlite3.Connection) -> None:
        return None


class UBSRunBaseDatesTests(unittest.TestCase):
    def test_prefers_execution_dates(self) -> None:
        raw = json.dumps(
            {
                "execution": {"from_date": "2020.01.01", "to_date": "2024.12.31"},
                "args": {"from_date": "wrong", "to_date": "wrong"},
            }
        )

        self.assertEqual(ubs_run_base_dates(raw), ("2020.01.01", "2024.12.31"))

    def test_missing_or_invalid_config_is_not_replaced_by_template_dates(self) -> None:
        self.assertEqual(ubs_run_base_dates("not-json"), ("", ""))
        self.assertEqual(ubs_run_base_dates("{}"), ("", ""))


class UBSContinuationStateTests(unittest.TestCase):
    def _memory(self, directory: Path, statuses: list[str], *, generation: int = 5) -> tuple[Path, Path]:
        memory = directory / "memory.sqlite"
        set_path = directory / "candidate.set"
        set_path.write_text("Lots=0.01\n", encoding="utf-8")
        conn = sqlite3.connect(memory)
        try:
            conn.executescript(
                """
                create table runs (
                    id integer primary key,
                    generations integer not null,
                    variants_per_seed integer not null,
                    max_seeds integer not null,
                    execute_backtests integer not null
                );
                create table candidates (
                    id integer primary key,
                    run_id integer not null,
                    generation integer not null,
                    status text not null,
                    set_path text not null
                );
                """
            )
            conn.execute("insert into runs values (1, 5, 10, 30, 1)")
            for index, status in enumerate(statuses, start=1):
                conn.execute(
                    "insert into candidates values (?, 1, ?, ?, ?)",
                    (index, generation, status, str(set_path)),
                )
            conn.commit()
        finally:
            conn.close()
        return memory, set_path

    def test_complete_generation_keeps_retryable_count_separate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory, _set_path = self._memory(Path(raw), ["accepted", "report_mismatch", "no_report"])

            info = _ContinuationHarness(memory)._ubs_continuation_info()

        self.assertFalse(info["available"])
        self.assertEqual(info["retryable_count"], 2)
        self.assertEqual(info["remaining"], 0)

    def test_clean_complete_generation_is_not_continuable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory, _set_path = self._memory(Path(raw), ["accepted", "rejected", "no_trades"])

            info = _ContinuationHarness(memory)._ubs_continuation_info()

        self.assertFalse(info["available"])
        self.assertEqual(info["retryable_count"], 0)

    def test_retryable_rows_do_not_block_remaining_generations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory, _set_path = self._memory(
                Path(raw),
                ["accepted", "report_mismatch"],
                generation=4,
            )

            info = _ContinuationHarness(memory)._ubs_continuation_info()

        self.assertTrue(info["available"])
        self.assertEqual(info["remaining"], 1)
        self.assertEqual(info["retryable_count"], 1)


if __name__ == "__main__":
    unittest.main()
