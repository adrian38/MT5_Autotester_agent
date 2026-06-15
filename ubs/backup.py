from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from ubs.account import account_memory_path, normalize_account_type


def backup_memory(
    base_dir: Path,
    account_type: object = "ECN",
    *,
    source_path: Path | None = None,
    backup_dir: Path | None = None,
) -> Path:
    account = normalize_account_type(account_type)
    source = source_path or account_memory_path(base_dir, account)
    if not source.exists():
        raise FileNotFoundError(source)
    destination_dir = backup_dir or (base_dir / "outputs" / "backups")
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / f"ubs_memory_{account}_{timestamp}.sqlite"
    suffix = 1
    while destination.exists():
        destination = destination_dir / f"ubs_memory_{account}_{timestamp}_{suffix}.sqlite"
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
