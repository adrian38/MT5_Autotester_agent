"""Lectura del formulario, bitacora y worker de generacion mensual."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    slice_strategy_sets_to_month,
    validate_strict_monthly_portfolio,
)
from ui.ubs_portfolio_logic import BASE_DIR, UBSPortfolioLogicMixin


MONTH_LABELS = (
    "01 - Enero", "02 - Febrero", "03 - Marzo", "04 - Abril",
    "05 - Mayo", "06 - Junio", "07 - Julio", "08 - Agosto",
    "09 - Septiembre", "10 - Octubre", "11 - Noviembre", "12 - Diciembre",
)


@dataclass
class _MonthlyBuild:
    """Estado que viaja entre los pasos de la generacion del portafolio mensual."""

    inputs: dict[str, object]
    log_path: Path | None
    portfolio_type: PortfolioType | None = None
    allowed_groups: set[str] = field(default_factory=set)
    rows: list = field(default_factory=list)
    raw_sets: list = field(default_factory=list)
    monthly_sets: list = field(default_factory=list)
    existing_curves: list = field(default_factory=list)
    engine_inputs: dict[str, object] = field(default_factory=dict)
    proposals: list = field(default_factory=list)
    availability: object = None
    month_filter_warnings: list[str] = field(default_factory=list)
    grid_warnings: list[str] = field(default_factory=list)
    load_warnings: list[str] = field(default_factory=list)
    slice_warnings: list[str] = field(default_factory=list)
    strict_retry_warnings: list[str] = field(default_factory=list)


class UBSMonthlyPortfolioWorkerMixin:
    """Formulario, bitacora y construccion del portafolio mensual en segundo plano."""

    def _read_ubs_monthly_portfolio_inputs(self) -> dict[str, object]:
        adapter = self._monthly_portfolio_adapter()
        # En mensual el DD puntual no es una restriccion de construccion.
        # Lo igualamos al DD valle para mantener una referencia coherente en
        # los datos persistidos.
        self.ubs_monthly_portfolio_point_pct.set(self.ubs_monthly_portfolio_valley_pct.get())
        inputs = UBSPortfolioLogicMixin._read_ubs_portfolio_inputs(adapter)
        month_text = str(self.ubs_monthly_portfolio_target_month.get()).strip()
        try:
            target_month = int(month_text.split("-", 1)[0].strip())
        except ValueError as exc:
            raise ValueError("Selecciona un mes objetivo valido.") from exc
        if not 1 <= target_month <= 12:
            raise ValueError("Selecciona un mes objetivo valido.")
        inputs["portfolio_scope"] = "monthly"
        inputs["target_month"] = target_month
        inputs["target_month_label"] = MONTH_LABELS[target_month - 1]
        inputs["enforce_point_dd"] = False
        inputs["max_daily_dd"] = UBSPortfolioLogicMixin._parse_float_setting(
            adapter,
            self.ubs_monthly_portfolio_max_daily_dd.get(),
            "DD diario max",
        )
        if float(inputs["max_daily_dd"]) <= 0:
            raise ValueError("DD diario max debe ser mayor que 0.")
        inputs["daily_dd_full_history"] = bool(self.ubs_monthly_portfolio_daily_dd_full_history.get())
        allowed_groups = self._ubs_monthly_allowed_asset_groups()
        if not allowed_groups:
            raise ValueError("Selecciona al menos un grupo permitido: Forex, Indices/Energias, Metales o Stocks.")
        inputs["allowed_asset_groups"] = sorted(allowed_groups)
        inputs["strict_yearly_month_validation"] = bool(
            self.ubs_monthly_portfolio_strict_yearly_month_validation.get()
        )
        inputs["deep_optimization"] = bool(self.ubs_monthly_portfolio_deep_optimization.get())
        inputs["exclude_monthly_used"] = bool(self.ubs_monthly_portfolio_exclude_monthly_used.get())
        inputs["corr_with_monthly_portfolios"] = bool(
            self.ubs_monthly_portfolio_corr_with_monthly_portfolios.get()
        )
        if bool(inputs["corr_with_monthly_portfolios"]) and inputs.get("max_portfolio_corr") is None:
            inputs["max_portfolio_corr"] = UBSPortfolioLogicMixin._parse_optional_float_setting(
                adapter,
                self.ubs_monthly_portfolio_max_portfolio_corr.get(),
                "Max corr portafolios",
            )
            if inputs["max_portfolio_corr"] is None:
                raise ValueError("Max corr portafolios es obligatorio si activas No corr mensual.")
            if not (0 <= float(inputs["max_portfolio_corr"]) <= 1):
                raise ValueError("Max corr portafolios debe estar entre 0 y 1.")
        self._apply_monthly_margin_inputs(inputs, adapter)
        return inputs

    def _apply_monthly_margin_inputs(self, inputs: dict[str, object], adapter) -> None:
        margin_profile = self._monthly_margin_profile()
        inputs["margin_profile"] = margin_profile
        inputs["validate_margin"] = True
        inputs["validate_roboforex_margin"] = margin_profile != "ttp"
        inputs["validate_ttp_margin"] = margin_profile == "ttp"
        inputs["max_margin_pct"] = UBSPortfolioLogicMixin._parse_float_setting(
            adapter,
            self.ubs_monthly_portfolio_max_margin_pct.get(),
            "Max margen",
        )
        if float(inputs["max_margin_pct"]) <= 0:
            raise ValueError("Max margen debe ser mayor que 0.")

    def _start_ubs_monthly_generation_log(self, inputs: dict[str, object]) -> Path:
        logs_dir = BASE_DIR / "logs" / "ubs_monthly_portfolio"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_month = int(inputs.get("target_month") or 0)
        path = logs_dir / f"monthly_{stamp}_mes_{target_month:02d}.log"
        self._append_ubs_monthly_generation_log(
            path,
            "INICIO_GENERACION_MENSUAL",
            {
                "inputs": self._monthly_log_safe(inputs),
                "allowed_asset_groups": self._monthly_log_safe(inputs.get("allowed_asset_groups")),
                "strict_yearly_month_validation": bool(inputs.get("strict_yearly_month_validation")),
                "deep_optimization": bool(inputs.get("deep_optimization")),
                "exclude_monthly_used": bool(inputs.get("exclude_monthly_used")),
                "corr_with_monthly_portfolios": bool(inputs.get("corr_with_monthly_portfolios")),
                "enforce_point_dd": bool(inputs.get("enforce_point_dd", True)),
                "margin_profile": str(inputs.get("margin_profile") or "roboforex"),
                "max_daily_dd": float(inputs.get("max_daily_dd") or 0.0),
                "daily_dd_full_history": bool(inputs.get("daily_dd_full_history")),
                "validate_margin": bool(inputs.get("validate_margin")),
                "validate_roboforex_margin": bool(inputs.get("validate_roboforex_margin")),
                "validate_ttp_margin": bool(inputs.get("validate_ttp_margin")),
            },
        )
        return path

    def _append_ubs_monthly_generation_log(
        self,
        path: Path | None,
        event: str,
        payload: object | None = None,
    ) -> None:
        if path is None:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
        }
        if payload is not None:
            record["payload"] = self._monthly_log_safe(payload)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _monthly_log_safe(self, value: object) -> object:
        if isinstance(value, dict):
            return {str(key): self._monthly_log_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._monthly_log_safe(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _ubs_monthly_portfolio_worker(self, inputs: dict[str, object], log_path: Path | None = None) -> None:
        state = _MonthlyBuild(inputs=inputs, log_path=log_path)
        try:
            self._monthly_filter_rows(state)
            self._monthly_load_sets(state)
            self._monthly_prepare_engine(state)
            self._monthly_optimize_proposals(state)
            self._monthly_log_proposals(state)
        except Exception as exc:
            self._append_ubs_monthly_generation_log(
                log_path,
                "ERROR_GENERACION",
                {"error": str(exc)},
            )
            self.after(0, self._ubs_monthly_portfolio_finished, {
                "ok": False,
                "error": f"Error generando portafolio mensual: {exc} | log {log_path}" if log_path else f"Error generando portafolio mensual: {exc}",
            })
            return
        self.after(0, self._ubs_monthly_portfolio_finished, {
            "ok": True,
            "availability": state.availability,
            "proposals": state.proposals,
            "log_path": str(log_path) if log_path else "",
        })

    def _monthly_filter_rows(self, state: _MonthlyBuild) -> None:
        """Candidatos Final Tick 6M filtrados por meses positivos, grid y grupos."""
        inputs = state.inputs
        log_path = state.log_path
        state.portfolio_type = PortfolioType(str(inputs["portfolio_type"]))
        rows = self._final_tick_passed_candidates_all_accounts(include_quarantined=True)
        self._append_ubs_monthly_generation_log(
            log_path,
            "CANDIDATOS_FINAL_TICK_6M",
            {"rows": len(rows)},
        )
        if not rows:
            raise ValueError("No hay candidatos con Final Tick 6M accepted en las memorias broker/cuenta.")
        if bool(inputs.get("require_3_positive_months_6m")):
            rows, state.month_filter_warnings = filter_rows_by_recent_positive_months(
                rows,
                min_positive_months=3,
                window_months=6,
            )
            self._append_ubs_monthly_generation_log(
                log_path,
                "FILTRO_3_6_MESES",
                {"rows_after": len(rows), "warnings": state.month_filter_warnings},
            )
        if bool(inputs.get("grid_off")):
            rows, state.grid_warnings = filter_rows_grid_off(rows)
            self._append_ubs_monthly_generation_log(
                log_path,
                "FILTRO_GRID_OFF",
                {"rows_after": len(rows), "warnings": state.grid_warnings},
            )
            if not rows:
                raise ValueError("No quedan candidatos tras aplicar Grid OFF.")
        state.allowed_groups = {str(group) for group in (inputs.get("allowed_asset_groups") or [])}
        rows, row_group_counts = self._filter_monthly_rows_by_allowed_groups(rows, state.allowed_groups)
        self._append_ubs_monthly_generation_log(
            log_path,
            "FILTRO_GRUPOS_ACTIVO",
            {
                "allowed_asset_groups": sorted(state.allowed_groups),
                "row_group_counts_before": row_group_counts,
                "rows_after": len(rows),
            },
        )
        if not rows:
            raise ValueError("No quedan candidatos tras aplicar grupos permitidos.")
        state.rows = rows

    def _monthly_load_sets(self, state: _MonthlyBuild) -> None:
        """Sets cargados desde las filas, filtrados por grupo y recortados al mes."""
        inputs = state.inputs
        log_path = state.log_path
        used_monthly_paths: list[str] = []
        if bool(inputs.get("exclude_monthly_used")):
            used_monthly_paths = self._used_monthly_set_paths_all_accounts()
            self._append_ubs_monthly_generation_log(
                log_path,
                "FILTRO_USADOS_MENSUAL",
                {"used_monthly_sets": len(used_monthly_paths)},
            )
        raw_sets, state.load_warnings = load_robust_sets_from_rows(
            state.rows,
            used_monthly_paths,
            progress=lambda msg: self.after(0, self.ubs_monthly_portfolio_status.set, msg),
        )
        raw_sets, set_group_counts = self._filter_monthly_sets_by_allowed_groups(raw_sets, state.allowed_groups)
        state.raw_sets = raw_sets
        state.availability = self._monthly_availability_from_sets(raw_sets)
        self._append_ubs_monthly_generation_log(
            log_path,
            "SETS_CARGADOS",
            {
                "raw_sets": len(raw_sets),
                "symbols": len({getattr(item, "symbol", "") for item in raw_sets}),
                "set_group_counts_after_load": set_group_counts,
                "warnings": state.load_warnings,
            },
        )
        if not raw_sets:
            raise ValueError("No quedan sets cargados tras aplicar grupos permitidos.")
        state.monthly_sets, state.slice_warnings = slice_strategy_sets_to_month(
            raw_sets,
            int(inputs["target_month"]),
        )
        self._append_ubs_monthly_generation_log(
            log_path,
            "SETS_MES_OBJETIVO",
            {
                "monthly_sets": len(state.monthly_sets),
                "target_month": int(inputs["target_month"]),
                "warnings": state.slice_warnings,
            },
        )
        if not state.monthly_sets:
            raise ValueError("Ningun candidato tiene trades fechados para el mes objetivo.")

    def _monthly_prepare_engine(self, state: _MonthlyBuild) -> None:
        """Curvas guardadas a correlacionar y eleccion del motor de refinamiento."""
        inputs = state.inputs
        saved_monthly_curves = self._saved_monthly_portfolio_curves_all_accounts()
        state.existing_curves = (
            saved_monthly_curves
            if bool(inputs.get("corr_with_monthly_portfolios"))
            else []
        )
        state.engine_inputs = dict(inputs)
        state.engine_inputs["use_deep_candidate_engine"] = (
            bool(inputs.get("strict_yearly_month_validation"))
            and bool(inputs.get("deep_optimization"))
        )
        self._append_ubs_monthly_generation_log(
            state.log_path,
            "MOTOR_SELECCIONADO",
            {
                "strict": bool(inputs.get("strict_yearly_month_validation")),
                "deep_refinement": bool(state.engine_inputs.get("use_deep_candidate_engine")),
                "exclude_monthly_used": bool(inputs.get("exclude_monthly_used")),
                "corr_with_monthly_portfolios": bool(inputs.get("corr_with_monthly_portfolios")),
                "saved_monthly_curves_available": len(saved_monthly_curves),
                "existing_monthly_curves": len(state.existing_curves),
            },
        )

    def _monthly_optimize_proposals(self, state: _MonthlyBuild) -> None:
        """Optimiza las tres propuestas; si el modo estricto falla, reintenta su pool."""
        inputs = state.inputs
        try:
            proposals = self._optimize_ubs_portfolio_proposals(
                state.monthly_sets,
                state.engine_inputs,
                state.portfolio_type,
                state.existing_curves,
                strict_full_sets=state.raw_sets,
                progress=lambda label, index: self.after(
                    0,
                    self.ubs_monthly_portfolio_status.set,
                    (
                        f"Calculando propuesta mensual estricta {index}/3 ({label})..."
                        if bool(inputs.get("strict_yearly_month_validation"))
                        else f"Calculando propuesta mensual {index}/3 ({label})..."
                    ),
                ),
            )
            proposals = self._filter_strict_monthly_valid_proposals(state.raw_sets, proposals, inputs)
        except ValueError as exc:
            self._append_ubs_monthly_generation_log(
                state.log_path,
                "MOTOR_PRINCIPAL_FALLO",
                {"error": str(exc)},
            )
            if not bool(inputs.get("strict_yearly_month_validation")):
                raise
            proposals = self._monthly_strict_retry(state)
        state.proposals = proposals

    def _monthly_strict_retry(self, state: _MonthlyBuild) -> list:
        """Segundo intento del modo estricto sobre su propio pool de candidatos."""
        inputs = state.inputs
        strict_raw_sets, state.strict_retry_warnings = self._strict_monthly_candidate_pool(
            state.raw_sets,
            inputs,
        )
        self._append_ubs_monthly_generation_log(
            state.log_path,
            "REINTENTO_POOL_ESTRICTO",
            {"strict_raw_sets": len(strict_raw_sets), "warnings": state.strict_retry_warnings},
        )
        if not strict_raw_sets:
            raise
        strict_monthly_sets, strict_slice_warnings = slice_strategy_sets_to_month(
            strict_raw_sets,
            int(inputs["target_month"]),
        )
        state.strict_retry_warnings.extend(strict_slice_warnings)
        if not strict_monthly_sets:
            raise
        proposals = self._optimize_ubs_portfolio_proposals(
            strict_monthly_sets,
            state.engine_inputs,
            state.portfolio_type,
            state.existing_curves,
            strict_full_sets=state.raw_sets,
            progress=lambda label, index: self.after(
                0,
                self.ubs_monthly_portfolio_status.set,
                f"Reintentando estricto {index}/3 ({label})...",
            ),
        )
        return self._filter_strict_monthly_valid_proposals(
            state.raw_sets,
            proposals,
            inputs,
        )

    def _monthly_log_proposals(self, state: _MonthlyBuild) -> None:
        """Antepone los avisos acumulados a cada propuesta y deja la bitacora final."""
        for proposal in state.proposals:
            proposal["result"].warnings[:0] = (
                state.month_filter_warnings
                + state.grid_warnings
                + state.load_warnings
                + state.slice_warnings
                + state.strict_retry_warnings
            )
        self._append_ubs_monthly_generation_log(
            state.log_path,
            "PROPUESTAS_OK",
            [
                {
                    "key": proposal.get("key"),
                    "label": proposal.get("label"),
                    "net": getattr(proposal.get("result"), "total_net_profit", None),
                    "units": getattr(proposal.get("result"), "total_units", None),
                    "strategies": getattr(proposal.get("result"), "active_strategies", None),
                    "max_daily_dd": getattr(proposal.get("result"), "max_daily_dd", None),
                    "target_daily_dd": getattr(proposal.get("result"), "target_daily_dd", None),
                    "strict_passed": bool(
                        (getattr(proposal.get("result"), "seasonal_validation", {}) or {}).get("passed")
                    ),
                }
                for proposal in state.proposals
            ],
        )

    def _merge_deep_monthly_proposals(
        self,
        base_proposals: list[dict[str, object]],
        deep_proposals: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        deep_by_key = {str(item.get("key")): item for item in deep_proposals}
        merged: list[dict[str, object]] = []
        for base in base_proposals:
            key = str(base.get("key"))
            deep = deep_by_key.get(key)
            if deep is None:
                merged.append(base)
                continue
            base_result: PortfolioResult = base["result"]  # type: ignore[assignment]
            deep_result: PortfolioResult = deep["result"]  # type: ignore[assignment]
            deep_is_better = (
                deep_result.active_strategies >= base_result.active_strategies
                and deep_result.total_net_profit > base_result.total_net_profit + 1e-9
            )
            if deep_is_better:
                deep_result.warnings.append(
                    "Optimizacion profunda aplicada: supera la solucion normal sin reducir estrategias."
                )
                merged.append(deep)
            else:
                base_result.warnings.append(
                    "Optimizacion profunda no mejoro la solucion normal; se mantuvo la normal."
                )
                merged.append(base)
        base_keys = {str(item.get("key")) for item in base_proposals}
        for deep in deep_proposals:
            if str(deep.get("key")) not in base_keys:
                merged.append(deep)
        return merged

    def _strict_monthly_candidate_pool(
        self,
        raw_sets: list,
        inputs: dict[str, object],
    ) -> tuple[list, list[str]]:
        target_month = int(inputs.get("target_month") or 0)
        if not 1 <= target_month <= 12:
            return [], []
        selected = []
        for strategy in raw_sets:
            validation = validate_strict_monthly_portfolio(
                [strategy],
                {strategy.set_id: 1},
                target_month=target_month,
                target_valley_dd=1_000_000_000.0,
                target_point_dd=1_000_000_000.0,
                lookback_years=5,
            )
            if (
                int(validation.get("best_month") or 0) == target_month
                and float(validation.get("target_month_net") or 0.0) > 0
            ):
                selected.append(strategy)
        warnings = [
            "Validacion estricta: reintento con "
            f"{len(selected)}/{len(raw_sets)} candidato(s) cuyo mejor mes individual 5A es el objetivo."
        ]
        return selected, warnings
