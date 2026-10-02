"""Propuestas alternativas del portafolio y su vista previa."""
from __future__ import annotations

from tkinter import messagebox

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    optimize_portfolio,
    optimize_strict_monthly_portfolio,
)
from ui.ubs_portfolio_base import PORTFOLIO_TYPE_DISPLAY


def _portfolio_proposal_comparison_row(
    proposal: dict[str, object], before_units: dict[str, int],
) -> tuple[object, ...]:
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
    daily_limit = result.target_daily_dd
    point_text = (
        f"{result.actual_point_dd:,.2f} info"
        if not result.enforce_point_dd
        else f"{result.actual_point_dd:,.2f}/{result.target_point_dd:,.2f}"
    )
    return (
        proposal["key"], proposal["label"], f"{result.total_net_profit:,.0f}",
        f"{result.actual_valley_dd:,.2f}/{result.target_valley_dd:,.2f}", point_text,
        f"{result.max_daily_dd:,.2f}/{daily_limit:,.2f}" if daily_limit else "-",
        f"{stress.valley_dd_p50:,.2f}" if stress else "-",
        f"{stress.valley_dd_p95:,.2f}" if stress else "-",
        f"{stress.probability_exceed_nominal_pct:.1f}%" if stress else "-",
        f"{stress.probability_exceed_effective_pct:.1f}%" if stress else "-",
        f"{margin_pct:.1f}%", f"{float(proposal['reserve_pct']):.1f}%",
        f"{margin_total:,.0f}/{margin_limit:,.0f}" if margin_summary else "-",
        f"{margin_usage:.1f}%" if margin_summary else "-", result.total_units,
        result.active_strategies, f"{max_group_pct:.1f}%", changes, stress_status,
    )


class UBSPortfolioProposalsMixin:
    """Propuestas alternativas del portafolio y su vista previa."""

    def _optimize_ubs_portfolio_proposals(
        self,
        raw_sets: list,
        inputs: dict[str, object],
        base_type: PortfolioType,
        existing_curves: list[list[float]],
        *,
        strict_full_sets: list | None = None,
        specs: tuple[tuple[str, str, PortfolioType, float], ...] | None = None,
        progress=None,
    ) -> list[dict[str, object]]:
        configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
        is_monthly_scope = str(inputs.get("portfolio_scope") or "full_history") == "monthly"
        enforce_point_dd = bool(inputs.get("enforce_point_dd", not is_monthly_scope))
        proposal_specs = specs or (
            ("profit", "Maximo beneficio", base_type, configured_reserve),
            ("balanced", "Equilibrada", PortfolioType.BALANCED, max(configured_reserve, 15.0)),
            ("margin", "Maximo margen DD", PortfolioType.CONSERVATIVE, max(configured_reserve, 25.0)),
        )
        proposals: list[dict[str, object]] = []
        errors: list[str] = []
        for index, (key, label, objective_type, reserve) in enumerate(proposal_specs, start=1):
            if callable(progress):
                progress(label, index)
            proposal_inputs = dict(inputs)
            proposal_inputs["optimization_profile"] = key
            proposal_inputs["optimization_profile_label"] = label
            proposal_inputs["portfolio_type"] = objective_type.value
            proposal_inputs["portfolio_type_label"] = PORTFOLIO_TYPE_DISPLAY[objective_type.value]
            proposal_inputs["dd_reserve_pct"] = reserve
            try:
                strict_monthly = (
                    bool(inputs.get("strict_yearly_month_validation"))
                    and is_monthly_scope
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
                    if bool(inputs.get("validate_margin") or inputs.get("validate_roboforex_margin") or inputs.get("validate_ttp_margin"))
                    else None,
                    "max_margin_pct": float(inputs.get("max_margin_pct") or 100.0)
                    if bool(inputs.get("validate_margin") or inputs.get("validate_roboforex_margin") or inputs.get("validate_ttp_margin"))
                    else None,
                    "margin_profile": str(inputs.get("margin_profile") or "roboforex"),
                    "stock_leverage": 20.0,
                    "default_leverage": 500.0,
                    "stock_contract_size": 100.0,
                    "default_contract_size": 1.0,
                    "max_daily_dd": inputs.get("max_daily_dd"),
                    "enforce_point_dd": enforce_point_dd,
                    "daily_dd_full_history": bool(inputs.get("daily_dd_full_history", False)),
                }
                if strict_monthly:
                    result = optimize_strict_monthly_portfolio(
                        monthly_sets=raw_sets,
                        full_sets=strict_full_sets or [],
                        target_month=int(inputs.get("target_month") or 0),
                        use_deep_refinement=bool(inputs.get("use_deep_candidate_engine")),
                        **optimizer_kwargs,  # type: ignore[arg-type]
                    )
                else:
                    result = optimize_portfolio(
                        raw_sets=raw_sets,
                        use_deep_refinement=bool(inputs.get("deep_optimization")),
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
        comparison_rows = [
            _portfolio_proposal_comparison_row(proposal, before_units)
            for proposal in proposals
        ]
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

    @staticmethod
    def _ubs_portfolio_proposal_summary_text(
        proposal: dict[str, object], result: PortfolioResult, inputs: dict[str, object]
    ) -> tuple[str, bool]:
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
        daily_text = (
            f"DD diario {result.max_daily_dd:.2f}/{float(result.target_daily_dd):.2f} | "
            if result.target_daily_dd else ""
        )
        point_text = (
            f"DD puntual {result.actual_point_dd:.2f} info | " if not result.enforce_point_dd
            else f"DD puntual {result.actual_point_dd:.2f}/{result.target_point_dd:.2f} | "
        )
        text = (
            f"{month_prefix}{proposal['label']}: net {result.total_net_profit:,.2f} | "
            f"DD valle {result.actual_valley_dd:.2f}/{result.target_valley_dd:.2f} | "
            f"{point_text}{daily_text}{stress_text}{margin_text}"
            f"reserva {float(proposal['reserve_pct']):.1f}% | "
            f"{result.active_strategies} estrategias | {result.total_units} unidades."
        )
        return text, bool(stress and stress.alert)

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
            summary, stress_alert = self._ubs_portfolio_proposal_summary_text(
                proposal, result, inputs
            )
            summary_var.set(summary)
            summary_label = getattr(self, "ubs_portfolio_proposals_summary_label", None)
            if summary_label is not None:
                summary_label.configure(
                    fg=self.colors["danger"] if stress_alert else self.colors["accent_soft_text"]
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
