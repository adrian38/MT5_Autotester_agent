"""Alta en memoria de un portafolio, sus asignaciones y sus lotes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from portfolio_manager.ubs_portfolio import PortfolioResult, portfolio_symbol_key
from ui.ubs_portfolio_base import PORTFOLIO_TYPE_DISPLAY


def _portfolio_allocation_values(portfolio_id, allocation, variant_key, variant_label):
    return (
        portfolio_id, variant_key, variant_label, allocation.set_id,
        allocation.candidate_id, allocation.symbol, allocation.units, allocation.lot,
        allocation.net_profit_contribution, allocation.standalone_valley_dd,
        allocation.standalone_point_dd, allocation.set_path or allocation.set_id,
        allocation.timeframe or "", allocation.lot_size_step, allocation.margin_required,
        allocation.margin_pct, allocation.margin_leverage, allocation.margin_contract_size,
        allocation.margin_price, allocation.is_report_path, allocation.oos_report_path,
        allocation.final_tick_report_path, allocation.full_history_report_path,
        allocation.max_balance_dd_001, allocation.max_equity_dd_001,
        allocation.floating_dd_source, allocation.standalone_floating_dd,
        allocation.recent_net_profit_001, allocation.recent_equity_dd_001,
        int(allocation.has_recent_performance),
    )


def _portfolio_member_values(portfolio_id, allocation, variant_key, variant_label):
    candidate_id = int(allocation.candidate_id) if str(allocation.candidate_id).isdigit() else None
    return (
        portfolio_id, variant_key, variant_label, candidate_id,
        allocation.set_path or allocation.set_id, allocation.symbol,
        allocation.timeframe or "", allocation.units, allocation.lot,
        allocation.lot_size_step, allocation.standalone_valley_dd, 0.0,
        allocation.net_profit_contribution, allocation.is_report_path,
        allocation.oos_report_path,
    )


def _insert_portfolio_decisions(conn, portfolio_id, decisions) -> None:
    for decision in decisions:
        conn.execute(
            """insert into portfolio_decision_log (
                portfolio_id, step, action, set_id, from_set_id, to_set_id,
                gain, valley_cost, point_cost, score, portfolio_net_profit_after,
                portfolio_valley_dd_after, portfolio_point_dd_after, reason
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                portfolio_id, decision.step, decision.action, decision.set_id,
                decision.from_set_id, decision.to_set_id, decision.gain,
                decision.valley_cost, decision.point_cost, decision.score,
                decision.portfolio_net_profit_after, decision.portfolio_valley_dd_after,
                decision.portfolio_point_dd_after, decision.reason,
            ),
        )


class UBSPortfolioInsertMixin:
    """Alta en memoria de un portafolio, sus asignaciones y sus lotes."""

    def _insert_portfolio_allocation(
        self,
        conn: sqlite3.Connection,
        portfolio_id: int,
        allocation,
        *,
        variant_key: str = "",
        variant_label: str = "",
    ) -> None:
        conn.execute(
            """
            insert into portfolio_allocations (
                portfolio_id, variant_key, variant_label,
                set_id, candidate_id, symbol, units, lot,
                net_profit_contribution, standalone_valley_dd, standalone_point_dd,
                set_path, timeframe, lot_size_step, margin_required, margin_pct,
                margin_leverage, margin_contract_size, margin_price,
                is_report_path, oos_report_path, final_tick_report_path,
                full_history_report_path, max_balance_dd_001, max_equity_dd_001,
                floating_dd_source, standalone_floating_dd, recent_net_profit_001,
                recent_equity_dd_001, has_recent_performance
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _portfolio_allocation_values(portfolio_id, allocation, variant_key, variant_label),
        )
        conn.execute(
            """
            insert into portfolio_members (
                portfolio_id, variant_key, variant_label,
                candidate_id, set_path, symbol, period, lot_multiplier,
                lot, lot_size_step, standalone_dd, quality_score, combined_net_profit,
                is_report_path, oos_report_path
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _portfolio_member_values(portfolio_id, allocation, variant_key, variant_label),
        )

    def _insert_portfolio(
        self,
        conn: sqlite3.Connection,
        inputs: dict[str, object],
        result: PortfolioResult,
        *,
        commit: bool = True,
    ) -> int:
        values = self._portfolio_insert_values(inputs, result)
        cur = conn.execute(
            """insert into portfolios (
                created_at, name, type, portfolio_type, num_symbols, account_capital,
                capital, target_valley_dd_pct, target_point_dd_pct, target_valley_dd,
                target_point_dd, actual_valley_dd, actual_point_dd, valley_usage_pct,
                point_usage_pct, total_net_profit, actual_closed_valley_dd,
                floating_dd_buffer, total_lot, total_units,
                active_strategies, target_strategies, stop_reason, binding_constraint,
                portfolio_scope, target_month, metrics_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        portfolio_id = int(cur.lastrowid)
        for allocation in result.allocations:
            self._insert_portfolio_allocation(conn, portfolio_id, allocation)
        _insert_portfolio_decisions(conn, portfolio_id, result.decision_log)
        if commit:
            conn.commit()
        return portfolio_id

    def _portfolio_insert_values(self, inputs, result):
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
        metrics = self._portfolio_result_metrics(inputs, result)
        return (
            created_at, name, portfolio_type, portfolio_type, active_symbols,
            float(inputs["capital"]), float(inputs["capital"]),
            float(inputs["valley_dd_pct"]), float(inputs["point_dd_pct"]),
            result.target_valley_dd, result.target_point_dd, result.actual_valley_dd,
            result.actual_point_dd, result.valley_usage_pct, result.point_usage_pct,
            result.total_net_profit, result.actual_closed_valley_dd,
            result.floating_dd_buffer, result.total_lot, result.total_units,
            result.active_strategies, result.active_strategies, result.stop_reason,
            "valley" if (not result.enforce_point_dd or result.valley_usage_pct >= result.point_usage_pct) else "point",
            portfolio_scope, target_month, json.dumps(metrics, ensure_ascii=True),
        )

    def _active_set_ids_from_result(self, result: PortfolioResult) -> list[str]:
        return [allocation.set_id for allocation in result.allocations if allocation.units > 0]
