"""La cola del arreglo de pendientes del auditor: a que alcanza y como avanza.

Dos cosas distintas que antes vivian con el resto de la pantalla: a cuantas
etapas llega el boton segun lo que haya marcado, y como la cola recorre las
corridas que sale de ahi -una por proceso, saltando la que su runner no
reconoce y parandose entera si una termina mal-.

Lo demas de la pantalla esta en `test_ubs_auditor_ui.py`, y lo que pinta en
`test_ubs_auditor_rows.py`.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.ubs.ui.auditor_fixtures import AuditorHarness
import ui.ubs_auditor_logic
from ubs.stage_rules_audit import RunGap


class _QueueHarness(AuditorHarness):
    """Auditor cuya etapa apunta los runs que le mandan en vez de correrlos.

    `runs_that_do_nothing` son los que el runner deja pasar -el .set ya no
    esta, el estado no es de los suyos-: no dejan proceso, asi que de ellos no
    cuelga el siguiente de la cola.
    """

    def __init__(self, *, runs_that_do_nothing=()) -> None:
        super().__init__()
        self.started: list[int] = []
        self.stages: list[str] = []
        self._skip = set(runs_that_do_nothing)

    def _run_ubs_auditor_pending(self, stage_key: str, run_id: int) -> bool:
        self.started.append(run_id)
        self.stages.append(stage_key)
        return run_id not in self._skip


class PendingQueueTests(unittest.TestCase):
    """La cola de runs del arreglo de pendientes."""

    @staticmethod
    def _harness(queue, *, stage: str = "final_tick_6m", **kwargs) -> _QueueHarness:
        harness = _QueueHarness(**kwargs)
        harness._ubs_auditor_pending_stage = stage
        harness._ubs_auditor_pending_queue = [
            item if isinstance(item, tuple) else (stage, item, 1, 0) for item in queue
        ]
        return harness

    def test_the_queue_advances_one_run_per_process(self) -> None:
        harness = self._harness([7, 8, 9])

        self.assertTrue(harness._start_next_ubs_auditor_pending())

        self.assertEqual(harness.started, [7])
        self.assertEqual([item[1] for item in harness._ubs_auditor_pending_queue], [8, 9])

        harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 0)

        self.assertEqual(harness.started, [7, 8])
        self.assertEqual([item[1] for item in harness._ubs_auditor_pending_queue], [9])

    def test_each_run_carries_its_own_stage(self) -> None:
        """Sin marca la cola lleva varias etapas, y cada una es la suya.

        De `_ubs_auditor_pending_stage` cuelga el permiso para encadenar la
        siguiente, asi que dejarlo en la etapa con la que empezo la cola
        contaria el run de Robustez como si fuera de Resultados."""
        harness = self._harness(
            [("candidates", 7, 1, 0), ("robustness", 7, 2, 0)], stage="candidates"
        )

        harness._start_next_ubs_auditor_pending()
        self.assertEqual(harness._ubs_auditor_pending_stage, "candidates")

        harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 0)
        self.assertEqual(harness._ubs_auditor_pending_stage, "robustness")
        self.assertEqual(harness.stages, ["candidates", "robustness"])

    def test_a_run_the_runner_skips_does_not_stall_the_queue(self) -> None:
        """Sin proceso no hay final al que colgar el siguiente."""
        harness = self._harness([7, 8, 9], runs_that_do_nothing=(7, 8))

        self.assertTrue(harness._start_next_ubs_auditor_pending())

        self.assertEqual(harness.started, [7, 8, 9])
        self.assertEqual(harness._ubs_auditor_pending_queue, [])

    def test_a_queue_nobody_runs_ends_saying_so(self) -> None:
        harness = self._harness([7, 8], runs_that_do_nothing=(7, 8))

        self.assertFalse(harness._start_next_ubs_auditor_pending())

        self.assertIn("2 corrida(s) sin nada", harness.ubs_auditor_status.get())

    def test_a_bad_exit_cancels_what_was_left(self) -> None:
        harness = self._harness([7, 8])

        started = harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 2)

        self.assertFalse(started)
        self.assertEqual(harness.started, [])
        self.assertEqual(harness._ubs_auditor_pending_queue, [])
        self.assertTrue(any("cancelada" in line for line in harness.console))

    def test_an_empty_queue_lets_the_other_chains_answer(self) -> None:
        harness = self._harness([])

        self.assertFalse(harness._maybe_auto_run_ubs_auditor_pending("ubs_agent.py", [], 0))


class _ClickHarness(AuditorHarness):
    """Auditor que apunta la cola que se le arma en vez de abrir MT5."""

    def __init__(self, *, marked: str = "", gaps=None) -> None:
        super().__init__()
        self.marked = marked
        self.gaps = gaps or {}
        self.confirmed: list = []
        self.queue: list = []

    def _selected_ubs_auditor_stage(self) -> str:
        return self.marked

    def _ubs_auditor_account_is_active(self) -> bool:
        return True

    def _ubs_auditor_selected_run_id(self) -> int:
        return 0

    def _ubs_auditor_open_memory(self):
        return SimpleNamespace(close=lambda: None)

    def _ubs_auditor_pending_runs(self, stage_key: str, *, quiet: bool = False):
        return self.gaps.get(stage_key)

    def _confirm_ubs_auditor_pending(self, queue) -> bool:
        self.confirmed = list(queue)
        return True

    def _start_next_ubs_auditor_pending(self) -> bool:
        self.queue = list(self._ubs_auditor_pending_queue)
        return True


def _summary(stage: str, pending: int = 0, missing: int = 0):
    return SimpleNamespace(stage=stage, pending=pending, missing=missing)


class FixPendingScopeTests(unittest.TestCase):
    """A que alcanza Arreglar pendientes segun lo que haya marcado."""

    def test_without_a_mark_it_fixes_every_stage(self) -> None:
        """La marca es un filtro, no un requisito: sin ella van todas.

        La tabla ya cuenta la memoria entera mientras no se marque nada, asi
        que pedir una fila para arreglar lo que se acaba de contar era un paso
        de mas."""
        harness = _ClickHarness(
            gaps={
                "candidates": (RunGap(7, 2, 0), RunGap(8, 1, 0)),
                "robustness": (RunGap(7, 3, 0),),
                "seeds": (),
            }
        )
        summaries = [
            _summary("seeds", pending=4),
            _summary("seed_final_tick"),
            _summary("candidates", pending=3),
            _summary("robustness", pending=3),
            _summary("regression"),
        ]

        with patch.object(ui.ubs_auditor_logic, "audit_pending_states", return_value=summaries):
            harness._fix_ubs_pending_states_clicked()

        # Solo las etapas con algo, y en el orden en que llegan: el del pipeline.
        self.assertEqual(
            [(item[0], item[1]) for item in harness.queue],
            [("seeds", 0), ("candidates", 7), ("candidates", 8), ("robustness", 7)],
        )
        # La de semilla no cuelga de ningun run y lleva la cuenta del resumen.
        self.assertEqual(harness.queue[0][2], 4)

    def test_a_marked_stage_is_the_only_one_fixed(self) -> None:
        harness = _ClickHarness(
            marked="robustness", gaps={"robustness": (RunGap(7, 3, 0), RunGap(9, 1, 2))}
        )

        with patch.object(ui.ubs_auditor_logic, "audit_pending_states") as swept:
            harness._fix_ubs_pending_states_clicked()

        swept.assert_not_called()
        self.assertEqual(
            [(item[0], item[1]) for item in harness.queue],
            [("robustness", 7), ("robustness", 9)],
        )

    def test_nothing_pending_anywhere_asks_for_nothing(self) -> None:
        harness = _ClickHarness()
        summaries = [_summary("candidates"), _summary("robustness")]

        with patch.object(ui.ubs_auditor_logic, "audit_pending_states", return_value=summaries), \
             patch.object(ui.ubs_auditor_logic.messagebox, "showinfo") as told:
            harness._fix_ubs_pending_states_clicked()

        self.assertEqual(harness.queue, [])
        self.assertEqual(harness.confirmed, [])
        self.assertTrue(told.called)

    def test_the_wrong_account_stops_before_counting_anything(self) -> None:
        """Arreglar abre MT5, asi que la cuenta auditada tiene que ser la activa."""
        harness = _ClickHarness()
        harness._ubs_auditor_account_is_active = lambda: False

        with patch.object(ui.ubs_auditor_logic, "audit_pending_states") as swept, \
             patch.object(ui.ubs_auditor_logic.messagebox, "showinfo"):
            harness._fix_ubs_pending_states_clicked()

        swept.assert_not_called()
        self.assertEqual(harness.queue, [])


if __name__ == "__main__":
    unittest.main()
