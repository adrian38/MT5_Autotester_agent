from __future__ import annotations

import shutil
import sqlite3
import json
from datetime import datetime
from pathlib import Path


BROKER_ACCOUNTS = {
    "ROBOFOREX": ("ECN", "PRO"),
    "ICTRADING": ("STANDARD",),
    "AXI": ("STANDARD", "PREMIUM"),
}
BROKERS = tuple(BROKER_ACCOUNTS)
ACCOUNT_TYPES = tuple(dict.fromkeys(account for accounts in BROKER_ACCOUNTS.values() for account in accounts))
BROKER_ACCOUNT_TYPES = tuple(
    (broker, account)
    for broker, accounts in BROKER_ACCOUNTS.items()
    for account in accounts
)
DEFAULT_BROKER = "ROBOFOREX"
DEFAULT_ACCOUNT_TYPE = "ECN"
DEFAULT_TIMEFRAME_UNIVERSE = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


def normalize_broker(value: object) -> str:
    broker = str(value or DEFAULT_BROKER).strip().upper().replace(" ", "")
    aliases = {
        "ROBO": "ROBOFOREX",
        "ROBOFOREX": "ROBOFOREX",
        "IC": "ICTRADING",
        "ICTRADING": "ICTRADING",
        "ICMARKETS": "ICTRADING",
        "AXI": "AXI",
    }
    return aliases.get(broker, DEFAULT_BROKER)


def account_types_for_broker(broker: object) -> tuple[str, ...]:
    return BROKER_ACCOUNTS[normalize_broker(broker)]


def normalize_account_type(value: object, broker: object = DEFAULT_BROKER) -> str:
    accounts = account_types_for_broker(broker)
    account = str(value or DEFAULT_ACCOUNT_TYPE).strip().upper()
    return account if account in accounts else accounts[0]


def account_scope_key(broker: object, account_type: object) -> str:
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    return f"{broker_key}_{account}"


def account_memory_path(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    return base_dir / "outputs" / f"ubs_memory_{account_scope_key(broker, account_type)}.sqlite"


def account_output_dir(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    return base_dir / "outputs" / "ubs_agent" / broker_key / account


def account_seed_dir(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    return base_dir / "sets" / "ubs_ready" / broker_key / account


def account_disabled_symbols_path(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    return broker_disabled_symbols_path(base_dir, broker)


def broker_disabled_symbols_path(base_dir: Path, broker: object = DEFAULT_BROKER) -> Path:
    return base_dir / "outputs" / f"ubs_disabled_symbols_{normalize_broker(broker)}.json"


def broker_asset_universe_path(base_dir: Path, broker: object = DEFAULT_BROKER) -> Path:
    broker_key = normalize_broker(broker).lower()
    return base_dir / "assets" / f"{broker_key}_assets.ini"


def broker_asset_universe_path_with_fallback(base_dir: Path, broker: object = DEFAULT_BROKER) -> Path:
    path = broker_asset_universe_path(base_dir, broker)
    if path.exists():
        return path
    return broker_asset_universe_path(base_dir, DEFAULT_BROKER)


def account_timeframe_universe_path(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> Path:
    return base_dir / "outputs" / f"ubs_timeframes_{account_scope_key(broker, account_type)}.json"


def load_account_timeframe_universe(
    base_dir: Path,
    account_type: object,
    broker: object = DEFAULT_BROKER,
    *,
    include_experimental_long: bool = False,
    default_timeframes: tuple[str, ...] = DEFAULT_TIMEFRAME_UNIVERSE,
    experimental_timeframes: tuple[str, ...] = ("W1", "MN"),
) -> tuple[str, ...]:
    path = account_timeframe_universe_path(base_dir, account_type, broker)
    values: list[object] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            values = list(data.get("timeframes") or [])
        elif isinstance(data, list):
            values = data
    normalized = tuple(
        dict.fromkeys(str(value).strip().upper() for value in values if str(value).strip())
    )
    universe = normalized or tuple(default_timeframes)
    if include_experimental_long:
        universe = tuple(dict.fromkeys((*universe, *experimental_timeframes)))
    return universe


def legacy_account_memory_path(base_dir: Path, account_type: object) -> Path:
    account = str(account_type or DEFAULT_ACCOUNT_TYPE).strip().upper()
    return base_dir / "outputs" / f"ubs_memory_{account}.sqlite"


def legacy_account_output_dir(base_dir: Path, account_type: object) -> Path:
    account = str(account_type or DEFAULT_ACCOUNT_TYPE).strip().upper()
    return base_dir / "outputs" / "ubs_agent" / account


def legacy_account_seed_dir(base_dir: Path, account_type: object) -> Path:
    account = str(account_type or DEFAULT_ACCOUNT_TYPE).strip().upper()
    return base_dir / "sets" / "ubs_ready" / account


def legacy_account_disabled_symbols_path(base_dir: Path, account_type: object) -> Path:
    account = str(account_type or DEFAULT_ACCOUNT_TYPE).strip().upper()
    return base_dir / "outputs" / f"ubs_disabled_symbols_{account}.json"


UBS_DATA_TABLES = (
    "runs",
    "candidates",
    "seed_scores",
    "candidate_robustness",
    "candidate_final_tick",
    "candidate_final_tick_6m",
)

SEED_PATH_COLUMNS = (
    ("seed_scores", "seed_path"),
    ("seed_overrides", "seed_path"),
    ("candidates", "seed_path"),
    ("generation_seed_selection", "seed_path"),
)


def _sqlite_ubs_row_count(path: Path) -> int | None:
    if not path.exists() or path.suffix.lower() not in {".sqlite", ".db"}:
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {
                str(row[0])
                for row in conn.execute("select name from sqlite_master where type='table'")
            }
            total = 0
            for table in UBS_DATA_TABLES:
                if table in tables:
                    total += int(conn.execute(f"select count(*) from {table}").fetchone()[0] or 0)
            return total
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _backup_existing_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.pre_legacy_migration_{timestamp}.bak")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.pre_legacy_migration_{timestamp}_{suffix}.bak")
        suffix += 1
    shutil.copy2(path, backup)
    return backup


def _copy_legacy_file(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return True
    source_rows = _sqlite_ubs_row_count(source)
    destination_rows = _sqlite_ubs_row_count(destination)
    if source_rows and destination_rows == 0:
        _backup_existing_path(destination)
        shutil.copy2(source, destination)
        return True
    return False


def _load_symbol_policy(path: Path) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), set()
    if isinstance(data, dict):
        disabled_values = data.get("disabled") or []
        seed_values = data.get("seed_enabled_when_disabled") or []
    elif isinstance(data, list):
        disabled_values = data
        seed_values = []
    else:
        disabled_values = []
        seed_values = []
    disabled = {str(value).strip().upper() for value in disabled_values if str(value).strip()}
    seed_enabled = {str(value).strip().upper() for value in seed_values if str(value).strip()}
    return disabled, seed_enabled & disabled


def _save_symbol_policy(path: Path, disabled: set[str], seed_enabled: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_disabled = {str(symbol).strip().upper() for symbol in disabled if str(symbol).strip()}
    clean_seed_enabled = {
        str(symbol).strip().upper()
        for symbol in seed_enabled
        if str(symbol).strip().upper() in clean_disabled
    }
    path.write_text(
        json.dumps(
            {
                "disabled": sorted(clean_disabled),
                "seed_enabled_when_disabled": sorted(clean_seed_enabled),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _merge_legacy_symbol_policy(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    source_disabled, source_seed_enabled = _load_symbol_policy(source)
    destination_disabled, destination_seed_enabled = _load_symbol_policy(destination)
    merged_disabled = destination_disabled | source_disabled
    merged_seed_enabled = destination_seed_enabled | source_seed_enabled
    if merged_disabled == destination_disabled and merged_seed_enabled == destination_seed_enabled and destination.exists():
        return False
    _save_symbol_policy(destination, merged_disabled, merged_seed_enabled)
    return True


def _copy_legacy_dir(source: Path, destination: Path) -> bool:
    if not source.exists() or not source.is_dir():
        return False
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        return True
    copied_any = False
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied_any = True
    return copied_any


def _legacy_seed_path_to_broker_path(base_dir: Path, value: object, account_type: str, broker: str) -> str | None:
    text = str(value or "")
    if not text:
        return None
    old_root = legacy_account_seed_dir(base_dir, account_type)
    new_root = account_seed_dir(base_dir, account_type, broker)
    path = Path(text).expanduser()
    try:
        relative = path.resolve().relative_to(old_root.resolve())
        return str(new_root / relative)
    except (OSError, ValueError):
        pass

    old_prefix = old_root.as_posix().lower().rstrip("/") + "/"
    normalized = text.replace("\\", "/")
    if normalized.lower().startswith(old_prefix):
        relative_text = normalized[len(old_prefix):]
        return str(new_root / Path(relative_text))
    return None


def migrate_legacy_seed_paths_in_memory(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> int:
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    if broker_key != DEFAULT_BROKER or account not in {"ECN", "PRO"}:
        return 0
    memory_path = account_memory_path(base_dir, account, broker_key)
    if not memory_path.exists():
        return 0
    changed = 0
    try:
        conn = sqlite3.connect(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                str(row[0])
                for row in conn.execute("select name from sqlite_master where type='table'")
            }
            for table, column in SEED_PATH_COLUMNS:
                if table not in tables:
                    continue
                columns = {
                    str(row["name"])
                    for row in conn.execute(f"pragma table_info({table})")
                }
                if column not in columns:
                    continue
                rowid_column = "rowid"
                for row in conn.execute(f"select rowid as _rowid, {column} from {table}"):
                    new_path = _legacy_seed_path_to_broker_path(base_dir, row[column], account, broker_key)
                    if not new_path or new_path == str(row[column]):
                        continue
                    if table in {"seed_scores", "seed_overrides"}:
                        conflict = conn.execute(
                            f"select 1 from {table} where {column}=? and rowid<>?",
                            (new_path, row["_rowid"]),
                        ).fetchone()
                        if conflict:
                            continue
                    cursor = conn.execute(
                        f"update {table} set {column}=? where {rowid_column}=?",
                        (new_path, row["_rowid"]),
                    )
                    changed += int(cursor.rowcount or 0)
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return changed
    return changed


def migrate_legacy_account_storage(base_dir: Path, account_type: object, broker: object = DEFAULT_BROKER) -> list[str]:
    """Copy legacy ECN/PRO storage into the broker/account layout without deleting originals."""
    broker_key = normalize_broker(broker)
    account = normalize_account_type(account_type, broker_key)
    if broker_key != DEFAULT_BROKER or account not in {"ECN", "PRO"}:
        return []

    copied: list[str] = []
    migrations = (
        ("memory", legacy_account_memory_path(base_dir, account), account_memory_path(base_dir, account, broker_key), _copy_legacy_file),
        ("disabled_symbols", legacy_account_disabled_symbols_path(base_dir, account), broker_disabled_symbols_path(base_dir, broker_key), _merge_legacy_symbol_policy),
        ("seeds", legacy_account_seed_dir(base_dir, account), account_seed_dir(base_dir, account, broker_key), _copy_legacy_dir),
        ("outputs", legacy_account_output_dir(base_dir, account), account_output_dir(base_dir, account, broker_key), _copy_legacy_dir),
    )
    for label, source, destination, copier in migrations:
        if copier(source, destination):
            copied.append(f"{label}: {source} -> {destination}")
    path_updates = migrate_legacy_seed_paths_in_memory(base_dir, account, broker_key)
    if path_updates:
        copied.append(f"seed_paths: {path_updates} row(s) actualizada(s)")
    return copied


def migrate_legacy_roboforex_storage(base_dir: Path) -> list[str]:
    copied: list[str] = []
    for account_type in ("ECN", "PRO"):
        copied.extend(migrate_legacy_account_storage(base_dir, account_type, DEFAULT_BROKER))
    return copied
