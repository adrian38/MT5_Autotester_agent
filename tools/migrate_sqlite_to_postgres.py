from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:
    import psycopg
    from psycopg import sql
except ImportError:  # pragma: no cover - the driver is optional for unit discovery
    psycopg = None
    sql = None


SCOPE_COLUMNS = ("node_id", "broker", "account_type")

RUN_COLUMNS = (
    "created_at", "source_dir", "output_dir", "generations",
    "variants_per_seed", "max_seeds", "execute_backtests", "dry_run",
    "hidden", "config_json",
)
CANDIDATE_COLUMNS = (
    "generation", "seed_path", "set_path", "symbol", "target_symbol", "period",
    "family", "run_strategy", "mutated_keys", "timeframe_keys",
    "mutation_details_json", "missing_lot_keys", "policy", "report_path", "score",
    "accepted", "metrics_json", "status", "created_at",
)
SEED_SCORE_COLUMNS = (
    "seed_mtime", "seed_size", "symbol", "period", "family", "run_strategy",
    "report_path", "score", "accepted", "metrics_json", "status", "active",
    "last_seen", "evaluated_at",
)
ROBUSTNESS_COLUMNS = (
    "status", "report_path", "score", "accepted", "metrics_json", "from_date",
    "to_date", "positive_bonus", "negative_bonus", "evaluated_at",
)
FINAL_TICK_COLUMNS = (
    "status", "accepted", "ohlc_report_path", "real_tick_report_path", "ohlc_score",
    "real_tick_score", "ohlc_metrics_json", "real_tick_metrics_json",
    "similarity_json", "history_quality", "min_history_quality", "from_date",
    "to_date", "max_net_delta_pct", "max_pf_delta_pct", "max_dd_delta_pct",
    "max_trades_delta_pct", "evaluated_at",
)
SELECTION_COLUMNS = (
    "generation", "rank", "seed_path", "symbol", "period", "family",
    "run_strategy", "selection_score", "asset_weight", "timeframe_weight",
    "diversity", "fitness_probability", "fitness_weight", "fitness_evidence",
    "created_at",
)
PORTFOLIO_COLUMNS = (
    "created_at", "name", "type", "portfolio_type", "num_symbols",
    "account_capital", "capital", "target_valley_dd_pct", "target_point_dd_pct",
    "target_valley_dd", "target_point_dd", "actual_valley_dd", "actual_point_dd",
    "valley_usage_pct", "point_usage_pct", "total_net_profit", "total_lot",
    "total_units", "active_strategies", "target_strategies", "stop_reason",
    "scale_factor", "binding_constraint", "portfolio_scope", "target_month",
    "metrics_json", "actual_closed_valley_dd", "floating_dd_buffer",
)
ALLOCATION_COLUMNS = (
    "variant_key", "variant_label", "set_id", "symbol", "units", "lot",
    "net_profit_contribution", "standalone_valley_dd", "standalone_point_dd",
    "set_path", "timeframe", "lot_size_step", "margin_required", "margin_pct",
    "margin_leverage", "margin_contract_size", "margin_price", "is_report_path",
    "oos_report_path", "max_balance_dd_001", "max_equity_dd_001",
    "floating_dd_source", "standalone_floating_dd", "recent_net_profit_001",
    "recent_equity_dd_001", "has_recent_performance",
    "final_tick_report_path", "full_history_report_path",
)
DECISION_COLUMNS = (
    "step", "action", "set_id", "from_set_id", "to_set_id", "gain",
    "valley_cost", "point_cost", "score", "portfolio_net_profit_after",
    "portfolio_valley_dd_after", "portfolio_point_dd_after", "reason",
)
MEMBER_COLUMNS = (
    "set_path", "symbol", "period", "lot_multiplier", "lot", "lot_size_step",
    "standalone_dd", "quality_score", "combined_net_profit", "is_report_path",
    "oos_report_path", "variant_key", "variant_label",
)

SOURCE_SCHEMAS: dict[str, set[str]] = {
    "runs": {"id", *RUN_COLUMNS},
    "candidates": {"id", "run_id", *CANDIDATE_COLUMNS},
    "seed_scores": {"id", "seed_path", *SEED_SCORE_COLUMNS},
    "seed_overrides": {"seed_path", "symbol", "period", "updated_at"},
    "candidate_robustness": {"candidate_id", "run_id", *ROBUSTNESS_COLUMNS},
    "candidate_final_tick": {"candidate_id", "run_id", *FINAL_TICK_COLUMNS},
    "candidate_final_tick_6m": {"candidate_id", "run_id", *FINAL_TICK_COLUMNS},
    "generation_seed_selection": {"run_id", *SELECTION_COLUMNS},
    "portfolios": {"id", *PORTFOLIO_COLUMNS},
    "portfolio_allocations": {
        "id", "portfolio_id", "candidate_id", *ALLOCATION_COLUMNS,
    },
    "portfolio_decision_log": {"id", "portfolio_id", *DECISION_COLUMNS},
    "portfolio_members": {"id", "portfolio_id", "candidate_id", *MEMBER_COLUMNS},
    "portfolio_quarantine": {
        "id", "account_type", "candidate_id", "set_path", "symbol", "timeframe",
        "reason", "source_portfolio_id", "quarantined_at",
    },
    "portfolio_versions": {
        "id", "portfolio_id", "version_no", "created_at", "reason", "snapshot_json",
    },
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def _rows(conn: sqlite3.Connection, table: str) -> Iterator[sqlite3.Row]:
    if not _table_exists(conn, table):
        return iter(())
    return iter(conn.execute(f'SELECT * FROM "{table}"'))


def _scope(context: Mapping[str, str]) -> dict[str, str]:
    return {column: context[column] for column in SCOPE_COLUMNS}


def _copy(row: sqlite3.Row, columns: Sequence[str]) -> dict[str, Any]:
    available = set(row.keys())
    return {column: row[column] for column in columns if column in available}


def canonical_seed_path(value: object, broker: str, account_type: str) -> str:
    """Make seed identity independent from the checkout drive and legacy layout."""
    original = str(value or "").strip()
    parts = [part for part in original.replace("/", "\\").split("\\") if part]
    lowered = [part.lower() for part in parts]
    try:
        tail = parts[lowered.index("ubs_ready") + 1 :]
    except ValueError:
        return original

    broker = broker.strip().upper()
    account_type = account_type.strip().upper()
    if len(tail) >= 2 and tail[0].upper() == broker and tail[1].upper() == account_type:
        tail = tail[2:]
    elif tail and tail[0].upper() == account_type:
        # Pre-broker layout: sets/ubs_ready/<account>/...
        tail = tail[1:]
    return "\\".join(("sets", "ubs_ready", broker, account_type, *tail))


def validate_source_schema(conn: sqlite3.Connection) -> None:
    """Refuse silent data loss when a node introduces a new table or column."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )
    }
    unknown_tables = sorted(tables - set(SOURCE_SCHEMAS))
    if unknown_tables:
        raise ValueError(
            "La SQLite contiene tablas aun no soportadas por el migrador: "
            + ", ".join(unknown_tables)
        )
    unknown_columns: list[str] = []
    for table in sorted(tables):
        columns = {
            str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
        for column in sorted(columns - SOURCE_SCHEMAS[table]):
            unknown_columns.append(f"{table}.{column}")
    if unknown_columns:
        raise ValueError(
            "La SQLite contiene columnas aun no soportadas por el migrador: "
            + ", ".join(unknown_columns)
        )


def _upsert(
    conn: psycopg.Connection[Any],
    table: str,
    values: Mapping[str, Any],
    conflict_columns: Sequence[str],
    *,
    returning: str | None = "id",
) -> Any:
    columns = tuple(values)
    update_columns = tuple(column for column in columns if column not in conflict_columns)
    statement = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values}) ").format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
        values=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    if update_columns:
        statement += sql.SQL("ON CONFLICT ({conflict}) DO UPDATE SET {updates}").format(
            conflict=sql.SQL(", ").join(map(sql.Identifier, conflict_columns)),
            updates=sql.SQL(", ").join(
                sql.SQL("{column}=EXCLUDED.{column}").format(column=sql.Identifier(column))
                for column in update_columns
            ),
        )
    else:
        statement += sql.SQL("ON CONFLICT ({conflict}) DO NOTHING").format(
            conflict=sql.SQL(", ").join(map(sql.Identifier, conflict_columns))
        )
    if returning:
        statement += sql.SQL(" RETURNING {column}").format(column=sql.Identifier(returning))
    result = conn.execute(statement, tuple(values[column] for column in columns))
    if not returning:
        return None
    row = result.fetchone()
    if row is None:
        raise RuntimeError(f"El upsert de {table} no devolvio {returning}")
    return row[0]


def _resolve_candidate_ref(value: object, candidate_map: Mapping[int, int]) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return candidate_map.get(int(text))
    _, separator, candidate_text = text.rpartition(":")
    if separator and candidate_text.isdigit():
        return candidate_map.get(int(candidate_text))
    return None


def apply_schema_migrations(
    pg: psycopg.Connection[Any], schema_dir: Path
) -> list[int]:
    """Apply numbered SQL files once, including on an existing Docker volume."""
    directory = schema_dir.expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"No existe el directorio de esquema: {directory}")
    files: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
        files.append((int(path.name.split("_", 1)[0]), path))
    if not files:
        raise ValueError(f"No hay migraciones SQL en {directory}")
    exists = pg.execute("select to_regclass('public.schema_versions') is not null").fetchone()[0]
    applied = (
        {int(row[0]) for row in pg.execute("select version from schema_versions")}
        if exists
        else set()
    )
    installed: list[int] = []
    for version, path in files:
        if version in applied:
            continue
        pg.execute(path.read_text(encoding="utf-8"))
        recorded = pg.execute(
            "select 1 from schema_versions where version=%s", (version,)
        ).fetchone()
        if recorded is None:
            raise RuntimeError(f"La migracion {path.name} no registro su version")
        applied.add(version)
        installed.append(version)
    return installed


def sqlite_file_uri(path: Path, query: str) -> str:
    """Build a SQLite URI that also works for Windows UNC paths."""
    posix = path.as_posix()
    if posix.startswith("//"):
        # Keep the URI authority empty; Python's SQLite rejects remote
        # authorities such as file://server/share but accepts //server/share
        # as the path in file:////server/share.
        return f"file:////{posix.lstrip('/')}?{query}"
    return f"file:{posix}?{query}"


@contextmanager
def sqlite_snapshot(source_path: Path) -> Iterator[tuple[Path, str]]:
    """Create a consistent local snapshot, including committed WAL contents."""
    source = source_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"No existe la SQLite de origen: {source}")
    with tempfile.TemporaryDirectory(prefix="ubs-sqlite-migration-") as temp_dir:
        snapshot = Path(temp_dir) / "memory.sqlite"
        wal_path = Path(f"{source}-wal")
        modes = ["mode=ro"]
        # A WAL-mode main file without a current WAL is complete by itself.
        # immutable avoids SQLite trying to create -shm beside a read-only
        # Docker bind mount. Never use it while a WAL exists: it would ignore it.
        if not wal_path.exists():
            modes.append("mode=ro&immutable=1")
        errors: list[str] = []
        copied = False
        for mode in modes:
            for attempt in range(1, 4):
                if snapshot.exists():
                    snapshot.unlink()
                source_conn = sqlite3.connect(
                    sqlite_file_uri(source, mode), uri=True, timeout=30.0
                )
                destination_conn = sqlite3.connect(snapshot)
                try:
                    source_conn.execute("pragma busy_timeout=30000")
                    source_conn.backup(destination_conn)
                    copied = True
                    break
                except sqlite3.OperationalError as exc:
                    errors.append(f"{mode} intento {attempt}: {exc}")
                    time.sleep(0.25 * attempt)
                finally:
                    destination_conn.close()
                    source_conn.close()
            if copied:
                break
        if not copied:
            raise sqlite3.OperationalError(
                "No se pudo crear un snapshot consistente. " + " | ".join(errors)
            )
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        yield snapshot, digest


def migrate_snapshot(
    sqlite_path: Path,
    pg: psycopg.Connection[Any],
    context: Mapping[str, str],
) -> dict[str, int]:
    source = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    scope = _scope(context)
    run_map: dict[int, int] = {}
    candidate_map: dict[int, int] = {}
    portfolio_map: dict[int, int] = {}
    try:
        validate_source_schema(source)
        for row in _rows(source, "runs"):
            source_id = int(row["id"])
            values = scope | {"source_id": source_id} | _copy(row, RUN_COLUMNS)
            run_map[source_id] = int(
                _upsert(pg, "runs", values, (*SCOPE_COLUMNS, "source_id"))
            )
            counts["runs"] = counts.get("runs", 0) + 1

        for row in _rows(source, "candidates"):
            source_id = int(row["id"])
            source_run_id = int(row["run_id"])
            values = (
                scope
                | {"source_id": source_id, "run_id": run_map.get(source_run_id),
                   "source_run_id": source_run_id}
                | _copy(row, CANDIDATE_COLUMNS)
            )
            candidate_map[source_id] = int(
                _upsert(pg, "candidates", values, (*SCOPE_COLUMNS, "source_id"))
            )
            counts["candidates"] = counts.get("candidates", 0) + 1
            if source_run_id not in run_map:
                counts["warnings_orphan_candidates"] = counts.get(
                    "warnings_orphan_candidates", 0
                ) + 1

        for row in _rows(source, "seed_scores"):
            values = scope | {"source_id": row["id"]} | _copy(row, ("seed_path", *SEED_SCORE_COLUMNS))
            values["seed_path"] = canonical_seed_path(
                row["seed_path"], context["broker"], context["account_type"]
            )
            _upsert(pg, "seed_scores", values, (*SCOPE_COLUMNS, "seed_path"))
            counts["seed_scores"] = counts.get("seed_scores", 0) + 1

        for row in _rows(source, "seed_overrides"):
            values = scope | _copy(row, ("seed_path", "symbol", "period", "updated_at"))
            values["seed_path"] = canonical_seed_path(
                row["seed_path"], context["broker"], context["account_type"]
            )
            _upsert(
                pg, "seed_overrides", values, (*SCOPE_COLUMNS, "seed_path"), returning=None
            )
            counts["seed_overrides"] = counts.get("seed_overrides", 0) + 1

        for table, columns in (
            ("candidate_robustness", ROBUSTNESS_COLUMNS),
            ("candidate_final_tick", FINAL_TICK_COLUMNS),
            ("candidate_final_tick_6m", FINAL_TICK_COLUMNS),
        ):
            for row in _rows(source, table):
                source_candidate_id = int(row["candidate_id"])
                source_run_id = int(row["run_id"])
                values = (
                    scope
                    | {"candidate_id": candidate_map.get(source_candidate_id),
                       "source_candidate_id": source_candidate_id,
                       "run_id": run_map.get(source_run_id), "source_run_id": source_run_id}
                    | _copy(row, columns)
                )
                _upsert(pg, table, values, (*SCOPE_COLUMNS, "source_candidate_id"))
                counts[table] = counts.get(table, 0) + 1
                if source_candidate_id not in candidate_map:
                    key = f"warnings_orphan_{table}_candidates"
                    counts[key] = counts.get(key, 0) + 1
                if source_run_id not in run_map:
                    key = f"warnings_orphan_{table}_runs"
                    counts[key] = counts.get(key, 0) + 1

        for row in _rows(source, "generation_seed_selection"):
            source_run_id = int(row["run_id"])
            values = (
                scope
                | {"run_id": run_map.get(source_run_id), "source_run_id": source_run_id}
                | _copy(row, SELECTION_COLUMNS)
            )
            _upsert(
                pg,
                "generation_seed_selection",
                values,
                (*SCOPE_COLUMNS, "source_run_id", "generation", "rank"),
                returning=None,
            )
            counts["generation_seed_selection"] = counts.get("generation_seed_selection", 0) + 1
            if source_run_id not in run_map:
                counts["warnings_orphan_generation_selections"] = counts.get(
                    "warnings_orphan_generation_selections", 0
                ) + 1

        for row in _rows(source, "portfolios"):
            source_id = int(row["id"])
            values = scope | {"source_id": source_id} | _copy(row, PORTFOLIO_COLUMNS)
            portfolio_map[source_id] = int(
                _upsert(pg, "portfolios", values, (*SCOPE_COLUMNS, "source_id"))
            )
            counts["portfolios"] = counts.get("portfolios", 0) + 1

        for row in _rows(source, "portfolio_allocations"):
            source_portfolio_id = int(row["portfolio_id"])
            candidate_ref = row["candidate_id"] if "candidate_id" in row.keys() else ""
            values = (
                scope
                | {"source_id": int(row["id"]),
                   "portfolio_id": portfolio_map.get(source_portfolio_id),
                   "source_portfolio_id": source_portfolio_id,
                   "source_candidate_ref": str(candidate_ref or ""),
                   "candidate_id": _resolve_candidate_ref(candidate_ref, candidate_map)}
                | _copy(row, ALLOCATION_COLUMNS)
            )
            _upsert(pg, "portfolio_allocations", values, (*SCOPE_COLUMNS, "source_id"))
            counts["portfolio_allocations"] = counts.get("portfolio_allocations", 0) + 1
            if source_portfolio_id not in portfolio_map:
                counts["warnings_orphan_portfolio_allocations"] = counts.get(
                    "warnings_orphan_portfolio_allocations", 0
                ) + 1

        for row in _rows(source, "portfolio_decision_log"):
            source_portfolio_id = int(row["portfolio_id"])
            values = (
                scope
                | {"source_id": int(row["id"]),
                   "portfolio_id": portfolio_map.get(source_portfolio_id),
                   "source_portfolio_id": source_portfolio_id}
                | _copy(row, DECISION_COLUMNS)
            )
            _upsert(pg, "portfolio_decision_log", values, (*SCOPE_COLUMNS, "source_id"))
            counts["portfolio_decision_log"] = counts.get("portfolio_decision_log", 0) + 1
            if source_portfolio_id not in portfolio_map:
                counts["warnings_orphan_portfolio_decisions"] = counts.get(
                    "warnings_orphan_portfolio_decisions", 0
                ) + 1

        for row in _rows(source, "portfolio_members"):
            source_portfolio_id = int(row["portfolio_id"])
            source_candidate_id = row["candidate_id"] if "candidate_id" in row.keys() else None
            candidate_id = (
                candidate_map.get(int(source_candidate_id))
                if source_candidate_id is not None and str(source_candidate_id).isdigit()
                else None
            )
            values = (
                scope
                | {"source_id": int(row["id"]),
                   "portfolio_id": portfolio_map.get(source_portfolio_id),
                   "source_portfolio_id": source_portfolio_id,
                   "source_candidate_id": source_candidate_id, "candidate_id": candidate_id}
                | _copy(row, MEMBER_COLUMNS)
            )
            _upsert(pg, "portfolio_members", values, (*SCOPE_COLUMNS, "source_id"))
            counts["portfolio_members"] = counts.get("portfolio_members", 0) + 1
            if source_portfolio_id not in portfolio_map:
                counts["warnings_orphan_portfolio_members"] = counts.get(
                    "warnings_orphan_portfolio_members", 0
                ) + 1

        for row in _rows(source, "portfolio_quarantine"):
            source_candidate_id = row["candidate_id"] if "candidate_id" in row.keys() else None
            candidate_id = (
                candidate_map.get(int(source_candidate_id))
                if source_candidate_id is not None and str(source_candidate_id).isdigit()
                else None
            )
            values = scope | {
                "source_id": row["id"],
                "source_account_type": row["account_type"],
                "source_candidate_id": source_candidate_id,
                "candidate_id": candidate_id,
            } | _copy(
                row,
                ("set_path", "symbol", "timeframe", "reason", "source_portfolio_id",
                 "quarantined_at"),
            )
            _upsert(pg, "portfolio_quarantine", values, (*SCOPE_COLUMNS, "set_path"))
            counts["portfolio_quarantine"] = counts.get("portfolio_quarantine", 0) + 1

        for row in _rows(source, "portfolio_versions"):
            source_portfolio_id = int(row["portfolio_id"])
            values = (
                scope
                | {"source_id": int(row["id"]),
                   "portfolio_id": portfolio_map.get(source_portfolio_id),
                   "source_portfolio_id": source_portfolio_id}
                | _copy(row, ("version_no", "created_at", "reason", "snapshot_json"))
            )
            _upsert(pg, "portfolio_versions", values, (*SCOPE_COLUMNS, "source_id"))
            counts["portfolio_versions"] = counts.get("portfolio_versions", 0) + 1
            if source_portfolio_id not in portfolio_map:
                counts["warnings_orphan_portfolio_versions"] = counts.get(
                    "warnings_orphan_portfolio_versions", 0
                ) + 1
    finally:
        source.close()
    return counts


def _load_context(args: argparse.Namespace) -> tuple[dict[str, str], Path]:
    config: dict[str, Any] = {}
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
        if config_path.is_file():
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
        elif args.config != "manager_node.json":
            raise FileNotFoundError(f"No existe el archivo de nodo: {config_path}")
    broker = str(args.broker or config.get("broker") or "").strip().upper()
    account_type = str(args.account_type or config.get("account_type") or "").strip().upper()
    node_id = str(args.node_id or config.get("node_id") or "").strip()
    if not broker or not account_type or not node_id:
        raise ValueError("node_id, broker y account_type son obligatorios")
    project_dir = Path(str(config.get("project_dir") or Path.cwd())).expanduser().resolve()
    sqlite_path = (
        Path(args.sqlite_path).expanduser()
        if args.sqlite_path
        else project_dir / "outputs" / f"ubs_memory_{broker}_{account_type}.sqlite"
    )
    if not sqlite_path.is_absolute():
        sqlite_path = project_dir / sqlite_path
    return {
        "node_id": node_id,
        "broker": broker,
        "account_type": account_type,
        "display_name": str(args.display_name or config.get("display_name") or node_id),
    }, sqlite_path.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migra una memoria UBS SQLite a PostgreSQL central sin modificar el origen."
    )
    parser.add_argument("--config", default="manager_node.json")
    parser.add_argument("--sqlite-path")
    parser.add_argument("--node-id")
    parser.add_argument("--broker")
    parser.add_argument("--account-type")
    parser.add_argument("--display-name")
    parser.add_argument("--dsn", default=os.getenv("CENTRAL_DATABASE_URL", ""))
    parser.add_argument(
        "--schema-dir",
        default=str(Path(__file__).resolve().parent.parent / "database" / "init"),
    )
    return parser


def main() -> int:
    if psycopg is None:
        raise SystemExit(
            "Falta psycopg. Usa el servicio Docker migrator o instala el extra [postgres]."
        )
    args = build_parser().parse_args()
    if not args.dsn:
        raise SystemExit("Falta CENTRAL_DATABASE_URL o --dsn")
    context, source_path = _load_context(args)
    with sqlite_snapshot(source_path) as (snapshot_path, digest):
        pg = psycopg.connect(args.dsn)
        try:
            pg.autocommit = True
            apply_schema_migrations(pg, Path(args.schema_dir))
            pg.execute(
                """INSERT INTO nodes(node_id,broker,account_type,display_name)
                   VALUES(%s,%s,%s,%s)
                   ON CONFLICT(node_id) DO UPDATE SET
                     broker=excluded.broker,
                     account_type=excluded.account_type,
                     display_name=excluded.display_name""",
                (
                    context["node_id"], context["broker"], context["account_type"],
                    context["display_name"],
                ),
            )
            batch_id = pg.execute(
                """INSERT INTO ingestion_batches(
                       node_id,broker,account_type,source_path,source_sha256,status
                   ) VALUES(%s,%s,%s,%s,%s,'running') RETURNING id""",
                (
                    context["node_id"], context["broker"], context["account_type"],
                    str(source_path), digest,
                ),
            ).fetchone()[0]
            pg.autocommit = False
            try:
                with pg.transaction():
                    counts = migrate_snapshot(snapshot_path, pg, context)
                    pg.execute(
                        """UPDATE ingestion_batches SET status='completed',
                               row_counts_json=%s::jsonb,finished_at=now() WHERE id=%s""",
                        (json.dumps(counts, sort_keys=True), batch_id),
                    )
                    pg.execute(
                        "UPDATE nodes SET last_ingested_at=now() WHERE node_id=%s",
                        (context["node_id"],),
                    )
            except Exception as exc:
                pg.rollback()
                pg.autocommit = True
                pg.execute(
                    """UPDATE ingestion_batches SET status='failed',error_text=%s,
                           finished_at=now() WHERE id=%s""",
                    (str(exc), batch_id),
                )
                raise
        finally:
            pg.close()
    print(json.dumps({"node": context, "source": str(source_path), "rows": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
