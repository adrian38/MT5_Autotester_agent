from __future__ import annotations

import argparse
import configparser
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import BROKER_ACCOUNTS, account_memory_path  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up and rescore UBS robustness rows with generalization-v2 rules."
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        metavar="BROKER:ACCOUNT",
        help="Limit the migration to one broker/account. Repeatable; default=all live DBs.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not create a pre-migration SQLite copy (not recommended).",
    )
    return parser.parse_args()


def selected_targets(raw_targets: list[str]) -> list[tuple[str, str, Path]]:
    requested: set[tuple[str, str]] = set()
    for raw in raw_targets:
        broker, separator, account = str(raw).partition(":")
        if not separator:
            raise ValueError(f"Invalid --target {raw!r}; expected BROKER:ACCOUNT")
        requested.add((broker.strip().upper(), account.strip().upper()))

    targets: list[tuple[str, str, Path]] = []
    for broker, accounts in BROKER_ACCOUNTS.items():
        for account in accounts:
            key = (str(broker).upper(), str(account).upper())
            if requested and key not in requested:
                continue
            memory = account_memory_path(BASE_DIR, account, broker)
            if memory.exists():
                targets.append((key[0], key[1], memory))
    missing = requested - {(broker, account) for broker, account, _ in targets}
    if missing:
        labels = ", ".join(f"{broker}:{account}" for broker, account in sorted(missing))
        raise ValueError(f"No live SQLite DB found for: {labels}")
    return targets


def load_robust_thresholds() -> dict[str, str]:
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(BASE_DIR / "ui_settings.ini", encoding="utf-8")
    general = settings["General"] if settings.has_section("General") else {}
    return {
        "min_net_profit": str(general.get("ubs_robust_pass_min_net_profit", "20")),
        "min_profit_factor": str(general.get("ubs_robust_pass_min_profit_factor", "1.20")),
        "min_trades": str(general.get("ubs_robust_pass_min_trades", "47")),
        "max_drawdown_pct": str(general.get("ubs_robust_pass_max_drawdown_pct", "25")),
        "min_recovery_factor": str(general.get("ubs_robust_pass_min_recovery_factor", "1.0")),
    }


def database_summary(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        statuses = {
            str(status): int(count)
            for status, count in conn.execute(
                "select status, count(1) from candidate_robustness group by status"
            )
        }
        formula_v2 = int(
            conn.execute(
                """
                select count(1)
                from candidate_robustness
                where json_extract(metrics_json, '$.score_formula_version')='2'
                """
            ).fetchone()[0]
        )
        degradation_v2 = int(
            conn.execute(
                """
                select count(1)
                from candidate_robustness
                where json_extract(degradation_json, '$.version')='robustness_degradation_v2'
                """
            ).fetchone()[0]
        )
        integrity = str(conn.execute("pragma integrity_check").fetchone()[0])
        return {
            "statuses": statuses,
            "score_v2": formula_v2,
            "degradation_v2": degradation_v2,
            "integrity": integrity,
        }
    finally:
        conn.close()


def backup_database(path: Path, stamp: str) -> Path:
    backup_dir = BASE_DIR / "outputs" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{path.stem}_pre_generalization_v2_{stamp}{path.suffix}"
    shutil.copy2(path, destination)
    return destination


def rescore_command(broker: str, account: str, thresholds: dict[str, str]) -> list[str]:
    return [
        sys.executable,
        str(BASE_DIR / "ubs_agent.py"),
        "--broker",
        broker,
        "--account-type",
        account,
        "--rescore-robustness-only",
        "--rescore-from-reports",
        "--min-net-profit",
        thresholds["min_net_profit"],
        "--min-profit-factor",
        thresholds["min_profit_factor"],
        "--min-trades",
        thresholds["min_trades"],
        "--max-drawdown-pct",
        thresholds["max_drawdown_pct"],
        "--min-recovery-factor",
        thresholds["min_recovery_factor"],
    ]


def main() -> int:
    args = parse_args()
    try:
        targets = selected_targets(args.target)
    except ValueError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2
    if not targets:
        print("No live UBS memory databases were found.", flush=True)
        return 0

    thresholds = load_robust_thresholds()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("Generalization-v2 migration", flush=True)
    print(f"Targets: {', '.join(f'{b}:{a}' for b, a, _ in targets)}", flush=True)
    print(f"OOS thresholds: {json.dumps(thresholds, sort_keys=True)}", flush=True)

    failures = 0
    for index, (broker, account, memory) in enumerate(targets, start=1):
        label = f"{broker}:{account}"
        print(f"\n=== [{index}/{len(targets)}] {label} ===", flush=True)
        before = database_summary(memory)
        print(f"Before: {json.dumps(before, sort_keys=True)}", flush=True)
        if not args.skip_backup:
            backup = backup_database(memory, stamp)
            print(f"Backup: {backup}", flush=True)
        command = rescore_command(broker, account, thresholds)
        completed = subprocess.run(command, cwd=BASE_DIR, check=False)
        if completed.returncode != 0:
            failures += 1
            print(f"ERROR: {label} exited with code {completed.returncode}", flush=True)
            continue
        after = database_summary(memory)
        print(f"After:  {json.dumps(after, sort_keys=True)}", flush=True)

    if failures:
        print(f"\nMigration finished with {failures} failed target(s).", flush=True)
        return 1
    print("\nMigration completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
