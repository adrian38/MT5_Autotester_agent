"""Remote Universo actions, using the agent's existing extraction and probe runner."""
from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ubs.account import account_disabled_symbols_path, broker_asset_universe_path_with_fallback
from ubs.mt5_symbol_extract import extract_symbols_from_mt5, write_asset_universe_from_symbols
from ubs.tester_diagnostics import (
    BROKER_BLOCKED_TRADE_MODES,
    load_trade_mode_snapshot,
    save_trade_mode_snapshot,
    snapshot_symbol_trade_modes,
    snapshot_trade_blocked_symbols,
    trade_mode_snapshot_path,
)
from ubs.universe import (
    canonical_symbol, load_asset_universe, load_disabled_symbols,
    load_seed_enabled_disabled_symbols, save_disabled_symbols,
)

from .common import utc_now


def assert_writable(path: Path, project: Path) -> Path:
    """Remote actions may only write within their own configured agent project."""
    target = path.expanduser().resolve()
    if not target.is_relative_to(project.resolve()):
        raise ValueError(f"Destino fuera del proyecto del agente: {target}")
    return target


class UniverseService:
    def __init__(self, config, settings, memory):
        self.config, self.settings, self.memory = config, settings, memory
        self.project = Path(config["project_dir"]).expanduser().resolve()
        self.broker = config.get("broker") or settings.get("General", "ubs_broker", fallback="ROBOFOREX")
        self.account = config.get("account_type") or settings.get("General", "ubs_account_type", fallback="ECN")
        self.assets = broker_asset_universe_path_with_fallback(self.project, self.broker)
        self.policy = account_disabled_symbols_path(self.project, self.account, self.broker)
        self.trade_modes = trade_mode_snapshot_path(self.project, self.broker, self.account)

    def policy_state(self):
        # Fail closed: damaged policy must not silently re-enable thousands of symbols.
        if self.policy.exists():
            value = json.loads(self.policy.read_text(encoding="utf-8"))
            if not isinstance(value, (dict, list)):
                raise ValueError("La politica de simbolos no es valida")
        groups, aliases = load_asset_universe(self.assets, include_disabled=True)
        canonical = lambda values: {canonical_symbol(symbol, aliases) for symbol in values}
        disabled = canonical(load_disabled_symbols(self.policy))
        seeds = canonical(load_seed_enabled_disabled_symbols(self.policy)) & disabled
        return groups, aliases, disabled, seeds

    def save_policy(self, disabled, seeds):
        path = assert_writable(self.policy, self.project)
        backup = None
        if path.exists():
            backup = path.with_suffix(path.suffix + f".bak_{datetime.now():%Y%m%d_%H%M%S_%f}")
            shutil.copy2(path, backup)
        save_disabled_symbols(path, disabled, seeds)
        return str(backup) if backup else None

    def terminal_path(self):
        for section in self.settings.sections():
            if not section.startswith("Terminal."):
                continue
            row = self.settings[section]
            if row.get("enabled", "1").lower() not in {"1", "true", "yes", "on", "si"}:
                continue
            if row.get("broker", "ROBOFOREX").upper() == str(self.broker).upper() and row.get("mt5_path"):
                return row["mt5_path"]
        return self.settings.get("Paths", "mt5_path", fallback="")

    def sync(self, payload):
        _, aliases, disabled, seeds = self.policy_state()
        assert_writable(self.assets, self.project)
        assert_writable(self.policy, self.project)
        assert_writable(self.trade_modes, self.project)
        login_text = str(payload.get("login") or "").strip()
        if login_text and (not login_text.isascii() or not login_text.isdecimal() or int(login_text) <= 0):
            raise ValueError("Login debe ser numerico y positivo")
        raw_path = str(payload.get("mt5_path") or self.terminal_path()).strip()
        terminal = Path(raw_path).expanduser() if raw_path else None
        if terminal is not None and not terminal.is_file():
            raise ValueError(f"No existe el terminal: {terminal}")
        password = str(payload.get("password") or "")
        try:
            extraction = extract_symbols_from_mt5(
                terminal_path=terminal, login=int(login_text) if login_text else None,
                password=password, server=str(payload.get("server") or ""),
            )
        except Exception as exc:
            detail = str(exc).replace(password, "[REDACTED]") if password else str(exc)
            raise ValueError(detail) from None
        if not extraction.symbols:
            raise ValueError("MT5 devolvio un universo vacio; se conserva el universo anterior")
        result = write_asset_universe_from_symbols(self.assets, extraction.symbols, preserve_existing_groups=False)
        save_trade_mode_snapshot(
            self.trade_modes,
            extraction.symbols,
            account_login=extraction.account_login,
            server=extraction.server,
            terminal_path=extraction.terminal_path,
        )
        retired = {canonical_symbol(symbol, aliases) for symbol in result.removed_symbols}
        newly_disabled = retired - disabled
        dropped_seeds = seeds & retired
        backup = self.save_policy(disabled | retired, seeds - retired) if newly_disabled or dropped_seeds else None
        return {
            "total": sum(result.counts.values()), "added": len(result.added_symbols),
            "removed": len(result.removed_symbols), "newly_disabled": len(newly_disabled),
            "dropped_seed_exceptions": len(dropped_seeds),
            "trade_blocked": len(snapshot_trade_blocked_symbols(self.trade_modes)),
            "trade_mode_snapshot": str(self.trade_modes),
            "universe_backup": str(result.backup_path) if result.backup_path else None,
            "policy_backup": backup,
        }

    def latest_statuses(self, aliases, *, history_probe=True):
        if not self.memory.exists():
            return {}
        with contextlib.closing(sqlite3.connect(self.memory.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            if not conn.execute("select 1 from sqlite_master where type='table' and name='candidates'").fetchone():
                return {}
            policy_filter = "policy='history_probe'" if history_probe else "coalesce(policy, '')!='history_probe'"
            rows = conn.execute(
                f"select target_symbol,status from candidates where {policy_filter} order by id"
            )
            return {canonical_symbol(symbol, aliases): status for symbol, status in rows if symbol}

    def history_dates(self):
        defaults = self.config.get("defaults") or {}
        start_text = defaults.get("from_date") or self.settings.get("General", "ubs_agent_from_date", fallback="") or "2020.01.01"
        start = datetime.strptime(start_text, "%Y.%m.%d")
        try:
            end = start.replace(year=start.year + 1)
        except ValueError:
            end = start + timedelta(days=365)
        return {"from_date": start_text, "to_date": end.strftime("%Y.%m.%d")}

    def history_preview(self):
        groups, aliases, disabled, _ = self.policy_state()
        latest = self.latest_statuses(aliases)
        active = {canonical_symbol(symbol, aliases) for values in groups.values() for symbol in values} - disabled
        return {"pending": sum(latest.get(symbol) not in {"history_ok", "no_history"} for symbol in active), **self.history_dates()}

    def disable_preview(self):
        _, aliases, disabled, _ = self.policy_state()
        symbols = {symbol for symbol, status in self.latest_statuses(aliases).items() if status == "no_history"}
        return {"total": len(symbols), "already_disabled": len(symbols & disabled),
                "newly_disabled": len(symbols - disabled), "symbols": sorted(symbols - disabled)}

    def trade_disabled_preview(self):
        _, aliases, disabled, _ = self.policy_state()
        journal_symbols = {
            symbol
            for symbol, status in self.latest_statuses(aliases, history_probe=False).items()
            if status == "trade_disabled"
        }
        terminal_modes = snapshot_symbol_trade_modes(self.trade_modes)
        terminal_known = {canonical_symbol(symbol, aliases) for symbol in terminal_modes}
        terminal_symbols = {
            canonical_symbol(symbol, aliases)
            for symbol, mode in terminal_modes.items()
            if mode in BROKER_BLOCKED_TRADE_MODES
        }
        journal_fallback = journal_symbols - terminal_known
        symbols = journal_fallback | terminal_symbols
        snapshot = load_trade_mode_snapshot(self.trade_modes)
        return {"total": len(symbols), "already_disabled": len(symbols & disabled),
                "newly_disabled": len(symbols - disabled), "symbols": sorted(symbols - disabled),
                "journal_total": len(journal_symbols), "terminal_total": len(terminal_symbols),
                "journal_fallback_total": len(journal_fallback),
                "terminal_captured_at": str(snapshot.get("captured_at") or "")}

    def disable(self, payload):
        return self._disable_confirmed(payload, self.disable_preview)

    def disable_trade_disabled(self, payload):
        return self._disable_confirmed(payload, self.trade_disabled_preview)

    def _disable_confirmed(self, payload, preview):
        values = payload.get("symbols")
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("symbols debe contener la lista de simbolos confirmados")
        _, aliases, disabled, seeds = self.policy_state()
        approved = {canonical_symbol(symbol, aliases) for symbol in values}
        current = set(preview()["symbols"])
        selected = current & approved
        backup = self.save_policy(disabled | selected, seeds - selected) if selected else None
        return {"newly_disabled": len(selected), "policy_backup": backup}


def build_history_command(config, dates):
    from .node import build_generation_command
    project = Path(config["project_dir"]).expanduser().resolve()
    script = project / "ubs_agent.py"
    source = script.read_text(encoding="utf-8", errors="replace")
    if '"--probe-universe-history"' not in source and "'--probe-universe-history'" not in source:
        raise ValueError("El agente no soporta --probe-universe-history")
    command, cwd = build_generation_command(config, {**dates, "execute_backtests": True, "dry_run": False})
    # Keep terminal, source, broker, memory, mapping and suffix configuration.
    # No generation/repair pipeline is invoked by the probe command.
    command.extend(["--probe-universe-history", "--probe-history-timeframe", "H1"])
    if "--execute-backtests" not in command:
        raise ValueError("El agente no soporta ejecutar backtests")
    for option in ("--memory", "--output-dir"):
        if option in command:
            target = Path(command[command.index(option) + 1])
            assert_writable(target if target.is_absolute() else project / target, project)
    return command, cwd


class UniverseControllerMixin:
    def _universe_service(self):
        settings, memory = self._settings_and_memory()
        return UniverseService(self.config, settings, memory)

    def _assert_universe_idle(self):
        if self._busy() or self.queue or (getattr(self, "ui_busy", None) and self.ui_busy()):
            raise RuntimeError("El agente esta ocupado; termina o detiene el proceso antes de modificar el universo")

    def universe_action(self, action, payload):
        with self.lock:
            self._assert_universe_idle()
            service = self._universe_service()
            if action == "sync":
                self.universe_operation_running = True
                try:
                    return service.sync(payload)
                finally:
                    self.universe_operation_running = False
            if action == "history-preview":
                return service.history_preview()
            if action == "disable-preview":
                return service.disable_preview()
            if action == "disable-no-history":
                return service.disable(payload)
            if action == "trade-disabled-preview":
                return service.trade_disabled_preview()
            if action == "disable-trade-disabled":
                return service.disable_trade_disabled(payload)
            raise ValueError("Accion de universo desconocida")

    def start_universe_history(self):
        with self.lock:
            self._assert_universe_idle()
            service = self._universe_service()
            dates = service.history_dates()
            command, cwd = build_history_command(self.config, dates)
            job_id = "universe_history_" + uuid.uuid4().hex
            log_path = self.runtime_dir / f"{job_id}.log"
            previous = self.state
            self.state = {
                "job_id": job_id, "job_type": "universe_history", "status": "running", "pid": None,
                "started_at": utc_now(), "finished_at": None, "return_code": None,
                "request": dates, "command": command, "log_path": str(log_path), "error": None,
                "pipeline": [{"action": "universe_history", "cycle": 1, "run_id": None}],
                "current_stage": "universe_history", "current_cycle": 1, "current_run_id": None,
                "current_attempt": None, "completed_stages": [], "skipped_stages": [],
                "stage_return_codes": {}, "stage_pending_counts": {}, "commands": {},
                "cycle_run_ids": {}, "telegram_notifications": [], "cleanup_failed": False,
            }
            try:
                self._launch_step(0, command, cwd, log_path, first=True)
            except Exception:
                if self.process is None:
                    if self.log_handle:
                        self.log_handle.close()
                        self.log_handle = None
                    self.state = previous
                raise
            return {**self.state, "queued": False, "task_queue": self._queue_snapshot()}
