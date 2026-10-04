from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import time
from typing import Any


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

def _safe_json(raw: object) -> dict[str, Any]:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}

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
