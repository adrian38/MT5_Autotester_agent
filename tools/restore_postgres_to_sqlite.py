from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - optional outside migration tooling
    psycopg = None
    dict_row = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ubs.memory import AgentMemory
from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin


TABLES = (
    "runs",
    "candidates",
    "seed_scores",
    "seed_overrides",
    "candidate_robustness",
    "candidate_final_tick",
    "candidate_final_tick_6m",
    "generation_seed_selection",
    "portfolios",
    "portfolio_allocations",
    "portfolio_decision_log",
    "portfolio_members",
    "portfolio_quarantine",
    "portfolio_versions",
)

EXTRA_COLUMNS = {
    "portfolios": (
        "actual_closed_valley_dd REAL NOT NULL DEFAULT 0",
        "floating_dd_buffer REAL NOT NULL DEFAULT 0",
    ),
    "portfolio_allocations": (
        "max_balance_dd_001 REAL NOT NULL DEFAULT 0",
        "max_equity_dd_001 REAL NOT NULL DEFAULT 0",
        "floating_dd_source TEXT NOT NULL DEFAULT ''",
        "standalone_floating_dd REAL NOT NULL DEFAULT 0",
        "recent_net_profit_001 REAL NOT NULL DEFAULT 0",
        "recent_equity_dd_001 REAL NOT NULL DEFAULT 0",
        "has_recent_performance INTEGER NOT NULL DEFAULT 0",
        "final_tick_report_path TEXT",
        "full_history_report_path TEXT",
    ),
}

PATH_COLUMNS = {
    "source_dir",
    "output_dir",
    "seed_path",
    "set_path",
    "report_path",
    "ohlc_report_path",
    "real_tick_report_path",
    "is_report_path",
    "oos_report_path",
    "final_tick_report_path",
    "full_history_report_path",
}
JSON_COLUMNS = {
    "config_json",
    "metrics_json",
    "ohlc_metrics_json",
    "real_tick_metrics_json",
    "similarity_json",
    "mutation_details_json",
    "snapshot_json",
}
PROJECT_ANCHORS = ("sets", "outputs", "reports", "configs", "assets", "logs", "runtime")


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _add_runtime_columns(conn: sqlite3.Connection) -> None:
    for table, definitions in EXTRA_COLUMNS.items():
        existing = set(_sqlite_columns(conn, table))
        for definition in definitions:
            name = definition.split(None, 1)[0]
            if name not in existing:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def _project_relative(value: str) -> tuple[str, ...] | None:
    normalized = value.replace("/", "\\")
    parts = [part for part in PureWindowsPath(normalized).parts if part not in {"\\", "/"}]
    lowered = [part.lower() for part in parts]
    for anchor in PROJECT_ANCHORS:
        if anchor in lowered:
            return tuple(parts[lowered.index(anchor) :])
    return None


def rewrite_project_path(value: Any, target_root: Path) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    relative = _project_relative(value)
    if relative is None:
        return value
    return str(target_root.joinpath(*relative))


def _rewrite_json_value(value: Any, target_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_json_value(item, target_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_json_value(item, target_root) for item in value]
    if isinstance(value, str):
        return rewrite_project_path(value, target_root)
    return value


def rewrite_json_document(value: Any, target_root: Path) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    return json.dumps(_rewrite_json_value(parsed, target_root), ensure_ascii=False, separators=(",", ":"))


def _mapped_value(
    table: str,
    column: str,
    row: dict[str, Any],
    candidate_sources: dict[int, int],
    target_root: Path,
) -> Any:
    if column == "id" and "source_id" in row:
        value = row.get("source_id")
    elif column == "run_id" and "source_run_id" in row:
        value = row.get("source_run_id")
    elif column == "portfolio_id" and "source_portfolio_id" in row:
        value = row.get("source_portfolio_id")
    elif column == "candidate_id":
        if "source_candidate_id" in row:
            value = row.get("source_candidate_id")
        elif row.get("source_candidate_ref") not in {None, ""}:
            try:
                value = int(row["source_candidate_ref"])
            except (TypeError, ValueError):
                value = candidate_sources.get(int(row["candidate_id"])) if row.get("candidate_id") else None
        else:
            value = candidate_sources.get(int(row["candidate_id"])) if row.get("candidate_id") else None
    elif column == "account_type" and "source_account_type" in row:
        value = row.get("source_account_type") or row.get("account_type")
    else:
        value = row.get(column)

    if column in PATH_COLUMNS:
        return rewrite_project_path(value, target_root)
    if column in JSON_COLUMNS:
        return rewrite_json_document(value, target_root)
    return value


def _insert_rows(
    sqlite_conn: sqlite3.Connection,
    table: str,
    rows: Iterable[dict[str, Any]],
    candidate_sources: dict[int, int],
    target_root: Path,
) -> int:
    local_columns = _sqlite_columns(sqlite_conn, table)
    rows = list(rows)
    if not rows:
        return 0
    central_columns = set(rows[0])
    columns = [
        column
        for column in local_columns
        if column in central_columns
        or (column == "id" and "source_id" in central_columns)
        or (column == "run_id" and "source_run_id" in central_columns)
        or (column == "portfolio_id" and "source_portfolio_id" in central_columns)
        or (column == "candidate_id" and ({"source_candidate_id", "source_candidate_ref", "candidate_id"} & central_columns))
        or (column == "account_type" and "source_account_type" in central_columns)
    ]
    placeholders = ",".join("?" for _ in columns)
    quoted = ",".join(f'"{column}"' for column in columns)
    statement = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
    payload = [
        tuple(_mapped_value(table, column, row, candidate_sources, target_root) for column in columns)
        for row in rows
    ]
    sqlite_conn.executemany(statement, payload)
    return len(payload)


def restore_scope(
    pg: Any,
    *,
    node_id: str,
    broker: str,
    account_type: str,
    target: Path,
    target_root: Path,
    force: bool,
) -> dict[str, int]:
    target = target.resolve()
    target_root = target_root.resolve()
    if target.exists() and not force:
        raise FileExistsError(f"Ya existe {target}; usa --force para reemplazarla")
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="central-sqlite-restore-", dir=target.parent) as temp_dir:
        temporary = Path(temp_dir) / target.name
        memory = AgentMemory(temporary)
        memory.close()
        sqlite_conn: sqlite3.Connection | None = None
        counts: dict[str, int] = {}
        try:
            sqlite_conn = sqlite3.connect(temporary)
            sqlite_conn.row_factory = sqlite3.Row
            sqlite_conn.execute("PRAGMA journal_mode=DELETE")
            sqlite_conn.execute("PRAGMA synchronous=FULL")
            UBSPortfolioLogicMixin()._ensure_portfolio_schema(sqlite_conn)
            _add_runtime_columns(sqlite_conn)

            candidate_sources = {
                int(row["id"]): int(row["source_id"])
                for row in pg.execute(
                    """SELECT id,source_id FROM candidates
                       WHERE node_id=%s AND broker=%s AND account_type=%s""",
                    (node_id, broker, account_type),
                ).fetchall()
            }
            sqlite_conn.execute("BEGIN IMMEDIATE")
            for table in TABLES:
                rows = pg.execute(
                    f'SELECT * FROM "{table}" WHERE node_id=%s AND broker=%s AND account_type=%s',
                    (node_id, broker, account_type),
                ).fetchall()
                counts[table] = _insert_rows(
                    sqlite_conn, table, rows, candidate_sources, target_root
                )
            sqlite_conn.commit()
            sqlite_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            if sqlite_conn is not None:
                sqlite_conn.rollback()
            raise
        finally:
            if sqlite_conn is not None:
                sqlite_conn.close()

        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(target) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        os.replace(temporary, target)
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restaura una memoria SQLite local de compatibilidad desde PostgreSQL central."
    )
    parser.add_argument("--dsn", default=os.getenv("CENTRAL_DATABASE_URL", ""))
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--broker", required=True)
    parser.add_argument("--account-type", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--target-project-dir", required=True)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    if psycopg is None:
        raise SystemExit("Falta psycopg; instala requirements-central-db.txt")
    args = build_parser().parse_args()
    if not args.dsn:
        raise SystemExit("Falta CENTRAL_DATABASE_URL o --dsn")
    broker = args.broker.strip().upper()
    account_type = args.account_type.strip().upper()
    with psycopg.connect(args.dsn, row_factory=dict_row) as pg:
        counts = restore_scope(
            pg,
            node_id=args.node_id,
            broker=broker,
            account_type=account_type,
            target=Path(args.target),
            target_root=Path(args.target_project_dir),
            force=args.force,
        )
    print(
        json.dumps(
            {
                "node_id": args.node_id,
                "broker": broker,
                "account_type": account_type,
                "target": str(Path(args.target).resolve()),
                "rows": counts,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
