"""Que filas juzgo cada etapa con reglas distintas a las de ahora.

Las reglas de las etapas se mueven a menudo, y cada fila guarda en SQLite los
umbrales con los que la juzgaron: las cuatro tablas Final Tick guardan sus
desvios maximos y su calidad minima, `candidate_robustness` sus bonus y su
rango, `candidate_regression` sus puntos, y las filas base y de semilla llevan
su `score_config` dentro de `metrics_json`. Comparar lo guardado con lo que hay
hoy en Configuracion dice, sin reparsear un solo reporte HTML, que veredictos
nacieron con otras reglas y que etapa hay que repuntuar.

Una fecha guardada que no es una fecha -vacia, o el cero que MT5 escribe en un
reporte sin periodo- tampoco es un umbral movido: es una fila que no se juzgo
sobre ninguna ventana. Se cuenta aparte (`undated`), y si ademas tiene
veredicto, ese veredicto no vale y la etapa no esta al dia por mucho que sus
umbrales coincidan.

Un cambio de regla no siempre se ve en lo guardado. Cuando lo que cambia es el
codigo que aplica los umbrales -y no los umbrales- la fila sigue guardando los
mismos numeros y la comparacion no encuentra nada, por mucho que el veredicto
haya nacido con otra regla. Eso se declara a mano: `StageSpec.rules_changed_at`
lleva el momento desde el que la regla nueva esta en vigor, y toda fila sellada
antes se cuenta como juzgada con la vieja. El sello es `judged_column`, que ya
existe en las ocho tablas, asi que el aviso se apaga solo en cuanto la fila se
vuelve a juzgar; declarar la fecha es el unico paso manual, y va en el mismo
commit que cambia la regla.

Una etapa tampoco esta al dia cuando le faltan filas. Quien pasa de rejected a
accepted se vuelve elegible para la etapa siguiente, y alli no se queda
pendiente: no tiene fila ninguna, asi que no sale en ningun recuento de
estados. `StageSpec.upstream_sql` cuenta esos, los que deberian tener fila y no
la tienen.

Lo que una etapa no guarda no se puede auditar asi: cada `StageSpec` declara en
`non_auditable` sus reglas ciegas para que la pantalla lo diga en vez de
callarlo. Aqui no se decide que estado es final ni que estado es reintentable:
lo primero lo pasa quien llama, y lo segundo pertenece al runner de cada etapa.

El catalogo de etapas vive en `ubs/stage_specs.py` y se reexporta desde aqui,
que es donde lo pide todo el mundo. Aqui quedan las consultas que lo recorren.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ubs.stage_specs import (
    FINAL_TICK_BLIND_RULES,
    FINAL_TICK_RULES,
    SCORE_RULES,
    STAGE_SPEC_BY_KEY,
    STAGE_SPECS,
    RuleSpec,
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
    "stage_spec",
    "stages_to_repeat",
    "stages_to_rescore",
]


# Tope de identificadores que se devuelven por hallazgo: la pantalla los usa
# para que el usuario reconozca las filas, no para reprocesarlas una por una.
MAX_REPORTED_IDS = 200

# Margen con el que se comparan dos umbrales numericos. SQLite guarda reales y
# la interfaz escribe texto: sin margen, "35" y 35.0 se verian distintos.
NUMBER_TOLERANCE = 1e-6

# Ano hasta el que una fecha guardada se lee como el cero de MT5 y no como una
# fecha que alguien puso.
EPOCH_YEAR = 1970


@dataclass(frozen=True)
class RuleFinding:
    """Una regla cuyo valor guardado no es el que hay hoy en Configuracion."""

    stage: str
    stage_label: str
    rule: str
    rule_label: str
    current: str
    stored: tuple[tuple[str, int], ...]
    rows: int
    verdicts: int
    ids: tuple[int, ...] = ()

    def stored_summary(self) -> str:
        return ", ".join(f"{value} ({count})" for value, count in self.stored) or "sin dato"


@dataclass(frozen=True)
class StageAudit:
    """Lo que la auditoria encontro en una etapa."""

    stage: str
    stage_label: str
    rows: int
    verdicts: int
    unknown: int
    # Filas tocadas por al menos una regla movida o cambiada. Se cuenta aparte
    # porque los identificadores de cada hallazgo vienen recortados por
    # `MAX_REPORTED_IDS`.
    affected: int = 0
    # De las afectadas, las que ya tienen veredicto: son las unicas que pueden
    # cambiar al repuntuar, y por eso la pantalla las cuenta aparte.
    affected_verdicts: int = 0
    # Filas cuya fecha guardada no es una fecha (vacia o el cero de MT5), y
    # cuantas de ellas ya tienen veredicto. Las que lo tienen son el problema:
    # se juzgaron sobre una ventana imposible, asi que su veredicto no vale.
    # Las que no, simplemente no han corrido todavia.
    undated: int = 0
    undated_verdicts: int = 0
    # Que fechas eran y a cuantas filas alcanza cada una: la vacia y el cero de
    # MT5 son dos problemas distintos, y la celda tiene que decir cual toco.
    undated_stored: tuple[tuple[str, int], ...] = ()
    # Filas selladas antes de `rules_changed_at`: sus umbrales son los de hoy,
    # pero la regla que los aplico no. Las que ya tienen veredicto son las que
    # pueden cambiar al repuntuar, igual que en un umbral movido.
    stale: int = 0
    stale_verdicts: int = 0
    findings: tuple[RuleFinding, ...] = ()
    missing_table: bool = False
    non_auditable: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingAudit:
    """Filas de una etapa que no llegaron a un estado final."""

    stage: str
    stage_label: str
    rows: int
    pending: int
    by_status: tuple[tuple[str, int], ...] = ()
    ids: tuple[int, ...] = ()
    missing_table: bool = False
    # Elegibles que no llegaron a tener fila: quien acaba de pasar a accepted
    # arriba todavia no ha corrido aqui, y sin fila no cuenta como pendiente.
    missing: int = 0


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
    selected.extend(rule.column for rule in spec.rules if rule.column and rule.column in columns)
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


def _score_config(raw: object) -> Mapping[str, object] | None:
    try:
        payload = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    stored = payload.get("score_config")
    return stored if isinstance(stored, Mapping) else None


def _format_number(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f"{number:g}"


def _same_value(stored: object, current: object, kind: str) -> bool:
    if kind == "date":
        return str(stored or "").strip() == str(current or "").strip()
    try:
        return abs(float(stored) - float(current)) <= NUMBER_TOLERANCE  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(stored or "").strip() == str(current or "").strip()


def _missing_date(value: object) -> bool:
    """Una fecha vacia o el cero de MT5 no es una regla: es la falta de una.

    Cuando el reporte OHLC no trae periodo -un backtest que no produjo nada-
    MT5 escribe 1970.01.01, y `_read_ohlc_report_cfg_dates()` lo copia tal cual
    a la fila. Compararlo con la ventana de hoy no dice que la regla se haya
    movido: dice que esa fila no se juzgo con ninguna. Lo mismo vale para la
    fecha vacia de una fila que aun no ha corrido.
    """
    text = str(value or "").strip()
    if not text:
        return True
    year = text[:4]
    return year.isdigit() and int(year) <= EPOCH_YEAR


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


def _display(value: object, kind: str) -> str:
    if kind == "date":
        return str(value or "").strip() or "(vacio)"
    return _format_number(value)


def _stored_rule_value(
    row: sqlite3.Row,
    rule: RuleSpec,
    *,
    columns: set[str],
    score_config: Mapping[str, object] | None,
) -> tuple[object, bool]:
    """Valor con el que se juzgo la fila, y si la fila no lo guarda."""
    if rule.column:
        if rule.column not in columns:
            return None, False
        return row[rule.column], True
    if rule.score_field:
        if score_config is None or rule.score_field not in score_config:
            return None, False
        return score_config[rule.score_field], True
    return None, False


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
    stale: set[int] = field(default_factory=set)
    stale_verdicts: set[int] = field(default_factory=set)
    rows: int = 0
    row_verdicts: int = 0
    unknown: int = 0

    def record_undated(self, values: set[str], row_id: int, *, is_verdict: bool) -> None:
        """Una fila cuenta una vez por cada valor distinto que guarda.

        Guarda dos fechas y puede traer la vacia en una y el cero de MT5 en la
        otra: contar por valor y no por celda deja cada numero diciendo a
        cuantas filas alcanza.
        """
        self.undated.add(row_id)
        if is_verdict:
            self.undated_verdicts.add(row_id)
        for value in values:
            self.undated_values[value] = self.undated_values.get(value, 0) + 1

    def record(self, rule: RuleSpec, stored: object, row_id: int, *, is_verdict: bool) -> None:
        counts = self.counts.setdefault(rule.key, {})
        label = _display(stored, rule.kind)
        counts[label] = counts.get(label, 0) + 1
        self.ids.setdefault(rule.key, []).append(row_id)
        self.affected.add(row_id)
        if is_verdict:
            self.affected_verdicts.add(row_id)
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
            tally.affected.add(row_id)
            tally.affected_verdicts.add(row_id)
    row_unknown = False
    row_undated: set[str] = set()
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
        if rule.kind == "date" and _missing_date(stored):
            row_undated.add(_display(stored, rule.kind))
            continue
        if _same_value(stored, expected[rule.key], rule.kind):
            continue
        tally.record(rule, stored, row_id, is_verdict=is_verdict)
    if row_undated:
        tally.record_undated(row_undated, row_id, is_verdict=is_verdict)
    tally.unknown += 1 if row_unknown else 0


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
        audits.append(
            StageAudit(
                stage=spec.key,
                stage_label=spec.label,
                rows=tally.rows,
                verdicts=tally.row_verdicts,
                unknown=tally.unknown,
                affected=len(tally.affected),
                affected_verdicts=len(tally.affected_verdicts),
                undated=len(tally.undated),
                undated_verdicts=len(tally.undated_verdicts),
                undated_stored=tuple(
                    sorted(tally.undated_values.items(), key=lambda item: (-item[1], item[0]))
                ),
                stale=len(tally.stale),
                stale_verdicts=len(tally.stale_verdicts),
                findings=_stage_findings(spec, expected, tally),
                non_auditable=spec.non_auditable,
            )
        )
    return audits


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


def _stale_rescorable(audit: StageAudit, spec: StageSpec) -> bool:
    """Si el cambio de regla declarado se arregla repuntuando lo guardado.

    Manda `stale_verdicts` y no `stale`: lo que no tiene veredicto no hay quien
    lo repuntue, y encolar la etapa por ello la dejaba en la cola para siempre.
    """
    return bool(audit.stale_verdicts) and spec.rules_change_fix == "rescore"


def stages_to_rescore(audits: Sequence[StageAudit]) -> tuple[str, ...]:
    """Etapas tocadas y con un repuntuado que las pueda arreglar.

    Tocadas son las de umbrales movidos y las del cambio de regla declarado
    que se arregla desde SQLite; un cambio marcado `repeat` no entra aqui por
    mucho repuntuado que tenga la etapa, porque lo que cambio no esta en lo
    guardado.

    Final Tick corto y 6M comparten el flag `--rescore-final-tick-only`, pero
    cada uno repuntua su propia tabla con sus propias fechas y lo que los
    separa es `--final-tick-stage`. El flag se repite a proposito: si solo
    entrara el primero, la tabla 6M se quedaria con las reglas viejas y la
    siguiente auditoria volveria a denunciarla.
    """
    stages: list[str] = []
    for audit in audits:
        spec = stage_spec(audit.stage)
        if not spec.rescore_flag:
            continue
        if audit.findings or _stale_rescorable(audit, spec):
            stages.append(audit.stage)
    return tuple(stages)


def stages_to_repeat(audits: Sequence[StageAudit]) -> tuple[str, ...]:
    """Etapas tocadas que ningun repuntuado arregla: hay que repetirlas.

    Son las dos caras de lo mismo: una etapa sin `rescore_flag` cuyos umbrales
    se movieron, y una cuyo cambio de regla declarado se marco `repeat` porque
    lo que cambio no se puede recalcular desde lo guardado.
    """
    stages: list[str] = []
    for audit in audits:
        spec = stage_spec(audit.stage)
        if audit.findings and not spec.rescore_flag:
            stages.append(audit.stage)
        elif audit.stale_verdicts and spec.rules_change_fix == "repeat":
            stages.append(audit.stage)
    return tuple(stages)
