from __future__ import annotations

import configparser
from dataclasses import dataclass
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_tests_parallel import runner_failure_summary

from .live_audit_helpers import audit_set_name, _member_strategy_id, _metric_number, _read_set_text, _redact_log_files, _redact_runner_output
from .live_audit_symbols import normalize_live_audit_set_symbols


@dataclass
class _TesterTerminals:
    """Terminales elegidas para el backtest y su estado verificado."""

    profiles: list[tuple[str, dict[str, Any]]]
    tester_profile: dict[str, Any]
    journal_snapshot: Any
    validations: list[dict[str, Any]]

    @property
    def workers(self) -> int:
        """Numero de terminales que ejecutan en paralelo."""
        return len(self.profiles)


class LiveAuditTesterMixin:
    """Ejecucion del tester sobre los sets del portafolio auditado."""

    def _audit_work_dirs(self, request: dict[str, Any], audit_id: str) -> tuple[Path, ...]:
        """Crea el arbol de trabajo de esta auditoria y devuelve sus carpetas."""
        work = self.runtime_dir / f"audit_{request['audit_key']}" / audit_id
        dirs = tuple(work / name for name in ("sets", "reports", "configs", "logs"))
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
        return (work, *dirs)

    def _member_lot_artifact(
        self, member: dict[str, Any], request: dict[str, Any], source: Path, target: Path,
        runtime_text: str, runtime_lot: float | None, lots: tuple[float, float, float | None, float | None, int],
    ) -> dict[str, Any]:
        """Ficha de lotaje de un set: lo configurado, lo ejecutado y sus desvios."""
        portfolio_lot, tester_lot, volume_min, volume_step, units = lots
        strategy = _member_strategy_id(member, target.stem)
        configured_real_lots = request.get("real_strategy_lots") or {}
        return {
            "strategy": strategy,
            "symbol": str(member.get("symbol") or ""),
            "configured_lot": portfolio_lot,
            "tester_lot": tester_lot,
            "real_account_lot": float(configured_real_lots.get(strategy, tester_lot)),
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
        }

    def _prepare_member_set(
        self, index: int, member: dict[str, Any], request: dict[str, Any],
        sets_dir: Path, volume_rules: Any,
    ) -> tuple[Path, dict[str, Any]]:
        """Copia el set del miembro al area de trabajo con el lotaje del tester."""
        source = self._resolve_set(str(member.get("set_path") or member.get("set_id") or ""))
        text, set_encoding = _read_set_text(source)
        lots = self._tester_lot(member, volume_rules)
        text = self._set_value(text, "StartLots", f"{lots[1]:.8f}".rstrip("0").rstrip("."))
        text = normalize_live_audit_set_symbols(text, request["tester_server"])
        work = sets_dir.parent
        target = sets_dir / audit_set_name(
            index, source.name, sets_dir, work / "reports", work / "configs"
        )
        target.write_text(text, encoding=set_encoding, newline="\n")
        runtime_text, _runtime_encoding = _read_set_text(target)
        try:
            runtime_lot = float(self._set_parameter(runtime_text, "StartLots"))
        except (TypeError, ValueError):
            runtime_lot = None
        artifact = self._member_lot_artifact(
            member, request, source, target, runtime_text, runtime_lot, lots
        )
        return target, {"member": member, "artifact": artifact}

    def _prepare_member_sets(
        self, request: dict[str, Any], members: list[dict[str, Any]], sets_dir: Path
    ) -> tuple[list[Path], dict[str, dict[str, Any]]]:
        """Prepara los sets de todos los miembros del portafolio auditado."""
        set_files: list[Path] = []
        member_by_stem: dict[str, dict[str, Any]] = {}
        volume_rules = self._broker_volume_rules()
        for index, member in enumerate(members, 1):
            target, prepared = self._prepare_member_set(index, member, request, sets_dir, volume_rules)
            set_files.append(target)
            member_by_stem[target.stem] = prepared
        return set_files, member_by_stem

    def _write_tester_template(
        self, request: dict[str, Any], detail: dict[str, Any], work: Path,
        period_start: datetime, period_end: datetime,
    ) -> Path:
        """Plantilla .ini del Strategy Tester para el periodo auditado."""
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
        return template_path

    def _prepare_tester_terminals(
        self, request: dict[str, Any], set_files: list[Path]
    ) -> _TesterTerminals:
        """Reserva y verifica las terminales que ejecutaran el backtest."""
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
        if not selected_profiles:
            raise ValueError("No hay terminales habilitadas para ejecutar el Strategy Tester")
        journal_snapshot = self._main_journal_snapshot(selected_profiles)
        validations = self._verify_tester_terminals(request, selected_profiles)
        verified_summary = ", ".join(
            f"{row['terminal']} → {row['login']} ({row['server']})"
            for row in validations
        )
        self._update(
            request["audit_key"], "testing", "Cuenta tester confirmada en todas las terminales.",
            f"Login tester verificado por MT5: {verified_summary}",
        )
        return _TesterTerminals(selected_profiles, tester_profile, journal_snapshot, validations)

    def _write_terminal_config(
        self, request: dict[str, Any], work: Path, terminals: _TesterTerminals
    ) -> tuple[Path, list[str]]:
        """Fichero de terminales del lanzador, con un bloque por worker."""
        terminal_config = configparser.ConfigParser(interpolation=None)
        terminal_config.optionxform = str
        terminal_config["Multiterminal"] = {
            "enabled": "1", "workers": str(terminals.workers),
            "broker": str(
                terminals.tester_profile.get("broker") or self.owner.config.get("broker") or "ICTRADING"
            ),
        }
        terminal_names: list[str] = []
        for index, (section, profile) in enumerate(terminals.profiles, 1):
            terminal_config[f"Terminal.{index}"] = {**profile, "enabled": "1"}
            terminal_names.append(str(profile.get("name") or section))
            self._remember_real_account_terminal(request["audit_key"], section, profile)
        terminal_config_path = work / "terminals.ini"
        with terminal_config_path.open("w", encoding="utf-8", newline="\n") as handle:
            terminal_config.write(handle)
        return terminal_config_path, terminal_names

    @staticmethod
    def _tester_command(
        wrapper: str, template_path: Path, terminal_config_path: Path, workers: int,
        set_files: list[Path], period_start: datetime, period_end: datetime,
    ) -> list[str]:
        """Linea de comandos del lanzador de backtests para esta auditoria."""
        command = [
            sys.executable, "-u", "-c", wrapper, "--template", str(template_path),
            "--multi-terminal", "--terminals-config", str(terminal_config_path), "--max-workers", str(workers),
            "--infer-tester-from-set", "--prefer-set-path-timeframe", "--model", "4",
            "--from-date", period_start.strftime("%Y.%m.%d"), "--to-date", period_end.strftime("%Y.%m.%d"),
        ]
        for set_file in set_files:
            command.extend(["--set-file", str(set_file)])
        return command

    @staticmethod
    def _discard_tester_secrets(secret_files: tuple[Path, ...], configs_dir: Path) -> None:
        """Borra los INI con la contraseña del tester y los configs generados."""
        for secret_file in secret_files:
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

    def _capture_tester_journals(
        self, request: dict[str, Any], terminals: _TesterTerminals, logs_dir: Path
    ) -> None:
        """Guarda los journals principales y anota el fallo si no se pudo."""
        try:
            self._capture_main_journals(
                terminals.profiles, terminals.journal_snapshot, logs_dir,
                terminals.validations, request,
            )
        except Exception as exc:
            safe_error = _redact_runner_output(
                str(exc), request.get("source_password", ""),
                request.get("tester_password", ""), request.get("restore_password", ""),
            )
            for row in terminals.validations:
                row["journal_captured"] = False
                row["journal_error"] = safe_error

    def _run_tester_command(
        self, request: dict[str, Any], command: list[str], terminals: _TesterTerminals,
        secret_files: tuple[Path, ...], configs_dir: Path, logs_dir: Path,
    ) -> subprocess.CompletedProcess:
        """Lanza el backtest y limpia siempre secretos y journals al terminar."""
        try:
            return subprocess.run(
                command, cwd=str(Path(str(self.owner.config["project_dir"])).resolve()),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=86400,
            )
        finally:
            # Los INI contienen la contraseña del tester: nunca se conservan en el agente.
            self._discard_tester_secrets(secret_files, configs_dir)
            _redact_log_files(
                logs_dir, request.get("source_password", ""), request.get("tester_password", "")
            )
            self._capture_tester_journals(request, terminals, logs_dir)

    def _store_runner_output(
        self, request: dict[str, Any], work: Path, completed: subprocess.CompletedProcess,
        terminals: _TesterTerminals,
    ) -> None:
        """Guarda la salida censurada del lanzador y confirma los journals."""
        runner_output = _redact_runner_output(
            completed.stdout or "", request.get("source_password", ""), request.get("tester_password", "")
        )
        (work / "runner.log").write_text(runner_output, encoding="utf-8")
        captured = [row["terminal"] for row in terminals.validations if row.get("journal_captured")]
        self._update(
            request["audit_key"], "testing", "Journals principales de MT5 capturados.",
            "Journal principal guardado para: " + (", ".join(captured) if captured else "ninguna terminal"),
        )
        if completed.returncode:
            tail = runner_failure_summary(runner_output)
            raise RuntimeError(f"Strategy Tester terminó con código {completed.returncode}: {tail}")

    @staticmethod
    def _tester_wrapper(reports_dir: Path, configs_dir: Path, logs_dir: Path) -> str:
        """Lanzador del runner con sus carpetas apuntando al area de la auditoria.

        El runner esta partido en modulos y cada uno conserva su propia
        referencia a REPORT_DIR/CONFIG_DIR/LOG_DIR, asi que reapuntar solo la
        fachada `run_tests` dejaba los reportes y los INI del tester en las
        carpetas del proyecto y la auditoria no encontraba ningun reporte.
        """
        return (
            "import sys, run_tests; from pathlib import Path; "
            f"_dirs = (('REPORT_DIR', Path({str(reports_dir)!r})), "
            f"('CONFIG_DIR', Path({str(configs_dir)!r})), "
            f"('LOG_DIR', Path({str(logs_dir)!r}))); "
            "[setattr(module, name, value) "
            "for key, module in list(sys.modules.items()) "
            "if key == 'run_tests' or key.startswith('run_tests_') "
            "for name, value in _dirs if hasattr(module, name)]; "
            "sys.argv = ['run_tests.py'] + sys.argv[1:]; "
            "raise SystemExit(run_tests.main())"
        )

    @staticmethod
    def _report_artifact(
        prepared: dict[str, Any], report: Any, report_path: Path, quality: float | None
    ) -> dict[str, Any]:
        """Ficha del set con lo que el reporte del tester confirma de el."""
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
        return artifact

    @staticmethod
    def _report_trades(strategy: str, report: Any) -> list[dict[str, Any]]:
        """Operaciones del reporte normalizadas a UTC."""
        trades: list[dict[str, Any]] = []
        for trade in report.trades:
            open_time = trade.open_time.replace(tzinfo=timezone.utc) if trade.open_time.tzinfo is None else trade.open_time
            close_time = trade.close_time.replace(tzinfo=timezone.utc) if trade.close_time.tzinfo is None else trade.close_time
            trades.append({
                "strategy": strategy, "symbol": report.symbol, "side": trade.trade_type.casefold(),
                "open_time": open_time, "close_time": close_time, "open_price": trade.open_price,
                "close_price": trade.close_price, "volume": trade.size, "profit": trade.profit_loss,
            })
        return trades

    def _collect_tester_reports(
        self, request: dict[str, Any], member_by_stem: dict[str, dict[str, Any]], reports_dir: Path
    ) -> tuple[list[dict[str, Any]], list[float], dict[str, int], list[dict[str, Any]]]:
        """Lee el reporte de cada set y reune operaciones, calidad y fichas."""
        from portfolio_manager.mt5_report import parse_report

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
            strategy_artifacts.append(self._report_artifact(prepared, report, report_path, quality))
            self._update(
                request["audit_key"], "testing", "Leyendo reportes del Strategy Tester.",
                f"Reporte {report.symbol} / {strategy}: {len(report.trades)} operaciones, "
                f"History Quality {quality if quality is not None else 'no informada'}",
            )
            tester_trades.extend(self._report_trades(strategy, report))
        return tester_trades, qualities, strategies, strategy_artifacts

    def _run_tester(
        self, request: dict[str, Any], audit_id: str, period_start: datetime, period_end: datetime
    ) -> tuple[list[dict[str, Any]], list[float], dict[str, int], list[dict[str, Any]], dict[str, Any]]:
        detail, members = self._portfolio_members(request["portfolio_id"], request["portfolio_type"])
        if not members:
            raise ValueError("El portafolio no contiene estrategias")
        work, sets_dir, reports_dir, configs_dir, logs_dir = self._audit_work_dirs(request, audit_id)
        set_files, member_by_stem = self._prepare_member_sets(request, members, sets_dir)
        selected_summary = ", ".join(
            f"{member.get('symbol') or '?'}:{_member_strategy_id(member)}"
            for member in members
        )
        self._update(
            request["audit_key"], "testing", "Preparando Strategy Tester.",
            f"Variante {request['portfolio_type']} seleccionada con {len(members)} estrategias: {selected_summary}",
        )
        template_path = self._write_tester_template(request, detail, work, period_start, period_end)
        wrapper = self._tester_wrapper(reports_dir, configs_dir, logs_dir)
        terminals = self._prepare_tester_terminals(request, set_files)
        terminal_config_path, terminal_names = self._write_terminal_config(request, work, terminals)
        tester_execution = {
            "portfolio_type": request["portfolio_type"],
            "set_count": len(set_files),
            "workers": terminals.workers,
            "terminal_profiles": terminal_names,
            "terminal_validations": terminals.validations,
        }
        self._update(
            request["audit_key"], "testing", "Ejecutando Strategy Tester en paralelo.",
            f"Solo variante {request['portfolio_type']}: {len(set_files)} sets repartidos entre "
            f"{terminals.workers} terminales ({', '.join(terminal_names)})",
        )
        command = self._tester_command(
            wrapper, template_path, terminal_config_path, terminals.workers,
            set_files, period_start, period_end,
        )
        completed = self._run_tester_command(
            request, command, terminals, (template_path, terminal_config_path), configs_dir, logs_dir
        )
        self._store_runner_output(request, work, completed, terminals)
        tester_trades, qualities, strategies, strategy_artifacts = self._collect_tester_reports(
            request, member_by_stem, reports_dir
        )
        return tester_trades, qualities, strategies, strategy_artifacts, tester_execution
