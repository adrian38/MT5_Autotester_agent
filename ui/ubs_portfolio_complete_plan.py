"""Piezas con las que se arma la propuesta para completar un portafolio."""
from __future__ import annotations

from ui.ubs_portfolio_base import portfolio_seasonal_coverage, portfolio_validates_margin
from portfolio_manager.ubs_portfolio import (
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    optimize_portfolio,
)


class UBSPortfolioCompletionPlanMixin:
    """Candidatas, universo y optimizacion de una reparacion de portafolio."""

    @staticmethod
    def _completion_required_rows(members: list[dict[str, object]]) -> list[dict[str, object]]:
        """Filas de los miembros actuales, que la reparacion debe conservar."""
        return [
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

    def _completion_candidate_rows(
        self, inputs: dict[str, object], is_monthly: bool
    ) -> tuple[list[dict[str, object]], list[str], list[str], set[str]]:
        """Candidatas disponibles tras los filtros guardados del portafolio."""
        rows = self._final_tick_passed_candidates_all_accounts(include_quarantined=is_monthly)
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
        return rows, month_warnings, grid_warnings, allowed_groups

    def _completion_candidate_sets(
        self, rows: list[dict[str, object]], used: list, allowed_groups: set[str]
    ) -> tuple[list, list[str]]:
        """Estrategias candidatas reconstruidas y filtradas por grupo."""
        candidate_sets, load_warnings = load_robust_sets_from_rows(rows, used)
        if allowed_groups:
            candidate_sets = [
                strategy for strategy in candidate_sets
                if self._portfolio_group_key(str(getattr(strategy, "symbol", ""))) in allowed_groups
            ]
        return candidate_sets, load_warnings

    def _completion_used_sets(
        self, inputs: dict[str, object], is_monthly: bool, portfolio_type, portfolio_id: int
    ) -> list:
        """Sets ya comprometidos en otros portafolios de la misma variante."""
        if is_monthly or not bool(inputs.get("exclude_used_sets", True)):
            return []
        return self._used_set_paths_all_accounts(
            portfolio_type,
            exclude_portfolio_id=portfolio_id,
        )

    @staticmethod
    def _completion_margin_balance(inputs: dict[str, object]) -> bool:
        """Si la variante guardada pide validar margen con el broker."""
        return portfolio_validates_margin(inputs)

    def _completion_optimize(
        self, inputs: dict[str, object], portfolio_type, raw_sets: list,
        required_set_ids: list[str], required_initial_allocations: dict[str, int],
        existing_curves, target_strategies: int,
    ):
        """Optimiza conservando las estrategias actuales y sus unidades."""
        use_correlation = inputs.get("use_correlation", True)
        validate_margin = self._completion_margin_balance(inputs)
        return optimize_portfolio(
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
            max_pair_corr=inputs.get("max_pair_corr") if use_correlation else None,  # type: ignore[arg-type]
            max_downside_corr=inputs.get("max_downside_corr") if use_correlation else None,  # type: ignore[arg-type]
            max_dd_overlap=inputs.get("max_dd_overlap") if use_correlation else None,  # type: ignore[arg-type]
            existing_portfolio_curves=existing_curves,
            max_portfolio_corr=inputs.get("max_portfolio_corr") if use_correlation else None,  # type: ignore[arg-type]
            required_set_ids=required_set_ids,
            minimum_active_strategies=target_strategies,
            maximum_active_strategies=target_strategies,
            required_initial_allocations=required_initial_allocations,
            preserve_required_allocations=True,
            dd_reserve_pct=float(inputs.get("dd_reserve_pct") or 0),
            search_restarts=int(inputs.get("search_restarts") or 0),
            margin_balance=float(inputs["capital"]) if validate_margin else None,
            max_margin_pct=float(inputs.get("max_margin_pct") or 100.0) if validate_margin else None,
            margin_profile=str(inputs.get("margin_profile") or "roboforex"),
            stock_leverage=20.0,
            default_leverage=500.0,
            stock_contract_size=100.0,
            default_contract_size=1.0,
            max_daily_dd=inputs.get("max_daily_dd"),  # type: ignore[arg-type]
            enforce_point_dd=bool(inputs.get("enforce_point_dd", True)),
            daily_dd_full_history=bool(inputs.get("daily_dd_full_history", False)),
            use_deep_refinement=bool(inputs.get("deep_optimization")),
        )

    @staticmethod
    def _completion_seasonal_coverage(result, raw_sets: list) -> dict:
        """Cobertura mensual de cada estrategia asignada en el resultado."""
        return portfolio_seasonal_coverage(
            result, {strategy.set_id: strategy for strategy in raw_sets}
        )

    def _completion_strict_validation(self, result, full_sets: list, inputs: dict[str, object]):
        """Pasa la propuesta por la auditoria mensual estricta si se pidio."""
        validated = self._filter_strict_monthly_valid_proposals(
            full_sets,
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
        return validated[0]["result"]

    @staticmethod
    def _completion_raw_sets(candidate_sets: list, required_sets: list) -> list:
        """Universo de la reparacion: candidatas mas los miembros actuales.

        Existing members are the fixed base of a repair. They may now be
        locked by a newer portfolio or have a changed pipeline status;
        neither condition is allowed to evict them implicitly. Only the
        replacement candidates pass through today's eligibility gates.
        """
        raw_by_id = {strategy.set_id: strategy for strategy in candidate_sets}
        raw_by_id.update({strategy.set_id: strategy for strategy in required_sets})
        return list(raw_by_id.values())

    @staticmethod
    def _completion_saved_units(members: list[dict[str, object]], required_sets: list) -> dict[str, int]:
        """Unidades guardadas de cada estrategia que se conserva."""
        saved_units = {
            str(member.get("set_path") or member.get("set_id") or ""): int(member.get("units") or 0)
            for member in members
        }
        return {strategy.set_id: saved_units[strategy.set_id] for strategy in required_sets}
