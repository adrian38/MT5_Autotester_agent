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
from ubs.path_utils import resolve_workspace_path, workspace_path_exists
from ubs.memory import AgentMemory
from ubs.universe import load_asset_universe, load_disabled_symbols
from ubs.weights import (
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    SEED_WEIGHT_SCALE,
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


class Audit:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(conn, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"pragma table_info({table})")}


def scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def format_count_map(rows) -> str:
    if not rows:
        return "-"
    return ", ".join(f"{row['status']}={row['n']}" for row in rows)


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


def print_heading(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


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

    missing_reports = []
    for row in conn.execute(
        """
        select id, status, report_path
        from candidates
        where status in ('accepted','rejected','no_trades')
          and coalesce(report_path, '') != ''
        """
    ):
        path = resolve_workspace_path(str(row["report_path"]))
        if not path.exists():
            missing_reports.append(row)
    print(f"reportes de candidatos faltantes en disco: {len(missing_reports)}")
    if missing_reports:
        audit.warn(f"{len(missing_reports)} reporte(s) de candidatos ya puntuados no existen en disco.")


def audit_seeds(conn, audit: Audit) -> None:
    if not table_exists(conn, "seed_scores"):
        print_heading("Seeds")
        audit.warn("No existe tabla seed_scores.")
        return
    print_heading("Seeds")
    active = scalar(conn, "select count(*) from seed_scores where active=1")
    inactive = scalar(conn, "select count(*) from seed_scores where active=0")
    print(f"activas={active} | obsoletas/inactivas={inactive} | seed_weight_scale={SEED_WEIGHT_SCALE}")
    for row in conn.execute(
        "select status, count(*) n from seed_scores where active=1 group by status order by status"
    ):
        print(f"{row['status']}: {row['n']}")

    valid_scored = scalar(
        conn,
        """
        select count(*)
        from seed_scores
        where active=1
          and status in ('accepted','rejected','no_trades')
          and (score is not null or status='no_trades')
        """,
    )
    print(f"seeds activas que aportan peso: {valid_scored}")

    not_ready = scalar(
        conn,
        """
        select count(*)
        from seed_scores
        where active=1
          and status not in ('accepted','rejected','no_trades','report_mismatch','disabled_symbol','invalid_seed')
        """,
    )
    if not_ready:
        audit.warn(f"{not_ready} seed(s) activas no estan listas/quarentenadas.")

    scored_missing = scalar(
        conn,
        """
        select count(*)
        from seed_scores
        where active=1
          and status in ('accepted','rejected')
          and (score is null or metrics_json is null)
        """,
    )
    if scored_missing:
        audit.warn(f"{scored_missing} seed(s) accepted/rejected no tienen score o metrics_json.")

    changed = []
    missing_seed_files = []
    missing_reports = []
    for row in conn.execute("select * from seed_scores where active=1"):
        seed_path = resolve_workspace_path(str(row["seed_path"]))
        if not seed_path.exists():
            missing_seed_files.append(row)
        else:
            try:
                stat = seed_path.stat()
                if abs(float(row["seed_mtime"] or 0.0) - float(stat.st_mtime)) > 0.001 or int(row["seed_size"] or -1) != int(stat.st_size):
                    changed.append(row)
            except OSError:
                missing_seed_files.append(row)
        report_path = str(row["report_path"] or "").strip()
        if report_path and str(row["status"] or "") in {"accepted", "rejected", "no_trades"} and not workspace_path_exists(report_path):
            missing_reports.append(row)
    print(f"seed files faltantes={len(missing_seed_files)} | cambiadas desde evaluacion={len(changed)} | reportes faltantes={len(missing_reports)}")
    if missing_seed_files:
        audit.warn(f"{len(missing_seed_files)} seed(s) activas apuntan a archivos .set inexistentes.")
    if changed:
        audit.warn(f"{len(changed)} seed(s) activas cambiaron en disco tras su evaluacion.")
    if missing_reports:
        audit.warn(f"{len(missing_reports)} seed report(s) puntuados no existen en disco.")


def audit_robustness(conn, audit: Audit) -> None:
    if not table_exists(conn, "candidate_robustness"):
        print_heading("Robustez")
        audit.warn("No existe tabla candidate_robustness.")
        return
    print_heading("Robustez")
    rows = conn.execute(
        """
        select c.run_id, cr.status, count(*) n
        from candidate_robustness cr
        left join candidates c on c.id=cr.candidate_id
        group by c.run_id, cr.status
        order by c.run_id, cr.status
        """
    ).fetchall()
    if not rows:
        print("sin resultados OOS")
    else:
        current_run = None
        parts: list[str] = []
        for row in rows:
            run_id = row["run_id"]
            if current_run is None:
                current_run = run_id
            if run_id != current_run:
                print(f"run #{current_run}: " + ", ".join(parts))
                current_run = run_id
                parts = []
            parts.append(f"{row['status']}={row['n']}")
        if current_run is not None:
            print(f"run #{current_run}: " + ", ".join(parts))

    pending = conn.execute(
        """
        select c.run_id, count(*) n
        from candidates c
        left join candidate_robustness cr on cr.candidate_id=c.id
        where c.status='accepted' and cr.candidate_id is null
        group by c.run_id
        order by c.run_id
        """
    ).fetchall()
    if pending:
        for row in pending:
            audit.warn(f"Run #{row['run_id']} tiene {row['n']} accepted pendiente(s) de robustez.")

    old_bonus = scalar(
        conn,
        """
        select count(*)
        from candidate_robustness
        where positive_bonus=30.0 or negative_bonus=-30.0
        """,
    )
    print(
        f"bonus default esperado: +{DEFAULT_ROBUST_POSITIVE_BONUS:.0f}/{DEFAULT_ROBUST_NEGATIVE_BONUS:.0f} "
        f"| filas con bonus viejo +30/-30: {old_bonus}"
    )
    if old_bonus:
        audit.warn(f"{old_bonus} fila(s) de robustez conservan bonus viejo +30/-30.")

    orphans = scalar(
        conn,
        """
        select count(*)
        from candidate_robustness cr
        left join candidates c on c.id=cr.candidate_id
        where c.id is null
        """,
    )
    if orphans:
        audit.warn(f"{orphans} fila(s) candidate_robustness no tienen candidato padre.")


def audit_final_tick(conn, audit: Audit) -> None:
    print_heading("Final Tick")
    if not table_exists(conn, "candidate_final_tick"):
        audit.warn("No existe tabla candidate_final_tick.")
        return
    rows = conn.execute(
        """
        select ft.status, count(*) n
        from candidate_final_tick ft
        group by ft.status
        order by n desc, ft.status
        """
    ).fetchall()
    if rows:
        for row in rows:
            print(f"{row['status']}: {row['n']}")
    else:
        print("sin resultados Final Tick")

    if table_exists(conn, "candidate_final_tick_6m"):
        print("Final Tick 6M:")
        rows_6m = conn.execute(
            """
            select status, count(*) n
            from candidate_final_tick_6m
            group by status
            order by n desc, status
            """
        ).fetchall()
        for row in rows_6m:
            print(f"  {row['status']}: {row['n']}")

    pending = conn.execute(
        """
        select ft.status, count(*) n
        from candidate_final_tick ft
        left join candidate_final_tick_6m ft6 on ft6.candidate_id=ft.candidate_id
        where ft.status='pending_history_quality'
           or (ft.status='pending_ohlc_trades' and ft6.candidate_id is null)
        group by ft.status
        order by ft.status
        """
    ).fetchall()
    for row in pending:
        audit.warn(f"Final Tick conserva {row['n']} fila(s) {row['status']} retryable(s).")
    short_ops_handoff = scalar(
        conn,
        """
        select count(*)
        from candidate_final_tick ft
        join candidate_final_tick_6m ft6 on ft6.candidate_id=ft.candidate_id
        where ft.status='pending_ohlc_trades'
        """,
    )
    if short_ops_handoff:
        print(f"probe pending_ohlc_trades resuelto/derivado a 6M: {short_ops_handoff}")

    robust_ready_without_final = scalar(
        conn,
        """
        select count(*)
        from candidates c
        join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
        left join candidate_final_tick ft on ft.candidate_id=c.id
        where c.status='accepted' and ft.candidate_id is null
        """,
    )
    print(f"robust accepted sin Final Tick: {robust_ready_without_final}")
    if robust_ready_without_final:
        audit.warn(f"{robust_ready_without_final} candidato(s) robust accepted no tienen Final Tick.")

    portfolio_eligible = scalar(
        conn,
        """
        select count(*)
        from candidates c
        join candidate_robustness cr on cr.candidate_id=c.id
        join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
        where c.status='accepted'
          and cr.status='accepted'
          and ft6.status='accepted'
        """,
    )
    print(f"elegibles por gate duro base+robust+final_tick_6m: {portfolio_eligible}")


def audit_regression(conn, audit: Audit) -> None:
    print_heading("Prueba regresiva")
    if not table_exists(conn, "candidate_regression"):
        audit.warn("No existe tabla candidate_regression.")
        return
    rows = conn.execute(
        "select status, count(*) n from candidate_regression group by status order by n desc, status"
    ).fetchall()
    if rows:
        for row in rows:
            print(f"{row['status']}: {row['n']}")
    else:
        print("sin resultados regresivos")
    eligible_missing = scalar(
        conn,
        """
        select count(*)
        from candidates c
        join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
        join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted'
        left join candidate_regression rg on rg.candidate_id=c.id
        where c.status='accepted' and rg.candidate_id is null
        """,
    )
    technical = scalar(
        conn,
        """
        select count(*) from candidate_regression
        where status in ('no_report','parse_error','report_mismatch','date_mismatch','no_history')
        """,
    )
    point_total = conn.execute("select coalesce(sum(points_applied),0) from candidate_regression").fetchone()[0]
    print(f"Final Tick 6M accepted sin regresiva: {eligible_missing}")
    print(f"retryables tecnicos neutros: {technical}")
    print(f"puntos aplicados acumulados: {float(point_total or 0.0):+.2f}")
    if technical:
        audit.warn(f"La regresiva conserva {technical} fila(s) tecnica(s) retryable(s).")


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
