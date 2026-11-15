"""Lo que una etapa declara parado tiene que estarlo de verdad.

`StageSpec.settled_statuses` saca filas de la cuenta de pendientes, asi que es
la clase de declaracion que envejece mal y en silencio. Aqui cada una se
pregunta a quien manda -el runner de su etapa, llamando a su propia funcion
cuando la tiene- en vez de contrastarla contra otra lista escrita a mano.

Pendiente es aquello a lo que todavia se le puede hacer el reporte. Un rechazo
especial no lo es: el historico que no esta no aparece al segundo intento y una
corrida sin operaciones vuelve a no tenerlas. Algunos runners los reencolan de
todas formas, y eso no es un descuido de nadie sino dos criterios distintos
sobre la misma fila; `RETRIED_ANYWAY` los tiene contados uno a uno para que el
dia que aparezca otro se vea aqui y no en una corrida de MT5.
"""
from __future__ import annotations

import unittest

from ubs.agent.evaluation.robustness import robust_status_pending_for_retry
from ubs.agent.final_tick.evaluate import final_tick_row_pending_for_dates
from ubs.memory import RETRYABLE_CANDIDATE_STATUSES
from ubs.memory_scores import SEED_KEPT_STATUSES
from ubs.regression_rules import REGRESSION_RETRYABLE_STATUSES
from ubs.stage_specs import STAGE_SPECS, stage_spec

# Lo que la tabla `candidates` puede llegar a guardar, de todos los sitios que
# le escriben un estado. `history_ok` no esta porque es de las sondas de
# historico, que `row_filter` deja fuera de la etapa.
CANDIDATE_STATUSES = (
    "accepted",
    "rejected",
    "generated",
    "no_report",
    "parse_error",
    "report_mismatch",
    "pending_tester_context",
    "no_trades",
    "trade_disabled",
    "no_history",
    "symbol_not_exist",
)

FROM_DATE = "2026.05.01"
TO_DATE = "2026.05.31"

# Que etapa es un Final Tick y de cual de las dos: su runner decide con la
# misma funcion pero no con la misma respuesta.
FINAL_TICK_STAGES = {
    "seed_final_tick": "probe",
    "final_tick": "probe",
    "seed_final_tick_6m": "six_month",
    "final_tick_6m": "six_month",
}

# Estados declarados parados que su runner si volveria a encolar. No son un
# error: el auditor contesta a «que falta por correr» y el runner a «a que le
# doy otra pasada», y en estos tres no coinciden. Pulsar Arreglar pendientes
# los toca aunque la pantalla no los cuente.
RETRIED_ANYWAY = {
    "seeds": {"no_trades", "no_history", "symbol_not_exist"},
    "robustness": {"no_trades"},
    "regression": {"no_history"},
}


def _final_tick_row(status: str) -> dict:
    """Una fila de Final Tick con su ventana puesta, que es el caso dificil.

    Con las fechas de la corrida iguales a las pedidas, `pending_ohlc_trades`
    ya no se reencola ni en el 6M: si aun asi dijera que si, la declaracion
    estaria mal.
    """
    return {
        "final_tick_status": status,
        "final_tick_from_date": FROM_DATE,
        "final_tick_to_date": TO_DATE,
        "ft_ohlc_metrics_json": '{"trades": 3}',
    }


def _would_retry(stage_key: str, status: str) -> bool:
    """Si el runner de esa etapa volveria a correr una fila en ese estado."""
    if stage_key in FINAL_TICK_STAGES:
        return final_tick_row_pending_for_dates(
            _final_tick_row(status),
            FROM_DATE,
            TO_DATE,
            final_tick_stage=FINAL_TICK_STAGES[stage_key],
        )
    if stage_key == "seeds":
        return status not in SEED_KEPT_STATUSES
    if stage_key == "candidates":
        return status in set(RETRYABLE_CANDIDATE_STATUSES)
    if stage_key == "robustness":
        return robust_status_pending_for_retry(status)
    if stage_key == "regression":
        return status in REGRESSION_RETRYABLE_STATUSES
    raise AssertionError(f"Etapa sin runner declarado en esta prueba: {stage_key}")


class SettledStatusesAreReallySettledTests(unittest.TestCase):
    def test_every_stage_says_who_would_retry_it(self) -> None:
        """Declarar un estado parado sin decir quien lo reintentaria no vale."""
        for spec in STAGE_SPECS:
            with self.subTest(stage=spec.key):
                _would_retry(spec.key, "un_estado_cualquiera")

    def test_no_declared_state_is_a_verdict(self) -> None:
        """Parado quiere decir sin veredicto: accepted y rejected no caben."""
        for spec in STAGE_SPECS:
            with self.subTest(stage=spec.key):
                self.assertEqual(
                    set(spec.settled_statuses) & {"accepted", "rejected"}, set()
                )

    def test_the_runners_that_disagree_are_the_declared_ones(self) -> None:
        """El solape con los runners esta contado, no descubierto por ahi."""
        for spec in STAGE_SPECS:
            with self.subTest(stage=spec.key):
                self.assertEqual(
                    {
                        status
                        for status in spec.settled_statuses
                        if _would_retry(spec.key, status)
                    },
                    RETRIED_ANYWAY.get(spec.key, set()),
                )

    def test_every_stage_parks_the_special_rejections(self) -> None:
        """La familia es la misma en todo el embudo, etapa a etapa."""
        for spec in STAGE_SPECS:
            with self.subTest(stage=spec.key):
                self.assertEqual(
                    {"no_trades", "no_history", "trade_disabled", "symbol_not_exist"}
                    - set(spec.settled_statuses),
                    set(),
                )

    def test_every_candidate_status_lands_in_exactly_one_place(self) -> None:
        """Veredicto, callejon sin salida, o algo a lo que le falta el backtest.

        Esta es la prueba que faltaba. `generated` no estaba en ninguno de los
        tres: la lista de reintentables se leia como «problemas tecnicos» y un
        .set que nunca corrio no lo parecia, asi que 180 candidatos salian
        pendientes en el auditor y ningun boton los tocaba. Caer en dos sitios
        tambien es un error: querria decir que algo se cuenta y se arregla con
        dos criterios distintos.
        """
        spec = stage_spec("candidates")
        for status in CANDIDATE_STATUSES:
            with self.subTest(status=status):
                places = (
                    status in {"accepted", "rejected"},
                    status in spec.settled_statuses,
                    status in RETRYABLE_CANDIDATE_STATUSES,
                )
                self.assertEqual(sum(places), 1, f"{status}: {places}")

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
