from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_disabled_symbols_path,
    broker_asset_universe_path_with_fallback,
    account_memory_path,
    migrate_legacy_account_storage,
    normalize_account_type,
    normalize_broker,
)
from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path
from ubs.memory import AgentMemory
from ubs.universe import load_asset_universe, load_disabled_symbols
from tools.ubs_memory_audit_common import (
    Audit,
    format_count_map,
    print_heading,
    scalar,
    table_columns,
    table_exists,
)
from tools.ubs_memory_audit_stages import (
    audit_final_tick,
    audit_regression,
    audit_robustness,
    audit_seeds,
)
DEFAULT_ASSETS = BASE_DIR / "assets" / "roboforex_assets.ini"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audita la memoria UBS SQLite y sus pesos.")
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER, help="Broker UBS a auditar.")
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE, help="Cuenta UBS a auditar.")
    parser.add_argument("--memory", default="", help="Ruta SQLite. Si se omite, usa la memoria de --account-type.")
    parser.add_argument("--assets", default=str(DEFAULT_ASSETS), help="Ruta al universo de activos.")
    parser.add_argument("--top", type=int, default=12, help="Cantidad de pesos top/bottom a mostrar.")
    parser.add_argument("--strict", action="store_true", help="Devuelve codigo 1 si hay avisos.")
    return parser.parse_args()

def run_config_summary(run) -> str:
    try:
        raw = str(run["config_json"] or "").strip()
    except (IndexError, KeyError):
        return "config=legacy"
    if not raw:
        return "config=legacy"
    try:
        config = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "config=invalida"
    generation = config.get("generation", {}) if isinstance(config, dict) else {}
    execution = config.get("execution", {}) if isinstance(config, dict) else {}
    score = config.get("score", {}) if isinstance(config, dict) else {}
    force = bool(generation.get("force_unseeded_universe")) if isinstance(generation, dict) else False
    mode = str(generation.get("mode") or ("discovery" if force else "production")) if isinstance(generation, dict) else "production"
    long_tf = bool(generation.get("experimental_long_timeframes")) if isinstance(generation, dict) else False
    timeframe_universe = generation.get("timeframe_universe", ()) if isinstance(generation, dict) else ()
    long_min_trades = generation.get("long_timeframe_min_trades", {}) if isinstance(generation, dict) else {}
    tf_min_ratios = generation.get("force_unseeded_timeframe_min_ratios", {}) if isinstance(generation, dict) else {}
    final_tick = config.get("final_tick_defaults", {}) if isinstance(config, dict) else {}
    from_date = str(execution.get("from_date") or "") if isinstance(execution, dict) else ""
    to_date = str(execution.get("to_date") or "") if isinstance(execution, dict) else ""
    min_pf = score.get("min_profit_factor") if isinstance(score, dict) else None
    min_trades = score.get("min_trades") if isinstance(score, dict) else None
    caps = generation.get("target_diversity_caps", {}) if isinstance(generation, dict) else {}
    dates = f" fechas={from_date or '-'}..{to_date or '-'}"
    score_text = f" pf>={min_pf} trades>={min_trades}" if min_pf is not None or min_trades is not None else ""
    cap_text = ""
    if isinstance(caps, dict) and caps:
        group_caps = caps.get("group_ratios", caps.get("group_ratio"))
        cap_text = (
            f" cap_group={group_caps}"
            f" cap_sym={caps.get('symbol_ratio')}"
            f" cap_tf={caps.get('timeframe_ratio')}"
            f" cap_pair={caps.get('symbol_timeframe_ratio')}"
        )
    tf_text = ""
    if isinstance(timeframe_universe, list) and timeframe_universe:
        tf_text = f" tf={','.join(str(tf) for tf in timeframe_universe)}"
    long_min_text = ""
    if isinstance(long_min_trades, dict) and long_min_trades:
        long_min_text = f" W1/MN_base={long_min_trades.get('W1')}/{long_min_trades.get('MN')}"
    ft_long_text = ""
    if isinstance(final_tick, dict) and ("min_trades_w1" in final_tick or "min_trades_mn" in final_tick):
        ft_long_text = f" W1/MN_FT={final_tick.get('min_trades_w1')}/{final_tick.get('min_trades_mn')}"
    tf_min_text = ""
    if isinstance(tf_min_ratios, dict) and tf_min_ratios:
        tf_min_text = " tf_min=" + ",".join(f"{key}:{value}" for key, value in sorted(tf_min_ratios.items()))
    return f"mode={mode} force_unseeded={'si' if force else 'no'} long_tf={'si' if long_tf else 'no'}{tf_text}{long_min_text}{ft_long_text}{dates}{score_text}{cap_text}{tf_min_text}"

def audit_runs(conn, audit: Audit) -> None:
    if not table_exists(conn, "runs"):
        audit.warn("No existe tabla runs.")
        return

    run_columns = table_columns(conn, "runs")
    rows = conn.execute("select * from runs order by id").fetchall()
    visible = conn.execute("select * from runs where hidden=0 order by id desc limit 1").fetchone()
    print_heading("Runs")
    print(f"runs totales: {len(rows)}")
    if visible:
        print(f"run visible/latest: #{visible['id']} creado={visible['created_at']}")
        if "config_json" in run_columns:
            print(f"config latest: {run_config_summary(visible)}")
        if table_exists(conn, "generation_seed_selection"):
            selection_columns = table_columns(conn, "generation_seed_selection")
            if {"fitness_probability", "fitness_weight", "fitness_evidence"} <= selection_columns:
                summary = conn.execute(
                    """
                    select count(*) n,
                           avg(fitness_probability) probability,
                           avg(fitness_weight) weight,
                           avg(fitness_evidence) evidence
                    from generation_seed_selection
                    where run_id=?
                    """,
                    (visible["id"],),
                ).fetchone()
                if summary and int(summary["n"] or 0):
                    print(
                        "selection fitness observed latest: "
                        f"n={summary['n']} p6m_avg={float(summary['probability'] or 0.0):.4f} "
                        f"observed_weight_avg={float(summary['weight'] or 0.0):.2f} "
                        f"evidence_avg={float(summary['evidence'] or 0.0):.2f}"
                    )
    for run in rows:
        counts = conn.execute(
            "select status, count(*) n from candidates where run_id=? group by status order by status",
            (run["id"],),
        ).fetchall()
        total = sum(int(row["n"] or 0) for row in counts)
        expected = int(run["generations"] or 0) * int(run["variants_per_seed"] or 0) * int(run["max_seeds"] or 0)
        expected_text = f" esperado_teorico={expected}" if expected else ""
        print(
            f"#{run['id']} hidden={run['hidden']} gens={run['generations']} "
            f"vps={run['variants_per_seed']} max_seeds={run['max_seeds']} "
            f"candidatos={total}{expected_text} | {format_count_map(counts)} | {run_config_summary(run)}"
        )
        generated = scalar(conn, "select count(*) from candidates where run_id=? and status='generated'", (run["id"],))
        if generated:
            audit.warn(f"Run #{run['id']} conserva {generated} candidato(s) en estado generated.")


def audit_candidates(conn, audit: Audit) -> None:
    if not table_exists(conn, "candidates"):
        audit.warn("No existe tabla candidates.")
        return
    print_heading("Candidatos")
    total = scalar(conn, "select count(*) from candidates")
    print(f"total candidatos: {total}")
    for row in conn.execute("select status, count(*) n from candidates group by status order by status"):
        print(f"{row['status']}: {row['n']}")

    scored_missing = scalar(
        conn,
        """
        select count(*)
        from candidates
        where status in ('accepted','rejected')
          and (score is null or metrics_json is null)
        """,
    )
    if scored_missing:
        audit.warn(f"{scored_missing} candidato(s) accepted/rejected no tienen score o metrics_json.")

    problem = scalar(
        conn,
        """
        select count(*)
        from candidates
        where status in ('report_mismatch','no_report','parse_error')
        """,
    )
    print(f"problemas retry/diagnostico: {problem}")

    duplicates = conn.execute(
        """
        select run_id, set_path, count(*) n
        from candidates
        group by run_id, set_path
        having count(*) > 1
        order by n desc
        limit 10
        """
    ).fetchall()
    if duplicates:
        audit.warn(f"Hay {len(duplicates)} set_path duplicado(s) dentro del mismo run.")
        for row in duplicates[:3]:
            print(f"duplicado run #{row['run_id']}: {row['n']}x {row['set_path']}")

    missing_reports = _missing_candidate_reports(conn)
    print(f"reportes de candidatos faltantes en disco: {len(missing_reports)}")
    if missing_reports:
        audit.warn(f"{len(missing_reports)} reporte(s) de candidatos ya puntuados no existen en disco.")


def _missing_candidate_reports(conn) -> list:
    rows = conn.execute(
        """
        select id, status, report_path
        from candidates
        where status in ('accepted','rejected','no_trades')
          and coalesce(report_path, '') != ''
        """
    )
    return [
        row for row in rows
        if not resolve_workspace_path(str(row["report_path"])).exists()
    ]
def audit_weights(memory_path: Path, assets_path: Path, account_type: str, broker: str) -> None:
    print_heading("Pesos")
    disabled = load_disabled_symbols(account_disabled_symbols_path(BASE_DIR, account_type, broker))
    _groups, aliases = load_asset_universe(assets_path, disabled_symbols=disabled)
    memory = AgentMemory(memory_path)
    try:
        asset_feedback = memory.asset_feedback(aliases)
        timeframe_feedback = memory.timeframe_feedback()
        mutation_feedback = memory.mutation_feedback()
    finally:
        memory.close()

    def show(title: str, values: dict[str, float], *, top: int) -> None:
        print(title)
        if not values:
            print("  -")
            return
        ranked = sorted(values.items(), key=lambda item: item[1], reverse=True)
        for key, value in ranked[:top]:
            print(f"  {key}: {value:.2f}")
        if len(ranked) > top:
            print("  ...")
            for key, value in ranked[-min(top, len(ranked)):]:
                print(f"  {key}: {value:.2f}")

    show("activos top/bottom", asset_feedback, top=8)
    show("timeframes top/bottom", timeframe_feedback, top=8)
    show("mutaciones top/bottom", mutation_feedback, top=6)


def audit_json_metrics(conn, audit: Audit) -> None:
    print_heading("Metricas JSON")
    bad: list[str] = []
    for table in ("candidates", "seed_scores", "candidate_robustness", "candidate_regression"):
        if not table_exists(conn, table):
            continue
        id_col = "candidate_id" if table in {"candidate_robustness", "candidate_regression"} else "id"
        for row in conn.execute(
            f"select {id_col} as row_id, metrics_json from {table} where coalesce(metrics_json, '') != ''"
        ):
            try:
                data = json.loads(str(row["metrics_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                bad.append(f"{table}#{row['row_id']}")
                continue
            if not isinstance(data, dict):
                bad.append(f"{table}#{row['row_id']}")
    print(f"metrics_json invalidos: {len(bad)}")
    if bad:
        audit.warn("Hay metrics_json invalidos: " + ", ".join(bad[:8]))


def main() -> int:
    args = parse_args()
    args.broker = normalize_broker(args.broker)
    args.account_type = normalize_account_type(args.account_type, args.broker)
    migrate_legacy_account_storage(BASE_DIR, args.account_type, args.broker)
    memory_path = Path(args.memory).expanduser() if args.memory else account_memory_path(BASE_DIR, args.account_type, args.broker)
    assets_path = Path(args.assets).expanduser()
    if assets_path == DEFAULT_ASSETS:
        assets_path = broker_asset_universe_path_with_fallback(BASE_DIR, args.broker)
    if not memory_path.exists():
        print(f"ERROR: no existe memoria UBS: {memory_path}")
        return 1
    audit = Audit()
    conn = connect_memory(memory_path, enable_wal=True)
    try:
        print(f"Memoria: {memory_path}")
        print(f"SQLite journal_mode: {conn.execute('pragma journal_mode').fetchone()[0]}")
        print(f"SQLite busy_timeout_ms: {conn.execute('pragma busy_timeout').fetchone()[0]}")
        audit_runs(conn, audit)
        audit_candidates(conn, audit)
        audit_seeds(conn, audit)
        audit_robustness(conn, audit)
        audit_final_tick(conn, audit)
        audit_regression(conn, audit)
        audit_json_metrics(conn, audit)
    finally:
        conn.close()

    audit_weights(memory_path, assets_path, args.account_type, args.broker)

    print_heading("Resultado")
    if audit.warnings:
        print(f"avisos: {len(audit.warnings)}")
        for warning in audit.warnings:
            print(f"- {warning}")
    else:
        print("sin avisos")
    return 1 if args.strict and audit.warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
