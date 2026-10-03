from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from run_tests import (
    apply_symbol_map,
    infer_tester_fields_from_set,
    load_set_files,
    normalize_set_symbol,
    parse_symbol_map,
)
from ubs.db import connect_memory
from ubs.memory import metrics_have_empty_tester_context
from ubs.path_utils import resolve_workspace_path
from ubs.tester_diagnostics import execution_failure_reason


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSeedsEvalMixin:
    """Evaluacion de semillas: motivos, plan, argumentos y resumen."""

    def _refresh_ubs_seeds_panel(self) -> None:
        for label, callback in (
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_seeds", self._refresh_ubs_seeds),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    @staticmethod
    def _ubs_invalid_seed_reason(row: object) -> str:
        try:
            metrics_json = row["metrics_json"] if row is not None else None
            data = json.loads(metrics_json) if metrics_json else {}
            reasons = data.get("reasons") or []
            if reasons:
                return " | ".join(str(reason) for reason in reasons)
        except Exception:
            pass
        return "set invalido/deshabilitado"

    @staticmethod
    def _ubs_seed_metric_reasons(row: object) -> str:
        """Motivos del veredicto a partir de las metricas guardadas de la semilla."""
        metrics_json = None
        try:
            metrics_json = row["metrics_json"]
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

    def _ubs_seed_reason(self, row: object, status: str) -> str:
        if status in {"rejected", "no_trades"}:
            try:
                reason = execution_failure_reason(json.loads(row["metrics_json"] or "{}"))
            except (TypeError, ValueError, KeyError, IndexError):
                reason = ""
            if reason:
                return reason
        if status == "report_mismatch":
            return "mismatch symbol/TF"
        if status == "pending_tester_context":
            return "reporte MT5 vacio (symbol/TF); reintento pendiente"
        if status == "invalid_seed":
            return self._ubs_invalid_seed_reason(row)
        if row is None:
            return ""
        if status == "parse_error":
            return "error al parsear reporte"
        if status == "no_report":
            return "sin reporte"
        if status == "no_trades":
            return "reporte sin operaciones"
        if status == "disabled_symbol":
            return "symbol deshabilitado"
        return self._ubs_seed_metric_reasons(row)

    def _count_ubs_seed_files(self) -> tuple[int, str]:
        source_dir = self._ubs_generator_source_dir()
        files = load_set_files(source_dir, None, recursive=True)
        return len(files), str(source_dir)

    def _active_ubs_symbol_map(self) -> dict[str, str]:
        return parse_symbol_map(self._effective_ubs_symbol_map_text())

    def _format_disabled_seed_counts(self, counts: Counter[tuple[str, str]]) -> str:
        parts = []
        shown_total = 0
        for (raw, mapped), count in counts.most_common(5):
            shown_total += count
            label = raw if raw == mapped else f"{raw} -> {mapped}"
            parts.append(f"{label}: {count}")
        remaining = sum(counts.values()) - shown_total
        if remaining > 0:
            parts.append(f"otros: {remaining}")
        return ", ".join(parts)

    def _ubs_seed_plan_memory(self, memory_path: Path) -> tuple[dict, dict]:
        """Filas y overrides guardados de las semillas, o vacios si no se pueden leer."""
        rows: dict[str, sqlite3.Row] = {}
        overrides: dict[str, tuple[str, str]] = {}
        if not memory_path.exists():
            return rows, overrides
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            if self._sqlite_table_exists(conn, "seed_scores"):
                rows = {
                    str(resolve_workspace_path(r["seed_path"])): r
                    for r in conn.execute("select * from seed_scores").fetchall()
                }
            if self._sqlite_table_exists(conn, "seed_overrides"):
                overrides = {
                    str(resolve_workspace_path(r["seed_path"])): (
                        str(r["symbol"] or "").strip().upper(),
                        str(r["period"] or "").strip().upper(),
                    )
                    for r in conn.execute("select seed_path, symbol, period from seed_overrides").fetchall()
                }
            conn.close()
        except sqlite3.Error:
            return {}, {}
        return rows, overrides

    @staticmethod
    def _ubs_seed_needs_eval(row, stat, symbol: str, period: str, ready_statuses: set[str]) -> bool:
        """La semilla vuelve a la cola si cambio en disco o su veredicto no es terminal."""
        return (
            row is None
            or abs(float(row["seed_mtime"] or 0.0) - float(stat.st_mtime)) > 0.001
            or int(row["seed_size"] or -1) != int(stat.st_size)
            or str(row["status"] or "") not in ready_statuses
            or (
                str(row["status"] or "") == "report_mismatch"
                and metrics_have_empty_tester_context(row["metrics_json"])
            )
            or str(row["symbol"] or "").strip().upper() != symbol.strip().upper()
            or str(row["period"] or "").strip().upper() != period.strip().upper()
        )

    def _ubs_seed_eval_plan(self, seed_files: list[Path]) -> dict[str, object]:
        """Estimate real MT5 jobs and skipped seed categories for the confirmation dialog."""
        memory_path = self._ubs_memory_path()
        disabled_symbols = self._load_disabled_ubs_symbols()
        seed_enabled_when_disabled = self._load_seed_enabled_disabled_ubs_symbols()
        seed_enabled_when_disabled &= disabled_symbols
        symbol_map = self._active_ubs_symbol_map()
        rows, overrides = self._ubs_seed_plan_memory(memory_path)

        stats: Counter[str] = Counter()
        disabled_counts: Counter[tuple[str, str]] = Counter()
        ready_statuses = {"accepted", "rejected", "invalid_seed", "report_mismatch", "trade_disabled"}
        for path in seed_files:
            path_text = str(path)
            try:
                stat = path.stat()
            except OSError:
                stats["missing"] += 1
                continue
            row = rows.get(path_text)
            inferred_symbol, inferred_period = self._inferred_ubs_seed_fields(path)
            ov_sym, ov_per = overrides.get(path_text, ("", ""))
            symbol = ov_sym or inferred_symbol
            period = ov_per or inferred_period

            if not symbol or not period or symbol == "UNKNOWN" or period == "UNKNOWN":
                stats["invalid"] += 1
                continue

            raw = normalize_set_symbol(symbol)
            mapped = normalize_set_symbol(apply_symbol_map(symbol, symbol_map))
            symbol_disabled = raw in disabled_symbols or mapped in disabled_symbols
            seed_allowed = raw in seed_enabled_when_disabled or mapped in seed_enabled_when_disabled
            if symbol_disabled and not seed_allowed:
                stats["disabled"] += 1
                disabled_counts[(raw or symbol, mapped or raw or symbol)] += 1
                continue

            if self._ubs_seed_needs_eval(row, stat, symbol, period, ready_statuses):
                stats["pending"] += 1
            else:
                stats["unchanged"] += 1

        return {
            "pending": int(stats["pending"]),
            "unchanged": int(stats["unchanged"]),
            "disabled": int(stats["disabled"]),
            "invalid": int(stats["invalid"]),
            "missing": int(stats["missing"]),
            "disabled_counts": disabled_counts,
        }

    def _count_ubs_seed_pending(self, seed_files: list) -> int:
        """Estimate how many seeds will actually run backtests."""
        return int(self._ubs_seed_eval_plan(seed_files)["pending"])

    def _ubs_seed_eval_args(self) -> list[str]:
        source_dir = self._ubs_generator_source_dir()
        output_dir = self._ubs_generation_output_dir()
        args = [
            "--evaluate-seeds",
            "--source-dir", str(source_dir),
            "--output-dir", str(output_dir),
            "--memory", str(self._ubs_memory_path()),
            "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
        ]
        if self.ubs_seed_from_date.get().strip():
            args.extend(["--from-date", self.ubs_seed_from_date.get().strip()])
        if self.ubs_seed_to_date.get().strip():
            args.extend(["--to-date", self.ubs_seed_to_date.get().strip()])
        args.extend(self._ubs_seed_score_args())
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
            if self.mt5_path.get().strip():
                args.extend(["--mt5-path", self.mt5_path.get()])
            if self.mt5_data_root.get().strip():
                args.extend(["--data-dir", self.mt5_data_root.get()])
        symbol_map = self._effective_ubs_symbol_map_text()
        if symbol_map:
            args.extend(["--symbol-map", symbol_map])
        args.extend(self._effective_symbol_suffix_args())
        return args

    def _run_ubs_seed_evaluation(self) -> None:
        try:
            args = self._ubs_seed_eval_args()
            source_dir = self._ubs_generator_source_dir()
            seed_files = load_set_files(source_dir, None, recursive=True)
            total = len(seed_files)
            target = str(source_dir)
            plan = self._ubs_seed_eval_plan(seed_files)
            pending = int(plan["pending"])
            already_ok = int(plan["unchanged"])
            disabled = int(plan["disabled"])
            invalid = int(plan["invalid"])
            missing = int(plan["missing"])
            disabled_counts = plan["disabled_counts"]
        except Exception as exc:
            self._show_error("No se pudo preparar evaluacion de semillas", str(exc))
            return
        details = [
            "Accion: Evaluar semillas UBS",
            f"Carpeta seeds: {target}",
            f"Seeds detectadas: {total}",
            f"Backtests reales a ejecutar: {pending}",
            f"No se ejecutan: {already_ok} ya listas/sin cambios, {disabled} por symbol deshabilitado, {invalid} por set invalido/Symbol-TF.",
            "Corren solo seeds nuevas/modificadas o retryables con set y symbol/TF validos.",
        ]
        if disabled:
            details.append(
                "Symbols deshabilitados (Universo de la cuenta): "
                f"{self._format_disabled_seed_counts(disabled_counts)}."
            )
            details.append("Ejemplo: XTIUSD cuenta como deshabilitado si el mapa activo lo traduce a WTI y WTI esta deshabilitado.")
        if invalid:
            details.append("Sets invalidos o sin Symbol/TF: se marcaran sin abrir MT5.")
        if missing:
            details.append(f"Archivos no accesibles y omitidos: {missing}.")
        details.extend([
            "Las semillas deshabilitadas solo aportan pesos si el activo tiene SEEDS=si en Universo.",
            f"Pass Seeds: net>{self.ubs_seed_pass_min_net_profit.get().strip()} | PF>={self.ubs_seed_pass_min_profit_factor.get().strip()} | DD<={self.ubs_seed_pass_max_drawdown_pct.get().strip()}%",
            f"Pass Seeds: trades>={self.ubs_seed_pass_min_trades.get()} | recovery>={self.ubs_seed_pass_min_recovery_factor.get().strip()}",
        ])
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar evaluacion de semillas", pending, details):
            self.ubs_seed_eval_summary.set("Evaluando semillas UBS...")
            self._run_script("ubs_agent.py", args)

    def _refresh_ubs_seed_eval_summary(self) -> None:
        if not hasattr(self, "ubs_seed_eval_summary"):
            return
        try:
            source_dir = self._ubs_generator_source_dir()
            seed_count = len(load_set_files(source_dir, None, recursive=True))
        except Exception:
            self.ubs_seed_eval_summary.set("Semillas: carpeta no valida")
            return

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_seed_eval_summary.set(f"Semillas: {seed_count} | evaluadas 0 | pendientes {seed_count}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            seed_table = conn.execute(
                "select name from sqlite_master where type='table' and name='seed_scores'"
            ).fetchone()
            if not seed_table:
                conn.close()
                self.ubs_seed_eval_summary.set(f"Semillas: {seed_count} | evaluadas 0 | pendientes {seed_count}")
                return
            active_counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when status in ('accepted', 'rejected', 'report_mismatch', 'disabled_symbol', 'invalid_seed', 'symbol_not_exist', 'trade_disabled') then 1 else 0 end) as ready,
                    sum(case
                        when status in ('accepted', 'rejected') and score is null then 1
                        when status not in ('accepted', 'rejected', 'report_mismatch', 'disabled_symbol', 'invalid_seed', 'symbol_not_exist', 'trade_disabled') then 1
                        else 0
                    end) as pending
                from seed_scores
                where active=1
                """
            ).fetchone()
            inactive = int(conn.execute("select count(*) from seed_scores where active=0").fetchone()[0] or 0)
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_seed_eval_summary.set(f"Semillas: error SQLite ({exc})")
            return

        ready = int(active_counts["ready"] or 0) if active_counts else 0
        pending = max(seed_count - ready, int(active_counts["pending"] or 0) if active_counts else seed_count)
        self.ubs_seed_eval_summary.set(
            f"Semillas: {seed_count} | listas {ready} | pendientes {pending} | obsoletas {inactive}"
        )

    def _sqlite_table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return bool(conn.execute("select name from sqlite_master where type='table' and name=?", (table,)).fetchone())

    def _ensure_ubs_seed_override_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists seed_overrides (
                seed_path text primary key,
                symbol text not null default '',
                period text not null default '',
                updated_at text not null
            )
            """
        )
        if self._sqlite_table_exists(conn, "seed_scores"):
            conn.execute(
                """
                update seed_scores
                set status='report_mismatch', accepted=null
                where status in ('accepted', 'rejected')
                  and (upper(symbol)='UNKNOWN' or upper(period)='UNKNOWN')
                """
            )
        conn.commit()

    def _current_ubs_seed_files(self) -> list[Path]:
        return sorted(load_set_files(self._ubs_generator_source_dir(), None, recursive=True), key=lambda path: path.name.lower())

    def _inferred_ubs_seed_fields(self, path: Path) -> tuple[str, str]:
        try:
            fields = infer_tester_fields_from_set(path)
        except Exception:
            fields = {}
        symbol = str(fields.get("Symbol") or "UNKNOWN").strip().upper()
        period = str(fields.get("Period") or "UNKNOWN").strip().upper()
        return symbol, period
