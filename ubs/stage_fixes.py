"""Que boton pone al dia cada cosa que la auditoria encontro.

La auditoria dice lo que la memoria tiene; esto dice que hacer con ello, que
es otra pregunta y no toca SQLite para contestarla: le basta con el
`StageAudit` ya contado y con el `StageSpec` de la etapa.

Son dos listas y entre las dos tiene que salir toda etapa que la tabla pinte
en rojo. `stages_to_rescore()` son las que se vuelven a juzgar desde lo
guardado, sin abrir MT5. `stages_to_repeat()` son las que hay que volver a
correr, porque lo que les pasa no esta en como se juzgaron sino en con que se
produjeron.

`stage_rules_audit` reexporta las dos, que es por donde las pide la pantalla.
"""
from __future__ import annotations

from typing import Sequence

from ubs.stage_specs import StageAudit, stage_spec


def _bent_a_verdict(audit: StageAudit) -> bool:
    """Si la etapa juzgo algo con una regla que ya no es la de ahora."""
    return bool(audit.stale_verdicts) or any(
        finding.verdicts for finding in audit.findings
    )


def _rescore_would_change_it(audit: StageAudit) -> bool:
    """Si el repuntuado tiene algo que hacer en esa etapa.

    Dos cosas distintas acaban en el mismo boton. Un veredicto dado con una
    regla que ya no es la de ahora se vuelve a juzgar y puede cambiar. Y uno
    dado sobre una ventana que no existe no se puede juzgar de ninguna manera,
    asi que el repuntuado lo devuelve a pendiente (`_demote_if_windowless()`
    escribe `no_report`), que es lo honesto y lo saca del rojo.

    Lo que no entra es una ventana que simplemente es otra: ahi el veredicto
    esta bien dado y no hay nada que reescribir.
    """
    return _bent_a_verdict(audit) or bool(audit.undated_verdicts)


def stages_to_rescore(audits: Sequence[StageAudit]) -> tuple[str, ...]:
    """Etapas donde la regla de hoy no es la que juzgo, y se pueden repuntuar.

    Lo que no entra es una etapa donde no cambio ninguna regla: repuntuarla
    seria recorrer la memoria entera para reescribir cada veredicto con el
    valor que ya tenia. Es lo que pasaba con una etapa cuya unica pega era la
    ventana, que ningun repuntuado mueve: 3450 filas reescritas con lo mismo y
    el rojo intacto al terminar.

    Final Tick corto y 6M comparten el flag `--rescore-final-tick-only` pero se
    encolan por separado, cada uno con su `--final-tick-stage` y sus fechas,
    porque cada uno repuntua su propia tabla.
    """
    return tuple(
        audit.stage
        for audit in audits
        if stage_spec(audit.stage).rescore_flag and _rescore_would_change_it(audit)
    )


def stages_to_repeat(audits: Sequence[StageAudit]) -> tuple[str, ...]:
    """Etapas que ningun repuntuado pone al dia: hay que repetirlas con MT5.

    Dos caras de lo mismo: el cambio de regla declarado que no se recalcula
    desde lo guardado, y la etapa sin `rescore_flag`, a la que cualquier cosa
    que haya torcido un veredicto le obliga a repetir porque no tiene otra
    forma.

    Una ventana distinta de la configurada no entra, aunque alcance a miles de
    filas: su veredicto vale para el periodo que corrio y nadie lo va a volver
    a dar. El propio runner lo trata asi -`final_tick_row_pending_for_dates()`
    deja de mirar la ventana en cuanto la fila tiene veredicto-, de modo que
    pedir repetir la etapa era mandar a MT5 un trabajo que el boton de esa
    etapa se salta fila por fila.

    Entre esta lista y `stages_to_rescore()` tiene que salir toda etapa que la
    tabla pinte en rojo, o la pantalla enrojece una fila sobre la que luego no
    sabe decir nada.
    """
    stages: list[str] = []
    for audit in audits:
        spec = stage_spec(audit.stage)
        if bool(audit.stale_verdicts) and spec.rules_change_fix == "repeat":
            stages.append(audit.stage)
        elif not spec.rescore_flag and _rescore_would_change_it(audit):
            stages.append(audit.stage)
    return tuple(stages)
