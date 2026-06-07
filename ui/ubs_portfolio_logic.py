from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from ubs.db import connect_memory
from ubs.set_utils import read_set_with_encoding, write_set_text
from portfolio_manager.ubs_portfolio import (
    AllocationResult,
    apply_portfolio_lot_text,
    compute_allocation,
    select_robust_sets,
)


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


# Etiqueta visible (español) <-> clave interna del módulo de cálculo.
PORTFOLIO_TYPE_LABELS = {
    "Conservador": "conservative",
    "Equilibrado": "balanced",
    "Agresivo": "aggressive",
}
PORTFOLIO_TYPE_DISPLAY = {value: key for key, value in PORTFOLIO_TYPE_LABELS.items()}


class UBSPortfolioLogicMixin:
    # ----------------------------------------------------------------- conexión / esquema
    def _ensure_portfolio_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists portfolios (
                id integer primary key autoincrement,
                created_at text not null,
                name text not null,
                type text not null,
                num_symbols integer not null,
                account_capital real not null,
                target_valley_dd_pct real not null,
                target_point_dd_pct real not null,
                scale_factor real,
                binding_constraint text,
                total_net_profit real,
                actual_valley_dd real,
                actual_point_dd real,
                metrics_json text
            )
            """
        )
        conn.execute(
            """
            create table if not exists portfolio_members (
                id integer primary key autoincrement,
                portfolio_id integer not null,
                candidate_id integer,
                set_path text not null,
                symbol text,
                period text,
                lot_multiplier real,
                lot real,
                lot_size_step real,
                standalone_dd real,
                quality_score real,
                combined_net_profit real,
                is_report_path text,
                oos_report_path text
            )
            """
        )
        conn.commit()

    def _ubs_portfolio_conn(self) -> sqlite3.Connection:
        conn = connect_memory(self._ubs_memory_path())
        conn.row_factory = sqlite3.Row
        # Reutiliza el esquema base del agente (runs/candidates/candidate_robustness)...
        self._ensure_ubs_memory_schema(conn)
        # ...y añade las tablas de portafolios.
        self._ensure_portfolio_schema(conn)
        return conn

    # ----------------------------------------------------------------- SQL
    def _robust_passed_candidates(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            """
            select c.id as candidate_id, c.set_path, c.symbol, c.target_symbol, c.period,
                   c.report_path as is_report_path, cr.report_path as oos_report_path
            from candidates c
            join candidate_robustness cr on cr.candidate_id = c.id
            where cr.status = 'accepted'
            order by c.id
            """
        ).fetchall()

    def _used_set_paths(self, conn: sqlite3.Connection) -> list[str]:
        return [str(row["set_path"]) for row in conn.execute("select set_path from portfolio_members")]

    def _insert_portfolio(
        self,
        conn: sqlite3.Connection,
        name: str,
        type_key: str,
        num_symbols: int,
        valley_pct: float,
        point_pct: float,
        result: AllocationResult,
    ) -> int:
        included = [s for s in result.strategies if not s.excluded]
        metrics = {
            "scale_factor": result.scale_factor,
            "binding_constraint": result.binding_constraint,
            "target_valley_dd": result.target_valley_dd,
            "target_point_dd": result.target_point_dd,
            "continuous_valley_dd": result.continuous_valley_dd,
            "continuous_point_dd": result.continuous_point_dd,
            "actual_valley_dd": result.actual_valley_dd,
            "actual_point_dd": result.actual_point_dd,
            "total_net_profit": result.total_net_profit,
            "account_capital": result.account_capital,
            "warnings": result.warnings,
            "strategies": [
                {
                    "symbol": s.symbol,
                    "period": s.period,
                    "lot": s.lot,
                    "lot_size_step": s.lot_size_step,
                    "multiplier": s.multiplier,
                    "standalone_dd": s.standalone_dd,
                    "quality": s.quality,
                    "net_profit": s.net_profit,
                    "excluded": s.excluded,
                    "note": s.note,
                }
                for s in result.strategies
            ],
        }
        cur = conn.execute(
            """
            insert into portfolios (
                created_at, name, type, num_symbols, account_capital,
                target_valley_dd_pct, target_point_dd_pct, scale_factor, binding_constraint,
                total_net_profit, actual_valley_dd, actual_point_dd, metrics_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                name,
                type_key,
                num_symbols,
                result.account_capital,
                valley_pct,
                point_pct,
                result.scale_factor,
                result.binding_constraint,
                result.total_net_profit,
                result.actual_valley_dd,
                result.actual_point_dd,
                json.dumps(metrics),
            ),
        )
        portfolio_id = int(cur.lastrowid)
        for strategy in included:
            conn.execute(
                """
                insert into portfolio_members (
                    portfolio_id, candidate_id, set_path, symbol, period, lot_multiplier,
                    lot, lot_size_step, standalone_dd, quality_score, combined_net_profit,
                    is_report_path, oos_report_path
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    strategy.candidate_id,
                    strategy.set_path,
                    strategy.symbol,
                    strategy.period,
                    strategy.multiplier,
                    strategy.lot,
                    strategy.lot_size_step,
                    strategy.standalone_dd,
                    strategy.quality,
                    strategy.net_profit,
                    strategy.is_report_path,
                    strategy.oos_report_path,
                ),
            )
        conn.commit()
        return portfolio_id

    def _list_portfolios(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute("select * from portfolios order by id desc").fetchall()

    def _portfolio_members(self, conn: sqlite3.Connection, portfolio_id: int) -> list[sqlite3.Row]:
        return conn.execute(
            "select * from portfolio_members where portfolio_id=? order by lot desc",
            (portfolio_id,),
        ).fetchall()

    def _delete_portfolio(self, conn: sqlite3.Connection, portfolio_id: int) -> None:
        conn.execute("delete from portfolio_members where portfolio_id=?", (portfolio_id,))
        conn.execute("delete from portfolios where id=?", (portfolio_id,))
        conn.commit()

    # ----------------------------------------------------------------- estado UI
    def _set_ubs_portfolio_running(self, running: bool) -> None:
        self.ubs_portfolio_running = running
        state = "disabled" if running else "normal"
        for button in getattr(self, "ubs_portfolio_buttons", []):
            try:
                button.configure(state=state)
            except Exception:
                pass
        if hasattr(self, "ubs_portfolio_progress"):
            if running:
                self.ubs_portfolio_progress.start(12)
            else:
                self.ubs_portfolio_progress.stop()

    # ----------------------------------------------------------------- generar
    def _run_ubs_portfolio_build(self) -> None:
        if getattr(self, "ubs_portfolio_running", False):
            messagebox.showwarning("Portafolio en ejecucion", "Ya hay un proceso de portafolio en marcha.")
            return
        try:
            num_symbols = int(self.ubs_portfolio_num_symbols.get())
        except (ValueError, AttributeError):
            messagebox.showerror("Numero invalido", "El numero de simbolos debe ser un entero.")
            return
        if num_symbols < 1:
            messagebox.showerror("Numero invalido", "El numero de simbolos debe ser >= 1.")
            return

        type_label = self.ubs_portfolio_type.get().strip()
        type_key = PORTFOLIO_TYPE_LABELS.get(type_label, "balanced")

        try:
            capital = float(self.ubs_portfolio_capital.get().replace(",", "."))
            valley_pct = float(self.ubs_portfolio_valley_pct.get().replace(",", "."))
            point_pct = float(self.ubs_portfolio_point_pct.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Entrada invalida", "Capital, DD valle %% y DD puntual %% deben ser numeros.")
            return
        if capital <= 0 or valley_pct <= 0 or point_pct <= 0:
            messagebox.showerror("Entrada invalida", "Capital y porcentajes de DD deben ser mayores que 0.")
            return

        if hasattr(self, "_write_ui_settings"):
            try:
                self._write_ui_settings()
            except Exception:
                pass

        self._set_ubs_portfolio_running(True)
        self.ubs_portfolio_status.set("Analizando sets robustos...")
        thread = threading.Thread(
            target=self._ubs_portfolio_worker,
            args=(num_symbols, type_key, type_label, capital, valley_pct, point_pct),
            daemon=True,
        )
        thread.start()

    def _ubs_portfolio_worker(
        self,
        num_symbols: int,
        type_key: str,
        type_label: str,
        capital: float,
        valley_pct: float,
        point_pct: float,
    ) -> None:
        try:
            conn = self._ubs_portfolio_conn()
        except Exception as exc:
            self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": f"No pude abrir la memoria UBS: {exc}"})
            return
        try:
            rows = self._robust_passed_candidates(conn)
            used = self._used_set_paths(conn)
            if not rows:
                self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": "No hay candidatos que hayan pasado robustez."})
                return
            selected, warnings = select_robust_sets(
                rows,
                num_symbols,
                used,
                progress=lambda msg: self.after(0, self.ubs_portfolio_status.set, msg),
            )
            if not selected:
                self.after(0, self._ubs_portfolio_finished, {
                    "ok": False,
                    "error": "No quedan sets robustos disponibles (¿todos usados en portafolios previos?).",
                    "warnings": warnings,
                })
                return
            self.after(0, self.ubs_portfolio_status.set, "Calibrando lotes...")
            result = compute_allocation(selected, type_key, capital, valley_pct, point_pct)
            included = [s for s in result.strategies if not s.excluded]
            if not included:
                self.after(0, self._ubs_portfolio_finished, {
                    "ok": False,
                    "error": "Capital o %% de DD demasiado bajos: ningun lote alcanza 0.01.",
                    "warnings": warnings + result.warnings,
                })
                return
            created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
            name = f"{type_label} · {len(included)} simbolos · {created_at}"
            portfolio_id = self._insert_portfolio(
                conn, name, type_key, num_symbols, valley_pct, point_pct, result
            )
        except Exception as exc:
            self.after(0, self._ubs_portfolio_finished, {"ok": False, "error": f"Error generando portafolio: {exc}"})
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass
        self.after(0, self._ubs_portfolio_finished, {
            "ok": True,
            "portfolio_id": portfolio_id,
            "result": result,
            "warnings": warnings + result.warnings,
        })

    def _ubs_portfolio_finished(self, info: dict) -> None:
        self._set_ubs_portfolio_running(False)
        if not info.get("ok"):
            message = info.get("error", "Error desconocido")
            self.ubs_portfolio_status.set(message)
            extra = info.get("warnings") or []
            full = message + ("\n\n" + "\n".join(extra) if extra else "")
            messagebox.showerror("Portafolio UBS", full)
            return

        result: AllocationResult = info["result"]
        self._refresh_ubs_portfolios(select_id=info["portfolio_id"])
        self.ubs_portfolio_status.set(
            f"Portafolio generado. Tope vinculante: {result.binding_constraint}. "
            f"Escala global S={result.scale_factor:,.2f}."
        )
        warnings = info.get("warnings") or []
        summary = (
            f"Net profit total: {result.total_net_profit:,.2f}\n"
            f"DD valle real: {result.actual_valley_dd:,.2f} (tope {result.target_valley_dd:,.2f})\n"
            f"DD puntual real: {result.actual_point_dd:,.2f} (tope {result.target_point_dd:,.2f})"
        )
        if warnings:
            summary += "\n\nAvisos:\n" + "\n".join(warnings)
        messagebox.showinfo("Portafolio UBS generado", summary)

    # ----------------------------------------------------------------- refresco / listado
    def _refresh_ubs_portfolios(self, select_id: int | None = None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
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
            type_key = str(row["type"])
            values = (
                row["id"],
                row["created_at"],
                PORTFOLIO_TYPE_DISPLAY.get(type_key, type_key),
                row["num_symbols"],
                f"{float(row['account_capital'] or 0):,.0f}",
                f"{float(row['total_net_profit'] or 0):,.0f}",
                f"{float(row['actual_valley_dd'] or 0):,.2f}",
                f"{float(row['actual_point_dd'] or 0):,.2f}",
                str(row["binding_constraint"] or ""),
            )
            item = tree.insert("", "end", iid=str(row["id"]), values=values)
            if select_id is not None and int(row["id"]) == int(select_id):
                target_item = item

        if target_item is None and portfolios:
            target_item = str(portfolios[0]["id"])
        if target_item is not None:
            tree.selection_set(target_item)
            tree.focus(target_item)
            self._populate_ubs_portfolio_members(int(target_item))
        else:
            self._clear_ubs_portfolio_members()
            self.ubs_portfolio_status.set("Sin portafolios generados todavia.")

    def _on_ubs_portfolio_select(self, _event=None) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            return
        try:
            self._populate_ubs_portfolio_members(int(selection[0]))
        except ValueError:
            pass

    def _clear_ubs_portfolio_members(self) -> None:
        if hasattr(self, "ubs_portfolio_members_tree"):
            for item in self.ubs_portfolio_members_tree.get_children(""):
                self.ubs_portfolio_members_tree.delete(item)
        for var in ("ubs_portfolio_metric_net", "ubs_portfolio_metric_valley",
                    "ubs_portfolio_metric_point", "ubs_portfolio_metric_count"):
            if hasattr(self, var):
                getattr(self, var).set("—")

    def _populate_ubs_portfolio_members(self, portfolio_id: int) -> None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return
        conn = self._ubs_portfolio_conn()
        try:
            portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
            members = self._portfolio_members(conn, portfolio_id)
        finally:
            conn.close()
        if portfolio is None:
            return

        tree = self.ubs_portfolio_members_tree
        for item in tree.get_children(""):
            tree.delete(item)
        self.ubs_portfolio_member_paths = {}
        for member in members:
            step = member["lot_size_step"]
            values = (
                member["symbol"],
                member["period"],
                f"{float(member['lot'] or 0):.2f}",
                f"{float(step):,.2f}" if step is not None else "—",
                f"{float(member['lot_multiplier'] or 0):.2f}",
                f"{float(member['standalone_dd'] or 0):,.2f}",
                f"{float(member['quality_score'] or 0):,.2f}",
                f"{float(member['combined_net_profit'] or 0):,.0f}",
                Path(str(member["set_path"])).name,
            )
            item = tree.insert("", "end", values=values)
            self.ubs_portfolio_member_paths[item] = {
                "is": str(member["is_report_path"] or ""),
                "oos": str(member["oos_report_path"] or ""),
            }

        if hasattr(self, "ubs_portfolio_metric_net"):
            self.ubs_portfolio_metric_net.set(f"{float(portfolio['total_net_profit'] or 0):,.0f}")
            self.ubs_portfolio_metric_valley.set(f"{float(portfolio['actual_valley_dd'] or 0):,.2f}")
            self.ubs_portfolio_metric_point.set(f"{float(portfolio['actual_point_dd'] or 0):,.2f}")
            self.ubs_portfolio_metric_count.set(str(len(members)))

    def _delete_selected_ubs_portfolio(self) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            messagebox.showinfo("Portafolio UBS", "Selecciona un portafolio para borrar.")
            return
        portfolio_id = int(selection[0])
        if not messagebox.askyesno(
            "Borrar portafolio",
            "Se borrara el portafolio y sus sets volveran a estar disponibles para nuevos portafolios.\n\n¿Continuar?",
        ):
            return
        conn = self._ubs_portfolio_conn()
        try:
            self._delete_portfolio(conn, portfolio_id)
        finally:
            conn.close()
        self._refresh_ubs_portfolios()
        self.ubs_portfolio_status.set(f"Portafolio #{portfolio_id} borrado; sus sets quedan liberados.")

    def _open_selected_ubs_portfolio_member(self) -> None:
        if not hasattr(self, "ubs_portfolio_members_tree"):
            return
        selection = self.ubs_portfolio_members_tree.selection()
        if not selection:
            return
        paths = getattr(self, "ubs_portfolio_member_paths", {}).get(selection[0], {})
        oos = paths.get("oos") or paths.get("is")
        if oos:
            self._open_local_file(Path(oos))

    def _export_ubs_portfolio_sets(self) -> None:
        if not hasattr(self, "ubs_portfolio_saved_tree"):
            return
        selection = self.ubs_portfolio_saved_tree.selection()
        if not selection:
            messagebox.showinfo("Exportar sets", "Selecciona un portafolio para exportar.")
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
        dest = Path(folder)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Exportar sets", f"No pude crear la carpeta:\n{exc}")
            return

        capital = float(portfolio["account_capital"] or 0)
        exported: list[tuple] = []
        missing: list[str] = []
        not_found_key: list[str] = []
        for member in members:
            set_path = Path(str(member["set_path"]))
            if not set_path.is_file():
                missing.append(set_path.name)
                continue
            try:
                text, encoding = read_set_with_encoding(set_path)
            except Exception:
                missing.append(set_path.name)
                continue
            step = float(member["lot_size_step"] or 0)
            new_text, step_int, found = apply_portfolio_lot_text(text, step)
            if not found:
                not_found_key.append(set_path.name)
            units = int(capital // step_int) if step_int > 0 else 0
            real_lot = round(units * 0.01, 2)
            out_path = dest / set_path.name
            write_set_text(out_path, new_text, encoding)
            exported.append((member["symbol"], member["period"], real_lot, step_int, set_path.name))

        # Resumen legible junto a los sets.
        resumen = dest / f"PORTAFOLIO_{portfolio_id}_resumen.txt"
        lines = [
            f"Portafolio: {portfolio['name']}",
            f"Tipo: {portfolio['type']}   Capital: {capital:,.0f}",
            f"Tope DD valle: {float(portfolio['target_valley_dd_pct'] or 0)}%   "
            f"Tope DD puntual: {float(portfolio['target_point_dd_pct'] or 0)}%",
            f"DD valle real (equity, suma): {float(portfolio['actual_valley_dd'] or 0):,.2f}",
            f"DD puntual real (cerrado): {float(portfolio['actual_point_dd'] or 0):,.2f}",
            f"Net profit total 2020-2026: {float(portfolio['total_net_profit'] or 0):,.2f}",
            "",
            "Modo de lote exportado: Risk=2 (lote por balance).",
            "El EA aplica  Lots = floor(AccountBalance / LotPerBalance_step) * 0.01",
            f"(calculado para un balance de {capital:,.0f}).",
            "",
            f"{'SIMBOLO':12s} {'TF':5s} {'LOTE':>7s} {'LotPerBalance_step':>20s}   SET",
        ]
        for symbol, period, real_lot, step_int, name in exported:
            lines.append(f"{str(symbol):12s} {str(period):5s} {real_lot:7.2f} {step_int:20d}   {name}")
        if missing:
            lines.append("")
            lines.append("OMITIDOS (set no encontrado en disco): " + ", ".join(missing))
        if not_found_key:
            lines.append("")
            lines.append("AVISO (sin clave LotPerBalance_step en el set): " + ", ".join(not_found_key))
        write_set_text(resumen, "\n".join(lines), "utf-8")

        message = f"Exportados {len(exported)} set(s) a:\n{dest}\n\nResumen: {resumen.name}"
        if missing:
            message += f"\n\nOmitidos por set no encontrado: {len(missing)}"
        self.ubs_portfolio_status.set(f"Exportados {len(exported)} set(s) a {dest}")
        messagebox.showinfo("Exportar sets", message)
        self._open_local_file(dest)
