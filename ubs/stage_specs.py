"""El catalogo de etapas del embudo UBS: que es cada una y donde esta.

`ubs/stage_rules_audit.py` se paso de las 600 lineas cuando cada etapa empezo
a declarar tambien su cambio de regla y su join de elegibilidad. Lo que salio
aqui es el catalogo -los datos-, y alli se quedaron las consultas que lo
recorren. Quien solo necesita saber como se llama una etapa, de que tabla
lee o en que orden va, importa de aqui; `stage_rules_audit` reexporta lo
mismo para quien ya lo pedia alli.

Una etapa se describe entera en su `StageSpec`: su tabla y su clave, las
reglas que guarda y las que aplica sin guardar (`non_auditable`), con que
flag se repuntua, como sella el momento en que juzgo, cuando cambio su regla
y de donde salen las filas que deberia tener.
"""
from __future__ import annotations

from dataclasses import dataclass


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
    # Que es una fila de esta tabla, para poder contar en voz alta: las etapas
    # de semilla puntuan el .set de partida y las demas, el .set generado.
    unit: str = "candidatos"
    run_column: str = ""
    metrics_column: str = ""
    rescore_flag: str = ""
    row_filter: str = ""
    non_auditable: tuple[str, ...] = ()
    # Cuando se juzgo la fila, que no es cuando se creo. Vacio en las etapas
    # que no lo sellan: alli un cambio de regla no se puede repartir fila por
    # fila, y se dice entero en vez de senalar a quien no toca.
    judged_column: str = "evaluated_at"
    # Momento desde el que la regla de la etapa es otra aunque sus umbrales
    # sean los mismos, en el formato ISO que escribe la memoria. Se declara a
    # mano, en el commit que cambia la regla.
    rules_changed_at: str = ""
    rules_change_note: str = ""
    # Como se arregla ese cambio: repuntuando desde SQLite ("rescore") o
    # repitiendo la etapa con MT5 ("repeat"), cuando lo que cambio no esta en
    # lo guardado y hay que volver a producirlo.
    rules_change_fix: str = "rescore"
    # Cuenta de los elegibles que todavia no tienen fila en esta etapa. Es el
    # join con el que su runner elige a quien le toca correr, reducido a contar.
    upstream_sql: str = ""


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

# Cuando las cuatro pantallas Final Tick pasaron a compartir reglas: los
# umbrales no se movieron, asi que la comparacion no encuentra nada, pero el
# codigo que los aplica si cambio y todo veredicto anterior nacio con otro.
# Se arregla repuntuando: lo que cambio es como se juzga lo ya guardado, no lo
# que hace falta guardar.
FINAL_TICK_SHARED_RULES_AT = "2026-11-12T05:17:49"
FINAL_TICK_SHARED_RULES_NOTE = "Las cuatro pantallas Final Tick pasaron a reglas compartidas"

# De donde sale cada etapa: el join con el que su runner elige a quien le toca
# correr, reducido a contar los que aun no tienen fila. La fuente de verdad
# sigue siendo el runner -`ubs/memory_stages.py` para los candidatos,
# `ubs/agent/seeds/final_tick.py` para las semillas-; si alli cambia a quien
# llama elegible, cambia aqui. `{run_filter}` solo entra en las etapas de
# candidato, que son las unicas que cuelgan de un run.
SEED_FINAL_TICK_UPSTREAM = """
    select count(*)
    from seed_scores ss
    left join seed_final_tick ft on ft.seed_id=ss.id
     and ft.seed_mtime=ss.seed_mtime and ft.seed_size=ss.seed_size
    where ss.active=1 and ss.status='accepted' and ft.seed_id is null
"""

SEED_FINAL_TICK_6M_UPSTREAM = """
    select count(*)
    from seed_scores ss
    join seed_final_tick probe on probe.seed_id=ss.id
     and probe.status in ('accepted', 'pending_ohlc_trades')
     and probe.seed_mtime=ss.seed_mtime and probe.seed_size=ss.seed_size
    left join seed_final_tick_6m ft on ft.seed_id=ss.id
     and ft.seed_mtime=ss.seed_mtime and ft.seed_size=ss.seed_size
    where ss.active=1 and ss.status='accepted' and ft.seed_id is null
"""

ROBUSTNESS_UPSTREAM = """
    select count(*)
    from candidates c
    left join candidate_robustness cr on cr.candidate_id=c.id
    where c.status='accepted' and cr.candidate_id is null {run_filter}
"""

FINAL_TICK_UPSTREAM = """
    select count(*)
    from candidates c
    join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
    left join candidate_final_tick ft on ft.candidate_id=c.id
    where c.status='accepted' and ft.candidate_id is null {run_filter}
"""

FINAL_TICK_6M_UPSTREAM = """
    select count(*)
    from candidates c
    join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
    join candidate_final_tick probe on probe.candidate_id=c.id
     and probe.status in ('accepted', 'pending_ohlc_trades')
    left join candidate_final_tick_6m ft on ft.candidate_id=c.id
    where c.status='accepted' and ft.candidate_id is null {run_filter}
"""

REGRESSION_UPSTREAM = """
    select count(*)
    from candidates c
    join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
    join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted'
    left join candidate_regression rg on rg.candidate_id=c.id
    where c.status='accepted' and rg.candidate_id is null {run_filter}
"""

# En el orden del pipeline, que es el de la barra lateral: Seeds, Seeds Final
# Tick, Seeds Final Tick 6M, Resultados, Robustez, Final Tick, Final Tick 6M,
# Regresiva. La auditoria se lee de arriba abajo como se recorre el embudo, y
# los nombres son los de las pantallas para no inventar un segundo vocabulario.
STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        key="seeds",
        label="Seeds",
        table="seed_scores",
        id_column="id",
        rules=SCORE_RULES,
        unit="semillas",
        metrics_column="metrics_json",
        rescore_flag="--rescore-seeds-only",
        row_filter="active=1",
    ),
    StageSpec(
        key="seed_final_tick",
        label="Seeds Final Tick",
        table="seed_final_tick",
        id_column="seed_id",
        rules=FINAL_TICK_RULES,
        unit="semillas",
        non_auditable=FINAL_TICK_BLIND_RULES,
        rules_changed_at=FINAL_TICK_SHARED_RULES_AT,
        rules_change_note=FINAL_TICK_SHARED_RULES_NOTE,
        # Sin `--rescore-*-only` propio: la etapa hay que repetirla entera.
        rules_change_fix="repeat",
        upstream_sql=SEED_FINAL_TICK_UPSTREAM,
    ),
    StageSpec(
        key="seed_final_tick_6m",
        label="Seeds Final Tick 6M",
        table="seed_final_tick_6m",
        id_column="seed_id",
        rules=FINAL_TICK_RULES,
        unit="semillas",
        non_auditable=FINAL_TICK_BLIND_RULES,
        rules_changed_at=FINAL_TICK_SHARED_RULES_AT,
        rules_change_note=FINAL_TICK_SHARED_RULES_NOTE,
        rules_change_fix="repeat",
        upstream_sql=SEED_FINAL_TICK_6M_UPSTREAM,
    ),
    StageSpec(
        key="candidates",
        label="Resultados",
        table="candidates",
        id_column="id",
        rules=SCORE_RULES,
        run_column="run_id",
        metrics_column="metrics_json",
        rescore_flag="--rescore-candidates-only",
        # `created_at` es cuando se genero el .set, no cuando se puntuo, y
        # repuntuar no lo toca: como sello de juicio mentiria en las dos
        # direcciones, asi que esta etapa no tiene.
        judged_column="",
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
        upstream_sql=ROBUSTNESS_UPSTREAM,
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
        rules_changed_at=FINAL_TICK_SHARED_RULES_AT,
        rules_change_note=FINAL_TICK_SHARED_RULES_NOTE,
        upstream_sql=FINAL_TICK_UPSTREAM,
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
        rules_changed_at=FINAL_TICK_SHARED_RULES_AT,
        rules_change_note=FINAL_TICK_SHARED_RULES_NOTE,
        upstream_sql=FINAL_TICK_6M_UPSTREAM,
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
        upstream_sql=REGRESSION_UPSTREAM,
    ),
)

STAGE_SPEC_BY_KEY = {spec.key: spec for spec in STAGE_SPECS}


def stage_spec(key: str) -> StageSpec:
    try:
        return STAGE_SPEC_BY_KEY[str(key)]
    except KeyError:
        raise ValueError(f"Etapa UBS desconocida: {key}") from None
