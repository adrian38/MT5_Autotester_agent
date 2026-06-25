from __future__ import annotations

from dataclasses import asdict
import json
import re
import shutil
import sqlite3
import sys
import threading
import zlib
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from ubs.account import ACCOUNT_TYPES, account_memory_path
from ubs.db import connect_memory
from ubs.set_utils import write_set_text
from portfolio_manager.ubs_portfolio import (
    PortfolioAvailability,
    PortfolioResult,
    PortfolioType,
    bootstrap_valley_drawdown,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    evaluate_portfolio,
    load_robust_sets_from_rows,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
    portfolio_group_summary,
    portfolio_margin_summary,
    portfolio_symbol_key,
    slice_strategy_sets_to_month,
    summarize_robust_rows,
    validate_strict_monthly_portfolio,
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
    "use_correlation": True,
    "require_3_positive_months_6m": False,
    "dd_reserve_pct": "10",
    "search_restarts": 4,
    "max_pair_corr": "0.35",
    "max_downside_corr": "0.25",
    "max_dd_overlap": "0.35",
    "max_portfolio_corr": "0.50",
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
                target_strategies integer not null default 0,
                stop_reason text not null default '',
                scale_factor real,
                binding_constraint text,
                portfolio_scope text not null default 'full_history',
                target_month integer,
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
            ("target_strategies", "integer not null default 0"),
            ("stop_reason", "text not null default ''"),
            ("scale_factor", "real"),
            ("binding_constraint", "text"),
            ("portfolio_scope", "text not null default 'full_history'"),
            ("target_month", "integer"),
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
                margin_required real not null default 0,
                margin_pct real not null default 0,
                margin_leverage real not null default 0,
                margin_contract_size real not null default 0,
                margin_price real not null default 0,
                is_report_path text,
                oos_report_path text,
                foreign key (portfolio_id) references portfolios(id)
            )
            """
        )
        for column, definition in (
            ("margin_required", "real not null default 0"),
            ("margin_pct", "real not null default 0"),
            ("margin_leverage", "real not null default 0"),
            ("margin_contract_size", "real not null default 0"),
            ("margin_price", "real not null default 0"),
        ):
            self._ensure_sqlite_column(conn, "portfolio_allocations", column, definition)
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
        conn.execute(
            """
            create table if not exists portfolio_quarantine (
                id integer primary key autoincrement,
                account_type text not null,
                candidate_id integer,
                set_path text not null unique,
                symbol text,
                timeframe text,
                reason text not null default '',
                source_portfolio_id integer,
                quarantined_at text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists portfolio_versions (
                id integer primary key autoincrement,
                portfolio_id integer not null,
                version_no integer not null,
                created_at text not null,
                reason text not null,
                snapshot_json blob not null,
                unique(portfolio_id, version_no)
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

    def _ubs_portfolio_conn_for_memory(self, memory_path: Path) -> sqlite3.Connection:
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        self._ensure_ubs_base_tables_for_portfolio(conn)
        self._ensure_ubs_memory_schema(conn)
        self._ensure_portfolio_schema(conn)
        return conn

    def _ubs_portfolio_conn(self) -> sqlite3.Connection:
        return self._ubs_portfolio_conn_for_memory(self._ubs_memory_path())

    def _ubs_portfolio_source_paths(self) -> list[tuple[str, Path]]:
        paths: list[tuple[str, Path]] = []
        for account_type in ACCOUNT_TYPES:
            path = account_memory_path(BASE_DIR, account_type)
            if path.exists():
                paths.append((account_type, path))
        return paths

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
    def _final_tick_passed_candidates(
        self,
        conn: sqlite3.Connection,
        account_type: str,
        *,
        include_quarantined: bool = False,
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            select ? as account_type,
                   ? || ':' || c.id as candidate_id,
                   c.id as source_candidate_id,
                   c.set_path, c.symbol, c.target_symbol,
                   c.period, c.family,
                   c.report_path as is_report_path,
                   cr.report_path as oos_report_path,
                   ft6.ohlc_report_path as final_ohlc_report_path,
                   ft6.real_tick_report_path as final_tick_report_path,
                   ft6.from_date as final_tick_from_date,
                   ft6.to_date as final_tick_to_date
            from candidates c
            join candidate_robustness cr on cr.candidate_id = c.id
            join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
            where c.status = 'accepted'
              and cr.status = 'accepted'
              and ft6.status = 'accepted'
              and (? = 1 or not exists (
                  select 1 from portfolio_quarantine pq
                  where pq.set_path = c.set_path
              ))
            order by c.id
            """,
            (account_type, account_type, 1 if include_quarantined else 0),
        ).fetchall()

    def _final_tick_passed_candidates_all_accounts(
        self,
        *,
        include_quarantined: bool = False,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                rows.extend(
                    dict(row)
                    for row in self._final_tick_passed_candidates(
                        conn,
                        account_type,
                        include_quarantined=include_quarantined,
                    )
                )
            finally:
                conn.close()
        return rows

    def _portfolio_type_from_label(self, value: object) -> PortfolioType:
        text = str(value or "").strip()
        if text in PORTFOLIO_TYPE_LABELS:
            return PORTFOLIO_TYPE_LABELS[text]
        try:
            return PortfolioType(text.lower())
        except ValueError:
            return PortfolioType.BALANCED

    def _used_set_paths(
        self,
        conn: sqlite3.Connection,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[str]:
        if target_portfolio_type == PortfolioType.AGGRESSIVE:
            type_filter = (
                "and lower(coalesce(nullif(p.portfolio_type, ''), nullif(p.type, ''), '')) = 'aggressive'"
            )
        else:
            type_filter = (
                "and lower(coalesce(nullif(p.portfolio_type, ''), nullif(p.type, ''), '')) <> 'aggressive'"
            )
        rows = conn.execute(
            f"""
            select pa.set_path
            from portfolio_allocations pa
            join portfolios p on p.id = pa.portfolio_id
            where pa.set_path is not null and pa.set_path <> ''
              {type_filter}
              and coalesce(nullif(p.portfolio_scope, ''), 'full_history') = ?
              and (? is null or p.target_month = ?)
              and (? is null or pa.portfolio_id <> ?)
            union
            select pm.set_path
            from portfolio_members pm
            join portfolios p on p.id = pm.portfolio_id
            where pm.set_path is not null and pm.set_path <> ''
              {type_filter}
              and coalesce(nullif(p.portfolio_scope, ''), 'full_history') = ?
              and (? is null or p.target_month = ?)
              and (? is null or pm.portfolio_id <> ?)
            """
            ,
            (
                portfolio_scope,
                target_month,
                target_month,
                exclude_portfolio_id,
                exclude_portfolio_id,
                portfolio_scope,
                target_month,
                target_month,
                exclude_portfolio_id,
                exclude_portfolio_id,
            ),
        ).fetchall()
        return [str(row["set_path"]) for row in rows]

    def _used_set_paths_all_accounts(
        self,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[str]:
        used: set[str] = set()
        active_memory = self._ubs_memory_path().resolve()
        for _account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                excluded = exclude_portfolio_id if memory_path.resolve() == active_memory else None
                used.update(
                    self._used_set_paths(
                        conn,
                        target_portfolio_type,
                        exclude_portfolio_id=excluded,
                        portfolio_scope=portfolio_scope,
                        target_month=target_month,
                    )
                )
            finally:
                conn.close()
        return sorted(used)

    def _portfolio_availability(
        self,
        _conn: sqlite3.Connection | None = None,
        *,
        target_portfolio_type: PortfolioType | None = None,
    ) -> PortfolioAvailability:
        target_portfolio_type = target_portfolio_type or self._portfolio_type_from_label(self.ubs_portfolio_type.get())
        rows = self._final_tick_passed_candidates_all_accounts()
        grid_off_var = getattr(self, "ubs_portfolio_grid_off", None)
        if grid_off_var is not None and bool(grid_off_var.get()):
            rows, _warnings = filter_rows_grid_off(rows)
        used = self._used_set_paths_all_accounts(target_portfolio_type)
        return summarize_robust_rows(rows, used)

    def _insert_portfolio(
        self,
        conn: sqlite3.Connection,
        inputs: dict[str, object],
        result: PortfolioResult,
    ) -> int:
        created_at = datetime.now().isoformat(timespec="seconds")
        portfolio_type = str(inputs["portfolio_type"])
        portfolio_scope = str(inputs.get("portfolio_scope") or "full_history")
        target_month = int(inputs["target_month"]) if inputs.get("target_month") else None
        name = (
            f"{PORTFOLIO_TYPE_DISPLAY.get(portfolio_type, portfolio_type)} | "
            + (f"Mes {target_month:02d} | " if target_month else "")
            + f"{result.active_strategies} estrategias | {datetime.now():%d.%m.%Y %H:%M}"
        )
        active_symbols = len({portfolio_symbol_key(allocation.symbol) for allocation in result.allocations if allocation.units > 0})
        metrics = {
            "inputs": inputs,
            "warnings": result.warnings,
            "group_summary": result.group_summary,
            "equity_curve_2020_2026": result.equity_curve_2020_2026,
            "unused_sets": [asdict(item) for item in result.unused_sets],
            "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
            "seasonal_coverage": result.seasonal_coverage,
            "seasonal_validation": result.seasonal_validation,
            "margin_summary": result.margin_summary,
        }
        cur = conn.execute(
            """
            insert into portfolios (
                created_at, name, type, portfolio_type, num_symbols, account_capital,
                capital, target_valley_dd_pct, target_point_dd_pct, target_valley_dd,
                target_point_dd, actual_valley_dd, actual_point_dd, valley_usage_pct,
                point_usage_pct, total_net_profit, total_lot, total_units,
                active_strategies, target_strategies, stop_reason, binding_constraint,
                portfolio_scope, target_month, metrics_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                result.active_strategies,
                result.stop_reason,
                "valley" if result.valley_usage_pct >= result.point_usage_pct else "point",
                portfolio_scope,
                target_month,
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
                    set_path, timeframe, lot_size_step, margin_required, margin_pct,
                    margin_leverage, margin_contract_size, margin_price,
                    is_report_path, oos_report_path
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    allocation.margin_required,
                    allocation.margin_pct,
                    allocation.margin_leverage,
                    allocation.margin_contract_size,
                    allocation.margin_price,
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

    def _list_portfolios(
        self,
        conn: sqlite3.Connection,
        *,
        portfolio_scope: str = "full_history",
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            select * from portfolios
            where coalesce(nullif(portfolio_scope, ''), 'full_history') = ?
            order by id desc
            """,
            (portfolio_scope,),
        ).fetchall()

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
        conn.execute("delete from portfolio_versions where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolios where id=?", (portfolio_id,))
        conn.commit()

    def _save_portfolio_version(
        self,
        conn: sqlite3.Connection,
        portfolio_id: int,
        reason: str,
    ) -> int:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe.")
        payload = {
            "portfolio": dict(portfolio),
            "allocations": [
                dict(row) for row in conn.execute(
                    "select * from portfolio_allocations where portfolio_id=? order by id",
                    (portfolio_id,),
                )
            ],
            "members": [
                dict(row) for row in conn.execute(
                    "select * from portfolio_members where portfolio_id=? order by id",
                    (portfolio_id,),
                )
            ],
            "decisions": [
                dict(row) for row in conn.execute(
                    "select * from portfolio_decision_log where portfolio_id=? order by id",
                    (portfolio_id,),
                )
            ],
        }
        version_no = int(
            conn.execute(
                "select coalesce(max(version_no), 0) + 1 from portfolio_versions where portfolio_id=?",
                (portfolio_id,),
            ).fetchone()[0]
        )
        compressed = zlib.compress(json.dumps(payload, ensure_ascii=True).encode("utf-8"), level=6)
        conn.execute(
            """
            insert into portfolio_versions (
                portfolio_id, version_no, created_at, reason, snapshot_json
            ) values (?, ?, ?, ?, ?)
            """,
            (
                portfolio_id,
                version_no,
                datetime.now().isoformat(timespec="seconds"),
                reason,
                compressed,
            ),
        )
        return version_no

    def _restore_portfolio_version_payload(
        self,
        conn: sqlite3.Connection,
        portfolio_id: int,
        snapshot_json: bytes,
    ) -> None:
        payload = json.loads(zlib.decompress(snapshot_json).decode("utf-8"))
        portfolio = dict(payload["portfolio"])
        portfolio.pop("id", None)
        columns = list(portfolio)
        assignments = ", ".join(f"{column}=?" for column in columns)
        conn.execute(
            f"update portfolios set {assignments} where id=?",
            [portfolio[column] for column in columns] + [portfolio_id],
        )
        for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members"):
            conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))

        def restore_rows(table: str, rows: list[dict[str, object]]) -> None:
            for raw_row in rows:
                row = dict(raw_row)
                row.pop("id", None)
                row["portfolio_id"] = portfolio_id
                row_columns = list(row)
                placeholders = ", ".join("?" for _ in row_columns)
                conn.execute(
                    f"insert into {table} ({', '.join(row_columns)}) values ({placeholders})",
                    [row[column] for column in row_columns],
                )

        restore_rows("portfolio_allocations", list(payload.get("allocations") or []))
        restore_rows("portfolio_members", list(payload.get("members") or []))
        restore_rows("portfolio_decision_log", list(payload.get("decisions") or []))

    def _undo_latest_ubs_portfolio_completion(self, portfolio_id: int) -> None:
        conn = self._ubs_portfolio_conn()
        try:
            version = conn.execute(
                """
                select * from portfolio_versions
                where portfolio_id=? order by version_no desc limit 1
                """,
                (portfolio_id,),
            ).fetchone()
            if version is None:
                messagebox.showinfo("Deshacer recomposicion", "No hay una version anterior guardada.")
                return
            if not messagebox.askyesno(
                "Deshacer recomposicion",
                f"Restaurar la version {version['version_no']} ({version['reason']})?",
            ):
                return
            self._save_portfolio_version(
                conn,
                portfolio_id,
                f"Antes de deshacer hacia version {version['version_no']}",
            )
            self._restore_portfolio_version_payload(conn, portfolio_id, bytes(version["snapshot_json"]))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            messagebox.showerror("Deshacer recomposicion", f"No se pudo restaurar la version:\n{exc}")
            return
        finally:
            conn.close()
        self._refresh_ubs_portfolios(select_id=portfolio_id)
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios(select_id=portfolio_id)
        self._populate_ubs_portfolio_detail(portfolio_id)
        self.ubs_portfolio_status.set(f"Portafolio #{portfolio_id} restaurado desde version anterior.")

    def _portfolio_quarantine_rows_all_accounts(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                for row in conn.execute(
                    "select * from portfolio_quarantine order by quarantined_at desc, id desc"
                ):
                    item = dict(row)
                    item["account_type"] = account_type
                    item["memory_path"] = str(memory_path)
                    rows.append(item)
            finally:
                conn.close()
        rows.sort(key=lambda item: str(item.get("quarantined_at") or ""), reverse=True)
        return rows

    def _resolve_portfolio_member_source(
        self,
        member: dict[str, object],
    ) -> tuple[str, Path, int | None]:
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        account = self._ubs_portfolio_member_account(member)
        candidate_label = self._ubs_portfolio_member_candidate_label(member)
        candidate_id = int(candidate_label) if candidate_label.isdigit() else None
        sources = self._ubs_portfolio_source_paths()
        if account:
            sources.sort(key=lambda item: item[0] != account)
        for source_account, memory_path in sources:
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                row = None
                if candidate_id is not None:
                    row = conn.execute(
                        "select id from candidates where id=? and set_path=?",
                        (candidate_id, set_path),
                    ).fetchone()
                if row is None:
                    row = conn.execute(
                        "select id from candidates where set_path=? order by id desc limit 1",
                        (set_path,),
                    ).fetchone()
                if row is not None:
                    return source_account, memory_path, int(row["id"])
            finally:
                conn.close()
        account_var = getattr(self, "ubs_account_type", None)
        current_account = str(account_var.get()) if account_var is not None else "ECN"
        fallback_account = account or current_account
        return fallback_account, account_memory_path(BASE_DIR, fallback_account), candidate_id

    def _recalculate_saved_portfolio(self, conn: sqlite3.Connection, portfolio_id: int) -> None:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe.")
        members = self._portfolio_members(conn, portfolio_id)
        metrics = self._portfolio_metrics_json(portfolio)
        if not members:
            metrics["equity_curve_2020_2026"] = [0.0]
            metrics["group_summary"] = {}
            metrics["seasonal_coverage"] = {}
            metrics["seasonal_validation"] = {}
            metrics["stress_bootstrap"] = asdict(
                bootstrap_valley_drawdown(
                    [0.0],
                    nominal_valley_dd_limit=(
                        float(portfolio["capital"] or portfolio["account_capital"] or 0)
                        * float(portfolio["target_valley_dd_pct"] or 0)
                        / 100.0
                    ),
                    effective_valley_dd_limit=float(portfolio["target_valley_dd"] or 0),
                )
            )
            conn.execute(
                """
                update portfolios
                set num_symbols=0, actual_valley_dd=0, actual_point_dd=0,
                    valley_usage_pct=0, point_usage_pct=0, total_net_profit=0,
                    total_lot=0, total_units=0, active_strategies=0,
                    metrics_json=?
                where id=?
                """,
                (json.dumps(metrics, ensure_ascii=True), portfolio_id),
            )
            return

        rows = [
            {
                "candidate_id": member.get("candidate_id"),
                "set_path": member.get("set_path") or member.get("set_id"),
                "symbol": member.get("symbol"),
                "target_symbol": member.get("symbol"),
                "period": member.get("timeframe") or member.get("period"),
                "family": "",
                "is_report_path": member.get("is_report_path"),
                "oos_report_path": member.get("oos_report_path"),
            }
            for member in members
        ]
        strategies, warnings = load_robust_sets_from_rows(rows, [])
        if len(strategies) != len(members):
            raise ValueError("No se pudieron reconstruir todas las curvas restantes.")
        full_strategies = list(strategies)
        saved_inputs = self._saved_portfolio_inputs(portfolio)
        strategies, scope_warnings = self._scope_portfolio_sets(
            strategies,
            saved_inputs,
        )
        warnings.extend(scope_warnings)
        if len(strategies) != len(members):
            raise ValueError("No se pudieron reconstruir todas las curvas del mes objetivo.")
        units = {
            str(member.get("set_path") or member.get("set_id")): int(member.get("units") or 0)
            for member in members
        }
        target_valley = float(portfolio["target_valley_dd"] or 0)
        target_point = float(portfolio["target_point_dd"] or 0)
        evaluation = evaluate_portfolio(strategies, units, target_valley, target_point)
        metrics["equity_curve_2020_2026"] = evaluation.equity_curve_2020_2026
        metrics["group_summary"] = portfolio_group_summary(strategies, units)
        metrics["seasonal_coverage"] = {
            strategy.set_id: {
                "target_month": strategy.target_month,
                "years": list(strategy.month_years),
                "positive_years": list(strategy.positive_month_years),
                "year_count": len(strategy.month_years),
                "positive_year_count": len(strategy.positive_month_years),
                "trades": strategy.trades_2020_2026,
            }
            for strategy in strategies
            if strategy.target_month is not None and units.get(strategy.set_id, 0) > 0
        }
        if bool(saved_inputs.get("strict_yearly_month_validation")):
            metrics["seasonal_validation"] = validate_strict_monthly_portfolio(
                full_strategies,
                units,
                target_month=int(saved_inputs.get("target_month") or 0),
                target_valley_dd=target_valley,
                target_point_dd=target_point,
                lookback_years=5,
            )
        else:
            metrics["seasonal_validation"] = {}
        metrics["stress_bootstrap"] = asdict(
            bootstrap_valley_drawdown(
                evaluation.equity_curve_2020_2026,
                nominal_valley_dd_limit=(
                    float(portfolio["capital"] or portfolio["account_capital"] or 0)
                    * float(portfolio["target_valley_dd_pct"] or 0)
                    / 100.0
                ),
                effective_valley_dd_limit=target_valley,
            )
        )
        if warnings:
            metrics.setdefault("warnings", []).extend(warnings)
        conn.execute(
            """
            update portfolios
            set num_symbols=?, actual_valley_dd=?, actual_point_dd=?,
                valley_usage_pct=?, point_usage_pct=?, total_net_profit=?,
                total_lot=?, total_units=?, active_strategies=?, metrics_json=?
            where id=?
            """,
            (
                len({portfolio_symbol_key(item.symbol) for item in strategies if units.get(item.set_id, 0) > 0}),
                evaluation.valley_dd,
                evaluation.point_dd,
                evaluation.valley_usage_pct,
                evaluation.point_usage_pct,
                evaluation.total_net_profit,
                evaluation.total_lot,
                evaluation.total_units,
                evaluation.active_strategies,
                json.dumps(metrics, ensure_ascii=True),
                portfolio_id,
            ),
        )

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
        values: dict[str, object] = {
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
            "use_correlation": bool(self.ubs_portfolio_use_correlation.get()),
            "require_3_positive_months_6m": bool(self.ubs_portfolio_require_3_positive_months_6m.get()),
            "grid_off": bool(getattr(self, "ubs_portfolio_grid_off").get())
            if hasattr(self, "ubs_portfolio_grid_off")
            else False,
            "dd_reserve_pct": self._parse_float_setting(
                self.ubs_portfolio_dd_reserve_pct.get(),
                "Reserva DD",
            ),
            "search_restarts": self._parse_int_setting(
                self.ubs_portfolio_search_restarts.get(),
                "Reinicios de busqueda",
                minimum=0,
            ),
            "max_pair_corr": self._parse_optional_float_setting(
                self.ubs_portfolio_max_pair_corr.get(),
                "Max correlacion",
            ),
            "max_downside_corr": self._parse_optional_float_setting(
                self.ubs_portfolio_max_downside_corr.get(),
                "Max correlacion downside",
            ),
            "max_dd_overlap": self._parse_optional_float_setting(
                self.ubs_portfolio_max_dd_overlap.get(),
                "Max solapamiento DD",
            ),
            "max_portfolio_corr": self._parse_optional_float_setting(
                self.ubs_portfolio_max_portfolio_corr.get(),
                "Max corr portafolios",
            ),
        }
        if not values["use_correlation"]:
            values["max_pair_corr"] = None
            values["max_downside_corr"] = None
            values["max_dd_overlap"] = None
            values["max_portfolio_corr"] = None
        for key, label in (
            ("max_pair_corr", "Max correlacion"),
            ("max_downside_corr", "Max correlacion downside"),
            ("max_dd_overlap", "Max solapamiento DD"),
            ("max_portfolio_corr", "Max corr portafolios"),
        ):
            value = values[key]
            if value is not None and not (0 <= float(value) <= 1):
                raise ValueError(f"{label} debe estar entre 0 y 1.")
        if not (0 <= float(values["dd_reserve_pct"]) < 100):
            raise ValueError("Reserva DD debe estar entre 0 y menos de 100.")
        return values

    def _parse_optional_float_setting(self, value: str, label: str) -> float | None:
        text = str(value).strip()
        if not text:
            return None
        parsed = self._parse_float_setting(text, label)
        return parsed

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
        self.ubs_portfolio_use_correlation.set(DEFAULT_PORTFOLIO_FORM["use_correlation"])
        self.ubs_portfolio_require_3_positive_months_6m.set(DEFAULT_PORTFOLIO_FORM["require_3_positive_months_6m"])
        if hasattr(self, "ubs_portfolio_grid_off"):
            self.ubs_portfolio_grid_off.set(False)
        self.ubs_portfolio_dd_reserve_pct.set(DEFAULT_PORTFOLIO_FORM["dd_reserve_pct"])
        self.ubs_portfolio_search_restarts.set(DEFAULT_PORTFOLIO_FORM["search_restarts"])
        self.ubs_portfolio_max_pair_corr.set(DEFAULT_PORTFOLIO_FORM["max_pair_corr"])
        self.ubs_portfolio_max_downside_corr.set(DEFAULT_PORTFOLIO_FORM["max_downside_corr"])
        self.ubs_portfolio_max_dd_overlap.set(DEFAULT_PORTFOLIO_FORM["max_dd_overlap"])
        self.ubs_portfolio_max_portfolio_corr.set(DEFAULT_PORTFOLIO_FORM["max_portfolio_corr"])
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self._set_ubs_portfolio_save_enabled(False)
        self._clear_ubs_portfolio_result_tables()
        self.ubs_portfolio_status.set("Formulario restaurado.")

    # ------------------------------------------------------------------ generate/save
    def _run_ubs_portfolio_build(self) -> None:
        if (
            getattr(self, "ubs_portfolio_running", False)
            or getattr(self, "ubs_monthly_portfolio_running", False)
        ):
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
        self._clear_ubs_portfolio_result_tables()
        self._set_ubs_portfolio_running(True)
        self.ubs_portfolio_status.set("Analizando sets Final Tick 6M accepted...")
        threading.Thread(target=self._ubs_portfolio_worker, args=(inputs,), daemon=True).start()

    def _ubs_portfolio_worker(self, inputs: dict[str, object]) -> None:
        try:
            target_portfolio_type = PortfolioType(str(inputs["portfolio_type"]))
            rows = self._final_tick_passed_candidates_all_accounts()
            used = self._used_set_paths_all_accounts(target_portfolio_type)
            existing_curves = self._saved_portfolio_curves_all_accounts(target_portfolio_type)
        except Exception as exc:
            self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": f"No pude abrir la memoria UBS: {exc}"})
            return
        try:
            if not rows:
                self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": "No hay candidatos con Final Tick 6M accepted en ECN/PRO."})
                return
            month_warnings: list[str] = []
            if bool(inputs.get("require_3_positive_months_6m")):
                rows, month_warnings = filter_rows_by_recent_positive_months(
                    rows,
                    min_positive_months=3,
                    window_months=6,
                    progress=lambda msg: self.after(0, self.ubs_portfolio_status.set, msg),
                )
                if not rows:
                    self.after(
                        0,
                        self._ubs_portfolio_finished,
                        {"ok": False, "error": "No quedan candidatos tras exigir 3 meses positivos en los ultimos 6."},
                    )
                    return
            grid_warnings: list[str] = []
            if bool(inputs.get("grid_off")):
                rows, grid_warnings = filter_rows_grid_off(rows)
                if not rows:
                    self.after(
                        0,
                        self._ubs_portfolio_finished,
                        {"ok": False, "error": "No quedan candidatos tras aplicar Grid OFF."},
                    )
                    return
            availability = summarize_robust_rows(rows, used)
            raw_sets, load_warnings = load_robust_sets_from_rows(
                rows,
                used,
                progress=lambda msg: self.after(0, self.ubs_portfolio_status.set, msg),
            )
            proposals = self._optimize_ubs_portfolio_proposals(
                raw_sets,
                inputs,
                target_portfolio_type,
                existing_curves,
                progress=lambda label, index: self.after(
                    0,
                    self.ubs_portfolio_status.set,
                    f"Calculando propuesta {index}/3 ({label})...",
                ),
            )
            for proposal in proposals:
                proposal["result"].warnings[:0] = month_warnings + grid_warnings + load_warnings
        except Exception as exc:
            self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": f"Error generando portafolio: {exc}"})
            return
        self.after(0, self._ubs_portfolio_finished, {
            "ok": True,
            "inputs": inputs,
            "availability": availability,
            "proposals": proposals,
        })

    def _saved_portfolio_curves(
        self,
        conn: sqlite3.Connection,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[list[float]]:
        curves: list[list[float]] = []
        if target_portfolio_type == PortfolioType.AGGRESSIVE:
            type_filter = (
                "and lower(coalesce(nullif(portfolio_type, ''), nullif(type, ''), '')) = 'aggressive'"
            )
        else:
            type_filter = "and lower(coalesce(nullif(portfolio_type, ''), nullif(type, ''), '')) <> 'aggressive'"
        for row in conn.execute(
            f"""
            select metrics_json from portfolios
            where metrics_json is not null and metrics_json <> ''
              {type_filter}
              and coalesce(nullif(portfolio_scope, ''), 'full_history') = ?
              and (? is null or target_month = ?)
              and (? is null or id <> ?)
            """,
            (
                portfolio_scope,
                target_month,
                target_month,
                exclude_portfolio_id,
                exclude_portfolio_id,
            ),
        ):
            try:
                metrics = json.loads(row["metrics_json"])
            except Exception:
                continue
            curve = metrics.get("equity_curve_2020_2026") if isinstance(metrics, dict) else None
            if isinstance(curve, list) and len(curve) > 1:
                try:
                    curves.append([float(value) for value in curve])
                except (TypeError, ValueError):
                    continue
        return curves

    def _saved_portfolio_curves_all_accounts(
        self,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[list[float]]:
        curves: list[list[float]] = []
        active_memory = self._ubs_memory_path().resolve()
        for _account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                excluded = exclude_portfolio_id if memory_path.resolve() == active_memory else None
                curves.extend(
                    self._saved_portfolio_curves(
                        conn,
                        target_portfolio_type,
                        exclude_portfolio_id=excluded,
                        portfolio_scope=portfolio_scope,
                        target_month=target_month,
                    )
                )
            finally:
                conn.close()
        return curves

    def _ubs_portfolio_finished(self, info: dict) -> None:
        self._set_ubs_portfolio_running(False)
        if not info.get("ok"):
            message = info.get("error", "Error desconocido")
            self._clear_failed_ubs_portfolio_generation()
            self.ubs_portfolio_status.set(message)
            self._notify_ubs_portfolio_event(f"Portfolio Builder fallido: {message}")
            return

        proposals = info.get("proposals") or []
        if not proposals:
            self._clear_failed_ubs_portfolio_generation()
            self.ubs_portfolio_status.set("No se genero ninguna propuesta viable.")
            return
        self.ubs_portfolio_proposals_availability = info.get("availability")
        self._show_ubs_portfolio_proposals_preview(
            0,
            proposals,
            [],
            mode="generate",
        )
        self.ubs_portfolio_status.set("Selecciona una de las tres propuestas para continuar.")

    def _accept_generated_ubs_portfolio_proposal(
        self,
        proposal: dict[str, object],
    ) -> None:
        result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
        inputs: dict[str, object] = proposal["inputs"]  # type: ignore[assignment]
        self.ubs_portfolio_pending_result = result
        self.ubs_portfolio_pending_inputs = inputs
        self._populate_ubs_portfolio_result(result)
        self._populate_ubs_portfolio_availability(
            getattr(self, "ubs_portfolio_proposals_availability", None)
        )
        self._set_ubs_portfolio_save_enabled(True)
        group_text = self._ubs_portfolio_group_summary_text(result.group_summary)
        status = (
            f"Portafolio generado: {result.total_units} unidades, "
            f"DD valle {result.valley_usage_pct:.1f}%, DD puntual {result.point_usage_pct:.1f}%."
        )
        if group_text:
            status += f" Grupos: {group_text}."
        group_warning = self._ubs_portfolio_group_warning(result.warnings)
        if group_warning:
            status += f" Aviso: {group_warning}"
        self.ubs_portfolio_status.set(status)
        self._notify_ubs_portfolio_event(
            f"Portfolio Builder propuesta {proposal['label']}: "
            f"net {result.total_net_profit:,.2f}, "
            f"lote {result.total_lot:.2f}, "
            f"{result.total_units} unidades, "
            f"{result.active_strategies} estrategias, "
            f"DD valle {result.actual_valley_dd:,.2f}/{result.target_valley_dd:,.2f} "
            f"({result.valley_usage_pct:.1f}%), "
            f"DD puntual {result.actual_point_dd:,.2f}/{result.target_point_dd:,.2f} "
            f"({result.point_usage_pct:.1f}%)."
            + (f" Grupos: {group_text}." if group_text else "")
            + (f" Aviso: {group_warning}" if group_warning else "")
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
        self._notify_ubs_portfolio_event(
            f"Portfolio Builder guardado: #{portfolio_id}, "
            f"net {result.total_net_profit:,.2f}, lote {result.total_lot:.2f}, "
            f"{result.active_strategies} estrategias."
        )

    def _notify_ubs_portfolio_event(self, message: str) -> None:
        notifier = getattr(self, "_notify_telegram", None)
        if callable(notifier):
            notifier(message)

    def _clear_failed_ubs_portfolio_generation(self) -> None:
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self._set_ubs_portfolio_save_enabled(False)
        self._clear_ubs_portfolio_result_tables()

    def _ubs_portfolio_group_summary_text(self, group_summary: dict[str, dict[str, float | int]]) -> str:
        if not group_summary:
            return ""
        parts = []
        for group, stats in list(group_summary.items())[:4]:
            parts.append(f"{group} {float(stats.get('unit_pct', 0.0)):.0f}%")
        return ", ".join(parts)

    def _ubs_portfolio_group_warning(self, warnings: list[str]) -> str:
        for warning in warnings:
            if "grupo" in warning.lower() or "asset group" in warning or "Group concentration" in warning:
                return warning
        return ""

    # ------------------------------------------------------------------ refresh/display
    def _refresh_ubs_portfolio_availability(self) -> None:
        if not hasattr(self, "ubs_portfolio_availability_tree"):
            return
        if not self._ubs_portfolio_source_paths():
            self.ubs_portfolio_availability.set("Memorias UBS ECN/PRO no encontradas.")
            self._populate_ubs_portfolio_availability(None)
            return
        try:
            availability = self._portfolio_availability(
                target_portfolio_type=self._portfolio_type_from_label(self.ubs_portfolio_type.get())
            )
        except Exception as exc:
            self.ubs_portfolio_availability.set(f"Disponibilidad: error leyendo memorias ({exc})")
            self._populate_ubs_portfolio_availability(None)
            return
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
        filter_suffix = ""
        recent_months_var = getattr(self, "ubs_portfolio_require_3_positive_months_6m", None)
        if recent_months_var is not None and bool(recent_months_var.get()):
            filter_suffix = " | Filtro 3/6M activo al generar"
        grid_off_var = getattr(self, "ubs_portfolio_grid_off", None)
        if grid_off_var is not None and bool(grid_off_var.get()):
            filter_suffix += " | Grid OFF activo"
        self.ubs_portfolio_availability.set(
            f"Sets Final Tick 6M OK ECN/PRO: {availability.robust_accepted} | "
            f"Sets bloqueados: {availability.already_used} | "
            f"Sets disponibles: {availability.available} | "
            f"Simbolos disponibles: {availability.symbols_available}"
            f"{filter_suffix}"
        )
        for symbol, count in availability.by_symbol.items():
            tree.insert("", "end", values=(symbol, count))

    def _refresh_ubs_portfolios(self, select_id: int | None = None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        self._refresh_ubs_portfolio_availability()
        self._refresh_ubs_portfolio_quarantine()
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

    def _refresh_ubs_portfolio_quarantine(self) -> None:
        tree = getattr(self, "ubs_portfolio_quarantine_tree", None)
        if tree is None:
            return
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_quarantine_rows = {}
        try:
            rows = self._portfolio_quarantine_rows_all_accounts()
        except Exception as exc:
            self.ubs_portfolio_status.set(f"No pude leer la cuarentena: {exc}")
            return
        for index, row in enumerate(rows):
            item = tree.insert(
                "",
                "end",
                iid=f"q:{index}",
                values=(
                    Path(str(row.get("set_path") or "")).name,
                    row.get("account_type") or "",
                    row.get("symbol") or "",
                    row.get("timeframe") or "",
                    row.get("quarantined_at") or "",
                ),
                tags=("rejected",),
            )
            self.ubs_portfolio_quarantine_rows[item] = row

    def _release_selected_ubs_portfolio_quarantine(self) -> None:
        tree = getattr(self, "ubs_portfolio_quarantine_tree", None)
        if tree is None or not tree.selection():
            messagebox.showinfo("Cuarentena", "Selecciona un set en cuarentena.")
            return
        row = getattr(self, "ubs_portfolio_quarantine_rows", {}).get(tree.selection()[0])
        if not row:
            return
        set_name = Path(str(row.get("set_path") or "")).name
        if not messagebox.askyesno(
            "Reintegrar set",
            f"{set_name} volvera a ser elegible para futuros portafolios.\n\nContinuar?",
        ):
            return
        conn = self._ubs_portfolio_conn_for_memory(Path(str(row["memory_path"])))
        try:
            conn.execute("delete from portfolio_quarantine where id=?", (int(row["id"]),))
            conn.commit()
        finally:
            conn.close()
        self._refresh_ubs_portfolios()
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios()
        self.ubs_portfolio_status.set(f"{set_name} reintegrado al pool elegible.")

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

    def _open_selected_ubs_portfolio_detail(self, event=None) -> None:
        tree = getattr(self, "ubs_portfolio_saved_tree", None)
        if tree is None:
            return
        if event is not None:
            item = tree.identify_row(event.y)
            if item:
                tree.selection_set(item)
        selection = tree.selection()
        if not selection:
            return
        portfolio_id = int(selection[0])
        self._create_ubs_portfolio_detail_window(portfolio_id)
        self._populate_ubs_portfolio_detail(portfolio_id)

    def _populate_ubs_portfolio_detail(self, portfolio_id: int) -> None:
        window = getattr(self, "ubs_portfolio_detail_window", None)
        if window is None or not window.winfo_exists():
            return
        tree = getattr(self, "ubs_portfolio_detail_tree", None)
        if tree is None:
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_detail_members = {}
        if portfolio is None:
            self.ubs_portfolio_detail_status.set("El portafolio ya no existe.")
            return
        try:
            metrics = json.loads(portfolio["metrics_json"] or "{}")
        except Exception:
            metrics = {}
        inputs = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        seasonal_coverage = (
            metrics.get("seasonal_coverage")
            if isinstance(metrics.get("seasonal_coverage"), dict)
            else {}
        )
        stress = metrics.get("stress_bootstrap") if isinstance(metrics.get("stress_bootstrap"), dict) else {}
        is_monthly = str(portfolio["portfolio_scope"] or inputs.get("portfolio_scope") or "full_history") == "monthly"
        month_no = int(portfolio["target_month"] or inputs.get("target_month") or 0)
        month_label = str(inputs.get("target_month_label") or "").strip()
        if is_monthly and not month_label and 1 <= month_no <= 12:
            month_names = (
                "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
            )
            month_label = f"{month_no:02d} - {month_names[month_no - 1]}"
        profile_label = str(inputs.get("optimization_profile_label") or "").strip()
        title_suffix = f" - Mensual {month_label}" if is_monthly and month_label else ""
        window.title(f"Portafolio #{portfolio_id}{title_suffix}")
        target = max(int(portfolio["target_strategies"] or 0), int(portfolio["active_strategies"] or 0))
        status_parts = [
            f"Portafolio #{portfolio_id}: {len(members)}/{target} estrategias | "
            f"{int(portfolio['total_units'] or 0)} unidades | lote {float(portfolio['total_lot'] or 0):.2f}"
        ]
        if is_monthly and month_label:
            status_parts.append(f"Mensual {month_label}")
        if profile_label:
            status_parts.append(f"Perfil {profile_label}")
        status_parts.append(
            f"DD valle {float(portfolio['actual_valley_dd'] or 0):,.2f}/{float(portfolio['target_valley_dd'] or 0):,.2f}"
        )
        status_parts.append(
            f"DD puntual {float(portfolio['actual_point_dd'] or 0):,.2f}/{float(portfolio['target_point_dd'] or 0):,.2f}"
        )
        if stress:
            stress_state = "ALERTA stress" if bool(stress.get("alert")) else "stress OK"
            status_parts.append(
                f"{stress_state} P95 {float(stress.get('valley_dd_p95') or 0):,.2f}"
            )
        self.ubs_portfolio_detail_status.set(" | ".join(status_parts))
        for index, member in enumerate(members):
            coverage = {}
            for coverage_key in (
                str(member.get("set_id") or ""),
                str(member.get("set_path") or ""),
            ):
                if coverage_key and coverage_key in seasonal_coverage:
                    coverage = seasonal_coverage[coverage_key]
                    break
            coverage_month = int(coverage.get("target_month") or 0) if isinstance(coverage, dict) else 0
            years = coverage.get("years") if isinstance(coverage, dict) else []
            positive_years = coverage.get("positive_years") if isinstance(coverage, dict) else []
            if not isinstance(years, list):
                years = []
            if not isinstance(positive_years, list):
                positive_years = []
            item = tree.insert(
                "",
                "end",
                iid=f"member:{index}",
                values=(
                    Path(str(member.get("set_path") or member.get("set_id") or "")).name,
                    self._ubs_portfolio_member_account(member),
                    self._ubs_portfolio_member_candidate_label(member),
                    member.get("symbol") or "",
                    member.get("timeframe") or member.get("period") or "",
                    f"{coverage_month:02d}" if coverage_month else "",
                    ",".join(str(year) for year in years),
                    f"{len(positive_years)}/{len(years)}" if years else "",
                    int(member.get("units") or 0),
                    f"{float(member.get('lot') or 0):.2f}",
                    f"{float(member.get('net_profit_contribution') or 0):,.0f}",
                    f"{float(member.get('standalone_valley_dd') or 0):,.2f}",
                    f"{float(member.get('standalone_point_dd') or 0):,.2f}",
                ),
                tags=("accepted",),
            )
            self.ubs_portfolio_detail_members[item] = member

    def _selected_ubs_portfolio_detail_member(self) -> dict[str, object] | None:
        tree = getattr(self, "ubs_portfolio_detail_tree", None)
        if tree is None or not tree.selection():
            messagebox.showinfo("Portafolio UBS", "Selecciona una estrategia del portafolio.")
            return None
        return getattr(self, "ubs_portfolio_detail_members", {}).get(tree.selection()[0])

    def _open_selected_ubs_portfolio_detail_member(self) -> None:
        member = self._selected_ubs_portfolio_detail_member()
        if not member:
            return
        report = str(member.get("oos_report_path") or member.get("is_report_path") or "")
        if report:
            self._open_local_file(Path(report))
        else:
            messagebox.showinfo("Abrir reporte", "La estrategia no tiene reporte guardado.")

    def _quarantine_selected_ubs_portfolio_member(self, portfolio_id: int) -> None:
        try:
            self._quarantine_selected_ubs_portfolio_member_impl(portfolio_id)
        except Exception as exc:
            messagebox.showerror("Poner en cuarentena", f"No se pudo actualizar el portafolio:\n{exc}")
            self._refresh_ubs_portfolios(select_id=portfolio_id)
            if hasattr(self, "_refresh_ubs_monthly_portfolios"):
                self._refresh_ubs_monthly_portfolios(select_id=portfolio_id)
            self._populate_ubs_portfolio_detail(portfolio_id)

    def _quarantine_selected_ubs_portfolio_member_impl(self, portfolio_id: int) -> None:
        member = self._selected_ubs_portfolio_detail_member()
        if not member:
            return
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        set_name = Path(set_path).name
        if not messagebox.askyesno(
            "Poner en cuarentena",
            f"{set_name} dejara de ser elegible y se quitara del portafolio #{portfolio_id}.\n\n"
            "Despues podras usar 'Completar portafolio' para buscar una sustituta y recalcular lotes.",
        ):
            return
        account_type, memory_path, candidate_id = self._resolve_portfolio_member_source(member)
        source_conn = self._ubs_portfolio_conn_for_memory(memory_path)
        try:
            source_conn.execute(
                """
                insert into portfolio_quarantine (
                    account_type, candidate_id, set_path, symbol, timeframe,
                    reason, source_portfolio_id, quarantined_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(set_path) do update set
                    account_type=excluded.account_type,
                    candidate_id=excluded.candidate_id,
                    symbol=excluded.symbol,
                    timeframe=excluded.timeframe,
                    reason=excluded.reason,
                    source_portfolio_id=excluded.source_portfolio_id,
                    quarantined_at=excluded.quarantined_at
                """,
                (
                    account_type,
                    candidate_id,
                    set_path,
                    str(member.get("symbol") or ""),
                    str(member.get("timeframe") or member.get("period") or ""),
                    "Retirada manualmente de un portafolio guardado",
                    portfolio_id,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            source_conn.commit()
        finally:
            source_conn.close()

        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            if portfolio is None:
                raise ValueError("El portafolio ya no existe.")
            target = max(
                int(portfolio["target_strategies"] or 0),
                int(portfolio["active_strategies"] or 0),
            )
            conn.execute("update portfolios set target_strategies=? where id=?", (target, portfolio_id))
            conn.execute(
                "delete from portfolio_allocations where portfolio_id=? and set_path=?",
                (portfolio_id, set_path),
            )
            conn.execute(
                "delete from portfolio_members where portfolio_id=? and set_path=?",
                (portfolio_id, set_path),
            )
            self._recalculate_saved_portfolio(conn, portfolio_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._refresh_ubs_portfolios(select_id=portfolio_id)
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios(select_id=portfolio_id)
        self._populate_ubs_portfolio_detail(portfolio_id)
        self.ubs_portfolio_status.set(f"{set_name} puesto en cuarentena y retirado del portafolio #{portfolio_id}.")

    def _saved_portfolio_inputs(self, portfolio: sqlite3.Row) -> dict[str, object]:
        metrics = self._portfolio_metrics_json(portfolio)
        stored = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        defaults: dict[str, object] = {
            "capital": float(portfolio["capital"] or portfolio["account_capital"] or 0),
            "valley_dd_pct": float(portfolio["target_valley_dd_pct"] or 0),
            "point_dd_pct": float(portfolio["target_point_dd_pct"] or 0),
            "portfolio_type": str(portfolio["portfolio_type"] or portfolio["type"] or "balanced").lower(),
            "top_k_per_symbol": 3,
            "max_total_candidates": 30,
            "min_trades_2020_2026": 100,
            "max_units_per_set": None,
            "max_total_units": None,
            "max_units_per_symbol": None,
            "max_sets_per_symbol": 1,
            "run_local_search": True,
            "use_correlation": True,
            "require_3_positive_months_6m": False,
            "dd_reserve_pct": 0.0,
            "search_restarts": 0,
            "max_pair_corr": 0.35,
            "max_downside_corr": 0.25,
            "max_dd_overlap": 0.35,
            "max_portfolio_corr": 0.50,
            "portfolio_scope": str(portfolio["portfolio_scope"] or "full_history"),
            "target_month": int(portfolio["target_month"] or 0) or None,
            "strict_yearly_month_validation": False,
            "deep_optimization": False,
        }
        defaults.update(stored)
        return defaults

    def _scope_portfolio_sets(
        self,
        strategies: list,
        inputs: dict[str, object],
    ) -> tuple[list, list[str]]:
        if str(inputs.get("portfolio_scope") or "full_history") != "monthly":
            return strategies, []
        target_month = int(inputs.get("target_month") or 0)
        if not 1 <= target_month <= 12:
            raise ValueError("El portafolio mensual no tiene un mes objetivo valido.")
        return slice_strategy_sets_to_month(strategies, target_month)

    def _filter_strict_monthly_valid_proposals(
        self,
        full_sets: list,
        proposals: list[dict[str, object]],
        inputs: dict[str, object],
    ) -> list[dict[str, object]]:
        if not bool(inputs.get("strict_yearly_month_validation")):
            return proposals
        target_month = int(inputs.get("target_month") or 0)
        if not 1 <= target_month <= 12:
            raise ValueError("La validacion estricta mensual no tiene un mes objetivo valido.")
        full_by_id = {strategy.set_id: strategy for strategy in full_sets}
        valid: list[dict[str, object]] = []
        rejected: list[str] = []
        for proposal in proposals:
            result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
            units = {
                allocation.set_id: allocation.units
                for allocation in result.allocations
                if allocation.units > 0
            }
            validation = validate_strict_monthly_portfolio(
                [full_by_id[set_id] for set_id in units if set_id in full_by_id],
                units,
                target_month=target_month,
                target_valley_dd=result.target_valley_dd,
                target_point_dd=result.target_point_dd,
                lookback_years=5,
            )
            result.seasonal_validation = validation
            if bool(validation.get("passed")):
                result.warnings.append(
                    "Validacion estricta mensual OK: el mes objetivo pasa ano a ano "
                    "y es el mejor mes neto de los ultimos 5 años."
                )
                valid.append(proposal)
            else:
                reasons = validation.get("reasons") or []
                rejected.append(
                    f"{proposal['label']}: " + "; ".join(str(item) for item in reasons[:3])
                )
        if not valid:
            raise ValueError(
                "Ninguna propuesta paso la validacion estricta mensual. "
                + " | ".join(rejected)
            )
        return valid

    def _set_ubs_portfolio_detail_running(self, running: bool, text: str = "") -> None:
        for button in getattr(self, "ubs_portfolio_detail_buttons", []):
            try:
                button.configure(state="disabled" if running else "normal")
            except Exception:
                pass
        if text and hasattr(self, "ubs_portfolio_detail_status"):
            self.ubs_portfolio_detail_status.set(text)

    def _reoptimize_saved_ubs_portfolio(self, portfolio_id: int) -> None:
        if (
            getattr(self, "ubs_portfolio_running", False)
            or getattr(self, "ubs_monthly_portfolio_running", False)
        ):
            messagebox.showwarning("Revalidar portafolio", "Ya hay un calculo de portafolio en marcha.")
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        if portfolio is None:
            messagebox.showerror("Revalidar portafolio", "El portafolio ya no existe.")
            return
        inputs = self._saved_portfolio_inputs(portfolio)
        is_monthly = str(inputs.get("portfolio_scope") or "full_history") == "monthly"
        reserve_var = (
            self.ubs_monthly_portfolio_dd_reserve_pct
            if is_monthly else self.ubs_portfolio_dd_reserve_pct
        )
        restarts_var = (
            self.ubs_monthly_portfolio_search_restarts
            if is_monthly else self.ubs_portfolio_search_restarts
        )
        try:
            inputs["dd_reserve_pct"] = self._parse_float_setting(
                reserve_var.get(),
                "Reserva DD",
            )
            inputs["search_restarts"] = self._parse_int_setting(
                restarts_var.get(),
                "Reinicios de busqueda",
                minimum=0,
            )
        except ValueError as exc:
            messagebox.showerror("Revalidar portafolio", str(exc))
            return
        self.ubs_portfolio_running = True
        self._set_ubs_portfolio_detail_running(
            True,
            f"Revalidando portafolio #{portfolio_id} con reserva DD {float(inputs['dd_reserve_pct']):.1f}%...",
        )
        threading.Thread(
            target=self._reoptimize_saved_ubs_portfolio_worker,
            args=(portfolio_id, inputs, members),
            daemon=True,
        ).start()

    def _reoptimize_saved_ubs_portfolio_worker(
        self,
        portfolio_id: int,
        inputs: dict[str, object],
        previous_members: list[dict[str, object]],
    ) -> None:
        try:
            portfolio_type = self._portfolio_type_from_label(inputs["portfolio_type"])
            inputs["portfolio_type"] = portfolio_type.value
            is_monthly = str(inputs.get("portfolio_scope") or "full_history") == "monthly"
            rows = self._final_tick_passed_candidates_all_accounts(
                include_quarantined=is_monthly,
            )
            if bool(inputs.get("require_3_positive_months_6m")):
                rows, month_warnings = filter_rows_by_recent_positive_months(
                    rows,
                    min_positive_months=3,
                    window_months=6,
                )
            else:
                month_warnings = []
            grid_warnings: list[str] = []
            if bool(inputs.get("grid_off")):
                rows, grid_warnings = filter_rows_grid_off(rows)
            used = [] if is_monthly else self._used_set_paths_all_accounts(
                portfolio_type,
                exclude_portfolio_id=portfolio_id,
            )
            raw_sets, load_warnings = load_robust_sets_from_rows(rows, used)
            full_sets_for_strict_validation = list(raw_sets)
            raw_sets, scope_warnings = self._scope_portfolio_sets(raw_sets, inputs)
            if not raw_sets:
                raise ValueError("No quedan candidatos elegibles para reoptimizar.")
            proposals = self._optimize_ubs_portfolio_proposals(
                raw_sets,
                inputs,
                portfolio_type,
                self._saved_portfolio_curves_all_accounts(
                    portfolio_type,
                    exclude_portfolio_id=portfolio_id,
                    portfolio_scope="monthly" if is_monthly else "full_history",
                    target_month=int(inputs.get("target_month") or 0) if is_monthly else None,
                ),
                strict_full_sets=full_sets_for_strict_validation if is_monthly else None,
                progress=lambda label, index: self.after(
                    0,
                    self._set_ubs_portfolio_detail_running,
                    True,
                    f"Revalidando #{portfolio_id}: propuesta {index}/3 ({label})...",
                ),
            )
            proposals = self._filter_strict_monthly_valid_proposals(
                full_sets_for_strict_validation,
                proposals,
                inputs,
            )
            for proposal in proposals:
                proposal["result"].warnings[:0] = (
                    month_warnings + grid_warnings + load_warnings + scope_warnings
                )
        except Exception as exc:
            self.after(
                0,
                self._reoptimize_saved_ubs_portfolio_finished,
                portfolio_id,
                None,
                inputs,
                previous_members,
                str(exc),
            )
            return
        self.after(
            0,
            self._reoptimize_saved_ubs_portfolio_finished,
            portfolio_id,
            proposals,
            inputs,
            previous_members,
            "",
        )

    def _reoptimize_saved_ubs_portfolio_finished(
        self,
        portfolio_id: int,
        proposals: list[dict[str, object]] | None,
        inputs: dict[str, object],
        previous_members: list[dict[str, object]],
        error: str,
    ) -> None:
        self.ubs_portfolio_running = False
        self._set_ubs_portfolio_detail_running(False)
        if error or not proposals:
            messagebox.showerror("Revalidar portafolio", error or "No se pudo reoptimizar el portafolio.")
            self._populate_ubs_portfolio_detail(portfolio_id)
            return
        self._show_ubs_portfolio_proposals_preview(
            portfolio_id,
            proposals,
            previous_members,
            mode="reoptimize",
        )

    def _optimize_ubs_portfolio_proposals(
        self,
        raw_sets: list,
        inputs: dict[str, object],
        base_type: PortfolioType,
        existing_curves: list[list[float]],
        *,
        strict_full_sets: list | None = None,
        progress=None,
    ) -> list[dict[str, object]]:
        configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
        specs = (
            ("profit", "Maximo beneficio", base_type, configured_reserve),
            ("balanced", "Equilibrada", PortfolioType.BALANCED, max(configured_reserve, 15.0)),
            ("margin", "Maximo margen DD", PortfolioType.CONSERVATIVE, max(configured_reserve, 25.0)),
        )
        proposals: list[dict[str, object]] = []
        errors: list[str] = []
        for index, (key, label, objective_type, reserve) in enumerate(specs, start=1):
            if callable(progress):
                progress(label, index)
            proposal_inputs = dict(inputs)
            proposal_inputs["optimization_profile"] = key
            proposal_inputs["optimization_profile_label"] = label
            proposal_inputs["dd_reserve_pct"] = reserve
            try:
                strict_monthly = (
                    bool(inputs.get("strict_yearly_month_validation"))
                    and bool(inputs.get("use_deep_candidate_engine"))
                    and str(inputs.get("portfolio_scope") or "full_history") == "monthly"
                    and strict_full_sets is not None
                )
                optimizer_kwargs = {
                    "capital": float(inputs["capital"]),
                    "valley_dd_pct": float(inputs["valley_dd_pct"]),
                    "point_dd_pct": float(inputs["point_dd_pct"]),
                    "portfolio_type": objective_type,
                    "min_trades_2020_2026": int(inputs["min_trades_2020_2026"]),
                    "top_k_per_symbol": int(inputs["top_k_per_symbol"]),
                    "max_total_candidates": int(inputs["max_total_candidates"]),
                    "max_units_per_set": inputs.get("max_units_per_set"),
                    "max_total_units": inputs.get("max_total_units"),
                    "max_units_per_symbol": inputs.get("max_units_per_symbol"),
                    "max_sets_per_symbol": inputs.get("max_sets_per_symbol"),
                    "run_local_search": bool(inputs.get("run_local_search", True)),
                    "max_pair_corr": inputs.get("max_pair_corr") if inputs.get("use_correlation", True) else None,
                    "max_downside_corr": inputs.get("max_downside_corr") if inputs.get("use_correlation", True) else None,
                    "max_dd_overlap": inputs.get("max_dd_overlap") if inputs.get("use_correlation", True) else None,
                    "existing_portfolio_curves": existing_curves,
                    "max_portfolio_corr": inputs.get("max_portfolio_corr") if inputs.get("use_correlation", True) else None,
                    "dd_reserve_pct": reserve,
                    "search_restarts": int(inputs.get("search_restarts") or 0),
                    "margin_balance": float(inputs["capital"])
                    if bool(inputs.get("validate_roboforex_margin"))
                    else None,
                    "max_margin_pct": float(inputs.get("max_margin_pct") or 100.0)
                    if bool(inputs.get("validate_roboforex_margin"))
                    else None,
                    "stock_leverage": 20.0,
                    "default_leverage": 500.0,
                    "stock_contract_size": 100.0,
                    "default_contract_size": 1.0,
                }
                if strict_monthly:
                    result = optimize_strict_monthly_portfolio(
                        monthly_sets=raw_sets,
                        full_sets=strict_full_sets or [],
                        target_month=int(inputs.get("target_month") or 0),
                        **optimizer_kwargs,  # type: ignore[arg-type]
                    )
                else:
                    result = optimize_portfolio(
                        raw_sets=raw_sets,
                        **optimizer_kwargs,  # type: ignore[arg-type]
                    )
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                continue
            raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
            result.seasonal_coverage = {
                allocation.set_id: {
                    "target_month": raw_by_id[allocation.set_id].target_month,
                    "years": list(raw_by_id[allocation.set_id].month_years),
                    "positive_years": list(raw_by_id[allocation.set_id].positive_month_years),
                    "year_count": len(raw_by_id[allocation.set_id].month_years),
                    "positive_year_count": len(raw_by_id[allocation.set_id].positive_month_years),
                    "trades": raw_by_id[allocation.set_id].trades_2020_2026,
                }
                for allocation in result.allocations
                if allocation.set_id in raw_by_id
                and raw_by_id[allocation.set_id].target_month is not None
            }
            proposals.append(
                {
                    "key": key,
                    "label": label,
                    "reserve_pct": reserve,
                    "result": result,
                    "inputs": proposal_inputs,
                }
            )
        if not proposals:
            raise ValueError("Ninguna propuesta fue viable. " + " | ".join(errors))
        return proposals

    def _show_ubs_portfolio_proposals_preview(
        self,
        portfolio_id: int,
        proposals: list[dict[str, object]],
        previous_members: list[dict[str, object]],
        *,
        mode: str,
    ) -> None:
        self.ubs_portfolio_proposals = {str(item["key"]): item for item in proposals}
        self.ubs_portfolio_proposals_previous_members = previous_members
        self.ubs_portfolio_proposals_id = portfolio_id
        self.ubs_portfolio_proposals_mode = mode
        before_units = {
            str(member.get("set_path") or member.get("set_id") or ""): int(member.get("units") or 0)
            for member in previous_members
        }
        comparison_rows = []
        for proposal in proposals:
            result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
            inputs: dict[str, object] = proposal["inputs"]  # type: ignore[assignment]
            after_units = {item.set_path or item.set_id: item.units for item in result.allocations}
            changes = sum(
                1 for set_path in set(before_units) | set(after_units)
                if before_units.get(set_path, 0) != after_units.get(set_path, 0)
            )
            nominal_valley = float(inputs["capital"]) * float(inputs["valley_dd_pct"]) / 100.0
            margin_pct = (
                max(nominal_valley - result.actual_valley_dd, 0.0) / max(nominal_valley, 1e-9) * 100.0
            )
            max_group_pct = max(
                (float(stats.get("unit_pct", 0.0)) for stats in result.group_summary.values()),
                default=0.0,
            )
            stress = result.stress_bootstrap
            stress_status = "ALERTA" if stress and stress.alert else "OK" if stress else "SIN DATOS"
            margin_summary = result.margin_summary or {}
            margin_total = float(margin_summary.get("total", 0.0) or 0.0)
            margin_limit = float(margin_summary.get("limit", 0.0) or 0.0)
            margin_usage = float(margin_summary.get("usage_pct", 0.0) or 0.0)
            comparison_rows.append(
                (
                    proposal["key"],
                    proposal["label"],
                    f"{result.total_net_profit:,.0f}",
                    f"{result.actual_valley_dd:,.2f}/{result.target_valley_dd:,.2f}",
                    f"{result.actual_point_dd:,.2f}/{result.target_point_dd:,.2f}",
                    f"{stress.valley_dd_p50:,.2f}" if stress else "-",
                    f"{stress.valley_dd_p95:,.2f}" if stress else "-",
                    f"{stress.probability_exceed_nominal_pct:.1f}%" if stress else "-",
                    f"{stress.probability_exceed_effective_pct:.1f}%" if stress else "-",
                    f"{margin_pct:.1f}%",
                    f"{float(proposal['reserve_pct']):.1f}%",
                    f"{margin_total:,.0f}/{margin_limit:,.0f}" if margin_summary else "-",
                    f"{margin_usage:.1f}%" if margin_summary else "-",
                    result.total_units,
                    result.active_strategies,
                    f"{max_group_pct:.1f}%",
                    changes,
                    stress_status,
                )
            )
        self._create_ubs_portfolio_proposals_window(portfolio_id, comparison_rows)
        if mode == "generate_monthly":
            selected_proposal = max(
                proposals,
                key=lambda item: float(item["result"].total_net_profit),  # type: ignore[index, union-attr]
            )
        else:
            selected_proposal = proposals[0]
        first_key = str(selected_proposal["key"])
        tree = getattr(self, "ubs_portfolio_proposals_tree", None)
        if tree is not None and tree.exists(first_key):
            tree.selection_set(first_key)
            tree.focus(first_key)
        self._on_ubs_portfolio_proposal_select()

    def _on_ubs_portfolio_proposal_select(self, _event=None) -> None:
        tree = getattr(self, "ubs_portfolio_proposals_tree", None)
        diff_tree = getattr(self, "ubs_portfolio_proposals_diff_tree", None)
        if tree is None or diff_tree is None or not tree.selection():
            return
        key = str(tree.selection()[0])
        proposal = getattr(self, "ubs_portfolio_proposals", {}).get(key)
        if not proposal:
            return
        result: PortfolioResult = proposal["result"]
        inputs: dict[str, object] = proposal["inputs"]
        self.ubs_portfolio_selected_proposal_key = key
        for item in diff_tree.get_children(""):
            diff_tree.delete(item)
        rows = self._ubs_portfolio_completion_diff_rows(
            getattr(self, "ubs_portfolio_proposals_previous_members", []),
            result,
        )
        for values in rows:
            state = values[-1]
            tag = "accepted" if state == "NUEVA" else "rejected" if state == "RETIRADA" else "pending"
            diff_tree.insert("", "end", values=values, tags=(tag,))
        summary_var = getattr(self, "ubs_portfolio_proposals_summary", None)
        if summary_var is not None:
            month_prefix = ""
            if str(inputs.get("portfolio_scope") or "full_history") == "monthly":
                month_label = str(inputs.get("target_month_label") or "").strip()
                if month_label:
                    month_prefix = f"Objetivo {month_label} | "
            stress = result.stress_bootstrap
            stress_text = (
                f"DD bootstrap P50/P95 {stress.valley_dd_p50:.2f}/{stress.valley_dd_p95:.2f} | "
                f"P(> nominal) {stress.probability_exceed_nominal_pct:.1f}% | "
                f"P(> efectivo) {stress.probability_exceed_effective_pct:.1f}% | "
                f"{'ALERTA ROJA' if stress.alert else 'estres OK'} | "
                if stress else "bootstrap sin datos | "
            )
            margin_text = ""
            if result.margin_summary:
                margin_text = (
                    f"margen {float(result.margin_summary.get('total', 0.0)):,.2f}/"
                    f"{float(result.margin_summary.get('limit', 0.0)):,.2f} "
                    f"({float(result.margin_summary.get('usage_pct', 0.0)):.1f}%) | "
                )
            summary_var.set(
                f"{month_prefix}{proposal['label']}: net {result.total_net_profit:,.2f} | "
                f"DD valle {result.actual_valley_dd:.2f}/{result.target_valley_dd:.2f} | "
                f"DD puntual {result.actual_point_dd:.2f}/{result.target_point_dd:.2f} | "
                f"{stress_text}"
                f"{margin_text}"
                f"reserva {float(proposal['reserve_pct']):.1f}% | "
                f"{result.active_strategies} estrategias | {result.total_units} unidades."
            )
            summary_label = getattr(self, "ubs_portfolio_proposals_summary_label", None)
            if summary_label is not None:
                summary_label.configure(
                    fg=self.colors["danger"] if stress and stress.alert else self.colors["accent_soft_text"]
                )

    def _apply_selected_ubs_portfolio_proposal(self) -> None:
        key = getattr(self, "ubs_portfolio_selected_proposal_key", None)
        proposal = getattr(self, "ubs_portfolio_proposals", {}).get(key)
        portfolio_id = getattr(self, "ubs_portfolio_proposals_id", None)
        if not proposal or portfolio_id is None:
            messagebox.showerror("Propuestas", "Selecciona una propuesta valida.")
            return
        proposal_mode = getattr(self, "ubs_portfolio_proposals_mode", "")
        if proposal_mode == "generate":
            self._accept_generated_ubs_portfolio_proposal(proposal)
            self._cancel_ubs_portfolio_proposals_preview(refresh_detail=False, update_status=False)
            return
        if proposal_mode == "generate_monthly":
            self._accept_generated_ubs_monthly_portfolio_proposal(proposal)
            self._cancel_ubs_portfolio_proposals_preview(refresh_detail=False, update_status=False)
            self._save_pending_ubs_monthly_portfolio()
            return
        result: PortfolioResult = proposal["result"]
        inputs: dict[str, object] = proposal["inputs"]
        conn = self._ubs_portfolio_conn()
        try:
            self._save_portfolio_version(
                conn,
                int(portfolio_id),
                f"Antes de aplicar propuesta {proposal['label']}",
            )
            self._replace_saved_portfolio_result(
                conn,
                int(portfolio_id),
                inputs,
                result,
                result.active_strategies,
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            messagebox.showerror("Propuestas", f"No se pudo aplicar la propuesta:\n{exc}")
            return
        finally:
            conn.close()
        self._cancel_ubs_portfolio_proposals_preview(refresh_detail=False)
        self._refresh_ubs_portfolios(select_id=int(portfolio_id))
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios(select_id=int(portfolio_id))
        self._populate_ubs_portfolio_detail(int(portfolio_id))
        self.ubs_portfolio_status.set(
            f"Portafolio #{portfolio_id} actualizado con propuesta {proposal['label']}."
        )

    def _cancel_ubs_portfolio_proposals_preview(
        self,
        *,
        refresh_detail: bool = True,
        update_status: bool = True,
    ) -> None:
        window = getattr(self, "ubs_portfolio_proposals_window", None)
        if window is not None and window.winfo_exists():
            window.destroy()
        portfolio_id = getattr(self, "ubs_portfolio_proposals_id", None)
        mode = getattr(self, "ubs_portfolio_proposals_mode", "")
        self.ubs_portfolio_proposals = {}
        self.ubs_portfolio_selected_proposal_key = None
        if update_status and mode == "generate":
            self.ubs_portfolio_status.set("Seleccion de propuesta cancelada.")
        elif update_status and mode == "generate_monthly":
            self.ubs_monthly_portfolio_status.set("Seleccion de propuesta mensual cancelada.")
        if refresh_detail and mode not in {"generate", "generate_monthly"} and portfolio_id is not None:
            self._populate_ubs_portfolio_detail(int(portfolio_id))

    def _complete_saved_ubs_portfolio(self, portfolio_id: int) -> None:
        if (
            getattr(self, "ubs_portfolio_running", False)
            or getattr(self, "ubs_monthly_portfolio_running", False)
        ):
            messagebox.showwarning("Completar portafolio", "Ya hay un calculo de portafolio en marcha.")
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        if portfolio is None:
            messagebox.showerror("Completar portafolio", "El portafolio ya no existe.")
            return
        target = max(int(portfolio["target_strategies"] or 0), int(portfolio["active_strategies"] or 0))
        if len(members) >= target:
            messagebox.showinfo("Completar portafolio", "El portafolio ya tiene todas sus estrategias.")
            return
        self.ubs_portfolio_running = True
        self._set_ubs_portfolio_detail_running(
            True,
            f"Completando portafolio #{portfolio_id}: conservando {len(members)} y buscando {target - len(members)} sustituta(s)...",
        )
        threading.Thread(
            target=self._complete_saved_ubs_portfolio_worker,
            args=(portfolio_id, dict(portfolio), members, target),
            daemon=True,
        ).start()

    def _complete_saved_ubs_portfolio_worker(
        self,
        portfolio_id: int,
        portfolio: dict[str, object],
        members: list[dict[str, object]],
        target_strategies: int,
    ) -> None:
        try:
            inputs = self._saved_portfolio_inputs(portfolio)  # type: ignore[arg-type]
            portfolio_type = self._portfolio_type_from_label(inputs["portfolio_type"])
            inputs["portfolio_type"] = portfolio_type.value
            is_monthly = str(inputs.get("portfolio_scope") or "full_history") == "monthly"
            rows = self._final_tick_passed_candidates_all_accounts(
                include_quarantined=is_monthly,
            )
            used = [] if is_monthly else self._used_set_paths_all_accounts(
                portfolio_type,
                exclude_portfolio_id=portfolio_id,
            )
            required_rows = [
                {
                    "candidate_id": member.get("candidate_id"),
                    "set_path": member.get("set_path") or member.get("set_id"),
                    "symbol": member.get("symbol"),
                    "target_symbol": member.get("symbol"),
                    "period": member.get("timeframe") or member.get("period"),
                    "family": "",
                    "is_report_path": member.get("is_report_path"),
                    "oos_report_path": member.get("oos_report_path"),
                }
                for member in members
            ]
            required_sets, required_warnings = load_robust_sets_from_rows(required_rows, [])
            if len(required_sets) != len(required_rows):
                raise ValueError("No se pudieron reconstruir todas las estrategias que deben conservarse.")
            if bool(inputs.get("require_3_positive_months_6m")):
                rows, month_warnings = filter_rows_by_recent_positive_months(
                    rows,
                    min_positive_months=3,
                    window_months=6,
                )
            else:
                month_warnings = []
            grid_warnings: list[str] = []
            if bool(inputs.get("grid_off")):
                rows, grid_warnings = filter_rows_grid_off(rows)
            candidate_sets, load_warnings = load_robust_sets_from_rows(rows, used)
            full_sets_for_strict_validation = list(required_sets) + list(candidate_sets)
            required_sets, required_scope_warnings = self._scope_portfolio_sets(required_sets, inputs)
            candidate_sets, candidate_scope_warnings = self._scope_portfolio_sets(candidate_sets, inputs)
            # Existing members are the fixed base of a repair. They may now be
            # locked by a newer portfolio or have a changed pipeline status;
            # neither condition is allowed to evict them implicitly. Only the
            # replacement candidates pass through today's eligibility gates.
            raw_by_id = {strategy.set_id: strategy for strategy in candidate_sets}
            raw_by_id.update({strategy.set_id: strategy for strategy in required_sets})
            raw_sets = list(raw_by_id.values())
            required_set_ids = [strategy.set_id for strategy in required_sets]
            saved_units = {
                str(member.get("set_path") or member.get("set_id") or ""): int(member.get("units") or 0)
                for member in members
            }
            required_initial_allocations = {
                strategy.set_id: saved_units[strategy.set_id]
                for strategy in required_sets
            }
            existing_curves = self._saved_portfolio_curves_all_accounts(
                portfolio_type,
                exclude_portfolio_id=portfolio_id,
                portfolio_scope="monthly" if is_monthly else "full_history",
                target_month=int(inputs.get("target_month") or 0) if is_monthly else None,
            )
            result = optimize_portfolio(
                raw_sets=raw_sets,
                capital=float(inputs["capital"]),
                valley_dd_pct=float(inputs["valley_dd_pct"]),
                point_dd_pct=float(inputs["point_dd_pct"]),
                portfolio_type=portfolio_type,
                min_trades_2020_2026=int(inputs["min_trades_2020_2026"]),
                top_k_per_symbol=int(inputs["top_k_per_symbol"]),
                max_total_candidates=int(inputs["max_total_candidates"]),
                max_units_per_set=inputs.get("max_units_per_set"),  # type: ignore[arg-type]
                max_total_units=inputs.get("max_total_units"),  # type: ignore[arg-type]
                max_units_per_symbol=inputs.get("max_units_per_symbol"),  # type: ignore[arg-type]
                max_sets_per_symbol=inputs.get("max_sets_per_symbol"),  # type: ignore[arg-type]
                run_local_search=bool(inputs.get("run_local_search", True)),
                max_pair_corr=inputs.get("max_pair_corr") if inputs.get("use_correlation", True) else None,  # type: ignore[arg-type]
                max_downside_corr=inputs.get("max_downside_corr") if inputs.get("use_correlation", True) else None,  # type: ignore[arg-type]
                max_dd_overlap=inputs.get("max_dd_overlap") if inputs.get("use_correlation", True) else None,  # type: ignore[arg-type]
                existing_portfolio_curves=existing_curves,
                max_portfolio_corr=inputs.get("max_portfolio_corr") if inputs.get("use_correlation", True) else None,  # type: ignore[arg-type]
                required_set_ids=required_set_ids,
                minimum_active_strategies=target_strategies,
                maximum_active_strategies=target_strategies,
                required_initial_allocations=required_initial_allocations,
                preserve_required_allocations=True,
                dd_reserve_pct=float(inputs.get("dd_reserve_pct") or 0),
                search_restarts=int(inputs.get("search_restarts") or 0),
            )
            raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
            result.seasonal_coverage = {
                allocation.set_id: {
                    "target_month": raw_by_id[allocation.set_id].target_month,
                    "years": list(raw_by_id[allocation.set_id].month_years),
                    "positive_years": list(raw_by_id[allocation.set_id].positive_month_years),
                    "year_count": len(raw_by_id[allocation.set_id].month_years),
                    "positive_year_count": len(raw_by_id[allocation.set_id].positive_month_years),
                    "trades": raw_by_id[allocation.set_id].trades_2020_2026,
                }
                for allocation in result.allocations
                if allocation.set_id in raw_by_id
                and raw_by_id[allocation.set_id].target_month is not None
            }
            if bool(inputs.get("strict_yearly_month_validation")):
                validated = self._filter_strict_monthly_valid_proposals(
                    full_sets_for_strict_validation,
                    [
                        {
                            "key": "complete",
                            "label": "Completar portafolio",
                            "result": result,
                            "inputs": inputs,
                        }
                    ],
                    inputs,
                )
                result = validated[0]["result"]  # type: ignore[assignment]
            result.warnings[:0] = (
                required_warnings
                + required_scope_warnings
                + month_warnings
                + grid_warnings
                + load_warnings
                + candidate_scope_warnings
            )
            if result.active_strategies < target_strategies:
                raise ValueError(
                    f"No existe una sustituta compatible: quedaron {result.active_strategies}/{target_strategies} estrategias."
                )
        except Exception as exc:
            self.after(
                0,
                self._complete_saved_ubs_portfolio_finished,
                portfolio_id,
                None,
                None,
                members,
                target_strategies,
                str(exc),
            )
            return
        self.after(
            0,
            self._complete_saved_ubs_portfolio_finished,
            portfolio_id,
            result,
            inputs,
            members,
            target_strategies,
            "",
        )

    def _complete_saved_ubs_portfolio_finished(
        self,
        portfolio_id: int,
        result: PortfolioResult | None,
        inputs: dict[str, object] | None,
        previous_members: list[dict[str, object]],
        target_strategies: int,
        error: str,
    ) -> None:
        self.ubs_portfolio_running = False
        self._set_ubs_portfolio_detail_running(False)
        if error or result is None:
            messagebox.showerror("Completar portafolio", error or "No se pudo completar el portafolio.")
            self._populate_ubs_portfolio_detail(portfolio_id)
            return
        if inputs is None:
            messagebox.showerror("Completar portafolio", "Faltan los parametros de la propuesta.")
            return
        repair_reductions = sum(
            1 for decision in result.decision_log if decision.action == "reduce_unit_for_repair"
        )
        adjustment_text = (
            f" Se redujeron {repair_reductions} unidad(es) existentes por limites de DD."
            if repair_reductions
            else " Las unidades existentes se conservaron."
        )
        summary = (
            f"Propuesta #{portfolio_id}: {result.active_strategies} estrategias | "
            f"{result.total_units} unidades | lote {result.total_lot:.2f} | "
            f"DD valle {result.actual_valley_dd:.2f}/{result.target_valley_dd:.2f} | "
            f"DD puntual {result.actual_point_dd:.2f}/{result.target_point_dd:.2f}."
            f"{adjustment_text}"
        )
        self.ubs_portfolio_preview_result = result
        self.ubs_portfolio_preview_inputs = inputs
        self.ubs_portfolio_preview_id = portfolio_id
        self.ubs_portfolio_preview_target = target_strategies
        self._create_ubs_portfolio_completion_preview(
            portfolio_id,
            summary,
            self._ubs_portfolio_completion_diff_rows(previous_members, result),
        )

    def _ubs_portfolio_completion_diff_rows(
        self,
        previous_members: list[dict[str, object]],
        result: PortfolioResult,
    ) -> list[tuple[str, str, str, str, int, int, int, str, str, str]]:
        before = {
            str(member.get("set_path") or member.get("set_id") or ""): member
            for member in previous_members
        }
        after = {allocation.set_path or allocation.set_id: allocation for allocation in result.allocations}
        rows: list[tuple[str, str, str, str, int, int, int, str, str, str]] = []
        for set_path in sorted(set(before) | set(after), key=lambda path: Path(path).name.lower()):
            old = before.get(set_path)
            new = after.get(set_path)
            old_units = int(old.get("units") or 0) if old else 0
            new_units = int(new.units) if new else 0
            old_lot = float(old.get("lot") or old_units * 0.01) if old else 0.0
            new_lot = float(new.lot) if new else 0.0
            if old is None:
                state = "NUEVA"
            elif new is None:
                state = "RETIRADA"
            elif old_units != new_units:
                state = "AJUSTADA"
            else:
                state = "SIN CAMBIO"
            candidate = str(new.candidate_id) if new else str(old.get("candidate_id") or "")
            symbol = str(new.symbol) if new else str(old.get("symbol") or "")
            rows.append(
                (
                    Path(set_path).name,
                    candidate,
                    symbol,
                    f"{new_lot:.2f}",
                    old_units,
                    new_units,
                    new_units - old_units,
                    f"{old_lot:.2f}",
                    f"{new_lot - old_lot:+.2f}",
                    state,
                )
            )
        return rows

    def _apply_ubs_portfolio_completion_preview(self) -> None:
        result = getattr(self, "ubs_portfolio_preview_result", None)
        inputs = getattr(self, "ubs_portfolio_preview_inputs", None)
        portfolio_id = getattr(self, "ubs_portfolio_preview_id", None)
        target = getattr(self, "ubs_portfolio_preview_target", None)
        if result is None or inputs is None or portfolio_id is None or target is None:
            messagebox.showerror("Completar portafolio", "La propuesta ya no esta disponible.")
            return
        conn = self._ubs_portfolio_conn()
        try:
            self._save_portfolio_version(
                conn,
                int(portfolio_id),
                "Antes de aplicar recomposicion desde vista previa",
            )
            self._replace_saved_portfolio_result(conn, int(portfolio_id), inputs, result, int(target))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            messagebox.showerror("Completar portafolio", f"No se pudo aplicar la propuesta:\n{exc}")
            return
        finally:
            conn.close()
        preview = getattr(self, "ubs_portfolio_preview_window", None)
        if preview is not None and preview.winfo_exists():
            preview.destroy()
        self.ubs_portfolio_preview_result = None
        self.ubs_portfolio_preview_inputs = None
        self._refresh_ubs_portfolios(select_id=int(portfolio_id))
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios(select_id=int(portfolio_id))
        self._populate_ubs_portfolio_detail(int(portfolio_id))
        self.ubs_portfolio_status.set(f"Portafolio #{portfolio_id} actualizado desde la vista previa.")

    def _cancel_ubs_portfolio_completion_preview(self) -> None:
        preview = getattr(self, "ubs_portfolio_preview_window", None)
        if preview is not None and preview.winfo_exists():
            preview.destroy()
        self.ubs_portfolio_preview_result = None
        self.ubs_portfolio_preview_inputs = None
        portfolio_id = getattr(self, "ubs_portfolio_preview_id", None)
        if portfolio_id is not None:
            self._populate_ubs_portfolio_detail(int(portfolio_id))

    def _replace_saved_portfolio_result(
        self,
        conn: sqlite3.Connection,
        portfolio_id: int,
        inputs: dict[str, object],
        result: PortfolioResult,
        target_strategies: int,
    ) -> None:
        active_symbols = len(
            {portfolio_symbol_key(item.symbol) for item in result.allocations if item.units > 0}
        )
        metrics = {
            "inputs": inputs,
            "warnings": result.warnings,
            "group_summary": result.group_summary,
            "equity_curve_2020_2026": result.equity_curve_2020_2026,
            "unused_sets": [asdict(item) for item in result.unused_sets],
            "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
            "seasonal_coverage": result.seasonal_coverage,
            "seasonal_validation": result.seasonal_validation,
            "margin_summary": result.margin_summary,
            "last_completed_at": datetime.now().isoformat(timespec="seconds"),
        }
        conn.execute(
            """
            update portfolios
            set num_symbols=?, account_capital=?, capital=?,
                target_valley_dd_pct=?, target_point_dd_pct=?,
                target_valley_dd=?, target_point_dd=?, actual_valley_dd=?,
                actual_point_dd=?, valley_usage_pct=?, point_usage_pct=?,
                total_net_profit=?, total_lot=?, total_units=?, active_strategies=?,
                target_strategies=?, stop_reason=?, binding_constraint=?, metrics_json=?
            where id=?
            """,
            (
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
                target_strategies,
                result.stop_reason,
                "valley" if result.valley_usage_pct >= result.point_usage_pct else "point",
                json.dumps(metrics, ensure_ascii=True),
                portfolio_id,
            ),
        )
        conn.execute("delete from portfolio_decision_log where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolio_allocations where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolio_members where portfolio_id=?", (portfolio_id,))
        for allocation in result.allocations:
            conn.execute(
                """
                insert into portfolio_allocations (
                    portfolio_id, set_id, candidate_id, symbol, units, lot,
                    net_profit_contribution, standalone_valley_dd, standalone_point_dd,
                    set_path, timeframe, lot_size_step, margin_required, margin_pct,
                    margin_leverage, margin_contract_size, margin_price,
                    is_report_path, oos_report_path
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    allocation.margin_required,
                    allocation.margin_pct,
                    allocation.margin_leverage,
                    allocation.margin_contract_size,
                    allocation.margin_price,
                    allocation.is_report_path,
                    allocation.oos_report_path,
                ),
            )
            candidate_text = str(allocation.candidate_id)
            legacy_candidate_id = int(candidate_text) if candidate_text.isdigit() else None
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
                    legacy_candidate_id,
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

    def _ubs_portfolio_member_account(self, member: dict[str, object]) -> str:
        account = str(member.get("account_type") or "").strip().upper()
        if account in ACCOUNT_TYPES:
            return account
        candidate_id = str(member.get("candidate_id") or "").strip()
        if ":" in candidate_id:
            prefix = candidate_id.split(":", 1)[0].strip().upper()
            if prefix in ACCOUNT_TYPES:
                return prefix
        return ""

    def _ubs_portfolio_member_candidate_label(self, member: dict[str, object]) -> str:
        candidate_id = str(member.get("candidate_id") or "").strip()
        if ":" in candidate_id:
            prefix, value = candidate_id.split(":", 1)
            if prefix.strip().upper() in ACCOUNT_TYPES:
                return value
        return candidate_id

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
                self._ubs_portfolio_member_account(member),
                self._ubs_portfolio_member_candidate_label(member),
                str(member.get("symbol") or ""),
                str(member.get("timeframe") or member.get("period") or ""),
                units,
                f"{lot:.2f}",
                f"{float(member.get('net_profit_contribution') or 0):,.0f}",
                f"{float(member.get('standalone_valley_dd') or 0):,.2f}",
                f"{float(member.get('standalone_point_dd') or 0):,.2f}",
                f"{float(step):,.2f}" if step not in (None, "") else "-",
                f"{float(member.get('margin_required') or 0):,.2f}",
                f"{float(member.get('margin_pct') or 0):.1f}%",
                f"1:{float(member.get('margin_leverage') or 0):.0f}" if float(member.get("margin_leverage") or 0) else "-",
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

    def _selected_ubs_portfolio_member_paths(self) -> dict[str, str] | None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return None
        selection = self.ubs_portfolio_members_tree.selection()
        if not selection:
            messagebox.showinfo("Portafolio UBS", "Selecciona una asignacion del portafolio.")
            return None
        return getattr(self, "ubs_portfolio_member_paths", {}).get(selection[0], {})

    def _open_selected_ubs_portfolio_member(self) -> None:
        paths = self._selected_ubs_portfolio_member_paths()
        if not paths:
            return
        report = paths.get("oos") or paths.get("is")
        if report:
            self._open_local_file(Path(report))
            return
        messagebox.showinfo("Abrir reporte", "La asignacion seleccionada no tiene reporte guardado.")

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
        created = str(portfolio["created_at"] or "").replace("T", "_").replace(":", "").replace("-", "")
        type_key = str(portfolio["portfolio_type"] or portfolio["type"] or "")
        type_label = PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key or "Portfolio")
        raw_folder_name = f"PORTAFOLIO_{portfolio_id}_{type_label}_{created[:15]}".strip("_")
        folder_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_folder_name).strip("._") or f"PORTAFOLIO_{portfolio_id}"
        dest = Path(folder) / folder_name
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Exportar sets", f"No pude crear la carpeta:\n{exc}")
            return

        exported: list[tuple[str, str, str, int, float, str]] = []
        missing: list[str] = []
        for member in members:
            set_path = Path(str(member.get("set_path") or member.get("set_id") or ""))
            if not set_path.is_file():
                missing.append(set_path.name)
                continue
            out_path = dest / set_path.name
            try:
                if set_path.resolve() != out_path.resolve():
                    shutil.copy2(set_path, out_path)
            except Exception:
                missing.append(set_path.name)
                continue
            exported.append((
                self._ubs_portfolio_member_account(member),
                str(member.get("symbol") or ""),
                str(member.get("timeframe") or member.get("period") or ""),
                int(member.get("units") or 0),
                float(member.get("lot") or 0),
                set_path.name,
            ))

        resumen = dest / f"PORTAFOLIO_{portfolio_id}_resumen.txt"
        capital = float(portfolio["capital"] or portfolio["account_capital"] or 0)
        lines = [
            f"Portafolio: {portfolio['name']}",
            f"Tipo: {PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key)}   Capital: {capital:,.0f}",
            f"DD valle objetivo: {float(portfolio['target_valley_dd'] or 0):,.2f}",
            f"DD puntual objetivo: {float(portfolio['target_point_dd'] or 0):,.2f}",
            f"DD valle usado: {float(portfolio['actual_valley_dd'] or 0):,.2f}",
            f"DD puntual usado: {float(portfolio['actual_point_dd'] or 0):,.2f}",
            f"Net profit total 2020-2026: {float(portfolio['total_net_profit'] or 0):,.2f}",
            "",
            "Sets exportados: copia exacta del .set original probado.",
            "No se modifica Risk, LotPerBalance_step, grid ni ningun otro parametro del EA.",
            "UNID. y LOTE son la asignacion informativa calculada por el portafolio.",
            "",
            f"{'CUENTA':7s} {'SIMBOLO':12s} {'TF':5s} {'UNID.':>7s} {'LOTE':>7s}   SET",
        ]
        for account, symbol, period, units, lot, name in exported:
            lines.append(f"{account:7s} {symbol:12s} {period:5s} {units:7d} {lot:7.2f}   {name}")
        if missing:
            lines.append("")
            lines.append("OMITIDOS (set no encontrado): " + ", ".join(missing))
        write_set_text(resumen, "\n".join(lines), "utf-8")

        self.ubs_portfolio_status.set(f"Exportados {len(exported)} set(s) a {dest}")
        messagebox.showinfo("Exportar sets", f"Exportados {len(exported)} set(s) a:\n{dest}\n\nResumen: {resumen.name}")
        self._open_local_file(dest)
