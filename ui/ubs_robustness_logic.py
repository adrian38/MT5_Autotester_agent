from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_robustness
from ubs.path_utils import resolve_workspace_path


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSRobustnessLogicMixin:
    def _capture_ubs_robust_tree_state(self) -> dict[str, object]:
        if not hasattr(self, "ubs_robust_tree"):
            return {}
        tree = self.ubs_robust_tree
        top_visible_id = ""
        for item in tree.get_children():
            if tree.bbox(item):
                top_visible_id = str(self.ubs_robust_paths.get(item, {}).get("id") or "")
                break
        selected_ids = [
            str(self.ubs_robust_paths.get(item, {}).get("id") or "")
            for item in tree.selection()
        ]
        return {
            "xview": tree.xview(),
            "yview": tree.yview(),
            "focus_id": str(self.ubs_robust_paths.get(tree.focus(), {}).get("id") or ""),
            "selected_ids": {cid for cid in selected_ids if cid},
            "top_visible_id": top_visible_id,
        }

    def _restore_ubs_robust_tree_state(self, state: dict[str, object], item_by_id: dict[str, str]) -> None:
        if not state or not hasattr(self, "ubs_robust_tree"):
            return

        def _restore() -> None:
            if not hasattr(self, "ubs_robust_tree"):
                return
            tree = self.ubs_robust_tree
            try:
                xview = state.get("xview")
                if isinstance(xview, tuple) and xview:
                    tree.xview_moveto(float(xview[0]))

                selected_items = [
                    item_by_id[cid]
                    for cid in state.get("selected_ids", set())
                    if isinstance(cid, str) and cid in item_by_id
                ]
                focus_id = state.get("focus_id")
                focus_item = item_by_id.get(focus_id) if isinstance(focus_id, str) else None
                if selected_items:
                    tree.selection_set(selected_items)
                    tree.focus(focus_item or selected_items[0])

                anchor_item = focus_item or (selected_items[0] if selected_items else None)
                if not anchor_item:
                    top_visible_id = state.get("top_visible_id")
                    anchor_item = item_by_id.get(top_visible_id) if isinstance(top_visible_id, str) else None
                if anchor_item:
                    children = list(tree.get_children())
                    if children:
                        try:
                            tree.yview_moveto(children.index(anchor_item) / max(len(children), 1))
                        except ValueError:
                            pass
                else:
                    yview = state.get("yview")
                    if isinstance(yview, tuple) and yview:
                        tree.yview_moveto(float(yview[0]))
            except Exception:
                pass

        self.ubs_robust_tree.after_idle(_restore)

    def _on_ubs_robust_tree_click(self, event) -> str | None:
        if not hasattr(self, "ubs_robust_tree"):
            return None
        item, column = self._tree_item_from_event(self.ubs_robust_tree, event)
        if not item or column != "#1":
            return None
        info = self.ubs_robust_paths.get(item, {})
        cid = info.get("id", item)
        if cid in self.ubs_robust_checked:
            self.ubs_robust_checked.remove(cid)
        else:
            self.ubs_robust_checked.add(cid)
        values = list(self.ubs_robust_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(cid in self.ubs_robust_checked)
            self.ubs_robust_tree.item(item, values=values)
        return "break"

    def _refresh_ubs_robustness_panel(self) -> None:
        for label, callback in (
            ("ubs_robustness", self._refresh_ubs_robustness),
            ("ubs_final_tick", self._refresh_ubs_final_tick),
            ("ubs_final_tick_6m", self._refresh_ubs_final_tick_6m),
            ("ubs_regression", self._refresh_ubs_regression),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _checked_ubs_robust_infos(self, *, fallback_selected: bool = True) -> list[dict[str, str]]:
        checked = [
            info for info in self.ubs_robust_paths.values()
            if info.get("id") in self.ubs_robust_checked
        ]
        if checked or not fallback_selected:
            return checked
        selected = self._selected_ubs_robust_info()
        return [selected] if selected else []

    def _manual_mark_selected_ubs_robust(self, status: str) -> None:
        infos = self._checked_ubs_robust_infos()
        ids = [info.get("id", "") for info in infos]
        if not ids:
            messagebox.showinfo("Estado manual", "Selecciona una o mas filas de robustez primero.")
            return
        label = "OK" if status == "accepted" else "FAIL"
        if not messagebox.askyesno(
            "Estado manual",
            f"Marcar {len(ids)} fila(s) de robustez como {label} manual?\n\n"
            "Si la fila base tiene score, el peso se actualiza. OK la deja disponible para Final Tick.",
        ):
            return
        try:
            positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            updated = mark_candidate_robustness(
                conn,
                ids,
                status,
                from_date=self.ubs_robust_from_date.get().strip(),
                to_date=self.ubs_robust_to_date.get().strip(),
                positive_bonus=positive_bonus,
                negative_bonus=negative_bonus,
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, ValueError) as exc:
            self._show_error("No se pudo aplicar estado manual", str(exc))
            return
        self.ubs_robust_checked.clear()
        self.ubs_weights_locked.set(False)
        self.status_text.set(f"Estado manual aplicado a {updated} fila(s) de robustez")
        self._refresh_ubs_robustness_panel()

    def _manual_accept_selected_ubs_robust(self) -> None:
        self._manual_mark_selected_ubs_robust("accepted")

    def _manual_reject_selected_ubs_robust(self) -> None:
        self._manual_mark_selected_ubs_robust("rejected")

    def _robustness_bonus_for_status(self, status: str, positive: object, negative: object) -> float | None:
        try:
            if status == "accepted":
                return float(positive or 0.0)
            if status == "rejected":
                return float(negative or 0.0)
        except (TypeError, ValueError):
                return None
        return None

    def _format_ubs_robustness_status(self, status: str) -> str:
        if status == "no_trades":
            return "0 ops/no aceptado"
        return self._format_ubs_status(status)

    def _ubs_robust_reason(self, status: str, metrics: dict, degradation: dict | None = None) -> str:
        if status == "pending":
            return "pendiente"
        if status == "no_report":
            return "sin reporte OOS"
        if status == "parse_error":
            return "error al parsear reporte OOS"
        if status == "report_mismatch":
            return "mismatch symbol/TF OOS"
        if isinstance(degradation, dict) and degradation.get("failure_type") == "invalid_stops":
            count = int(degradation.get("invalid_order_count") or 0)
            prefix = f"{count} orden(es) rechazada(s)" if count else "ordenes rechazadas"
            return f"{prefix} por stops invalidos; no pasa robustez"
        if status == "no_trades":
            return "reporte correcto, 0 operaciones; no pasa robustez"
        reasons = metrics.get("reasons") or []
        if not reasons:
            return ""
        formats = {
            "net_profit": ("net norm", ".0f", ""),
            "profit_factor": ("PF", ".2f", ""),
            "trades": ("trades", "d", ""),
            "drawdown_pct": ("DD", ".1f", "%"),
            "recovery_factor": ("RF", ".2f", ""),
            "positive_month_ratio": ("meses+", ".0%", ""),
        }
        parts: list[str] = []
        checks = degradation.get("checks", {}) if isinstance(degradation, dict) else {}
        degradation_labels = {
            "degradation_net": ("net retenido", "net_retention", "percent"),
            "degradation_profit_factor": ("edge PF retenido", "pf_edge_retention", "percent"),
            "degradation_recovery": ("recovery anual retenido", "recovery_retention", "percent"),
            "degradation_drawdown": ("inflacion DD", "dd_inflation", "ratio"),
            "degradation_trade_rate": ("ritmo trades", "trade_rate_retention", "percent"),
            "generalization_residual_profit": ("neto sin top3", "residual_profit_ratio", "percent"),
            "generalization_month_breadth": ("meses OOS+", "oos_positive_month_ratio", "percent"),
            "generalization_stability": ("estabilidad OOS", "trade_curve_stability", "decimal"),
            "generalization_stability_retention": ("estabilidad retenida", "stability_retention", "percent"),
            "generalization_bootstrap_net": (
                "P(neto>0) bootstrap",
                "bootstrap_net_positive_probability",
                "percent",
            ),
            "generalization_bootstrap_pf": ("PF p05 bootstrap", "bootstrap_pf_p05", "decimal"),
        }
        for reason in reasons:
            degradation_format = degradation_labels.get(str(reason))
            if degradation_format is not None:
                label, check_name, value_format = degradation_format
                check = checks.get(check_name, {}) if isinstance(checks, dict) else {}
                value = check.get("value") if isinstance(check, dict) else None
                threshold = check.get("threshold") if isinstance(check, dict) else None
                comparison = check.get("comparison") if isinstance(check, dict) else "minimum"
                if value is None or threshold is None:
                    parts.append(label)
                else:
                    operator = "<" if comparison == "minimum" else ">"
                    if value_format == "percent":
                        rendered_value = f"{float(value):.0%}"
                        rendered_threshold = f"{float(threshold):.0%}"
                    elif value_format == "ratio":
                        rendered_value = f"{float(value):.2f}x"
                        rendered_threshold = f"{float(threshold):.2f}x"
                    else:
                        rendered_value = f"{float(value):.2f}"
                        rendered_threshold = f"{float(threshold):.2f}"
                    parts.append(f"{label}: {rendered_value} {operator} {rendered_threshold}")
                continue
            label, fmt, suffix = formats.get(str(reason), (str(reason), "", ""))
            value = metrics.get("normalized_net_profit") if str(reason) == "net_profit" else metrics.get(reason)
            if value is None:
                parts.append(label)
                continue
            try:
                parts.append(f"{label}: {value:{fmt}}{suffix}")
            except (TypeError, ValueError):
                parts.append(f"{label}: {value}")
        return " | ".join(parts)

    def _format_ubs_degradation_value(self, degradation: dict, check_name: str, *, percentage: bool) -> str:
        checks = degradation.get("checks", {}) if isinstance(degradation, dict) else {}
        check = checks.get(check_name, {}) if isinstance(checks, dict) else {}
        value = check.get("value") if isinstance(check, dict) else None
        if value is None:
            return ""
        try:
            return f"{float(value):.0%}" if percentage else f"{float(value):.2f}x"
        except (TypeError, ValueError):
            return ""

    def _ubs_robust_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                r.hidden,
                count(c.id) as total,
                sum(case when c.status = 'accepted' then 1 else 0 end) as accepted
            from runs r
            left join candidates c on c.run_id = r.id
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            accepted = int(row["accepted"] or 0)
            hidden_tag = " [arch]" if row["hidden"] else ""
            options.append((run_id, f"#{run_id} | {created} | {total} ({accepted} ok){hidden_tag}"))
        return options

    def _selected_ubs_robust_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_robust_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_robust_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_robust_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _ in options):
                return run_id
        return newest_run_id

    def _update_ubs_robust_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_robust_run_combo"):
            return
        labels = [label for _, label in options]
        self.ubs_robust_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_robust_run_id.get() != selected_label:
            self.ubs_robust_run_id.set(selected_label)

    def _latest_visible_ubs_run(self) -> sqlite3.Row | None:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return None
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            # Use the run selected in the robustness combobox if set
            import re
            selected = self.ubs_robust_run_id.get().strip()
            match = re.search(r"#?(\d+)", selected)
            if match:
                run = conn.execute("select * from runs where id=?", (int(match.group(1)),)).fetchone()
                if run is not None:
                    return run
            return conn.execute("select * from runs where hidden=0 order by id desc limit 1").fetchone()
        finally:
            conn.close()

    def _accepted_candidates_for_robustness(self, run_id: int) -> list[sqlite3.Row]:
        memory_path = self._ubs_memory_path()
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            return conn.execute(
                """
                select c.*, cr.status as robust_status
                from candidates c
                left join candidate_robustness cr on cr.candidate_id = c.id
                where c.run_id=? and c.status='accepted'
                order by c.generation, c.id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()

    def _ubs_robustness_args(
        self,
        run_id: int,
        *,
        pending_only: bool = False,
        candidate_ids: list[int] | None = None,
    ) -> list[str]:
        output_dir = self._ubs_generation_output_dir()
        positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
        args = [
            "--source-dir", str(self._ubs_generator_source_dir()),
            "--output-dir", str(output_dir),
            "--memory", str(self._ubs_memory_path()),
            "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            "--evaluate-robustness",
            "--robust-run-id", str(run_id),
            "--robust-positive-bonus", str(positive_bonus),
            "--robust-negative-bonus", str(negative_bonus),
            "--delay", str(self.delay.get()),
        ]
        for candidate_id in candidate_ids or []:
            args.extend(["--robust-candidate-id", str(int(candidate_id))])
        if pending_only:
            args.append("--robust-pending-only")
        if self.ubs_robust_from_date.get().strip():
            args.extend(["--from-date", self.ubs_robust_from_date.get().strip()])
        if self.ubs_robust_to_date.get().strip():
            args.extend(["--to-date", self.ubs_robust_to_date.get().strip()])
        args.extend(self._ubs_robust_score_args())
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

    def _run_ubs_robustness_for_latest_run(
        self,
        *,
        confirm: bool = True,
        auto: bool = False,
        pending_only: bool = True,
        candidate_ids: list[int] | None = None,
    ) -> bool:
        try:
            run = self._latest_visible_ubs_run()
            if run is None:
                if not auto:
                    messagebox.showinfo("Robustez UBS", "No hay run visible para robustez.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_robustness(run_id)
            rows = [row for row in rows if resolve_workspace_path(row["set_path"]).exists()]
            selected_ids = {int(value) for value in (candidate_ids or [])}
            if selected_ids:
                rows = [row for row in rows if int(row["id"]) in selected_ids]
            if pending_only:
                rows = [
                    row for row in rows
                    if not str(row["robust_status"] or "").strip()
                    or str(row["robust_status"]) in {"no_report", "parse_error", "report_mismatch"}
                ]
            if not rows:
                if pending_only:
                    message = (
                        f"Run #{run_id} no tiene accepted pendientes ni retryables de robustez OOS. "
                        "Usa Reprobar robustez para repetir todos."
                    )
                else:
                    message = f"Run #{run_id} no tiene candidatos accepted con .set existente para robustez."
                self.ubs_robust_status.set(message)
                if not auto:
                    messagebox.showinfo("Robustez UBS", message)
                else:
                    self._append_console(f"\n[Robustez auto] {message}\n", tag="info")
                return False
            positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
            args = self._ubs_robustness_args(run_id, pending_only=pending_only, candidate_ids=sorted(selected_ids))
        except Exception as exc:
            if not auto:
                self._show_error("No se pudo preparar robustez UBS", str(exc))
            else:
                self._append_console(f"\n[Robustez auto] No se pudo preparar: {exc}\n", tag="error")
            return False

        details = [
            f"Accion: {'Continuar robustez OOS UBS' if pending_only else 'Reprobar robustez OOS UBS'} run #{run_id}",
            f"Modo: {'accepted sin OOS + OOS retryable' if pending_only else ('seleccion marcada, reemplaza OOS existente' if candidate_ids else 'todos los accepted, reemplaza OOS existente')}",
            f"Candidatos accepted a testear: {len(rows)}",
            f"Fechas: {self.ubs_robust_from_date.get().strip() or '(template)'} -> {self.ubs_robust_to_date.get().strip() or '(template)'}",
            f"Pass OOS: net>{self.ubs_robust_pass_min_net_profit.get().strip()} | PF>={self.ubs_robust_pass_min_profit_factor.get().strip()} | DD<={self.ubs_robust_pass_max_drawdown_pct.get().strip()}%",
            f"Pass OOS: trades>={self.ubs_robust_pass_min_trades.get()} | recovery>={self.ubs_robust_pass_min_recovery_factor.get().strip()}",
            f"Degradacion: ret. net>={self.ubs_robust_min_net_retention.get().strip()} | edge PF>={self.ubs_robust_min_pf_edge_retention.get().strip()} | recovery>={self.ubs_robust_min_recovery_retention.get().strip()} | DD<={self.ubs_robust_max_dd_inflation.get().strip()}x",
            f"Trades W1/MN OOS: W1>={self.ubs_long_tf_min_trades_w1.get().strip()} | MN>={self.ubs_long_tf_min_trades_mn.get().strip()}",
            f"Bonus: accepted {positive_bonus:+.2f} | rejected {negative_bonus:+.2f}",
        ]
        details.extend(self._multiterminal_execution_details())
        if confirm and not self._confirm_execution_start("Confirmar robustez UBS", len(rows), details):
            return False
        self._show_section("ubs_robustez")
        self._run_script("ubs_agent.py", args)
        return True

    def _rerun_ubs_robustness_for_latest_run(self) -> bool:
        checked_ids = sorted(
            int(info["id"])
            for info in self._checked_ubs_robust_infos(fallback_selected=False)
            if str(info.get("id") or "").isdigit()
        )
        return self._run_ubs_robustness_for_latest_run(
            pending_only=False,
            candidate_ids=checked_ids or None,
        )

    def _maybe_auto_run_ubs_robustness(self, script_name: str, args: list[str], code: int) -> bool:
        if script_name != "ubs_agent.py" or not self.ubs_robust_auto.get():
            return False
        if code != 0:
            self._append_console(f"\n[Robustez auto] No se lanza: proceso UBS termino con codigo {code}.\n", tag="error")
            return False
        excluded = {
            "--probe-universe-history",
            "--evaluate-robustness",
            "--evaluate-seeds",
            "--rescore-seeds-only",
            "--rescore-candidates-only",
            "--rescore-robustness-only",
            "--rescore-final-tick-only",
            "--retry-candidate-id",
            "--retry-seed-path",
            "--retry-mismatch-generation",
            "--evaluate-final-tick",
        }
        if any(flag in args for flag in excluded):
            self._append_console("\n[Robustez auto] No se lanza: el proceso terminado no es generacion/backtests base.\n", tag="info")
            return False
        if "--retry-mismatch-run" in args:
            continuation = self._ubs_continuation_info()
            if (
                int(continuation.get("remaining") or 0) > 0
                or int(continuation.get("pending_count") or 0) > 0
                or int(continuation.get("retryable_count") or 0) > 0
            ):
                self._append_console(
                    "\n[Robustez auto] No se lanza: el run base aun necesita otra continuacion.\n",
                    tag="info",
                )
                return False
        runs_base_backtests = (
            "--execute-backtests" in args
            or "--backtest-pending-only" in args
            or "--retry-mismatch-run" in args
            or "--retry-full-run" in args
        )
        if not runs_base_backtests:
            self._append_console("\n[Robustez auto] No se lanza: el run no ejecuto backtests base.\n", tag="info")
            return False
        self._append_console("\n[Robustez auto] Lanzando robustez OOS sobre accepted pendientes sin OOS.\n", tag="info")
        return self._run_ubs_robustness_for_latest_run(confirm=False, auto=True, pending_only=True)

    def _refresh_ubs_robustness(self) -> None:
        tree_state = self._capture_ubs_robust_tree_state()
        if hasattr(self, "ubs_robust_tree"):
            for item in self.ubs_robust_tree.get_children():
                self.ubs_robust_tree.delete(item)
        self.ubs_robust_paths.clear()

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_robust_summary.set("Robustez: sin memoria UBS")
            self.ubs_robust_status.set(f"No existe memoria: {memory_path}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            run_options = self._ubs_robust_run_options(conn)
            selected_run_id = self._selected_ubs_robust_run_id(run_options)
            self._update_ubs_robust_run_combo(run_options, selected_run_id)
            if selected_run_id <= 0:
                conn.close()
                self.ubs_robust_summary.set("Robustez: sin run visible")
                self.ubs_robust_status.set("Limpiaste la vista de resultados; el historico conserva la memoria.")
                return
            run = conn.execute("select * from runs where id=?", (selected_run_id,)).fetchone()
            if run is None:
                conn.close()
                self.ubs_robust_summary.set("Robustez: sin run visible")
                self.ubs_robust_status.set("Limpiaste la vista de resultados; el historico conserva la memoria.")
                return
            rows = conn.execute(
                """
                select
                    c.id, c.run_id, c.generation, c.target_symbol, c.symbol, c.period,
                    c.score as train_score, c.set_path,
                    cr.status as robust_status,
                    cr.report_path as robust_report_path,
                    cr.score as robust_score,
                    cr.metrics_json as robust_metrics_json,
                    cr.degradation_json as robust_degradation_json,
                    cr.from_date, cr.to_date,
                    cr.positive_bonus, cr.negative_bonus,
                    cr.evaluated_at
                from candidates c
                left join candidate_robustness cr on cr.candidate_id = c.id
                where c.run_id=? and c.status='accepted'
                order by
                    case
                        when cr.status='accepted' then 0
                        when cr.status='rejected' then 1
                        when cr.status is null then 2
                        else 3
                    end,
                    cr.score desc,
                    c.score desc,
                    c.id desc
                """,
                (run["id"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_robust_summary.set("Robustez: error SQLite")
            self.ubs_robust_status.set(str(exc))
            return

        total = len(rows)
        accepted = sum(1 for row in rows if row["robust_status"] == "accepted")
        rejected = sum(1 for row in rows if row["robust_status"] == "rejected")
        no_trades = sum(1 for row in rows if row["robust_status"] == "no_trades")
        settled = accepted + rejected
        neutral = total - settled - no_trades
        self.ubs_robust_summary.set(
            f"Run #{run['id']} | candidatos accepted {total} | robust resueltos {settled} | OK {accepted} | FAIL {rejected} | 0 ops/no aceptado {no_trades}"
        )
        self.ubs_robust_status.set(
            f"Pendientes/neutros sin bonus: {neutral} | Fechas config: {self.ubs_robust_from_date.get().strip() or '(template)'} -> {self.ubs_robust_to_date.get().strip() or '(template)'}"
        )
        if not hasattr(self, "ubs_robust_tree"):
            return

        valid_ids: set[str] = set()
        item_by_id: dict[str, str] = {}
        for index, row in enumerate(rows):
            status = str(row["robust_status"] or "pending")
            metrics = self._parse_ubs_metrics(row["robust_metrics_json"])
            degradation = self._parse_ubs_metrics(row["robust_degradation_json"])
            bonus = self._robustness_bonus_for_status(status, row["positive_bonus"], row["negative_bonus"])
            date_range = ""
            if row["from_date"] or row["to_date"]:
                date_range = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}"
            cid = str(row["id"] or "")
            valid_ids.add(cid)
            item = self.ubs_robust_tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(cid in self.ubs_robust_checked),
                    row["run_id"],
                    row["id"],
                    row["generation"],
                    self._format_ubs_robustness_status(status),
                    self._ubs_robust_reason(status, metrics, degradation),
                    row["target_symbol"] or row["symbol"],
                    row["period"],
                    self._format_ubs_number(row["train_score"]),
                    self._format_ubs_number(row["robust_score"]),
                    self._format_ubs_number(bonus),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("normalized_net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_int(metrics.get("trades")),
                    self._format_ubs_degradation_value(degradation, "net_retention", percentage=True),
                    self._format_ubs_degradation_value(degradation, "pf_edge_retention", percentage=True),
                    self._format_ubs_degradation_value(degradation, "recovery_retention", percentage=True),
                    self._format_ubs_degradation_value(degradation, "dd_inflation", percentage=False),
                    date_range,
                    Path(str(row["set_path"] or "")).name,
                ),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            self.ubs_robust_paths[item] = {
                "id": cid,
                "set": str(row["set_path"] or ""),
                "report": str(row["robust_report_path"] or ""),
                "status": status,
            }
            if cid:
                item_by_id[cid] = item
        self.ubs_robust_checked.intersection_update(valid_ids)
        self._restore_ubs_robust_tree_state(tree_state, item_by_id)

    def _selected_ubs_robust_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_robust_tree"):
            return {}
        selected = self.ubs_robust_tree.selection()
        if not selected:
            return {}
        return self.ubs_robust_paths.get(selected[0], {})

    def _selected_ubs_robust_path(self, kind: str) -> Path | None:
        info = self._selected_ubs_robust_info()
        raw_path = info.get(kind, "")
        return resolve_workspace_path(raw_path) if raw_path else None

    def _open_selected_ubs_robust_set(self) -> None:
        path = self._selected_ubs_robust_path("set")
        if path is None:
            messagebox.showinfo("Robustez UBS", "Selecciona una fila primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_robust_report(self) -> None:
        path = self._selected_ubs_robust_path("report")
        if path is None:
            messagebox.showinfo("Robustez UBS", "Esa fila no tiene reporte OOS asociado.")
            return
        self._open_local_file(path)
