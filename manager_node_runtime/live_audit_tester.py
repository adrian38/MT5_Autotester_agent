from __future__ import annotations

import configparser
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_audit_helpers import _member_strategy_id, _metric_number, _read_set_text, _redact_log_files, _redact_runner_output


class LiveAuditTesterMixin:
    """Ejecucion del tester sobre los sets del portafolio auditado."""

    def _run_tester(
        self, request: dict[str, Any], audit_id: str, period_start: datetime, period_end: datetime
    ) -> tuple[list[dict[str, Any]], list[float], dict[str, int], list[dict[str, Any]], dict[str, Any]]:
        from portfolio_manager.mt5_report import parse_report

        detail, members = self._portfolio_members(request["portfolio_id"], request["portfolio_type"])
        if not members:
            raise ValueError("El portafolio no contiene estrategias")
        work = self.runtime_dir / f"audit_{request['audit_key']}" / audit_id
        sets_dir, reports_dir, configs_dir, logs_dir = (work / name for name in ("sets", "reports", "configs", "logs"))
        for directory in (sets_dir, reports_dir, configs_dir, logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        set_files: list[Path] = []
        member_by_stem: dict[str, dict[str, Any]] = {}
        volume_rules = self._broker_volume_rules()
        for index, member in enumerate(members, 1):
            source = self._resolve_set(str(member.get("set_path") or member.get("set_id") or ""))
            text, set_encoding = _read_set_text(source)
            portfolio_lot, tester_lot, volume_min, volume_step, units = self._tester_lot(member, volume_rules)
            text = self._set_value(text, "StartLots", f"{tester_lot:.8f}".rstrip("0").rstrip("."))
            target = sets_dir / f"audit_{index:03d}_{source.name}"
            target.write_text(text, encoding=set_encoding, newline="\n")
            runtime_text, _runtime_encoding = _read_set_text(target)
            runtime_lot_text = self._set_parameter(runtime_text, "StartLots")
            try:
                runtime_lot = float(runtime_lot_text)
            except (TypeError, ValueError):
                runtime_lot = None
            set_files.append(target)
            strategy = _member_strategy_id(member, target.stem)
            configured_real_lots = request.get("real_strategy_lots") or {}
            real_account_lot = float(configured_real_lots.get(strategy, tester_lot))
            member_by_stem[target.stem] = {
                "member": member,
                "artifact": {
                    "strategy": strategy,
                    "symbol": str(member.get("symbol") or ""),
                    "configured_lot": portfolio_lot,
                    "tester_lot": tester_lot,
                    "real_account_lot": real_account_lot,
                    "real_account_lot_source": (
                        "configured" if strategy in configured_real_lots else "tester_default"
                    ),
                    "portfolio_units": units,
                    "broker_volume_min": volume_min,
                    "broker_volume_step": volume_step,
                    "configured_lot_below_broker_minimum": (
                        volume_min is not None and portfolio_lot < volume_min - 1e-9
                    ),
                    "lot_adjusted_to_broker_rules": not math.isclose(
                        portfolio_lot, tester_lot, rel_tol=0, abs_tol=1e-9
                    ),
                    "runtime_start_lots": runtime_lot,
                    "lot_matches_portfolio": (
                        runtime_lot is not None and math.isclose(runtime_lot, portfolio_lot, rel_tol=0, abs_tol=1e-9)
                    ),
                    "lot_matches_effective_lot": (
                        runtime_lot is not None and math.isclose(runtime_lot, tester_lot, rel_tol=0, abs_tol=1e-9)
                    ),
                    "magic": self._set_parameter(runtime_text, "EA_MagicNumber"),
                    "source_set": source.name,
                    "runtime_set": target.name,
                },
            }
        selected_summary = ", ".join(
            f"{member.get('symbol') or '?'}:{_member_strategy_id(member)}"
            for member in members
        )
        self._update(
            request["audit_key"], "testing", "Preparando Strategy Tester.",
            f"Variante {request['portfolio_type']} seleccionada con {len(members)} estrategias: {selected_summary}",
        )
        template = configparser.ConfigParser(interpolation=None)
        template.optionxform = str
        template.read_dict({
            "Common": {"Login": request["tester_login"], "Password": request["tester_password"], "Server": request["tester_server"]},
            "Tester": {
                "Expert": "", "Symbol": "XAUUSD", "Period": "H1", "Model": "4",
                "FromDate": period_start.strftime("%Y.%m.%d"), "ToDate": period_end.strftime("%Y.%m.%d"),
                "Deposit": str(float(detail.get("capital") or 1000)), "Currency": "EUR", "Leverage": "1:500",
                "Optimization": "0", "Visual": "0", "ReplaceReport": "1", "ShutdownTerminal": "1", "Report": "",
            },
        })
        template_path = work / "tester.ini"
        with template_path.open("w", encoding="utf-8", newline="\n") as handle:
            template.write(handle)
        wrapper = (
            "import sys,run_tests; from pathlib import Path; "
            f"run_tests.REPORT_DIR=Path({str(reports_dir)!r}); run_tests.CONFIG_DIR=Path({str(configs_dir)!r}); "
            f"run_tests.LOG_DIR=Path({str(logs_dir)!r}); sys.argv=['run_tests.py']+sys.argv[1:]; "
            "raise SystemExit(run_tests.main())"
        )
        tester_mt5, tester_section, tester_profile, tester_pids = self._login_terminal(
            request["tester_login"], request["tester_password"], request["tester_server"]
        )
        tester_mt5.shutdown()
        # Cierre ordenado, no `taskkill /F`: este terminal es el que el pipeline
        # reutiliza para cada backtest. Matarlo antes de que MT5 guarde su
        # configuración le borra la cuenta y el Strategy Tester se queda en
        # «not synchronized with trade server» para siempre.
        self._close_terminal_pids_gracefully(tester_pids)
        selected_profiles = self._tester_terminal_pool(tester_section, tester_profile, len(set_files))
        workers = len(selected_profiles)
        if not selected_profiles:
            raise ValueError("No hay terminales habilitadas para ejecutar el Strategy Tester")
        journal_snapshot = self._main_journal_snapshot(selected_profiles)
        terminal_validations = self._verify_tester_terminals(request, selected_profiles)
        verified_summary = ", ".join(
            f"{row['terminal']} → {row['login']} ({row['server']})"
            for row in terminal_validations
        )
        self._update(
            request["audit_key"], "testing", "Cuenta tester confirmada en todas las terminales.",
            f"Login tester verificado por MT5: {verified_summary}",
        )
        terminal_config = configparser.ConfigParser(interpolation=None)
        terminal_config.optionxform = str
        terminal_config["Multiterminal"] = {
            "enabled": "1", "workers": str(workers),
            "broker": str(tester_profile.get("broker") or self.owner.config.get("broker") or "ICTRADING"),
        }
        terminal_names: list[str] = []
        for index, (section, profile) in enumerate(selected_profiles, 1):
            terminal_config[f"Terminal.{index}"] = {**profile, "enabled": "1"}
            terminal_names.append(str(profile.get("name") or section))
            self._remember_real_account_terminal(request["audit_key"], section, profile)
        tester_execution = {
            "portfolio_type": request["portfolio_type"],
            "set_count": len(set_files),
            "workers": workers,
            "terminal_profiles": terminal_names,
            "terminal_validations": terminal_validations,
        }
        self._update(
            request["audit_key"], "testing", "Ejecutando Strategy Tester en paralelo.",
            f"Solo variante {request['portfolio_type']}: {len(set_files)} sets repartidos entre "
            f"{workers} terminales ({', '.join(terminal_names)})",
        )
        terminal_config_path = work / "terminals.ini"
        with terminal_config_path.open("w", encoding="utf-8", newline="\n") as handle:
            terminal_config.write(handle)
        command = [
            sys.executable, "-u", "-c", wrapper, "--template", str(template_path),
            "--multi-terminal", "--terminals-config", str(terminal_config_path), "--max-workers", str(workers),
            "--infer-tester-from-set", "--prefer-set-path-timeframe", "--model", "4",
            "--from-date", period_start.strftime("%Y.%m.%d"), "--to-date", period_end.strftime("%Y.%m.%d"),
        ]
        for set_file in set_files:
            command.extend(["--set-file", str(set_file)])
        try:
            completed = subprocess.run(
                command, cwd=str(Path(str(self.owner.config["project_dir"])).resolve()),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=86400,
            )
        finally:
            # Los INI contienen la contraseña del tester: nunca se conservan en el agente.
            for secret_file in (template_path, terminal_config_path):
                try:
                    secret_file.unlink(missing_ok=True)
                except OSError:
                    pass
            if configs_dir.is_dir():
                for secret_file in configs_dir.iterdir():
                    try:
                        if secret_file.is_file():
                            secret_file.unlink()
                    except OSError:
                        pass
            _redact_log_files(
                logs_dir, request.get("source_password", ""), request.get("tester_password", "")
            )
            try:
                self._capture_main_journals(
                    selected_profiles, journal_snapshot, logs_dir, terminal_validations, request
                )
            except Exception as exc:
                safe_error = _redact_runner_output(
                    str(exc), request.get("source_password", ""),
                    request.get("tester_password", ""), request.get("restore_password", ""),
                )
                for row in terminal_validations:
                    row["journal_captured"] = False
                    row["journal_error"] = safe_error
        runner_output = _redact_runner_output(
            completed.stdout or "", request.get("source_password", ""), request.get("tester_password", "")
        )
        (work / "runner.log").write_text(runner_output, encoding="utf-8")
        captured = [row["terminal"] for row in terminal_validations if row.get("journal_captured")]
        self._update(
            request["audit_key"], "testing", "Journals principales de MT5 capturados.",
            "Journal principal guardado para: " + (", ".join(captured) if captured else "ninguna terminal"),
        )
        if completed.returncode:
            tail = "\n".join(runner_output.splitlines()[-20:])
            raise RuntimeError(f"Strategy Tester terminó con código {completed.returncode}: {tail}")
        tester_trades: list[dict[str, Any]] = []
        qualities: list[float] = []
        strategies: dict[str, int] = {}
        strategy_artifacts: list[dict[str, Any]] = []
        for stem, prepared in member_by_stem.items():
            member = prepared["member"]
            candidates = [reports_dir / f"{stem}.htm", reports_dir / f"{stem}.html"]
            report_path = next((path for path in candidates if path.is_file()), None)
            if report_path is None:
                raise RuntimeError(f"MT5 no generó el reporte de {member.get('set_name') or stem}")
            report = parse_report(report_path)
            quality = _metric_number(report.metrics, "History Quality", "Calidad del historial")
            if quality is not None:
                qualities.append(quality)
            strategy = _member_strategy_id(member, stem)
            strategies[strategy] = len(report.trades)
            observed_trade_volumes = sorted({round(float(trade.size), 8) for trade in report.trades})
            runtime_lot = prepared["artifact"].get("runtime_start_lots")
            artifact = dict(prepared["artifact"])
            artifact.update(
                report_file=report_path.name,
                report_symbol=report.symbol,
                tester_trades=len(report.trades),
                history_quality_pct=quality,
                observed_trade_volumes=observed_trade_volumes,
                report_volumes_match_start_lots=(
                    all(math.isclose(value, runtime_lot, rel_tol=0, abs_tol=1e-9) for value in observed_trade_volumes)
                    if observed_trade_volumes and runtime_lot is not None else None
                ),
            )
            strategy_artifacts.append(artifact)
            self._update(
                request["audit_key"], "testing", "Leyendo reportes del Strategy Tester.",
                f"Reporte {report.symbol} / {strategy}: {len(report.trades)} operaciones, "
                f"History Quality {quality if quality is not None else 'no informada'}",
            )
            for trade in report.trades:
                open_time = trade.open_time.replace(tzinfo=timezone.utc) if trade.open_time.tzinfo is None else trade.open_time
                close_time = trade.close_time.replace(tzinfo=timezone.utc) if trade.close_time.tzinfo is None else trade.close_time
                tester_trades.append({
                    "strategy": strategy, "symbol": report.symbol, "side": trade.trade_type.casefold(),
                    "open_time": open_time, "close_time": close_time, "open_price": trade.open_price,
                    "close_price": trade.close_price, "volume": trade.size, "profit": trade.profit_loss,
                })
        return tester_trades, qualities, strategies, strategy_artifacts, tester_execution
