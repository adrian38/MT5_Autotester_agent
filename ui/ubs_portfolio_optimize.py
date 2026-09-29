"""Reoptimizacion de un portafolio guardado con sus sets bloqueados."""
from __future__ import annotations

import threading
from pathlib import Path
from tkinter import messagebox

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    optimize_portfolio,
    slice_strategy_sets_to_month,
    validate_strict_monthly_portfolio,
)
from ui.ubs_portfolio_base import PORTFOLIO_TYPE_BATCH_SPECS, PORTFOLIO_TYPE_DISPLAY


class UBSPortfolioOptimizeMixin:
    """Reoptimizacion de un portafolio guardado con sus sets bloqueados."""

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
                enforce_point_dd=bool(inputs.get("enforce_point_dd", True)),
                lookback_years=5,
            )
            result.seasonal_validation = validation
            if bool(validation.get("passed")):
                result.warnings.append(
                    "Validacion estricta mensual OK: el mes objetivo pasa año a año "
                    "todos los meses respetan el DD y el objetivo es el mejor mes neto de los ultimos 5 años."
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

    def _optimize_locked_ubs_portfolio_variants(
        self,
        raw_sets: list,
        inputs: dict[str, object],
        base_type: PortfolioType,
        existing_curves_by_type: dict[PortfolioType, list[list[float]]],
        *,
        progress=None,
    ) -> list[dict[str, object]]:
        configured_reserve = float(inputs.get("dd_reserve_pct") or 0)
        base_key = next(
            (key for key, _label, portfolio_type in PORTFOLIO_TYPE_BATCH_SPECS if portfolio_type == base_type),
            base_type.value,
        )
        base_label = PORTFOLIO_TYPE_DISPLAY.get(base_type.value, base_type.value)
        # The composition is shared by every A/M/C variant. Select it against
        # the strictest reserve up front so the locked one-unit allocation is
        # feasible for the conservative variant as well as the looser ones.
        base_reserve = max(
            self._portfolio_type_reserve_pct(configured_reserve, portfolio_type)
            for _key, _label, portfolio_type in PORTFOLIO_TYPE_BATCH_SPECS
        )
        if callable(progress):
            progress(f"Composicion base {base_label}", 0)
        base_proposal = self._optimize_ubs_portfolio_proposals(
            raw_sets,
            inputs,
            base_type,
            existing_curves_by_type.get(base_type, []),
            specs=((base_key, f"Composicion base {base_label}", base_type, base_reserve),),
        )[0]
        base_result: PortfolioResult = base_proposal["result"]  # type: ignore[assignment]
        locked_set_ids = self._active_set_ids_from_result(base_result)
        if not locked_set_ids:
            raise ValueError("La composicion base no produjo ningun set activo.")
        raw_by_id = {strategy.set_id: strategy for strategy in raw_sets}
        missing_locked = [set_id for set_id in locked_set_ids if set_id not in raw_by_id]
        if missing_locked:
            raise ValueError(
                "La composicion base contiene sets que ya no estan en el universo cargado: "
                + ", ".join(Path(set_id).name for set_id in missing_locked)
            )
        locked_sets = [raw_by_id[set_id] for set_id in locked_set_ids]
        locked_count = len(locked_sets)
        max_total_units = inputs.get("max_total_units")
        if max_total_units is not None and int(max_total_units) < locked_count:
            raise ValueError(
                "Max unidades es menor que la cantidad de sets de la composicion comun "
                f"({int(max_total_units)} < {locked_count})."
            )

        enforce_point_dd = bool(inputs.get("enforce_point_dd", True))
        proposals: list[dict[str, object]] = []
        errors: list[str] = []
        for index, (key, label, portfolio_type) in enumerate(PORTFOLIO_TYPE_BATCH_SPECS, start=1):
            if callable(progress):
                progress(label, index)
            reserve = self._portfolio_type_reserve_pct(configured_reserve, portfolio_type)
            proposal_inputs = dict(inputs)
            proposal_inputs["optimization_profile"] = key
            proposal_inputs["optimization_profile_label"] = label
            proposal_inputs["portfolio_type"] = portfolio_type.value
            proposal_inputs["portfolio_type_label"] = PORTFOLIO_TYPE_DISPLAY[portfolio_type.value]
            proposal_inputs["composition_portfolio_type"] = base_type.value
            proposal_inputs["composition_portfolio_type_label"] = base_label
            proposal_inputs["dd_reserve_pct"] = reserve
            try:
                result = optimize_portfolio(
                    raw_sets=locked_sets,
                    capital=float(inputs["capital"]),
                    valley_dd_pct=float(inputs["valley_dd_pct"]),
                    point_dd_pct=float(inputs["point_dd_pct"]),
                    portfolio_type=portfolio_type,
                    min_trades_2020_2026=int(inputs["min_trades_2020_2026"]),
                    top_k_per_symbol=max(int(inputs["top_k_per_symbol"]), locked_count),
                    max_total_candidates=None,
                    max_units_per_set=inputs.get("max_units_per_set"),
                    max_total_units=max_total_units,
                    max_units_per_symbol=inputs.get("max_units_per_symbol"),
                    max_sets_per_symbol=inputs.get("max_sets_per_symbol"),
                    run_local_search=bool(inputs.get("run_local_search", True)),
                    max_pair_corr=inputs.get("max_pair_corr") if inputs.get("use_correlation", True) else None,
                    max_downside_corr=inputs.get("max_downside_corr") if inputs.get("use_correlation", True) else None,
                    max_dd_overlap=inputs.get("max_dd_overlap") if inputs.get("use_correlation", True) else None,
                    existing_portfolio_curves=existing_curves_by_type.get(portfolio_type, []),
                    max_portfolio_corr=inputs.get("max_portfolio_corr") if inputs.get("use_correlation", True) else None,
                    max_units_per_group_pct=None,
                    max_sets_per_group=locked_count,
                    group_unit_cap_bootstrap=max(locked_count, 1),
                    minimum_active_strategies=locked_count,
                    maximum_active_strategies=locked_count,
                    dd_reserve_pct=reserve,
                    search_restarts=0,
                    margin_balance=float(inputs["capital"])
                    if bool(inputs.get("validate_margin") or inputs.get("validate_roboforex_margin") or inputs.get("validate_ttp_margin"))
                    else None,
                    max_margin_pct=float(inputs.get("max_margin_pct") or 100.0)
                    if bool(inputs.get("validate_margin") or inputs.get("validate_roboforex_margin") or inputs.get("validate_ttp_margin"))
                    else None,
                    margin_profile=str(inputs.get("margin_profile") or "roboforex"),
                    stock_leverage=20.0,
                    default_leverage=500.0,
                    stock_contract_size=100.0,
                    default_contract_size=1.0,
                    max_daily_dd=inputs.get("max_daily_dd"),
                    enforce_point_dd=enforce_point_dd,
                    daily_dd_full_history=bool(inputs.get("daily_dd_full_history", False)),
                    use_deep_refinement=bool(inputs.get("deep_optimization")),
                )
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                continue
            variant_ids = set(self._active_set_ids_from_result(result))
            if variant_ids != set(locked_set_ids):
                errors.append(f"{label}: el optimizador no pudo mantener todos los sets comunes.")
                continue
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
            result.warnings.insert(
                0,
                f"Composicion comun A/M/C bloqueada desde base {base_label}: {locked_count} sets; "
                f"seleccionada con reserva DD comun {base_reserve:.1f}%.",
            )
            if int(inputs.get("search_restarts") or 0) > 0:
                result.warnings.append(
                    "Reinicios multi-start omitidos en variante bloqueada para no cambiar la composicion de sets."
                )
            proposals.append(
                {
                    "key": key,
                    "label": label,
                    "reserve_pct": reserve,
                    "result": result,
                    "inputs": proposal_inputs,
                }
            )
        if len(proposals) < len(PORTFOLIO_TYPE_BATCH_SPECS):
            raise ValueError(
                "No se pudieron calcular las tres variantes sobre la misma composicion. "
                + " | ".join(errors)
            )
        return proposals

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
        if self._portfolio_is_bundle(portfolio):
            messagebox.showinfo(
                "Revalidar portafolio",
                "Este portafolio es A/M/C con composicion comun. Regeneralo desde Portfolio Builder para recalcular las tres variantes juntas.",
            )
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
            allowed_groups = {str(group) for group in (inputs.get("allowed_asset_groups") or [])}
            if allowed_groups:
                rows = [
                    row for row in rows
                    if self._portfolio_group_key(str(row.get("target_symbol") or row.get("symbol") or "")) in allowed_groups
                ]
            if is_monthly:
                used = (
                    self._used_monthly_set_paths_all_accounts(
                        exclude_portfolio_id=portfolio_id,
                    )
                    if bool(inputs.get("exclude_monthly_used"))
                    else []
                )
            else:
                used = (
                    self._used_set_paths_all_accounts(
                        portfolio_type,
                        exclude_portfolio_id=portfolio_id,
                    )
                    if bool(inputs.get("exclude_used_sets", True))
                    else []
                )
            raw_sets, load_warnings = load_robust_sets_from_rows(rows, used)
            if allowed_groups:
                raw_sets = [
                    strategy for strategy in raw_sets
                    if self._portfolio_group_key(str(getattr(strategy, "symbol", ""))) in allowed_groups
                ]
            full_sets_for_strict_validation = list(raw_sets)
            raw_sets, scope_warnings = self._scope_portfolio_sets(raw_sets, inputs)
            if not raw_sets:
                raise ValueError("No quedan candidatos elegibles para reoptimizar.")
            proposals = self._optimize_ubs_portfolio_proposals(
                raw_sets,
                inputs,
                portfolio_type,
                (
                    self._saved_monthly_portfolio_curves_all_accounts(
                        exclude_portfolio_id=portfolio_id,
                    )
                    if is_monthly and bool(inputs.get("corr_with_monthly_portfolios"))
                    else self._saved_portfolio_curves_all_accounts(
                        portfolio_type,
                        exclude_portfolio_id=portfolio_id,
                    )
                    if not is_monthly
                    else []
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
