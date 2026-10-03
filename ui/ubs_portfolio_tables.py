"""Refresco de las tablas de disponibilidad, portafolios y detalle."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from portfolio_manager.ubs_portfolio import PortfolioAvailability
from ui.ubs_portfolio_base import PORTFOLIO_BUNDLE_DISPLAY, PORTFOLIO_TYPE_DISPLAY


class UBSPortfolioTablesMixin:
    """Refresco de las tablas de disponibilidad, portafolios y detalle."""

    def _refresh_ubs_portfolio_availability(self) -> None:
        if not hasattr(self, "ubs_portfolio_availability_tree"):
            return
        if not self._ubs_portfolio_source_paths():
            self.ubs_portfolio_availability.set("Memorias UBS broker/cuenta no encontradas.")
            self._populate_ubs_portfolio_availability(None)
            return
        try:
            availability = self._portfolio_availability(
                target_portfolio_type=self._portfolio_type_from_label(self.ubs_portfolio_type.get())
            )
        except Exception as exc:
            self.ubs_portfolio_availability.set(f"Disponibilidad: error leyendo memorias ({exc})")
            self._populate_ubs_portfolio_availability(None)
            return
        self._populate_ubs_portfolio_availability(availability)

    def _populate_ubs_portfolio_availability(self, availability: PortfolioAvailability | None) -> None:
        if not hasattr(self, "ubs_portfolio_availability_tree"):
            return
        tree = self.ubs_portfolio_availability_tree
        for item in tree.get_children(""):
            tree.delete(item)
        if availability is None:
            self.ubs_portfolio_availability.set("Disponibilidad: sin datos")
            return
        filter_suffix = ""
        recent_months_var = getattr(self, "ubs_portfolio_require_3_positive_months_6m", None)
        if recent_months_var is not None and bool(recent_months_var.get()):
            filter_suffix = " | Filtro 3/6M activo al generar"
        grid_off_var = getattr(self, "ubs_portfolio_grid_off", None)
        if grid_off_var is not None and bool(grid_off_var.get()):
            filter_suffix += " | Grid OFF activo"
        exclude_used_var = getattr(self, "ubs_portfolio_exclude_used_sets", None)
        if exclude_used_var is not None and not bool(exclude_used_var.get()):
            filter_suffix += " | Reutilizacion de sets activa"
        self.ubs_portfolio_availability.set(
            f"Sets Final Tick 6M OK broker/cuenta: {availability.robust_accepted} | "
            f"Sets bloqueados: {availability.already_used} | "
            f"Sets disponibles: {availability.available} | "
            f"Simbolos disponibles: {availability.symbols_available}"
            f"{filter_suffix}"
        )
        for symbol, count in availability.by_symbol.items():
            tree.insert("", "end", values=(symbol, count))

    def _refresh_ubs_portfolios(self, select_id: int | None = None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        self._refresh_ubs_portfolio_availability()
        self._refresh_ubs_portfolio_quarantine()
        tree = self.ubs_portfolio_saved_tree
        for item in tree.get_children(""):
            tree.delete(item)
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_portfolio_status.set("Memoria UBS no encontrada.")
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolios = self._list_portfolios(conn)
        finally:
            conn.close()

        target_item = None
        for row in portfolios:
            type_key = str(row["portfolio_type"] or row["type"] or "")
            type_label = PORTFOLIO_BUNDLE_DISPLAY if self._portfolio_is_bundle(row) else PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key)
            capital = float(row["capital"] or row["account_capital"] or 0)
            values = (
                row["id"],
                row["created_at"],
                type_label,
                f"{capital:,.0f}",
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
            self._populate_ubs_portfolio_saved(int(target_item))
        else:
            self._clear_ubs_portfolio_result_tables()
            self.ubs_portfolio_status.set("Sin portafolios guardados todavia.")

    def _refresh_ubs_portfolio_quarantine(self) -> None:
        tree = getattr(self, "ubs_portfolio_quarantine_tree", None)
        if tree is None:
            return
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_quarantine_rows = {}
        try:
            rows = self._portfolio_quarantine_rows_all_accounts()
        except Exception as exc:
            self.ubs_portfolio_status.set(f"No pude leer la cuarentena: {exc}")
            return
        for index, row in enumerate(rows):
            item = tree.insert(
                "",
                "end",
                iid=f"q:{index}",
                values=(
                    Path(str(row.get("set_path") or "")).name,
                    row.get("account_type") or "",
                    row.get("symbol") or "",
                    row.get("timeframe") or "",
                    row.get("quarantined_at") or "",
                ),
                tags=("rejected",),
            )
            self.ubs_portfolio_quarantine_rows[item] = row

    def _release_selected_ubs_portfolio_quarantine(self) -> None:
        tree = getattr(self, "ubs_portfolio_quarantine_tree", None)
        if tree is None or not tree.selection():
            messagebox.showinfo("Cuarentena", "Selecciona un set en cuarentena.")
            return
        row = getattr(self, "ubs_portfolio_quarantine_rows", {}).get(tree.selection()[0])
        if not row:
            return
        set_name = Path(str(row.get("set_path") or "")).name
        if not messagebox.askyesno(
            "Reintegrar set",
            f"{set_name} volvera a ser elegible para futuros portafolios.\n\nContinuar?",
        ):
            return
        conn = self._ubs_portfolio_conn_for_memory(Path(str(row["memory_path"])))
        try:
            conn.execute("delete from portfolio_quarantine where id=?", (int(row["id"]),))
            conn.commit()
        finally:
            conn.close()
        self._refresh_ubs_portfolios()
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios()
        self.ubs_portfolio_status.set(f"{set_name} reintegrado al pool elegible.")

    def _on_ubs_portfolio_select(self, _event=None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            return
        try:
            self._populate_ubs_portfolio_saved(int(selection[0]))
        except ValueError:
            pass

    def _open_selected_ubs_portfolio_detail(self, event=None) -> None:
        tree = getattr(self, "ubs_portfolio_saved_tree", None)
        if tree is None:
            return
        if event is not None:
            item = tree.identify_row(event.y)
            if item:
                tree.selection_set(item)
        selection = tree.selection()
        if not selection:
            return
        portfolio_id = int(selection[0])
        self._create_ubs_portfolio_detail_window(portfolio_id)
        self._populate_ubs_portfolio_detail(portfolio_id)

    @staticmethod
    def _portfolio_detail_month_label(inputs: dict, is_monthly: bool, month_no: int) -> str:
        """Nombre del mes objetivo de un portafolio mensual."""
        month_label = str(inputs.get("target_month_label") or "").strip()
        if not is_monthly or month_label or not 1 <= month_no <= 12:
            return month_label
        month_names = (
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
        )
        return f"{month_no:02d} - {month_names[month_no - 1]}"

    @staticmethod
    def _portfolio_detail_headline(
        portfolio, portfolio_id: int, metrics: dict, members: list, profile_label: str,
        is_bundle: bool,
    ) -> str:
        """Primera linea del estado: composicion y unidades del portafolio."""
        target = max(int(portfolio["target_strategies"] or 0), int(portfolio["active_strategies"] or 0))
        if not is_bundle:
            return (
                f"Portafolio #{portfolio_id}: {len(members)}/{target} estrategias | "
                f"{int(portfolio['total_units'] or 0)} unidades | lote {float(portfolio['total_lot'] or 0):.2f}"
            )
        common_set_ids = metrics.get("common_set_ids") if isinstance(metrics, dict) else []
        variant_order = metrics.get("variant_order") if isinstance(metrics, dict) else []
        common_count = len(common_set_ids) if isinstance(common_set_ids, list) else target
        variant_count = len(variant_order) if isinstance(variant_order, list) else len({
            str(member.get("variant_key") or member.get("variant_label") or "")
            for member in members
            if member.get("variant_key") or member.get("variant_label")
        })
        return (
            f"Portafolio #{portfolio_id} A/M/C: {common_count} sets | {variant_count} variantes | "
            f"vista {profile_label or 'seleccionada'}: {int(portfolio['total_units'] or 0)} unidades, "
            f"lote {float(portfolio['total_lot'] or 0):.2f}"
        )

    def _portfolio_detail_status(
        self, portfolio, portfolio_id: int, metrics: dict, members: list,
        inputs: dict, is_bundle: bool, is_monthly: bool, month_label: str,
    ) -> str:
        """Linea de estado completa de la ventana de detalle."""
        profile_label = str(inputs.get("optimization_profile_label") or "").strip()
        status_parts = [
            self._portfolio_detail_headline(
                portfolio, portfolio_id, metrics, members, profile_label, is_bundle
            )
        ]
        if is_monthly and month_label:
            status_parts.append(f"Mensual {month_label}")
        if profile_label:
            status_parts.append(f"Perfil {profile_label}")
        status_parts.append(
            f"DD valle {float(portfolio['actual_valley_dd'] or 0):,.2f}/{float(portfolio['target_valley_dd'] or 0):,.2f}"
        )
        status_parts.append(
            f"DD puntual {float(portfolio['actual_point_dd'] or 0):,.2f}/{float(portfolio['target_point_dd'] or 0):,.2f}"
        )
        stress = metrics.get("stress_bootstrap") if isinstance(metrics.get("stress_bootstrap"), dict) else {}
        if stress:
            stress_state = "ALERTA stress" if bool(stress.get("alert")) else "stress OK"
            status_parts.append(
                f"{stress_state} P95 {float(stress.get('valley_dd_p95') or 0):,.2f}"
            )
        return " | ".join(status_parts)

    @staticmethod
    def _portfolio_member_coverage(member: dict, seasonal_coverage: dict) -> tuple[int, list, list]:
        """Mes objetivo y anos cubiertos por una estrategia del portafolio."""
        coverage = {}
        for coverage_key in (
            str(member.get("set_id") or ""),
            str(member.get("set_path") or ""),
        ):
            if coverage_key and coverage_key in seasonal_coverage:
                coverage = seasonal_coverage[coverage_key]
                break
        coverage_month = int(coverage.get("target_month") or 0) if isinstance(coverage, dict) else 0
        years = coverage.get("years") if isinstance(coverage, dict) else []
        positive_years = coverage.get("positive_years") if isinstance(coverage, dict) else []
        if not isinstance(years, list):
            years = []
        if not isinstance(positive_years, list):
            positive_years = []
        return coverage_month, years, positive_years

    def _portfolio_member_values(self, member: dict, seasonal_coverage: dict) -> tuple:
        """Columnas de una estrategia en la tabla de detalle."""
        coverage_month, years, positive_years = self._portfolio_member_coverage(
            member, seasonal_coverage
        )
        return (
            self._ubs_portfolio_member_variant_label(member),
            Path(str(member.get("set_path") or member.get("set_id") or "")).name,
            self._ubs_portfolio_member_account(member),
            self._ubs_portfolio_member_candidate_label(member),
            member.get("symbol") or "",
            member.get("timeframe") or member.get("period") or "",
            f"{coverage_month:02d}" if coverage_month else "",
            ",".join(str(year) for year in years),
            f"{len(positive_years)}/{len(years)}" if years else "",
            int(member.get("units") or 0),
            f"{float(member.get('lot') or 0):.2f}",
            f"{float(member.get('net_profit_contribution') or 0):,.0f}",
            f"{float(member.get('standalone_valley_dd') or 0):,.2f}",
            f"{float(member.get('standalone_point_dd') or 0):,.2f}",
        )

    def _populate_ubs_portfolio_detail(self, portfolio_id: int) -> None:
        window = getattr(self, "ubs_portfolio_detail_window", None)
        if window is None or not window.winfo_exists():
            return
        tree = getattr(self, "ubs_portfolio_detail_tree", None)
        if tree is None:
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_detail_members = {}
        if portfolio is None:
            self.ubs_portfolio_detail_status.set("El portafolio ya no existe.")
            return
        try:
            metrics = json.loads(portfolio["metrics_json"] or "{}")
        except Exception:
            metrics = {}
        inputs = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        seasonal_coverage = (
            metrics.get("seasonal_coverage")
            if isinstance(metrics.get("seasonal_coverage"), dict)
            else {}
        )
        is_bundle = self._portfolio_is_bundle(portfolio)
        is_monthly = str(portfolio["portfolio_scope"] or inputs.get("portfolio_scope") or "full_history") == "monthly"
        month_no = int(portfolio["target_month"] or inputs.get("target_month") or 0)
        month_label = self._portfolio_detail_month_label(inputs, is_monthly, month_no)
        title_suffix = f" - Mensual {month_label}" if is_monthly and month_label else ""
        bundle_suffix = " - A/M/C" if is_bundle else ""
        window.title(f"Portafolio #{portfolio_id}{bundle_suffix}{title_suffix}")
        self.ubs_portfolio_detail_status.set(
            self._portfolio_detail_status(
                portfolio, portfolio_id, metrics, members, inputs, is_bundle, is_monthly, month_label
            )
        )
        for index, member in enumerate(members):
            item = tree.insert(
                "",
                "end",
                iid=f"member:{index}",
                values=self._portfolio_member_values(member, seasonal_coverage),
                tags=("accepted",),
            )
            self.ubs_portfolio_detail_members[item] = member

    def _selected_ubs_portfolio_detail_member(self) -> dict[str, object] | None:
        tree = getattr(self, "ubs_portfolio_detail_tree", None)
        if tree is None or not tree.selection():
            messagebox.showinfo("Portafolio UBS", "Selecciona una estrategia del portafolio.")
            return None
        return getattr(self, "ubs_portfolio_detail_members", {}).get(tree.selection()[0])

    def _open_selected_ubs_portfolio_detail_member(self) -> None:
        member = self._selected_ubs_portfolio_detail_member()
        if not member:
            return
        report = str(member.get("oos_report_path") or member.get("is_report_path") or "")
        if report:
            self._open_local_file(Path(report))
        else:
            messagebox.showinfo("Abrir reporte", "La estrategia no tiene reporte guardado.")

    def _quarantine_selected_ubs_portfolio_member(self, portfolio_id: int) -> None:
        try:
            self._quarantine_selected_ubs_portfolio_member_impl(portfolio_id)
        except Exception as exc:
            messagebox.showerror("Poner en cuarentena", f"No se pudo actualizar el portafolio:\n{exc}")
            self._refresh_ubs_portfolios(select_id=portfolio_id)
            if hasattr(self, "_refresh_ubs_monthly_portfolios"):
                self._refresh_ubs_monthly_portfolios(select_id=portfolio_id)
            self._populate_ubs_portfolio_detail(portfolio_id)

    def _record_portfolio_member_quarantine(self, member, portfolio_id, set_path) -> None:
        account_type, memory_path, candidate_id = self._resolve_portfolio_member_source(member)
        conn = self._ubs_portfolio_conn_for_memory(memory_path)
        try:
            conn.execute(
                """insert into portfolio_quarantine (
                    account_type, candidate_id, set_path, symbol, timeframe,
                    reason, source_portfolio_id, quarantined_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(set_path) do update set
                    account_type=excluded.account_type, candidate_id=excluded.candidate_id,
                    symbol=excluded.symbol, timeframe=excluded.timeframe,
                    reason=excluded.reason, source_portfolio_id=excluded.source_portfolio_id,
                    quarantined_at=excluded.quarantined_at""",
                (
                    account_type, candidate_id, set_path, str(member.get("symbol") or ""),
                    str(member.get("timeframe") or member.get("period") or ""),
                    "Retirada manualmente de un portafolio guardado", portfolio_id,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _remove_quarantined_portfolio_member(self, member, portfolio_id, set_path) -> None:
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            if portfolio is None:
                raise ValueError("El portafolio ya no existe.")
            target = max(int(portfolio["target_strategies"] or 0), int(portfolio["active_strategies"] or 0))
            conn.execute("update portfolios set target_strategies=? where id=?", (target, portfolio_id))
            deleted = conn.execute(
                "delete from portfolio_allocations where portfolio_id=? and set_path=?",
                (portfolio_id, set_path),
            )
            if deleted.rowcount == 0 and member.get("id") is not None:
                deleted = conn.execute(
                    "delete from portfolio_allocations where portfolio_id=? and id=?",
                    (portfolio_id, int(member["id"])),
                )
            conn.execute("delete from portfolio_members where portfolio_id=? and set_path=?", (portfolio_id, set_path))
            if deleted.rowcount == 0:
                raise ValueError("No se encontro la asignacion seleccionada dentro del portafolio.")
            self._recalculate_saved_portfolio(conn, portfolio_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _quarantine_selected_ubs_portfolio_member_impl(self, portfolio_id: int) -> None:
        member = self._selected_ubs_portfolio_detail_member()
        if not member:
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        finally:
            conn.close()
        if portfolio is not None and self._portfolio_is_bundle(portfolio):
            messagebox.showinfo(
                "Poner en cuarentena",
                "Este portafolio es A/M/C. Si quieres retirar un set, regeneralo para recalcular las tres variantes juntas.",
            )
            return
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        set_name = Path(set_path).name
        if not messagebox.askyesno(
            "Poner en cuarentena",
            f"{set_name} dejara de ser elegible y se quitara del portafolio #{portfolio_id}.\n\n"
            "Despues podras usar 'Completar portafolio' para buscar una sustituta y recalcular lotes.",
        ):
            return
        self._record_portfolio_member_quarantine(member, portfolio_id, set_path)
        self._remove_quarantined_portfolio_member(member, portfolio_id, set_path)
        self._refresh_ubs_portfolios(select_id=portfolio_id)
        if hasattr(self, "_refresh_ubs_monthly_portfolios"):
            self._refresh_ubs_monthly_portfolios(select_id=portfolio_id)
        self._populate_ubs_portfolio_detail(portfolio_id)
        self.ubs_portfolio_status.set(f"{set_name} puesto en cuarentena y retirado del portafolio #{portfolio_id}.")

    def _saved_portfolio_inputs(self, portfolio: sqlite3.Row) -> dict[str, object]:
        metrics = self._portfolio_metrics_json(portfolio)
        stored = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        defaults: dict[str, object] = {
            "capital": float(portfolio["capital"] or portfolio["account_capital"] or 0),
            "valley_dd_pct": float(portfolio["target_valley_dd_pct"] or 0),
            "point_dd_pct": float(portfolio["target_point_dd_pct"] or 0),
            "portfolio_type": str(portfolio["portfolio_type"] or portfolio["type"] or "balanced").lower(),
            "top_k_per_symbol": 3,
            "max_total_candidates": 30,
            "min_trades_2020_2026": 100,
            "max_units_per_set": None,
            "max_total_units": None,
            "max_units_per_symbol": None,
            "max_sets_per_symbol": 1,
            "run_local_search": True,
            "use_correlation": True,
            "require_3_positive_months_6m": False,
            "dd_reserve_pct": 0.0,
            "search_restarts": 0,
            "max_pair_corr": 0.35,
            "max_downside_corr": 0.25,
            "max_dd_overlap": 0.35,
            "max_portfolio_corr": 0.50,
            "portfolio_scope": str(portfolio["portfolio_scope"] or "full_history"),
            "target_month": int(portfolio["target_month"] or 0) or None,
            "strict_yearly_month_validation": False,
            "deep_optimization": False,
            "exclude_used_sets": True,
            "exclude_monthly_used": False,
            "corr_with_monthly_portfolios": False,
            "enforce_point_dd": str(portfolio["portfolio_scope"] or "full_history") != "monthly",
            "daily_dd_full_history": False,
            "allowed_asset_groups": [
                "Forex", "Metals", "Indices", "Energies",
                "Crypto", "Stocks", "Bonds", "Softs",
            ],
        }
        defaults.update(stored)
        stored_groups = set(defaults.get("allowed_asset_groups") or [])
        legacy_group_filter = not stored_groups.intersection(
            {"Indices", "Energies", "Crypto", "Bonds", "Softs"}
        )
        if "IndicesEnergies" in stored_groups:
            stored_groups.remove("IndicesEnergies")
            stored_groups.update(("Indices", "Energies"))
        if legacy_group_filter:
            stored_groups.update(("Crypto", "Bonds", "Softs"))
        if stored_groups:
            defaults["allowed_asset_groups"] = sorted(stored_groups)
        if str(defaults.get("portfolio_scope") or "full_history") == "monthly":
            defaults["enforce_point_dd"] = False
        return defaults
