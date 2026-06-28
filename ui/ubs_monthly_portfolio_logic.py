from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from types import MethodType

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    filter_rows_grid_off,
    filter_rows_by_recent_positive_months,
    load_robust_sets_from_rows,
    slice_strategy_sets_to_month,
    summarize_robust_rows,
    validate_strict_monthly_portfolio,
)
from ui.ubs_portfolio_logic import (
    BASE_DIR,
    DEFAULT_PORTFOLIO_FORM,
    PORTFOLIO_TYPE_DISPLAY,
    UBSPortfolioLogicMixin,
)


MONTH_LABELS = (
    "01 - Enero", "02 - Febrero", "03 - Marzo", "04 - Abril",
    "05 - Mayo", "06 - Junio", "07 - Julio", "08 - Agosto",
    "09 - Septiembre", "10 - Octubre", "11 - Noviembre", "12 - Diciembre",
)


class _MonthlyPortfolioLogicAdapter:
    """Run shared portfolio UI helpers against the monthly widget namespace."""

    def __init__(self, app: object) -> None:
        object.__setattr__(self, "_app", app)

    def __getattr__(self, name: str):
        app = object.__getattribute__(self, "_app")
        if "ubs_portfolio" in name:
            monthly_name = name.replace("ubs_portfolio", "ubs_monthly_portfolio")
            try:
                return getattr(app, monthly_name)
            except AttributeError:
                shared = getattr(UBSPortfolioLogicMixin, name, None)
                if callable(shared):
                    return MethodType(shared, self)
        return getattr(app, name)

    def __setattr__(self, name: str, value: object) -> None:
        app = object.__getattribute__(self, "_app")
        if "ubs_portfolio" in name:
            name = name.replace("ubs_portfolio", "ubs_monthly_portfolio")
        setattr(app, name, value)

    def _set_portfolio_metrics_from_result(self, result: PortfolioResult) -> None:
        UBSPortfolioLogicMixin._set_portfolio_metrics_from_result(self, result)


class UBSMonthlyPortfolioLogicMixin:
    def _monthly_portfolio_adapter(self) -> _MonthlyPortfolioLogicAdapter:
        return _MonthlyPortfolioLogicAdapter(self)

    def _monthly_roboforex_margin_enabled(self) -> bool:
        broker_getter = getattr(self, "_ubs_broker", None)
        broker = broker_getter() if callable(broker_getter) else "ROBOFOREX"
        return str(broker).strip().upper() == "ROBOFOREX" and bool(
            self.ubs_monthly_portfolio_validate_roboforex_margin.get()
        )

    def _read_ubs_monthly_portfolio_inputs(self) -> dict[str, object]:
        adapter = self._monthly_portfolio_adapter()
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
        inputs["validate_roboforex_margin"] = self._monthly_roboforex_margin_enabled()
        inputs["max_margin_pct"] = UBSPortfolioLogicMixin._parse_float_setting(
            adapter,
            self.ubs_monthly_portfolio_max_margin_pct.get(),
            "Max margen",
        )
        if float(inputs["max_margin_pct"]) <= 0:
            raise ValueError("Max margen debe ser mayor que 0.")
        return inputs

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
                "strict_yearly_month_validation": bool(inputs.get("strict_yearly_month_validation")),
                "deep_optimization": bool(inputs.get("deep_optimization")),
                "exclude_monthly_used": bool(inputs.get("exclude_monthly_used")),
                "corr_with_monthly_portfolios": bool(inputs.get("corr_with_monthly_portfolios")),
                "validate_roboforex_margin": bool(inputs.get("validate_roboforex_margin")),
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

    def _set_ubs_monthly_portfolio_running(self, running: bool) -> None:
        UBSPortfolioLogicMixin._set_ubs_portfolio_running(
            self._monthly_portfolio_adapter(),
            running,
        )

    def _set_ubs_monthly_portfolio_save_enabled(self, enabled: bool) -> None:
        UBSPortfolioLogicMixin._set_ubs_portfolio_save_enabled(
            self._monthly_portfolio_adapter(),
            enabled,
        )

    def _clear_ubs_monthly_portfolio_result_tables(self) -> None:
        UBSPortfolioLogicMixin._clear_ubs_portfolio_result_tables(
            self._monthly_portfolio_adapter()
        )

    def _populate_ubs_monthly_portfolio_result(self, result: PortfolioResult) -> None:
        UBSPortfolioLogicMixin._populate_ubs_portfolio_result(
            self._monthly_portfolio_adapter(),
            result,
        )

    def _reset_ubs_monthly_portfolio_form(self) -> None:
        self.ubs_monthly_portfolio_capital.set(DEFAULT_PORTFOLIO_FORM["capital"])
        self.ubs_monthly_portfolio_valley_pct.set(DEFAULT_PORTFOLIO_FORM["valley_dd_pct"])
        self.ubs_monthly_portfolio_point_pct.set(DEFAULT_PORTFOLIO_FORM["point_dd_pct"])
        self.ubs_monthly_portfolio_type.set(DEFAULT_PORTFOLIO_FORM["portfolio_type"])
        self.ubs_monthly_portfolio_top_k.set(DEFAULT_PORTFOLIO_FORM["top_k_per_symbol"])
        self.ubs_monthly_portfolio_max_candidates.set(DEFAULT_PORTFOLIO_FORM["max_total_candidates"])
        self.ubs_monthly_portfolio_min_trades.set(15)
        self.ubs_monthly_portfolio_max_units_per_set.set(DEFAULT_PORTFOLIO_FORM["max_units_per_set"])
        self.ubs_monthly_portfolio_max_total_units.set(DEFAULT_PORTFOLIO_FORM["max_total_units"])
        self.ubs_monthly_portfolio_max_units_per_symbol.set(DEFAULT_PORTFOLIO_FORM["max_units_per_symbol"])
        self.ubs_monthly_portfolio_max_sets_per_symbol.set(DEFAULT_PORTFOLIO_FORM["max_sets_per_symbol"])
        self.ubs_monthly_portfolio_run_local_search.set(DEFAULT_PORTFOLIO_FORM["run_local_search"])
        self.ubs_monthly_portfolio_use_correlation.set(DEFAULT_PORTFOLIO_FORM["use_correlation"])
        self.ubs_monthly_portfolio_require_3_positive_months_6m.set(
            DEFAULT_PORTFOLIO_FORM["require_3_positive_months_6m"]
        )
        self.ubs_monthly_portfolio_grid_off.set(False)
        self.ubs_monthly_portfolio_dd_reserve_pct.set(DEFAULT_PORTFOLIO_FORM["dd_reserve_pct"])
        self.ubs_monthly_portfolio_search_restarts.set(DEFAULT_PORTFOLIO_FORM["search_restarts"])
        self.ubs_monthly_portfolio_max_pair_corr.set(DEFAULT_PORTFOLIO_FORM["max_pair_corr"])
        self.ubs_monthly_portfolio_max_downside_corr.set(DEFAULT_PORTFOLIO_FORM["max_downside_corr"])
        self.ubs_monthly_portfolio_max_dd_overlap.set(DEFAULT_PORTFOLIO_FORM["max_dd_overlap"])
        self.ubs_monthly_portfolio_max_portfolio_corr.set(DEFAULT_PORTFOLIO_FORM["max_portfolio_corr"])
        self.ubs_monthly_portfolio_target_month.set(MONTH_LABELS[0])
        self.ubs_monthly_portfolio_strict_yearly_month_validation.set(False)
        self.ubs_monthly_portfolio_deep_optimization.set(False)
        self.ubs_monthly_portfolio_exclude_monthly_used.set(False)
        self.ubs_monthly_portfolio_corr_with_monthly_portfolios.set(False)
        self.ubs_monthly_portfolio_validate_roboforex_margin.set(True)
        self.ubs_monthly_portfolio_max_margin_pct.set("100")
        self.ubs_monthly_portfolio_pending_result = None
        self.ubs_monthly_portfolio_pending_inputs = None
        self._set_ubs_monthly_portfolio_save_enabled(False)
        self._clear_ubs_monthly_portfolio_result_tables()
        self.ubs_monthly_portfolio_status.set("Formulario mensual restaurado.")

    def _run_ubs_monthly_portfolio_build(self) -> None:
        if (
            getattr(self, "ubs_monthly_portfolio_running", False)
            or getattr(self, "ubs_portfolio_running", False)
        ):
            messagebox.showwarning("Portafolio mensual", "Ya hay un calculo mensual en marcha.")
            return
        try:
            inputs = self._read_ubs_monthly_portfolio_inputs()
        except ValueError as exc:
            messagebox.showerror("Entrada invalida", str(exc))
            return
        log_path = self._start_ubs_monthly_generation_log(inputs)
        inputs["generation_log_path"] = str(log_path)
        if hasattr(self, "_write_ui_settings"):
            try:
                self._write_ui_settings()
            except Exception:
                pass
        self.ubs_monthly_portfolio_pending_result = None
        self.ubs_monthly_portfolio_pending_inputs = None
        self._set_ubs_monthly_portfolio_save_enabled(False)
        self._clear_ubs_monthly_portfolio_result_tables()
        self._set_ubs_monthly_portfolio_running(True)
        self.ubs_monthly_portfolio_status.set(
            f"Analizando {inputs['target_month_label']} en todo el historico disponible..."
        )
        threading.Thread(
            target=self._ubs_monthly_portfolio_worker,
            args=(inputs, log_path),
            daemon=True,
        ).start()

    def _ubs_monthly_portfolio_worker(self, inputs: dict[str, object], log_path: Path | None = None) -> None:
        try:
            portfolio_type = PortfolioType(str(inputs["portfolio_type"]))
            rows = self._final_tick_passed_candidates_all_accounts(include_quarantined=True)
            self._append_ubs_monthly_generation_log(
                log_path,
                "CANDIDATOS_FINAL_TICK_6M",
                {"rows": len(rows)},
            )
            if not rows:
                raise ValueError("No hay candidatos con Final Tick 6M accepted en las memorias broker/cuenta.")
            month_filter_warnings: list[str] = []
            if bool(inputs.get("require_3_positive_months_6m")):
                rows, month_filter_warnings = filter_rows_by_recent_positive_months(
                    rows,
                    min_positive_months=3,
                    window_months=6,
                )
                self._append_ubs_monthly_generation_log(
                    log_path,
                    "FILTRO_3_6_MESES",
                    {"rows_after": len(rows), "warnings": month_filter_warnings},
                )
            grid_warnings: list[str] = []
            if bool(inputs.get("grid_off")):
                rows, grid_warnings = filter_rows_grid_off(rows)
                self._append_ubs_monthly_generation_log(
                    log_path,
                    "FILTRO_GRID_OFF",
                    {"rows_after": len(rows), "warnings": grid_warnings},
                )
                if not rows:
                    raise ValueError("No quedan candidatos tras aplicar Grid OFF.")
            used_monthly_paths: list[str] = []
            if bool(inputs.get("exclude_monthly_used")):
                used_monthly_paths = self._used_monthly_set_paths_all_accounts()
                self._append_ubs_monthly_generation_log(
                    log_path,
                    "FILTRO_USADOS_MENSUAL",
                    {"used_monthly_sets": len(used_monthly_paths)},
                )
            availability = summarize_robust_rows(rows, used_monthly_paths)
            raw_sets, load_warnings = load_robust_sets_from_rows(
                rows,
                used_monthly_paths,
                progress=lambda msg: self.after(0, self.ubs_monthly_portfolio_status.set, msg),
            )
            self._append_ubs_monthly_generation_log(
                log_path,
                "SETS_CARGADOS",
                {
                    "raw_sets": len(raw_sets),
                    "symbols": len({getattr(item, "symbol", "") for item in raw_sets}),
                    "warnings": load_warnings,
                },
            )
            monthly_sets, slice_warnings = slice_strategy_sets_to_month(
                raw_sets,
                int(inputs["target_month"]),
            )
            self._append_ubs_monthly_generation_log(
                log_path,
                "SETS_MES_OBJETIVO",
                {
                    "monthly_sets": len(monthly_sets),
                    "target_month": int(inputs["target_month"]),
                    "warnings": slice_warnings,
                },
            )
            if not monthly_sets:
                raise ValueError("Ningun candidato tiene trades fechados para el mes objetivo.")
            saved_monthly_curves = self._saved_monthly_portfolio_curves_all_accounts()
            existing_curves = (
                saved_monthly_curves
                if bool(inputs.get("corr_with_monthly_portfolios"))
                else []
            )
            strict_retry_warnings: list[str] = []
            engine_inputs = dict(inputs)
            engine_inputs["use_deep_candidate_engine"] = (
                bool(inputs.get("strict_yearly_month_validation"))
                and bool(inputs.get("deep_optimization"))
            )
            self._append_ubs_monthly_generation_log(
                log_path,
                "MOTOR_SELECCIONADO",
                {
                    "strict": bool(inputs.get("strict_yearly_month_validation")),
                    "deep_refinement": bool(engine_inputs.get("use_deep_candidate_engine")),
                    "exclude_monthly_used": bool(inputs.get("exclude_monthly_used")),
                    "corr_with_monthly_portfolios": bool(inputs.get("corr_with_monthly_portfolios")),
                    "saved_monthly_curves_available": len(saved_monthly_curves),
                    "existing_monthly_curves": len(existing_curves),
                },
            )
            try:
                proposals = self._optimize_ubs_portfolio_proposals(
                    monthly_sets,
                    engine_inputs,
                    portfolio_type,
                    existing_curves,
                    strict_full_sets=raw_sets,
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
                proposals = self._filter_strict_monthly_valid_proposals(raw_sets, proposals, inputs)
            except ValueError as exc:
                self._append_ubs_monthly_generation_log(
                    log_path,
                    "MOTOR_PRINCIPAL_FALLO",
                    {"error": str(exc)},
                )
                if not bool(inputs.get("strict_yearly_month_validation")):
                    raise
                strict_raw_sets, strict_retry_warnings = self._strict_monthly_candidate_pool(
                    raw_sets,
                    inputs,
                )
                self._append_ubs_monthly_generation_log(
                    log_path,
                    "REINTENTO_POOL_ESTRICTO",
                    {"strict_raw_sets": len(strict_raw_sets), "warnings": strict_retry_warnings},
                )
                if not strict_raw_sets:
                    raise
                strict_monthly_sets, strict_slice_warnings = slice_strategy_sets_to_month(
                    strict_raw_sets,
                    int(inputs["target_month"]),
                )
                strict_retry_warnings.extend(strict_slice_warnings)
                if not strict_monthly_sets:
                    raise
                proposals = self._optimize_ubs_portfolio_proposals(
                    strict_monthly_sets,
                    engine_inputs,
                    portfolio_type,
                    existing_curves,
                    strict_full_sets=raw_sets,
                    progress=lambda label, index: self.after(
                        0,
                        self.ubs_monthly_portfolio_status.set,
                        f"Reintentando estricto {index}/3 ({label})...",
                    ),
                )
                proposals = self._filter_strict_monthly_valid_proposals(
                    raw_sets,
                    proposals,
                    inputs,
                )
            for proposal in proposals:
                proposal["result"].warnings[:0] = (
                    month_filter_warnings
                    + grid_warnings
                    + load_warnings
                    + slice_warnings
                    + strict_retry_warnings
                )
            self._append_ubs_monthly_generation_log(
                log_path,
                "PROPUESTAS_OK",
                [
                    {
                        "key": proposal.get("key"),
                        "label": proposal.get("label"),
                        "net": getattr(proposal.get("result"), "total_net_profit", None),
                        "units": getattr(proposal.get("result"), "total_units", None),
                        "strategies": getattr(proposal.get("result"), "active_strategies", None),
                        "strict_passed": bool(
                            (getattr(proposal.get("result"), "seasonal_validation", {}) or {}).get("passed")
                        ),
                    }
                    for proposal in proposals
                ],
            )
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
            "availability": availability,
            "proposals": proposals,
            "log_path": str(log_path) if log_path else "",
        })

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

    def _ubs_monthly_portfolio_finished(self, info: dict[str, object]) -> None:
        self._set_ubs_monthly_portfolio_running(False)
        if not info.get("ok"):
            message = str(info.get("error") or "Error desconocido")
            self.ubs_monthly_portfolio_pending_result = None
            self.ubs_monthly_portfolio_pending_inputs = None
            if not self._restore_selected_ubs_monthly_portfolio_after_failed_generate():
                self._clear_ubs_monthly_portfolio_result_tables()
            self.ubs_monthly_portfolio_status.set(message)
            return
        proposals = info.get("proposals") or []
        self.ubs_monthly_portfolio_proposals_availability = info.get("availability")
        self._show_ubs_portfolio_proposals_preview(0, proposals, [], mode="generate_monthly")
        log_path = str(info.get("log_path") or "").strip()
        suffix = f" Log: {log_path}" if log_path else ""
        self.ubs_monthly_portfolio_status.set(
            f"Selecciona una propuesta mensual para continuar.{suffix}"
        )

    def _restore_selected_ubs_monthly_portfolio_after_failed_generate(self) -> bool:
        tree = getattr(self, "ubs_monthly_portfolio_saved_tree", None)
        if tree is None:
            return False
        selection = tree.selection()
        if not selection:
            return False
        try:
            portfolio_id = int(selection[0])
        except (TypeError, ValueError):
            return False
        try:
            self._populate_ubs_monthly_portfolio_saved(portfolio_id)
        except Exception:
            return False
        return True

    def _accept_generated_ubs_monthly_portfolio_proposal(self, proposal: dict[str, object]) -> None:
        result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
        inputs: dict[str, object] = proposal["inputs"]  # type: ignore[assignment]
        self.ubs_monthly_portfolio_pending_result = result
        self.ubs_monthly_portfolio_pending_inputs = inputs
        self._populate_ubs_monthly_portfolio_result(result)
        self._populate_ubs_monthly_portfolio_availability(
            getattr(self, "ubs_monthly_portfolio_proposals_availability", None)
        )
        self._set_ubs_monthly_portfolio_save_enabled(True)
        self.ubs_monthly_portfolio_status.set(
            f"{inputs['target_month_label']}: {result.active_strategies} estrategias, "
            f"{result.total_units} unidades, DD valle {result.valley_usage_pct:.1f}%."
        )

    def _save_pending_ubs_monthly_portfolio(self) -> None:
        result = getattr(self, "ubs_monthly_portfolio_pending_result", None)
        inputs = getattr(self, "ubs_monthly_portfolio_pending_inputs", None)
        if result is None or inputs is None:
            messagebox.showinfo("Guardar portafolio mensual", "Genera una propuesta valida antes de guardarla.")
            return
        if not result.allocations:
            messagebox.showwarning("Guardar portafolio mensual", "El portafolio mensual no tiene asignaciones.")
            return
        if bool(inputs.get("strict_yearly_month_validation")) and not bool(
            (getattr(result, "seasonal_validation", {}) or {}).get("passed")
        ):
            messagebox.showerror(
                "Guardar portafolio mensual",
                "Bloqueado: la propuesta estricta no tiene validacion 5A/DD mensual pasada.",
            )
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio_id = self._insert_portfolio(conn, inputs, result)
        except Exception as exc:
            messagebox.showerror("Guardar portafolio mensual", f"No se pudo guardar el portafolio mensual:\n{exc}")
            return
        finally:
            conn.close()
        self.ubs_monthly_portfolio_pending_result = None
        self.ubs_monthly_portfolio_pending_inputs = None
        self._set_ubs_monthly_portfolio_save_enabled(False)
        self._refresh_ubs_monthly_portfolios(select_id=portfolio_id)
        self.ubs_monthly_portfolio_status.set(f"Portafolio mensual #{portfolio_id} guardado.")

    def _refresh_ubs_monthly_portfolio_availability(self) -> None:
        if not hasattr(self, "ubs_monthly_portfolio_availability_tree"):
            return
        try:
            rows = self._final_tick_passed_candidates_all_accounts(include_quarantined=True)
            if bool(self.ubs_monthly_portfolio_grid_off.get()):
                rows, _warnings = filter_rows_grid_off(rows)
            used_paths: list[str] = []
            if bool(self.ubs_monthly_portfolio_exclude_monthly_used.get()):
                used_paths = self._used_monthly_set_paths_all_accounts()
            availability = summarize_robust_rows(rows, used_paths)
        except Exception as exc:
            self.ubs_monthly_portfolio_availability.set(f"Disponibilidad: error ({exc})")
            return
        self._populate_ubs_monthly_portfolio_availability(availability)

    def _populate_ubs_monthly_portfolio_availability(self, availability) -> None:
        tree = self.ubs_monthly_portfolio_availability_tree
        for item in tree.get_children(""):
            tree.delete(item)
        if availability is None:
            self.ubs_monthly_portfolio_availability.set("Disponibilidad: sin datos")
            return
        self.ubs_monthly_portfolio_availability.set(
            f"Final Tick 6M accepted: {availability.robust_accepted} | "
            + (
                "Excluyendo usados mensuales | "
                if bool(self.ubs_monthly_portfolio_exclude_monthly_used.get())
                else "Sin exclusion por cuarentena ni por uso | "
            )
            + f"Simbolos: {availability.symbols_available}"
            + (" | Grid OFF activo" if bool(self.ubs_monthly_portfolio_grid_off.get()) else "")
        )
        for symbol, count in availability.by_symbol.items():
            tree.insert("", "end", values=(symbol, count))

    def _refresh_ubs_monthly_portfolios(self, select_id: int | None = None) -> None:
        if not hasattr(self, "ubs_monthly_portfolio_saved_tree"):
            return
        self._refresh_ubs_monthly_portfolio_availability()
        UBSPortfolioLogicMixin._refresh_ubs_portfolio_quarantine(
            self._monthly_portfolio_adapter()
        )
        tree = self.ubs_monthly_portfolio_saved_tree
        for item in tree.get_children(""):
            tree.delete(item)
        conn = self._ubs_portfolio_conn()
        try:
            portfolios = self._list_portfolios(conn, portfolio_scope="monthly")
        finally:
            conn.close()
        target_item = None
        for row in portfolios:
            type_key = str(row["portfolio_type"] or row["type"] or "")
            month = int(row["target_month"] or 0)
            values = (
                row["id"],
                row["created_at"],
                f"Mes {month:02d} / {PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key)}",
                f"{float(row['capital'] or row['account_capital'] or 0):,.0f}",
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
            self._populate_ubs_monthly_portfolio_saved(int(target_item))
        else:
            self._clear_ubs_monthly_portfolio_result_tables()
            self.ubs_monthly_portfolio_status.set("Sin portafolios mensuales guardados.")

    def _populate_ubs_monthly_portfolio_saved(self, portfolio_id: int) -> None:
        UBSPortfolioLogicMixin._populate_ubs_portfolio_saved(
            self._monthly_portfolio_adapter(),
            portfolio_id,
        )

    def _on_ubs_monthly_portfolio_select(self, event=None) -> None:
        UBSPortfolioLogicMixin._on_ubs_portfolio_select(
            self._monthly_portfolio_adapter(),
            event,
        )

    def _open_selected_ubs_monthly_portfolio_detail(self, event=None) -> None:
        UBSPortfolioLogicMixin._open_selected_ubs_portfolio_detail(
            self._monthly_portfolio_adapter(),
            event,
        )

    def _open_selected_ubs_monthly_portfolio_member(self) -> None:
        UBSPortfolioLogicMixin._open_selected_ubs_portfolio_member(
            self._monthly_portfolio_adapter()
        )

    def _delete_selected_ubs_monthly_portfolio(self) -> None:
        UBSPortfolioLogicMixin._delete_selected_ubs_portfolio(
            self._monthly_portfolio_adapter()
        )

    def _export_ubs_monthly_portfolio_sets(self) -> None:
        UBSPortfolioLogicMixin._export_ubs_portfolio_sets(
            self._monthly_portfolio_adapter()
        )

    def _release_selected_ubs_monthly_portfolio_quarantine(self) -> None:
        UBSPortfolioLogicMixin._release_selected_ubs_portfolio_quarantine(
            self._monthly_portfolio_adapter()
        )
