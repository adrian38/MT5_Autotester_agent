from __future__ import annotations

import argparse
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
    migrate_legacy_account_storage,
    normalize_account_type,
    normalize_broker,
)
from ubs.backup import backup_memory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crea un backup consistente de la memoria UBS SQLite.")
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER)
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE)
    parser.add_argument("--memory", default="", help="Ruta SQLite origen. Si se omite, usa la memoria de la cuenta.")
    parser.add_argument("--backup-dir", default="", help="Carpeta destino. Si se omite, usa outputs/backups.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    broker = normalize_broker(args.broker)
    account = normalize_account_type(args.account_type, broker)
    migrate_legacy_account_storage(BASE_DIR, account, broker)
    source = Path(args.memory).expanduser() if args.memory else None
    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else None
    try:
        destination = backup_memory(BASE_DIR, account, broker=broker, source_path=source, backup_dir=backup_dir)
    except OSError as exc:
        print(f"ERROR: no pude crear backup: {exc}")
        return 1
    print(f"Backup {broker}/{account}: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
