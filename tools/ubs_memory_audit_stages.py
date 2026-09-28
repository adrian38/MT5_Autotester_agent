from __future__ import annotations

from ubs.path_utils import resolve_workspace_path, workspace_path_exists
from ubs.weights import (
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    SEED_WEIGHT_SCALE,
)

from tools.ubs_memory_audit_common import Audit, print_heading, scalar, table_exists


def audit_seeds(conn, audit: Audit) -> None:
    if not table_exists(conn, "seed_scores"):
        print_heading("Seeds")
        audit.warn("No existe tabla seed_scores.")
        return
    print_heading("Seeds")
    _audit_seed_counts(conn, audit)
    _audit_seed_files(conn, audit)


def _audit_seed_counts(conn, audit: Audit) -> None:
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
          and status not in ('accepted','rejected','no_trades','report_mismatch','disabled_symbol','invalid_seed','symbol_not_exist')
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

def _audit_seed_files(conn, audit: Audit) -> None:
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


def _print_robustness_by_run(conn) -> None:
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
        return
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


def _print_final_tick_status_counts(conn) -> None:
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

    if not table_exists(conn, "candidate_final_tick_6m"):
        return
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


def audit_robustness(conn, audit: Audit) -> None:
    if not table_exists(conn, "candidate_robustness"):
        print_heading("Robustez")
        audit.warn("No existe tabla candidate_robustness.")
        return
    print_heading("Robustez")
    _print_robustness_by_run(conn)

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
    _print_final_tick_status_counts(conn)

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
