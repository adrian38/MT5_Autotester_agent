"""El vocabulario del auditor UBS: que es una etapa y que se dice de ella.

`ubs/stage_rules_audit.py` se paso de las 600 lineas cuando cada etapa empezo
a declarar tambien su cambio de regla y su join de elegibilidad. Lo que salio
aqui es el catalogo -los datos-, y alli se quedaron las consultas que lo
recorren. Quien solo necesita saber como se llama una etapa, de que tabla
lee o en que orden va, importa de aqui; `stage_rules_audit` reexporta lo
mismo para quien ya lo pedia alli.

Una etapa se describe entera en su `StageSpec`: su tabla y su clave, las
reglas que guarda y las que aplica sin guardar (`non_auditable`), la ventana
sobre la que corrio, con que flag se repuntua, como sella el momento en que
juzgo, cuando cambio su regla y de donde salen las filas que deberia tener.
Detras van los tres dataclass con los que la auditoria contesta
-`RuleFinding`, `StageAudit`, `PendingAudit`-, que son la otra mitad del mismo
vocabulario y aqui no tocan SQLite: las consultas que los llenan viven en
`stage_rules_audit`.

Regla y configuracion no son lo mismo y este fichero las separa:

- **Regla** es la comparacion que, a partir del resultado, decide accepted o
  rejected: la calidad minima, los minimos de operaciones, las deltas de
  net/PF/DD/trades, la banda de DD. Si cambia, el veredicto viejo nacio con
  otra y hay que volver a juzgarlo, que es lo que hace el repuntuado.
- **Configuracion** es lo demas que la corrida necesita y acaba guardado en la
  misma fila, empezando por la ventana `Desde`/`Hasta`. Moverla no deja mal
  dado ningun veredicto: deja filas corridas sobre otra cosa, y eso solo lo
  arregla volver a correrlas.

Por eso la ventana no va en `rules` sino en `window`, y la auditoria la cuenta
aparte en vez de llamarla regla movida.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleSpec:
    """Una regla de etapa y donde quedo guardado el valor que se aplico.

    Toda regla se arregla repuntuando, porque una regla es una comparacion
    sobre un resultado que ya esta guardado: volver a aplicarla no necesita
    MT5. La etapa que no tiene repuntuado es otra cosa, y eso lo dice su
    `StageSpec`, no la regla.
    """

    key: str
    label: str
    kind: str = "number"
    column: str = ""
    score_field: str = ""


# La ventana se guarda en la fila y se compara igual que una regla, asi que
# reusa la misma forma, pero no es una regla: no se aplica al puntuar. Es el
# periodo sobre el que corrio el backtest, y ningun repuntuado lo mueve -Final
# Tick lo relee del reporte OHLC (`_read_ohlc_report_cfg_dates`), robustez no
# reescribe esas columnas y la regresiva se salta la fila cuya ventana no
# coincide-. Cambiarla en Configuracion no deja ninguna fila mal puntuada: las
# deja corridas sobre otro periodo, y eso solo lo arregla repetir la etapa.
WINDOW_FIELDS: tuple[RuleSpec, ...] = (
    RuleSpec("from_date", "Desde", kind="date", column="from_date"),
    RuleSpec("to_date", "Hasta", kind="date", column="to_date"),
)


@dataclass(frozen=True)
class StageSpec:
    """Una etapa del pipeline: su tabla, sus reglas guardadas y su repuntuado."""

    key: str
    label: str
    table: str
    id_column: str
    rules: tuple[RuleSpec, ...]
    # La ventana que la etapa guarda, cuando guarda alguna. Va aparte de las
    # reglas porque no decide ningun veredicto: ver la cabecera del modulo.
    window: tuple[RuleSpec, ...] = ()
    # Que es una fila de esta tabla, para poder contar en voz alta: las etapas
    # de semilla puntuan el .set de partida y las demas, el .set generado.
    unit: str = "candidatos"
    run_column: str = ""
    metrics_column: str = ""
    rescore_flag: str = ""
    row_filter: str = ""
    # Estados en los que la fila se queda sin que la regla de la etapa haya
    # dicho accepted ni rejected y sin que quede un reporte que hacer: no hubo
    # operaciones, el simbolo ya no existe, el broker tenia el trading cerrado,
    # no hay historico, o el reporte se quedo corto y la etapa lo paso al 6M.
    # Pendiente es aquello a lo que todavia se le puede hacer el reporte, asi
    # que estos no lo son: contarlos como tales ponia a Resultados a medias
    # para siempre. Se declaran a mano porque un estado nuevo tiene que salir
    # a la cara como pendiente, no desaparecer solo.
    #
    # No quiere decir que nadie los toque: hay runners que reencolan alguno
    # -ver `SPECIAL_REJECTION_STATUSES`-, y la prueba de guardia tiene esos
    # casos contados uno a uno.
    settled_statuses: tuple[str, ...] = ()
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
)

# Minimos de operaciones que el Final Tick aplica pero no guarda en su fila:
# sin columna propia, un cambio en ellos no se puede ver desde la memoria.
FINAL_TICK_BLIND_RULES = (
    "Min ops OHLC",
    "W1 min ops",
    "MN min ops",
)

# El Final Tick de sonda que no reunio operaciones suficientes en el OHLC no se
# reintenta nunca -`final_tick_row_pending_for_dates` solo lo reencola en la
# etapa de seis meses-: su candidato sigue el embudo con ese estado, que es lo
# que miran los joins de mas abajo. La etapa no llego a juzgarlo, y volver a
# correrla no cambiaria nada.
PROBE_SETTLED_STATUSES = ("pending_ohlc_trades",)

# Los rechazos especiales, que son los mismos en todo el embudo: el backtest
# corrio -o ni siquiera pudo- y no dejo nada que una regla pueda juzgar. Sin
# operaciones, sin historico del broker, con el trading cerrado en ese simbolo,
# o con el simbolo retirado del universo. Repetirlos no cambia nada porque el
# reporte que falta no se puede producir: el historico que no esta no aparece
# al segundo intento, y una corrida sin operaciones vuelve a no tenerlas.
#
# Hay etapas cuyo runner si los reencola -`ROBUST_RETRYABLE_STATUSES` incluye
# `no_trades`, `REGRESSION_RETRYABLE_STATUSES` incluye `no_history`-. El
# auditor no los cuenta como pendientes igualmente, y esa discrepancia se
# declara y se prueba en `tests/ubs/scoring/test_ubs_stage_settled_statuses.py`
# en vez de quedarse en el aire.
#
# Una etapa que nunca escribe alguno de los cuatro lo declara igual: la familia
# es la misma mire quien la mire, y declararla entera evita que manana aparezca
# en una tabla donde no estaba y se cuele como pendiente.
SPECIAL_REJECTION_STATUSES = (
    "no_trades",
    "no_history",
    "trade_disabled",
    "symbol_not_exist",
)

# Ahora mismo ninguna etapa declara un cambio de regla, y conviene que siga
# asi: solo se declara cuando cambia el codigo que decide accepted/rejected
# sin que cambie ningun umbral guardado, porque entonces la comparacion no
# encuentra nada y aun asi el veredicto nacio con otra regla.
#
# El primero que se declaro fue el commit 62aef74, "Centraliza las reglas de
# las cuatro pantallas Final Tick", y estaba mal: fuera de `ui/` ese commit
# solo anadio `min_history_quality` a un SELECT para pintarlo, y
# `ubs/agent/final_tick/` no se toco. Centralizo las pantallas, no el juicio.
# Costo 480 veredictos marcados en rojo y a punto de repetirse con MT5.
#
# Asi se declara, en el mismo commit que cambia la regla:
#
#     rules_changed_at="2026-11-12T05:17:49",
#     rules_change_note="Que cambio, dicho para la celda de la tabla",
#     rules_change_fix="rescore",  # o "repeat" si hay que volver a correrla
#
# Antes de ponerlo, mira si el commit toca el camino que escribe el veredicto
# -`ubs/agent/...`-, no solo la pantalla que lo ensena.

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
        # `invalid_seed` es el .set que MT5 no acepta: tampoco da reporte.
        settled_statuses=(*SPECIAL_REJECTION_STATUSES, "invalid_seed"),
    ),
    StageSpec(
        key="seed_final_tick",
        label="Seeds Final Tick",
        table="seed_final_tick",
        id_column="seed_id",
        rules=FINAL_TICK_RULES,
        window=WINDOW_FIELDS,
        unit="semillas",
        non_auditable=FINAL_TICK_BLIND_RULES,
        settled_statuses=(*SPECIAL_REJECTION_STATUSES, *PROBE_SETTLED_STATUSES),
        upstream_sql=SEED_FINAL_TICK_UPSTREAM,
    ),
    StageSpec(
        key="seed_final_tick_6m",
        label="Seeds Final Tick 6M",
        table="seed_final_tick_6m",
        id_column="seed_id",
        rules=FINAL_TICK_RULES,
        window=WINDOW_FIELDS,
        unit="semillas",
        non_auditable=FINAL_TICK_BLIND_RULES,
        settled_statuses=SPECIAL_REJECTION_STATUSES,
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
        # Las sondas de historico no son candidatos: son el .set con el que el
        # agente le pregunta al broker si ese simbolo tiene datos, y su estado
        # -`history_ok`, `no_history`- contesta a esa pregunta, no a la regla.
        # Ninguna regla las juzga, asi que no cuentan ni como pendientes ni
        # como tamano de la etapa. Mismo filtro que usa `universe_service`.
        row_filter="ifnull(policy,'')<>'history_probe'",
        settled_statuses=SPECIAL_REJECTION_STATUSES,
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
        ),
        window=WINDOW_FIELDS,
        run_column="run_id",
        metrics_column="metrics_json",
        rescore_flag="--rescore-robustness-only",
        settled_statuses=SPECIAL_REJECTION_STATUSES,
        non_auditable=("Retenciones de degradacion", "Inflacion DD"),
        upstream_sql=ROBUSTNESS_UPSTREAM,
    ),
    StageSpec(
        key="final_tick",
        label="Final Tick",
        table="candidate_final_tick",
        id_column="candidate_id",
        rules=FINAL_TICK_RULES,
        window=WINDOW_FIELDS,
        run_column="run_id",
        rescore_flag="--rescore-final-tick-only",
        non_auditable=FINAL_TICK_BLIND_RULES,
        settled_statuses=(*SPECIAL_REJECTION_STATUSES, *PROBE_SETTLED_STATUSES),
        upstream_sql=FINAL_TICK_UPSTREAM,
    ),
    StageSpec(
        key="final_tick_6m",
        label="Final Tick 6M",
        table="candidate_final_tick_6m",
        id_column="candidate_id",
        rules=FINAL_TICK_RULES,
        window=WINDOW_FIELDS,
        run_column="run_id",
        rescore_flag="--rescore-final-tick-only",
        non_auditable=FINAL_TICK_BLIND_RULES,
        settled_statuses=SPECIAL_REJECTION_STATUSES,
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
        ),
        window=WINDOW_FIELDS,
        run_column="run_id",
        rescore_flag="--rescore-regression-only",
        # El watchdog corto la corrida antes de que hubiera reporte.
        settled_statuses=(*SPECIAL_REJECTION_STATUSES, "watchdog_timeout"),
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
    # Por que no tienen ventana y a cuantas filas alcanza cada motivo: no es lo
    # mismo la que no ha corrido que la que corrio sin producir nada.
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
    """Lo que a una etapa le queda por correr, y nada mas.

    Una fila de la etapa esta en uno de tres sitios: la juzgo su regla
    (accepted o rejected), se quedo sin reporte que hacer
    (`settled_statuses`), o sigue esperando a que alguien la corra. Solo lo
    tercero es un pendiente, que es lo unico que este resumen cuenta.

    `rows` son las filas de las que responde la regla de la etapa: ni las
    paradas ni las que `row_filter` deja fuera por no ser suyas. Contar
    aquellas dentro dejaba a Resultados «a medias» con 9.954 candidatos a los
    que repetir el backtest no les saca un reporte.
    """

    stage: str
    stage_label: str
    rows: int
    pending: int
    by_status: tuple[tuple[str, int], ...] = ()
    ids: tuple[int, ...] = ()
    missing_table: bool = False
    # Las que se quedaron sin veredicto y sin reporte que hacer, por estado.
    # No son un problema que arreglar, pero se dicen una vez para que se vea
    # que la etapa las tenia y la auditoria las aparto a proposito.
    settled: int = 0
    settled_by_status: tuple[tuple[str, int], ...] = ()
    # Elegibles que no llegaron a tener fila: quien acaba de pasar a accepted
    # arriba todavia no ha corrido aqui, y sin fila no cuenta como pendiente.
    missing: int = 0

