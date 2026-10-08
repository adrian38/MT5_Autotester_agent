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
    single_variant_mode,
)
from .live_audit_symbols import audit_symbol_key
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
    tester_login_settle_seconds = 30.0
    account_probe_seconds = 2.0

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
                "last_payload": (self.states.get(audit_key) or {}).get("last_payload"),
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

    def _pause_active_job(self, audit_key: str) -> bool:
        """Pausa el pipeline si estaba corriendo; True si lo pauso la auditoria."""
        with self.owner.lock:
            job_status = str(self.owner.state.get("status") or "idle")
            has_process = self.owner.process is not None
        if has_process and job_status in {"running", "stopping"}:
            self._update(audit_key, "pausing", "Pausando el proceso activo.", "Pausa solicitada al pipeline activo")
            self.owner.pause()
            paused_by_auditor = self._wait_for_pause()
            if not paused_by_auditor:
                raise RuntimeError("El proceso terminó sin confirmar la pausa; la auditoría no ocupó sus terminales")
            return paused_by_auditor
        if job_status in {"paused", "interrupted"}:
            self._update(audit_key, "queued", "El pipeline ya estaba pausado; se conservará así.", "Pausa previa del usuario detectada")
        return False

    def _log_real_account_sync(
        self, audit_key: str, account: dict[str, Any],
        real_account_report: dict[str, Any], real_history_detail: dict[str, Any],
    ) -> None:
        """Deja constancia de la cuenta, el historial y el HTML nativo capturados."""
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

    def _extract_real_account(
        self, request: dict[str, Any], audit_id: str, period_start: datetime, period_end: datetime
    ) -> tuple[
        list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any],
        dict[str, Any], list[dict[str, Any]],
    ]:
        """Trae de MT5 el historial real del periodo junto con su HTML nativo."""
        audit_key = request["audit_key"]
        reports_dir = self.runtime_dir / f"audit_{audit_key}" / audit_id / "reports"
        native_report_path = reports_dir / "real_account_mt5_report.html"
        real_trades, symbol_points, account = self._extract_real(
            request, period_start, period_end, native_report_path,
        )
        real_account_report = dict(account.pop("native_report", {}) or {})
        if not real_account_report.get("native_terminal_report"):
            raise RuntimeError("MT5 no entregó el HTML nativo del historial de la cuenta real")
        real_history_detail = dict(account.pop("history_detail", {}) or {})
        open_positions = list(real_history_detail.pop("open_positions_at_period_end", []) or [])
        real_history_detail["open_positions_at_period_end"] = [
            _trade_view(position) for position in open_positions
        ]
        self._log_real_account_sync(audit_key, account, real_account_report, real_history_detail)
        return (
            real_trades, symbol_points, account, real_account_report,
            real_history_detail, open_positions,
        )

    @staticmethod
    def _symbols_by_strategy(
        tester_trades: list[dict[str, Any]], strategy_artifacts: list[dict[str, Any]]
    ) -> dict[str, set[str]]:
        """Simbolos que el tester asocio a cada estrategia del portafolio."""
        symbols_by_strategy: dict[str, set[str]] = {}
        pairs = [(row.get("strategy"), row.get("symbol")) for row in tester_trades]
        pairs += [(row.get("strategy"), row.get("report_symbol")) for row in strategy_artifacts]
        for raw_strategy, raw_symbol in pairs:
            strategy = str(raw_strategy or "")
            symbol = audit_symbol_key(raw_symbol)
            if strategy and symbol:
                symbols_by_strategy.setdefault(strategy, set()).add(symbol)
        return symbols_by_strategy

    def _portfolio_trade_signatures(
        self, request: dict[str, Any], selected_members: list[dict[str, Any]],
        tester_trades: list[dict[str, Any]], strategy_artifacts: list[dict[str, Any]],
    ) -> set[tuple[str, float]]:
        """Pares simbolo/lote con los que la variante opera en la cuenta real."""
        volume_rules = self._broker_volume_rules()
        symbols_by_strategy = self._symbols_by_strategy(tester_trades, strategy_artifacts)
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
                audit_symbol_key(member.get("symbol"))
            }
            signatures.update(
                (symbol, round(real_lot, 8)) for symbol in symbols if symbol and real_lot > 0
            )
        return signatures

    def _filter_real_trades(
        self, audit_key: str, real_trades: list[dict[str, Any]],
        signatures: set[tuple[str, float]], real_history_detail: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Deja solo los cierres reales que corresponden a la variante auditada."""
        if not signatures:
            return real_trades
        before_filter = len(real_trades)
        filtered = [
            trade for trade in real_trades
            if (
                audit_symbol_key(trade.get("symbol")),
                round(float(trade.get("volume") or 0), 8),
            ) in signatures
        ]
        ignored = before_filter - len(filtered)
        self._update(
            audit_key, "extracting", "Filtrando operaciones de la variante seleccionada.",
            f"Filtro por símbolo/lote real configurado: {len(filtered)} cierres del portafolio, "
            f"{ignored} cierres ajenos ignorados; firmas {sorted(signatures)}",
        )
        real_history_detail["portfolio_closures"] = len(filtered)
        real_history_detail["foreign_closures_ignored"] = ignored
        return filtered

    def _not_comparable_result(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        real_trades: list[dict[str, Any]], tester_trades: list[dict[str, Any]], quality: float | None,
    ) -> dict[str, Any]:
        """Resultado cuando la calidad del historial no permite comparar."""
        result = self._result_base(request, period_start, period_end, real_trades, tester_trades, quality)
        result.update(
            status="not_comparable", status_label="NO COMPARABLE", matched_trades=0,
            discrepancies=0, stalled_strategies=0,
            summary=("MT5 no informó History Quality." if quality is None else
                     f"History Quality {quality:.2f}% inferior al mínimo {request['min_tick_history_quality_pct']:.2f}%.")
        )
        return result

    def _compared_result(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        real_trades: list[dict[str, Any]], tester_trades: list[dict[str, Any]], quality: float | None,
        symbol_points: dict[str, Any], strategies: dict[str, int],
    ) -> dict[str, Any]:
        """Resultado de la comparacion completa entre cuenta real y tester."""
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
        return result

    @staticmethod
    def _comparison_detail_log(result: dict[str, Any]) -> str:
        """Resumen en una linea del detalle por estrategia de la comparacion."""
        detail = result.get("comparison_detail") or {}
        return "; ".join(
            f"{key}={detail[key]}" for key in (
                "matched_by_strategy", "within_tolerance_by_strategy", "deviating_by_strategy",
                "missing_by_strategy", "unmatched_real", "deviation_reasons", "tester_data_issues",
            ) if detail.get(key)
        )

    def _restore_terminals(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Devuelve cada terminal tocada a su cuenta de restauracion."""
        try:
            return self._restore_tester_login(request)
        except Exception as exc:
            # Un fallo aquí no puede tapar el resultado de la auditoría.
            return [{
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

    def _record_restore(
        self, audit_key: str, audit_id: str, restored: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Anota la cuenta en que quedo cada terminal y devuelve las no restauradas."""
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
            last_payload = raw.get("last_payload")
            if isinstance(last_payload, dict) and str(last_payload.get("audit_id") or "") == audit_id:
                last_payload["terminal_restore"] = restored
            self._persist()
        return [row for row in restored if not row["restored"]]

    def _close_audit_state(
        self, request: dict[str, Any], terminal_status: str, unrestored: list[dict[str, Any]]
    ) -> None:
        """Fija el estado final de la auditoria y avisa de terminales sin restaurar."""
        audit_key = request["audit_key"]
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

    def _finish_run(
        self, request: dict[str, Any], audit_id: str, terminal_status: str, paused_by_auditor: bool
    ) -> None:
        """Restaura terminales, reanuda el pipeline y cierra el estado."""
        audit_key = request["audit_key"]
        # La cuenta activa de un terminal es estado persistente de MT5. Cada
        # terminal que tocó la auditoría se devuelve a la cuenta independiente
        # configurada para restauración antes de reanudar el pipeline.
        restored = self._restore_terminals(request)
        with self.lock:
            self.real_account_terminals.pop(audit_key, None)
        unrestored = self._record_restore(audit_key, audit_id, restored) if restored else []
        if paused_by_auditor:
            try:
                self._update(audit_key, "resuming", "Reanudando el proceso que pausó el auditor.", "Reanudación solicitada")
                self.owner.resume()
            except Exception as exc:
                self._update(audit_key, "failed", f"La auditoría terminó, pero no se pudo reanudar: {exc}", str(exc), error=str(exc))
        self._close_audit_state(request, terminal_status, unrestored)
        if getattr(self.owner, "queue", None):
            self.owner._schedule_queue_drain()

    def _audit(self, request: dict[str, Any], audit_id: str) -> str:
        """Ejecuta la auditoria completa y devuelve su estado terminal."""
        audit_key = request["audit_key"]
        self._update(audit_key, "extracting", "Extrayendo operaciones de la cuenta real.", "Conectando la cuenta real")
        period_start, period_end = _audit_period(request)
        real_trades, symbol_points, account, real_account_report, real_history_detail, open_positions = (
            self._extract_real_account(request, audit_id, period_start, period_end)
        )
        self._update(
            audit_key, "testing", "Ejecutando el portafolio con ticks reales en el nodo.",
            f"{len(real_trades)} cierres reales reconstruidos antes del filtro del portafolio.",
        )
        tester_trades, qualities, strategies, strategy_artifacts, tester_execution = self._run_tester(
            request, audit_id, period_start, period_end
        )
        _detail, selected_members = self._portfolio_members(
            request["portfolio_id"], request["portfolio_type"]
        )
        volume_rules = {
            symbol: {"volume_min": minimum, "volume_step": step}
            for symbol, (minimum, step) in self._broker_volume_rules().items()
        }
        tester_groups: dict[str, int] = {}
        for trade in tester_trades:
            key = f"{trade.get('symbol') or '?'} / {trade.get('strategy') or '?'}"
            tester_groups[key] = tester_groups.get(key, 0) + 1
        tester_summary = ", ".join(f"{key}: {count}" for key, count in sorted(tester_groups.items())) or "sin operaciones"
        self._update(
            audit_key, "comparing", "Publicando datos para análisis en el manager.",
            f"{len(tester_trades)} operaciones del tester ({tester_summary})",
        )
        payload = {
            "audit_id": audit_id, "audit_key": audit_key, "completed_at": utc_now(),
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "request": {
                key: request.get(key) for key in (
                    "audit_key", "portfolio_id", "portfolio_type", "period_mode",
                    "period_days", "period_start_date", "period_end_date",
                )
            },
            "real_trades": [_trade_view(trade) for trade in real_trades],
            "tester_trades": [_trade_view(trade) for trade in tester_trades],
            "open_positions_at_period_end": [_trade_view(position) for position in open_positions],
            "symbol_points": symbol_points, "strategies": strategies, "qualities": qualities,
            "strategy_artifacts": strategy_artifacts, "selected_members": selected_members,
            "volume_rules": volume_rules, "account": account,
            "real_history_detail": real_history_detail, "tester_execution": tester_execution,
            "real_account_report": real_account_report, "terminal_restore": [],
        }
        self._update(
            audit_key, "finalizing", "Restaurando las cuentas de todas las terminales utilizadas.",
            f"Materia prima publicada: {len(real_trades)} cierres reales sin filtrar, "
            f"{len(tester_trades)} operaciones tester, {len(selected_members)} miembros y "
            f"{len(volume_rules)} símbolos con especificación.",
            last_payload=payload,
        )
        return "completed"

    def _run(self, request: dict[str, Any], audit_id: str) -> None:
        audit_key = request["audit_key"]
        paused_by_auditor = False
        terminal_status = "failed"
        with self.lock:
            self.real_account_terminals[audit_key] = []
        try:
            paused_by_auditor = self._pause_active_job(audit_key)
            terminal_status = self._audit(request, audit_id)
        except Exception as exc:
            terminal_status = "failed"
            self._update(
                audit_key, "finalizing", f"La auditoría falló; restaurando las terminales: {exc}", str(exc),
                error=str(exc),
            )
        finally:
            self._finish_run(request, audit_id, terminal_status, paused_by_auditor)
