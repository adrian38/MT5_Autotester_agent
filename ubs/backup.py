from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from ubs.account import DEFAULT_BROKER, account_memory_path, normalize_account_type, normalize_broker


def backup_memory(
    base_dir: Path,
    account_type: object = "ECN",
    *,
    broker: object = DEFAULT_BROKER,
    source_path: Path | None = None,
    backup_dir: Path | None = None,
) -> Path:
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    source = source_path or account_memory_path(base_dir, account, broker_key)
    if not source.exists():
        raise FileNotFoundError(source)
    destination_dir = backup_dir or (base_dir / "outputs" / "backups")
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / f"ubs_memory_{broker_key}_{account}_{timestamp}.sqlite"
    suffix = 1
    while destination.exists():
        destination = destination_dir / f"ubs_memory_{broker_key}_{account}_{timestamp}_{suffix}.sqlite"
        suffix += 1

    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    return destination
