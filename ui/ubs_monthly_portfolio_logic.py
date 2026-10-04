from __future__ import annotations

import threading
from pathlib import Path
from tkinter import messagebox
from types import MethodType

from portfolio_manager.ubs_portfolio import (
    PortfolioAvailability,
    PortfolioResult,
    filter_rows_grid_off,
    portfolio_group_key,
    summarize_robust_rows,
)
from ui.ubs_portfolio_logic import (
    BASE_DIR,
    DEFAULT_PORTFOLIO_FORM,
    PORTFOLIO_TYPE_DISPLAY,
    UBSPortfolioLogicMixin,
)
from ui.ubs_monthly_portfolio_worker import (  # noqa: F401  MONTH_LABELS es fachada
    MONTH_LABELS,
    UBSMonthlyPortfolioWorkerMixin,
)


MONTHLY_ASSET_GROUP_FLAGS = (
    ("Forex", "allow_forex"),
    ("Metals", "allow_metals"),
    ("Indices", "allow_indices"),
    ("Energies", "allow_energies"),
    ("Crypto", "allow_crypto"),
    ("Stocks", "allow_stocks"),
    ("Bonds", "allow_bonds"),
    ("Softs", "allow_softs"),
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
                try:
                    from ui.ubs_portfolio_view import UBSPortfolioViewMixin

                    shared_view = getattr(UBSPortfolioViewMixin, name, None)
                    if callable(shared_view):
                        return MethodType(shared_view, self)
                except Exception:
                    pass
        return getattr(app, name)

    def __setattr__(self, name: str, value: object) -> None:
        app = object.__getattribute__(self, "_app")
        if "ubs_portfolio" in name:
            name = name.replace("ubs_portfolio", "ubs_monthly_portfolio")
        setattr(app, name, value)

    def _set_portfolio_metrics_from_result(self, result: PortfolioResult) -> None:
        UBSPortfolioLogicMixin._set_portfolio_metrics_from_result(self, result)


class UBSMonthlyPortfolioLogicMixin(UBSMonthlyPortfolioWorkerMixin):
    def _monthly_portfolio_adapter(self) -> _MonthlyPortfolioLogicAdapter:
        return _MonthlyPortfolioLogicAdapter(self)

    def _ubs_monthly_allowed_asset_groups(self) -> set[str]:
        groups: set[str] = set()
        for group, suffix in MONTHLY_ASSET_GROUP_FLAGS:
            var = getattr(self, f"ubs_monthly_portfolio_{suffix}", None)
            if var is not None and bool(var.get()):
                groups.add(group)
        return groups

    def _monthly_row_group(self, row: object) -> str:
        if isinstance(row, dict):
            symbol = str(row.get("target_symbol") or row.get("symbol") or "")
        else:
            getter = getattr(row, "get", None)
            if callable(getter):
                symbol = str(getter("target_symbol") or getter("symbol") or "")
            else:
                symbol = str(getattr(row, "target_symbol", "") or getattr(row, "symbol", ""))
        return portfolio_group_key(symbol)

    def _filter_monthly_rows_by_allowed_groups(
        self,
        rows: list[dict[str, object]],
        allowed_groups: set[str],
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        counts: dict[str, int] = {}
        filtered: list[dict[str, object]] = []
        for row in rows:
            group = self._monthly_row_group(row)
            counts[group] = counts.get(group, 0) + 1
            if group in allowed_groups:
                filtered.append(row)
        return filtered, counts

    def _filter_monthly_sets_by_allowed_groups(
        self,
        sets: list,
        allowed_groups: set[str],
    ) -> tuple[list, dict[str, int]]:
        counts: dict[str, int] = {}
        filtered: list = []
        for strategy in sets:
            group = portfolio_group_key(str(getattr(strategy, "symbol", "")))
            counts[group] = counts.get(group, 0) + 1
            if group in allowed_groups:
                filtered.append(strategy)
        return filtered, counts

    def _monthly_availability_from_sets(self, sets: list) -> PortfolioAvailability:
        by_symbol: dict[str, int] = {}
        for strategy in sets:
            symbol = str(getattr(strategy, "symbol", "") or "")
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
        return PortfolioAvailability(
            robust_accepted=len(sets),
            already_used=0,
            available=len(sets),
            symbols_available=len(by_symbol),
            by_symbol=dict(sorted(by_symbol.items())),
        )

    def _select_ubs_monthly_margin_profile(self, profile: str) -> None:
        """Keep broker/TTP margin checks mutually exclusive in the monthly UI."""
        profile_key = str(profile or "").strip().lower()
        if profile_key == "ttp" and bool(self.ubs_monthly_portfolio_validate_ttp_margin.get()):
            self.ubs_monthly_portfolio_validate_roboforex_margin.set(False)
        elif profile_key != "ttp" and bool(self.ubs_monthly_portfolio_validate_roboforex_margin.get()):
            self.ubs_monthly_portfolio_validate_ttp_margin.set(False)

    def _monthly_margin_profile(self) -> str:
        ttp_enabled = bool(self.ubs_monthly_portfolio_validate_ttp_margin.get())
        broker_enabled = bool(self.ubs_monthly_portfolio_validate_roboforex_margin.get())
        broker_profile = UBSPortfolioLogicMixin._active_broker_margin_profile(self)
        if ttp_enabled:
            if broker_enabled:
                self.ubs_monthly_portfolio_validate_roboforex_margin.set(False)
            return "ttp"
        if broker_enabled:
            return broker_profile
        self.ubs_monthly_portfolio_validate_roboforex_margin.set(True)
        return broker_profile

    def _monthly_roboforex_margin_enabled(self) -> bool:
        return self._monthly_margin_profile() != "ttp"

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
        self.ubs_monthly_portfolio_max_daily_dd.set("150")
        self.ubs_monthly_portfolio_daily_dd_full_history.set(False)
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
        self.ubs_monthly_portfolio_allow_forex.set(True)
        self.ubs_monthly_portfolio_allow_metals.set(True)
        self.ubs_monthly_portfolio_allow_indices.set(True)
        self.ubs_monthly_portfolio_allow_energies.set(True)
        self.ubs_monthly_portfolio_allow_crypto.set(True)
        self.ubs_monthly_portfolio_allow_stocks.set(True)
        self.ubs_monthly_portfolio_allow_bonds.set(True)
        self.ubs_monthly_portfolio_allow_softs.set(True)
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
        self.ubs_monthly_portfolio_validate_ttp_margin.set(False)
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
            allowed_groups = self._ubs_monthly_allowed_asset_groups()
            if allowed_groups:
                rows, _group_counts = self._filter_monthly_rows_by_allowed_groups(rows, allowed_groups)
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
            + f" | Grupos: {','.join(sorted(self._ubs_monthly_allowed_asset_groups()))}"
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

    def _create_ubs_monthly_portfolio_detail_window(self, portfolio_id: int) -> None:
        from ui.ubs_monthly_portfolio_view import _MonthlyPortfolioScreenAdapter
        from ui.ubs_portfolio_view import UBSPortfolioViewMixin

        UBSPortfolioViewMixin._create_ubs_portfolio_detail_window(
            _MonthlyPortfolioScreenAdapter(self),
            portfolio_id,
        )

    def _open_selected_ubs_monthly_portfolio_member(self) -> None:
        UBSPortfolioLogicMixin._open_selected_ubs_portfolio_member(
            self._monthly_portfolio_adapter()
        )

    def _populate_ubs_monthly_portfolio_detail(self, portfolio_id: int) -> None:
        UBSPortfolioLogicMixin._populate_ubs_portfolio_detail(
            self._monthly_portfolio_adapter(),
            portfolio_id,
        )

    def _open_selected_ubs_monthly_portfolio_detail_member(self) -> None:
        UBSPortfolioLogicMixin._open_selected_ubs_portfolio_detail_member(
            self._monthly_portfolio_adapter()
        )

    def _quarantine_selected_ubs_monthly_portfolio_member(self, portfolio_id: int) -> None:
        UBSPortfolioLogicMixin._quarantine_selected_ubs_portfolio_member(
            self._monthly_portfolio_adapter(),
            portfolio_id,
        )

    def _complete_saved_ubs_monthly_portfolio(self, portfolio_id: int) -> None:
        UBSPortfolioLogicMixin._complete_saved_ubs_portfolio(
            self._monthly_portfolio_adapter(),
            portfolio_id,
        )

    def _reoptimize_saved_ubs_monthly_portfolio(self, portfolio_id: int) -> None:
        UBSPortfolioLogicMixin._reoptimize_saved_ubs_portfolio(
            self._monthly_portfolio_adapter(),
            portfolio_id,
        )

    def _undo_latest_ubs_monthly_portfolio_completion(self, portfolio_id: int) -> None:
        UBSPortfolioLogicMixin._undo_latest_ubs_portfolio_completion(
            self._monthly_portfolio_adapter(),
            portfolio_id,
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
