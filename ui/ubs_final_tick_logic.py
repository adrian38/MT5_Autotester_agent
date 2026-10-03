from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_final_tick
from ubs.path_utils import resolve_workspace_path
from ui.ubs_final_tick_rows import UBSFinalTickRowsMixin


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent



class UBSFinalTickLogicMixin(UBSFinalTickRowsMixin):
    def _on_ubs_final_tick_tree_click(self, event) -> str | None:
        if not hasattr(self, "ubs_final_tick_tree"):
            return None
        item, column = self._tree_item_from_event(self.ubs_final_tick_tree, event)
        if not item or column != "#1":
            return None
        info = self.ubs_final_tick_paths.get(item, {})
        cid = info.get("id", item)
        if cid in self.ubs_final_tick_checked:
            self.ubs_final_tick_checked.remove(cid)
        else:
            self.ubs_final_tick_checked.add(cid)
        values = list(self.ubs_final_tick_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(cid in self.ubs_final_tick_checked)
            self.ubs_final_tick_tree.item(item, values=values)
        return "break"

    def _refresh_ubs_final_tick_panel(self) -> None:
        self._safe_refresh("ubs_final_tick_reconcile", self._reconcile_ubs_final_tick_from_disk)
        for label, callback in (
            ("ubs_final_tick", self._refresh_ubs_final_tick),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _reconcile_ubs_final_tick_from_disk(self) -> None:
        """Concilia reportes OHLC/Every Tick ya generados en disco (p. ej. tras
        cortar el proceso manualmente) antes de repintar la tabla. No abre MT5."""
        if self.process and self.process.poll() is None:
            return  # hay un proceso activo escribiendo; no competir con el
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return
        from ubs_agent import reconcile_final_tick_reports
        from ubs.memory import AgentMemory
        from ubs.score import ScoreConfig
        from run_tests import parse_symbol_map

        run = self._latest_visible_ubs_run_for_final_tick()
        if run is None:
            return
        thresholds = self._ubs_final_tick_threshold_values()
        score_config = ScoreConfig(
            min_net_profit=float(self.ubs_pass_min_net_profit.get() or 100),
            min_profit_factor=float(self.ubs_pass_min_profit_factor.get() or 1.2),
            min_trades=int(float(self.ubs_pass_min_trades.get() or 50)),
            max_drawdown_pct=float(self.ubs_pass_max_drawdown_pct.get() or 25),
            min_recovery_factor=float(self.ubs_pass_min_recovery_factor.get() or 1.0),
        )
        symbol_map = parse_symbol_map(self._effective_ubs_symbol_map_text())
        memory = AgentMemory(memory_path)
        try:
            counts = reconcile_final_tick_reports(
                memory,
                int(run["id"]),
                score_config,
                symbol_map,
                broker=self._ubs_broker(),
                min_history_quality=thresholds["min_quality"],
                min_ohlc_trades=thresholds["min_ohlc_trades"],
                min_trades_w1=thresholds["min_trades_w1"],
                min_trades_mn=thresholds["min_trades_mn"],
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
                symbol_suffix=self._effective_symbol_suffix_text(),
            )
        finally:
            memory.close()
        if counts:
            resumen = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            self.status_text.set(f"Final Tick reconciliado desde disco: {resumen}")

    def _checked_ubs_final_tick_infos(self, *, fallback_selected: bool = True) -> list[dict[str, str]]:
        checked = [
            info for info in self.ubs_final_tick_paths.values()
            if info.get("id") in self.ubs_final_tick_checked
        ]
        if checked or not fallback_selected:
            return checked
        selected = self._selected_ubs_final_tick_info()
        return [selected] if selected else []

    def _manual_mark_selected_ubs_final_tick(self, status: str) -> None:
        infos = self._checked_ubs_final_tick_infos()
        ids = [info.get("id", "") for info in infos]
        if not ids:
            messagebox.showinfo("Estado manual", "Selecciona una o mas filas de Final Tick primero.")
            return
        label = "OK" if status == "accepted" else "FAIL"
        if not messagebox.askyesno(
            "Estado manual",
            f"Marcar {len(ids)} fila(s) de Final Tick como {label} manual?\n\n"
            "Si el candidato base tiene score, el peso se actualiza.",
        ):
            return
        try:
            thresholds = self._ubs_final_tick_threshold_values()
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            updated = mark_candidate_final_tick(
                conn,
                ids,
                status,
                min_history_quality=thresholds["min_quality"],
                from_date=self.ubs_final_tick_from_date.get().strip(),
                to_date=self.ubs_final_tick_to_date.get().strip(),
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, ValueError) as exc:
            self._show_error("No se pudo aplicar estado manual", str(exc))
            return
        self.ubs_final_tick_checked.clear()
        self.ubs_weights_locked.set(False)
        self.status_text.set(f"Estado manual aplicado a {updated} fila(s) de Final Tick")
        self._refresh_ubs_final_tick_panel()

    def _manual_accept_selected_ubs_final_tick(self) -> None:
        self._manual_mark_selected_ubs_final_tick("accepted")

    def _manual_reject_selected_ubs_final_tick(self) -> None:
        self._manual_mark_selected_ubs_final_tick("rejected")

    def _validated_final_tick_stage_dates(self, final_tick_stage: str) -> tuple[str, str, str, str]:
        from_date, to_date, ohlc_from_date, ohlc_to_date = self._final_tick_stage_dates(final_tick_stage)
        if not from_date or not to_date:
            raise ValueError("Final Tick requiere fechas Desde y Hasta.")
        if bool(ohlc_from_date) != bool(ohlc_to_date):
            if str(final_tick_stage).strip().lower() in {"six_month", "6m"}:
                raise ValueError("Final Tick 6M pocas ops OHLC requiere rellenar Ops bajas desde y Ops bajas hasta.")
            raise ValueError("Final Tick OHLC retry requiere rellenar OHLC desde y OHLC hasta.")
        if str(final_tick_stage).strip().lower() in {"six_month", "6m"}:
            from ubs_agent import validate_final_tick_stage_dates

            date_error = validate_final_tick_stage_dates("six_month", from_date, to_date)
            if date_error:
                raise ValueError(date_error)
            if ohlc_from_date and ohlc_to_date:
                ohlc_date_error = validate_final_tick_stage_dates("six_month", ohlc_from_date, ohlc_to_date)
                if ohlc_date_error:
                    raise ValueError(f"Final Tick 6M OHLC retry invalido: {ohlc_date_error}")
        return from_date, to_date, ohlc_from_date, ohlc_to_date

    def _ubs_final_tick_args(
        self,
        run_id: int,
        *,
        pending_only: bool = False,
        retry_pending_quality: bool = False,
        final_tick_stage: str = "probe",
    ) -> list[str]:
        from_date, to_date, ohlc_from_date, ohlc_to_date = self._validated_final_tick_stage_dates(final_tick_stage)
        output_dir = self._ubs_generation_output_dir()
        thresholds = self._ubs_final_tick_threshold_values()
        args = [
            "--source-dir", str(self._ubs_generator_source_dir()),
            "--output-dir", str(output_dir),
            "--memory", str(self._ubs_memory_path()),
            "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            "--evaluate-final-tick",
            "--final-tick-run-id", str(run_id),
            "--final-tick-stage", final_tick_stage,
            "--from-date", from_date,
            "--to-date", to_date,
            "--final-tick-min-history-quality", str(thresholds["min_quality"]),
            "--final-tick-min-ohlc-trades", str(thresholds["min_ohlc_trades"]),
            "--final-tick-min-trades-w1", str(thresholds["min_trades_w1"]),
            "--final-tick-min-trades-mn", str(thresholds["min_trades_mn"]),
            "--final-tick-max-net-delta-pct", str(thresholds["net_delta"]),
            "--final-tick-max-pf-delta-pct", str(thresholds["pf_delta"]),
            "--final-tick-max-dd-delta-pct", str(thresholds["dd_delta"]),
            "--final-tick-max-trades-delta-pct", str(thresholds["trades_delta"]),
            "--delay", str(self.delay.get()),
        ]
        if ohlc_from_date and ohlc_to_date:
            args.extend([
                "--final-tick-ohlc-from-date", ohlc_from_date,
                "--final-tick-ohlc-to-date", ohlc_to_date,
            ])
        if pending_only:
            args.append("--final-tick-pending-only")
        if retry_pending_quality:
            args.append("--final-tick-retry-pending-quality")
            args.append("--final-tick-skip-ohlc")
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
            if self.mt5_path.get().strip():
                args.extend(["--mt5-path", self.mt5_path.get()])
            if self.mt5_data_root.get().strip():
                args.extend(["--data-dir", self.mt5_data_root.get()])
        symbol_map = self._effective_ubs_symbol_map_text()
        if symbol_map:
            args.extend(["--symbol-map", symbol_map])
        args.extend(self._effective_symbol_suffix_args())
        return args

    def _pending_final_tick_rows(self, rows, final_tick_stage: str):
        rows = [
            row for row in rows
            if self._final_tick_row_pending_for_current_dates(row, final_tick_stage=final_tick_stage)
        ]
        _from_date, _to_date, ohlc_from, ohlc_to = self._final_tick_stage_dates(final_tick_stage)
        has_ohlc_retry = final_tick_stage == "six_month" and bool(ohlc_from and ohlc_to)
        has_ohlc_pending = final_tick_stage == "six_month" and any(
            str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades" for row in rows
        )
        if not (has_ohlc_retry and has_ohlc_pending):
            return rows

        def in_retry_scope(row) -> bool:
            if str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades":
                return True
            return (
                str(row["final_tick_from_date"] or "").strip() == ohlc_from
                and str(row["final_tick_to_date"] or "").strip() == ohlc_to
            )

        return [row for row in rows if in_retry_scope(row)]

    def _final_tick_execution_details(self, run_id, rows, pending_only, final_tick_stage, thresholds):
        stage_label = "Final Tick 6M" if final_tick_stage == "six_month" else "Final Tick"
        from_date, to_date, ohlc_from, ohlc_to = self._final_tick_stage_dates(final_tick_stage)
        pf_delta = min(thresholds["pf_delta"], 30.0) if final_tick_stage == "six_month" else thresholds["pf_delta"]
        details = [
            f"Accion: {'Continuar' if pending_only else 'Reprobar'} {stage_label} UBS run #{run_id}",
            f"Modo: {'pendientes + retryables' if pending_only else 'todos los elegibles, reemplaza estado existente'}",
            f"Candidatos a testear: {len(rows)}",
            f"Fechas: {from_date} -> {to_date}",
            "Modelos: OHLC Model=1 vs Every tick based on real ticks Model=4",
            f"History Quality >= {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']}",
            f"Min ops W1/MN Final Tick: W1>={thresholds['min_trades_w1']} | MN>={thresholds['min_trades_mn']}",
            ("Retry pocas ops OHLC: " if final_tick_stage == "six_month" else "Fechas retry OHLC: ")
            + f"{ohlc_from or '(mismas)'} -> {ohlc_to or '(mismas)'}",
            f"Deltas max: net {thresholds['net_delta']:.2f}% | PF {pf_delta:.2f}% | "
            f"DD {thresholds['dd_delta']:.2f}% | trades {thresholds['trades_delta']:.2f}%",
        ]
        if final_tick_stage == "six_month":
            details.append(f"6M PF minimo por modelo: >= {self.ubs_pass_min_profit_factor.get().strip() or '1.20'}")
        details.extend(self._multiterminal_execution_details())
        return details

    def _run_ubs_final_tick_for_latest_run(
        self,
        *,
        confirm: bool = True,
        auto: bool = False,
        pending_only: bool = True,
        final_tick_stage: str = "probe",
    ) -> bool:
        final_tick_stage = "six_month" if str(final_tick_stage).strip().lower() in {"six_month", "6m"} else "probe"
        stage_label = "Final Tick 6M" if final_tick_stage == "six_month" else "Final Tick"
        status_var = self.ubs_final_tick_6m_status if final_tick_stage == "six_month" else self.ubs_final_tick_status
        try:
            run = self._latest_visible_ubs_run_for_final_tick(final_tick_stage=final_tick_stage)
            if run is None:
                if not auto:
                    messagebox.showinfo("Final Tick UBS", f"No hay run visible para {stage_label}.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
            rows = [row for row in rows if resolve_workspace_path(row["set_path"]).exists()]
            if pending_only:
                rows = self._pending_final_tick_rows(rows, final_tick_stage)
            if not rows:
                if pending_only:
                    message = f"Run #{run_id} no tiene candidatos pendientes de {stage_label}."
                else:
                    message = f"Run #{run_id} no tiene candidatos elegibles con .set existente para {stage_label}."
                status_var.set(message)
                if not auto:
                    messagebox.showinfo("Final Tick UBS", message)
                return False
            thresholds = self._ubs_final_tick_threshold_values()
            args = self._ubs_final_tick_args(run_id, pending_only=pending_only, final_tick_stage=final_tick_stage)
        except Exception as exc:
            if not auto:
                self._show_error(f"No se pudo preparar {stage_label} UBS", str(exc))
            else:
                self._append_console(f"\n[{stage_label} auto] No se pudo preparar: {exc}\n", tag="error")
            return False

        details = self._final_tick_execution_details(run_id, rows, pending_only, final_tick_stage, thresholds)
        if confirm and not self._confirm_execution_start(f"Confirmar {stage_label} UBS", len(rows), details):
            return False
        if final_tick_stage == "six_month":
            self._show_section("ubs_final_tick_6m")
        else:
            self._show_section("ubs_final_tick")
        status_var.set(f"Lanzando {stage_label} run #{run_id}: {len(rows)} candidato(s)...")
        self.status_text.set(f"Preparando {stage_label} UBS")
        self._append_console(
            f"\n[{stage_label}] Lanzando run #{run_id} con {len(rows)} candidato(s).\n",
            tag="info",
        )
        self.after(10, lambda: self._run_script("ubs_agent.py", args))
        return True

    def _rerun_ubs_final_tick_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(pending_only=False)

    def _run_ubs_final_tick_6m_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(pending_only=True, final_tick_stage="six_month")

    def _rerun_ubs_final_tick_6m_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(pending_only=False, final_tick_stage="six_month")

    def _maybe_auto_run_ubs_final_tick(self, script_name: str, args: list[str], code: int) -> bool:
        """Encadena robustez -> Final Tick corto y Final Tick corto -> 6M."""
        if code != 0 or script_name != "ubs_agent.py":
            return False
        if "--evaluate-robustness" in args:
            if not self.ubs_final_tick_auto.get():
                return False
            self._append_console("\n[Final Tick auto] Lanzando Final Tick sobre robust accepted pendientes.\n", tag="info")
            return self._run_ubs_final_tick_for_latest_run(confirm=False, auto=True, pending_only=True)
        if "--evaluate-final-tick" not in args:
            return False
        if not self.ubs_final_tick_6m_auto.get():
            return False
        stage = "probe"
        if "--final-tick-stage" in args:
            index = args.index("--final-tick-stage")
            if index + 1 < len(args):
                stage = str(args[index + 1]).strip().lower().replace("-", "_")
        if stage in {"six_month", "6m", "sixmonth"}:
            return False
        self._append_console("\n[Final Tick 6M auto] Lanzando Final Tick 6M sobre corto accepted/pend. OHLC.\n", tag="info")
        return self._run_ubs_final_tick_for_latest_run(
            confirm=False,
            auto=True,
            pending_only=True,
            final_tick_stage="six_month",
        )

    def _retry_ubs_final_tick_pending_quality(self, *, final_tick_stage: str = "probe") -> bool:
        """Re-run only rows with status=pending_history_quality, ignoring stored dates."""
        final_tick_stage = "six_month" if str(final_tick_stage).strip().lower() in {"six_month", "6m"} else "probe"
        stage_label = "Final Tick 6M" if final_tick_stage == "six_month" else "Final Tick"
        section_key = "ubs_final_tick_6m" if final_tick_stage == "six_month" else "ubs_final_tick"
        status_var = self.ubs_final_tick_6m_status if final_tick_stage == "six_month" else self.ubs_final_tick_status
        try:
            run = self._latest_visible_ubs_run_for_final_tick(final_tick_stage=final_tick_stage)
            if run is None:
                messagebox.showinfo("Final Tick UBS", f"No hay run visible para {stage_label}.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
            rows = [
                row for row in rows
                if resolve_workspace_path(row["set_path"]).exists()
                and str(row["final_tick_status"] or "").strip() == "pending_history_quality"
            ]
            if not rows:
                msg = f"Run #{run_id}: no hay filas con calidad pendiente en {stage_label}."
                status_var.set(msg)
                messagebox.showinfo("Final Tick UBS", msg)
                return False
            thresholds = self._ubs_final_tick_threshold_values()
            args = self._ubs_final_tick_args(
                run_id,
                pending_only=True,
                retry_pending_quality=True,
                final_tick_stage=final_tick_stage,
            )
        except Exception as exc:
            self._show_error(f"No se pudo preparar reintentar calidad baja {stage_label}", str(exc))
            return False

        from_date, to_date, _ohlc_from_date, _ohlc_to_date = self._final_tick_stage_dates(final_tick_stage)
        details = [
            f"Accion: Reintentar calidad baja {stage_label} - run #{run_id}",
            "Modo: solo filas pending_history_quality (ignora si las fechas coinciden o no)",
            f"Candidatos a reintentar: {len(rows)}",
            f"Fechas: {from_date} -> {to_date}",
            f"History Quality minima requerida: {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']}",
            f"Min ops W1/MN Final Tick: W1>={thresholds['min_trades_w1']} | MN>={thresholds['min_trades_mn']}",
        ]
        details.extend(self._multiterminal_execution_details())
        if not self._confirm_execution_start(f"Confirmar reintentar calidad baja {stage_label}", len(rows), details):
            return False
        self._show_section(section_key)
        self._run_script("ubs_agent.py", args)
        return True

    def _retry_ubs_final_tick_6m_pending_quality(self) -> bool:
        return self._retry_ubs_final_tick_pending_quality(final_tick_stage="six_month")

    def _ubs_final_tick_run_rows(self, conn: sqlite3.Connection, run_id: int) -> list:
        """Candidatos robustos del run con su resultado de Final Tick corto."""
        return conn.execute(
            """
            select
                c.id, c.run_id, c.generation, c.target_symbol, c.symbol, c.period,
                c.set_path,
                ft.status as final_status,
                ft.ohlc_report_path,
                ft.real_tick_report_path,
                ft.ohlc_score,
                ft.real_tick_score,
                ft.ohlc_metrics_json,
                ft.real_tick_metrics_json,
                ft.similarity_json,
                ft.history_quality,
                ft.min_history_quality,
                ft.from_date,
                ft.to_date,
                ft.evaluated_at
            from candidates c
            join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted'
            order by
                case
                    when ft.status='accepted' then 0
                    when ft.status='rejected' then 1
                    when ft.status is null then 2
                    else 3
                end,
                ft.real_tick_score desc,
                c.id desc
            """,
            (run_id,),
        ).fetchall()

    def _load_ubs_final_tick_rows(self):
        """Run visible y sus filas; None si no hay memoria, run o la consulta falla."""
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_final_tick_summary.set("Final Tick: sin memoria UBS")
            self.ubs_final_tick_status.set(f"No existe memoria: {memory_path}")
            return None
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run_options = self._ubs_final_tick_run_options(conn)
            selected_run_id = self._selected_ubs_final_tick_run_id(run_options)
            self._update_ubs_final_tick_run_combo(run_options, selected_run_id)
            run = (
                conn.execute("select * from runs where id=?", (selected_run_id,)).fetchone()
                if selected_run_id > 0
                else None
            )
            if run is None:
                conn.close()
                self.ubs_final_tick_summary.set("Final Tick: sin run visible")
                self.ubs_final_tick_status.set("Limpiaste la vista de resultados; el historico conserva la memoria.")
                return None
            rows = self._ubs_final_tick_run_rows(conn, run["id"])
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_final_tick_summary.set("Final Tick: error SQLite")
            self.ubs_final_tick_status.set(str(exc))
            return None
        return run, rows

    def _set_ubs_final_tick_summary(self, run, rows: list) -> None:
        """Cabecera del panel: resueltos, pendientes y fechas configuradas."""
        total = len(rows)
        accepted = sum(1 for row in rows if row["final_status"] == "accepted")
        rejected = sum(1 for row in rows if row["final_status"] == "rejected")
        settled = accepted + rejected
        self.ubs_final_tick_summary.set(
            f"Run #{run['id']} | robust accepted {total} | final corto resueltos {settled} | OK {accepted} | FAIL {rejected}"
        )
        self.ubs_final_tick_status.set(
            f"Pendientes/neutros: {total - settled} | Fechas config: "
            f"{self.ubs_final_tick_from_date.get().strip()} -> {self.ubs_final_tick_to_date.get().strip()}"
        )

    def _ubs_final_tick_row_values(self, row, status: str, cid: str) -> tuple:
        """Columnas de una fila del arbol de Final Tick corto."""
        similarity = self._parse_ubs_final_tick_similarity(row["similarity_json"])
        date_range = ""
        if row["from_date"] or row["to_date"]:
            date_range = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}"
        return (
            self._checkbox_text(cid in self.ubs_final_tick_checked),
            row["run_id"],
            row["id"],
            row["generation"],
            self._format_ubs_status(status),
            self._ubs_final_tick_reason(
                status,
                similarity,
                history_quality=row["history_quality"],
                min_history_quality=row["min_history_quality"],
            ),
            row["target_symbol"] or row["symbol"],
            row["period"],
            f"{self._format_ubs_number(row['history_quality'])}%" if row["history_quality"] is not None else "",
            self._format_ubs_number(row["ohlc_score"]),
            self._format_ubs_number(row["real_tick_score"]),
            self._format_ubs_number(self._metric_from_json(row["ohlc_metrics_json"], "net_profit")),
            self._format_ubs_number(self._metric_from_json(row["real_tick_metrics_json"], "net_profit")),
            self._format_ubs_number(self._metric_from_json(row["ohlc_metrics_json"], "profit_factor")),
            self._format_ubs_number(self._metric_from_json(row["real_tick_metrics_json"], "profit_factor")),
            self._format_ubs_number(self._metric_from_json(row["ohlc_metrics_json"], "drawdown_pct")),
            self._format_ubs_number(self._metric_from_json(row["real_tick_metrics_json"], "drawdown_pct")),
            self._format_ubs_int(self._metric_from_json(row["ohlc_metrics_json"], "trades")),
            self._format_ubs_int(self._metric_from_json(row["real_tick_metrics_json"], "trades")),
            date_range,
            Path(str(row["set_path"] or "")).name,
        )

    def _fill_ubs_final_tick_tree(self, rows: list) -> set[str]:
        """Pinta las filas y devuelve los candidatos que siguen visibles."""
        valid_ids: set[str] = set()
        for index, row in enumerate(rows):
            status = str(row["final_status"] or "pending")
            cid = str(row["id"] or "")
            valid_ids.add(cid)
            item = self.ubs_final_tick_tree.insert(
                "",
                "end",
                values=self._ubs_final_tick_row_values(row, status, cid),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            self.ubs_final_tick_paths[item] = {
                "id": cid,
                "set": str(row["set_path"] or ""),
                "ohlc_report": str(row["ohlc_report_path"] or ""),
                "real_report": str(row["real_tick_report_path"] or ""),
                "status": status,
            }
        return valid_ids

    def _refresh_ubs_final_tick(self) -> None:
        if hasattr(self, "ubs_final_tick_tree"):
            for item in self.ubs_final_tick_tree.get_children():
                self.ubs_final_tick_tree.delete(item)
        self.ubs_final_tick_paths.clear()
        loaded = self._load_ubs_final_tick_rows()
        if loaded is None:
            return
        run, rows = loaded
        self._set_ubs_final_tick_summary(run, rows)
        if not hasattr(self, "ubs_final_tick_tree"):
            return
        self.ubs_final_tick_checked.intersection_update(self._fill_ubs_final_tick_tree(rows))
