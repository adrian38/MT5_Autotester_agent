from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.db import connect_memory
from ubs.memory import AgentMemory
from ubs.universe import disabled_symbols_path, load_asset_universe, load_disabled_symbols
from ubs.weights import (
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    SEED_WEIGHT_SCALE,
)


DEFAULT_MEMORY = BASE_DIR / "outputs" / "ubs_memory.sqlite"
DEFAULT_ASSETS = BASE_DIR / "assets" / "roboforex_assets.ini"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audita la memoria UBS SQLite y sus pesos.")
    parser.add_argument("--memory", default=str(DEFAULT_MEMORY), help="Ruta a outputs/ubs_memory.sqlite.")
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


def scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def format_count_map(rows) -> str:
    if not rows:
        return "-"
    return ", ".join(f"{row['status']}={row['n']}" for row in rows)


def print_heading(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def audit_runs(conn, audit: Audit) -> None:
    if not table_exists(conn, "runs"):
        audit.warn("No existe tabla runs.")
        return

    rows = conn.execute("select * from runs order by id").fetchall()
    visible = conn.execute("select * from runs where hidden=0 order by id desc limit 1").fetchone()
    print_heading("Runs")
    print(f"runs totales: {len(rows)}")
    if visible:
        print(f"run visible/latest: #{visible['id']} creado={visible['created_at']}")
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
            f"candidatos={total}{expected_text} | {format_count_map(counts)}"
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
        path = Path(str(row["report_path"]))
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
          and status not in ('accepted','rejected','no_trades','report_mismatch','disabled_symbol')
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
        seed_path = Path(str(row["seed_path"]))
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
        if report_path and str(row["status"] or "") in {"accepted", "rejected", "no_trades"} and not Path(report_path).exists():
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


def audit_weights(memory_path: Path, assets_path: Path) -> None:
    print_heading("Pesos")
    disabled = load_disabled_symbols(disabled_symbols_path(BASE_DIR))
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
    for table in ("candidates", "seed_scores", "candidate_robustness"):
        if not table_exists(conn, table):
            continue
        id_col = "candidate_id" if table == "candidate_robustness" else "id"
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
    memory_path = Path(args.memory).expanduser()
    assets_path = Path(args.assets).expanduser()
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
        audit_json_metrics(conn, audit)
    finally:
        conn.close()

    audit_weights(memory_path, assets_path)

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
