"""Esquema de etapas y payloads compartidos por los tests de portafolio del nodo."""
from __future__ import annotations

from dataclasses import asdict

from portfolio_manager.ubs_portfolio import PortfolioResult, StrategyAllocation


CANDIDATE_STAGES = """
create table if not exists candidates(id integer primary key,run_id integer not null default 1,
    set_path text,symbol text,target_symbol text,period text,family text,report_path text,
    status text,score real,accepted integer,metrics_json text,seed_path text,generation integer);
create table if not exists candidate_robustness(candidate_id integer primary key,run_id integer not null default 1,
    status text,report_path text,score real,accepted integer,metrics_json text,
    from_date text not null default '',to_date text not null default '',
    positive_bonus real not null default 70.0,negative_bonus real not null default -70.0,
    evaluated_at text not null default '');
create table if not exists candidate_final_tick(candidate_id integer primary key,run_id integer not null default 1,
    status text,accepted integer,ohlc_report_path text,real_tick_report_path text,
    ohlc_score real,real_tick_score real,ohlc_metrics_json text,real_tick_metrics_json text,
    similarity_json text,history_quality real,min_history_quality real,
    from_date text not null default '',to_date text not null default '',
    max_net_delta_pct real,max_pf_delta_pct real,max_dd_delta_pct real,max_trades_delta_pct real,
    evaluated_at text not null default '');
create table if not exists candidate_final_tick_6m(candidate_id integer primary key,run_id integer not null default 1,
    status text,accepted integer,ohlc_report_path text,real_tick_report_path text,
    ohlc_score real,real_tick_score real,ohlc_metrics_json text,real_tick_metrics_json text,
    similarity_json text,history_quality real,min_history_quality real,
    from_date text not null default '',to_date text not null default '',
    max_net_delta_pct real,max_pf_delta_pct real,max_dd_delta_pct real,max_trades_delta_pct real,
    evaluated_at text not null default '');
insert into candidates(id,run_id,set_path,symbol,status,score) values(1,1,'same.set','EURUSD','accepted',91.5);
insert into candidate_robustness(candidate_id,run_id,status,accepted,score) values(1,1,'accepted',1,88.0);
insert into candidate_final_tick(candidate_id,run_id,status,accepted) values(1,1,'accepted',1);
insert into candidate_final_tick_6m(candidate_id,run_id,status,accepted) values(1,1,'accepted',1);
"""



def portfolio_proposal(key: str, label: str, units: int, request_id: str) -> dict[str, object]:
    inputs = {
        "capital": 5000,
        "valley_dd_pct": 6,
        "point_dd_pct": 6,
        "portfolio_type": key,
        "composition_portfolio_type": "balanced",
        "portfolio_scope": "full_history",
        "_manager_save_request_id": request_id,
    }
    allocation = StrategyAllocation(
        "same.set",
        "ICTRADING/STANDARD:1",
        "EURUSD",
        units,
        units * 0.01,
        100 * units,
        20 * units,
        10 * units,
        "H1",
        "same.set",
        "is.html",
        "oos.html",
        0.01,
    )
    result = PortfolioResult(
        [allocation],
        [0, 100 * units],
        100 * units,
        20 * units,
        10 * units,
        300,
        300,
        10,
        5,
        units * 0.01,
        units,
        1,
        "ok",
        [],
        [],
    )
    return {
        "key": key,
        "label": label,
        "reserve_pct": 10,
        "inputs": inputs,
        "result": asdict(result),
    }


def portfolio_payload(
    request_id: str,
    *,
    operation: str = "generate",
    portfolio_id: int | None = None,
    balanced_units: int = 2,
) -> dict[str, object]:
    return {
        "scope": "full_history",
        "selected_key": "balanced",
        "operation": operation,
        "portfolio_id": portfolio_id,
        "request_id": request_id,
        "proposals": [
            portfolio_proposal("aggressive", "Agresivo", 3, request_id),
            portfolio_proposal("balanced", "Moderado", balanced_units, request_id),
            portfolio_proposal("conservative", "Conservador", 1, request_id),
        ],
    }
