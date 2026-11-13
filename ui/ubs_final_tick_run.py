"""Lanzamiento de Final Tick: fechas validadas, argumentos y encadenado."""
from __future__ import annotations

from tkinter import messagebox

from ubs.path_utils import resolve_workspace_path
from ui.ubs_final_tick_shared import is_six_month, stage_label


class UBSFinalTickRunMixin:
    """Preparar y lanzar una etapa; el panel que la pinta es otro mixin."""

    # ------------------------------------------------------------------
    # Lanzamiento
    # ------------------------------------------------------------------
    def _validated_final_tick_stage_dates(self, final_tick_stage: str) -> tuple[str, str, str, str]:
        from_date, to_date, ohlc_from_date, ohlc_to_date = self._final_tick_stage_dates(final_tick_stage)
        if not from_date or not to_date:
            raise ValueError("Final Tick requiere fechas Desde y Hasta.")
        if bool(ohlc_from_date) != bool(ohlc_to_date):
            if is_six_month(final_tick_stage):
                raise ValueError("Final Tick 6M pocas ops OHLC requiere rellenar Ops bajas desde y Ops bajas hasta.")
            raise ValueError("Final Tick OHLC retry requiere rellenar OHLC desde y OHLC hasta.")
        if is_six_month(final_tick_stage):
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
        has_ohlc_retry = is_six_month(final_tick_stage) and bool(ohlc_from and ohlc_to)
        has_ohlc_pending = is_six_month(final_tick_stage) and any(
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
        six_month = is_six_month(final_tick_stage)
        label = stage_label(final_tick_stage)
        from_date, to_date, ohlc_from, ohlc_to = self._final_tick_stage_dates(final_tick_stage)
        pf_delta = min(thresholds["pf_delta"], 30.0) if six_month else thresholds["pf_delta"]
        details = [
            f"Accion: {'Continuar' if pending_only else 'Reprobar'} {label} UBS run #{run_id}",
            f"Modo: {'pendientes + retryables' if pending_only else 'todos los elegibles, reemplaza estado existente'}",
            f"Candidatos a testear: {len(rows)}",
            f"Fechas: {from_date} -> {to_date}",
            "Modelos: OHLC Model=1 vs Every tick based on real ticks Model=4",
            f"History Quality >= {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']}",
            f"Diagnostico score ops W1/MN: W1>={thresholds['min_trades_w1']} | MN>={thresholds['min_trades_mn']}",
            ("Retry pocas ops OHLC: " if six_month else "Fechas retry OHLC: ")
            + f"{ohlc_from or '(mismas)'} -> {ohlc_to or '(mismas)'}",
            f"Deltas comportamiento (bloquean): DD {thresholds['dd_delta']:.2f}% | "
            f"trades {thresholds['trades_delta']:.2f}%",
            f"Deltas economicos (solo diagnostico): net {thresholds['net_delta']:.2f}% | PF {pf_delta:.2f}%",
        ]
        if six_month:
            details.append(
                f"Diagnostico 6M PF por modelo: >= "
                f"{self.ubs_pass_min_profit_factor.get().strip() or '1.20'} (no bloquea)"
            )
        details.extend(self._multiterminal_execution_details())
        return details

    def _run_ubs_final_tick_for_latest_run(
        self,
        *,
        confirm: bool = True,
        auto: bool = False,
        pending_only: bool = True,
        final_tick_stage: str = "probe",
        run_id_override: int = 0,
    ) -> bool:
        final_tick_stage = "six_month" if is_six_month(final_tick_stage) else "probe"
        label = stage_label(final_tick_stage)
        status_var = self._final_tick_attr(final_tick_stage, "status")
        try:
            run_id = int(run_id_override or 0)
            if run_id <= 0:
                run = self._latest_visible_ubs_run_for_final_tick(final_tick_stage=final_tick_stage)
                if run is None:
                    if not auto:
                        messagebox.showinfo("Final Tick UBS", f"No hay run visible para {label}.")
                    return False
                run_id = int(run["id"])
            rows = self._accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
            rows = [row for row in rows if resolve_workspace_path(row["set_path"]).exists()]
            if pending_only:
                rows = self._pending_final_tick_rows(rows, final_tick_stage)
            if not rows:
                if pending_only:
                    message = f"Run #{run_id} no tiene candidatos pendientes de {label}."
                else:
                    message = f"Run #{run_id} no tiene candidatos elegibles con .set existente para {label}."
                status_var.set(message)
                if not auto:
                    messagebox.showinfo("Final Tick UBS", message)
                return False
            thresholds = self._ubs_final_tick_threshold_values()
            args = self._ubs_final_tick_args(run_id, pending_only=pending_only, final_tick_stage=final_tick_stage)
        except Exception as exc:
            if not auto:
                self._show_error(f"No se pudo preparar {label} UBS", str(exc))
            else:
                self._append_console(f"\n[{label} auto] No se pudo preparar: {exc}\n", tag="error")
            return False

        details = self._final_tick_execution_details(run_id, rows, pending_only, final_tick_stage, thresholds)
        if confirm and not self._confirm_execution_start(f"Confirmar {label} UBS", len(rows), details):
            return False
        self._show_section(self._final_tick_section(final_tick_stage))
        status_var.set(f"Lanzando {label} run #{run_id}: {len(rows)} candidato(s)...")
        self.status_text.set(f"Preparando {label} UBS")
        self._append_console(
            f"\n[{label}] Lanzando run #{run_id} con {len(rows)} candidato(s).\n",
            tag="info",
        )
        self.after(10, lambda: self._run_script("ubs_agent.py", args))
        return True

    def _rerun_ubs_final_tick_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(pending_only=False)

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
                stage = str(args[index + 1])
        if is_six_month(stage):
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
        final_tick_stage = "six_month" if is_six_month(final_tick_stage) else "probe"
        label = stage_label(final_tick_stage)
        status_var = self._final_tick_attr(final_tick_stage, "status")
        try:
            run = self._latest_visible_ubs_run_for_final_tick(final_tick_stage=final_tick_stage)
            if run is None:
                messagebox.showinfo("Final Tick UBS", f"No hay run visible para {label}.")
                return False
            run_id = int(run["id"])
            rows = self._accepted_candidates_for_final_tick(run_id, final_tick_stage=final_tick_stage)
            rows = [
                row for row in rows
                if resolve_workspace_path(row["set_path"]).exists()
                and str(row["final_tick_status"] or "").strip() == "pending_history_quality"
            ]
            if not rows:
                msg = f"Run #{run_id}: no hay filas con calidad pendiente en {label}."
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
            self._show_error(f"No se pudo preparar reintentar calidad baja {label}", str(exc))
            return False

        from_date, to_date, _ohlc_from_date, _ohlc_to_date = self._final_tick_stage_dates(final_tick_stage)
        details = [
            f"Accion: Reintentar calidad baja {label} - run #{run_id}",
            "Modo: solo filas pending_history_quality (ignora si las fechas coinciden o no)",
            f"Candidatos a reintentar: {len(rows)}",
            f"Fechas: {from_date} -> {to_date}",
            f"History Quality minima requerida: {thresholds['min_quality']:.2f}%",
            f"Min ops OHLC: {thresholds['min_ohlc_trades']}",
            f"Min ops W1/MN Final Tick: W1>={thresholds['min_trades_w1']} | MN>={thresholds['min_trades_mn']}",
        ]
        details.extend(self._multiterminal_execution_details())
        if not self._confirm_execution_start(f"Confirmar reintentar calidad baja {label}", len(rows), details):
            return False
        self._show_section(self._final_tick_section(final_tick_stage))
        self._run_script("ubs_agent.py", args)
        return True
