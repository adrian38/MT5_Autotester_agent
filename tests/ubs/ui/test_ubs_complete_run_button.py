"""El boton «Completar run» mira el run del selector, no el ultimo de memoria.

Un `no_report` de un run viejo seguia siendo trabajo pendiente y el boton ni se
encendia: decidia sobre el ultimo run que hubiera en la memoria.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ui.ubs_results_logic import UBSResultsLogicMixin


class _Var:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value) -> None:
        self._value = value


class _CompleteRunHarness(UBSResultsLogicMixin):
    """La pantalla de Resultados reducida a lo que el boton necesita."""

    def __init__(self, memory_path: Path, selected_label: str) -> None:
        self._memory = memory_path
        self.ubs_results_run_id = _Var(selected_label)
        self.ubs_results_summary = _Var()
        self.ubs_results_status = _Var()
        self.launched: list[tuple[str, int]] = []
        self.enabled: dict[str, bool] = {}

    def _ubs_memory_path(self) -> Path:
        return self._memory

    def _ensure_ubs_memory_schema(self, _conn: sqlite3.Connection) -> None:
        return None

    def _run_ubs_continue(self) -> None:
        self.launched.append(("continue", 0))

    def _retry_visible_ubs_run_mismatches(self, *, run_id: int = 0, confirm: bool = True) -> bool:
        self.launched.append(("retry", int(run_id)))
        return True

    def _set_ubs_results_execute_backtests_enabled(self, enabled: bool) -> None:
        self.enabled["backtests"] = enabled

    def _set_ubs_results_complete_run_enabled(self, enabled: bool) -> None:
        self.enabled["complete"] = enabled

    def _set_ubs_results_continue_run_enabled(self, enabled: bool) -> None:
        self.enabled["continue"] = enabled


def _memory(directory: Path, runs: list[tuple[int, int]], candidates: list[tuple[int, int, str]]) -> Path:
    """Memoria minima: `runs` son (id, generaciones) y `candidates` (run, gen, estado)."""
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
        for run_id, generations in runs:
            conn.execute("insert into runs values (?, ?, 10, 30, 1)", (run_id, generations))
        for index, (run_id, generation, status) in enumerate(candidates, start=1):
            conn.execute(
                "insert into candidates values (?, ?, ?, ?, ?)",
                (index, run_id, generation, status, str(set_path)),
            )
        conn.commit()
    finally:
        conn.close()
    return memory


class CompleteVisibleRunTests(unittest.TestCase):
    def test_old_run_with_no_report_runs_its_pending(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(
                Path(raw),
                [(86, 2), (505, 1)],
                [(86, 2, "accepted"), (86, 2, "no_report"), (505, 1, "accepted")],
            )
            harness = _CompleteRunHarness(memory, "#86 | 2026-07-30 | 600 (52/452)")

            with mock.patch("ui.ubs_results_runs.messagebox") as box:
                harness._run_visible_ubs_complete_run()

        self.assertEqual(harness.launched, [("retry", 86)])
        box.showinfo.assert_not_called()

    def test_last_run_with_generations_left_keeps_chaining(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(Path(raw), [(505, 3)], [(505, 1, "accepted")])
            harness = _CompleteRunHarness(memory, "#505 | 2026-10-13 | 600 (52/452)")

            with mock.patch("ui.ubs_results_runs.messagebox"):
                harness._run_visible_ubs_complete_run()

        self.assertEqual(harness.launched, [("continue", 0)])

    def test_old_run_without_pending_says_so_and_launches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(
                Path(raw),
                [(86, 2), (505, 1)],
                [(86, 2, "accepted"), (86, 2, "no_trades"), (505, 1, "accepted")],
            )
            harness = _CompleteRunHarness(memory, "#86 | 2026-07-30 | 600 (52/452)")

            with mock.patch("ui.ubs_results_runs.messagebox") as box:
                harness._run_visible_ubs_complete_run()

        self.assertEqual(harness.launched, [])
        self.assertIn("#86", box.showinfo.call_args[0][1])

    def test_generated_set_of_the_last_run_still_goes_through_continuar(self) -> None:
        """El .set que nunca corrio sigue entrando por el camino de siempre."""
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(Path(raw), [(505, 2)], [(505, 2, "accepted"), (505, 2, "generated")])
            harness = _CompleteRunHarness(memory, "#505 | 2026-10-13 | 600 (52/452)")

            with mock.patch("ui.ubs_results_runs.messagebox"):
                harness._run_visible_ubs_complete_run()

        self.assertEqual(harness.launched, [("continue", 0)])


class CompleteRunButtonStateTests(unittest.TestCase):
    def _summary(self, harness: _CompleteRunHarness, run_id: int, counts: dict) -> None:
        base = {
            "total": 600, "scored": 504, "accepted": 52, "rejected": 452,
            "generated": 0, "no_report": 0, "no_trades": 0, "no_history": 0,
            "trade_disabled": 0, "symbol_not_exist": 0, "report_mismatch": 0,
        }
        base.update(counts)
        run = {
            "id": run_id,
            "created_at": "2026-07-30T05:00:37",
            "execute_backtests": 1,
            "output_dir": "outputs/ubs_agent",
        }
        harness._update_ubs_results_summary(base, run, [])

    def test_no_report_of_an_old_run_enables_the_button(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(
                Path(raw),
                [(86, 2), (505, 1)],
                [(86, 2, "accepted"), (86, 2, "no_report"), (505, 1, "accepted")],
            )
            harness = _CompleteRunHarness(memory, "#86 | 2026-07-30 | 600 (52/452)")

            self._summary(harness, 86, {"no_report": 1})

        self.assertTrue(harness.enabled["complete"])
        self.assertTrue(harness.enabled["continue"])

    def test_generated_set_counts_as_pending_for_continuar_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(
                Path(raw),
                [(86, 2), (505, 1)],
                [(86, 2, "accepted"), (86, 2, "generated"), (505, 1, "accepted")],
            )
            harness = _CompleteRunHarness(memory, "#86 | 2026-07-30 | 600 (52/452)")

            self._summary(harness, 86, {"generated": 1})

        self.assertTrue(harness.enabled["complete"])
        self.assertTrue(harness.enabled["continue"])

    def test_clean_old_run_leaves_the_button_off(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            memory = _memory(
                Path(raw),
                [(86, 2), (505, 1)],
                [(86, 2, "accepted"), (86, 2, "no_trades"), (505, 1, "accepted")],
            )
            harness = _CompleteRunHarness(memory, "#86 | 2026-07-30 | 600 (52/452)")

            self._summary(harness, 86, {"no_trades": 1})

        self.assertFalse(harness.enabled["complete"])
        self.assertFalse(harness.enabled["continue"])


if __name__ == "__main__":
    unittest.main()
