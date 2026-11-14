"""Las dos consultas del auditor UBS y como se pintan en su tabla.

`ui/ubs_auditor_logic.py` se paso de las 600 lineas. Lo que sale aqui es lo
que lee la memoria y la convierte en filas -reglas movidas, reglas cambiadas,
pendientes y elegibles sin fila-; alli se queda lo que elige cuenta y run,
reune las reglas vigentes y lanza los dos arreglos.

Las dos consultas llenan la misma tabla porque responden a lo mismo: que etapa
esta fuera de sitio y a cuantas semillas o candidatos arrastra. Cada una trae
sus propias columnas (`AUDITOR_RULE_LAYOUT`, `AUDITOR_PENDING_LAYOUT`) y una
fila por etapa, con el detalle colgando de ella.
"""
from __future__ import annotations

import sqlite3

from ubs.db import connect_memory
from ubs.stage_rules_audit import (
    STAGE_SPECS,
    audit_pending_states,
    audit_stage_rules,
    stage_spec,
    stages_to_repeat,
)
from ui.ubs_auditor_view import AUDITOR_PENDING_LAYOUT, AUDITOR_RULE_LAYOUT
from ui.ubs_search_audit_base import AUDIT_FINAL_STATUSES

# Valores distintos que caben en la celda «SE JUZGO CON» antes de resumir.
MAX_STORED_VALUES_SHOWN = 2


def _counted(count: int, stage_key: str) -> str:
    """Cuenta con lo que cuenta: «1046 candidatos», no «1046 filas»."""
    return f"{count} {stage_spec(stage_key).unit}"


def _of_total(count: int, total: int, stage_key: str) -> str:
    """«2 de 3798 candidatos»: el numero y con que se compara, en la misma celda.

    Antes el tamano de la etapa y los afectados vivian en columnas distintas y
    la fila se leia como si se contradijera: «afecta a 2 candidatos» al lado de
    un «3798 candidatos» que contaba otra cosa.
    """
    return f"{count} de {_counted(total, stage_key)}"


def _blind_rules_note() -> str:
    """Las reglas que ninguna auditoria puede ver, dichas una sola vez.

    Como fila de la tabla se repetian en cuatro etapas y se cortaban en su
    columna. Aqui las etapas que comparten reglas ciegas van juntas: las Final
    Tick son ciegas a los mismos minimos de operaciones.
    """
    grouped: dict[tuple[str, ...], list[str]] = {}
    for spec in STAGE_SPECS:
        if spec.non_auditable:
            grouped.setdefault(spec.non_auditable, []).append(spec.label)
    if not grouped:
        return ""
    parts = [
        f"{', '.join(labels)} ({', '.join(rules)})" for rules, labels in grouped.items()
    ]
    return (
        "Reglas ciegas, que la etapa aplica pero no guarda, asi que moverlas no "
        "se ve desde aqui: " + "; ".join(parts) + ". El repuntuado si las aplica: "
        "si moviste una, marca la etapa en la tabla y pulsa Repuntuar afectados."
    )


def _stored_cell(finding) -> str:
    """Los valores con los que se juzgo, sin pasarse de celda.

    Una etapa repuntuada a trozos acumula muchos valores distintos, y la lista
    entera solo servia para que la columna la cortara a mitad de un numero.
    Van ordenados por cuantos arrastra cada uno, asi que los dos primeros son
    los que explican casi todo y el resto se cuenta.
    """
    return _values_cell(finding.stored)


def _values_cell(values) -> str:
    """«valor (cuantos)», recortado, para cualquier celda de la columna ancha.

    Lo comparten los umbrales movidos y las fechas que no son fechas: las dos
    responden a «se juzgo con», y la segunda decia «(vacia o 1970.01.01)», una
    adivinanza de las dos que podia ser, cuando el dato estaba en la fila.
    """
    shown = tuple(values)[:MAX_STORED_VALUES_SHOWN]
    rest = len(values) - len(shown)
    summary = ", ".join(f"{value} ({count})" for value, count in shown) or "sin dato"
    return f"{summary} +{rest} mas" if rest > 0 else summary


def _moved_verdicts(audit) -> int:
    """Reglas movidas que llegaron a torcer un veredicto.

    Una regla movida que solo alcanza a filas pendientes no ha torcido nada:
    esas filas se juzgaran con el valor de ahora en cuanto corran.
    """
    return sum(1 for finding in audit.findings if finding.verdicts)


def _stage_headline(audit) -> str:
    """Veredicto de la etapa, que es lo que se lee sin desplegarla.

    Una etapa con los umbrales de hoy pero con filas juzgadas sobre una ventana
    imposible no esta al dia: esos veredictos no valen. Decir «Al dia» en verde
    escondia justo lo que habia que mirar. Lo mismo vale para una etapa cuya
    regla cambio sin mover un solo umbral.

    Cuando coinciden varias cosas gana la mas concreta, que es la que dice que
    regla mirar; las demas siguen colgando de la etapa como hijas, que es donde
    se explican. Lo unico que la cabecera tiene que hacer bien es no decir «Al
    dia».

    La ventana no sale aqui aunque alcance a miles de filas: ninguna de ellas
    esta mal juzgada -corrio sobre ese periodo y su veredicto vale-, asi que la
    etapa esta al dia. Se dice en una hija apagada, que es lo que es: un dato.
    """
    count = _moved_verdicts(audit)
    if count:
        return "1 regla movida" if count == 1 else f"{count} reglas movidas"
    if audit.stale_verdicts:
        return "Regla cambiada"
    broken = audit.undated_verdicts
    if broken:
        return "1 sin fecha valida" if broken == 1 else f"{broken} sin fecha valida"
    return "Al dia"


def _rules_status_line(audits, scope: str) -> str:
    """Resumen de una linea de la auditoria de reglas.

    Las tres cosas que puede encontrar son independientes y se dicen por
    separado: un umbral movido, una regla que cambio sin mover un umbral, y una
    fila juzgada sobre una ventana que no existe. Mezclarlas en una sola frase
    hacia que arreglar una pareciera arreglarlas todas.

    Todo lo que cuenta son veredictos, que es lo unico que se puede torcer y lo
    unico que un repuntuado puede enderezar.
    """
    moved = sum(_moved_verdicts(audit) for audit in audits)
    changed = sum(1 for audit in audits if audit.stale_verdicts)
    parts: list[str] = []
    if moved:
        stages = sum(1 for audit in audits if _moved_verdicts(audit))
        parts.append(f"{stages} etapa(s) con {moved} regla(s) movida(s)")
    if changed:
        rows = sum(audit.stale_verdicts for audit in audits)
        parts.append(f"{changed} etapa(s) con la regla cambiada ({rows} veredicto(s))")
    if not parts:
        head = f"{scope}: ningun veredicto se dio con otras reglas."
    else:
        affected = sum(audit.affected_verdicts for audit in audits)
        head = (
            f"{scope}: {'; '.join(parts)}. Afecta a {affected} veredicto(s) de "
            "semilla o candidato, sumando etapas."
        )
    return head + _repeat_tail(audits)


def _repeat_tail(audits) -> str:
    """Lo que sigue rojo despues de pulsar Repuntuar afectados, dicho aparte.

    Sin esta frase la pantalla parecia no haberse enterado de que el boton no
    llega a todo. Y lo que si arregla tambien se dice, porque no se ve: el
    veredicto dado sin ventana no se vuelve a juzgar -no hay con que-, se
    devuelve a pendiente, y quien lo lea esperando un accepted nuevo pensaria
    que fallo.
    """
    repeat = [stage_spec(stage).label for stage in stages_to_repeat(audits)]
    undated = sum(audit.undated_verdicts for audit in audits)
    tail = ""
    if repeat:
        tail += (
            " El repuntuado no llega a " + ", ".join(repeat) + ": hay que repetir "
            "la etapa."
        )
    if undated:
        tail += (
            f" Otros {undated} se juzgaron sin una ventana que existiera: el "
            "repuntuado los devuelve a pendiente."
        )
    return tail


def _pending_headline(summary) -> str:
    """Por que una etapa no esta al dia: a medias, sin empezar, o las dos."""
    if summary.pending and summary.missing:
        return "A medias y sin correr"
    return "Se quedaron a medias" if summary.pending else "Sin correr"


def _pending_status_line(pending: int, missing: int, scope: str) -> str:
    """Resumen de una linea de la consulta de pendientes.

    Los dos huecos se cuentan por separado porque no se parecen: uno empezo y
    no termino, y el otro no empezo nunca -quien acaba de volverse elegible al
    repuntuar-. El mismo boton arregla los dos, asi que la frase los suma al
    final en vez de mandar a sitios distintos.
    """
    if not pending and not missing:
        return f"{scope}: todas las etapas en estado final."
    parts: list[str] = []
    if pending:
        parts.append(f"{pending} sin estado final")
    if missing:
        parts.append(f"{missing} sin fila en su etapa")
    return (
        f"{scope}: {' y '.join(parts)} semilla(s)/candidato(s). "
        "Marca una etapa y pulsa Arreglar pendientes."
    )



def _rule_children(audit) -> tuple:
    """El detalle que cuelga de una etapa en la auditoria de reglas.

    Todas las cuentas son de veredictos. Una fila pendiente guarda el valor
    viejo igual que las demas, pero el repuntuado no la toca -no hay nada que
    volver a puntuar- y cuando corra lo hara con la regla de ahora: contarla
    daba una hija diciendo «303 de 303» bajo una etapa que decia «207 de 303».
    """
    children: list[tuple] = []
    for finding in audit.findings:
        # El hijo no repite «Regla movida»: la columna REGLA ya dice cual es,
        # y la palabra sobraba en todas y cada una de ellas. Toda regla movida
        # la endereza el boton, asi que no hay nada que advertir aqui.
        children.append((("",
            finding.rule_label, finding.current, _stored_cell(finding),
            _of_total(finding.verdicts, audit.rows, audit.stage),
        ), "rejected" if finding.verdicts else "pending"))
    if audit.stale:
        # No hay regla que senalar: lo que cambio fue como se aplican todas,
        # asi que la fila habla de la etapa entera.
        spec = stage_spec(audit.stage)
        children.append(((
            "Regla cambiada", "(toda la etapa)", "",
            f"{spec.rules_change_note} ({spec.rules_changed_at[:10]})",
            _of_total(audit.stale_verdicts, audit.rows, audit.stage),
        ), "rejected" if audit.stale_verdicts else "pending"))
    if audit.undated:
        children.append(((
            "Sin fecha valida", "", "", _values_cell(audit.undated_stored),
            _of_total(audit.undated_verdicts, audit.rows, audit.stage),
        ), "rejected" if audit.undated_verdicts else "pending"))
    for window in audit.window_findings:
        # Una fila por fecha, como cualquier hallazgo, porque la de al lado ya
        # dice cual es y asi el valor cabe entero. Apagada y sin cuenta en «a
        # cuantos afecta»: no hay veredicto torcido que contar, solo filas que
        # corrieron sobre otro periodo. Cuantas, lo dice su propia celda.
        children.append(((
            "Otra ventana", window.rule_label, window.current,
            _stored_cell(window), "",
        ), "pending"))
    if audit.unknown:
        children.append(((
            "Sin saber", "", "", "(no lo guardaron)",
            _of_total(0, audit.rows, audit.stage),
        ), "pending"))
    return tuple(children)


def _rule_group(audit) -> tuple:
    """Una etapa de la auditoria de reglas: su resumen y lo que cuelga de el."""
    if audit.missing_table:
        return (audit.stage, audit.stage_label, (
            "Sin tabla", "", "", "la memoria no tiene su tabla", "",
        ), "pending", ())
    broken = _moved_verdicts(audit) or audit.stale_verdicts or audit.undated_verdicts
    return (audit.stage, audit.stage_label, (
        _stage_headline(audit), "", "", "",
        _of_total(audit.affected_verdicts, audit.rows, audit.stage),
    ), "rejected" if broken else "accepted", _rule_children(audit))


def _pending_group(summary) -> tuple:
    """Una etapa de la consulta de pendientes, con sus dos huecos separados."""
    if summary.missing_table:
        return (summary.stage, summary.stage_label, (
            "Sin tabla", "", "", "la memoria no tiene su tabla", "",
        ), "pending", ())
    # Los que nunca llegaron a tener fila cuentan para el tamano de la etapa:
    # son parte de lo que deberia estar juzgado, y sin ellos el «de cuantos» se
    # mide contra una etapa mas pequena de lo que es.
    total = summary.rows + summary.missing
    if not summary.pending and not summary.missing:
        return (summary.stage, summary.stage_label, (
            "Al dia", "", "", "", _of_total(0, total, summary.stage),
        ), "accepted", ())
    children: list[tuple] = []
    if summary.pending:
        # Mismo formato que los hallazgos, «valor (cuantos)», para que la
        # columna ancha se lea igual venga de la consulta que venga.
        detail = ", ".join(f"{status} ({count})" for status, count in summary.by_status)
        children.append(((
            "Se quedaron a medias", "", "", detail,
            _of_total(summary.pending, total, summary.stage),
        ), "rejected"))
    if summary.missing:
        children.append(((
            "Sin fila todavia", "", "", "(elegibles que no han corrido)",
            _of_total(summary.missing, total, summary.stage),
        ), "rejected"))
    return (summary.stage, summary.stage_label, (
        _pending_headline(summary), "", "", "",
        _of_total(summary.pending + summary.missing, total, summary.stage),
    ), "rejected", tuple(children))


class UBSAuditorQueriesMixin:
    """Lee la memoria auditada y la deja pintada en la tabla del auditor."""

    def _ubs_auditor_open_memory(self) -> sqlite3.Connection | None:
        memory_path = self._ubs_auditor_memory_path()
        if memory_path is None:
            self.ubs_auditor_status.set("Cuenta invalida.")
            return None
        if not memory_path.exists():
            self.ubs_auditor_status.set(f"No existe memoria {self.ubs_auditor_account.get()}.")
            return None
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        self._ensure_ubs_memory_schema(conn)
        return conn

    def _audit_ubs_stage_rules_clicked(self) -> None:
        try:
            current = self._ubs_auditor_current_rules()
        except ValueError as exc:
            self._show_error("Auditor UBS", str(exc))
            return
        conn = self._ubs_auditor_open_memory()
        if conn is None:
            return
        try:
            audits = audit_stage_rules(
                conn,
                current,
                final_statuses=AUDIT_FINAL_STATUSES,
                run_id=self._ubs_auditor_selected_run_id(),
            )
        except sqlite3.Error as exc:
            self._show_error("Auditor UBS", f"Error auditando reglas: {exc}")
            return
        finally:
            conn.close()

        self._ubs_auditor_audits = tuple(audits)
        self._populate_ubs_auditor_rows(
            [_rule_group(audit) for audit in audits], AUDITOR_RULE_LAYOUT
        )
        self.ubs_auditor_blind_rules.set(_blind_rules_note())
        self.ubs_auditor_status.set(
            _rules_status_line(audits, self._ubs_auditor_scope_label())
        )

    def _audit_ubs_pending_states_clicked(self) -> None:
        conn = self._ubs_auditor_open_memory()
        if conn is None:
            return
        try:
            summaries = audit_pending_states(
                conn,
                final_statuses=AUDIT_FINAL_STATUSES,
                run_id=self._ubs_auditor_selected_run_id(),
            )
        except sqlite3.Error as exc:
            self._show_error("Auditor UBS", f"Error consultando pendientes: {exc}")
            return
        finally:
            conn.close()

        self._ubs_auditor_audits = ()
        self._populate_ubs_auditor_rows(
            [_pending_group(summary) for summary in summaries], AUDITOR_PENDING_LAYOUT
        )
        self.ubs_auditor_blind_rules.set("")
        self.ubs_auditor_status.set(
            _pending_status_line(
                sum(summary.pending for summary in summaries),
                sum(summary.missing for summary in summaries),
                self._ubs_auditor_scope_label(),
            )
        )

    def _populate_ubs_auditor_rows(self, groups, layout=AUDITOR_RULE_LAYOUT) -> None:
        """Una fila por etapa, y su detalle colgando de ella.

        Antes cada hallazgo era una fila suelta que repetia el nombre de la
        etapa, y ocho etapas se leian como veinte. Ahora la etapa se resume en
        su propia fila y solo se despliega sola cuando hay reglas movidas.

        `layout` son las columnas de la consulta que llena la tabla: cada una
        pregunta una cosa distinta, y ensenar las seis siempre dejaba media
        tabla vacia bajo titulos que no venian a cuento.
        """
        tree = getattr(self, "ubs_auditor_tree", None)
        if tree is None:
            return
        self._show_ubs_auditor_columns(layout)
        for item in tree.get_children():
            tree.delete(item)
        self.ubs_auditor_row_stage = {}
        for stage, stage_label, values, tag, children in groups:
            parent = tree.insert(
                "",
                "end",
                text=f" {stage_label}",
                values=values,
                tags=(tag,),
                open=any(child_tag == "rejected" for _values, child_tag in children),
            )
            self.ubs_auditor_row_stage[parent] = stage
            for child_values, child_tag in children:
                item = tree.insert(parent, "end", values=child_values, tags=(child_tag,))
                self.ubs_auditor_row_stage[item] = stage

    def _selected_ubs_auditor_stage(self) -> str:
        tree = getattr(self, "ubs_auditor_tree", None)
        if tree is None:
            return ""
        selection = tree.selection()
        if not selection:
            return ""
        return str(getattr(self, "ubs_auditor_row_stage", {}).get(selection[0], ""))
