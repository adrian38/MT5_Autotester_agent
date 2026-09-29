"""Esquema SQLite del portafolio y conexiones por cuenta."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ubs.account import account_memory_path, account_types_for_broker, normalize_broker
from ubs.db import connect_memory
from ui.ubs_portfolio_base import BASE_DIR


class UBSPortfolioSchemaMixin:
    """Esquema SQLite del portafolio y conexiones por cuenta."""

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
                actual_closed_valley_dd real not null default 0,
                floating_dd_buffer real not null default 0,
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
            ("actual_closed_valley_dd", "real not null default 0"),
            ("floating_dd_buffer", "real not null default 0"),
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
                variant_key text not null default '',
                variant_label text not null default '',
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
                final_tick_report_path text,
                full_history_report_path text,
                max_balance_dd_001 real not null default 0,
                max_equity_dd_001 real not null default 0,
                floating_dd_source text not null default '',
                standalone_floating_dd real not null default 0,
                recent_net_profit_001 real not null default 0,
                recent_equity_dd_001 real not null default 0,
                has_recent_performance integer not null default 0,
                foreign key (portfolio_id) references portfolios(id)
            )
            """
        )
        for column, definition in (
            ("variant_key", "text not null default ''"),
            ("variant_label", "text not null default ''"),
            ("margin_required", "real not null default 0"),
            ("margin_pct", "real not null default 0"),
            ("margin_leverage", "real not null default 0"),
            ("margin_contract_size", "real not null default 0"),
            ("margin_price", "real not null default 0"),
            ("final_tick_report_path", "text"),
            ("full_history_report_path", "text"),
            ("max_balance_dd_001", "real not null default 0"),
            ("max_equity_dd_001", "real not null default 0"),
            ("floating_dd_source", "text not null default ''"),
            ("standalone_floating_dd", "real not null default 0"),
            ("recent_net_profit_001", "real not null default 0"),
            ("recent_equity_dd_001", "real not null default 0"),
            ("has_recent_performance", "integer not null default 0"),
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
                variant_key text not null default '',
                variant_label text not null default '',
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
        for column, definition in (
            ("variant_key", "text not null default ''"),
            ("variant_label", "text not null default ''"),
        ):
            self._ensure_sqlite_column(conn, "portfolio_members", column, definition)
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
        broker_var = getattr(self, "ubs_broker", None)
        active_broker = normalize_broker(broker_var.get() if broker_var is not None else "ROBOFOREX")
        for account_type in account_types_for_broker(active_broker):
            path = account_memory_path(BASE_DIR, account_type, active_broker)
            if path.exists():
                paths.append((f"{active_broker}/{account_type}", path))
        return paths

    def _ubs_portfolio_memory_from_label(self, label: str) -> Path:
        text = str(label or "").strip().upper()
        if "/" in text:
            broker, account_type = text.split("/", 1)
            return account_memory_path(BASE_DIR, account_type, broker)
        broker_var = getattr(self, "ubs_broker", None)
        broker = str(broker_var.get()) if broker_var is not None else "ROBOFOREX"
        return account_memory_path(BASE_DIR, text or "ECN", broker)

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
