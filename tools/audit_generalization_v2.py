from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import (  # noqa: E402
    BROKERS,
    DEFAULT_BROKER,
    account_memory_path,
    account_scope_key,
    account_types_for_broker,
    normalize_broker,
)
from tools.audit_generalization_output import (  # noqa: E402
    write_changed_csv,
    write_text_report,
)
from tools.audit_generalization_rows import (  # noqa: E402,F401
    DEGRADATION_VERSION,
    FORMULA_VERSION,
    REQUIRED_CHECKS,
    SCORED_STATUSES,
    resolved_report_exists,
)
from tools.audit_generalization_build import (  # noqa: E402,F401
    build_audit,
    connect_read_only,
    load_portfolio_members,
    load_robustness_rows,
    load_stage_statuses,
    status_counts,
    table_columns,
    table_exists,
)


TERMINAL_REGRESSION_STATUSES = {
    "accepted",
    "rejected",
    "no_trades",
    "watchdog_timeout",
    "report_mismatch",
    "parse_error",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audita la migracion de reglas de degradacion v2 de robustez "
            "comparando la memoria actual con un backup pre-v2."
        )
    )
    parser.add_argument(
        "--broker",
        default=DEFAULT_BROKER,
        help=f"Broker a auditar ({', '.join(BROKERS)}); por defecto {DEFAULT_BROKER}.",
    )
    parser.add_argument(
        "--account-type",
        default=None,
        help="Tipo de cuenta del broker; por defecto la primera cuenta configurada.",
    )
    parser.add_argument("--memory", type=Path, help="BD actual; por defecto la memoria del broker/cuenta.")
    parser.add_argument("--before", type=Path, help="Backup anterior a v2; por defecto se descubre automaticamente.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR / "outputs" / "audits",
        help="Directorio para JSON, TXT y CSV.",
    )
    parser.add_argument("--skip-integrity-check", action="store_true")
    return parser.parse_args()


def resolve_scope(raw_broker: object, raw_account: object) -> tuple[str, str]:
    """Resolve BROKER/ACCOUNT, failing loudly instead of silently defaulting."""
    requested = str(raw_broker or DEFAULT_BROKER).strip().upper().replace(" ", "")
    broker = normalize_broker(requested)
    # normalize_broker() falls back to DEFAULT_BROKER for unknown input; only the
    # ROBO/ROBOFOREX aliases legitimately resolve to it.
    if broker == DEFAULT_BROKER and requested not in {"ROBO", DEFAULT_BROKER}:
        raise SystemExit(
            f"Broker desconocido: {requested!r}. Opciones: {', '.join(BROKERS)}"
        )

    accounts = account_types_for_broker(broker)
    if raw_account is None:
        return broker, accounts[0]
    account = str(raw_account).strip().upper()
    if account not in accounts:
        raise SystemExit(
            f"Cuenta {account!r} no valida para {broker}. Opciones: {', '.join(accounts)}"
        )
    return broker, account


def is_pre_v2_database(path: Path) -> bool:
    try:
        conn = connect_read_only(path)
        try:
            columns = table_columns(conn, "candidate_robustness")
            if not columns:
                return False
            if "degradation_json" not in columns:
                return True
            count = conn.execute(
                """
                select count(*) from candidate_robustness
                where json_valid(degradation_json)
                  and json_extract(degradation_json, '$.version')=?
                """,
                (DEGRADATION_VERSION,),
            ).fetchone()[0]
            return int(count or 0) == 0
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def discover_before_database(broker: str, account_type: str) -> Path:
    scope = account_scope_key(broker, account_type)
    pattern = f"ubs_memory_{scope}_pre_generalization_v2_*.sqlite"
    candidates = sorted((BASE_DIR / "outputs" / "backups").glob(pattern), reverse=True)
    for path in candidates:
        if is_pre_v2_database(path):
            return path
    raise FileNotFoundError(f"No se encontro un backup pre-v2 valido con patron {pattern}")


def main() -> int:
    args = parse_args()
    broker, account = resolve_scope(args.broker, args.account_type)
    scope = account_scope_key(broker, account)
    current_path = (args.memory or account_memory_path(BASE_DIR, account, broker)).resolve()
    before_path = (args.before or discover_before_database(broker, account)).resolve()
    if not current_path.exists():
        raise SystemExit(f"No existe la BD actual: {current_path}")
    if not before_path.exists():
        raise SystemExit(f"No existe el backup pre-v2: {before_path}")

    print(f"Auditando {broker} {account}", flush=True)
    print(f"Actual: {current_path}", flush=True)
    print(f"Antes:  {before_path}", flush=True)
    audit = build_audit(
        current_path,
        before_path,
        integrity_check=not args.skip_integrity_check,
        scope=scope,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"generalization_v2_audit_{scope}_{stamp}"
    json_path = output_dir / f"{stem}.json"
    text_path = output_dir / f"{stem}.txt"
    csv_path = output_dir / f"{stem}_transitions.csv"
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    write_text_report(audit, text_path)
    write_changed_csv(audit["changed_candidates"], csv_path)

    newly = audit["newly_accepted"]
    print(f"Veredicto: {audit['verdict']}", flush=True)
    print(f"Transiciones: {audit['transition_counts']}", flush=True)
    print(
        "Nuevos accepted: "
        f"{newly['count']}; con downstream previo={newly['with_prior_any_downstream']}; "
        f"con pipeline previo completo={newly['with_prior_complete_downstream']}; "
        f"sin Final Tick actual={newly['currently_missing_final_tick']}",
        flush=True,
    )
    print(f"Pipeline: {audit['pipeline']}", flush=True)
    print(f"JSON: {json_path}", flush=True)
    print(f"TXT:  {text_path}", flush=True)
    print(f"CSV:  {csv_path}", flush=True)
    return 1 if audit["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
