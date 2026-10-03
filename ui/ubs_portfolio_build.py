"""Construccion en segundo plano y guardado del portafolio propuesto."""
from __future__ import annotations

import json
import sqlite3
import threading
from tkinter import messagebox

from portfolio_manager.ubs_portfolio import (
    PortfolioResult,
    PortfolioType,
    filter_rows_by_recent_positive_months,
    filter_rows_grid_off,
    load_robust_sets_from_rows,
    summarize_robust_rows,
)
from ui.ubs_portfolio_base import PORTFOLIO_TYPE_BATCH_SPECS


def _float_curve(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) <= 1:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _portfolio_metric_curves(
    metrics: object, target_portfolio_type: PortfolioType
) -> list[list[float]]:
    if not isinstance(metrics, dict):
        return []
    if bool(metrics.get("portfolio_bundle")) and isinstance(metrics.get("variants"), dict):
        keys = (
            ("aggressive",)
            if target_portfolio_type == PortfolioType.AGGRESSIVE
            else ("balanced", "conservative")
        )
        curves = []
        for key in keys:
            variant = metrics["variants"].get(key)
            value = variant.get("equity_curve_2020_2026") if isinstance(variant, dict) else None
            curve = _float_curve(value)
            if curve is not None:
                curves.append(curve)
        return curves
    curve = _float_curve(metrics.get("equity_curve_2020_2026"))
    return [curve] if curve is not None else []


class UBSPortfolioBuildMixin:
    """Construccion en segundo plano y guardado del portafolio propuesto."""

    def _run_ubs_portfolio_build(self) -> None:
        if (
            getattr(self, "ubs_portfolio_running", False)
            or getattr(self, "ubs_monthly_portfolio_running", False)
        ):
            messagebox.showwarning("Portafolio en ejecucion", "Ya hay un proceso de portafolio en marcha.")
            return
        try:
            inputs = self._read_ubs_portfolio_inputs()
        except ValueError as exc:
            messagebox.showerror("Entrada invalida", str(exc))
            return

        if hasattr(self, "_write_ui_settings"):
            try:
                self._write_ui_settings()
            except Exception:
                pass

        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self.ubs_portfolio_pending_proposals = []
        self._set_ubs_portfolio_save_enabled(False)
        self._clear_ubs_portfolio_result_tables()
        self._set_ubs_portfolio_running(True)
        self.ubs_portfolio_status.set("Analizando sets Final Tick 6M accepted...")
        threading.Thread(target=self._ubs_portfolio_worker, args=(inputs,), daemon=True).start()

    def _portfolio_rows_after_month_filter(self, rows: list, inputs: dict[str, object]):
        """Aplica el filtro de 3 meses positivos; None si vacia la seleccion."""
        if not bool(inputs.get("require_3_positive_months_6m")):
            return rows, []
        rows, month_warnings = filter_rows_by_recent_positive_months(
            rows,
            min_positive_months=3,
            window_months=6,
            progress=lambda msg: self.after(0, self.ubs_portfolio_status.set, msg),
        )
        if not rows:
            self._ubs_portfolio_failed(
                "No quedan candidatos tras exigir 3 meses positivos en los ultimos 6."
            )
            return None, []
        return rows, month_warnings

    def _portfolio_rows_after_grid_filter(self, rows: list, inputs: dict[str, object]):
        """Aplica el filtro Grid OFF; None si vacia la seleccion."""
        if not bool(inputs.get("grid_off")):
            return rows, []
        rows, grid_warnings = filter_rows_grid_off(rows)
        if not rows:
            self._ubs_portfolio_failed("No quedan candidatos tras aplicar Grid OFF.")
            return None, []
        return rows, grid_warnings

    def _portfolio_rows_after_group_filter(self, rows: list, allowed_groups: set[str]):
        """Deja solo los grupos permitidos; None si vacia la seleccion."""
        if not allowed_groups:
            return rows, []
        rows, row_group_counts = self._filter_portfolio_rows_by_allowed_groups(rows, allowed_groups)
        group_warnings: list[str] = []
        blocked_groups = {
            group: count
            for group, count in row_group_counts.items()
            if group not in allowed_groups and count
        }
        if blocked_groups:
            group_warnings.append(
                "Filtro grupos activo: "
                + ", ".join(sorted(allowed_groups))
                + ". Excluidos: "
                + ", ".join(f"{group}={count}" for group, count in sorted(blocked_groups.items()))
                + "."
            )
        if not rows:
            self._ubs_portfolio_failed("No quedan candidatos tras aplicar grupos permitidos.")
            return None, group_warnings
        return rows, group_warnings

    def _ubs_portfolio_failed(self, error: str) -> None:
        """Devuelve el error del hilo a la interfaz."""
        self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": error})

    def _portfolio_sets_from_rows(self, rows: list, inputs: dict[str, object], allowed_groups: set[str]):
        """Carga las curvas de los candidatos filtrados y su disponibilidad."""
        used = (
            self._used_set_paths_all_risk_profiles()
            if bool(inputs.get("exclude_used_sets", True))
            else []
        )
        availability = summarize_robust_rows(rows, used)
        raw_sets, load_warnings = load_robust_sets_from_rows(
            rows,
            used,
            progress=lambda msg: self.after(0, self.ubs_portfolio_status.set, msg),
        )
        if allowed_groups:
            raw_sets, _set_group_counts = self._filter_portfolio_sets_by_allowed_groups(
                raw_sets,
                allowed_groups,
            )
        if not raw_sets:
            detail = " ".join(load_warnings)
            raise ValueError(
                "No quedan sets cargados tras filtros, grupos y usados."
                + (f" {detail}" if detail else "")
            )
        return raw_sets, availability, load_warnings

    def _portfolio_build_proposals(self, raw_sets: list, inputs: dict[str, object], base_type):
        """Calcula las tres variantes sobre la composicion comun."""
        existing_curves_by_type = {
            portfolio_type: self._saved_portfolio_curves_all_accounts(portfolio_type)
            for _key, _label, portfolio_type in PORTFOLIO_TYPE_BATCH_SPECS
        }
        return self._optimize_locked_ubs_portfolio_variants(
            raw_sets,
            inputs,
            base_type,
            existing_curves_by_type,
            progress=lambda proposal_label, index: self.after(
                0,
                self.ubs_portfolio_status.set,
                (
                    f"Seleccionando composicion base ({proposal_label})..."
                    if index == 0
                    else f"Calculando variante {index}/3 ({proposal_label}) con los mismos sets..."
                ),
            ),
        )

    def _ubs_portfolio_worker(self, inputs: dict[str, object]) -> None:
        try:
            rows = self._final_tick_passed_candidates_all_accounts()
        except Exception as exc:
            self._ubs_portfolio_failed(f"No pude abrir la memoria UBS: {exc}")
            return
        try:
            if not rows:
                self._ubs_portfolio_failed(
                    "No hay candidatos con Final Tick 6M accepted en las memorias broker/cuenta."
                )
                return
            rows, month_warnings = self._portfolio_rows_after_month_filter(rows, inputs)
            if rows is None:
                return
            rows, grid_warnings = self._portfolio_rows_after_grid_filter(rows, inputs)
            if rows is None:
                return
            allowed_groups = {str(group) for group in (inputs.get("allowed_asset_groups") or [])}
            rows, group_warnings = self._portfolio_rows_after_group_filter(rows, allowed_groups)
            if rows is None:
                return
            base_type = PortfolioType(str(inputs.get("portfolio_type") or PortfolioType.BALANCED.value))
            raw_sets, availability, load_warnings = self._portfolio_sets_from_rows(
                rows, inputs, allowed_groups
            )
            proposals = self._portfolio_build_proposals(raw_sets, inputs, base_type)
            for proposal in proposals:
                proposal["result"].warnings[:0] = (
                    month_warnings + grid_warnings + group_warnings + load_warnings
                )
        except Exception as exc:
            self._ubs_portfolio_failed(f"Error generando portafolio: {exc}")
            return
        self.after(0, self._ubs_portfolio_finished, {
            "ok": True,
            "inputs": inputs,
            "availability": availability,
            "proposals": proposals,
        })

    def _saved_portfolio_curves(
        self,
        conn: sqlite3.Connection,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[list[float]]:
        curves: list[list[float]] = []
        if target_portfolio_type == PortfolioType.AGGRESSIVE:
            type_filter = (
                "and lower(coalesce(nullif(portfolio_type, ''), nullif(type, ''), '')) in ('aggressive', 'bundle')"
            )
        else:
            type_filter = "and lower(coalesce(nullif(portfolio_type, ''), nullif(type, ''), '')) <> 'aggressive'"
        for row in conn.execute(
            f"""
            select metrics_json from portfolios
            where metrics_json is not null and metrics_json <> ''
              {type_filter}
              and coalesce(nullif(portfolio_scope, ''), 'full_history') = ?
              and (? is null or target_month = ?)
              and (? is null or id <> ?)
            """,
            (
                portfolio_scope,
                target_month,
                target_month,
                exclude_portfolio_id,
                exclude_portfolio_id,
            ),
        ):
            try:
                metrics = json.loads(row["metrics_json"])
            except Exception:
                continue
            curves.extend(_portfolio_metric_curves(metrics, target_portfolio_type))
        return curves

    def _saved_portfolio_curves_all_accounts(
        self,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[list[float]]:
        curves: list[list[float]] = []
        active_memory = self._ubs_memory_path().resolve()
        for _account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                excluded = exclude_portfolio_id if memory_path.resolve() == active_memory else None
                curves.extend(
                    self._saved_portfolio_curves(
                        conn,
                        target_portfolio_type,
                        exclude_portfolio_id=excluded,
                        portfolio_scope=portfolio_scope,
                        target_month=target_month,
                    )
                )
            finally:
                conn.close()
        return curves

    def _saved_monthly_portfolio_curves_all_accounts(
        self,
        *,
        exclude_portfolio_id: int | None = None,
    ) -> list[list[float]]:
        curves: list[list[float]] = []
        active_memory = self._ubs_memory_path().resolve()
        for _account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                excluded = exclude_portfolio_id if memory_path.resolve() == active_memory else None
                for row in conn.execute(
                    """
                    select metrics_json from portfolios
                    where metrics_json is not null and metrics_json <> ''
                      and coalesce(nullif(portfolio_scope, ''), 'full_history') = 'monthly'
                      and (? is null or id <> ?)
                    """,
                    (excluded, excluded),
                ):
                    try:
                        metrics = json.loads(row["metrics_json"])
                    except Exception:
                        continue
                    curve = metrics.get("equity_curve_2020_2026") if isinstance(metrics, dict) else None
                    if isinstance(curve, list) and len(curve) > 1:
                        try:
                            curves.append([float(value) for value in curve])
                        except (TypeError, ValueError):
                            continue
            finally:
                conn.close()
        return curves

    def _ubs_portfolio_finished(self, info: dict) -> None:
        self._set_ubs_portfolio_running(False)
        if not info.get("ok"):
            message = info.get("error", "Error desconocido")
            self._clear_failed_ubs_portfolio_generation()
            self.ubs_portfolio_status.set(message)
            self._notify_ubs_portfolio_event(f"Portfolio Builder fallido: {message}")
            return

        proposals = info.get("proposals") or []
        if not proposals:
            self._clear_failed_ubs_portfolio_generation()
            self.ubs_portfolio_status.set("No se genero ninguna propuesta viable.")
            return
        self.ubs_portfolio_proposals_availability = info.get("availability")
        self._show_ubs_portfolio_proposals_preview(
            0,
            proposals,
            [],
            mode="generate",
        )
        self.ubs_portfolio_status.set(
            "Revisa las tres variantes. Al guardar se persistira un solo portafolio A/M/C."
        )

    def _accept_generated_ubs_portfolio_proposal(
        self,
        proposal: dict[str, object],
    ) -> None:
        result: PortfolioResult = proposal["result"]  # type: ignore[assignment]
        inputs: dict[str, object] = proposal["inputs"]  # type: ignore[assignment]
        self.ubs_portfolio_pending_result = result
        self.ubs_portfolio_pending_inputs = inputs
        all_proposals = list(getattr(self, "ubs_portfolio_proposals", {}).values())
        self.ubs_portfolio_pending_proposals = all_proposals or [proposal]
        self._populate_ubs_portfolio_result(result)
        self._populate_ubs_portfolio_availability(
            getattr(self, "ubs_portfolio_proposals_availability", None)
        )
        self._set_ubs_portfolio_save_enabled(True)
        group_text = self._ubs_portfolio_group_summary_text(result.group_summary)
        point_status = (
            f"DD puntual {result.point_usage_pct:.1f}% info"
            if not result.enforce_point_dd
            else f"DD puntual {result.point_usage_pct:.1f}%"
        )
        point_event = (
            f"DD puntual {result.actual_point_dd:,.2f} info"
            if not result.enforce_point_dd
            else (
                f"DD puntual {result.actual_point_dd:,.2f}/{result.target_point_dd:,.2f} "
                f"({result.point_usage_pct:.1f}%)"
            )
        )
        status = (
            f"Portafolio generado: {result.total_units} unidades, "
            f"DD valle {result.valley_usage_pct:.1f}%, {point_status}."
        )
        if group_text:
            status += f" Grupos: {group_text}."
        group_warning = self._ubs_portfolio_group_warning(result.warnings)
        if group_warning:
            status += f" Aviso: {group_warning}"
        self.ubs_portfolio_status.set(status)
        self._notify_ubs_portfolio_event(
            f"Portfolio Builder propuesta {proposal['label']}: "
            f"net {result.total_net_profit:,.2f}, "
            f"lote {result.total_lot:.2f}, "
            f"{result.total_units} unidades, "
            f"{result.active_strategies} estrategias, "
            f"DD valle {result.actual_valley_dd:,.2f}/{result.target_valley_dd:,.2f} "
            f"({result.valley_usage_pct:.1f}%), "
            f"{point_event}."
            + (f" Grupos: {group_text}." if group_text else "")
            + (f" Aviso: {group_warning}" if group_warning else "")
        )

    def _save_pending_ubs_portfolio(self) -> None:
        result: PortfolioResult | None = getattr(self, "ubs_portfolio_pending_result", None)
        inputs: dict[str, object] | None = getattr(self, "ubs_portfolio_pending_inputs", None)
        if result is None or inputs is None:
            messagebox.showinfo("Guardar portafolio", "Genera un portafolio valido antes de guardarlo.")
            return
        pending_proposals = list(getattr(self, "ubs_portfolio_pending_proposals", []) or [])
        if not pending_proposals:
            pending_proposals = [{"label": inputs.get("optimization_profile_label", "Portafolio"), "inputs": inputs, "result": result}]
        save_items: list[tuple[str, dict[str, object], PortfolioResult]] = []
        valid_proposals: list[dict[str, object]] = []
        for proposal in pending_proposals:
            proposal_result = proposal.get("result")
            proposal_inputs = proposal.get("inputs")
            if proposal_result is None or proposal_inputs is None:
                continue
            label = str(proposal.get("label") or "Portafolio")
            typed_inputs: dict[str, object] = proposal_inputs  # type: ignore[assignment]
            typed_result: PortfolioResult = proposal_result  # type: ignore[assignment]
            save_items.append((label, typed_inputs, typed_result))
            valid_proposals.append(
                {
                    **proposal,
                    "label": label,
                    "inputs": typed_inputs,
                    "result": typed_result,
                }
            )
        if not save_items:
            messagebox.showinfo("Guardar portafolio", "No hay propuestas validas para guardar.")
            return
        empty_labels = [label for label, _proposal_inputs, proposal_result in save_items if not proposal_result.allocations]
        if empty_labels:
            messagebox.showwarning(
                "Guardar portafolio",
                "Estas propuestas no tienen asignaciones: " + ", ".join(empty_labels),
            )
            return
        conn = self._ubs_portfolio_conn()
        saved_ids: list[int] = []
        selected_id: int | None = None
        try:
            if len(valid_proposals) > 1:
                portfolio_id = self._insert_portfolio_bundle(
                    conn,
                    valid_proposals,
                    result,
                    commit=False,
                )
                saved_ids.append(portfolio_id)
                selected_id = portfolio_id
            else:
                for _label, proposal_inputs, proposal_result in save_items:
                    portfolio_id = self._insert_portfolio(
                        conn,
                        proposal_inputs,
                        proposal_result,
                        commit=False,
                    )
                    saved_ids.append(portfolio_id)
                    if proposal_result is result:
                        selected_id = portfolio_id
            conn.commit()
        except Exception as exc:
            conn.rollback()
            messagebox.showerror("Guardar portafolio", f"No se pudo guardar el portafolio:\n{exc}")
            return
        finally:
            conn.close()
        if selected_id is None and saved_ids:
            selected_id = saved_ids[-1]
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self.ubs_portfolio_pending_proposals = []
        self._set_ubs_portfolio_save_enabled(False)
        self._refresh_ubs_portfolios(select_id=selected_id)
        if len(valid_proposals) > 1 and saved_ids:
            self.ubs_portfolio_status.set(
                f"Portafolio #{saved_ids[0]} guardado como A/M/C con {len(valid_proposals)} variantes."
            )
        elif len(saved_ids) == 1:
            self.ubs_portfolio_status.set(f"Portafolio #{saved_ids[0]} guardado.")
        else:
            self.ubs_portfolio_status.set(
                f"Guardados {len(saved_ids)} portafolios: "
                + ", ".join(f"#{portfolio_id}" for portfolio_id in saved_ids)
                + "."
            )
        notify_prefix = (
            f"Portfolio Builder guardado: 1 portafolio A/M/C con {len(valid_proposals)} variantes, "
            if len(valid_proposals) > 1 and saved_ids
            else f"Portfolio Builder guardado: {len(saved_ids)} portafolio(s), "
        )
        self._notify_ubs_portfolio_event(
            notify_prefix
            + f"ids {', '.join(str(portfolio_id) for portfolio_id in saved_ids)}, "
            + f"seleccion net {result.total_net_profit:,.2f}, lote {result.total_lot:.2f}, "
            + f"{result.active_strategies} estrategias."
        )

    def _notify_ubs_portfolio_event(self, message: str) -> None:
        notifier = getattr(self, "_notify_telegram", None)
        if callable(notifier):
            notifier(message)

    def _clear_failed_ubs_portfolio_generation(self) -> None:
        self.ubs_portfolio_pending_result = None
        self.ubs_portfolio_pending_inputs = None
        self.ubs_portfolio_pending_proposals = []
        self._set_ubs_portfolio_save_enabled(False)
        self._clear_ubs_portfolio_result_tables()

    def _ubs_portfolio_group_summary_text(self, group_summary: dict[str, dict[str, float | int]]) -> str:
        if not group_summary:
            return ""
        parts = []
        for group, stats in list(group_summary.items())[:4]:
            parts.append(f"{group} {float(stats.get('unit_pct', 0.0)):.0f}%")
        return ", ".join(parts)

    def _ubs_portfolio_group_warning(self, warnings: list[str]) -> str:
        for warning in warnings:
            if "grupo" in warning.lower() or "asset group" in warning or "Group concentration" in warning:
                return warning
        return ""
