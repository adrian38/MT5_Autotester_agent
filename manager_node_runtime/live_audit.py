from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now
from .live_audit_helpers import (  # noqa: F401  fachada del modulo
    PROGRESS,
    RUNNING_STATUSES,
    STATUS_LABELS,
    _as_float,
    _as_int,
    _audit_period,
    _drawdown,
    _effective_price_tolerance,
    _member_strategy_id,
    _metric_number,
    _normalize_real_strategy_lots,
    _pnl_comparison,
    _read_set_text,
    _redact_log_files,
    _redact_runner_output,
    _safe_state,
    _trade_view,
    normalize_request,
)
from .live_audit_terminals import LiveAuditTerminalsMixin
from .live_audit_extract import LiveAuditExtractMixin
from .live_audit_tester import LiveAuditTesterMixin
from .live_audit_compare import LiveAuditCompareMixin


class LiveAuditController(
    LiveAuditTerminalsMixin,
    LiveAuditExtractMixin,
    LiveAuditTesterMixin,
    LiveAuditCompareMixin,
):
    """Ejecuta auditorías en el agente sin persistir las credenciales recibidas."""

    history_sync_attempts = 6
    history_sync_delay_seconds = 1.0

    def __init__(self, owner: Any, runtime_dir: Path) -> None:
        self.owner = owner
        self.runtime_dir = runtime_dir / "live_audits"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "state.json"
        self.lock = threading.RLock()
        self.states: dict[str, dict[str, Any]] = {}
        # Terminales donde esta auditoría activó la cuenta real. MT5 recuerda la
        # última cuenta de cada terminal, así que hay que devolverlos a la cuenta
        # configurada para restauración antes de soltarlos: ver `_restore_tester_login`.
        self.real_account_terminals: dict[str, list[dict[str, str]]] = {}
        if self.state_path.is_file():
            try:
                stored = load_json(self.state_path)
                if isinstance(stored, dict):
                    self.states = {str(key): dict(value) for key, value in stored.items() if isinstance(value, dict)}
                    for value in self.states.values():
                        if str(value.get("status")) in RUNNING_STATUSES:
                            value.update(status="failed", finished_at=utc_now(), error="Auditoría interrumpida al reiniciar el agente")
            except ValueError:
                pass
        self._persist()

    def _persist(self) -> None:
        save_json(self.state_path, self.states)

    def is_running(self) -> bool:
        with self.lock:
            return any(str(item.get("status")) in RUNNING_STATUSES for item in self.states.values())

    def all_states(self) -> dict[str, Any]:
        with self.lock:
            return {key: _safe_state(value) for key, value in self.states.items()}

    def state(self, audit_key: str | int) -> dict[str, Any]:
        with self.lock:
            key = str(audit_key)
            raw = self.states.get(key) or {"audit_key": key, "status": "idle"}
            return _safe_state(raw)

    def artifact_path(self, audit_key: str, audit_id: str, filename: str) -> Path:
        """Resuelve únicamente reportes de la ejecución visible de una auditoría."""
        if any(
            not value or len(value) > 255 or not all(char.isalnum() or char in "-_." for char in value)
            for value in (str(audit_key), str(audit_id))
        ):
            raise ValueError("Identificador de artefacto no válido")
        if not filename or Path(filename).name != filename:
            raise ValueError("Nombre de artefacto no válido")
        if Path(filename).suffix.casefold() not in {".htm", ".html", ".png", ".gif", ".jpg", ".jpeg"}:
            raise ValueError("Tipo de artefacto no permitido")
        with self.lock:
            raw = self.states.get(str(audit_key))
            if not raw or str(raw.get("audit_id") or "") != str(audit_id):
                raise FileNotFoundError("La ejecución solicitada no es la ejecución visible")
        reports_dir = (
            self.runtime_dir / f"audit_{audit_key}" / str(audit_id) / "reports"
        ).resolve()
        path = (reports_dir / filename).resolve()
        if path.parent != reports_dir or not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_request(payload)
        portfolio_id = request["portfolio_id"]
        audit_key = request["audit_key"]
        # Solo el UBS estable entra en este servicio. El mensual sigue congelado.
        self._portfolio_members(portfolio_id, request["portfolio_type"])
        with self.lock:
            if self.is_running():
                raise RuntimeError("Ya hay una auditoría utilizando las terminales del nodo")
            audit_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self.states[audit_key] = {
                "audit_key": audit_key, "portfolio_id": portfolio_id,
                "portfolio_type": request["portfolio_type"], "audit_id": audit_id, "status": "queued",
                "started_at": utc_now(), "finished_at": None, "error": None,
                "progress_text": "Preparando la auditoría en el nodo.",
                "log_lines": [
                    f"[{utc_now()}] Inicio {audit_key}: portafolio #{portfolio_id}, "
                    f"variante {request['portfolio_type']}, cuenta real {request['source_login']} "
                    f"({request['source_server']}), tester {request['tester_login']} ({request['tester_server']})"
                ],
                "last_result": (self.states.get(audit_key) or {}).get("last_result"),
            }
            self._persist()
        thread = threading.Thread(target=self._run, args=(request, audit_id), daemon=True)
        thread.start()
        return self.state(audit_key)

    def _update(self, audit_key: str, status: str, text: str, log: str | None = None, **changes: Any) -> None:
        with self.lock:
            raw = self.states[audit_key]
            raw.update(status=status, progress_text=text, **changes)
            if log:
                raw.setdefault("log_lines", []).append(f"[{utc_now()}] {log}")
            self._persist()

    def _log(self, audit_key: str, line: str) -> None:
        """Registra un hecho sin tocar el estado terminal ya publicado."""
        with self.lock:
            raw = self.states.get(audit_key)
            if raw is None:
                return
            raw.setdefault("log_lines", []).append(f"[{utc_now()}] {line}")
            self._persist()

    def _remember_real_account_terminal(
        self, audit_key: str, section: str, profile: dict[str, str]
    ) -> None:
        """Anota un terminal utilizado por la auditoría para restaurarlo al final."""
        path = str(profile.get("mt5_path") or "")
        if not path:
            return
        with self.lock:
            touched = self.real_account_terminals.setdefault(audit_key, [])
            if any(row["mt5_path"].casefold() == path.casefold() for row in touched):
                return
            touched.append({
                "section": section,
                "terminal": str(profile.get("name") or section),
                "mt5_path": path,
            })

    def _wait_for_pause(self, timeout: float = 180.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.owner.lock:
                status = str(self.owner.state.get("status") or "")
            if status == "paused":
                return True
            if status not in {"running", "stopping"}:
                return False
            time.sleep(0.25)
        raise TimeoutError("El pipeline no confirmó la pausa dentro del tiempo permitido")

    def _run(self, request: dict[str, Any], audit_id: str) -> None:
        portfolio_id = request["portfolio_id"]
        audit_key = request["audit_key"]
        paused_by_auditor = False
        terminal_status = "failed"
        unrestored: list[dict[str, Any]] = []
        with self.lock:
            self.real_account_terminals[audit_key] = []
        try:
            with self.owner.lock:
                job_status = str(self.owner.state.get("status") or "idle")
                has_process = self.owner.process is not None
            if has_process and job_status in {"running", "stopping"}:
                self._update(audit_key, "pausing", "Pausando el proceso activo.", "Pausa solicitada al pipeline activo")
                self.owner.pause()
                paused_by_auditor = self._wait_for_pause()
                if not paused_by_auditor:
                    raise RuntimeError("El proceso terminó sin confirmar la pausa; la auditoría no ocupó sus terminales")
            elif job_status in {"paused", "interrupted"}:
                self._update(audit_key, "queued", "El pipeline ya estaba pausado; se conservará así.", "Pausa previa del usuario detectada")

            self._update(audit_key, "extracting", "Extrayendo operaciones de la cuenta real.", "Conectando la cuenta real")
            period_start, period_end = _audit_period(request)
            reports_dir = self.runtime_dir / f"audit_{audit_key}" / audit_id / "reports"
            native_report_path = reports_dir / "real_account_mt5_report.html"
            real_trades, symbol_points, account = self._extract_real(
                request, period_start, period_end, native_report_path,
            )
            real_account_report = dict(account.pop("native_report", {}) or {})
            if not real_account_report.get("native_terminal_report"):
                raise RuntimeError("MT5 no entregó el HTML nativo del historial de la cuenta real")
            real_history_detail = dict(account.pop("history_detail", {}) or {})
            self._update(
                audit_key, "extracting", "Historial de la cuenta real sincronizado.",
                f"Cuenta MT5 verificada: login {account.get('login')}, servidor {account.get('server')}, "
                f"terminal {account.get('terminal_profile')}; "
                f"{real_history_detail.get('period_raw_deals', 0)} deals brutos, "
                f"{real_history_detail.get('closing_deals', 0)} cierres y "
                f"{real_history_detail.get('positions_recovered', 0)} apertura(s) anterior(es) recuperada(s) "
                f"tras {real_history_detail.get('sync_attempts', 0)} consulta(s). "
                f"HTML nativo {real_account_report.get('filename')} capturado por "
                f"{real_account_report.get('capture_terminal_profile') or account.get('terminal_profile')} "
                f"con periodo {real_account_report.get('period_mode')} "
                f"{real_account_report.get('period_start_date')} a {real_account_report.get('period_end_date')}, "
                f"{real_account_report.get('bytes', 0)} bytes, sha256 "
                f"{str(real_account_report.get('sha256') or '')[:16]}...",
            )
            self._update(
                audit_key, "testing", "Ejecutando el portafolio con ticks reales en el nodo.",
                f"{len(real_trades)} cierres reales reconstruidos antes del filtro del portafolio.",
            )
            tester_trades, qualities, strategies, strategy_artifacts, tester_execution = self._run_tester(
                request, audit_id, period_start, period_end
            )
            _detail, selected_members = self._portfolio_members(portfolio_id, request["portfolio_type"])
            volume_rules = self._broker_volume_rules()
            symbols_by_strategy: dict[str, set[str]] = {}
            for trade in tester_trades:
                strategy = str(trade.get("strategy") or "")
                symbol = str(trade.get("symbol") or "").casefold()
                if strategy and symbol:
                    symbols_by_strategy.setdefault(strategy, set()).add(symbol)
            for artifact in strategy_artifacts:
                strategy = str(artifact.get("strategy") or "")
                symbol = str(artifact.get("report_symbol") or "").casefold()
                if strategy and symbol:
                    symbols_by_strategy.setdefault(strategy, set()).add(symbol)
            real_strategy_lots = request.get("real_strategy_lots") or {}
            signatures: set[tuple[str, float]] = set()
            for member in selected_members:
                strategy = _member_strategy_id(member)
                try:
                    _configured_lot, effective_lot, _volume_min, _volume_step, _units = self._tester_lot(
                        member, volume_rules,
                    )
                except (TypeError, ValueError):
                    continue
                real_lot = float(real_strategy_lots.get(strategy, effective_lot))
                symbols = symbols_by_strategy.get(strategy) or {
                    str(member.get("symbol") or "").casefold()
                }
                signatures.update(
                    (symbol, round(real_lot, 8)) for symbol in symbols if symbol and real_lot > 0
                )
            if signatures:
                before_filter = len(real_trades)
                real_trades = [
                    trade for trade in real_trades
                    if (
                        str(trade.get("symbol") or "").casefold(),
                        round(float(trade.get("volume") or 0), 8),
                    ) in signatures
                ]
                ignored = before_filter - len(real_trades)
                self._update(
                    audit_key, "extracting", "Filtrando operaciones de la variante seleccionada.",
                    f"Filtro por símbolo/lote real configurado: {len(real_trades)} cierres del portafolio, "
                    f"{ignored} cierres ajenos ignorados; firmas {sorted(signatures)}",
                )
                real_history_detail["portfolio_closures"] = len(real_trades)
                real_history_detail["foreign_closures_ignored"] = ignored
            real_groups: dict[str, int] = {}
            for trade in real_trades:
                key = f"{trade.get('symbol') or '?'} / lote {float(trade.get('volume') or 0):g}"
                real_groups[key] = real_groups.get(key, 0) + 1
            real_summary = ", ".join(f"{key}: {count}" for key, count in sorted(real_groups.items())) or "sin cierres"
            tester_groups: dict[str, int] = {}
            for trade in tester_trades:
                key = f"{trade.get('symbol') or '?'} / {trade.get('strategy') or '?'}"
                tester_groups[key] = tester_groups.get(key, 0) + 1
            tester_summary = ", ".join(f"{key}: {count}" for key, count in sorted(tester_groups.items())) or "sin operaciones"
            self._update(
                audit_key, "comparing", "Comparando cuenta real y Strategy Tester.",
                f"{len(tester_trades)} operaciones del tester ({tester_summary})",
            )
            quality = min(qualities) if qualities else None
            if quality is None or quality < request["min_tick_history_quality_pct"]:
                result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
                result.update(
                    status="not_comparable", status_label="NO COMPARABLE", matched_trades=0,
                    discrepancies=0, stalled_strategies=0,
                    summary=("MT5 no informó History Quality." if quality is None else
                             f"History Quality {quality:.2f}% inferior al mínimo {request['min_tick_history_quality_pct']:.2f}%.")
                )
                final_status = "not_comparable"
            else:
                comparison = self._compare(real_trades, tester_trades, symbol_points, request, strategies)
                result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
                result.update(comparison)
                invalid_tester = sum((comparison.get("comparison_detail") or {}).get("tester_data_issues", {}).values())
                result["summary"] = (
                    f"{comparison['matched_trades']} parejas alineadas, "
                    f"{comparison['within_tolerance_trades']} dentro de todas las tolerancias y "
                    f"{comparison['discrepancies']} discrepancias; "
                    f"{comparison['stalled_strategies']} estrategia(s) sin continuidad"
                    + (f"; {invalid_tester} operación(es) tester con tiempos inválidos." if invalid_tester else ".")
                )
                result["status"] = result["status_label"] = "completed"
                result["status_label"] = "COMPLETADA"
                final_status = "completed"
            result["account"] = account
            result["real_history_detail"] = real_history_detail
            result["audit_key"] = audit_key
            result["audit_id"] = audit_id
            result["portfolio_type"] = request["portfolio_type"]
            result["strategy_artifacts"] = strategy_artifacts
            result["tester_execution"] = tester_execution
            result["real_account_report"] = real_account_report
            detail = result.get("comparison_detail") or {}
            detail_log = "; ".join(
                f"{key}={detail[key]}" for key in (
                    "matched_by_strategy", "within_tolerance_by_strategy", "deviating_by_strategy",
                    "missing_by_strategy", "unmatched_real", "deviation_reasons", "tester_data_issues",
                ) if detail.get(key)
            )
            terminal_status = final_status
            self._update(
                audit_key, "finalizing", "Restaurando las cuentas de todas las terminales utilizadas.",
                f"Comparación finalizada" + (f": {detail_log}" if detail_log else ""), last_result=result,
            )
        except Exception as exc:
            terminal_status = "failed"
            self._update(
                audit_key, "finalizing", f"La auditoría falló; restaurando las terminales: {exc}", str(exc),
                error=str(exc),
            )
        finally:
            # La cuenta activa de un terminal es estado persistente de MT5. Cada
            # terminal que tocó la auditoría se devuelve a la cuenta independiente
            # configurada para restauración antes de reanudar el pipeline.
            try:
                restored = self._restore_tester_login(request)
            except Exception as exc:
                # Un fallo aquí no puede tapar el resultado de la auditoría.
                restored = [{
                    "terminal": "desconocido", "mt5_path": "", "section": "",
                    "expected_login": str(request.get("restore_login") or ""),
                    "expected_server": str(request.get("restore_server") or ""),
                    "login": None, "server": None, "restored": False,
                    "error": _redact_runner_output(
                        str(exc), str(request.get("tester_password") or ""),
                        str(request.get("source_password") or ""),
                        str(request.get("restore_password") or ""),
                    ),
                }]
            with self.lock:
                self.real_account_terminals.pop(audit_key, None)
            if restored:
                unrestored = [row for row in restored if not row["restored"]]
                self._log(audit_key, "Cuenta dejada en cada terminal: " + "; ".join(
                    f"{row['terminal']} → {row['expected_login']} ({row['expected_server']})"
                    if row["restored"] else
                    f"{row['terminal']} → SIN RESTAURAR: {row['error']}"
                    for row in restored
                ))
                with self.lock:
                    raw = self.states[audit_key]
                    raw["terminal_restore"] = restored
                    last_result = raw.get("last_result")
                    if isinstance(last_result, dict) and str(last_result.get("audit_id") or "") == audit_id:
                        last_result["terminal_restore"] = restored
                    self._persist()
            if paused_by_auditor:
                try:
                    self._update(audit_key, "resuming", "Reanudando el proceso que pausó el auditor.", "Reanudación solicitada")
                    self.owner.resume()
                except Exception as exc:
                    self._update(audit_key, "failed", f"La auditoría terminó, pero no se pudo reanudar: {exc}", str(exc), error=str(exc))
            with self.lock:
                raw = self.states[audit_key]
                if str(raw.get("status")) in {"finalizing", "resuming"}:
                    raw.update(
                        status=terminal_status,
                        progress_text=str(
                            (raw.get("last_result") or {}).get("summary")
                            or raw.get("error") or "Auditoría finalizada."
                        ),
                    )
                if unrestored:
                    # No cambia el veredicto de la comparación, pero el usuario tiene
                    # que enterarse sin abrir los logs: el terminal quedó en otra cuenta.
                    raw["progress_text"] = str(raw.get("progress_text") or "") + (
                        " ⚠ "
                        + ", ".join(str(row["terminal"]) for row in unrestored)
                        + f" no quedó en la cuenta configurada {request['restore_login']}."
                    )
                raw["finished_at"] = utc_now()
                self._persist()
            if getattr(self.owner, "queue", None):
                self.owner._schedule_queue_drain()
