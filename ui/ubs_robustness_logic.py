from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSRobustnessLogicMixin:
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
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _robustness_bonus_for_status(self, status: str, positive: object, negative: object) -> float | None:
        try:
            if status == "accepted":
                return float(positive or 0.0)
            if status == "rejected":
                return float(negative or 0.0)
        except (TypeError, ValueError):
            return None
        return None

    def _ubs_robust_reason(self, status: str, metrics: dict) -> str:
        if status == "pending":
            return "pendiente"
        if status == "no_report":
            return "sin reporte OOS"
        if status == "parse_error":
            return "error al parsear reporte OOS"
        if status == "report_mismatch":
            return "mismatch symbol/TF OOS"
        if status == "no_trades":
            return "reporte OOS sin operaciones"
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
        for reason in reasons:
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

    def _ubs_robustness_args(self, run_id: int, *, pending_only: bool = False) -> list[str]:
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent"))
        positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
        args = [
            "--source-dir", self.set_files_root.get().strip() or str(BASE_DIR / "sets" / "ubs_ready"),
            "--output-dir", str(output_dir),
            "--memory", str(self._ubs_memory_path()),
            "--template", self.template_path.get(),
            "--evaluate-robustness",
            "--robust-run-id", str(run_id),
            "--robust-positive-bonus", str(positive_bonus),
            "--robust-negative-bonus", str(negative_bonus),
            "--delay", str(self.delay.get()),
        ]
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
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        return args

    def _run_ubs_robustness_for_latest_run(
        self,
        *,
        confirm: bool = True,
        auto: bool = False,
        pending_only: bool = True,
    ) -> bool:
        try:
            run = self._latest_visible_ubs_run()
            if run is None:
                if not auto:
                    messagebox.showinfo("Robustez UBS", "No hay run visible para robustez.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_robustness(run_id)
            rows = [row for row in rows if Path(row["set_path"]).exists()]
            if pending_only:
                rows = [
                    row for row in rows
                    if not str(row["robust_status"] or "").strip()
                    or str(row["robust_status"]) == "report_mismatch"
                ]
            if not rows:
                if pending_only:
                    message = (
                        f"Run #{run_id} no tiene accepted pendientes de robustez ni con mismatch OOS. "
                        "Usa Reprobar robustez para repetir todos."
                    )
                else:
                    message = f"Run #{run_id} no tiene candidatos accepted con .set existente para robustez."
                self.ubs_robust_status.set(message)
                if not auto:
                    messagebox.showinfo("Robustez UBS", message)
                return False
            positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
            args = self._ubs_robustness_args(run_id, pending_only=pending_only)
        except Exception as exc:
            if not auto:
                self._show_error("No se pudo preparar robustez UBS", str(exc))
            else:
                self._append_console(f"\n[Robustez auto] No se pudo preparar: {exc}\n", tag="error")
            return False

        details = [
            f"Accion: {'Continuar robustez OOS UBS' if pending_only else 'Reprobar robustez OOS UBS'} run #{run_id}",
            f"Modo: {'accepted sin OOS + mismatch OOS' if pending_only else 'todos los accepted, reemplaza OOS existente'}",
            f"Candidatos accepted a testear: {len(rows)}",
            f"Fechas: {self.ubs_robust_from_date.get().strip() or '(template)'} -> {self.ubs_robust_to_date.get().strip() or '(template)'}",
            f"Pass OOS: net>{self.ubs_robust_pass_min_net_profit.get().strip()} | PF>={self.ubs_robust_pass_min_profit_factor.get().strip()} | DD<={self.ubs_robust_pass_max_drawdown_pct.get().strip()}%",
            f"Pass OOS: trades>={self.ubs_robust_pass_min_trades.get()} | recovery>={self.ubs_robust_pass_min_recovery_factor.get().strip()}",
            f"Bonus: accepted {positive_bonus:+.2f} | rejected {negative_bonus:+.2f}",
        ]
        details.extend(self._multiterminal_execution_details())
        if confirm and not self._confirm_execution_start("Confirmar robustez UBS", len(rows), details):
            return False
        self._show_section("ubs_robustez")
        self._run_script("ubs_agent.py", args)
        return True

    def _rerun_ubs_robustness_for_latest_run(self) -> bool:
        return self._run_ubs_robustness_for_latest_run(pending_only=False)

    def _maybe_auto_run_ubs_robustness(self, script_name: str, args: list[str], code: int) -> bool:
        if code != 0 or script_name != "ubs_agent.py" or not self.ubs_robust_auto.get():
            return False
        excluded = {
            "--evaluate-robustness",
            "--evaluate-seeds",
            "--rescore-seeds-only",
            "--retry-candidate-id",
            "--retry-seed-path",
            "--retry-mismatch-run",
            "--retry-mismatch-generation",
        }
        if any(flag in args for flag in excluded):
            return False
        if "--execute-backtests" not in args:
            return False
        self._append_console("\n[Robustez auto] Lanzando robustez OOS sobre accepted pendientes sin OOS.\n", tag="info")
        return self._run_ubs_robustness_for_latest_run(confirm=False, auto=True, pending_only=True)

    def _refresh_ubs_robustness(self) -> None:
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
        evaluated = sum(1 for row in rows if row["robust_status"])
        accepted = sum(1 for row in rows if row["robust_status"] == "accepted")
        rejected = sum(1 for row in rows if row["robust_status"] == "rejected")
        neutral = evaluated - accepted - rejected
        self.ubs_robust_summary.set(
            f"Run #{run['id']} | candidatos accepted {total} | robust evaluados {evaluated} | OK {accepted} | FAIL {rejected}"
        )
        self.ubs_robust_status.set(
            f"Neutros sin bonus: {neutral} | Fechas config: {self.ubs_robust_from_date.get().strip() or '(template)'} -> {self.ubs_robust_to_date.get().strip() or '(template)'}"
        )
        if not hasattr(self, "ubs_robust_tree"):
            return

        valid_ids: set[str] = set()
        for index, row in enumerate(rows):
            status = str(row["robust_status"] or "pending")
            metrics = self._parse_ubs_metrics(row["robust_metrics_json"])
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
                    self._format_ubs_status(status),
                    self._ubs_robust_reason(status, metrics),
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
        self.ubs_robust_checked.intersection_update(valid_ids)

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
        return Path(raw_path).expanduser() if raw_path else None

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
