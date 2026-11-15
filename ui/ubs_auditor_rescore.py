"""Repuntuado encadenado del auditor: una etapa por proceso.

El agente atiende un modo por proceso, asi que repuntuar varias etapas es
lanzarlas en fila: la siguiente sale cuando la anterior termina bien. Aqui
estan los argumentos de cada etapa y la cola; lo que decide que etapas entran
vive en `ubs/stage_fixes.py`, y el arreglo de pendientes -el otro boton, que
si abre MT5- se quedo en `ui/ubs_auditor_logic.py`, de donde esto salio
cuando aquel fichero se paso de las 600 lineas.
"""
from __future__ import annotations

from tkinter import messagebox

from ubs.stage_rules_audit import stage_spec, stages_to_repeat, stages_to_rescore


class UBSAuditorRescoreMixin:
    """Prepara y encadena los `--rescore-*-only` de las etapas afectadas."""

    def _ubs_auditor_robustness_rescore_args(self) -> list[str]:
        positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
        args = [
            "--robust-positive-bonus", str(positive_bonus),
            "--robust-negative-bonus", str(negative_bonus),
        ]
        if self.ubs_robust_from_date.get().strip():
            args.extend(["--from-date", self.ubs_robust_from_date.get().strip()])
        if self.ubs_robust_to_date.get().strip():
            args.extend(["--to-date", self.ubs_robust_to_date.get().strip()])
        # Las retenciones de degradacion viajan en los argumentos base, y los
        # umbrales propios de robustez van detras para ganar el ultimo turno.
        args.extend(self._ubs_score_args())
        args.extend(self._ubs_robust_score_args())
        return args

    def _ubs_auditor_final_tick_rescore_args(self, *, six_month: bool) -> list[str]:
        thresholds = self._ubs_final_tick_threshold_values()
        stage = "six_month" if six_month else "probe"
        from_date, to_date, _ohlc_from, _ohlc_to = self._final_tick_stage_dates(stage)
        args = [
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
            "--final-tick-max-dd-gap-pp", str(thresholds["dd_gap_pp"]),
        ]
        args.extend(self._ubs_score_args())
        return args

    def _ubs_auditor_regression_rescore_args(self) -> list[str]:
        values = self._ubs_regression_threshold_values()
        return [
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
        ]

    def _ubs_auditor_stage_rescore_args(self, stage_key: str) -> list[str]:
        """Reglas vigentes que necesita el repuntuado de una etapa."""
        if stage_key == "seeds":
            return self._ubs_seed_score_args()
        if stage_key == "candidates":
            return self._ubs_score_args()
        if stage_key == "robustness":
            return self._ubs_auditor_robustness_rescore_args()
        if stage_key in {"final_tick", "final_tick_6m"}:
            return self._ubs_auditor_final_tick_rescore_args(
                six_month=stage_key == "final_tick_6m"
            )
        if stage_key == "regression":
            return self._ubs_auditor_regression_rescore_args()
        raise ValueError(
            f"La etapa {stage_spec(stage_key).label} no tiene repuntuado desde SQLite."
        )

    def _ubs_auditor_rescore_args(self, stage_key: str) -> list[str]:
        spec = stage_spec(stage_key)
        memory_path = self._ubs_auditor_memory_path()
        context = self._parse_ubs_account_context(self.ubs_auditor_account.get())
        if memory_path is None or context is None:
            raise ValueError("Cuenta auditada invalida.")
        stage_args = self._ubs_auditor_stage_rescore_args(stage_key)
        broker, account_type = context
        args = [
            "--source-dir", str(self._ubs_generator_source_dir()),
            "--output-dir", str(self._ubs_generation_output_dir()),
            "--memory", str(memory_path),
            "--broker", broker,
            "--account-type", account_type,
            spec.rescore_flag,
            *stage_args,
        ]
        symbol_map = self._effective_ubs_symbol_map_text()
        if symbol_map:
            args.extend(["--symbol-map", symbol_map])
        args.extend(self._effective_symbol_suffix_args())
        return args

    def _explain_nothing_to_rescore(self, audits) -> None:
        """Por que no hay cola, sin contradecir lo que la tabla pinta en rojo."""
        repeat = [stage_spec(stage).label for stage in stages_to_repeat(audits)]
        parts = [
            "Ninguna etapa con repuntuado juzgo nada con otra regla."
            if repeat
            else "Ningun veredicto se dio con otras reglas."
        ]
        if repeat:
            parts.append("Lo que esta en rojo hay que repetirlo: " + ", ".join(repeat) + ".")
        parts.append(
            "Las reglas ciegas no se ven desde aqui: si moviste una, marca la "
            "etapa en la tabla y vuelve a pulsar para repuntuarla igualmente."
        )
        messagebox.showinfo("Auditor UBS", "\n\n".join(parts))

    def _ubs_auditor_stages_to_rescore(self, audits) -> tuple[list[str], str]:
        """Que etapas repuntuar: solo donde la regla cambio.

        Una etapa en la que no cambio nada no se repuntua: seria recorrer la
        memoria entera para reescribir cada veredicto con el valor que ya
        tenia. La excepcion es marcarla a mano, que es como se pide lo que la
        auditoria no puede ver -una regla ciega movida no deja rastro con el
        que comparar, y el repuntuado si la aplica-.
        """
        selected = self._selected_ubs_auditor_stage()
        if selected and stage_spec(selected).rescore_flag:
            return [selected], "marcada a mano"
        return list(stages_to_rescore(audits)), "donde cambio la regla"

    def _rescore_ubs_affected_clicked(self) -> None:
        audits = getattr(self, "_ubs_auditor_audits", ())
        if not audits:
            messagebox.showinfo("Auditor UBS", "Audita las reglas antes de repuntuar.")
            return
        stages, why = self._ubs_auditor_stages_to_rescore(audits)
        if not stages:
            self._explain_nothing_to_rescore(audits)
            return
        labels = [stage_spec(stage).label for stage in stages]
        repeat = [stage_spec(stage).label for stage in stages_to_repeat(audits)]
        details = [
            f"Cuenta: {self.ubs_auditor_account.get()}",
            f"Etapas a repuntuar ({why}): {', '.join(labels)}",
            "Se reescribe el accepted/rejected de lo ya guardado con las reglas "
            "actuales; no abre MT5.",
            "El repuntuado recorre toda la memoria de la cuenta, no solo el run auditado.",
        ]
        if repeat:
            details.append(
                "Esto no lo arregla, hay que repetir la etapa en: " + ", ".join(repeat)
            )
        if not self._confirm_execution_start(
            "Confirmar repuntuado de afectados", len(stages), details
        ):
            return
        try:
            first_args = self._ubs_auditor_rescore_args(stages[0])
        except ValueError as exc:
            self._show_error("Auditor UBS", str(exc))
            return
        self._ubs_auditor_rescore_queue = list(stages[1:])
        self.ubs_auditor_status.set(
            f"Repuntuando {stage_spec(stages[0]).label}: {len(stages)} etapa(s) en cola."
        )
        self._run_script("ubs_agent.py", first_args)

    def _maybe_auto_run_ubs_auditor_rescore(
        self, script_name: str, args: list[str], code: int
    ) -> bool:
        """Lanza el siguiente repuntuado de la cola del auditor."""
        queue = list(getattr(self, "_ubs_auditor_rescore_queue", []))
        if not queue:
            return False
        if script_name != "ubs_agent.py" or code != 0:
            self._ubs_auditor_rescore_queue = []
            self._append_console(
                "\n[Auditor UBS] Cola de repuntuado cancelada: el proceso anterior no termino bien.\n",
                tag="error",
            )
            return False
        stage_key = queue.pop(0)
        self._ubs_auditor_rescore_queue = queue
        try:
            next_args = self._ubs_auditor_rescore_args(stage_key)
        except ValueError as exc:
            self._ubs_auditor_rescore_queue = []
            self._append_console(f"\n[Auditor UBS] {exc}\n", tag="error")
            return False
        self._append_console(
            f"\n[Auditor UBS] Repuntuando {stage_spec(stage_key).label}; quedan {len(queue)} etapa(s).\n",
            tag="info",
        )
        self.ubs_auditor_status.set(
            f"Repuntuando {stage_spec(stage_key).label}; quedan {len(queue)} etapa(s)."
        )
        self.after(10, lambda: self._run_script("ubs_agent.py", next_args))
        return True
