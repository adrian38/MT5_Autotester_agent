"""Pintado del resultado, la curva y la exportacion de sets."""
from __future__ import annotations

from dataclasses import asdict
import json
import re
import shutil
import sqlite3
from pathlib import Path
from tkinter import filedialog, messagebox

from ubs.account import ACCOUNT_TYPES, BROKER_ACCOUNT_TYPES
from ubs.path_utils import resolve_workspace_path
from ubs.set_utils import write_set_text
from portfolio_manager.ubs_portfolio import PortfolioResult
from ui.ubs_portfolio_base import PORTFOLIO_BUNDLE_DISPLAY, PORTFOLIO_TYPE_DISPLAY


class UBSPortfolioResultMixin:
    """Pintado del resultado, la curva y la exportacion de sets."""

    def _clear_ubs_portfolio_result_tables(self) -> None:
        for tree_name in (
            "ubs_portfolio_members_tree",
            "ubs_portfolio_decision_tree",
            "ubs_portfolio_unused_tree",
        ):
            tree = getattr(self, tree_name, None)
            if tree is None:
                continue
            for item in tree.get_children(""):
                tree.delete(item)
        for var in (
            "ubs_portfolio_metric_net",
            "ubs_portfolio_metric_valley",
            "ubs_portfolio_metric_point",
            "ubs_portfolio_metric_count",
            "ubs_portfolio_metric_lot",
            "ubs_portfolio_metric_units",
        ):
            if hasattr(self, var):
                getattr(self, var).set("-")
        self.ubs_portfolio_member_paths = {}
        self._draw_ubs_portfolio_curve([])

    def _populate_ubs_portfolio_result(self, result: PortfolioResult) -> None:
        self._clear_ubs_portfolio_result_tables()
        self._set_portfolio_metrics_from_result(result)
        self._populate_ubs_portfolio_allocations([asdict(item) for item in result.allocations])
        self._populate_ubs_portfolio_decisions([asdict(item) for item in result.decision_log])
        self._populate_ubs_portfolio_unused([asdict(item) for item in result.unused_sets])
        self._draw_ubs_portfolio_curve(result.equity_curve_2020_2026)

    def _populate_ubs_portfolio_saved(self, portfolio_id: int) -> None:
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
            decisions = [dict(row) for row in self._portfolio_decisions(conn, portfolio_id)]
        finally:
            conn.close()
        if portfolio is None:
            return
        self._clear_ubs_portfolio_result_tables()
        self.ubs_portfolio_metric_net.set(f"{float(portfolio['total_net_profit'] or 0):,.0f}")
        self.ubs_portfolio_metric_valley.set(
            f"{float(portfolio['actual_valley_dd'] or 0):,.2f} ({float(portfolio['valley_usage_pct'] or 0):.1f}%)"
        )
        self.ubs_portfolio_metric_point.set(
            f"{float(portfolio['actual_point_dd'] or 0):,.2f} ({float(portfolio['point_usage_pct'] or 0):.1f}%)"
        )
        self.ubs_portfolio_metric_count.set(str(int(portfolio["active_strategies"] or len(members))))
        self.ubs_portfolio_metric_lot.set(f"{float(portfolio['total_lot'] or 0):.2f}")
        self.ubs_portfolio_metric_units.set(str(int(portfolio["total_units"] or 0)))
        self._populate_ubs_portfolio_allocations(members)
        self._populate_ubs_portfolio_decisions(decisions)
        metrics = self._portfolio_metrics_json(portfolio)
        self._populate_ubs_portfolio_unused(metrics.get("unused_sets", []))
        self._draw_ubs_portfolio_curve(metrics.get("equity_curve_2020_2026", []))

    def _set_portfolio_metrics_from_result(self, result: PortfolioResult) -> None:
        self.ubs_portfolio_metric_net.set(f"{result.total_net_profit:,.0f}")
        self.ubs_portfolio_metric_valley.set(f"{result.actual_valley_dd:,.2f} ({result.valley_usage_pct:.1f}%)")
        self.ubs_portfolio_metric_point.set(f"{result.actual_point_dd:,.2f} ({result.point_usage_pct:.1f}%)")
        self.ubs_portfolio_metric_count.set(str(result.active_strategies))
        self.ubs_portfolio_metric_lot.set(f"{result.total_lot:.2f}")
        self.ubs_portfolio_metric_units.set(str(result.total_units))

    def _ubs_portfolio_member_variant_label(self, member: dict[str, object]) -> str:
        label = str(member.get("variant_label") or "").strip()
        if label:
            return label
        key = str(member.get("variant_key") or "").strip().lower()
        return {
            "aggressive": "Agresivo",
            "balanced": "Moderado",
            "conservative": "Conservador",
        }.get(key, "")

    def _ubs_portfolio_member_account(self, member: dict[str, object]) -> str:
        account = str(member.get("account_type") or "").strip().upper()
        valid_labels = {f"{broker}/{account_type}" for broker, account_type in BROKER_ACCOUNT_TYPES}
        if account in valid_labels:
            return account
        if account in ACCOUNT_TYPES:
            return account
        candidate_id = str(member.get("candidate_id") or "").strip()
        if ":" in candidate_id:
            prefix = candidate_id.split(":", 1)[0].strip().upper()
            if prefix in valid_labels or prefix in ACCOUNT_TYPES:
                return prefix
        return ""

    def _ubs_portfolio_member_candidate_label(self, member: dict[str, object]) -> str:
        candidate_id = str(member.get("candidate_id") or "").strip()
        if ":" in candidate_id:
            prefix, value = candidate_id.split(":", 1)
            valid_labels = {f"{broker}/{account_type}" for broker, account_type in BROKER_ACCOUNT_TYPES}
            if prefix.strip().upper() in valid_labels or prefix.strip().upper() in ACCOUNT_TYPES:
                return value
        return candidate_id

    def _populate_ubs_portfolio_allocations(self, members: list[dict[str, object]]) -> None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return
        tree = self.ubs_portfolio_members_tree
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_member_paths = {}
        for member in members:
            set_id = str(member.get("set_id") or member.get("set_path") or "")
            set_path = str(member.get("set_path") or set_id)
            units = int(member.get("units") or 0)
            lot = float(member.get("lot") or 0)
            step = member.get("lot_size_step")
            values = (
                self._ubs_portfolio_member_variant_label(member),
                Path(set_id).name,
                self._ubs_portfolio_member_account(member),
                self._ubs_portfolio_member_candidate_label(member),
                str(member.get("symbol") or ""),
                str(member.get("timeframe") or member.get("period") or ""),
                units,
                f"{lot:.2f}",
                f"{float(member.get('net_profit_contribution') or 0):,.0f}",
                f"{float(member.get('standalone_valley_dd') or 0):,.2f}",
                f"{float(member.get('standalone_point_dd') or 0):,.2f}",
                f"{float(step):,.2f}" if step not in (None, "") else "-",
                f"{float(member.get('margin_required') or 0):,.2f}",
                f"{float(member.get('margin_pct') or 0):.1f}%",
                f"1:{float(member.get('margin_leverage') or 0):.0f}" if float(member.get("margin_leverage") or 0) else "-",
            )
            item = tree.insert("", "end", values=values)
            self.ubs_portfolio_member_paths[item] = {
                "set_path": set_path,
                "is": str(member.get("is_report_path") or ""),
                "oos": str(member.get("oos_report_path") or ""),
            }

    def _populate_ubs_portfolio_decisions(self, decisions: list[dict[str, object]]) -> None:
        if not hasattr(self, "ubs_portfolio_decision_tree"):
            return
        tree = self.ubs_portfolio_decision_tree
        for item in tree.get_children(""):
            tree.delete(item)
        for decision in decisions:
            tree.insert(
                "",
                "end",
                values=(
                    decision.get("step"),
                    decision.get("action"),
                    Path(str(decision.get("set_id") or "")).name,
                    Path(str(decision.get("from_set_id") or "")).name,
                    Path(str(decision.get("to_set_id") or "")).name,
                    f"{float(decision.get('gain') or 0):,.2f}",
                    f"{float(decision.get('valley_cost') or 0):,.2f}",
                    f"{float(decision.get('point_cost') or 0):,.2f}",
                    f"{float(decision.get('score') or 0):,.2f}",
                    f"{float(decision.get('portfolio_net_profit_after') or 0):,.2f}",
                    f"{float(decision.get('portfolio_valley_dd_after') or 0):,.2f}",
                    f"{float(decision.get('portfolio_point_dd_after') or 0):,.2f}",
                    decision.get("reason") or "",
                ),
            )

    def _populate_ubs_portfolio_unused(self, unused: list[dict[str, object]]) -> None:
        if not hasattr(self, "ubs_portfolio_unused_tree"):
            return
        tree = self.ubs_portfolio_unused_tree
        for item in tree.get_children(""):
            tree.delete(item)
        for item in unused[:200]:
            tree.insert(
                "",
                "end",
                values=(
                    Path(str(item.get("set_id") or "")).name,
                    item.get("symbol") or "",
                    f"{float(item.get('score') or 0):,.2f}",
                    item.get("reason") or "",
                ),
            )

    def _portfolio_metrics_json(self, portfolio: sqlite3.Row) -> dict[str, object]:
        raw = portfolio["metrics_json"]
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _portfolio_is_bundle(self, portfolio: sqlite3.Row | dict[str, object]) -> bool:
        try:
            type_key = str(portfolio["portfolio_type"] or portfolio["type"] or "").lower()  # type: ignore[index]
        except Exception:
            type_key = ""
        if type_key == "bundle":
            return True
        try:
            metrics = self._portfolio_metrics_json(portfolio)  # type: ignore[arg-type]
        except Exception:
            raw = portfolio.get("metrics_json") if isinstance(portfolio, dict) else None
            try:
                metrics = json.loads(str(raw or "{}"))
            except Exception:
                metrics = {}
        return isinstance(metrics, dict) and bool(metrics.get("portfolio_bundle"))

    def _draw_ubs_portfolio_curve(self, values: list[float]) -> None:
        canvas = getattr(self, "ubs_portfolio_curve_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(int(canvas.winfo_width()), 1)
        height = max(int(canvas.winfo_height()), 1)
        if width <= 1:
            canvas.after(60, lambda: self._draw_ubs_portfolio_curve(values))
            return
        if len(values) < 2:
            canvas.create_text(
                width // 2,
                height // 2,
                text="Sin curva",
                fill=self.colors["muted"],
                font=("Segoe UI", 9),
            )
            return
        low = min(values)
        high = max(values)
        span = high - low or 1.0
        pad = 10
        points: list[float] = []
        for index, value in enumerate(values):
            x = pad + (width - pad * 2) * index / max(len(values) - 1, 1)
            y = height - pad - (height - pad * 2) * (value - low) / span
            points.extend([x, y])
        canvas.create_line(*points, fill=self.colors["accent"], width=2, smooth=True)
        zero_y = height - pad - (height - pad * 2) * (0.0 - low) / span
        if pad <= zero_y <= height - pad:
            canvas.create_line(pad, zero_y, width - pad, zero_y, fill=self.colors["border"], dash=(3, 3))

    # ------------------------------------------------------------------ actions
    def _delete_selected_ubs_portfolio(self) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            messagebox.showinfo("Portfolio Builder", "Selecciona un portafolio para borrar.")
            return
        portfolio_id = int(selection[0])
        if not messagebox.askyesno(
            "Borrar portafolio",
            "Se borrara el portafolio y sus sets volveran a estar disponibles.\n\nContinuar?",
        ):
            return
        conn = self._ubs_portfolio_conn()
        try:
            self._delete_portfolio(conn, portfolio_id)
        finally:
            conn.close()
        self._refresh_ubs_portfolios()
        self.ubs_portfolio_status.set(f"Portafolio #{portfolio_id} borrado.")

    def _selected_ubs_portfolio_member_paths(self) -> dict[str, str] | None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return None
        selection = self.ubs_portfolio_members_tree.selection()
        if not selection:
            messagebox.showinfo("Portafolio UBS", "Selecciona una asignacion del portafolio.")
            return None
        return getattr(self, "ubs_portfolio_member_paths", {}).get(selection[0], {})

    def _open_selected_ubs_portfolio_member(self) -> None:
        paths = self._selected_ubs_portfolio_member_paths()
        if not paths:
            return
        report = paths.get("oos") or paths.get("is")
        if report:
            self._open_local_file(Path(report))
            return
        messagebox.showinfo("Abrir reporte", "La asignacion seleccionada no tiene reporte guardado.")

    def _export_ubs_portfolio_sets(self) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            messagebox.showinfo("Exportar sets", "Selecciona un portafolio guardado para exportar.")
            return
        portfolio_id = int(selection[0])
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        if portfolio is None or not members:
            messagebox.showinfo("Exportar sets", "El portafolio no tiene estrategias que exportar.")
            return

        folder = filedialog.askdirectory(title="Carpeta destino para los sets del portafolio")
        if not folder:
            return
        created = str(portfolio["created_at"] or "").replace("T", "_").replace(":", "").replace("-", "")
        type_key = str(portfolio["portfolio_type"] or portfolio["type"] or "")
        type_label = PORTFOLIO_BUNDLE_DISPLAY if self._portfolio_is_bundle(portfolio) else PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key or "Portfolio")
        raw_folder_name = f"PORTAFOLIO_{portfolio_id}_{type_label}_{created[:15]}".strip("_")
        folder_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_folder_name).strip("._") or f"PORTAFOLIO_{portfolio_id}"
        dest = Path(folder) / folder_name
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Exportar sets", f"No pude crear la carpeta:\n{exc}")
            return

        exported: list[tuple[str, str, str, str, int, float, str]] = []
        copied_paths: set[Path] = set()
        missing: list[str] = []
        for member in members:
            set_path = resolve_workspace_path(str(member.get("set_path") or member.get("set_id") or ""))
            if not set_path.is_file():
                missing.append(set_path.name)
                continue
            out_path = dest / set_path.name
            try:
                resolved_set_path = set_path.resolve()
                if resolved_set_path not in copied_paths and resolved_set_path != out_path.resolve():
                    shutil.copy2(set_path, out_path)
                copied_paths.add(resolved_set_path)
            except Exception:
                missing.append(set_path.name)
                continue
            exported.append((
                self._ubs_portfolio_member_variant_label(member),
                self._ubs_portfolio_member_account(member),
                str(member.get("symbol") or ""),
                str(member.get("timeframe") or member.get("period") or ""),
                int(member.get("units") or 0),
                float(member.get("lot") or 0),
                set_path.name,
            ))

        resumen = dest / f"PORTAFOLIO_{portfolio_id}_resumen.txt"
        capital = float(portfolio["capital"] or portfolio["account_capital"] or 0)
        lines = [
            f"Portafolio: {portfolio['name']}",
            f"Tipo: {PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key)}   Capital: {capital:,.0f}",
            f"DD valle objetivo: {float(portfolio['target_valley_dd'] or 0):,.2f}",
            f"DD puntual objetivo: {float(portfolio['target_point_dd'] or 0):,.2f}",
            f"DD valle usado: {float(portfolio['actual_valley_dd'] or 0):,.2f}",
            f"DD puntual usado: {float(portfolio['actual_point_dd'] or 0):,.2f}",
            f"Net profit total 2020-2026: {float(portfolio['total_net_profit'] or 0):,.2f}",
            "",
            "Sets exportados: copia exacta del .set original probado.",
            "No se modifica Risk, LotPerBalance_step, grid ni ningun otro parametro del EA.",
            "UNID. y LOTE son la asignacion informativa calculada por el portafolio.",
            "",
            f"{'PERFIL':12s} {'CUENTA':7s} {'SIMBOLO':12s} {'TF':5s} {'UNID.':>7s} {'LOTE':>7s}   SET",
        ]
        for variant, account, symbol, period, units, lot, name in exported:
            lines.append(f"{variant[:12]:12s} {account:7s} {symbol:12s} {period:5s} {units:7d} {lot:7.2f}   {name}")
        if missing:
            lines.append("")
            lines.append("OMITIDOS (set no encontrado): " + ", ".join(missing))
        write_set_text(resumen, "\n".join(lines), "utf-8")

        self.ubs_portfolio_status.set(f"Exportados {len(exported)} set(s) a {dest}")
        messagebox.showinfo("Exportar sets", f"Exportados {len(exported)} set(s) a:\n{dest}\n\nResumen: {resumen.name}")
        self._open_local_file(dest)
