from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_candidate_regression
from ubs.path_utils import resolve_workspace_path
from ubs.regression_rules import REGRESSION_RETRYABLE_STATUSES, validate_regression_date_range


class UBSRegressionLogicMixin:
    def _ubs_regression_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select r.id, r.created_at, r.hidden,
                   count(distinct case when ft6.status='accepted' then c.id end) as eligible,
                   sum(case when ft6.status='accepted' and rg.status='accepted' then 1 else 0 end) as ok,
                   sum(case when ft6.status='accepted' and rg.status in ('rejected','no_trades') then 1 else 0 end) as fail,
                   sum(case when ft6.status='accepted' and rg.status is not null
                                  and rg.status not in ('accepted','rejected','no_trades') then 1 else 0 end) as technical
            from runs r
            left join candidates c on c.run_id=r.id
            left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            left join candidate_regression rg on rg.candidate_id=c.id
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            hidden = " [arch]" if row["hidden"] else ""
            options.append((
                run_id,
                f"#{run_id} | {str(row['created_at'] or '')[:16]} | 6M OK {int(row['eligible'] or 0)} | "
                f"REG OK {int(row['ok'] or 0)} FAIL {int(row['fail'] or 0)} TEC {int(row['technical'] or 0)}{hidden}",
            ))
        return options

    def _selected_ubs_regression_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        selected = self.ubs_regression_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match and any(run_id == int(match.group(1)) for run_id, _label in options):
            return int(match.group(1))
        return options[0][0]

    def _update_ubs_regression_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_regression_run_combo"):
            return
        labels = [label for _run_id, label in options]
        self.ubs_regression_run_combo.configure(values=labels)
        selected = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected:
            self.ubs_regression_run_id.set(selected)

    @staticmethod
    def _regression_json(raw: object) -> dict:
        try:
            data = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _regression_reason(self, status: str, details: dict, metrics: dict) -> str:
        labels = {
            "": "sin evaluar",
            "no_report": "sin reporte",
            "parse_error": "error al parsear reporte",
            "report_mismatch": "mismatch symbol/TF",
            "date_mismatch": "el reporte no cubre exactamente el rango",
            "no_history": "historico MT5 no disponible",
            "no_trades": "sin operaciones en el tramo",
        }
        reasons = details.get("reasons") or metrics.get("reasons") or []
        if status in labels:
            return labels[status]
        translated = {
            "net_profit": "net",
            "profit_factor": "PF",
            "trades": "operaciones",
            "drawdown_pct": "drawdown",
            "recovery_factor": "recovery",
            "positive_month_ratio": "meses positivos",
            "pf_efficiency": "PF vs base",
            "dd_ratio": "DD vs base",
            "manual_verdict": "decision manual",
        }
        return ", ".join(translated.get(str(reason), str(reason)) for reason in reasons)

    def _refresh_ubs_regression_panel(self) -> None:
        self._refresh_ubs_regression()

    def _refresh_ubs_regression(self) -> None:
        if hasattr(self, "ubs_regression_tree"):
            for item in self.ubs_regression_tree.get_children():
                self.ubs_regression_tree.delete(item)
        self.ubs_regression_paths.clear()
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_regression_summary.set("Regresiva: sin memoria UBS")
            self.ubs_regression_status.set(f"No existe memoria: {memory_path}")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            options = self._ubs_regression_run_options(conn)
            run_id = self._selected_ubs_regression_run_id(options)
            self._update_ubs_regression_run_combo(options, run_id)
            rows = conn.execute(
                """
                select c.id, c.run_id, c.generation, c.target_symbol, c.symbol, c.period, c.set_path,
                       rg.status as regression_status, rg.report_path, rg.score, rg.metrics_json,
                       rg.details_json, rg.from_date, rg.to_date, rg.points_applied
                from candidates c
                join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
                join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted'
                left join candidate_regression rg on rg.candidate_id=c.id
                where c.run_id=? and c.status='accepted'
                order by case when rg.status='accepted' then 0
                              when rg.status in ('rejected','no_trades') then 1
                              when rg.status is null then 2 else 3 end,
                         rg.score desc, c.id desc
                """,
                (run_id,),
            ).fetchall() if run_id else []
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_regression_summary.set("Regresiva: error SQLite")
            self.ubs_regression_status.set(str(exc))
            return

        ok = sum(1 for row in rows if row["regression_status"] == "accepted")
        fail = sum(1 for row in rows if row["regression_status"] in {"rejected", "no_trades"})
        technical = sum(
            1 for row in rows
            if row["regression_status"] and row["regression_status"] not in {"accepted", "rejected", "no_trades"}
        )
        pending = sum(1 for row in rows if not row["regression_status"])
        points = sum(float(row["points_applied"] or 0.0) for row in rows)
        self.ubs_regression_summary.set(
            f"Run #{run_id} | 6M accepted {len(rows)} | REG OK {ok} | FAIL {fail} | "
            f"tecnicos {technical} | sin evaluar {pending} | puntos {points:+.0f}"
        )
        self.ubs_regression_status.set(
            f"OHLC 1 minuto | {self.ubs_regression_from_date.get()} -> {self.ubs_regression_to_date} | "
            "los fallos tecnicos no suman ni restan"
        )
        if not hasattr(self, "ubs_regression_tree"):
            return
        for row in rows:
            status = str(row["regression_status"] or "")
            metrics = self._regression_json(row["metrics_json"])
            details = self._regression_json(row["details_json"])
            dates = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}" if status else ""
            item = self.ubs_regression_tree.insert(
                "", "end",
                values=(
                    self._checkbox_text(str(row["id"]) in self.ubs_regression_checked),
                    row["run_id"], row["id"], row["generation"], self._format_ubs_status(status or "pending"),
                    self._regression_reason(status, details, metrics),
                    f"{float(row['points_applied'] or 0.0):+.0f}" if status else "",
                    row["target_symbol"] or row["symbol"], row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_int(metrics.get("trades")),
                    self._format_ubs_number(metrics.get("recovery_factor")),
                    f"{self._format_ubs_number(metrics.get('positive_month_ratio', 0) * 100)}%" if metrics else "",
                    dates, Path(str(row["set_path"])).name,
                ),
                tags=("accepted" if status == "accepted" else "rejected" if status in {"rejected", "no_trades"} else "pending",),
            )
            self.ubs_regression_paths[item] = {
                "id": str(row["id"]),
                "set": str(row["set_path"] or ""),
                "report": str(row["report_path"] or ""),
            }
        valid_ids = {record["id"] for record in self.ubs_regression_paths.values()}
        self.ubs_regression_checked.intersection_update(valid_ids)

    def _on_ubs_regression_tree_click(self, event) -> str | None:
        if not hasattr(self, "ubs_regression_tree"):
            return None
        item, column = self._tree_item_from_event(self.ubs_regression_tree, event)
        if not item or column != "#1":
            return None
        record = self.ubs_regression_paths.get(item, {})
        candidate_id = str(record.get("id") or "")
        if not candidate_id:
            return "break"
        if candidate_id in self.ubs_regression_checked:
            self.ubs_regression_checked.remove(candidate_id)
        else:
            self.ubs_regression_checked.add(candidate_id)
        values = list(self.ubs_regression_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(candidate_id in self.ubs_regression_checked)
            self.ubs_regression_tree.item(item, values=values)
        return "break"

    def _selected_ubs_regression_records(self) -> list[dict[str, str]]:
        if not hasattr(self, "ubs_regression_tree"):
            return []
        checked = [
            record for record in self.ubs_regression_paths.values()
            if record.get("id") in self.ubs_regression_checked
        ]
        if checked:
            return checked
        return [self.ubs_regression_paths[item] for item in self.ubs_regression_tree.selection() if item in self.ubs_regression_paths]

    def _open_selected_ubs_regression_set(self) -> None:
        records = self._selected_ubs_regression_records()
        if records:
            self._open_local_file(resolve_workspace_path(records[0]["set"]))

    def _open_selected_ubs_regression_report(self) -> None:
        records = self._selected_ubs_regression_records()
        if records and records[0]["report"]:
            self._open_local_file(resolve_workspace_path(records[0]["report"]))

    def _manual_mark_selected_ubs_regression(self, status: str) -> None:
        records = self._selected_ubs_regression_records()
        if not records:
            messagebox.showinfo("Regresiva UBS", "Selecciona al menos un candidato.")
            return
        try:
            values = self._ubs_regression_threshold_values()
            with connect_memory(self._ubs_memory_path()) as conn:
                conn.row_factory = sqlite3.Row
                self._ensure_ubs_memory_schema(conn)
                changed = mark_candidate_regression(
                    conn,
                    [record["id"] for record in records],
                    status,
                    from_date=self.ubs_regression_from_date.get().strip(),
                    to_date=self.ubs_regression_to_date.get().strip(),
                    positive_points=float(values["positive"]),
                    negative_points=float(values["negative"]),
                )
                conn.commit()
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            self._show_error("No se pudo aplicar el estado regresivo", str(exc))
            return
        self.ubs_regression_status.set(f"Estado manual aplicado a {changed} candidato(s).")
        self._refresh_ubs_regression()
        self._refresh_ubs_universe()

    def _manual_accept_selected_ubs_regression(self) -> None:
        self._manual_mark_selected_ubs_regression("accepted")

    def _manual_reject_selected_ubs_regression(self) -> None:
        self._manual_mark_selected_ubs_regression("rejected")

    def _ubs_regression_threshold_values(self) -> dict[str, float | int]:
        error = validate_regression_date_range(
            self.ubs_regression_from_date.get().strip(), self.ubs_regression_to_date.get().strip()
        )
        if error:
            raise ValueError(error)
        values: dict[str, float | int] = {
            "net": float(self.ubs_regression_min_net_profit.get()),
            "pf": float(self.ubs_regression_min_profit_factor.get()),
            "trades": int(self.ubs_regression_min_trades.get()),
            "trades_w1": int(self.ubs_regression_min_trades_w1.get()),
            "trades_mn": int(self.ubs_regression_min_trades_mn.get()),
            "dd": float(self.ubs_regression_max_drawdown_pct.get()),
            "recovery": float(self.ubs_regression_min_recovery_factor.get()),
            "months": float(self.ubs_regression_min_positive_month_ratio.get()),
            "pf_efficiency": float(self.ubs_regression_min_pf_efficiency.get()),
            "dd_ratio": float(self.ubs_regression_max_dd_ratio.get()),
            "positive": float(self.ubs_regression_positive_points.get()),
            "negative": float(self.ubs_regression_negative_points.get()),
        }
        if min(int(values["trades"]), int(values["trades_w1"]), int(values["trades_mn"])) < 0:
            raise ValueError("Los minimos de operaciones no pueden ser negativos.")
        if not 0 <= float(values["months"]) <= 1:
            raise ValueError("Meses positivos debe estar entre 0 y 1.")
        if min(float(values["pf"]), float(values["dd"]), float(values["recovery"])) < 0:
            raise ValueError("PF, drawdown y recovery no pueden ser negativos.")
        if min(float(values["pf_efficiency"]), float(values["dd_ratio"])) < 0:
            raise ValueError("Eficiencia PF y cociente DD no pueden ser negativos (0 desactiva).")
        if float(values["positive"]) < 0 or float(values["negative"]) > 0:
            raise ValueError("Puntos OK deben ser >= 0 y puntos FAIL <= 0.")
        return values

    def _ubs_regression_args(self, run_id: int, *, pending_only: bool, rescore: bool = False) -> list[str]:
        values = self._ubs_regression_threshold_values()
        args = [
            "--source-dir", str(self._ubs_generator_source_dir()), "--output-dir", str(self._ubs_generation_output_dir()),
            "--memory", str(self._ubs_memory_path()), "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(), "--template", self.template_path.get(),
            "--regression-run-id", str(run_id),
            "--regression-from-date", self.ubs_regression_from_date.get().strip(),
            "--regression-to-date", self.ubs_regression_to_date.get().strip(),
            "--regression-min-net-profit", str(values["net"]),
            "--regression-min-profit-factor", str(values["pf"]),
            "--regression-min-trades", str(values["trades"]),
            "--regression-min-trades-w1", str(values["trades_w1"]),
            "--regression-min-trades-mn", str(values["trades_mn"]),
            "--regression-max-drawdown-pct", str(values["dd"]),
            "--regression-min-recovery-factor", str(values["recovery"]),
            "--regression-min-positive-month-ratio", str(values["months"]),
            "--regression-min-pf-efficiency", str(values["pf_efficiency"]),
            "--regression-max-dd-ratio", str(values["dd_ratio"]),
            "--regression-positive-points", str(values["positive"]),
            "--regression-negative-points", str(values["negative"]),
            "--delay", str(self.delay.get()),
            "--rescore-regression-only" if rescore else "--evaluate-regression",
        ]
        if pending_only and not rescore:
            args.append("--regression-pending-only")
        if not rescore:
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

    def _run_ubs_regression_for_latest_run(
        self,
        *,
        confirm: bool = True,
        auto: bool = False,
        pending_only: bool = True,
        run_id_override: int = 0,
    ) -> bool:
        try:
            with connect_memory(self._ubs_memory_path()) as conn:
                conn.row_factory = sqlite3.Row
                self._ensure_ubs_memory_schema(conn)
                options = self._ubs_regression_run_options(conn)
                run_id = int(run_id_override or 0)
                if run_id <= 0:
                    run_id = options[0][0] if auto and options else self._selected_ubs_regression_run_id(options)
                rows = conn.execute(
                    """
                    select c.id, c.set_path, rg.status
                    from candidates c
                    join candidate_robustness cr on cr.candidate_id=c.id and cr.status='accepted'
                    join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted'
                    left join candidate_regression rg on rg.candidate_id=c.id
                    where c.run_id=? and c.status='accepted'
                    """,
                    (run_id,),
                ).fetchall()
            rows = [row for row in rows if resolve_workspace_path(row["set_path"]).exists()]
            if pending_only:
                rows = [
                    row for row in rows
                    if not str(row["status"] or "").strip()
                    or str(row["status"] or "").strip().lower() in REGRESSION_RETRYABLE_STATUSES
                ]
            if not rows:
                if not auto:
                    messagebox.showinfo("Regresiva UBS", f"Run #{run_id}: no hay candidatos en este alcance.")
                return False
            values = self._ubs_regression_threshold_values()
            args = self._ubs_regression_args(run_id, pending_only=pending_only)
        except Exception as exc:
            if auto:
                self._append_console(f"\n[Regresiva auto] No se pudo preparar: {exc}\n", tag="error")
            else:
                self._show_error("No se pudo preparar la prueba regresiva", str(exc))
            return False
        details = [
            f"Run #{run_id} | candidatos: {len(rows)}",
            f"Rango: {self.ubs_regression_from_date.get()} -> {self.ubs_regression_to_date.get()}",
            "Modelo: OHLC 1 minuto (Model=1)",
            f"Net > {values['net']} | PF >= {values['pf']} | ops >= {values['trades']}",
            f"DD <= {values['dd']}% | recovery >= {values['recovery']} | meses + >= {values['months']}",
            f"Puntos: OK {float(values['positive']):+.0f}; FAIL base {float(values['negative']):+.0f}, hasta -60 extra por causas",
        ]
        details.extend(self._multiterminal_execution_details())
        if confirm and not self._confirm_execution_start("Confirmar prueba regresiva UBS", len(rows), details):
            return False
        self._show_section("ubs_regression")
        self.ubs_regression_status.set(f"Lanzando regresiva run #{run_id}: {len(rows)} candidato(s)...")
        self.after(10, lambda: self._run_script("ubs_agent.py", args))
        return True

    def _rerun_ubs_regression_for_latest_run(self) -> bool:
        return self._run_ubs_regression_for_latest_run(pending_only=False)

    def _rescore_ubs_regression(self) -> bool:
        try:
            with connect_memory(self._ubs_memory_path()) as conn:
                conn.row_factory = sqlite3.Row
                self._ensure_ubs_memory_schema(conn)
                options = self._ubs_regression_run_options(conn)
                run_id = self._selected_ubs_regression_run_id(options)
            args = self._ubs_regression_args(run_id, pending_only=False, rescore=True)
        except Exception as exc:
            self._show_error("No se pudo recalcular la regresiva", str(exc))
            return False
        self._show_section("ubs_regression")
        self._run_script("ubs_agent.py", args)
        return True

    def _maybe_auto_run_ubs_regression(self, script_name: str, args: list[str], code: int) -> bool:
        if code != 0 or script_name != "ubs_agent.py" or "--evaluate-final-tick" not in args:
            return False
        if not self.ubs_regression_auto.get():
            return False
        stage = "probe"
        if "--final-tick-stage" in args:
            index = args.index("--final-tick-stage")
            if index + 1 < len(args):
                stage = str(args[index + 1]).strip().lower().replace("-", "_")
        if stage not in {"six_month", "6m", "sixmonth"}:
            return False
        run_id = 0
        if "--final-tick-run-id" in args:
            index = args.index("--final-tick-run-id")
            if index + 1 < len(args):
                try:
                    run_id = int(args[index + 1])
                except (TypeError, ValueError):
                    run_id = 0
        self._append_console("\n[Regresiva auto] Lanzando OHLC historico sobre Final Tick 6M accepted.\n", tag="info")
        return self._run_ubs_regression_for_latest_run(
            confirm=False,
            auto=True,
            pending_only=True,
            run_id_override=run_id,
        )
