"""Versiones guardadas de un portafolio y recalculo de sus metricas."""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from portfolio_manager.ubs_portfolio import (
    bootstrap_valley_drawdown,
    evaluate_portfolio,
    load_robust_sets_from_rows,
    portfolio_group_summary,
    portfolio_symbol_key,
    validate_strict_monthly_portfolio,
)


class UBSPortfolioVersionsMixin:
    """Versiones guardadas de un portafolio y recalculo de sus metricas."""

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
        return fallback_account, self._ubs_portfolio_memory_from_label(fallback_account), candidate_id

    @staticmethod
    def _portfolio_nominal_valley_limit(portfolio) -> float:
        """Limite de valle sin la reserva de DD aplicada."""
        return (
            float(portfolio["capital"] or portfolio["account_capital"] or 0)
            * float(portfolio["target_valley_dd_pct"] or 0)
            / 100.0
        )

    def _empty_portfolio_metrics(
        self, conn: sqlite3.Connection, portfolio, portfolio_id: int, metrics: dict
    ) -> None:
        """Deja a cero un portafolio que se quedo sin estrategias."""
        metrics["equity_curve_2020_2026"] = [0.0]
        metrics["group_summary"] = {}
        metrics["seasonal_coverage"] = {}
        metrics["seasonal_validation"] = {}
        metrics["stress_bootstrap"] = asdict(
            bootstrap_valley_drawdown(
                [0.0],
                nominal_valley_dd_limit=self._portfolio_nominal_valley_limit(portfolio),
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

    def _recalculated_strategies(self, portfolio, members: list) -> tuple[list, list, list[str]]:
        """Curvas de los miembros guardados, completas y acotadas al mes."""
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
        strategies, scope_warnings = self._scope_portfolio_sets(
            strategies,
            self._saved_portfolio_inputs(portfolio),
        )
        warnings.extend(scope_warnings)
        if len(strategies) != len(members):
            raise ValueError("No se pudieron reconstruir todas las curvas del mes objetivo.")
        return strategies, full_strategies, warnings

    @staticmethod
    def _recalculated_seasonal_coverage(strategies: list, units: dict[str, int]) -> dict:
        """Cobertura mensual de las estrategias que siguen con unidades."""
        return {
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

    @staticmethod
    def _recalculated_seasonal_validation(
        saved_inputs: dict, full_strategies: list, units: dict[str, int],
        target_valley: float, target_point: float,
    ) -> dict:
        """Auditoria mensual estricta, si el portafolio la tenia activada."""
        if not bool(saved_inputs.get("strict_yearly_month_validation")):
            return {}
        return validate_strict_monthly_portfolio(
            full_strategies,
            units,
            target_month=int(saved_inputs.get("target_month") or 0),
            target_valley_dd=target_valley,
            target_point_dd=target_point,
            enforce_point_dd=bool(saved_inputs.get("enforce_point_dd", True)),
            lookback_years=5,
        )

    @staticmethod
    def _store_recalculated_portfolio(
        conn: sqlite3.Connection, portfolio_id: int, strategies: list,
        units: dict[str, int], evaluation, metrics: dict,
    ) -> None:
        """Guarda las cifras recalculadas del portafolio."""
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

    def _recalculate_saved_portfolio(self, conn: sqlite3.Connection, portfolio_id: int) -> None:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe.")
        members = self._portfolio_members(conn, portfolio_id)
        metrics = self._portfolio_metrics_json(portfolio)
        if not members:
            self._empty_portfolio_metrics(conn, portfolio, portfolio_id, metrics)
            return
        strategies, full_strategies, warnings = self._recalculated_strategies(portfolio, members)
        saved_inputs = self._saved_portfolio_inputs(portfolio)
        units = {
            str(member.get("set_path") or member.get("set_id")): int(member.get("units") or 0)
            for member in members
        }
        target_valley = float(portfolio["target_valley_dd"] or 0)
        target_point = float(portfolio["target_point_dd"] or 0)
        evaluation = evaluate_portfolio(
            strategies,
            units,
            target_valley,
            target_point,
            saved_inputs.get("max_daily_dd"),  # type: ignore[arg-type]
            bool(saved_inputs.get("enforce_point_dd", True)),
            bool(saved_inputs.get("daily_dd_full_history", False)),
        )
        metrics["equity_curve_2020_2026"] = evaluation.equity_curve_2020_2026
        metrics["group_summary"] = portfolio_group_summary(strategies, units)
        metrics["seasonal_coverage"] = self._recalculated_seasonal_coverage(strategies, units)
        metrics["seasonal_validation"] = self._recalculated_seasonal_validation(
            saved_inputs, full_strategies, units, target_valley, target_point
        )
        metrics["stress_bootstrap"] = asdict(
            bootstrap_valley_drawdown(
                evaluation.equity_curve_2020_2026,
                nominal_valley_dd_limit=self._portfolio_nominal_valley_limit(portfolio),
                effective_valley_dd_limit=target_valley,
            )
        )
        if warnings:
            metrics.setdefault("warnings", []).extend(warnings)
        self._store_recalculated_portfolio(
            conn, portfolio_id, strategies, units, evaluation, metrics
        )
