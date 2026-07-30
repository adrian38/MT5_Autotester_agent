from __future__ import annotations

import argparse
import configparser
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import account_memory_path, normalize_account_type, normalize_broker
from ubs.db import connect_memory
from ubs.weights import ASSET_ACCEPTED_BONUS, feedback_weight, metric_reasons


@dataclass(frozen=True)
class ReportAudit:
    path: str
    ok: bool
    seconds: float
    error: str = ""
    symbol: str = ""
    timeframe: str = ""
    period_start: str = ""
    period_end: str = ""
    raw_deal_net: float = 0.0
    trade_net: float = 0.0
    net_diff: float = 0.0
    out_deals: int = 0
    trades: int = 0
    unmatched_out_deals: int = 0
    mixed_separator_hits: tuple[str, ...] = ()


def _parse_report_worker(path_text: str) -> dict[str, Any]:
    from portfolio_manager.mt5_report import parse_report

    path = Path(path_text)
    started = time.perf_counter()
    try:
        report = parse_report(path)
        raw_trade_net = sum(
            deal.net_profit
            for deal in report.raw_deals
            if deal.trade_type.lower() in {"buy", "sell"}
        )
        trade_net = sum(trade.profit_loss for trade in report.trades)
        out_deals = sum(
            1
            for deal in report.raw_deals
            if deal.trade_type.lower() in {"buy", "sell"} and deal.direction.lower() == "out"
        )
        hits = _mixed_separator_hits(path)
        audit = ReportAudit(
            path=str(path),
            ok=True,
            seconds=round(time.perf_counter() - started, 4),
            symbol=report.symbol,
            timeframe=report.timeframe,
            period_start=report.period_start,
            period_end=report.period_end,
            raw_deal_net=round(raw_trade_net, 2),
            trade_net=round(trade_net, 2),
            net_diff=round(raw_trade_net - trade_net, 4),
            out_deals=out_deals,
            trades=len(report.trades),
            unmatched_out_deals=max(out_deals - len(report.trades), 0),
            mixed_separator_hits=tuple(hits[:8]),
        )
    except Exception as exc:
        audit = ReportAudit(
            path=str(path),
            ok=False,
            seconds=round(time.perf_counter() - started, 4),
            error=f"{type(exc).__name__}: {exc}",
        )
    return asdict(audit)


def _mixed_separator_hits(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    pattern = re.compile(r"[-+]?\d{1,3}(?:[.,]\d{3})+[.,]\d+")
    return list(dict.fromkeys(pattern.findall(text)))


def _active_context() -> tuple[str, str]:
    parser = configparser.ConfigParser()
    parser.read(BASE_DIR / "ui_settings.ini", encoding="utf-8")
    broker = normalize_broker(parser.get("General", "ubs_broker", fallback="ROBOFOREX"))
    account = normalize_account_type(parser.get("General", "ubs_account_type", fallback="ECN"), broker)
    return broker, account


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def _count_map(rows: list[sqlite3.Row]) -> dict[str, int]:
    return {str(row["status"] or ""): int(row["n"] or 0) for row in rows}


def _safe_json(raw: object) -> dict[str, Any]:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _existing_path(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and Path(text).is_file()


def _add_report_ref(refs: dict[str, list[dict[str, Any]]], path: object, **metadata: Any) -> None:
    text = str(path or "").strip()
    if text:
        refs.setdefault(text, []).append(metadata)


def _metric_mismatches(report: dict[str, Any], refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not report.get("ok"):
        return []
    result: list[dict[str, Any]] = []
    for ref in refs:
        metrics = _safe_json(ref.get("metrics_json"))
        if not metrics:
            continue
        expected_symbol = str(metrics.get("symbol") or "").strip()
        expected_tf = str(metrics.get("timeframe") or "").strip().upper()
        checks = []
        if expected_symbol and expected_symbol != str(report.get("symbol") or "").strip():
            checks.append(f"symbol {expected_symbol} != {report.get('symbol')}")
        if expected_tf and expected_tf != str(report.get("timeframe") or "").strip().upper():
            checks.append(f"tf {expected_tf} != {report.get('timeframe')}")
        try:
            expected_net = float(metrics.get("net_profit"))
            if abs(expected_net - float(report.get("trade_net") or 0.0)) > 0.05:
                checks.append(f"net {expected_net:.2f} != {float(report.get('trade_net') or 0.0):.2f}")
        except (TypeError, ValueError):
            pass
        try:
            expected_trades = int(metrics.get("trades"))
            if expected_trades != int(report.get("trades") or 0):
                checks.append(f"trades {expected_trades} != {report.get('trades')}")
        except (TypeError, ValueError):
            pass
        if checks:
            result.append(
                {
                    "stage": ref.get("stage"),
                    "row_id": ref.get("row_id"),
                    "path": report.get("path"),
                    "checks": checks,
                }
            )
    return result


def build_audit(memory_path: Path, *, run_id: int | None, parse_reports: bool, workers: int) -> dict[str, Any]:
    conn = connect_memory(memory_path)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "select * from runs where id=coalesce(?, (select max(id) from runs))",
            (run_id,),
        ).fetchone()
        if run is None:
            return {"memory_path": str(memory_path), "error": "no runs"}
        run_id = int(run["id"])

        summary: dict[str, Any] = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "memory_path": str(memory_path),
            "run_id": run_id,
            "run_created_at": run["created_at"],
            "counts": {
                "base": _count_map(_rows(conn, "select status,count(*) n from candidates where run_id=? group by status", (run_id,))),
                "robust": _count_map(
                    _rows(
                        conn,
                        "select cr.status,count(*) n from candidate_robustness cr join candidates c on c.id=cr.candidate_id where c.run_id=? group by cr.status",
                        (run_id,),
                    )
                ),
                "final_tick_probe": _count_map(
                    _rows(
                        conn,
                        "select ft.status,count(*) n from candidate_final_tick ft join candidates c on c.id=ft.candidate_id where c.run_id=? group by ft.status",
                        (run_id,),
                    )
                ),
                "final_tick_6m": _count_map(
                    _rows(
                        conn,
                        "select ft.status,count(*) n from candidate_final_tick_6m ft join candidates c on c.id=ft.candidate_id where c.run_id=? group by ft.status",
                        (run_id,),
                    )
                ),
                "regression": _count_map(
                    _rows(
                        conn,
                        "select rg.status,count(*) n from candidate_regression rg join candidates c on c.id=rg.candidate_id where c.run_id=? group by rg.status",
                        (run_id,),
                    )
                ),
            },
        }

        checks = {
            "base_scored_missing_report_path": conn.execute(
                "select count(*) from candidates where run_id=? and status in ('accepted','rejected','no_trades') and coalesce(report_path,'')=''",
                (run_id,),
            ).fetchone()[0],
            "accepted_without_robust": conn.execute(
                """
                select count(*)
                from candidates c
                left join candidate_robustness cr on cr.candidate_id=c.id
                where c.run_id=? and c.status='accepted' and cr.candidate_id is null
                """,
                (run_id,),
            ).fetchone()[0],
            "robust_accepted_without_probe": conn.execute(
                """
                select count(*)
                from candidates c
                join candidate_robustness cr on cr.candidate_id=c.id
                left join candidate_final_tick ft on ft.candidate_id=c.id
                where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft.candidate_id is null
                """,
                (run_id,),
            ).fetchone()[0],
            "probe_eligible_without_6m": conn.execute(
                """
                select count(*)
                from candidates c
                join candidate_robustness cr on cr.candidate_id=c.id
                join candidate_final_tick ft on ft.candidate_id=c.id
                left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
                where c.run_id=? and c.status='accepted' and cr.status='accepted'
                  and ft.status in ('accepted','pending_ohlc_trades')
                  and ft6.candidate_id is null
                """,
                (run_id,),
            ).fetchone()[0],
            "portfolio_usable_6m": conn.execute(
                """
                select count(*)
                from candidates c
                join candidate_robustness cr on cr.candidate_id=c.id
                join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
                where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.status='accepted'
                """,
                (run_id,),
            ).fetchone()[0],
            "six_month_accepted_without_regression": conn.execute(
                """
                select count(*)
                from candidates c
                join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted'
                left join candidate_regression rg on rg.candidate_id=c.id
                where c.run_id=? and c.status='accepted' and rg.candidate_id is null
                """,
                (run_id,),
            ).fetchone()[0],
        }
        summary["checks"] = checks

        refs: dict[str, list[dict[str, Any]]] = {}
        base_rows = _rows(conn, "select id,status,set_path,report_path,score,metrics_json from candidates where run_id=?", (run_id,))
        for row in base_rows:
            _add_report_ref(
                refs,
                row["report_path"],
                stage="base",
                row_id=int(row["id"]),
                status=row["status"],
                metrics_json=row["metrics_json"],
            )
        robust_rows = _rows(
            conn,
            """
            select cr.candidate_id,cr.status,cr.report_path,cr.metrics_json
            from candidate_robustness cr
            join candidates c on c.id=cr.candidate_id
            where c.run_id=?
            """,
            (run_id,),
        )
        for row in robust_rows:
            _add_report_ref(
                refs,
                row["report_path"],
                stage="robust",
                row_id=int(row["candidate_id"]),
                status=row["status"],
                metrics_json=row["metrics_json"],
            )
        for table, stage in (("candidate_final_tick", "final_tick_probe"), ("candidate_final_tick_6m", "final_tick_6m")):
            ft_rows = _rows(
                conn,
                f"""
                select ft.candidate_id,ft.status,ft.ohlc_report_path,ft.real_tick_report_path,
                       ft.ohlc_metrics_json,ft.real_tick_metrics_json
                from {table} ft
                join candidates c on c.id=ft.candidate_id
                where c.run_id=?
                """,
                (run_id,),
            )
            for row in ft_rows:
                _add_report_ref(
                    refs,
                    row["ohlc_report_path"],
                    stage=f"{stage}_ohlc",
                    row_id=int(row["candidate_id"]),
                    status=row["status"],
                    metrics_json=row["ohlc_metrics_json"],
                )
                _add_report_ref(
                    refs,
                    row["real_tick_report_path"],
                    stage=f"{stage}_tick",
                    row_id=int(row["candidate_id"]),
                    status=row["status"],
                    metrics_json=row["real_tick_metrics_json"],
                )
        regression_rows = _rows(
            conn,
            """
            select rg.candidate_id,rg.status,rg.report_path,rg.metrics_json,rg.details_json
            from candidate_regression rg
            join candidates c on c.id=rg.candidate_id
            where c.run_id=?
            """,
            (run_id,),
        )
        for row in regression_rows:
            _add_report_ref(
                refs,
                row["report_path"],
                stage="regression_ohlc",
                row_id=int(row["candidate_id"]),
                status=row["status"],
                metrics_json=row["metrics_json"],
            )
        seed_rows = _rows(
            conn,
            "select id,status,seed_path,report_path,score,metrics_json from seed_scores where active=1",
        )
        for row in seed_rows:
            _add_report_ref(
                refs,
                row["report_path"],
                stage="active_seed",
                row_id=int(row["id"]),
                status=row["status"],
                metrics_json=row["metrics_json"],
            )

        missing_files = [
            {"path": path, "refs": refs[path]}
            for path in sorted(refs)
            if not _existing_path(path)
        ]
        summary["report_refs"] = {
            "unique_paths": len(refs),
            "total_refs": sum(len(items) for items in refs.values()),
            "missing_files": len(missing_files),
            "missing_file_examples": missing_files[:30],
        }

        reason_counts: dict[str, dict[str, int]] = {}
        for stage, rows, column in (
            ("base", base_rows, "metrics_json"),
            ("robust", robust_rows, "metrics_json"),
        ):
            counter = Counter()
            for row in rows:
                counter.update(metric_reasons(row[column]))
            reason_counts[stage] = dict(counter.most_common())
        for table, stage in (("candidate_final_tick", "final_tick_probe"), ("candidate_final_tick_6m", "final_tick_6m")):
            counter = Counter()
            for row in _rows(
                conn,
                f"select ft.similarity_json from {table} ft join candidates c on c.id=ft.candidate_id where c.run_id=?",
                (run_id,),
            ):
                counter.update(metric_reasons(row["similarity_json"]))
            reason_counts[stage] = dict(counter.most_common())
        regression_counter = Counter()
        for row in regression_rows:
            regression_counter.update(metric_reasons(row["details_json"] or row["metrics_json"]))
        reason_counts["regression"] = dict(regression_counter.most_common())
        summary["reason_counts"] = reason_counts

        feedback_rows = _rows(
            conn,
            """
            select c.run_id,c.generation,c.id,c.set_path,c.seed_path,c.target_symbol,c.symbol,c.period,c.family,
                   c.score,c.accepted,c.metrics_json,c.status,c.report_path,
                   cr.status robust_status,cr.positive_bonus robust_positive_bonus,
                   cr.negative_bonus robust_negative_bonus,cr.metrics_json robust_metrics_json,
                   ft.status final_tick_status,ft.similarity_json final_tick_similarity_json,
                   ft6.status final_tick_6m_status,ft6.similarity_json final_tick_6m_similarity_json,
                   rg.status regression_status,rg.points_applied regression_points_applied
            from candidates c
            left join candidate_robustness cr on cr.candidate_id=c.id and c.status='accepted'
            left join candidate_final_tick ft on ft.candidate_id=c.id and c.status='accepted' and cr.status='accepted'
            left join candidate_final_tick_6m ft6
              on ft6.candidate_id=c.id and c.status='accepted' and cr.status='accepted'
             and ft.status in ('accepted','pending_ohlc_trades')
            left join candidate_regression rg on rg.candidate_id=c.id and ft6.status='accepted'
            where c.run_id=? and c.status in ('accepted','rejected','no_trades')
              and (c.score is not null or c.status='no_trades')
            """,
            (run_id,),
        )
        weights = []
        by_6m: dict[str, list[float]] = defaultdict(list)
        for row in feedback_rows:
            value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
            if value is None:
                continue
            weights.append(float(value))
            by_6m[str(row["final_tick_6m_status"] or "sin_6m")].append(float(value))
        summary["weights"] = {
            "rows_considered": len(feedback_rows),
            "rows_weighted": len(weights),
            "min": round(min(weights), 4) if weights else None,
            "avg": round(sum(weights) / len(weights), 4) if weights else None,
            "max": round(max(weights), 4) if weights else None,
            "by_final_tick_6m": {
                key: {
                    "n": len(values),
                    "avg": round(sum(values) / len(values), 4),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                }
                for key, values in sorted(by_6m.items())
            },
        }

        if parse_reports:
            existing_paths = [path for path in sorted(refs) if _existing_path(path)]
            report_results: list[dict[str, Any]] = []
            if existing_paths:
                with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
                    futures = {pool.submit(_parse_report_worker, path): path for path in existing_paths}
                    for future in as_completed(futures):
                        report_results.append(future.result())
            report_results.sort(key=lambda item: item["path"])
            parse_errors = [item for item in report_results if not item.get("ok")]
            net_mismatches = [
                item
                for item in report_results
                if item.get("ok") and abs(float(item.get("net_diff") or 0.0)) > 0.05
            ]
            unmatched = [
                item for item in report_results if item.get("ok") and int(item.get("unmatched_out_deals") or 0) > 0
            ]
            mixed_numbers = [item for item in report_results if item.get("mixed_separator_hits")]
            metric_mismatches = []
            for item in report_results:
                metric_mismatches.extend(_metric_mismatches(item, refs.get(str(item["path"]), [])))
            summary["report_parse"] = {
                "parsed": len(report_results),
                "parse_errors": len(parse_errors),
                "parse_error_examples": parse_errors[:20],
                "raw_vs_trade_net_mismatches": len(net_mismatches),
                "raw_vs_trade_net_examples": net_mismatches[:30],
                "unmatched_out_deal_reports": len(unmatched),
                "unmatched_out_deal_examples": unmatched[:30],
                "mixed_separator_reports": len(mixed_numbers),
                "mixed_separator_examples": mixed_numbers[:30],
                "stored_metric_mismatches": len(metric_mismatches),
                "stored_metric_mismatch_examples": metric_mismatches[:50],
                "slowest_reports": sorted(report_results, key=lambda item: float(item.get("seconds") or 0.0), reverse=True)[:20],
            }
        return summary
    finally:
        conn.close()


def write_text_report(audit: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = []
    lines.append("UBS COMPLETE AUDIT")
    lines.append("=" * 80)
    lines.append(f"created_at: {audit.get('created_at')}")
    lines.append(f"memory: {audit.get('memory_path')}")
    lines.append(f"run: #{audit.get('run_id')} ({audit.get('run_created_at')})")
    lines.append("")
    lines.append("COUNTS")
    for key, value in audit.get("counts", {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("CHECKS")
    for key, value in audit.get("checks", {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("REPORT REFERENCES")
    refs = audit.get("report_refs", {})
    for key in ("unique_paths", "total_refs", "missing_files"):
        lines.append(f"- {key}: {refs.get(key)}")
    lines.append("")
    lines.append("REASONS")
    for key, value in audit.get("reason_counts", {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("WEIGHTS")
    for key, value in audit.get("weights", {}).items():
        lines.append(f"- {key}: {value}")
    if "report_parse" in audit:
        lines.append("")
        lines.append("REPORT PARSE")
        parse = audit["report_parse"]
        for key in (
            "parsed",
            "parse_errors",
            "raw_vs_trade_net_mismatches",
            "unmatched_out_deal_reports",
            "mixed_separator_reports",
            "stored_metric_mismatches",
        ):
            lines.append(f"- {key}: {parse.get(key)}")
        for key in (
            "parse_error_examples",
            "raw_vs_trade_net_examples",
            "unmatched_out_deal_examples",
            "mixed_separator_examples",
            "stored_metric_mismatch_examples",
            "slowest_reports",
        ):
            lines.append("")
            lines.append(key.upper())
            for item in parse.get(key, []):
                lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Auditoria completa UBS: DB, pesos y reportes.")
    parser.add_argument("--broker", default="")
    parser.add_argument("--account-type", default="")
    parser.add_argument("--memory", default="")
    parser.add_argument("--run-id", type=int, default=0)
    parser.add_argument("--skip-report-parse", action="store_true")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()

    if args.memory:
        memory_path = Path(args.memory)
    else:
        broker, account = _active_context()
        if args.broker:
            broker = normalize_broker(args.broker)
        if args.account_type:
            account = normalize_account_type(args.account_type, broker)
        memory_path = account_memory_path(BASE_DIR, account, broker)
    if not memory_path.exists():
        raise SystemExit(f"No existe memoria UBS: {memory_path}")

    audit = build_audit(
        memory_path,
        run_id=args.run_id or None,
        parse_reports=not args.skip_report_parse,
        workers=args.workers,
    )
    out_dir = BASE_DIR / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"ubs_complete_audit_run_{audit.get('run_id', 'none')}_{stamp}"
    json_path = out_dir / f"{stem}.json"
    txt_path = out_dir / f"{stem}.txt"
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_text_report(audit, txt_path)
    print(f"JSON={json_path}")
    print(f"TXT={txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
