from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from ubs.tester_diagnostics import execution_failure_reason


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSResultsFormatMixin:
    """Motivos y formateo de las filas de resultados."""

    @staticmethod
    def _ubs_no_history_reason(row: object) -> str:
        """Rango disponible frente al pedido cuando el broker no tiene historico."""
        metrics_json = None
        try:
            metrics_json = row["metrics_json"]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            pass
        try:
            data = json.loads(metrics_json) if metrics_json else {}
        except (TypeError, json.JSONDecodeError):
            data = {}
        available_from = str(data.get("history_available_from") or "").strip()
        available_to = str(data.get("history_available_to") or "").strip()
        requested_from = str(data.get("history_requested_from") or "").strip()
        requested_to = str(data.get("history_requested_to") or "").strip()
        parts = ["sin historico broker"]
        if available_from or available_to:
            parts.append(f"disponible {available_from} -> {available_to}")
        if requested_from or requested_to:
            parts.append(f"pedido {requested_from} -> {requested_to}")
        parts.append("recom.: desactivar simbolo")
        return " | ".join(parts)

    @staticmethod
    def _ubs_result_metric_reasons(row: object) -> str:
        """Motivos del veredicto a partir de las metricas guardadas."""
        metrics_json = None
        try:
            metrics_json = row["metrics_json"]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            pass
        if not metrics_json:
            return ""
        try:
            data = json.loads(metrics_json)
            reasons = data.get("reasons") or []
            if not reasons:
                return ""
            formats = {
                "net_profit": ("net norm", ".0f", ""),
                "profit_factor": ("PF", ".2f", ""),
                "trades": ("trades", "d", ""),
                "drawdown_pct": ("DD", ".1f", "%"),
                "recovery_factor": ("RF", ".2f", ""),
                "positive_month_ratio": ("meses+", ".0%", ""),
            }
            parts = []
            for reason in reasons:
                label, fmt, suffix = formats.get(reason, (reason, "", ""))
                value = data.get("normalized_net_profit") if reason == "net_profit" else data.get(reason)
                if value is None:
                    parts.append(label)
                    continue
                try:
                    parts.append(f"{label}: {value:{fmt}}{suffix}")
                except (TypeError, ValueError):
                    parts.append(f"{label}: {value}")
            return " | ".join(parts)
        except Exception:
            return ""

    def _ubs_result_reason(self, row: object, status: str) -> str:
        if status in {"rejected", "no_trades"}:
            try:
                reason = execution_failure_reason(json.loads(row["metrics_json"] or "{}"))
            except (TypeError, ValueError, KeyError, IndexError):
                reason = ""
            if reason:
                return reason
        if status == "report_mismatch":
            return "mismatch symbol/TF"
        if status == "parse_error":
            return "error al parsear reporte"
        if status == "no_report":
            return "sin reporte"
        if status == "symbol_not_exist":
            return "el broker ya no ofrece este simbolo"
        if status == "no_trades":
            return "reporte sin operaciones"
        if status == "trade_disabled":
            return "el broker no permite abrir nuevas posiciones"
        if status == "history_ok":
            return "historico disponible para el rango pedido"
        if status == "no_history":
            return self._ubs_no_history_reason(row)
        if status in ("generated",):
            return "sin backtest"
        return self._ubs_result_metric_reasons(row)

    def _short_filename(self, value, max_length: int = 72) -> str:
        name = Path(str(value)).name
        if len(name) <= max_length:
            return name
        suffix = Path(name).suffix
        stem = name[: -len(suffix)] if suffix else name
        tail_length = max(12, max_length // 3)
        head_length = max(8, max_length - tail_length - len(suffix) - 3)
        return f"{stem[:head_length]}...{stem[-tail_length:]}{suffix}"

    def _ubs_variant_code(self, set_name: str) -> str:
        matches = re.findall(r"g\d+_s\d+_v\d+", set_name, flags=re.IGNORECASE)
        return matches[-1] if matches else ""

    def _format_ubs_set_label(self, row: sqlite3.Row) -> str:
        set_path = Path(str(row["set_path"] or ""))
        name = set_path.name
        candidate_id = str(row["id"] or "").strip()
        symbol = str(row["target_symbol"] or row["symbol"] or "").strip()
        period = str(row["period"] or "").strip()
        variant_code = self._ubs_variant_code(name)
        prefix = f"#{candidate_id} " if candidate_id else ""
        if symbol and period and variant_code:
            return f"{prefix}{symbol}_{period}_{variant_code}{set_path.suffix or '.set'}"
        if variant_code:
            return f"{prefix}{variant_code}{set_path.suffix or '.set'}"
        return f"{prefix}{self._short_filename(name)}"

    def _parse_ubs_metrics(self, raw) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _format_ubs_number(self, value, decimals: int = 2) -> str:
        if value in (None, ""):
            return ""
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)

    def _format_ubs_int(self, value) -> str:
        if value in (None, ""):
            return ""
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)

    def _format_ubs_status(self, status: str) -> str:
        labels = {
            "accepted": "aceptado",
            "rejected": "rechazado",
            "generated": "generado",
            "no_report": "pend. reporte",
            "no_trades": "0 ops/no aceptado",
            "no_history": "sin historico",
            "trade_disabled": "trading bloqueado",
            "history_ok": "historico OK",
            "disabled_symbol": "deshabilitado",
            "symbol_not_exist": "simbolo inexistente",
            "parse_error": "pend. parse",
            "report_mismatch": "pend. mismatch",
            "pending_tester_context": "pend. contexto",
            "invalid_seed": "set invalido",
            "pending": "pendiente",
            "missing_6m": "sin 6M",
            "pending_history_quality": "pend. calidad",
            "pending_ohlc_trades": "pend. OHLC ops",
            "pending_risk_evidence": "pend. evidencia riesgo",
            "sin_evaluar": "sin evaluar",
        }
        return labels.get(status, status or "-")

    def _format_ubs_robust_status(self, status: str, positive_bonus, negative_bonus) -> str:
        if not status:
            return "pendiente"
        labels = {
            "accepted": "OK",
            "rejected": "FAIL",
            "no_trades": "0 ops/no aceptado",
            "no_report": "pend. reporte",
            "parse_error": "pend. parse",
            "report_mismatch": "pend. mismatch",
            "symbol_not_exist": "simbolo inexistente",
        }
        label = labels.get(status, status)
        bonus = None
        if status == "accepted":
            bonus = positive_bonus
        elif status == "rejected":
            bonus = negative_bonus
        if bonus in (None, ""):
            return label
        try:
            bonus_value = float(bonus)
        except (TypeError, ValueError):
            return label
        return f"{label} {bonus_value:+.0f}"

    def _ubs_result_tag(self, status: str) -> str:
        if status == "accepted":
            return "accepted"
        # symbol_not_exist es terminal como no_history: no queda nada que reintentar.
        if status in {"rejected", "no_history", "symbol_not_exist"}:
            return "rejected"
        return "pending"
