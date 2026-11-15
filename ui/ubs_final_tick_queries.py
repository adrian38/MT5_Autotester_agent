"""Consultas y etiquetas por etapa de las pantallas Final Tick.

Lo unico que separa al tramo corto del 6M es de que tabla leen y a que
candidatos llaman elegibles. Aqui estan esas dos diferencias, y solo esas.
"""
from __future__ import annotations


FINAL_TICK_STAGE_TABLES = {"": "candidate_final_tick", "_6m": "candidate_final_tick_6m"}

FINAL_TICK_PROBE_JOIN = (
    "join candidate_final_tick probe on probe.candidate_id = c.id "
    "and probe.status in ('accepted', 'pending_ohlc_trades')"
)

FINAL_TICK_RUN_OPTION_SPECS = {
    "": {
        "total": "count(c.id)",
        "probe_join": "",
        "eligible": "c.status='accepted' and cr.status='accepted'",
        "where": "",
        "label": "#{run_id} | {created} | cand {total} | robust {eligible} | FT {done} OK {ok} FAIL {fail}",
    },
    "_6m": {
        "total": "count(distinct c.id)",
        "probe_join": "left join candidate_final_tick probe on probe.candidate_id = c.id",
        "eligible": "probe.status in ('accepted', 'pending_ohlc_trades')",
        "where": "where c.id is null or (c.status='accepted' and cr.status='accepted')",
        "label": "#{run_id} | {created} | cand {total} | corto eleg {eligible} | 6M {done} OK {ok} FAIL {fail}",
    },
}

FINAL_TICK_ELIGIBLE_LABELS = {"": "robust accepted", "_6m": "corto elegibles"}

FINAL_TICK_MANUAL_WARNINGS = {
    "": "Si el candidato base tiene score, el peso se actualiza.",
    "_6m": "Esto cambia SQLite, pesos, score de feedback y elegibilidad de UBS Portfolio.",
}


def final_tick_run_options_sql(suffix: str) -> str:
    spec = FINAL_TICK_RUN_OPTION_SPECS[suffix]
    table = FINAL_TICK_STAGE_TABLES[suffix]
    eligible = spec["eligible"]
    return f"""
        select
            r.id, r.created_at, r.hidden,
            {spec['total']} as total,
            sum(case when {eligible} then 1 else 0 end) as eligible,
            sum(case when {eligible} and ft.status in ('accepted', 'rejected') then 1 else 0 end) as done,
            sum(case when {eligible} and ft.status='accepted' then 1 else 0 end) as ok,
            sum(case when {eligible} and ft.status='rejected' then 1 else 0 end) as fail
        from runs r
        left join candidates c on c.run_id = r.id
        left join candidate_robustness cr on cr.candidate_id = c.id
        {spec['probe_join']}
        left join {table} ft on ft.candidate_id = c.id
        {spec['where']}
        group by r.id
        order by r.id desc
    """


def final_tick_run_rows_sql(suffix: str) -> str:
    table = FINAL_TICK_STAGE_TABLES[suffix]
    probe_join = FINAL_TICK_PROBE_JOIN if suffix == "_6m" else ""
    return f"""
        select
            c.id, c.run_id, c.generation, c.target_symbol, c.symbol, c.period,
            c.set_path,
            ft.status as final_status,
            ft.ohlc_report_path,
            ft.real_tick_report_path,
            ft.ohlc_score,
            ft.real_tick_score,
            ft.ohlc_metrics_json,
            ft.real_tick_metrics_json,
            ft.similarity_json,
            ft.history_quality,
            ft.min_history_quality,
            ft.from_date,
            ft.to_date,
            ft.evaluated_at
        from candidates c
        join candidate_robustness cr on cr.candidate_id = c.id
        {probe_join}
        left join {table} ft on ft.candidate_id = c.id
        where c.run_id=? and c.status='accepted' and cr.status='accepted'
        order by
            case
                when ft.status='accepted' then 0
                when ft.status='rejected' then 1
                when ft.status is null then 2
                else 3
            end,
            ft.real_tick_score desc,
            c.id desc
    """
