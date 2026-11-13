"""Que filas juzgo cada etapa con reglas distintas a las de ahora.

Las reglas de las etapas se mueven a menudo, y cada fila guarda en SQLite los
umbrales con los que la juzgaron: las cuatro tablas Final Tick guardan sus
desvios maximos y su calidad minima, `candidate_robustness` sus bonus y su
rango, `candidate_regression` sus puntos, y las filas base y de semilla llevan
su `score_config` dentro de `metrics_json`. Comparar lo guardado con lo que hay
hoy en Configuracion dice, sin reparsear un solo reporte HTML, que veredictos
nacieron con otras reglas y que etapa hay que repuntuar.

Lo que una etapa no guarda no se puede auditar asi: cada `StageSpec` declara en
`non_auditable` sus reglas ciegas para que la pantalla lo diga en vez de
callarlo. Aqui no se decide que estado es final ni que estado es reintentable:
lo primero lo pasa quien llama, y lo segundo pertenece al runner de cada etapa.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence


# Tope de identificadores que se devuelven por hallazgo: la pantalla los usa
# para que el usuario reconozca las filas, no para reprocesarlas una por una.
MAX_REPORTED_IDS = 200

# Margen con el que se comparan dos umbrales numericos. SQLite guarda reales y
# la interfaz escribe texto: sin margen, "35" y 35.0 se verian distintos.
NUMBER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class RuleSpec:
    """Una regla de etapa y donde quedo guardado el valor que se aplico."""

    key: str
    label: str
    kind: str = "number"
    column: str = ""
    score_field: str = ""


@dataclass(frozen=True)
class StageSpec:
    """Una etapa del pipeline: su tabla, sus reglas guardadas y su repuntuado."""

    key: str
    label: str
    table: str
    id_column: str
    rules: tuple[RuleSpec, ...]
    run_column: str = ""
    metrics_column: str = ""
    rescore_flag: str = ""
    row_filter: str = ""
    non_auditable: tuple[str, ...] = ()


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
    # Filas tocadas por al menos una regla movida. Se cuenta aparte porque los
    # identificadores de cada hallazgo vienen recortados por `MAX_REPORTED_IDS`.
    affected: int = 0
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


SCORE_RULES: tuple[RuleSpec, ...] = (
    RuleSpec("min_net_profit", "Net minimo", score_field="min_net_profit"),
    RuleSpec("min_profit_factor", "PF minimo", score_field="min_profit_factor"),
    RuleSpec("min_trades", "Operaciones minimas", score_field="min_trades"),
    RuleSpec("max_drawdown_pct", "Drawdown maximo %", score_field="max_drawdown_pct"),
    RuleSpec("min_recovery_factor", "Recovery minimo", score_field="min_recovery_factor"),
)

FINAL_TICK_RULES: tuple[RuleSpec, ...] = (
    RuleSpec("min_history_quality", "Calidad minima", column="min_history_quality"),
    RuleSpec("max_net_delta_pct", "Delta net %", column="max_net_delta_pct"),
    RuleSpec("max_pf_delta_pct", "Delta PF %", column="max_pf_delta_pct"),
    RuleSpec("max_dd_delta_pct", "Delta DD %", column="max_dd_delta_pct"),
    RuleSpec("max_trades_delta_pct", "Delta trades %", column="max_trades_delta_pct"),
    RuleSpec("max_dd_gap_pp", "Banda DD pp", column="max_dd_gap_pp"),
    RuleSpec("from_date", "Desde", kind="date", column="from_date"),
    RuleSpec("to_date", "Hasta", kind="date", column="to_date"),
)

# Minimos de operaciones que el Final Tick aplica pero no guarda en su fila:
# sin columna propia, un cambio en ellos no se puede ver desde la memoria.
FINAL_TICK_BLIND_RULES = (
    "Min ops OHLC",
    "W1 min ops",
    "MN min ops",
)

STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        key="seeds",
        label="Seeds",
        table="seed_scores",
        id_column="id",
        rules=SCORE_RULES,
        metrics_column="metrics_json",
        rescore_flag="--rescore-seeds-only",
        row_filter="active=1",
    ),
    StageSpec(
        key="candidates",
        label="Base",
        table="candidates",
        id_column="id",
        rules=SCORE_RULES,
        run_column="run_id",
        metrics_column="metrics_json",
        rescore_flag="--rescore-candidates-only",
    ),
    StageSpec(
        key="robustness",
        label="Robustez",
        table="candidate_robustness",
        id_column="candidate_id",
        rules=(
            *SCORE_RULES,
            RuleSpec("positive_bonus", "Bonus positivo", column="positive_bonus"),
            RuleSpec("negative_bonus", "Bonus negativo", column="negative_bonus"),
            RuleSpec("from_date", "Desde", kind="date", column="from_date"),
            RuleSpec("to_date", "Hasta", kind="date", column="to_date"),
        ),
        run_column="run_id",
        metrics_column="metrics_json",
        rescore_flag="--rescore-robustness-only",
        non_auditable=("Retenciones de degradacion", "Inflacion DD"),
    ),
    StageSpec(
        key="final_tick",
        label="Final Tick",
        table="candidate_final_tick",
        id_column="candidate_id",
        rules=FINAL_TICK_RULES,
        run_column="run_id",
        rescore_flag="--rescore-final-tick-only",
        non_auditable=FINAL_TICK_BLIND_RULES,
    ),
    StageSpec(
        key="final_tick_6m",
        label="Final Tick 6M",
        table="candidate_final_tick_6m",
        id_column="candidate_id",
        rules=FINAL_TICK_RULES,
        run_column="run_id",
        rescore_flag="--rescore-final-tick-only",
        non_auditable=FINAL_TICK_BLIND_RULES,
    ),
    StageSpec(
        key="seed_final_tick",
        label="Seeds Final Tick",
        table="seed_final_tick",
        id_column="seed_id",
        rules=FINAL_TICK_RULES,
        non_auditable=FINAL_TICK_BLIND_RULES,
    ),
    StageSpec(
        key="seed_final_tick_6m",
        label="Seeds Final Tick 6M",
        table="seed_final_tick_6m",
        id_column="seed_id",
        rules=FINAL_TICK_RULES,
        non_auditable=FINAL_TICK_BLIND_RULES,
    ),
    StageSpec(
        key="regression",
        label="Regresiva",
        table="candidate_regression",
        id_column="candidate_id",
        rules=(
            RuleSpec("positive_points", "Puntos OK", column="positive_points"),
            RuleSpec("negative_points", "Puntos FAIL", column="negative_points"),
            RuleSpec("from_date", "Desde", kind="date", column="from_date"),
            RuleSpec("to_date", "Hasta", kind="date", column="to_date"),
        ),
        run_column="run_id",
        rescore_flag="--rescore-regression-only",
        non_auditable=("Umbrales de aceptacion de la regresiva",),
    ),
)

STAGE_SPEC_BY_KEY = {spec.key: spec for spec in STAGE_SPECS}


def stage_spec(key: str) -> StageSpec:
    try:
        return STAGE_SPEC_BY_KEY[str(key)]
    except KeyError:
        raise ValueError(f"Etapa UBS desconocida: {key}") from None


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
    rows: int = 0
    row_verdicts: int = 0
    unknown: int = 0

    def record(self, rule: RuleSpec, stored: object, row_id: int, *, is_verdict: bool) -> None:
        counts = self.counts.setdefault(rule.key, {})
        label = _display(stored, rule.kind)
        counts[label] = counts.get(label, 0) + 1
        self.ids.setdefault(rule.key, []).append(row_id)
        self.affected.add(row_id)
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
    row_unknown = False
    for rule in spec.rules:
        if rule.key not in expected:
            continue
        stored, present = _stored_rule_value(
            row, rule, columns=columns, score_config=score_config
        )
        if not present:
            # Una fila sin `score_config` no prueba nada: se cuenta aparte en
            # vez de inventarle un valor guardado.
            row_unknown = row_unknown or bool(rule.score_field)
            continue
        if _same_value(stored, expected[rule.key], rule.kind):
            continue
        tally.record(rule, stored, row_id, is_verdict=is_verdict)
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
                findings=_stage_findings(spec, expected, tally),
                non_auditable=spec.non_auditable,
            )
        )
    return audits


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
            )
        )
    return summaries


def stages_to_rescore(audits: Sequence[StageAudit]) -> tuple[str, ...]:
    """Etapas con reglas movidas y con un repuntuado que las pueda arreglar.

    Final Tick corto y 6M comparten modo de repuntuado, asi que el orden de
    `STAGE_SPECS` decide cual se lanza primero y el flag no se repite.
    """
    flags: list[str] = []
    stages: list[str] = []
    for audit in audits:
        if not audit.findings:
            continue
        spec = stage_spec(audit.stage)
        if not spec.rescore_flag or spec.rescore_flag in flags:
            continue
        flags.append(spec.rescore_flag)
        stages.append(spec.key)
    return tuple(stages)
