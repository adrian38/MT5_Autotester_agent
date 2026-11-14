"""Que filas juzgo cada etapa con reglas distintas a las de ahora.

Las reglas de las etapas se mueven a menudo, y cada fila guarda en SQLite los
umbrales con los que la juzgaron: las cuatro tablas Final Tick guardan sus
desvios maximos y su calidad minima, `candidate_robustness` sus bonus y su
rango, `candidate_regression` sus puntos, y las filas base y de semilla llevan
su `score_config` dentro de `metrics_json`. Comparar lo guardado con lo que hay
hoy en Configuracion dice, sin reparsear un solo reporte HTML, que veredictos
nacieron con otras reglas y que etapa hay que repuntuar.

La ventana no es una regla y no se cuenta como tal: no decide accepted ni
rejected, solo dice sobre que periodo corrio la fila (ver la cabecera de
`stage_specs`). De ella salen dos cuentas propias. `undated` son las filas sin
ninguna ventana -la fecha vacia de la que no ha corrido, o el cero que MT5
escribe en un reporte sin periodo-, y la que ademas tiene veredicto lo dio
sobre una ventana que no existe, asi que no vale por mucho que sus umbrales
coincidan. `offwindow` son las que corrieron sobre un periodo que ya no es el
configurado: ahi el veredicto esta bien dado y lo unico que cambia es volver a
correrlas.

Un cambio de regla no siempre se ve en lo guardado: cuando lo que cambia es el
codigo que aplica los umbrales -y no los umbrales- la fila guarda los mismos
numeros y la comparacion no encuentra nada. Eso se declara a mano en
`StageSpec.rules_changed_at`, y toda fila sellada antes (`judged_column`) se
cuenta como juzgada con la vieja, asi que el aviso se apaga solo en cuanto se
vuelve a juzgar.

Una etapa tampoco esta al dia cuando le faltan filas: quien pasa de rejected a
accepted se vuelve elegible para la siguiente y alli no se queda pendiente, es
que no tiene fila. `StageSpec.upstream_sql` cuenta esos.

Nada de esto dice como se arregla: eso es otra pregunta y vive en
`ubs/stage_fixes.py`, reexportado desde aqui. Lo que una etapa no guarda no se
puede auditar: cada `StageSpec` declara en `non_auditable` sus reglas ciegas
para que la pantalla lo diga en vez de callarlo. Aqui tampoco se decide que
estado es final ni cual es reintentable: lo primero lo pasa quien llama, lo
segundo es del runner.

El catalogo de etapas vive en `ubs/stage_specs.py` y se reexporta desde aqui,
que es donde lo pide todo el mundo. Aqui quedan las consultas que lo recorren.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from ubs.stage_fixes import stages_to_repeat, stages_to_rescore  # noqa: F401
from ubs.stage_values import (  # noqa: F401
    EPOCH_YEAR,
    NUMBER_TOLERANCE,
    _by_count,
    _display,
    _format_number,
    _same_value,
    _score_config,
    _stored_rule_value,
    _undated_reason,
    missing_window_date,
)
from ubs.stage_specs import (
    FINAL_TICK_BLIND_RULES,
    FINAL_TICK_RULES,
    SCORE_RULES,
    STAGE_SPEC_BY_KEY,
    STAGE_SPECS,
    PendingAudit,
    RuleFinding,
    RuleSpec,
    StageAudit,
    StageSpec,
    stage_spec,
)


__all__ = [
    "EPOCH_YEAR",
    "FINAL_TICK_BLIND_RULES",
    "FINAL_TICK_RULES",
    "MAX_REPORTED_IDS",
    "NUMBER_TOLERANCE",
    "SCORE_RULES",
    "STAGE_SPECS",
    "STAGE_SPEC_BY_KEY",
    "PendingAudit",
    "RuleFinding",
    "RuleSpec",
    "StageAudit",
    "StageSpec",
    "audit_pending_states",
    "audit_stage_rules",
    "missing_window_date",
    "stage_spec",
    "stages_to_repeat",
    "stages_to_rescore",
]


# Tope de identificadores que se devuelven por hallazgo: la pantalla los usa
# para que el usuario reconozca las filas, no para reprocesarlas una por una.
MAX_REPORTED_IDS = 200


def _selected_specs(stages: Iterable[str] | None) -> tuple[StageSpec, ...]:
    if stages is None:
        return STAGE_SPECS
    return tuple(stage_spec(key) for key in stages)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}


def _normalized_statuses(statuses: Iterable[str]) -> frozenset[str]:
    return frozenset(str(status or "").strip().lower() for status in statuses)


def _stage_rows(
    conn: sqlite3.Connection,
    spec: StageSpec,
    *,
    run_id: int,
    columns: set[str],
) -> list[sqlite3.Row]:
    selected = [spec.id_column, "status"]
    selected.extend(
        rule.column
        for rule in (*spec.rules, *spec.window)
        if rule.column and rule.column in columns
    )
    if spec.metrics_column and spec.metrics_column in columns:
        selected.append(spec.metrics_column)
    if spec.judged_column and spec.judged_column in columns:
        selected.append(spec.judged_column)
    where: list[str] = []
    params: list[object] = []
    if spec.row_filter:
        where.append(spec.row_filter)
    if run_id > 0 and spec.run_column and spec.run_column in columns:
        where.append(f"{spec.run_column}=?")
        params.append(int(run_id))
    clause = f" where {' and '.join(where)}" if where else ""
    sql = f"select {', '.join(dict.fromkeys(selected))} from {spec.table}{clause}"
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.row_factory = previous_factory


def _judged_before_change(
    row: sqlite3.Row,
    spec: StageSpec,
    *,
    columns: set[str],
    is_verdict: bool,
) -> bool:
    """Si la fila se juzgo con la regla anterior a `rules_changed_at`.

    La comparacion es de texto porque la memoria sella en ISO
    (`2026-11-12T05:17:49`), que ordena igual leido como fecha que como cadena.
    Una fila sin sello no ha corrido todavia y correra con la regla de ahora,
    asi que no cuenta; una etapa sin columna de sello no puede repartir el
    cambio fila por fila, y entonces lo que esta en duda es todo lo que ya
    tiene veredicto, que es lo unico que se sabe a ciencia cierta.
    """
    if not spec.rules_changed_at:
        return False
    if not spec.judged_column or spec.judged_column not in columns:
        return is_verdict
    stamp = str(row[spec.judged_column] or "").strip()
    if not stamp:
        return False
    return stamp < spec.rules_changed_at



@dataclass
class _StageTally:
    """Lo que se va contando por regla mientras se recorren las filas."""

    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    ids: dict[str, list[int]] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    affected: set[int] = field(default_factory=set)
    affected_verdicts: set[int] = field(default_factory=set)
    undated: set[int] = field(default_factory=set)
    undated_verdicts: set[int] = field(default_factory=set)
    # Cuantas filas guardan cada fecha que no es una fecha. Son dos casos
    # distintos -la vacia de la que no ha corrido y el cero de MT5- y decir
    # "vacia o 1970.01.01" obligaba a ir a mirar cual de los dos era.
    undated_values: dict[str, int] = field(default_factory=dict)
    offwindow: set[int] = field(default_factory=set)
    offwindow_verdicts: set[int] = field(default_factory=set)
    # Lo mismo que `counts`/`ids`/`verdicts`, pero para las fechas: cada una
    # lleva su cuenta aparte porque cada una se dice en su propia fila.
    offwindow_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    offwindow_ids: dict[str, list[int]] = field(default_factory=dict)
    offwindow_field_verdicts: dict[str, int] = field(default_factory=dict)
    stale: set[int] = field(default_factory=set)
    stale_verdicts: set[int] = field(default_factory=set)
    rows: int = 0
    row_verdicts: int = 0
    unknown: int = 0

    def bend(self, row_id: int, *, is_verdict: bool) -> None:
        """Apunta un veredicto torcido, venga de donde venga.

        La etapa cuenta en «a cuantos afecta» todo lo que tiene mal juzgado, no
        solo lo que un umbral movido explica: un veredicto dado sobre una
        ventana que no existe esta igual de mal, y dejarlo fuera daba una etapa
        diciendo «0 de 3798» con una hija debajo diciendo «1 de 3798».
        """
        if not is_verdict:
            return
        self.affected.add(row_id)
        self.affected_verdicts.add(row_id)

    def record_undated(self, values: set[str], row_id: int, *, is_verdict: bool) -> None:
        """Una fila cuenta una vez por cada motivo distinto que trae.

        Guarda dos fechas y puede no haber corrido en una y no haber producido
        nada en la otra: contar por motivo y no por celda deja cada numero
        diciendo a cuantas filas alcanza.
        """
        self.undated.add(row_id)
        if is_verdict:
            self.undated_verdicts.add(row_id)
        self.bend(row_id, is_verdict=is_verdict)
        for value in values:
            self.undated_values[value] = self.undated_values.get(value, 0) + 1

    def record_offwindow(
        self, values: dict[str, str], row_id: int, *, is_verdict: bool
    ) -> None:
        """Una fila que corrio sobre un periodo que ya no es el configurado.

        No cuenta como veredicto torcido ni como fila afectada: su veredicto es
        valido para el periodo que corrio, y nadie lo va a volver a dar. El
        propio runner lo dice -`final_tick_row_pending_for_dates()` ignora la
        ventana en cuanto la fila tiene veredicto-, asi que exigir repetir la
        etapa era pedir un trabajo que ningun boton hace. Se dice y ya.

        `values` trae el valor guardado de cada fecha movida, por su clave,
        porque cada fecha se cuenta y se dice por separado. La fila, en cambio,
        se cuenta una vez aunque se le hayan movido las dos.
        """
        self.offwindow.add(row_id)
        if is_verdict:
            self.offwindow_verdicts.add(row_id)
        for key, value in values.items():
            counts = self.offwindow_counts.setdefault(key, {})
            counts[value] = counts.get(value, 0) + 1
            self.offwindow_ids.setdefault(key, []).append(row_id)
            if is_verdict:
                self.offwindow_field_verdicts[key] = (
                    self.offwindow_field_verdicts.get(key, 0) + 1
                )

    def record(self, rule: RuleSpec, stored: object, row_id: int, *, is_verdict: bool) -> None:
        counts = self.counts.setdefault(rule.key, {})
        label = _display(stored, rule.kind)
        counts[label] = counts.get(label, 0) + 1
        self.ids.setdefault(rule.key, []).append(row_id)
        self.affected.add(row_id)
        self.bend(row_id, is_verdict=is_verdict)
        if is_verdict:
            self.verdicts[rule.key] = self.verdicts.get(rule.key, 0) + 1


def _tally_stage_row(
    row: sqlite3.Row,
    spec: StageSpec,
    expected: Mapping[str, object],
    tally: _StageTally,
    *,
    columns: set[str],
    finals: frozenset[str],
) -> None:
    is_verdict = str(row["status"] or "").strip().lower() in finals
    tally.rows += 1
    tally.row_verdicts += 1 if is_verdict else 0
    score_config = (
        _score_config(row[spec.metrics_column])
        if spec.metrics_column and spec.metrics_column in columns
        else None
    )
    row_id = int(row[spec.id_column] or 0)
    if _judged_before_change(row, spec, columns=columns, is_verdict=is_verdict):
        tally.stale.add(row_id)
        if is_verdict:
            # Solo arrastra la que ya tiene veredicto, y por la misma razon que
            # una fecha invalida: la que no ha corrido se juzgara con la regla
            # de ahora, y un repuntuado ni la mira -no hay nada que volver a
            # puntuar-. Contarla dejaba la etapa en rojo para siempre.
            tally.stale_verdicts.add(row_id)
        tally.bend(row_id, is_verdict=is_verdict)
    row_unknown = False
    for rule in spec.rules:
        if rule.key not in expected:
            continue
        stored, present = _stored_rule_value(
            row, rule, columns=columns, score_config=score_config
        )
        if not present:
            # Una fila sin `score_config` no prueba nada: se cuenta aparte en
            # vez de inventarle un valor guardado. Que la columna no exista en
            # esta memoria es cosa del esquema, no de la fila, y no cuenta.
            row_unknown = row_unknown or bool(rule.score_field)
            continue
        if _same_value(stored, expected[rule.key], rule.kind):
            continue
        tally.record(rule, stored, row_id, is_verdict=is_verdict)
    _tally_window_row(
        row, spec, expected, tally, row_id, columns=columns, is_verdict=is_verdict
    )
    tally.unknown += 1 if row_unknown else 0


def _tally_window_row(
    row: sqlite3.Row,
    spec: StageSpec,
    expected: Mapping[str, object],
    tally: _StageTally,
    row_id: int,
    *,
    columns: set[str],
    is_verdict: bool,
) -> None:
    """La ventana de la fila: la que falta, o la que ya no es la configurada.

    Se mira aparte de las reglas porque no es una: no decide accepted ni
    rejected, solo dice sobre que periodo corrio. De las dos cosas que pueden
    pasarle, una fila puede traer las dos -la fecha de arranque sin poner y la
    de cierre movida- y entonces cuenta en cada una.

    Cada fecha movida se devuelve por su clave, porque cada una se dice en su
    propia fila: juntarlas en una sola celda obligaba a escribir «Desde» delante
    de cada valor para saber cual era, y entonces ni la celda ni la columna de
    al lado daban de si.
    """
    undated: set[str] = set()
    elsewhere: dict[str, str] = {}
    for window_field in spec.window:
        if window_field.key not in expected:
            continue
        stored, present = _stored_rule_value(
            row, window_field, columns=columns, score_config=None
        )
        if not present:
            continue
        if missing_window_date(stored):
            undated.add(_undated_reason(stored))
            continue
        if _same_value(stored, expected[window_field.key], window_field.kind):
            continue
        elsewhere[window_field.key] = _display(stored, window_field.kind)
    if undated:
        tally.record_undated(undated, row_id, is_verdict=is_verdict)
    if elsewhere:
        tally.record_offwindow(elsewhere, row_id, is_verdict=is_verdict)


def _window_findings(
    spec: StageSpec, expected: Mapping[str, object], tally: _StageTally
) -> tuple[RuleFinding, ...]:
    """Cada fecha que ya no es la configurada, con lo que guardo cada fila.

    Misma forma que un hallazgo de regla porque se lee igual -que hay ahora,
    con que se corrio y a cuantos alcanza- y se pinta en las mismas columnas.
    Lo que cambia es el tono: esto no torcio ningun veredicto.
    """
    findings: list[RuleFinding] = []
    for window_field in spec.window:
        counts = tally.offwindow_counts.get(window_field.key)
        if not counts:
            continue
        ids = tally.offwindow_ids.get(window_field.key, [])
        findings.append(
            RuleFinding(
                stage=spec.key,
                stage_label=spec.label,
                rule=window_field.key,
                rule_label=window_field.label,
                current=_display(expected[window_field.key], window_field.kind),
                stored=_by_count(counts),
                rows=len(ids),
                verdicts=tally.offwindow_field_verdicts.get(window_field.key, 0),
                ids=tuple(ids[:MAX_REPORTED_IDS]),
            )
        )
    return tuple(findings)


def _stage_findings(
    spec: StageSpec, expected: Mapping[str, object], tally: _StageTally
) -> tuple[RuleFinding, ...]:
    findings: list[RuleFinding] = []
    for rule in spec.rules:
        counts = tally.counts.get(rule.key)
        if not counts:
            continue
        ids = tally.ids.get(rule.key, [])
        findings.append(
            RuleFinding(
                stage=spec.key,
                stage_label=spec.label,
                rule=rule.key,
                rule_label=rule.label,
                current=_display(expected[rule.key], rule.kind),
                stored=tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
                rows=len(ids),
                verdicts=tally.verdicts.get(rule.key, 0),
                ids=tuple(ids[:MAX_REPORTED_IDS]),
            )
        )
    return tuple(findings)


def audit_stage_rules(
    conn: sqlite3.Connection,
    current: Mapping[str, Mapping[str, object]],
    *,
    final_statuses: Iterable[str],
    run_id: int = 0,
    stages: Iterable[str] | None = None,
) -> list[StageAudit]:
    """Compara, etapa por etapa, lo guardado en cada fila con las reglas de hoy.

    `current` lleva por etapa el valor que la interfaz aplica ahora a cada
    regla; una regla que no aparezca ahi no se audita. `final_statuses` decide
    que fila cuenta como veredicto y cual sigue pendiente.
    """
    finals = _normalized_statuses(final_statuses)
    audits: list[StageAudit] = []
    for spec in _selected_specs(stages):
        expected = current.get(spec.key) or {}
        columns = _table_columns(conn, spec.table)
        if not columns:
            audits.append(
                StageAudit(
                    stage=spec.key,
                    stage_label=spec.label,
                    rows=0,
                    verdicts=0,
                    unknown=0,
                    missing_table=True,
                    non_auditable=spec.non_auditable,
                )
            )
            continue
        tally = _StageTally()
        for row in _stage_rows(conn, spec, run_id=run_id, columns=columns):
            _tally_stage_row(row, spec, expected, tally, columns=columns, finals=finals)
        audits.append(_stage_audit(spec, expected, tally))
    return audits


def _stage_audit(
    spec: StageSpec, expected: Mapping[str, object], tally: _StageTally
) -> StageAudit:
    """Lo contado en una etapa, ya en la forma en que se contesta."""
    return StageAudit(
        stage=spec.key,
        stage_label=spec.label,
        rows=tally.rows,
        verdicts=tally.row_verdicts,
        unknown=tally.unknown,
        affected=len(tally.affected),
        affected_verdicts=len(tally.affected_verdicts),
        undated=len(tally.undated),
        undated_verdicts=len(tally.undated_verdicts),
        undated_stored=_by_count(tally.undated_values),
        offwindow=len(tally.offwindow),
        offwindow_verdicts=len(tally.offwindow_verdicts),
        window_findings=_window_findings(spec, expected, tally),
        stale=len(tally.stale),
        stale_verdicts=len(tally.stale_verdicts),
        findings=_stage_findings(spec, expected, tally),
        non_auditable=spec.non_auditable,
    )


def _missing_rows(conn: sqlite3.Connection, spec: StageSpec, *, run_id: int) -> int:
    """Elegibles que todavia no tienen fila en la etapa.

    Una memoria a la que le falta una tabla de mas arriba no es un error que
    valga la pena contar aparte: la etapa ya se denuncia sola con
    `missing_table`, y aqui se responde cero.
    """
    if not spec.upstream_sql:
        return 0
    run_filter = ""
    params: tuple[object, ...] = ()
    if run_id > 0 and spec.run_column:
        run_filter = "and c.run_id=?"
        params = (int(run_id),)
    try:
        row = conn.execute(spec.upstream_sql.format(run_filter=run_filter), params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


def audit_pending_states(
    conn: sqlite3.Connection,
    *,
    final_statuses: Iterable[str],
    run_id: int = 0,
    stages: Iterable[str] | None = None,
) -> list[PendingAudit]:
    """Resumen por etapa de las filas que no llegaron a un estado final."""
    finals = _normalized_statuses(final_statuses)
    summaries: list[PendingAudit] = []
    for spec in _selected_specs(stages):
        columns = _table_columns(conn, spec.table)
        if not columns:
            summaries.append(
                PendingAudit(
                    stage=spec.key,
                    stage_label=spec.label,
                    rows=0,
                    pending=0,
                    missing_table=True,
                )
            )
            continue
        rows = _stage_rows(conn, spec, run_id=run_id, columns=columns)
        counts: dict[str, int] = {}
        ids: list[int] = []
        for row in rows:
            status = str(row["status"] or "").strip().lower() or "(sin estado)"
            if status in finals:
                continue
            counts[status] = counts.get(status, 0) + 1
            ids.append(int(row[spec.id_column] or 0))
        summaries.append(
            PendingAudit(
                stage=spec.key,
                stage_label=spec.label,
                rows=len(rows),
                pending=len(ids),
                by_status=tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
                ids=tuple(ids[:MAX_REPORTED_IDS]),
                missing=_missing_rows(conn, spec, run_id=run_id),
            )
        )
    return summaries
