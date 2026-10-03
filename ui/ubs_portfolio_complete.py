"""Completado de un portafolio guardado y sustitucion de su resultado."""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from ui.ubs_portfolio_complete_plan import UBSPortfolioCompletionPlanMixin
from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    optimize_portfolio,
    portfolio_symbol_key,
)


class UBSPortfolioCompleteMixin(UBSPortfolioCompletionPlanMixin):
    """Completado de un portafolio guardado y sustitucion de su resultado."""

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
        if self._portfolio_is_bundle(portfolio):
            messagebox.showinfo(
                "Completar portafolio",
                "Este portafolio es A/M/C. Para mantener los mismos sets en las tres variantes, regeneralo desde Portfolio Builder.",
            )
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

    def _complete_saved_ubs_portfolio_plan(
        self, portfolio_id: int, portfolio: dict[str, object],
        members: list[dict[str, object]], target_strategies: int,
    ):
        """Propuesta que completa el portafolio guardado sin tocar lo que ya tiene."""
        inputs = self._saved_portfolio_inputs(portfolio)  # type: ignore[arg-type]
        portfolio_type = self._portfolio_type_from_label(inputs["portfolio_type"])
        inputs["portfolio_type"] = portfolio_type.value
        is_monthly = str(inputs.get("portfolio_scope") or "full_history") == "monthly"
        used = self._completion_used_sets(inputs, is_monthly, portfolio_type, portfolio_id)
        required_rows = self._completion_required_rows(members)
        required_sets, required_warnings = load_robust_sets_from_rows(required_rows, [])
        if len(required_sets) != len(required_rows):
            raise ValueError("No se pudieron reconstruir todas las estrategias que deben conservarse.")
        rows, month_warnings, grid_warnings, allowed_groups = self._completion_candidate_rows(
            inputs, is_monthly
        )
        candidate_sets, load_warnings = self._completion_candidate_sets(rows, used, allowed_groups)
        full_sets_for_strict_validation = list(required_sets) + list(candidate_sets)
        required_sets, required_scope_warnings = self._scope_portfolio_sets(required_sets, inputs)
        candidate_sets, candidate_scope_warnings = self._scope_portfolio_sets(candidate_sets, inputs)
        raw_sets = self._completion_raw_sets(candidate_sets, required_sets)
        result = self._completion_optimize(
            inputs,
            portfolio_type,
            raw_sets,
            [strategy.set_id for strategy in required_sets],
            self._completion_saved_units(members, required_sets),
            self._saved_portfolio_curves_all_accounts(
                portfolio_type,
                exclude_portfolio_id=portfolio_id,
                portfolio_scope="monthly" if is_monthly else "full_history",
                target_month=int(inputs.get("target_month") or 0) if is_monthly else None,
            ),
            target_strategies,
        )
        result.seasonal_coverage = self._completion_seasonal_coverage(result, raw_sets)
        if bool(inputs.get("strict_yearly_month_validation")):
            result = self._completion_strict_validation(
                result, full_sets_for_strict_validation, inputs
            )
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
        return result, inputs

    def _complete_saved_ubs_portfolio_worker(
        self,
        portfolio_id: int,
        portfolio: dict[str, object],
        members: list[dict[str, object]],
        target_strategies: int,
    ) -> None:
        try:
            result, inputs = self._complete_saved_ubs_portfolio_plan(
                portfolio_id, portfolio, members, target_strategies
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
            "daily_dd_summary": result.daily_dd_summary,
            "max_daily_dd": result.max_daily_dd,
            "target_daily_dd": result.target_daily_dd,
            "daily_dd_full_history": result.daily_dd_full_history,
            "enforce_point_dd": result.enforce_point_dd,
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
                "valley"
                if (not result.enforce_point_dd or result.valley_usage_pct >= result.point_usage_pct)
                else "point",
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
