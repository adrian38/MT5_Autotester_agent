from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_final_tick


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent

FINAL_TICK_RETRYABLE_STATUSES = {
    "no_report",
    "parse_error",
    "report_mismatch",
}
FINAL_TICK_DATE_RETRYABLE_STATUSES = {
    "pending_history_quality",
    "pending_ohlc_trades",
}


class UBSFinalTickLogicMixin:
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
        symbol_map = {}
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            symbol_map = parse_symbol_map(self.symbol_map.get().strip())
        memory = AgentMemory(memory_path)
        try:
            counts = reconcile_final_tick_reports(
                memory,
                int(run["id"]),
                score_config,
                symbol_map,
                min_history_quality=thresholds["min_quality"],
                min_ohlc_trades=thresholds["min_ohlc_trades"],
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
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

    def _ubs_final_tick_threshold_values(self) -> dict[str, float]:
        return {
            "min_quality": self._score_float(
                self.ubs_final_tick_min_history_quality,
                "Final Tick calidad minima",
                minimum=0.0,
                maximum=100.0,
            ),
            "min_ohlc_trades": int(
                self._score_float(
                    self.ubs_final_tick_min_ohlc_trades,
                    "Final Tick min ops OHLC",
                    minimum=0.0,
                )
            ),
            "net_delta": self._score_float(
                self.ubs_final_tick_max_net_delta_pct,
                "Final Tick delta net",
                minimum=0.0,
            ),
            "pf_delta": self._score_float(
                self.ubs_final_tick_max_pf_delta_pct,
                "Final Tick delta PF",
                minimum=0.0,
            ),
            "dd_delta": self._score_float(
                self.ubs_final_tick_max_dd_delta_pct,
                "Final Tick delta DD",
                minimum=0.0,
            ),
            "trades_delta": self._score_float(
                self.ubs_final_tick_max_trades_delta_pct,
                "Final Tick delta trades",
                minimum=0.0,
            ),
        }

    def _ubs_final_tick_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                r.hidden,
                count(c.id) as total,
                sum(case when c.status='accepted' and cr.status='accepted' then 1 else 0 end) as robust_ok,
                sum(case when ft.status in ('accepted', 'rejected') then 1 else 0 end) as final_done,
                sum(case when ft.status='accepted' then 1 else 0 end) as final_ok,
                sum(case when ft.status='rejected' then 1 else 0 end) as final_fail
            from runs r
            left join candidates c on c.run_id = r.id
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            robust_ok = int(row["robust_ok"] or 0)
            final_done = int(row["final_done"] or 0)
            final_ok = int(row["final_ok"] or 0)
            final_fail = int(row["final_fail"] or 0)
            hidden_tag = " [arch]" if row["hidden"] else ""
            options.append((
                run_id,
                f"#{run_id} | {created} | cand {total} | robust {robust_ok} | FT {final_done} OK {final_ok} FAIL {final_fail}{hidden_tag}",
            ))
        return options

    def _selected_ubs_final_tick_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_final_tick_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_final_tick_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_final_tick_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _ in options):
                return run_id
        return newest_run_id

    def _update_ubs_final_tick_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_final_tick_run_combo"):
            return
        labels = [label for _, label in options]
        self.ubs_final_tick_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_final_tick_run_id.get() != selected_label:
            self.ubs_final_tick_run_id.set(selected_label)

    def _latest_visible_ubs_run_for_final_tick(self) -> sqlite3.Row | None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return None
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            selected = self.ubs_final_tick_run_id.get().strip()
            match = re.search(r"#?(\d+)", selected)
            if match:
                run = conn.execute("select * from runs where id=?", (int(match.group(1)),)).fetchone()
                if run is not None:
                    return run
            return conn.execute("select * from runs where hidden=0 order by id desc limit 1").fetchone()
        finally:
            conn.close()

    def _accepted_candidates_for_final_tick(self, run_id: int) -> list[sqlite3.Row]:
        memory_path = self._ubs_memory_path()
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            return conn.execute(
                """
                select
                    c.*,
                    ft.status as final_tick_status,
                    ft.from_date as final_tick_from_date,
                    ft.to_date as final_tick_to_date
                from candidates c
                join candidate_robustness cr on cr.candidate_id = c.id
                left join candidate_final_tick ft on ft.candidate_id = c.id
                where c.run_id=? and c.status='accepted' and cr.status='accepted'
                order by c.generation, c.id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()

    def _final_tick_effective_dates_for_row(self, row: sqlite3.Row) -> tuple[str, str]:
        status = str(row["final_tick_status"] or "").strip()
        ohlc_from = self.ubs_final_tick_ohlc_from_date.get().strip()
        ohlc_to = self.ubs_final_tick_ohlc_to_date.get().strip()
        if status == "pending_ohlc_trades" and ohlc_from and ohlc_to:
            return ohlc_from, ohlc_to
        return self.ubs_final_tick_from_date.get().strip(), self.ubs_final_tick_to_date.get().strip()

    def _final_tick_row_dates_match(self, row: sqlite3.Row) -> bool:
        stored_from = str(row["final_tick_from_date"] or "").strip()
        stored_to = str(row["final_tick_to_date"] or "").strip()
        if not stored_from and not stored_to:
            return False
        from_date, to_date = self._final_tick_effective_dates_for_row(row)
        return stored_from == from_date and stored_to == to_date

    def _final_tick_row_pending_for_current_dates(self, row: sqlite3.Row) -> bool:
        status = str(row["final_tick_status"] or "").strip()
        if not status:
            return True
        if status in FINAL_TICK_RETRYABLE_STATUSES:
            return True
        if status in FINAL_TICK_DATE_RETRYABLE_STATUSES:
            return not self._final_tick_row_dates_match(row)
        return False

    def _ubs_final_tick_args(self, run_id: int, *, pending_only: bool = False, retry_pending_quality: bool = False) -> list[str]:
        from_date = self.ubs_final_tick_from_date.get().strip()
        to_date = self.ubs_final_tick_to_date.get().strip()
        if not from_date or not to_date:
            raise ValueError("Final Tick requiere fechas Desde y Hasta.")
        ohlc_from_date = self.ubs_final_tick_ohlc_from_date.get().strip()
        ohlc_to_date = self.ubs_final_tick_ohlc_to_date.get().strip()
        if bool(ohlc_from_date) != bool(ohlc_to_date):
            raise ValueError("Final Tick OHLC retry requiere rellenar OHLC desde y OHLC hasta.")
        output_dir = self._ubs_generation_output_dir()
        thresholds = self._ubs_final_tick_threshold_values()
        args = [
            "--source-dir", str(self._ubs_generator_source_dir()),
            "--output-dir", str(output_dir),
            "--memory", str(self._ubs_memory_path()),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            "--evaluate-final-tick",
            "--final-tick-run-id", str(run_id),
            "--from-date", from_date,
            "--to-date", to_date,
            "--final-tick-min-history-quality", str(thresholds["min_quality"]),
            "--final-tick-min-ohlc-trades", str(thresholds["min_ohlc_trades"]),
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
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        return args

    def _run_ubs_final_tick_for_latest_run(
        self,
        *,
        confirm: bool = True,
        auto: bool = False,
        pending_only: bool = True,
    ) -> bool:
        try:
            run = self._latest_visible_ubs_run_for_final_tick()
            if run is None:
                if not auto:
                    messagebox.showinfo("Final Tick UBS", "No hay run visible para Final Tick.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_final_tick(run_id)
            rows = [row for row in rows if Path(row["set_path"]).exists()]
            if pending_only:
                rows = [
                    row for row in rows
                    if self._final_tick_row_pending_for_current_dates(row)
                ]
                ohlc_from = self.ubs_final_tick_ohlc_from_date.get().strip()
                ohlc_to = self.ubs_final_tick_ohlc_to_date.get().strip()
                has_ohlc_retry = bool(ohlc_from and ohlc_to)

                def _row_in_retry_scope(row) -> bool:
                    # pending_ohlc_trades o filas ya registradas con el rango retry
                    # (p. ej. mismatch durante un retry): van con las fechas retry.
                    if str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades":
                        return True
                    return (
                        has_ohlc_retry
                        and str(row["final_tick_from_date"] or "").strip() == ohlc_from
                        and str(row["final_tick_to_date"] or "").strip() == ohlc_to
                    )

                has_ohlc_pending = any(
                    str(row["final_tick_status"] or "").strip() == "pending_ohlc_trades"
                    for row in rows
                )
                if has_ohlc_retry and has_ohlc_pending:
                    rows = [row for row in rows if _row_in_retry_scope(row)]
            if not rows:
                if pending_only:
                    message = f"Run #{run_id} no tiene robust accepted pendientes de Final Tick."
                else:
                    message = f"Run #{run_id} no tiene candidatos robust accepted con .set existente."
                self.ubs_final_tick_status.set(message)
                if not auto:
                    messagebox.showinfo("Final Tick UBS", message)
                return False
            thresholds = self._ubs_final_tick_threshold_values()
            args = self._ubs_final_tick_args(run_id, pending_only=pending_only)
        except Exception as exc:
            if not auto:
                self._show_error("No se pudo preparar Final Tick UBS", str(exc))
            else:
                self._append_console(f"\n[Final Tick auto] No se pudo preparar: {exc}\n", tag="error")
            return False

        details = [
            f"Accion: {'Continuar Final Tick UBS' if pending_only else 'Reprobar Final Tick UBS'} run #{run_id}",
            f"Modo: {'robust accepted sin Final Tick + retryables' if pending_only else 'todos los robust accepted, reemplaza Final Tick existente'}",
            f"Candidatos robust accepted a testear: {len(rows)}",
            f"Fechas: {self.ubs_final_tick_from_date.get().strip()} -> {self.ubs_final_tick_to_date.get().strip()}",
            "Modelos: OHLC Model=1 vs Every tick based on real ticks Model=4",
            f"History Quality >= {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']}",
            (
                "Fechas retry OHLC: "
                f"{self.ubs_final_tick_ohlc_from_date.get().strip() or '(mismas)'} -> "
                f"{self.ubs_final_tick_ohlc_to_date.get().strip() or '(mismas)'}"
            ),
            (
                f"Deltas max: net {thresholds['net_delta']:.2f}% | PF {thresholds['pf_delta']:.2f}% | "
                f"DD {thresholds['dd_delta']:.2f}% | trades {thresholds['trades_delta']:.2f}%"
            ),
        ]
        details.extend(self._multiterminal_execution_details())
        if confirm and not self._confirm_execution_start("Confirmar Final Tick UBS", len(rows), details):
            return False
        self._show_section("ubs_final_tick")
        self.ubs_final_tick_status.set(f"Lanzando Final Tick run #{run_id}: {len(rows)} candidato(s)...")
        self.status_text.set("Preparando Final Tick UBS")
        self._append_console(
            f"\n[Final Tick] Lanzando run #{run_id} con {len(rows)} candidato(s).\n",
            tag="info",
        )
        self.after(10, lambda: self._run_script("ubs_agent.py", args))
        return True

    def _rerun_ubs_final_tick_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(pending_only=False)

    def _maybe_auto_run_ubs_final_tick(self, script_name: str, args: list[str], code: int) -> bool:
        """Encadena robustez -> Final Tick: al terminar una evaluacion de
        robustez OOS con exito y con el toggle Auto Final Tick activo, lanza
        Final Tick sobre los robust accepted pendientes."""
        if code != 0 or script_name != "ubs_agent.py" or not self.ubs_final_tick_auto.get():
            return False
        if "--evaluate-robustness" not in args:
            return False
        self._append_console("\n[Final Tick auto] Lanzando Final Tick sobre robust accepted pendientes.\n", tag="info")
        return self._run_ubs_final_tick_for_latest_run(confirm=False, auto=True, pending_only=True)

    def _retry_ubs_final_tick_pending_quality(self) -> bool:
        """Re-run only rows with status=pending_history_quality, ignoring stored dates."""
        try:
            run = self._latest_visible_ubs_run_for_final_tick()
            if run is None:
                messagebox.showinfo("Final Tick UBS", "No hay run visible para Final Tick.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_final_tick(run_id)
            rows = [
                row for row in rows
                if Path(row["set_path"]).exists()
                and str(row["final_tick_status"] or "").strip() == "pending_history_quality"
            ]
            if not rows:
                msg = f"Run #{run_id}: no hay filas con calidad pendiente (pending_history_quality)."
                self.ubs_final_tick_status.set(msg)
                messagebox.showinfo("Final Tick UBS", msg)
                return False
            thresholds = self._ubs_final_tick_threshold_values()
            args = self._ubs_final_tick_args(run_id, pending_only=True, retry_pending_quality=True)
        except Exception as exc:
            self._show_error("No se pudo preparar reintentar calidad baja", str(exc))
            return False

        details = [
            f"Accion: Reintentar calidad baja — run #{run_id}",
            "Modo: solo filas pending_history_quality (ignora si las fechas coinciden o no)",
            f"Candidatos a reintentar: {len(rows)}",
            f"Fechas: {self.ubs_final_tick_from_date.get().strip()} -> {self.ubs_final_tick_to_date.get().strip()}",
            f"History Quality minima requerida: {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']}",
        ]
        details.extend(self._multiterminal_execution_details())
        if not self._confirm_execution_start("Confirmar reintentar calidad baja", len(rows), details):
            return False
        self._show_section("ubs_final_tick")
        self._run_script("ubs_agent.py", args)
        return True

    def _parse_ubs_final_tick_similarity(self, raw) -> dict:
        try:
            data = json.loads(str(raw or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ubs_final_tick_reason(self, status: str, similarity: dict) -> str:
        if status == "pending":
            return "pendiente"
        if status == "pending_history_quality":
            quality = similarity.get("history_quality")
            minimum = similarity.get("min_history_quality")
            return f"calidad pendiente: {self._format_ubs_number(quality)}% < {self._format_ubs_number(minimum)}%"
        if status == "pending_ohlc_trades":
            checks = similarity.get("checks") if isinstance(similarity.get("checks"), dict) else {}
            check = checks.get("ohlc_trades", {}) if isinstance(checks, dict) else {}
            trades = check.get("ohlc") if isinstance(check, dict) else None
            minimum = check.get("min_trades") if isinstance(check, dict) else None
            return f"OHLC pendiente: {self._format_ubs_int(trades)} ops < {self._format_ubs_int(minimum)}"
        if status == "no_report":
            return "sin reporte OHLC o real tick"
        if status == "parse_error":
            return "error al parsear reporte"
        if status == "report_mismatch":
            return "mismatch symbol/TF"
        if status == "no_trades":
            return "sin operaciones en el tramo"
        reasons = similarity.get("reasons") or []
        if not reasons:
            return ""
        checks = similarity.get("checks") if isinstance(similarity.get("checks"), dict) else {}
        parts: list[str] = []
        for reason in reasons:
            if reason == "history_quality":
                quality = similarity.get("history_quality")
                minimum = similarity.get("min_history_quality")
                parts.append(
                    f"calidad: {self._format_ubs_number(quality)}% <= {self._format_ubs_number(minimum)}%"
                )
                continue
            if reason == "ohlc_trades":
                check = checks.get("ohlc_trades", {}) if isinstance(checks, dict) else {}
                trades = check.get("ohlc") if isinstance(check, dict) else None
                minimum = check.get("min_trades") if isinstance(check, dict) else None
                parts.append(f"OHLC ops: {self._format_ubs_int(trades)} < {self._format_ubs_int(minimum)}")
                continue
            check = checks.get(str(reason), {}) if isinstance(checks, dict) else {}
            delta = check.get("delta_pct") if isinstance(check, dict) else None
            maximum = check.get("max_delta_pct") if isinstance(check, dict) else None
            labels = {
                "net_profit": "net",
                "profit_factor": "PF",
                "drawdown_pct": "DD",
                "trades": "trades",
            }
            label = labels.get(str(reason), str(reason))
            if delta is None:
                parts.append(label)
            else:
                parts.append(f"{label}: {self._format_ubs_number(delta)}% > {self._format_ubs_number(maximum)}%")
        return " | ".join(parts)

    def _metric_from_json(self, raw, key: str):
        metrics = self._parse_ubs_metrics(raw)
        return metrics.get(key)

    def _refresh_ubs_final_tick(self) -> None:
        if hasattr(self, "ubs_final_tick_tree"):
            for item in self.ubs_final_tick_tree.get_children():
                self.ubs_final_tick_tree.delete(item)
        self.ubs_final_tick_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_final_tick_summary.set("Final Tick: sin memoria UBS")
            self.ubs_final_tick_status.set(f"No existe memoria: {memory_path}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run_options = self._ubs_final_tick_run_options(conn)
            selected_run_id = self._selected_ubs_final_tick_run_id(run_options)
            self._update_ubs_final_tick_run_combo(run_options, selected_run_id)
            if selected_run_id <= 0:
                conn.close()
                self.ubs_final_tick_summary.set("Final Tick: sin run visible")
                self.ubs_final_tick_status.set("Limpiaste la vista de resultados; el historico conserva la memoria.")
                return
            run = conn.execute("select * from runs where id=?", (selected_run_id,)).fetchone()
            if run is None:
                conn.close()
                self.ubs_final_tick_summary.set("Final Tick: sin run visible")
                self.ubs_final_tick_status.set("Limpiaste la vista de resultados; el historico conserva la memoria.")
                return
            rows = conn.execute(
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
                (run["id"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_final_tick_summary.set("Final Tick: error SQLite")
            self.ubs_final_tick_status.set(str(exc))
            return

        total = len(rows)
        accepted = sum(1 for row in rows if row["final_status"] == "accepted")
        rejected = sum(1 for row in rows if row["final_status"] == "rejected")
        settled = accepted + rejected
        neutral = total - settled
        self.ubs_final_tick_summary.set(
            f"Run #{run['id']} | robust accepted {total} | final resueltos {settled} | OK {accepted} | FAIL {rejected}"
        )
        self.ubs_final_tick_status.set(
            f"Pendientes/neutros: {neutral} | Fechas config: {self.ubs_final_tick_from_date.get().strip()} -> {self.ubs_final_tick_to_date.get().strip()}"
        )
        if not hasattr(self, "ubs_final_tick_tree"):
            return

        valid_ids: set[str] = set()
        for index, row in enumerate(rows):
            status = str(row["final_status"] or "pending")
            similarity = self._parse_ubs_final_tick_similarity(row["similarity_json"])
            date_range = ""
            if row["from_date"] or row["to_date"]:
                date_range = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}"
            cid = str(row["id"] or "")
            valid_ids.add(cid)
            item = self.ubs_final_tick_tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(cid in self.ubs_final_tick_checked),
                    row["run_id"],
                    row["id"],
                    row["generation"],
                    self._format_ubs_status(status),
                    self._ubs_final_tick_reason(status, similarity),
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
                ),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            self.ubs_final_tick_paths[item] = {
                "id": cid,
                "set": str(row["set_path"] or ""),
                "ohlc_report": str(row["ohlc_report_path"] or ""),
                "real_report": str(row["real_tick_report_path"] or ""),
                "status": status,
            }
        self.ubs_final_tick_checked.intersection_update(valid_ids)

    def _selected_ubs_final_tick_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_final_tick_tree"):
            return {}
        selected = self.ubs_final_tick_tree.selection()
        if not selected:
            return {}
        return self.ubs_final_tick_paths.get(selected[0], {})

    def _selected_ubs_final_tick_path(self, kind: str) -> Path | None:
        info = self._selected_ubs_final_tick_info()
        raw_path = info.get(kind, "")
        return Path(raw_path).expanduser() if raw_path else None

    def _open_selected_ubs_final_tick_set(self) -> None:
        path = self._selected_ubs_final_tick_path("set")
        if path is None:
            messagebox.showinfo("Final Tick UBS", "Selecciona una fila primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_final_tick_ohlc_report(self) -> None:
        path = self._selected_ubs_final_tick_path("ohlc_report")
        if path is None:
            messagebox.showinfo("Final Tick UBS", "Esa fila no tiene reporte OHLC asociado.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_final_tick_real_report(self) -> None:
        path = self._selected_ubs_final_tick_path("real_report")
        if path is None:
            messagebox.showinfo("Final Tick UBS", "Esa fila no tiene reporte real tick asociado.")
            return
        self._open_local_file(path)
