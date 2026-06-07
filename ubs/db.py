from __future__ import annotations

import sqlite3
from pathlib import Path


DEFAULT_BUSY_TIMEOUT_MS = 10000


def configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    enable_wal: bool = False,
) -> sqlite3.Connection:
    """Apply UBS-safe SQLite defaults to an open connection."""
    conn.execute(f"pragma busy_timeout={int(busy_timeout_ms)}")
    if enable_wal:
        conn.execute("pragma journal_mode=wal")
    return conn


def connect_memory(
    path: str | Path,
    *,
    timeout: float = 10.0,
    row_factory: type[sqlite3.Row] | None = sqlite3.Row,
    enable_wal: bool = True,
) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path), timeout=timeout)
    if row_factory is not None:
        conn.row_factory = row_factory
    configure_sqlite_connection(
        conn,
        busy_timeout_ms=max(1, int(timeout * 1000)),
        enable_wal=enable_wal,
    )
    return conn
