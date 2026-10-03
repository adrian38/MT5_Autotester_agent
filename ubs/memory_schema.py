from __future__ import annotations

import json
from datetime import datetime

from ubs.path_utils import resolve_workspace_path
from ubs.tester_diagnostics import TRADE_DISABLED_STATUS, trade_disabled_metadata, execution_failure_metadata


FINAL_TICK_STAGE_TABLES = {
    "probe": "candidate_final_tick",
    "six_month": "candidate_final_tick_6m",
}
FINAL_TICK_6M_PROBE_ELIGIBLE_STATUSES = ("accepted", "pending_ohlc_trades")


def metrics_have_empty_tester_context(metrics_json: object) -> bool:
    """Return whether stored score metrics came from an unusable MT5 report."""
    try:
        metrics = json.loads(str(metrics_json or "{}"))
    except (TypeError, ValueError):
        return False
    symbol = str(metrics.get("symbol") or "").strip()
    timeframe = str(metrics.get("timeframe") or "").strip().upper()
    try:
        trades = int(metrics.get("trades") or 0)
    except (TypeError, ValueError):
        trades = 0
    return trades <= 0 and (not symbol or timeframe in {"", "M0"})


def final_tick_table_for_stage(stage: str | None) -> str:
    key = str(stage or "probe").strip().lower().replace("-", "_")
    if key in {"6m", "sixmonth", "six_month"}:
        key = "six_month"
    if key not in FINAL_TICK_STAGE_TABLES:
        raise ValueError(f"Etapa Final Tick desconocida: {stage}")
    return FINAL_TICK_STAGE_TABLES[key]


SCHEMA_DDL = """
create table if not exists runs (
    id integer primary key autoincrement,
    created_at text not null,
    source_dir text not null,
    output_dir text not null,
    generations integer not null,
    variants_per_seed integer not null,
    max_seeds integer not null,
    execute_backtests integer not null,
    dry_run integer not null,
    hidden integer not null default 0,
    config_json text not null default ''
);
create table if not exists candidates (
    id integer primary key autoincrement,
    run_id integer not null,
    generation integer not null,
    seed_path text not null,
    set_path text not null,
    symbol text not null,
    target_symbol text not null,
    period text not null,
    family text not null,
    run_strategy text not null,
    mutated_keys text not null,
    timeframe_keys text not null default '',
    mutation_details_json text not null default '',
    missing_lot_keys text not null,
    policy text not null,
    report_path text,
    score real,
    accepted integer,
    metrics_json text,
    status text not null,
    created_at text not null
);
create table if not exists seed_scores (
    id integer primary key autoincrement,
    seed_path text not null unique,
    seed_mtime real not null,
    seed_size integer not null,
    symbol text not null,
    period text not null,
    family text not null,
    run_strategy text not null,
    report_path text,
    score real,
    accepted integer,
    metrics_json text,
    status text not null,
    active integer not null default 1,
    last_seen text not null,
    evaluated_at text
);
create table if not exists seed_overrides (
    seed_path text primary key,
    symbol text not null default '',
    period text not null default '',
    updated_at text not null
);
create table if not exists candidate_robustness (
    candidate_id integer primary key,
    run_id integer not null,
    status text not null,
    report_path text,
    score real,
    accepted integer,
    metrics_json text,
    degradation_json text not null default '',
    from_date text not null default '',
    to_date text not null default '',
    positive_bonus real not null default 70.0,
    negative_bonus real not null default -70.0,
    evaluated_at text not null
);
create table if not exists candidate_final_tick (
    candidate_id integer primary key,
    run_id integer not null,
    status text not null,
    accepted integer,
    ohlc_report_path text,
    real_tick_report_path text,
    ohlc_score real,
    real_tick_score real,
    ohlc_metrics_json text,
    real_tick_metrics_json text,
    similarity_json text,
    history_quality real,
    min_history_quality real not null default 80.0,
    from_date text not null default '',
    to_date text not null default '',
    max_net_delta_pct real not null default 35.0,
    max_pf_delta_pct real not null default 35.0,
    max_dd_delta_pct real not null default 35.0,
    max_trades_delta_pct real not null default 35.0,
    evaluated_at text not null
);
create table if not exists candidate_final_tick_6m (
    candidate_id integer primary key,
    run_id integer not null,
    status text not null,
    accepted integer,
    ohlc_report_path text,
    real_tick_report_path text,
    ohlc_score real,
    real_tick_score real,
    ohlc_metrics_json text,
    real_tick_metrics_json text,
    similarity_json text,
    history_quality real,
    min_history_quality real not null default 80.0,
    from_date text not null default '',
    to_date text not null default '',
    max_net_delta_pct real not null default 35.0,
    max_pf_delta_pct real not null default 35.0,
    max_dd_delta_pct real not null default 35.0,
    max_trades_delta_pct real not null default 35.0,
    evaluated_at text not null
);
create table if not exists candidate_regression (
    candidate_id integer primary key,
    run_id integer not null,
    status text not null,
    accepted integer,
    report_path text,
    score real,
    metrics_json text,
    details_json text,
    from_date text not null default '2017.01.01',
    to_date text not null default '2019.12.31',
    positive_points real not null default 80.0,
    negative_points real not null default -100.0,
    points_applied real not null default 0.0,
    evaluated_at text not null
);
create table if not exists generation_seed_selection (
    run_id integer not null,
    generation integer not null,
    rank integer not null,
    seed_path text not null,
    symbol text not null,
    period text not null,
    family text not null,
    run_strategy text not null,
    selection_score real not null,
    asset_weight real not null,
    timeframe_weight real not null,
    diversity real not null,
    created_at text not null,
    primary key (run_id, generation, rank)
);
"""


class MemorySchemaMixin:
    """Creacion del esquema y reclasificacion de estados heredados."""

    def _init(self) -> None:
        self.conn.executescript(
            SCHEMA_DDL
        )
        self._ensure_column("runs", "hidden", "integer not null default 0")
        self._ensure_column("runs", "config_json", "text not null default ''")
        self._ensure_column("candidates", "timeframe_keys", "text not null default ''")
        self._ensure_column("candidates", "mutation_details_json", "text not null default ''")
        self._ensure_column("candidate_robustness", "degradation_json", "text not null default ''")
        self._ensure_column("generation_seed_selection", "fitness_probability", "real not null default 0.0")
        self._ensure_column("generation_seed_selection", "fitness_weight", "real not null default 0.0")
        self._ensure_column("generation_seed_selection", "fitness_evidence", "real not null default 0.0")
        self._detach_history_probe_runs()
        self.conn.execute(
            """
            update seed_scores
            set status='report_mismatch', accepted=null
            where status in ('accepted', 'rejected')
              and (upper(symbol)='UNKNOWN' or upper(period)='UNKNOWN')
            """
        )
        self._reclassify_empty_tester_contexts()
        self._reclassify_legacy_real_tick_no_history()
        self._reclassify_trade_disabled_no_trades()
        self._reclassify_invalid_stops_no_trades()
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            self.conn.execute(f"alter table {table} add column {column} {definition}")

    def _detach_history_probe_runs(self) -> None:
        rows = self.conn.execute("select id, config_json from runs").fetchall()
        probe_run_ids: list[int] = []
        for row in rows:
            try:
                data = json.loads(row["config_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("mode") == "history_probe":
                probe_run_ids.append(int(row["id"]))
        if not probe_run_ids:
            return
        placeholders = ",".join("?" for _ in probe_run_ids)
        self.conn.execute(
            f"update candidates set run_id=0, generation=0 where run_id in ({placeholders})",
            tuple(probe_run_ids),
        )
        self.conn.execute(f"delete from runs where id in ({placeholders})", tuple(probe_run_ids))

    def _reclassify_empty_tester_contexts(self) -> None:
        migrations = (
            (
                "candidates",
                ("no_trades", "report_mismatch", "pending_tester_context"),
                "pending_tester_context",
            ),
            (
                "seed_scores",
                ("no_trades", "report_mismatch", "pending_tester_context"),
                "pending_tester_context",
            ),
        )
        for table, statuses, target_status in migrations:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.conn.execute(
                f"""
                select id, metrics_json
                from {table}
                where status in ({placeholders}) and coalesce(metrics_json, '') != ''
                """,
                statuses,
            ).fetchall()
            ids: list[int] = []
            for row in rows:
                if metrics_have_empty_tester_context(row["metrics_json"]):
                    ids.append(int(row["id"]))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.conn.execute(
                    f"update {table} set status=?, accepted=null where id in ({placeholders})",
                    (target_status, *ids),
                )

    def _reclassify_trade_disabled_no_trades(self) -> int:
        """Migrate the latest run's zero-trade rows with broker-block evidence."""

        migrated = 0
        rows = self.conn.execute(
            """
            select id, report_path, metrics_json
            from candidates
            where status='no_trades'
              and coalesce(report_path, '') != ''
              and run_id=(select max(id) from runs)
            """
        ).fetchall()
        for row in rows:
            metadata = trade_disabled_metadata(resolve_workspace_path(row["report_path"]))
            if metadata is None:
                continue
            try:
                payload = json.loads(str(row["metrics_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update(metadata)
            payload["score"] = None
            payload["accepted"] = False
            self.conn.execute(
                """
                update candidates
                set status=?, score=null, accepted=null, metrics_json=?
                where id=?
                """,
                (
                    TRADE_DISABLED_STATUS,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                    int(row["id"]),
                ),
            )
            migrated += 1
        return migrated

    def _reclassify_invalid_stops_no_trades(self, run_id: int | None = None) -> int:
        """Recover evidence for the latest run and seeds without rerunning MT5.

        A caller may repair a specific older run. Only matching, zero-trade
        metrics and an attributable journal qualify; numeric scores stay intact.
        """
        if run_id is None:
            run_id = self.conn.execute("select max(id) from runs").fetchone()[0]
        migrated = 0
        for table, key in (("candidates", "id"), ("candidate_robustness", "candidate_id"),
                           ("seed_scores", "id")):
            scope = "" if table == "seed_scores" else " and run_id=?"
            params = () if table == "seed_scores" else (run_id,)
            rows = self.conn.execute(
                f"select * from {table} where status='no_trades' "
                f"and coalesce(report_path, '') != ''{scope}", params,
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["metrics_json"] or "{}")
                    if not isinstance(payload, dict) or float(payload.get("trades", -1)) != 0:
                        continue
                    metadata = execution_failure_metadata(
                        resolve_workspace_path(row["report_path"]),
                        payload.get("symbol") or "", payload.get("timeframe") or "",
                    )
                except (TypeError, ValueError):
                    continue
                if not metadata:
                    continue
                payload.update(metadata)
                payload["accepted"] = False
                extra, values = "", []
                if table == "candidate_robustness":
                    try:
                        audit = json.loads(row["degradation_json"] or "{}")
                    except (TypeError, ValueError):
                        audit = {}
                    audit = audit if isinstance(audit, dict) else {}
                    audit.update(metadata)
                    extra = ", degradation_json=?"
                    values.append(json.dumps(audit, ensure_ascii=True, sort_keys=True))
                self.conn.execute(
                    f"update {table} set status='rejected', accepted=0, metrics_json=?{extra} "
                    f"where {key}=? and status='no_trades'",
                    (json.dumps(payload, ensure_ascii=True, sort_keys=True), *values, row[key]),
                )
                migrated += 1
        return migrated

    def _reclassify_legacy_real_tick_table(self, table, migrated_at) -> int:
        """Migra en una tabla los fallos de sincronizacion guardados como veredicto."""
        migrated = 0
        rows = self.conn.execute(
            f"""
            select candidate_id, similarity_json, history_quality, min_history_quality
            from {table}
            where status='rejected'
              and coalesce(similarity_json, '') != ''
            """
        ).fetchall()
        for row in rows:
            try:
                context = json.loads(row["similarity_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(context, dict):
                continue
            reasons = context.get("reasons")
            history = context.get("history")
            if (
                not isinstance(reasons, list)
                or "real_tick_no_history" not in reasons
                or not isinstance(history, dict)
                or history.get("tick_download_failed") is not True
            ):
                continue

            context["accepted"] = False
            context["technical_failure"] = True
            context.setdefault("history_quality", row["history_quality"])
            context.setdefault("min_history_quality", row["min_history_quality"])
            history["retryable"] = True
            history["failure_type"] = "tick_history_sync"
            history["recommendation"] = (
                "reintentar tras estabilizar la conexion MT5; "
                "no asumir ausencia de historico del broker"
            )
            context["status_audit"] = {
                "classification": "transient_tick_sync_failure",
                "migrated_at": migrated_at,
                "migrated_from_status": "rejected",
            }
            self.conn.execute(
                f"""
                update {table}
                set status='pending_history_quality',
                    accepted=0,
                    real_tick_score=null,
                    real_tick_metrics_json=null,
                    similarity_json=?
                where candidate_id=? and status='rejected'
                """,
                (
                    json.dumps(context, ensure_ascii=True, sort_keys=True),
                    int(row["candidate_id"]),
                ),
            )
            migrated += 1
        return migrated

    def _reclassify_legacy_real_tick_no_history(self) -> int:
        """Migrate transient Model=4 sync failures stored as final rejections."""
        migrated = 0
        migrated_at = datetime.now().isoformat(timespec="seconds")
        for table in FINAL_TICK_STAGE_TABLES.values():
            migrated += self._reclassify_legacy_real_tick_table(table, migrated_at)
        return migrated
