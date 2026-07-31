"""Fast re-score of AXI results after a normalization-factor change.

Only the per-symbol net-profit factor changed, and every scored row already stores
its raw metrics in ``metrics_json``. So instead of re-parsing tens of thousands of
HTML reports from disk (hours), we recompute ``normalized_net_profit`` = raw_net *
new_factor and re-derive the pass/fail gates directly from stored metrics (seconds).

Reuses the authoritative ``net_profit_normalization`` (reads the current broker JSON)
and ``_score_formula`` so results match a real re-score exactly; only report parsing
is skipped. Idempotent: safe to re-run.

    py tools/fast_rescore_from_metrics.py --broker AXI --account-type STANDARD
    py tools/fast_rescore_from_metrics.py --broker AXI --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import account_memory_path, normalize_account_type, normalize_broker
from ubs.db import connect_memory  # shared WAL connection helper (busy_timeout)
from ubs.normalization import net_profit_normalization
from ubs.score import _score_formula

# stage -> (min_net, min_pf, min_trades_base, max_dd, min_recovery)
STAGE_GATES = {
    "candidates": (100.0, 1.20, 50, 25.0, 1.0),
    "seeds": (0.0, 1.20, 50, 25.0, 1.0),
    "robustness": (20.0, 1.20, 46, 25.0, 1.0),
}
LONG_TF_MIN_TRADES = {"W1": 11, "MN": 4}


def _min_trades(base: int, timeframe: str) -> int:
    return LONG_TF_MIN_TRADES.get(str(timeframe or "").upper(), base)


def _recompute(m: dict, gates, broker: str) -> tuple[str, int, float, dict]:
    min_net, min_pf, min_tr_base, max_dd, min_rec = gates
    raw = m.get("raw_net_profit", m.get("net_profit"))
    symbol = m.get("symbol", "")
    factor, group, basis = net_profit_normalization(symbol, broker=broker, base_dir=BASE_DIR)
    norm = round(float(raw) * factor, 2)

    pf = float(m.get("profit_factor", 0.0) or 0.0)
    trades = int(m.get("trades", 0) or 0)
    dd = float(m.get("drawdown_pct", 0.0) or 0.0)
    rec = float(m.get("recovery_factor", 0.0) or 0.0)
    pmr = float(m.get("positive_month_ratio", 0.0) or 0.0)
    min_tr = _min_trades(min_tr_base, m.get("timeframe", ""))

    reasons = []
    if norm <= min_net:
        reasons.append("net_profit")
    if pf < min_pf:
        reasons.append("profit_factor")
    if trades < min_tr:
        reasons.append("trades")
    if dd > max_dd:
        reasons.append("drawdown_pct")
    if rec < min_rec:
        reasons.append("recovery_factor")

    score = _score_formula(
        net_profit=norm,
        profit_factor=pf,
        recovery_factor=rec,
        drawdown_pct=dd,
        trades=trades,
        positive_month_ratio=pmr,
        max_month_concentration=float(m.get("max_month_concentration", 0.0) or 0.0),
        sqn=float(m.get("sqn", 0.0) or 0.0),
    )

    m = dict(m)
    m["net_profit_factor"] = round(factor, 4)
    m["normalized_net_profit"] = norm
    m["net_profit_basis"] = basis
    m["normalization_group"] = group
    m["reasons"] = reasons
    m["score"] = round(score, 4)
    status = "accepted" if not reasons else "rejected"
    return status, (1 if not reasons else 0), round(score, 4), m


def _process_table(conn, table: str, key_cols: list[str], gates, broker: str, dry: bool):
    rows = conn.execute(
        f"select {', '.join(key_cols)}, status, metrics_json from {table} "
        f"where status in ('accepted','rejected') and metrics_json is not null and metrics_json != ''"
    ).fetchall()
    to_reject = to_accept = changed = skipped = 0
    updates = []
    for row in rows:
        keys = row[: len(key_cols)]
        old_status = row[len(key_cols)]
        mj = row[len(key_cols) + 1]
        try:
            m = json.loads(mj)
        except Exception:
            skipped += 1
            continue
        if m.get("raw_net_profit", m.get("net_profit")) is None:
            skipped += 1
            continue
        status, accepted, score, new_m = _recompute(m, gates, broker)
        if status != old_status:
            changed += 1
            if status == "accepted":
                to_accept += 1
            else:
                to_reject += 1
        updates.append((status, accepted, score, json.dumps(new_m, ensure_ascii=True, sort_keys=True), *keys))

    if not dry:
        where = " and ".join(f"{k}=?" for k in key_cols)
        conn.executemany(
            f"update {table} set status=?, accepted=?, score=?, metrics_json=? where {where}",
            updates,
        )
        conn.commit()
    print(
        f"[{table}] scored={len(rows)} skipped={skipped} changed={changed} "
        f"(->accepted {to_accept}, ->rejected {to_reject}){' [DRY-RUN]' if dry else ''}"
    )
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fast metrics-based re-score after factor change.")
    ap.add_argument("--broker", default="AXI")
    ap.add_argument("--account-type", default="STANDARD")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    broker = normalize_broker(args.broker)
    account = normalize_account_type(args.account_type, broker)
    db_path = account_memory_path(BASE_DIR, account, broker)
    print(f"DB: {db_path}")
    net_profit_normalization.cache_clear() if hasattr(net_profit_normalization, "cache_clear") else None

    conn = connect_memory(db_path)
    try:
        _process_table(conn, "candidates", ["id"], STAGE_GATES["candidates"], broker, args.dry_run)
        _process_table(conn, "seed_scores", ["id"], STAGE_GATES["seeds"], broker, args.dry_run)
        _process_table(conn, "candidate_robustness", ["candidate_id", "run_id"], STAGE_GATES["robustness"], broker, args.dry_run)

        # spot-check a few watch stocks
        print("\nSpot check (symbol from metrics -> status):")
        cur = conn.execute(
            "select status, metrics_json from candidates where status in ('accepted','rejected') "
            "and metrics_json like '%Costco+%' limit 3"
        )
        for status, mj in cur.fetchall():
            m = json.loads(mj)
            if m.get("symbol", "").startswith("Costco"):
                print(f"  Costco+  raw={m.get('raw_net_profit')}  factor={m.get('net_profit_factor')}  "
                      f"norm={m.get('normalized_net_profit')}  -> {status}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
