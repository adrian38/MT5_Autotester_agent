"""Lotes A/M/C y consulta de los portafolios guardados."""
from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from datetime import datetime

from portfolio_manager.ubs_portfolio import PortfolioResult, PortfolioType, portfolio_symbol_key
from ui.ubs_portfolio_base import PORTFOLIO_BUNDLE_DISPLAY, PORTFOLIO_TYPE_DISPLAY


class UBSPortfolioBundleMixin:
    """Lotes A/M/C y consulta de los portafolios guardados."""

    @staticmethod
    def _bundle_variant_key(proposal: dict[str, object], proposal_inputs: dict[str, object], order: int) -> str:
        """Clave con la que se guarda una variante dentro del bundle."""
        key = str(proposal.get("key") or proposal_inputs.get("optimization_profile") or "").strip()
        return key or str(proposal_inputs.get("portfolio_type") or order)

    @staticmethod
    def _bundle_variant_label(proposal: dict[str, object], proposal_inputs: dict[str, object], key: str) -> str:
        """Etiqueta visible de una variante del bundle."""
        return str(proposal.get("label") or proposal_inputs.get("optimization_profile_label") or key)

    @staticmethod
    def _bundle_variant_summary(result: PortfolioResult) -> dict[str, object]:
        """Cifras de cabecera de una variante del bundle."""
        return {
            "total_net_profit": result.total_net_profit,
            "actual_valley_dd": result.actual_valley_dd,
            "actual_point_dd": result.actual_point_dd,
            "actual_closed_valley_dd": result.actual_closed_valley_dd,
            "floating_dd_buffer": result.floating_dd_buffer,
            "valley_usage_pct": result.valley_usage_pct,
            "point_usage_pct": result.point_usage_pct,
            "total_lot": result.total_lot,
            "total_units": result.total_units,
            "active_strategies": result.active_strategies,
        }

    def _bundle_variant_payloads(
        self, proposals: list[dict[str, object]], common_set_id_set: set[str]
    ) -> tuple[dict[str, object], list[str]]:
        """Payload de cada variante, exigiendo que compartan composicion."""
        variant_payloads: dict[str, object] = {}
        variant_order: list[str] = []
        for proposal in proposals:
            result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
            proposal_inputs: dict[str, object] = proposal["inputs"]  # type: ignore[assignment]
            if set(self._active_set_ids_from_result(result)) != common_set_id_set:
                label = str(proposal.get("label") or proposal_inputs.get("optimization_profile_label") or "")
                raise ValueError(
                    "Las variantes no comparten la misma composicion de sets. "
                    f"Rechazada variante {label or 'sin etiqueta'}."
                )
            key = self._bundle_variant_key(proposal, proposal_inputs, len(variant_order) + 1)
            variant_order.append(key)
            payload = self._portfolio_result_metrics(proposal_inputs, result)
            payload["label"] = self._bundle_variant_label(proposal, proposal_inputs, key)
            payload["summary"] = self._bundle_variant_summary(result)
            payload["allocations"] = [asdict(allocation) for allocation in result.allocations]
            variant_payloads[key] = payload
        return variant_payloads, variant_order

    @staticmethod
    def _bundle_composition_labels(selected_inputs: dict[str, object]) -> tuple[str, str]:
        """Tipo de cartera que define la composicion y su nombre visible."""
        composition_type = str(
            selected_inputs.get("composition_portfolio_type")
            or selected_inputs.get("base_portfolio_type")
            or selected_inputs.get("portfolio_type")
            or PortfolioType.BALANCED.value
        )
        composition_label = str(
            selected_inputs.get("composition_portfolio_type_label")
            or PORTFOLIO_TYPE_DISPLAY.get(composition_type, composition_type)
        )
        return composition_type, composition_label

    def _bundle_metrics(
        self, selected_proposal: dict[str, object], selected_inputs: dict[str, object],
        selected_result: PortfolioResult, variant_payloads: dict[str, object],
        variant_order: list[str], common_set_ids: list[str],
    ) -> tuple[dict[str, object], str]:
        """Metricas del bundle y la etiqueta de la composicion elegida."""
        composition_type, composition_label = self._bundle_composition_labels(selected_inputs)
        metrics = self._portfolio_result_metrics(selected_inputs, selected_result)
        metrics.update(
            {
                "portfolio_bundle": True,
                "bundle_display": PORTFOLIO_BUNDLE_DISPLAY,
                "composition_portfolio_type": composition_type,
                "composition_portfolio_type_label": composition_label,
                "selected_variant": str(selected_proposal.get("key") or selected_inputs.get("optimization_profile") or ""),
                "variant_order": variant_order,
                "variants": variant_payloads,
                "common_set_ids": common_set_ids,
            }
        )
        return metrics, composition_label

    def _insert_bundle_row(
        self, conn: sqlite3.Connection, selected_inputs: dict[str, object],
        selected_result: PortfolioResult, metrics: dict[str, object], name: str,
        selected_label: str, common_set_ids: list[str], created_at: str, target_month: int | None,
    ) -> int:
        """Guarda la fila del bundle y devuelve su identificador."""
        active_symbols = len({
            portfolio_symbol_key(allocation.symbol)
            for allocation in selected_result.allocations
            if allocation.units > 0
        })
        cur = conn.execute(
            """
            insert into portfolios (
                created_at, name, type, portfolio_type, num_symbols, account_capital,
                capital, target_valley_dd_pct, target_point_dd_pct, target_valley_dd,
                target_point_dd, actual_valley_dd, actual_point_dd, valley_usage_pct,
                point_usage_pct, total_net_profit, actual_closed_valley_dd,
                floating_dd_buffer, total_lot, total_units,
                active_strategies, target_strategies, stop_reason, binding_constraint,
                portfolio_scope, target_month, metrics_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                name,
                "bundle",
                "bundle",
                active_symbols,
                float(selected_inputs["capital"]),
                float(selected_inputs["capital"]),
                float(selected_inputs["valley_dd_pct"]),
                float(selected_inputs["point_dd_pct"]),
                selected_result.target_valley_dd,
                selected_result.target_point_dd,
                selected_result.actual_valley_dd,
                selected_result.actual_point_dd,
                selected_result.valley_usage_pct,
                selected_result.point_usage_pct,
                selected_result.total_net_profit,
                selected_result.actual_closed_valley_dd,
                selected_result.floating_dd_buffer,
                selected_result.total_lot,
                selected_result.total_units,
                len(common_set_ids),
                len(common_set_ids),
                f"Bundle A/M/C; seleccionado {selected_label}; {selected_result.stop_reason}",
                "valley"
                if (not selected_result.enforce_point_dd or selected_result.valley_usage_pct >= selected_result.point_usage_pct)
                else "point",
                str(selected_inputs.get("portfolio_scope") or "full_history"),
                target_month,
                json.dumps(metrics, ensure_ascii=True),
            ),
        )
        return int(cur.lastrowid)

    def _insert_bundle_allocations(
        self, conn: sqlite3.Connection, portfolio_id: int, proposals: list[dict[str, object]]
    ) -> None:
        """Guarda las asignaciones de cada variante del bundle."""
        for proposal in proposals:
            result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
            proposal_inputs: dict[str, object] = proposal["inputs"]  # type: ignore[assignment]
            variant_key = str(proposal.get("key") or proposal_inputs.get("optimization_profile") or "")
            variant_label = str(proposal.get("label") or proposal_inputs.get("optimization_profile_label") or variant_key)
            for allocation in result.allocations:
                self._insert_portfolio_allocation(
                    conn,
                    portfolio_id,
                    allocation,
                    variant_key=variant_key,
                    variant_label=variant_label,
                )

    @staticmethod
    def _insert_bundle_decisions(
        conn: sqlite3.Connection, portfolio_id: int, selected_result: PortfolioResult,
        selected_label: str,
    ) -> None:
        """Guarda la bitacora de decisiones de la variante seleccionada."""
        for decision in selected_result.decision_log:
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
                    f"{selected_label}: {decision.reason}",
                ),
            )

    def _insert_portfolio_bundle(
        self,
        conn: sqlite3.Connection,
        proposals: list[dict[str, object]],
        selected_result: PortfolioResult,
        *,
        commit: bool = True,
    ) -> int:
        if not proposals:
            raise ValueError("No hay variantes para guardar.")
        selected_proposal = next(
            (
                proposal for proposal in proposals
                if proposal.get("result") is selected_result
            ),
            proposals[0],
        )
        selected_inputs: dict[str, object] = selected_proposal["inputs"]  # type: ignore[assignment]
        selected_result = selected_proposal["result"]  # type: ignore[assignment]
        common_set_ids = self._active_set_ids_from_result(selected_result)
        if not common_set_ids:
            raise ValueError("La composicion seleccionada no tiene asignaciones.")
        variant_payloads, variant_order = self._bundle_variant_payloads(
            proposals, set(common_set_ids)
        )
        metrics, composition_label = self._bundle_metrics(
            selected_proposal, selected_inputs, selected_result, variant_payloads,
            variant_order, common_set_ids,
        )
        selected_label = str(
            selected_proposal.get("label")
            or selected_inputs.get("optimization_profile_label")
            or PORTFOLIO_BUNDLE_DISPLAY
        )
        created_at = datetime.now().isoformat(timespec="seconds")
        target_month = int(selected_inputs["target_month"]) if selected_inputs.get("target_month") else None
        name = (
            f"{PORTFOLIO_BUNDLE_DISPLAY} | Base {composition_label} | "
            + (f"Mes {target_month:02d} | " if target_month else "")
            + f"{len(common_set_ids)} sets | {datetime.now():%d.%m.%Y %H:%M}"
        )
        portfolio_id = self._insert_bundle_row(
            conn, selected_inputs, selected_result, metrics, name, selected_label,
            common_set_ids, created_at, target_month,
        )
        self._insert_bundle_allocations(conn, portfolio_id, proposals)
        self._insert_bundle_decisions(conn, portfolio_id, selected_result, selected_label)
        if commit:
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
            order by
                case coalesce(variant_key, '')
                    when 'aggressive' then 1
                    when 'balanced' then 2
                    when 'conservative' then 3
                    else 9
                end,
                set_id,
                units desc,
                net_profit_contribution desc
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
                "variant_key": row["variant_key"] if "variant_key" in row.keys() else "",
                "variant_label": row["variant_label"] if "variant_label" in row.keys() else "",
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

