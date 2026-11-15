"""Lo que una etapa declara parado tiene que estarlo de verdad.

`StageSpec.settled_statuses` saca filas de la cuenta de pendientes, asi que es
la clase de declaracion que envejece mal y en silencio: el dia que alguien
haga reintentable uno de esos estados, el auditor seguiria diciendo que no
espera nada. Aqui se contrasta cada declaracion contra quien manda de verdad
-el set de reintento de su runner-, no contra otra lista escrita a mano.
"""
from __future__ import annotations

import unittest

from ubs.agent.config import (
    FINAL_TICK_DATE_RETRYABLE_STATUSES,
    FINAL_TICK_RETRYABLE_STATUSES,
)
from ubs.agent.evaluation.robustness import ROBUST_RETRYABLE_STATUSES
from ubs.agent.final_tick.evaluate import final_tick_row_pending_for_dates
from ubs.memory import GENERATED_CANDIDATE_STATUS, RETRYABLE_CANDIDATE_STATUSES
from ubs.memory_scores import SEED_SETTLED_STATUSES
from ubs.regression_rules import REGRESSION_RETRYABLE_STATUSES
from ubs.stage_specs import STAGE_SPECS, stage_spec

# Que etapa corre con que set de reintento. Una etapa que falte aqui rompe la
# primera prueba: declarar un estado parado sin decir quien lo reintentaria es
# justo lo que esta hoja impide.
RETRYABLE_BY_STAGE = {
    "seeds": frozenset(),
    "seed_final_tick": FINAL_TICK_RETRYABLE_STATUSES | FINAL_TICK_DATE_RETRYABLE_STATUSES,
    "seed_final_tick_6m": FINAL_TICK_RETRYABLE_STATUSES | FINAL_TICK_DATE_RETRYABLE_STATUSES,
    "candidates": frozenset({*RETRYABLE_CANDIDATE_STATUSES, GENERATED_CANDIDATE_STATUS}),
    "robustness": frozenset(ROBUST_RETRYABLE_STATUSES),
    "final_tick": FINAL_TICK_RETRYABLE_STATUSES | FINAL_TICK_DATE_RETRYABLE_STATUSES,
    "final_tick_6m": FINAL_TICK_RETRYABLE_STATUSES | FINAL_TICK_DATE_RETRYABLE_STATUSES,
    "regression": frozenset(REGRESSION_RETRYABLE_STATUSES),
}

FINAL_TICK_STAGES = {
    "seed_final_tick": "probe",
    "final_tick": "probe",
    "seed_final_tick_6m": "six_month",
    "final_tick_6m": "six_month",
}


def _final_tick_row(status: str) -> dict:
    """Una fila de Final Tick con su ventana puesta, que es el caso dificil.

    Con las fechas de la corrida iguales a las pedidas, `pending_ohlc_trades`
    ya no se reencola ni en el 6M: si aun asi dijera que si, la declaracion
    estaria mal.
    """
    return {
        "final_tick_status": status,
        "final_tick_from_date": "2026.05.01",
        "final_tick_to_date": "2026.05.31",
        "ft_ohlc_metrics_json": '{"trades": 3}',
    }


class SettledStatusesAreReallySettledTests(unittest.TestCase):
    def test_every_stage_says_who_would_retry_it(self) -> None:
        self.assertEqual(
            {spec.key for spec in STAGE_SPECS}, set(RETRYABLE_BY_STAGE)
        )

    def test_no_declared_state_is_in_its_runners_retry_set(self) -> None:
        for spec in STAGE_SPECS:
            with self.subTest(stage=spec.key):
                self.assertEqual(
                    set(spec.settled_statuses) & RETRYABLE_BY_STAGE[spec.key], set()
                )

    def test_no_declared_state_is_a_verdict(self) -> None:
        """Parado quiere decir sin veredicto: accepted y rejected no caben."""
        for spec in STAGE_SPECS:
            with self.subTest(stage=spec.key):
                self.assertEqual(
                    set(spec.settled_statuses) & {"accepted", "rejected"}, set()
                )

    def test_final_tick_asks_its_own_runner(self) -> None:
        """La etapa de Final Tick se comprueba llamando a quien decide."""
        for stage_key, final_tick_stage in FINAL_TICK_STAGES.items():
            for status in stage_spec(stage_key).settled_statuses:
                with self.subTest(stage=stage_key, status=status):
                    self.assertFalse(
                        final_tick_row_pending_for_dates(
                            _final_tick_row(status),
                            "2026.05.01",
                            "2026.05.31",
                            final_tick_stage=final_tick_stage,
                        )
                    )

    def test_the_six_month_stage_does_not_park_a_short_probe(self) -> None:
        """En el 6M `pending_ohlc_trades` si se reencola al mover la ventana.

        Es el mismo estado que la sonda si declara, y por eso se dice aqui en
        voz alta: lo que esta parado en una etapa no lo esta en la siguiente.
        """
        for stage_key in ("seed_final_tick_6m", "final_tick_6m"):
            with self.subTest(stage=stage_key):
                self.assertNotIn(
                    "pending_ohlc_trades", stage_spec(stage_key).settled_statuses
                )
        self.assertTrue(
            final_tick_row_pending_for_dates(
                _final_tick_row("pending_ohlc_trades"),
                "2026.01.01",
                "2026.06.30",
                final_tick_stage="six_month",
            )
        )


if __name__ == "__main__":
    unittest.main()
