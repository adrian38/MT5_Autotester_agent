from __future__ import annotations

from dataclasses import asdict
import json
import math
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from ubs.db import connect_memory
from ubs.set_utils import read_set_with_encoding, write_set_text
from portfolio_manager.ubs_portfolio import (
    PortfolioAvailability,
    PortfolioResult,
    PortfolioType,
    apply_portfolio_lot_text,
    load_robust_sets_from_rows,
    optimize_portfolio,
    summarize_robust_rows,
)


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


PORTFOLIO_TYPE_LABELS = {
    "Conservative": PortfolioType.CONSERVATIVE,
    "Balanced": PortfolioType.BALANCED,
    "Aggressive": PortfolioType.AGGRESSIVE,
    # Backward-compatible labels from the previous Spanish UI.
    "Conservador": PortfolioType.CONSERVATIVE,
    "Equilibrado": PortfolioType.BALANCED,
    "Agresivo": PortfolioType.AGGRESSIVE,
}
PORTFOLIO_TYPE_DISPLAY = {
    PortfolioType.CONSERVATIVE.value: "Conservative",
    PortfolioType.BALANCED.value: "Balanced",
    PortfolioType.AGGRESSIVE.value: "Aggressive",
}

DEFAULT_PORTFOLIO_FORM = {
    "capital": "10000",
    "valley_dd_pct": "10",
    "point_dd_pct": "4",
    "portfolio_type": "Balanced",
    "top_k_per_symbol": 3,
    "max_total_candidates": 30,
    "min_trades_2020_2026": 100,
    "max_units_per_set": "",
    "max_total_units": "",
    "max_units_per_symbol": "",
    "max_sets_per_symbol": 1,
    "run_local_search": True,
}


class UBSPortfolioLogicMixin:
    # ------------------------------------------------------------------ schema
    def _ensure_portfolio_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists portfolios (
                id integer primary key autoincrement,
                created_at text not null,
                name text not null default '',
                type text not null default '',
                portfolio_type text not null default 'balanced',
                num_symbols integer not null default 0,
                account_capital real not null default 0,
                capital real not null default 0,
                target_valley_dd_pct real not null default 0,
                target_point_dd_pct real not null default 0,
                target_valley_dd real not null default 0,
                target_point_dd real not null default 0,
                actual_valley_dd real not null default 0,
                actual_point_dd real not null default 0,
                valley_usage_pct real not null default 0,
                point_usage_pct real not null default 0,
                total_net_profit real not null default 0,
                total_lot real not null default 0,
                total_units integer not null default 0,
                active_strategies integer not null default 0,
                stop_reason text not null default '',
                scale_factor real,
                binding_constraint text,
                metrics_json text
            )
            """
        )
        for column, definition in (
            ("name", "text not null default ''"),
            ("type", "text not null default ''"),
            ("portfolio_type", "text not null default 'balanced'"),
            ("num_symbols", "integer not null default 0"),
            ("account_capital", "real not null default 0"),
            ("capital", "real not null default 0"),
            ("target_valley_dd_pct", "real not null default 0"),
            ("target_point_dd_pct", "real not null default 0"),
            ("target_valley_dd", "real not null default 0"),
            ("target_point_dd", "real not null default 0"),
            ("actual_valley_dd", "real not null default 0"),
            ("actual_point_dd", "real not null default 0"),
            ("valley_usage_pct", "real not null default 0"),
            ("point_usage_pct", "real not null default 0"),
            ("total_net_profit", "real not null default 0"),
            ("total_lot", "real not null default 0"),
            ("total_units", "integer not null default 0"),
            ("active_strategies", "integer not null default 0"),
            ("stop_reason", "text not null default ''"),
            ("scale_factor", "real"),
            ("binding_constraint", "text"),
            ("metrics_json", "text"),
        ):
            self._ensure_sqlite_column(conn, "portfolios", column, definition)

        conn.execute(
            """
            create table if not exists portfolio_allocations (
                id integer primary key autoincrement,
                portfolio_id integer not null,
                set_id text not null,
                candidate_id text not null,
                symbol text not null,
                units integer not null,
                lot real not null,
                net_profit_contribution real not null,
                standalone_valley_dd real not null,
                standalone_point_dd real not null,
                set_path text,
                timeframe text,
                lot_size_step real,
                is_report_path text,
                oos_report_path text,
                foreign key (portfolio_id) references portfolios(id)
            )
            """
        )
        conn.execute(
            """
            create table if not exists portfolio_decision_log (
                id integer primary key autoincrement,
                portfolio_id integer not null,
                step integer not null,
                action text not null,
                set_id text,
                from_set_id text,
                to_set_id text,
                gain real not null,
                valley_cost real not null,
                point_cost real not null,
                score real not null,
                portfolio_net_profit_after real not null,
                portfolio_valley_dd_after real not null,
                portfolio_point_dd_after real not null,
                reason text not null,
                foreign key (portfolio_id) references portfolios(id)
            )
            """
        )
        # Compatibility with the previous UBS Portafolio tab. Existing rows in
        # this table still count as used sets and remain exportable.
        conn.execute(
            """
            create table if not exists portfolio_members (
                id integer primary key autoincrement,
                portfolio_id integer not null,
                candidate_id integer,
                set_path text not null,
                symbol text,
                period text,
                lot_multiplier real,
                lot real,
                lot_size_step real,
                standalone_dd real,
                quality_score real,
                combined_net_profit real,
                is_report_path text,
                oos_report_path text
            )
            """
        )
        conn.commit()

    def _ensure_sqlite_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {definition}")

    def _ubs_portfolio_conn(self) -> sqlite3.Connection:
        conn = connect_memory(self._ubs_memory_path())
        conn.row_factory = sqlite3.Row
        self._ensure_ubs_base_tables_for_portfolio(conn)
        self._ensure_ubs_memory_schema(conn)
        self._ensure_portfolio_schema(conn)
        return conn

    def _ensure_ubs_base_tables_for_portfolio(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            create table if not exists runs (
                id integer primary key autoincrement,
                created_at text not null,
                source_dir text not null,
                output_dir text not null,
                generations integer not null,
                variants_per_seed integer not null,
                max_seeds integer not null,
                execute_backtests integer not null,
                dry_run integer not null,
                hidden integer not null default 0
            );
            create table if not exists candidates (
                id integer primary key autoincrement,
                run_id integer not null,
                generation integer not null,
                seed_path text not null,
                set_path text not null,
                symbol text not null,
                target_symbol text not null,
                period text not null,
                family text not null,
                run_strategy text not null,
                mutated_keys text not null,
                missing_lot_keys text not null,
                policy text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                status text not null,
                created_at text not null
            );
            """
        )
        conn.commit()

    # ------------------------------------------------------------------ SQL
    def _robust_passed_candidates(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            """
            select c.id as candidate_id, c.set_path, c.symbol, c.target_symbol,
                   c.period, c.family,
                   c.report_path as is_report_path, cr.report_path as oos_report_path
            from candidates c
            join candidate_robustness cr on cr.candidate_id = c.id
            where c.status = 'accepted' and cr.status = 'accepted'
            order by c.id
            """
        ).fetchall()

    def _used_set_paths(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            """
            select set_path from portfolio_allocations where set_path is not null and set_path <> ''
            union
            select set_path from portfolio_members where set_path is not null and set_path <> ''
            """
        ).fetchall()
        return [str(row["set_path"]) for row in rows]

    def _portfolio_availability(self, conn: sqlite3.Connection) -> PortfolioAvailability:
        rows = self._robust_passed_candidates(conn)
        used = self._used_set_paths(conn)
        return summarize_robust_rows(rows, used)

    def _insert_portfolio(
        self,
        conn: sqlite3.Connection,
        inputs: dict[str, object],
        result: PortfolioResult,
    ) -> int:
        created_at = datetime.now().isoformat(timespec="seconds")
        portfolio_type = str(inputs["portfolio_type"])
        name = (
            f"{PORTFOLIO_TYPE_DISPLAY.get(portfolio_type, portfolio_type)} | "
            f"{result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
        )
        active_symbols = len({allocation.symbol for allocation in result.allocations if allocation.units > 0})
        metrics = {
            "inputs": inputs,
            "warnings": result.warnings,
            "equity_curve_2020_2026": result.equity_curve_2020_2026,
            "unused_sets": [asdict(item) for item in result.unused_sets],
        }
        cur = conn.execute(
            """
            insert into portfolios (
                created_at, name, type, portfolio_type, num_symbols, account_capital,
                capital, target_valley_dd_pct, target_point_dd_pct, target_valley_dd,
                target_point_dd, actual_valley_dd, actual_point_dd, valley_usage_pct,
                point_usage_pct, total_net_profit, total_lot, total_units,
                active_strategies, stop_reason, binding_constraint, metrics_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                name,
                portfolio_type,
                portfolio_type,
                active_symbols,
                float(inputs["capital"]),
                float(inputs["capital"]),
                float(inputs["valley_dd_pct"]),
                float(inputs["point_dd_pct"]),
                result.target_valley_dd,
                result.target_point_dd,
                result.actual_valley_dd,
                result.actual_point_dd,
                result.valley_usage_pct,
                result.point_usage_pct,
                result.total_net_profit,
                result.total_lot,
                result.total_units,
                result.active_strategies,
                result.stop_reason,
                "valley" if result.valley_usage_pct >= result.point_usage_pct else "point",
                json.dumps(metrics, ensure_ascii=True),
            ),
        )
        portfolio_id = int(cur.lastrowid)
        for allocation in result.allocations:
            conn.execute(
                """
                insert into portfolio_allocations (
                    portfolio_id, set_id, candidate_id, symbol, units, lot,
                    net_profit_contribution, standalone_valley_dd, standalone_point_dd,
                    set_path, timeframe, lot_size_step, is_report_path, oos_report_path
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    allocation.set_id,
                    allocation.candidate_id,
                    allocation.symbol,
                    allocation.units,
                    allocation.lot,
                    allocation.net_profit_contribution,
                    allocation.standalone_valley_dd,
                    allocation.standalone_point_dd,
                    allocation.set_path or allocation.set_id,
                    allocation.timeframe or "",
                    allocation.lot_size_step,
                    allocation.is_report_path,
                    allocation.oos_report_path,
                ),
            )
            conn.execute(
                """
                insert into portfolio_members (
                    portfolio_id, candidate_id, set_path, symbol, period, lot_multiplier,
                    lot, lot_size_step, standalone_dd, quality_score, combined_net_profit,
                    is_report_path, oos_report_path
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    int(allocation.candidate_id) if str(allocation.candidate_id).isdigit() else None,
                    allocation.set_path or allocation.set_id,
                    allocation.symbol,
                    allocation.timeframe or "",
                    allocation.units,
                    allocation.lot,
                    allocation.lot_size_step,
                    allocation.standalone_valley_dd,
                    0.0,
                    allocation.net_profit_contribution,
                    allocation.is_report_path,
                    allocation.oos_report_path,
                ),
            )
        for decision in result.decision_log:
            conn.execute(
                """
                insert into portfolio_decision_log (
                    portfolio_id, step, action, set_id, from_set_id, to_set_id,
                    gain, valley_cost, point_cost, score, portfolio_net_profit_after,
                    portfolio_valley_dd_after, portfolio_point_dd_after, reason
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    decision.step,
                    decision.action,
                    decision.set_id,
                    decision.from_set_id,
                    decision.to_set_id,
                    decision.gain,
                    decision.valley_cost,
                    decision.point_cost,
                    decision.score,
                    decision.portfolio_net_profit_after,
                    decision.portfolio_valley_dd_after,
                    decision.portfolio_point_dd_after,
                    decision.reason,
                ),
            )
        conn.commit()
        return portfolio_id

    def _list_portfolios(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute("select * from portfolios order by id desc").fetchall()

    def _portfolio_members(self, conn: sqlite3.Connection, portfolio_id: int) -> list[dict[str, object]]:
        rows = conn.execute(
            """
            select * from portfolio_allocations
            where portfolio_id=?
            order by units desc, net_profit_contribution desc
            """,
            (portfolio_id,),
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]
        legacy = conn.execute(
            "select * from portfolio_members where portfolio_id=? order by lot desc",
            (portfolio_id,),
        ).fetchall()
        return [
            {
                "set_id": str(row["set_path"]),
                "candidate_id": str(row["candidate_id"] or ""),
                "symbol": row["symbol"],
                "timeframe": row["period"],
                "units": int(round(float(row["lot"] or 0) / 0.01)),
                "lot": row["lot"],
                "lot_size_step": row["lot_size_step"],
                "net_profit_contribution": row["combined_net_profit"],
                "standalone_valley_dd": row["standalone_dd"],
                "standalone_point_dd": 0.0,
                "set_path": row["set_path"],
                "is_report_path": row["is_report_path"],
                "oos_report_path": row["oos_report_path"],
            }
            for row in legacy
        ]

    def _portfolio_decisions(self, conn: sqlite3.Connection, portfolio_id: int) -> list[sqlite3.Row]:
        return conn.execute(
            "select * from portfolio_decision_log where portfolio_id=? order by step, id",
            (portfolio_id,),
        ).fetchall()

    def _delete_portfolio(self, conn: sqlite3.Connection, portfolio_id: int) -> None:
        conn.execute("delete from portfolio_decision_log where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolio_allocations where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolio_members where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolios where id=?", (portfolio_id,))
        conn.commit()

    # ------------------------------------------------------------------ form/state
    def _parse_float_setting(self, value: str, label: str) -> float:
        try:
            return float(str(value).strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{label} debe ser numerico.") from exc

    def _parse_int_setting(self, value: object, label: str, *, minimum: int) -> int:
        try:
            parsed = int(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"{label} debe ser entero.") from exc
        if parsed < minimum:
            raise ValueError(f"{label} debe ser >= {minimum}.")
        return parsed

    def _parse_optional_int_setting(self, value: str, label: str) -> int | None:
        text = str(value).strip()
        if not text:
            return None
        return self._parse_int_setting(text, label, minimum=1)

    def _read_ubs_portfolio_inputs(self) -> dict[str, object]:
        capital = self._parse_float_setting(self.ubs_portfolio_capital.get(), "Capital")
        valley_pct = self._parse_float_setting(self.ubs_portfolio_valley_pct.get(), "DD valle")
        point_pct = self._parse_float_setting(self.ubs_portfolio_point_pct.get(), "DD puntual")
        if capital <= 0 or valley_pct <= 0 or point_pct <= 0:
            raise ValueError("Capital y porcentajes de DD deben ser mayores que 0.")
        if point_pct > valley_pct:
            raise ValueError("El DD puntual no deberia ser mayor que el DD valle.")

        top_k = self._parse_int_setting(self.ubs_portfolio_top_k.get(), "Top K sets por simbolo", minimum=1)
        max_candidates = self._parse_int_setting(
            self.ubs_portfolio_max_candidates.get(),
            "Maximo total de candidatos",
            minimum=1,
        )
        min_trades = self._parse_int_setting(
            self.ubs_portfolio_min_trades.get(),
            "Minimo de trades 2020-2026",
            minimum=0,
        )
        max_sets_per_symbol = self._parse_int_setting(
            self.ubs_portfolio_max_sets_per_symbol.get(),
            "Maximo de sets por simbolo",
            minimum=1,
        )
        type_label = self.ubs_portfolio_type.get().strip()
        portfolio_type = PORTFOLIO_TYPE_LABELS.get(type_label, PortfolioType.BALANCED)
        return {
            "capital": capital,
            "valley_dd_pct": valley_pct,
            "point_dd_pct": point_pct,
            "portfolio_type": portfolio_type.value,
            "portfolio_type_label": PORTFOLIO_TYPE_DISPLAY[portfolio_type.value],
            "top_k_per_symbol": top_k,
            "max_total_candidates": max_candidates,
            "min_trades_2020_2026": min_trades,
            "max_units_per_set": self._parse_optional_int_setting(
                self.ubs_portfolio_max_units_per_set.get(),
                "Maximo de unidades por set",
            ),
            "max_total_units": self._parse_optional_int_setting(
                self.ubs_portfolio_max_total_units.get(),
                "Maximo total de unidades",
            ),
            "max_units_per_symbol": self._parse_optional_int_setting(
                self.ubs_portfolio_max_units_per_symbol.get(),
                "Maximo de unidades por simbolo",
            ),
            "max_sets_per_symbol": max_sets_per_symbol,
            "run_local_search": bool(self.ubs_portfolio_run_local_search.get()),
        }

    def _set_ubs_portfolio_running(self, running: bool) -> None:
        self.ubs_portfolio_running = running
        state = "disabled" if running else "normal"
        for button in getattr(self, "ubs_portfolio_buttons", []):
            try:
                button.configure(state=state)
            except Exception:
                pass
        self._set_ubs_portfolio_save_enabled(
            (not running) and getattr(self, "ubs_portfolio_pending_result", None) is not None
        )
        if hasattr(self, "ubs_portfolio_progress"):
            if running:
                self.ubs_portfolio_progress.start(12)
            else:
                self.ubs_portfolio_progress.stop()

    def _set_ubs_portfolio_save_enabled(self, enabled: bool) -> None:
        button = getattr(self, "ubs_portfolio_save_button", None)
        if button is None:
            return
        try:
            button.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _reset_ubs_portfolio_form(self) -> None:
        self.ubs_portfolio_capital.set(DEFAULT_PORTFOLIO_FORM["capital"])
        self.ubs_portfolio_valley_pct.set(DEFAULT_PORTFOLIO_FORM["valley_dd_pct"])
        self.ubs_portfolio_point_pct.set(DEFAULT_PORTFOLIO_FORM["point_dd_pct"])
        self.ubs_portfolio_type.set(DEFAULT_PORTFOLIO_FORM["portfolio_type"])
        self.ubs_portfolio_top_k.set(DEFAULT_PORTFOLIO_FORM["top_k_per_symbol"])
        self.ubs_portfolio_max_candidates.set(DEFAULT_PORTFOLIO_FORM["max_total_candidates"])
        self.ubs_portfolio_min_trades.set(DEFAULT_PORTFOLIO_FORM["min_trades_2020_2026"])
        self.ubs_portfolio_max_units_per_set.set(DEFAULT_PORTFOLIO_FORM["max_units_per_set"])
        self.ubs_portfolio_max_total_units.set(DEFAULT_PORTFOLIO_FORM["max_total_units"])
        self.ubs_portfolio_max_units_per_symbol.set(DEFAULT_PORTFOLIO_FORM["max_units_per_symbol"])
        self.ubs_portfolio_max_sets_per_symbol.set(DEFAULT_PORTFOLIO_FORM["max_sets_per_symbol"])
        self.ubs_portfolio_run_local_search.set(DEFAULT_PORTFOLIO_FORM["run_local_search"])
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self._set_ubs_portfolio_save_enabled(False)
        self._clear_ubs_portfolio_result_tables()
        self.ubs_portfolio_status.set("Formulario restaurado.")

    # ------------------------------------------------------------------ generate/save
    def _run_ubs_portfolio_build(self) -> None:
        if getattr(self, "ubs_portfolio_running", False):
            messagebox.showwarning("Portafolio en ejecucion", "Ya hay un proceso de portafolio en marcha.")
            return
        try:
            inputs = self._read_ubs_portfolio_inputs()
        except ValueError as exc:
            messagebox.showerror("Entrada invalida", str(exc))
            return

        if hasattr(self, "_write_ui_settings"):
            try:
                self._write_ui_settings()
            except Exception:
                pass

        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self._set_ubs_portfolio_save_enabled(False)
        self._set_ubs_portfolio_running(True)
        self.ubs_portfolio_status.set("Analizando sets robustos...")
        threading.Thread(target=self._ubs_portfolio_worker, args=(inputs,), daemon=True).start()

    def _ubs_portfolio_worker(self, inputs: dict[str, object]) -> None:
        try:
            conn = self._ubs_portfolio_conn()
        except Exception as exc:
            self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": f"No pude abrir la memoria UBS: {exc}"})
            return
        try:
            rows = self._robust_passed_candidates(conn)
            used = self._used_set_paths(conn)
            availability = summarize_robust_rows(rows, used)
            if not rows:
                self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": "No hay candidatos con robustez accepted."})
                return
            raw_sets, load_warnings = load_robust_sets_from_rows(
                rows,
                used,
                progress=lambda msg: self.after(0, self.ubs_portfolio_status.set, msg),
            )
            self.after(0, self.ubs_portfolio_status.set, "Optimizando incrementos de 0.01...")
            result = optimize_portfolio(
                raw_sets=raw_sets,
                capital=float(inputs["capital"]),
                valley_dd_pct=float(inputs["valley_dd_pct"]),
                point_dd_pct=float(inputs["point_dd_pct"]),
                portfolio_type=PortfolioType(str(inputs["portfolio_type"])),
                min_trades_2020_2026=int(inputs["min_trades_2020_2026"]),
                top_k_per_symbol=int(inputs["top_k_per_symbol"]),
                max_total_candidates=int(inputs["max_total_candidates"]),
                max_units_per_set=inputs["max_units_per_set"],  # type: ignore[arg-type]
                max_total_units=inputs["max_total_units"],  # type: ignore[arg-type]
                max_units_per_symbol=inputs["max_units_per_symbol"],  # type: ignore[arg-type]
                max_sets_per_symbol=inputs["max_sets_per_symbol"],  # type: ignore[arg-type]
                run_local_search=bool(inputs["run_local_search"]),
            )
            result.warnings[:0] = load_warnings
        except Exception as exc:
            self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": f"Error generando portafolio: {exc}"})
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass
        self.after(0, self._ubs_portfolio_finished, {
            "ok": True,
            "inputs": inputs,
            "availability": availability,
            "result": result,
        })

    def _ubs_portfolio_finished(self, info: dict) -> None:
        self._set_ubs_portfolio_running(False)
        if not info.get("ok"):
            message = info.get("error", "Error desconocido")
            self.ubs_portfolio_status.set(message)
            messagebox.showerror("Portfolio Builder", message)
            return

        result: PortfolioResult = info["result"]
        self.ubs_portfolio_pending_result = result
        self.ubs_portfolio_pending_inputs = info["inputs"]
        self._populate_ubs_portfolio_result(result)
        self._populate_ubs_portfolio_availability(info.get("availability"))
        self._set_ubs_portfolio_save_enabled(True)
        self.ubs_portfolio_status.set(
            f"Portafolio generado: {result.total_units} unidades, "
            f"DD valle {result.valley_usage_pct:.1f}%, DD puntual {result.point_usage_pct:.1f}%."
        )

    def _save_pending_ubs_portfolio(self) -> None:
        result: PortfolioResult | None = getattr(self, "ubs_portfolio_pending_result", None)
        inputs: dict[str, object] | None = getattr(self, "ubs_portfolio_pending_inputs", None)
        if result is None or inputs is None:
            messagebox.showinfo("Guardar portafolio", "Genera un portafolio valido antes de guardarlo.")
            return
        if not result.allocations:
            messagebox.showwarning("Guardar portafolio", "El portafolio no tiene asignaciones.")
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio_id = self._insert_portfolio(conn, inputs, result)
        finally:
            conn.close()
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self._set_ubs_portfolio_save_enabled(False)
        self._refresh_ubs_portfolios(select_id=portfolio_id)
        self.ubs_portfolio_status.set(f"Portafolio #{portfolio_id} guardado.")

    # ------------------------------------------------------------------ refresh/display
    def _refresh_ubs_portfolio_availability(self) -> None:
        if not hasattr(self, "ubs_portfolio_availability_tree"):
            return
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_portfolio_availability.set("Memoria UBS no encontrada.")
            self._populate_ubs_portfolio_availability(None)
            return
        conn = self._ubs_portfolio_conn()
        try:
            availability = self._portfolio_availability(conn)
        finally:
            conn.close()
        self._populate_ubs_portfolio_availability(availability)

    def _populate_ubs_portfolio_availability(self, availability: PortfolioAvailability | None) -> None:
        if not hasattr(self, "ubs_portfolio_availability_tree"):
            return
        tree = self.ubs_portfolio_availability_tree
        for item in tree.get_children(""):
            tree.delete(item)
        if availability is None:
            self.ubs_portfolio_availability.set("Disponibilidad: sin datos")
            return
        self.ubs_portfolio_availability.set(
            f"Sets robustos accepted: {availability.robust_accepted} | "
            f"Sets ya usados: {availability.already_used} | "
            f"Sets disponibles: {availability.available} | "
            f"Simbolos disponibles: {availability.symbols_available}"
        )
        for symbol, count in availability.by_symbol.items():
            tree.insert("", "end", values=(symbol, count))

    def _refresh_ubs_portfolios(self, select_id: int | None = None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        self._refresh_ubs_portfolio_availability()
        tree = self.ubs_portfolio_saved_tree
        for item in tree.get_children(""):
            tree.delete(item)
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_portfolio_status.set("Memoria UBS no encontrada.")
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolios = self._list_portfolios(conn)
        finally:
            conn.close()

        target_item = None
        for row in portfolios:
            type_key = str(row["portfolio_type"] or row["type"] or "")
            capital = float(row["capital"] or row["account_capital"] or 0)
            values = (
                row["id"],
                row["created_at"],
                PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key),
                f"{capital:,.0f}",
                f"{float(row['total_net_profit'] or 0):,.0f}",
                f"{float(row['actual_valley_dd'] or 0):,.2f}",
                f"{float(row['valley_usage_pct'] or 0):.1f}%",
                f"{float(row['actual_point_dd'] or 0):,.2f}",
                f"{float(row['point_usage_pct'] or 0):.1f}%",
                int(row["total_units"] or 0),
                int(row["active_strategies"] or 0),
            )
            item = tree.insert("", "end", iid=str(row["id"]), values=values)
            if select_id is not None and int(row["id"]) == int(select_id):
                target_item = item

        if target_item is None and portfolios:
            target_item = str(portfolios[0]["id"])
        if target_item is not None:
            tree.selection_set(target_item)
            tree.focus(target_item)
            self._populate_ubs_portfolio_saved(int(target_item))
        else:
            self._clear_ubs_portfolio_result_tables()
            self.ubs_portfolio_status.set("Sin portafolios guardados todavia.")

    def _on_ubs_portfolio_select(self, _event=None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            return
        try:
            self._populate_ubs_portfolio_saved(int(selection[0]))
        except ValueError:
            pass

    def _clear_ubs_portfolio_result_tables(self) -> None:
        for tree_name in (
            "ubs_portfolio_members_tree",
            "ubs_portfolio_decision_tree",
            "ubs_portfolio_unused_tree",
        ):
            tree = getattr(self, tree_name, None)
            if tree is None:
                continue
            for item in tree.get_children(""):
                tree.delete(item)
        for var in (
            "ubs_portfolio_metric_net",
            "ubs_portfolio_metric_valley",
            "ubs_portfolio_metric_point",
            "ubs_portfolio_metric_count",
            "ubs_portfolio_metric_lot",
            "ubs_portfolio_metric_units",
        ):
            if hasattr(self, var):
                getattr(self, var).set("-")
        self.ubs_portfolio_member_paths = {}
        self._draw_ubs_portfolio_curve([])

    def _populate_ubs_portfolio_result(self, result: PortfolioResult) -> None:
        self._clear_ubs_portfolio_result_tables()
        self._set_portfolio_metrics_from_result(result)
        self._populate_ubs_portfolio_allocations([asdict(item) for item in result.allocations])
        self._populate_ubs_portfolio_decisions([asdict(item) for item in result.decision_log])
        self._populate_ubs_portfolio_unused([asdict(item) for item in result.unused_sets])
        self._draw_ubs_portfolio_curve(result.equity_curve_2020_2026)

    def _populate_ubs_portfolio_saved(self, portfolio_id: int) -> None:
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
            decisions = [dict(row) for row in self._portfolio_decisions(conn, portfolio_id)]
        finally:
            conn.close()
        if portfolio is None:
            return
        self._clear_ubs_portfolio_result_tables()
        self.ubs_portfolio_metric_net.set(f"{float(portfolio['total_net_profit'] or 0):,.0f}")
        self.ubs_portfolio_metric_valley.set(
            f"{float(portfolio['actual_valley_dd'] or 0):,.2f} ({float(portfolio['valley_usage_pct'] or 0):.1f}%)"
        )
        self.ubs_portfolio_metric_point.set(
            f"{float(portfolio['actual_point_dd'] or 0):,.2f} ({float(portfolio['point_usage_pct'] or 0):.1f}%)"
        )
        self.ubs_portfolio_metric_count.set(str(int(portfolio["active_strategies"] or len(members))))
        self.ubs_portfolio_metric_lot.set(f"{float(portfolio['total_lot'] or 0):.2f}")
        self.ubs_portfolio_metric_units.set(str(int(portfolio["total_units"] or 0)))
        self._populate_ubs_portfolio_allocations(members)
        self._populate_ubs_portfolio_decisions(decisions)
        metrics = self._portfolio_metrics_json(portfolio)
        self._populate_ubs_portfolio_unused(metrics.get("unused_sets", []))
        self._draw_ubs_portfolio_curve(metrics.get("equity_curve_2020_2026", []))

    def _set_portfolio_metrics_from_result(self, result: PortfolioResult) -> None:
        self.ubs_portfolio_metric_net.set(f"{result.total_net_profit:,.0f}")
        self.ubs_portfolio_metric_valley.set(f"{result.actual_valley_dd:,.2f} ({result.valley_usage_pct:.1f}%)")
        self.ubs_portfolio_metric_point.set(f"{result.actual_point_dd:,.2f} ({result.point_usage_pct:.1f}%)")
        self.ubs_portfolio_metric_count.set(str(result.active_strategies))
        self.ubs_portfolio_metric_lot.set(f"{result.total_lot:.2f}")
        self.ubs_portfolio_metric_units.set(str(result.total_units))

    def _populate_ubs_portfolio_allocations(self, members: list[dict[str, object]]) -> None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return
        tree = self.ubs_portfolio_members_tree
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_member_paths = {}
        for member in members:
            set_id = str(member.get("set_id") or member.get("set_path") or "")
            set_path = str(member.get("set_path") or set_id)
            units = int(member.get("units") or 0)
            lot = float(member.get("lot") or 0)
            step = member.get("lot_size_step")
            values = (
                Path(set_id).name,
                str(member.get("candidate_id") or ""),
                str(member.get("symbol") or ""),
                str(member.get("timeframe") or member.get("period") or ""),
                units,
                f"{lot:.2f}",
                f"{float(member.get('net_profit_contribution') or 0):,.0f}",
                f"{float(member.get('standalone_valley_dd') or 0):,.2f}",
                f"{float(member.get('standalone_point_dd') or 0):,.2f}",
                f"{float(step):,.2f}" if step not in (None, "") else "-",
            )
            item = tree.insert("", "end", values=values)
            self.ubs_portfolio_member_paths[item] = {
                "set_path": set_path,
                "is": str(member.get("is_report_path") or ""),
                "oos": str(member.get("oos_report_path") or ""),
            }

    def _populate_ubs_portfolio_decisions(self, decisions: list[dict[str, object]]) -> None:
        if not hasattr(self, "ubs_portfolio_decision_tree"):
            return
        tree = self.ubs_portfolio_decision_tree
        for item in tree.get_children(""):
            tree.delete(item)
        for decision in decisions:
            tree.insert(
                "",
                "end",
                values=(
                    decision.get("step"),
                    decision.get("action"),
                    Path(str(decision.get("set_id") or "")).name,
                    Path(str(decision.get("from_set_id") or "")).name,
                    Path(str(decision.get("to_set_id") or "")).name,
                    f"{float(decision.get('gain') or 0):,.2f}",
                    f"{float(decision.get('valley_cost') or 0):,.2f}",
                    f"{float(decision.get('point_cost') or 0):,.2f}",
                    f"{float(decision.get('score') or 0):,.2f}",
                    f"{float(decision.get('portfolio_net_profit_after') or 0):,.2f}",
                    f"{float(decision.get('portfolio_valley_dd_after') or 0):,.2f}",
                    f"{float(decision.get('portfolio_point_dd_after') or 0):,.2f}",
                    decision.get("reason") or "",
                ),
            )

    def _populate_ubs_portfolio_unused(self, unused: list[dict[str, object]]) -> None:
        if not hasattr(self, "ubs_portfolio_unused_tree"):
            return
        tree = self.ubs_portfolio_unused_tree
        for item in tree.get_children(""):
            tree.delete(item)
        for item in unused[:200]:
            tree.insert(
                "",
                "end",
                values=(
                    Path(str(item.get("set_id") or "")).name,
                    item.get("symbol") or "",
                    f"{float(item.get('score') or 0):,.2f}",
                    item.get("reason") or "",
                ),
            )

    def _portfolio_metrics_json(self, portfolio: sqlite3.Row) -> dict[str, object]:
        raw = portfolio["metrics_json"]
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _draw_ubs_portfolio_curve(self, values: list[float]) -> None:
        canvas = getattr(self, "ubs_portfolio_curve_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(int(canvas.winfo_width()), 1)
        height = max(int(canvas.winfo_height()), 1)
        if width <= 1:
            canvas.after(60, lambda: self._draw_ubs_portfolio_curve(values))
            return
        if len(values) < 2:
            canvas.create_text(
                width // 2,
                height // 2,
                text="Sin curva",
                fill=self.colors["muted"],
                font=("Segoe UI", 9),
            )
            return
        low = min(values)
        high = max(values)
        span = high - low or 1.0
        pad = 10
        points: list[float] = []
        for index, value in enumerate(values):
            x = pad + (width - pad * 2) * index / max(len(values) - 1, 1)
            y = height - pad - (height - pad * 2) * (value - low) / span
            points.extend([x, y])
        canvas.create_line(*points, fill=self.colors["accent"], width=2, smooth=True)
        zero_y = height - pad - (height - pad * 2) * (0.0 - low) / span
        if pad <= zero_y <= height - pad:
            canvas.create_line(pad, zero_y, width - pad, zero_y, fill=self.colors["border"], dash=(3, 3))

    # ------------------------------------------------------------------ actions
    def _delete_selected_ubs_portfolio(self) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            messagebox.showinfo("Portfolio Builder", "Selecciona un portafolio para borrar.")
            return
        portfolio_id = int(selection[0])
        if not messagebox.askyesno(
            "Borrar portafolio",
            "Se borrara el portafolio y sus sets volveran a estar disponibles.\n\nContinuar?",
        ):
            return
        conn = self._ubs_portfolio_conn()
        try:
            self._delete_portfolio(conn, portfolio_id)
        finally:
            conn.close()
        self._refresh_ubs_portfolios()
        self.ubs_portfolio_status.set(f"Portafolio #{portfolio_id} borrado.")

    def _open_selected_ubs_portfolio_member(self) -> None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return
        selection = self.ubs_portfolio_members_tree.selection()
        if not selection:
            return
        paths = getattr(self, "ubs_portfolio_member_paths", {}).get(selection[0], {})
        report = paths.get("oos") or paths.get("is")
        if report:
            self._open_local_file(Path(report))

    def _export_ubs_portfolio_sets(self) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            messagebox.showinfo("Exportar sets", "Selecciona un portafolio guardado para exportar.")
            return
        portfolio_id = int(selection[0])
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        if portfolio is None or not members:
            messagebox.showinfo("Exportar sets", "El portafolio no tiene estrategias que exportar.")
            return

        folder = filedialog.askdirectory(title="Carpeta destino para los sets del portafolio")
        if not folder:
            return
        dest = Path(folder)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Exportar sets", f"No pude crear la carpeta:\n{exc}")
            return

        capital = float(portfolio["capital"] or portfolio["account_capital"] or 0)
        exported: list[tuple[str, str, float, int, str]] = []
        missing: list[str] = []
        not_found_key: list[str] = []
        for member in members:
            set_path = Path(str(member.get("set_path") or member.get("set_id") or ""))
            if not set_path.is_file():
                missing.append(set_path.name)
                continue
            try:
                text, encoding = read_set_with_encoding(set_path)
            except Exception:
                missing.append(set_path.name)
                continue
            step = float(member.get("lot_size_step") or 0)
            if step <= 0:
                units = int(member.get("units") or 0)
                step = math.ceil(capital / units * 100.0) / 100.0 if units > 0 else 0
            new_text, step_int, found = apply_portfolio_lot_text(text, step)
            if not found:
                not_found_key.append(set_path.name)
            units = int(capital // step_int) if step_int > 0 else 0
            real_lot = round(units * 0.01, 2)
            out_path = dest / set_path.name
            write_set_text(out_path, new_text, encoding)
            exported.append((
                str(member.get("symbol") or ""),
                str(member.get("timeframe") or member.get("period") or ""),
                real_lot,
                step_int,
                set_path.name,
            ))

        resumen = dest / f"PORTAFOLIO_{portfolio_id}_resumen.txt"
        type_key = str(portfolio["portfolio_type"] or portfolio["type"] or "")
        lines = [
            f"Portafolio: {portfolio['name']}",
            f"Tipo: {PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key)}   Capital: {capital:,.0f}",
            f"DD valle objetivo: {float(portfolio['target_valley_dd'] or 0):,.2f}",
            f"DD puntual objetivo: {float(portfolio['target_point_dd'] or 0):,.2f}",
            f"DD valle usado: {float(portfolio['actual_valley_dd'] or 0):,.2f}",
            f"DD puntual usado: {float(portfolio['actual_point_dd'] or 0):,.2f}",
            f"Net profit total 2020-2026: {float(portfolio['total_net_profit'] or 0):,.2f}",
            "",
            "Modo de lote exportado: Risk=2.",
            "El EA aplica Lots = floor(AccountBalance / LotPerBalance_step) * 0.01",
            f"Calculado para balance {capital:,.0f}.",
            "",
            f"{'SIMBOLO':12s} {'TF':5s} {'LOTE':>7s} {'LotPerBalance_step':>20s}   SET",
        ]
        for symbol, period, real_lot, step_int, name in exported:
            lines.append(f"{symbol:12s} {period:5s} {real_lot:7.2f} {step_int:20d}   {name}")
        if missing:
            lines.append("")
            lines.append("OMITIDOS (set no encontrado): " + ", ".join(missing))
        if not_found_key:
            lines.append("")
            lines.append("AVISO (sin clave LotPerBalance_step): " + ", ".join(not_found_key))
        write_set_text(resumen, "\n".join(lines), "utf-8")

        self.ubs_portfolio_status.set(f"Exportados {len(exported)} set(s) a {dest}")
        messagebox.showinfo("Exportar sets", f"Exportados {len(exported)} set(s) a:\n{dest}\n\nResumen: {resumen.name}")
        self._open_local_file(dest)
