"""Pantallas Seeds Tick y Seeds Tick 6M: carga, lanzamiento y estado manual.

Las dos etapas comparten todo menos la tabla de memoria, las fechas y los
nombres de los widgets, asi que el mixin esta parametrizado por etapa y lo que
la vista engancha son envoltorios de una linea. Los umbrales y el criterio de
aceptacion son los del Final Tick de candidatos: no hay una segunda copia que
pueda divergir.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.manual_status import mark_seed_final_tick
from ubs.path_utils import resolve_workspace_path


SEED_FINAL_TICK_STAGES = ("probe", "six_month")
SEED_FINAL_TICK_TABLES = {
    "probe": "seed_final_tick",
    "six_month": "seed_final_tick_6m",
}
SEED_FINAL_TICK_LABELS = {
    "probe": "Seeds Tick",
    "six_month": "Seeds Tick 6M",
}
SEED_FINAL_TICK_SECTIONS = {
    "probe": "ubs_seeds_tick",
    "six_month": "ubs_seeds_tick_6m",
}


class UBSSeedFinalTickLogicMixin:
    """Carga y acciones de las dos etapas tick a tick de las seeds."""

    @staticmethod
    def _seed_final_tick_stage(stage: str) -> str:
        key = str(stage or "probe").strip().lower().replace("-", "_")
        return "six_month" if key in {"6m", "sixmonth", "six_month"} else "probe"

    def _seed_final_tick_attr(self, stage: str, name: str) -> str:
        suffix = "_6m" if self._seed_final_tick_stage(stage) == "six_month" else ""
        return f"ubs_seed_final_tick{suffix}_{name}"

    def _seed_final_tick_widget(self, stage: str, name: str):
        return getattr(self, self._seed_final_tick_attr(stage, name), None)

    def _seed_final_tick_dates(self, stage: str) -> tuple[str, str]:
        if self._seed_final_tick_stage(stage) == "six_month":
            return (
                self.ubs_seed_final_tick_6m_from_date.get().strip(),
                self.ubs_seed_final_tick_6m_to_date.get().strip(),
            )
        return (
            self.ubs_seed_final_tick_from_date.get().strip(),
            self.ubs_seed_final_tick_to_date.get().strip(),
        )

    def _validated_seed_final_tick_dates(self, stage: str) -> tuple[str, str]:
        stage = self._seed_final_tick_stage(stage)
        from_date, to_date = self._seed_final_tick_dates(stage)
        label = SEED_FINAL_TICK_LABELS[stage]
        if not from_date or not to_date:
            raise ValueError(f"{label} requiere fechas Desde y Hasta.")
        from ubs_agent import validate_final_tick_stage_dates

        date_error = validate_final_tick_stage_dates(stage, from_date, to_date)
        if date_error:
            raise ValueError(date_error)
        return from_date, to_date

    # ------------------------------------------------------------------ carga

    def _seed_final_tick_rows(self, conn: sqlite3.Connection, stage: str) -> list:
        """Seeds elegibles de la etapa con su resultado guardado, si lo tienen."""
        stage = self._seed_final_tick_stage(stage)
        table = SEED_FINAL_TICK_TABLES[stage]
        probe_join = ""
        if stage == "six_month":
            probe_join = (
                "join seed_final_tick probe on probe.seed_path = s.seed_path "
                "and probe.status in ('accepted', 'pending_ohlc_trades')"
            )
        return conn.execute(
            f"""
            select
                s.seed_path, s.symbol, s.period, s.score as seed_score,
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
                ft.to_date
            from seed_scores s
            {probe_join}
            left join {table} ft on ft.seed_path = s.seed_path
            where s.active=1 and s.status='accepted'
            order by
                case
                    when ft.status='accepted' then 0
                    when ft.status='rejected' then 1
                    when ft.status is null then 2
                    else 3
                end,
                ft.real_tick_score desc,
                s.seed_path
            """
        ).fetchall()

    def _load_seed_final_tick_rows(self, stage: str):
        """Filas de la etapa, o None si no hay memoria o la consulta falla."""
        stage = self._seed_final_tick_stage(stage)
        label = SEED_FINAL_TICK_LABELS[stage]
        summary = self._seed_final_tick_widget(stage, "summary")
        status_var = self._seed_final_tick_widget(stage, "status")
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            if summary is not None:
                summary.set(f"{label}: sin memoria UBS")
            if status_var is not None:
                status_var.set(f"No existe memoria: {memory_path}")
            return None
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            rows = self._seed_final_tick_rows(conn, stage)
            conn.close()
        except sqlite3.Error as exc:
            if summary is not None:
                summary.set(f"{label}: error SQLite")
            if status_var is not None:
                status_var.set(str(exc))
            return None
        return rows

    def _set_seed_final_tick_summary(self, stage: str, rows: list) -> None:
        stage = self._seed_final_tick_stage(stage)
        label = SEED_FINAL_TICK_LABELS[stage]
        accepted = sum(1 for row in rows if row["final_status"] == "accepted")
        rejected = sum(1 for row in rows if row["final_status"] == "rejected")
        missing = sum(1 for row in rows if row["final_status"] is None)
        pending = sum(
            1 for row in rows if row["final_status"] not in (None, "accepted", "rejected")
        )
        summary = self._seed_final_tick_widget(stage, "summary")
        if summary is not None:
            summary.set(
                f"{label} | seeds elegibles {len(rows)} | OK {accepted} | FAIL {rejected} | "
                f"pendientes {pending} | sin resultado {missing}"
            )
        from_date, to_date = self._seed_final_tick_dates(stage)
        status_var = self._seed_final_tick_widget(stage, "status")
        if status_var is not None:
            status_var.set(f"Fechas {label} config: {from_date or '?'} -> {to_date or '?'}")

    def _seed_final_tick_row_values(self, row, status: str) -> tuple:
        similarity = self._parse_ubs_final_tick_similarity(row["similarity_json"])
        date_range = ""
        if row["from_date"] or row["to_date"]:
            date_range = f"{row['from_date'] or '?'} -> {row['to_date'] or '?'}"
        return (
            self._format_ubs_status(status),
            self._ubs_final_tick_reason(
                status,
                similarity,
                history_quality=row["history_quality"],
                min_history_quality=row["min_history_quality"],
            ),
            row["symbol"],
            row["period"],
            f"{self._format_ubs_number(row['history_quality'])}%" if row["history_quality"] is not None else "",
            self._format_ubs_number(row["seed_score"]),
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
            Path(str(row["seed_path"] or "")).name,
        )

    def _refresh_seed_final_tick(self, stage: str) -> None:
        stage = self._seed_final_tick_stage(stage)
        tree = self._seed_final_tick_widget(stage, "tree")
        paths = self._seed_final_tick_widget(stage, "paths")
        previous_yview = None
        previous_selected: set[str] = set()
        if tree is not None:
            try:
                previous_yview = tree.yview()
                previous_selected = {
                    str((paths or {}).get(item, {}).get("seed_path") or "")
                    for item in tree.selection()
                }
                previous_selected.discard("")
            except Exception:
                previous_yview = None
                previous_selected = set()
            for item in tree.get_children():
                tree.delete(item)
        if paths is not None:
            paths.clear()

        rows = self._load_seed_final_tick_rows(stage)
        if rows is None:
            return
        self._set_seed_final_tick_summary(stage, rows)
        if tree is None:
            return

        path_to_item: dict[str, str] = {}
        for index, row in enumerate(rows):
            status = str(row["final_status"] or "pending")
            item = tree.insert(
                "",
                "end",
                values=self._seed_final_tick_row_values(row, status),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            seed_path = str(row["seed_path"] or "")
            if paths is not None:
                paths[item] = {
                    "seed_path": seed_path,
                    "set": seed_path,
                    "ohlc_report": str(row["ohlc_report_path"] or ""),
                    "real_report": str(row["real_tick_report_path"] or ""),
                    "status": status,
                }
            if seed_path:
                path_to_item[seed_path] = item

        restored = [path_to_item[path] for path in previous_selected if path in path_to_item]
        if restored:
            tree.selection_set(restored)
        if previous_yview:
            try:
                tree.yview_moveto(previous_yview[0])
            except Exception:
                pass

    def _refresh_ubs_seed_final_tick(self) -> None:
        self._refresh_seed_final_tick("probe")

    def _refresh_ubs_seed_final_tick_6m(self) -> None:
        self._refresh_seed_final_tick("six_month")

    def _refresh_ubs_seed_final_tick_panel(self) -> None:
        self._refresh_ubs_seed_final_tick()

    def _refresh_ubs_seed_final_tick_6m_panel(self) -> None:
        self._refresh_ubs_seed_final_tick_6m()

    # ------------------------------------------------------------ lanzamiento

    def _seed_final_tick_args(self, stage: str, *, pending_only: bool) -> list[str]:
        stage = self._seed_final_tick_stage(stage)
        from_date, to_date = self._validated_seed_final_tick_dates(stage)
        thresholds = self._ubs_final_tick_threshold_values()
        args = [
            "--source-dir", str(self._ubs_generator_source_dir()),
            "--output-dir", str(self._ubs_generation_output_dir()),
            "--memory", str(self._ubs_memory_path()),
            "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            "--evaluate-seed-final-tick",
            "--final-tick-stage", stage,
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
        if pending_only:
            args.append("--seed-final-tick-pending-only")
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

    def _pending_seed_final_tick_rows(self, rows: list, stage: str) -> list:
        from ubs_agent_final_tick import final_tick_row_pending_for_dates

        stage = self._seed_final_tick_stage(stage)
        from_date, to_date = self._seed_final_tick_dates(stage)
        pending = []
        for row in rows:
            # final_tick_row_pending_for_dates lee final_tick_status/from/to; la
            # consulta del panel los publica como final_status/from_date.
            proxy = {
                "final_tick_status": row["final_status"],
                "final_tick_from_date": row["from_date"],
                "final_tick_to_date": row["to_date"],
                "ft_ohlc_metrics_json": row["ohlc_metrics_json"],
            }
            if final_tick_row_pending_for_dates(
                proxy, from_date, to_date, final_tick_stage=stage,
            ):
                pending.append(row)
        return pending

    def _seed_final_tick_execution_details(self, stage: str, rows: list, pending_only: bool, thresholds) -> list[str]:
        stage = self._seed_final_tick_stage(stage)
        label = SEED_FINAL_TICK_LABELS[stage]
        from_date, to_date = self._seed_final_tick_dates(stage)
        pf_delta = min(thresholds["pf_delta"], 30.0) if stage == "six_month" else thresholds["pf_delta"]
        details = [
            f"Accion: {'Continuar' if pending_only else 'Reprobar'} {label} UBS",
            f"Modo: {'pendientes + retryables' if pending_only else 'todas las seeds elegibles, reemplaza estado existente'}",
            f"Seeds a testear: {len(rows)}",
            f"Fechas: {from_date} -> {to_date}",
            "Modelos: OHLC Model=1 vs Every tick based on real ticks Model=4",
            f"History Quality >= {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']} (W1>={thresholds['min_trades_w1']} | MN>={thresholds['min_trades_mn']})",
            f"Deltas max: net {thresholds['net_delta']:.2f}% | PF {pf_delta:.2f}% | "
            f"DD {thresholds['dd_delta']:.2f}% | trades {thresholds['trades_delta']:.2f}%",
        ]
        if stage == "six_month":
            details.append(
                f"6M PF minimo por modelo: >= {self.ubs_pass_min_profit_factor.get().strip() or '1.20'}"
            )
        else:
            details.append("Solo entran seeds con veredicto accepted en la pestana Seeds.")
        details.extend(self._multiterminal_execution_details())
        return details

    def _run_seed_final_tick(self, stage: str, *, pending_only: bool = True, confirm: bool = True) -> bool:
        stage = self._seed_final_tick_stage(stage)
        label = SEED_FINAL_TICK_LABELS[stage]
        status_var = self._seed_final_tick_widget(stage, "status")
        try:
            rows = self._load_seed_final_tick_rows(stage) or []
            rows = [row for row in rows if resolve_workspace_path(row["seed_path"]).exists()]
            if pending_only:
                rows = self._pending_seed_final_tick_rows(rows, stage)
            if not rows:
                message = (
                    f"No hay seeds pendientes de {label}."
                    if pending_only
                    else f"No hay seeds elegibles con .set existente para {label}."
                )
                if status_var is not None:
                    status_var.set(message)
                messagebox.showinfo(f"{label} UBS", message)
                return False
            thresholds = self._ubs_final_tick_threshold_values()
            args = self._seed_final_tick_args(stage, pending_only=pending_only)
        except Exception as exc:
            self._show_error(f"No se pudo preparar {label} UBS", str(exc))
            return False

        details = self._seed_final_tick_execution_details(stage, rows, pending_only, thresholds)
        if confirm and not self._confirm_execution_start(f"Confirmar {label} UBS", len(rows), details):
            return False
        self._show_section(SEED_FINAL_TICK_SECTIONS[stage])
        if status_var is not None:
            status_var.set(f"Lanzando {label}: {len(rows)} seed(s)...")
        self.status_text.set(f"Preparando {label} UBS")
        self._append_console(f"\n[{label}] Lanzando con {len(rows)} seed(s).\n", tag="info")
        self.after(10, lambda: self._run_script("ubs_agent.py", args))
        return True

    def _run_ubs_seed_final_tick(self) -> bool:
        return self._run_seed_final_tick("probe", pending_only=True)

    def _rerun_ubs_seed_final_tick(self) -> bool:
        return self._run_seed_final_tick("probe", pending_only=False)

    def _run_ubs_seed_final_tick_6m(self) -> bool:
        return self._run_seed_final_tick("six_month", pending_only=True)

    def _rerun_ubs_seed_final_tick_6m(self) -> bool:
        return self._run_seed_final_tick("six_month", pending_only=False)

    # -------------------------------------------------------- estado y ficheros

    def _selected_seed_final_tick_infos(self, stage: str) -> list[dict[str, str]]:
        tree = self._seed_final_tick_widget(stage, "tree")
        paths = self._seed_final_tick_widget(stage, "paths") or {}
        if tree is None:
            return []
        return [paths[item] for item in tree.selection() if item in paths]

    def _manual_mark_seed_final_tick(self, stage: str, status: str) -> None:
        stage = self._seed_final_tick_stage(stage)
        label = SEED_FINAL_TICK_LABELS[stage]
        infos = self._selected_seed_final_tick_infos(stage)
        seed_paths = [info.get("seed_path", "") for info in infos if info.get("seed_path")]
        if not seed_paths:
            messagebox.showinfo(f"Estado manual {label}", f"Selecciona una o mas filas de {label} primero.")
            return
        verdict = "OK" if status == "accepted" else "FAIL"
        if not messagebox.askyesno(
            f"Estado manual {label}",
            f"Marcar {len(seed_paths)} fila(s) de {label} como {verdict} manual?\n\n"
            "Esto cambia SQLite y los pesos que la seed aporta al Universo.",
        ):
            return
        try:
            thresholds = self._ubs_final_tick_threshold_values()
            from_date, to_date = self._seed_final_tick_dates(stage)
            conn = connect_memory(self._ubs_memory_path())
            conn.row_factory = sqlite3.Row
            self._ensure_ubs_memory_schema(conn)
            updated = mark_seed_final_tick(
                conn,
                seed_paths,
                status,
                final_tick_stage=stage,
                min_history_quality=thresholds["min_quality"],
                from_date=from_date,
                to_date=to_date,
                max_net_delta_pct=thresholds["net_delta"],
                max_pf_delta_pct=thresholds["pf_delta"],
                max_dd_delta_pct=thresholds["dd_delta"],
                max_trades_delta_pct=thresholds["trades_delta"],
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, ValueError) as exc:
            self._show_error(f"No se pudo aplicar estado manual {label}", str(exc))
            return
        self.ubs_weights_locked.set(False)
        self.status_text.set(f"Estado manual aplicado a {updated} fila(s) de {label}")
        self._refresh_seed_final_tick(stage)
        if stage == "probe":
            self._refresh_seed_final_tick("six_month")
        for label_name, callback_name in (
            ("ubs_universe", "_refresh_ubs_universe"),
            ("ubs_seed_summary", "_refresh_ubs_seed_eval_summary"),
        ):
            if hasattr(self, callback_name):
                self._safe_refresh(label_name, getattr(self, callback_name))

    def _manual_accept_ubs_seed_final_tick(self) -> None:
        self._manual_mark_seed_final_tick("probe", "accepted")

    def _manual_reject_ubs_seed_final_tick(self) -> None:
        self._manual_mark_seed_final_tick("probe", "rejected")

    def _manual_accept_ubs_seed_final_tick_6m(self) -> None:
        self._manual_mark_seed_final_tick("six_month", "accepted")

    def _manual_reject_ubs_seed_final_tick_6m(self) -> None:
        self._manual_mark_seed_final_tick("six_month", "rejected")

    def _open_seed_final_tick_file(self, stage: str, kind: str, missing_message: str) -> None:
        infos = self._selected_seed_final_tick_infos(stage)
        value = infos[0].get(kind, "") if infos else ""
        if not value:
            messagebox.showinfo(f"{SEED_FINAL_TICK_LABELS[self._seed_final_tick_stage(stage)]} UBS", missing_message)
            return
        self._open_local_file(Path(value))

    def _open_selected_ubs_seed_final_tick_set(self) -> None:
        self._open_seed_final_tick_file("probe", "set", "Selecciona una fila primero.")

    def _open_selected_ubs_seed_final_tick_ohlc_report(self) -> None:
        self._open_seed_final_tick_file("probe", "ohlc_report", "Esa fila no tiene reporte OHLC asociado.")

    def _open_selected_ubs_seed_final_tick_real_report(self) -> None:
        self._open_seed_final_tick_file("probe", "real_report", "Esa fila no tiene reporte real tick asociado.")

    def _open_selected_ubs_seed_final_tick_6m_set(self) -> None:
        self._open_seed_final_tick_file("six_month", "set", "Selecciona una fila primero.")

    def _open_selected_ubs_seed_final_tick_6m_ohlc_report(self) -> None:
        self._open_seed_final_tick_file("six_month", "ohlc_report", "Esa fila no tiene reporte OHLC asociado.")

    def _open_selected_ubs_seed_final_tick_6m_real_report(self) -> None:
        self._open_seed_final_tick_file("six_month", "real_report", "Esa fila no tiene reporte real tick asociado.")
