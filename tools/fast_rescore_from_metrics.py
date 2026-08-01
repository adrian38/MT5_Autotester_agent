"""Fast re-score of stored results after a normalization-factor change.

Only the per-symbol net-profit factor changed, and every scored row already stores
its raw metrics in ``metrics_json``. So instead of re-parsing tens of thousands of
HTML reports from disk (hours), we recompute ``normalized_net_profit`` = raw_net *
new_factor and re-derive the pass/fail gates directly from stored metrics (seconds).

This is the only re-score path that picks a normalization change up:
``ubs_agent.py --rescore-*-only`` deliberately preserves the stored factor (see
``ubs.score.rescore_result``) and only re-applies the gates.

Two kinds of table:

* Scored stages (candidates, seeds, robustness) — the net gate decides the status,
  so status/accepted/score are recomputed.
* Comparison stages (Final Tick, Final Tick 6M, regression) — the status comes from
  OHLC-vs-tick similarity or from the regression rules, and net profit is not an
  active criterion there. Only the normalization fields inside the metrics blobs
  and the derived score are refreshed; the status is never touched.

Thresholds are read from ``ui_settings.ini`` so this cannot drift from what the UI
and the agent are using.

    py tools/fast_rescore_from_metrics.py --broker AXI --account-type STANDARD --dry-run
    py tools/fast_rescore_from_metrics.py --broker AXI --account-type STANDARD
"""

from __future__ import annotations

import argparse
import configparser
import json
import sys
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import account_memory_path, normalize_account_type, normalize_broker
from ubs.db import connect_memory  # shared WAL connection helper (busy_timeout)
from ubs.normalization import net_profit_normalization
from ubs.score import _score_formula


@dataclass(frozen=True)
class Gates:
    min_net: float
    min_pf: float
    min_trades: int
    max_dd: float
    min_recovery: float
    min_positive_month_ratio: float
    min_trades_w1: int
    min_trades_mn: int

    def trades_for(self, timeframe: str) -> int:
        key = str(timeframe or "").upper()
        if key == "W1":
            return self.min_trades_w1
        if key == "MN":
            return self.min_trades_mn
        return self.min_trades


# Defaults match ScoreConfig / the UI defaults; ui_settings.ini overrides them.
_STAGE_DEFAULTS = {
    "candidates": ("ubs_pass_", 100.0, 1.20, 50, 25.0, 1.0),
    "seeds": ("ubs_seed_pass_", 0.0, 1.20, 50, 25.0, 1.0),
    "robustness": ("ubs_robust_pass_", 20.0, 1.20, 46, 25.0, 1.0),
}


def _settings(base_dir: Path) -> dict[str, str]:
    path = base_dir / "ui_settings.ini"
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8-sig")
    except (OSError, configparser.Error):
        return {}
    values: dict[str, str] = {}
    for section in parser.sections():
        values.update({key: value for key, value in parser[section].items()})
    return values


def _number(values: dict[str, str], key: str, fallback: float) -> float:
    try:
        return float(str(values.get(key, "")).strip().replace(",", "."))
    except (TypeError, ValueError):
        return fallback


def load_gates(base_dir: Path) -> dict[str, Gates]:
    values = _settings(base_dir)
    w1 = int(_number(values, "ubs_long_tf_min_trades_w1", 12))
    mn = int(_number(values, "ubs_long_tf_min_trades_mn", 4))
    gates: dict[str, Gates] = {}
    for stage, (prefix, net, pf, trades, dd, recovery) in _STAGE_DEFAULTS.items():
        gates[stage] = Gates(
            min_net=_number(values, f"{prefix}min_net_profit", net),
            min_pf=_number(values, f"{prefix}min_profit_factor", pf),
            min_trades=int(_number(values, f"{prefix}min_trades", trades)),
            max_dd=_number(values, f"{prefix}max_drawdown_pct", dd),
            min_recovery=_number(values, f"{prefix}min_recovery_factor", recovery),
            # ScoreConfig has this gate too; it defaults to 0.0 (never binds) but
            # copying the threshold instead of assuming it keeps the two in step.
            min_positive_month_ratio=_number(values, f"{prefix}min_positive_month_ratio", 0.0),
            min_trades_w1=w1,
            min_trades_mn=mn,
        )
    return gates


def _renormalize(metrics: dict, broker: str) -> tuple[dict, float, float] | None:
    """Return (metrics with refreshed normalization, normalized net, score)."""
    raw = metrics.get("raw_net_profit", metrics.get("net_profit"))
    symbol = metrics.get("symbol", "")
    if raw is None or not symbol:
        return None
    factor, group, basis = net_profit_normalization(symbol, broker=broker, base_dir=BASE_DIR)
    normalized = round(float(raw) * factor, 2)
    score = _score_formula(
        net_profit=normalized,
        profit_factor=float(metrics.get("profit_factor", 0.0) or 0.0),
        recovery_factor=float(metrics.get("recovery_factor", 0.0) or 0.0),
        drawdown_pct=float(metrics.get("drawdown_pct", 0.0) or 0.0),
        trades=int(metrics.get("trades", 0) or 0),
        positive_month_ratio=float(metrics.get("positive_month_ratio", 0.0) or 0.0),
        max_month_concentration=float(metrics.get("max_month_concentration", 0.0) or 0.0),
        sqn=float(metrics.get("sqn", 0.0) or 0.0),
    )
    updated = dict(metrics)
    updated["net_profit_factor"] = round(factor, 4)
    updated["normalized_net_profit"] = normalized
    updated["net_profit_basis"] = basis
    updated["normalization_group"] = group
    updated["score"] = round(score, 4)
    return updated, normalized, round(score, 4)


def _reasons(metrics: dict, normalized: float, gates: Gates) -> list[str]:
    reasons: list[str] = []
    if normalized <= gates.min_net:
        reasons.append("net_profit")
    if float(metrics.get("profit_factor", 0.0) or 0.0) < gates.min_pf:
        reasons.append("profit_factor")
    if int(metrics.get("trades", 0) or 0) < gates.trades_for(metrics.get("timeframe", "")):
        reasons.append("trades")
    if float(metrics.get("drawdown_pct", 0.0) or 0.0) > gates.max_dd:
        reasons.append("drawdown_pct")
    if float(metrics.get("recovery_factor", 0.0) or 0.0) < gates.min_recovery:
        reasons.append("recovery_factor")
    if float(metrics.get("positive_month_ratio", 0.0) or 0.0) < gates.min_positive_month_ratio:
        reasons.append("positive_month_ratio")
    return reasons


def _dump(metrics: dict) -> str:
    return json.dumps(metrics, ensure_ascii=True, sort_keys=True)


def rescore_stage(conn, table: str, key_cols: list[str], gates: Gates, broker: str, dry: bool) -> None:
    """Re-apply the net gate to a stage whose status depends on it."""
    rows = conn.execute(
        f"select {', '.join(key_cols)}, status, score, metrics_json from {table} "
        f"where status in ('accepted','rejected') and metrics_json is not null and metrics_json != ''"
    ).fetchall()
    updates = []
    changed = to_accept = to_reject = skipped = 0
    for row in rows:
        keys = row[: len(key_cols)]
        old_status = row[len(key_cols)]
        old_score = row[len(key_cols) + 1]
        try:
            metrics = json.loads(row[len(key_cols) + 2])
        except (TypeError, ValueError):
            skipped += 1
            continue
        result = _renormalize(metrics, broker)
        if result is None:
            skipped += 1
            continue
        updated, normalized, score = result
        reasons = _reasons(updated, normalized, gates)
        updated["reasons"] = reasons
        status = "accepted" if not reasons else "rejected"
        if status != old_status:
            changed += 1
            if status == "accepted":
                to_accept += 1
            else:
                to_reject += 1
        # Keep a cleared score cleared: the UI nulls it to take the row out of
        # the universe weights, and re-scoring must not undo that.
        updates.append(
            (status, 1 if not reasons else 0, None if old_score is None else score, _dump(updated), *keys)
        )

    if not dry and updates:
        where = " and ".join(f"{key}=?" for key in key_cols)
        conn.executemany(
            f"update {table} set status=?, accepted=?, score=?, metrics_json=? where {where}",
            updates,
        )
        conn.commit()
    print(
        f"[{table}] scored={len(rows)} skipped={skipped} changed={changed} "
        f"(->accepted {to_accept}, ->rejected {to_reject}){' [DRY-RUN]' if dry else ''}"
    )


def refresh_stage(
    conn,
    table: str,
    key_cols: list[str],
    columns: list[tuple[str, str]],
    broker: str,
    dry: bool,
) -> None:
    """Refresh normalization fields only. Never touches status.

    Final Tick, Final Tick 6M and the backward regression decide acceptance by
    comparing two runs of the same strategy (or by the regression rules), and net
    profit is explicitly not an active criterion there. Leaving their metrics on an
    old factor only makes the UI show a normalized net that no longer matches the
    active normalization, so the fields are refreshed and the verdict is left alone.

    ``accepted``/``reasons`` inside those blobs also keep the agent's original
    verdict: they were produced with the stage's own score config, and the Final
    Tick UI reads its cause from ``similarity_json``, not from here.
    """
    if not columns:
        return
    available = {row[1] for row in conn.execute(f"pragma table_info({table})")}
    # A missing score column must not cost us the metrics refresh.
    columns = [(m, s if s in available else "") for m, s in columns if m in available]
    if not columns:
        print(f"[{table}] (no metrics columns)")
        return
    selected = [name for pair in columns for name in pair if name]
    rows = conn.execute(
        f"select {', '.join(key_cols + selected)} from {table}"
    ).fetchall()
    updates: list[tuple] = []
    refreshed = untouched = 0
    for row in rows:
        keys = list(row[: len(key_cols)])
        values = dict(zip(selected, row[len(key_cols):]))
        assignments: list[str] = []
        params: list[object] = []
        for metrics_col, score_col in columns:
            raw = values.get(metrics_col)
            if not raw:
                continue
            try:
                metrics = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(metrics, dict):
                continue
            result = _renormalize(metrics, broker)
            if result is None:
                continue
            updated, _normalized, score = result
            if _dump(updated) == _dump(metrics):
                continue
            assignments.append(f"{metrics_col}=?")
            params.append(_dump(updated))
            # A NULL score is a state, not a missing value: the UI clears it to
            # drop a row out of the universe weights, and rows that were never
            # scored (no_history, history_ok) carry NULL by construction. Only
            # refresh a score that is already there.
            if score_col and values.get(score_col) is not None:
                assignments.append(f"{score_col}=?")
                params.append(score)
        if assignments:
            refreshed += 1
            where = " and ".join(f"{key}=?" for key in key_cols)
            updates.append((f"update {table} set {', '.join(assignments)} where {where}", params + keys))
        else:
            untouched += 1

    if not dry:
        for statement, params in updates:
            conn.execute(statement, params)
        conn.commit()
    print(
        f"[{table}] rows={len(rows)} normalization_refreshed={refreshed} already_current={untouched} "
        f"(status untouched){' [DRY-RUN]' if dry else ''}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast metrics-based re-score after a factor change.")
    parser.add_argument("--broker", default="AXI")
    parser.add_argument("--account-type", default="STANDARD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    broker = normalize_broker(args.broker)
    account = normalize_account_type(args.account_type, broker)
    db_path = account_memory_path(BASE_DIR, account, broker)
    gates = load_gates(BASE_DIR)
    print(f"DB: {db_path}")
    for stage, value in gates.items():
        print(
            f"  gates[{stage}] net>{value.min_net:g} pf>={value.min_pf:g} trades>={value.min_trades} "
            f"(W1 {value.min_trades_w1} / MN {value.min_trades_mn}) dd<={value.max_dd:g} "
            f"recovery>={value.min_recovery:g}"
        )

    conn = connect_memory(db_path)
    try:
        rescore_stage(conn, "candidates", ["id"], gates["candidates"], broker, args.dry_run)
        rescore_stage(conn, "seed_scores", ["id"], gates["seeds"], broker, args.dry_run)
        rescore_stage(
            conn, "candidate_robustness", ["candidate_id", "run_id"], gates["robustness"], broker, args.dry_run
        )
        # The gate pass only visits accepted/rejected rows. no_trades, no_history,
        # history_ok and the retryable states also carry metrics, and leaving them
        # on an old factor is what made half the base tables look stale after every
        # regeneration. Their status is not derived from the net gate, so they are
        # refreshed, not re-judged.
        for table, keys in (
            ("candidates", ["id"]),
            ("seed_scores", ["id"]),
            ("candidate_robustness", ["candidate_id", "run_id"]),
        ):
            refresh_stage(conn, table, keys, [("metrics_json", "score")], broker, args.dry_run)
        for table in ("candidate_final_tick", "candidate_final_tick_6m"):
            refresh_stage(
                conn,
                table,
                ["candidate_id"],
                [("ohlc_metrics_json", "ohlc_score"), ("real_tick_metrics_json", "real_tick_score")],
                broker,
                args.dry_run,
            )
        refresh_stage(conn, "candidate_regression", ["candidate_id"], [("metrics_json", "score")], broker, args.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
