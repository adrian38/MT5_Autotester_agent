from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


TRADE_DISABLED_STATUS = "trade_disabled"
BROKER_BLOCKED_TRADE_MODES = {0, 3}


_TRADE_RESTRICTIONS: tuple[tuple[str, int, tuple[re.Pattern[str], ...]], ...] = (
    (
        "close_only",
        10044,
        (
            re.compile(r"only position closing is allowed", re.IGNORECASE),
            re.compile(r"\b(?:retcode\s*[=:]?\s*)10044\b", re.IGNORECASE),
        ),
    ),
    (
        "disabled",
        10017,
        (
            re.compile(r"\btrade is disabled\b", re.IGNORECASE),
            re.compile(r"\b(?:retcode\s*[=:]?\s*)10017\b", re.IGNORECASE),
        ),
    ),
)


def tester_journal_sidecar(report_path: Path) -> Path:
    """Return the journal sidecar path without importing the MT5 runner."""

    return report_path.with_name(f"{report_path.stem}.mt5log.txt")


def trade_mode_snapshot_path(project_dir: Path, broker: object, account_type: object) -> Path:
    broker_key = re.sub(r"[^A-Z0-9_-]+", "_", str(broker or "UNKNOWN").strip().upper())
    account_key = re.sub(r"[^A-Z0-9_-]+", "_", str(account_type or "UNKNOWN").strip().upper())
    return project_dir / "outputs" / f"ubs_symbol_trade_modes_{broker_key}_{account_key}.json"


def save_trade_mode_snapshot(
    path: Path,
    symbols: Iterable[object],
    *,
    account_login: int | None,
    server: str,
    terminal_path: Path | None,
) -> Path:
    """Persist the trade modes returned by the latest live MT5 extraction."""

    rows: list[dict[str, object]] = []
    for symbol in symbols:
        name = str(getattr(symbol, "name", "") or "").strip()
        mode = getattr(symbol, "trade_mode", None)
        if not name or not isinstance(mode, int):
            continue
        rows.append({"symbol": name, "trade_mode": mode})
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account_login": account_login,
        "server": str(server or ""),
        "terminal_path": str(terminal_path) if terminal_path else "",
        "symbols": sorted(rows, key=lambda row: str(row["symbol"]).upper()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def load_trade_mode_snapshot(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def snapshot_trade_blocked_symbols(path: Path) -> set[str]:
    return {
        symbol
        for symbol, mode in snapshot_symbol_trade_modes(path).items()
        if mode in BROKER_BLOCKED_TRADE_MODES
    }


def snapshot_symbol_trade_modes(path: Path) -> dict[str, int]:
    payload = load_trade_mode_snapshot(path)
    values = payload.get("symbols")
    if not isinstance(values, list):
        return {}
    modes: dict[str, int] = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        mode = row.get("trade_mode")
        if symbol and isinstance(mode, int):
            modes[symbol] = mode
    return modes


def trade_disabled_metadata(report_path: Path) -> dict[str, object] | None:
    """Return authoritative MT5 trade-mode evidence stored beside a report.

    Generic order errors are deliberately ignored: only MetaTrader's explicit
    close-only/disabled messages or their documented return codes qualify.
    Callers additionally require a parsed report with zero trades.
    """

    sidecar = tester_journal_sidecar(report_path)
    if not sidecar.exists():
        return None
    try:
        journal = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for trade_mode, retcode, patterns in _TRADE_RESTRICTIONS:
        evidence = None
        for pattern in patterns:
            evidence = pattern.search(journal)
            if evidence is not None:
                break
        if evidence is None:
            continue
        return {
            "reasons": [TRADE_DISABLED_STATUS],
            "no_score": True,
            "recommendation": "deshabilitar simbolo; el broker no permite abrir nuevas posiciones",
            "log_source": str(sidecar),
            "failure_type": "symbol_trade_mode",
            "trade_mode": trade_mode,
            "trade_retcode": retcode,
            "retryable": False,
            "trade_restriction_evidence": evidence.group(0),
        }
    return None
