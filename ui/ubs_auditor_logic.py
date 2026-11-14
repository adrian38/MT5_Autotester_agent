"""Pantalla UBS Auditor: que cuenta se audita y como se arregla lo que sale.

Las reglas de las etapas cambian a menudo y la memoria guarda en cada fila las
que se le aplicaron. `ubs/stage_rules_audit.py` compara lo guardado con lo que
hay hoy en Configuracion y `ui/ubs_auditor_queries.py` lo pinta; aqui se elige
la cuenta y el run, se reunen las reglas vigentes y se ofrecen los dos
arreglos:

- **Repuntuar afectados** no arregla ninguna regla: arregla el veredicto de las
  semillas y candidatos que se juzgaron con otra. Repuntua desde SQLite, sin
  abrir MT5, encadenando un `--rescore-*-only` por etapa: el agente atiende un
  modo por proceso, asi que el siguiente sale cuando el anterior termina bien.
  El repuntuado recorre toda la memoria de la cuenta, no solo el run auditado.
- **Arreglar pendientes** delega en el runner de cada etapa, que es el unico
  que sabe que estado se puede reintentar y con que fechas. Eso si abre MT5, y
  por eso exige que la cuenta auditada sea la cuenta UBS activa. Cubre los dos
  huecos a la vez, porque el runner ya los trata igual: el que se quedo a
  medias y el que acaba de volverse elegible y no tiene fila todavia -lo que
  deja detras un repuntuado que paso a alguien de rejected a accepted-.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.account import account_memory_path
from ubs.db import connect_memory
from ubs.stage_rules_audit import (
    stage_spec,
    stages_to_repeat,
    stages_to_rescore,
)
from ui.ubs_auditor_queries import UBSAuditorQueriesMixin


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSAuditorLogicMixin(UBSAuditorQueriesMixin):
    """Auditoria de reglas por etapa, resumen de pendientes y sus arreglos.

    Hereda las dos consultas en vez de pedirselas a otro mixin para que la
    pantalla siga entrando por un solo nombre: `app_ui` y las pruebas montan
    este y lo tienen todo.
    """

    # ------------------------------------------------------------------
    # Cuenta y run auditados
    # ------------------------------------------------------------------
    def _refresh_ubs_auditor(self) -> None:
        self._refresh_ubs_auditor_account_values()
        self._refresh_ubs_auditor_run_combo()

    def _refresh_ubs_auditor_account_values(self) -> None:
        combo = getattr(self, "ubs_auditor_account_combo", None)
        values = tuple(
            self._ubs_account_context_label(broker, account)
            for broker, account in self._ubs_active_broker_account_contexts()
        )
        if combo is not None:
            combo.configure(values=values)
        context = self._parse_ubs_account_context(self.ubs_auditor_account.get())
        current = self._ubs_account_context_label(*context) if context else (values[0] if values else "")
        if self.ubs_auditor_account.get() != current:
            self.ubs_auditor_account.set(current)

    def _refresh_ubs_auditor_run_combo(self) -> None:
        combo = getattr(self, "ubs_auditor_run_combo", None)
        if combo is None:
            return
        memory_path = self._ubs_auditor_memory_path()
        if memory_path is None or not memory_path.exists():
            combo.configure(values=())
            self.ubs_auditor_run_id.set("")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "select id, created_at, hidden from runs order by id desc"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            combo.configure(values=())
            self.ubs_auditor_status.set(f"Error leyendo runs: {exc}")
            return
        labels = ["Toda la memoria"]
        labels.extend(
            f"#{int(row['id'])} | {str(row['created_at'] or '')[:16]}"
            + (" [arch]" if row["hidden"] else "")
            for row in rows
        )
        combo.configure(values=labels)
        current = str(self.ubs_auditor_run_id.get() or "").strip()
        if current not in labels:
            self.ubs_auditor_run_id.set(labels[0])

    def _ubs_auditor_memory_path(self) -> Path | None:
        context = self._parse_ubs_account_context(self.ubs_auditor_account.get())
        if context is None:
            return None
        broker, account_type = context
        return account_memory_path(BASE_DIR, account_type, broker)

    def _ubs_auditor_selected_run_id(self) -> int:
        """Run auditado; 0 cuando la auditoria cubre toda la memoria."""
        match = re.search(r"#(\d+)", str(self.ubs_auditor_run_id.get() or ""))
        return int(match.group(1)) if match else 0

    def _ubs_auditor_scope_label(self) -> str:
        run_id = self._ubs_auditor_selected_run_id()
        account = str(self.ubs_auditor_account.get() or "").strip()
        return f"{account} | run #{run_id}" if run_id else f"{account} | toda la memoria"

    # ------------------------------------------------------------------
    # Reglas vigentes
    # ------------------------------------------------------------------
    def _ubs_auditor_score_rules(
        self,
        *,
        min_net_profit_var,
        min_profit_factor_var,
        min_trades_var,
        max_drawdown_pct_var,
        min_recovery_factor_var,
        context: str,
    ) -> dict[str, object]:
        return {
            "min_net_profit": self._score_float(min_net_profit_var, f"{context} profit neto min"),
            "min_profit_factor": self._score_float(
                min_profit_factor_var, f"{context} profit factor min", minimum=0
            ),
            "min_trades": int(min_trades_var.get()),
            "max_drawdown_pct": self._score_float(
                max_drawdown_pct_var, f"{context} DD max %", minimum=0
            ),
            "min_recovery_factor": self._score_float(
                min_recovery_factor_var, f"{context} recovery min"
            ),
        }

    def _ubs_auditor_final_tick_rules(self, *, six_month: bool) -> dict[str, object]:
        thresholds = self._ubs_final_tick_threshold_values()
        from_date, to_date, _ohlc_from, _ohlc_to = self._final_tick_stage_dates(
            "six_month" if six_month else "probe"
        )
        return {
            "min_history_quality": thresholds["min_quality"],
            "max_net_delta_pct": thresholds["net_delta"],
            "max_pf_delta_pct": thresholds["pf_delta"],
            "max_dd_delta_pct": thresholds["dd_delta"],
            "max_trades_delta_pct": thresholds["trades_delta"],
            "max_dd_gap_pp": thresholds["dd_gap_pp"],
            "from_date": from_date,
            "to_date": to_date,
        }

    def _ubs_auditor_current_rules(self) -> dict[str, dict[str, object]]:
        """Valor que la interfaz aplica ahora a cada regla auditable."""
        positive_bonus, negative_bonus = self._ubs_robust_bonus_values()
        probe_rules = self._ubs_auditor_final_tick_rules(six_month=False)
        six_month_rules = self._ubs_auditor_final_tick_rules(six_month=True)
        return {
            "seeds": self._ubs_auditor_score_rules(
                min_net_profit_var=self.ubs_seed_pass_min_net_profit,
                min_profit_factor_var=self.ubs_seed_pass_min_profit_factor,
                min_trades_var=self.ubs_seed_pass_min_trades,
                max_drawdown_pct_var=self.ubs_seed_pass_max_drawdown_pct,
                min_recovery_factor_var=self.ubs_seed_pass_min_recovery_factor,
                context="Seeds UBS",
            ),
            "candidates": self._ubs_auditor_score_rules(
                min_net_profit_var=self.ubs_pass_min_net_profit,
                min_profit_factor_var=self.ubs_pass_min_profit_factor,
                min_trades_var=self.ubs_pass_min_trades,
                max_drawdown_pct_var=self.ubs_pass_max_drawdown_pct,
                min_recovery_factor_var=self.ubs_pass_min_recovery_factor,
                context="Agente UBS",
            ),
            "robustness": {
                **self._ubs_auditor_score_rules(
                    min_net_profit_var=self.ubs_robust_pass_min_net_profit,
                    min_profit_factor_var=self.ubs_robust_pass_min_profit_factor,
                    min_trades_var=self.ubs_robust_pass_min_trades,
                    max_drawdown_pct_var=self.ubs_robust_pass_max_drawdown_pct,
                    min_recovery_factor_var=self.ubs_robust_pass_min_recovery_factor,
                    context="Robustez UBS",
                ),
                "positive_bonus": positive_bonus,
                "negative_bonus": negative_bonus,
                "from_date": self.ubs_robust_from_date.get().strip(),
                "to_date": self.ubs_robust_to_date.get().strip(),
            },
            "final_tick": probe_rules,
            "final_tick_6m": six_month_rules,
            "seed_final_tick": probe_rules,
            "seed_final_tick_6m": six_month_rules,
            "regression": {
                "positive_points": self._score_float(
                    self.ubs_regression_positive_points, "Regresiva puntos OK"
                ),
                "negative_points": self._score_float(
                    self.ubs_regression_negative_points, "Regresiva puntos FAIL"
                ),
                "from_date": self.ubs_regression_from_date.get().strip(),
                "to_date": self.ubs_regression_to_date.get().strip(),
            },
        }

    # ------------------------------------------------------------------
    # Arreglo de reglas: repuntuado encadenado
    # ------------------------------------------------------------------
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

    def _rescore_ubs_affected_clicked(self) -> None:
        audits = getattr(self, "_ubs_auditor_audits", ())
        if not audits:
            messagebox.showinfo("Auditor UBS", "Audita las reglas antes de repuntuar.")
            return
        stages = list(stages_to_rescore(audits))
        blind = [stage_spec(stage).label for stage in stages_to_repeat(audits)]
        if not stages:
            message = (
                "Ninguna etapa con reglas movidas o cambiadas se puede repuntuar "
                "desde SQLite."
            )
            if blind:
                message += "\n\nHay que repetir la etapa en: " + ", ".join(blind)
            messagebox.showinfo("Auditor UBS", message)
            return
        labels = [stage_spec(stage).label for stage in stages]
        details = [
            f"Cuenta: {self.ubs_auditor_account.get()}",
            f"Etapas a repuntuar: {', '.join(labels)}",
            "Se reescribe el accepted/rejected de lo ya guardado con las reglas "
            "actuales; no abre MT5.",
            "El repuntuado recorre toda la memoria de la cuenta, no solo el run auditado.",
        ]
        if blind:
            details.append("Sin repuntuado (hay que repetir la etapa): " + ", ".join(blind))
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

    # ------------------------------------------------------------------
    # Arreglo de pendientes: lo lanza el runner de cada etapa
    # ------------------------------------------------------------------
    def _ubs_auditor_account_is_active(self) -> bool:
        context = self._parse_ubs_account_context(self.ubs_auditor_account.get())
        if context is None:
            return False
        broker, account_type = context
        return broker == self._ubs_broker() and account_type == self._ubs_account_type()

    def _fix_ubs_pending_states_clicked(self) -> None:
        stage_key = self._selected_ubs_auditor_stage()
        if not stage_key:
            messagebox.showinfo(
                "Auditor UBS",
                "Selecciona la fila de la etapa cuyos pendientes quieres arreglar.",
            )
            return
        if not self._ubs_auditor_account_is_active():
            messagebox.showinfo(
                "Auditor UBS",
                "Repetir una etapa usa la cuenta UBS activa. Cambia la cuenta activa en "
                "Configuracion a "
                f"{self.ubs_auditor_account.get()} y vuelve a intentarlo.",
            )
            return
        run_id = self._ubs_auditor_selected_run_id()
        if not run_id and stage_key not in {"seeds", "seed_final_tick", "seed_final_tick_6m"}:
            messagebox.showinfo(
                "Auditor UBS",
                f"Elige un run concreto para repetir pendientes de {stage_spec(stage_key).label}.",
            )
            return
        self.ubs_auditor_status.set(
            f"Pendientes de {stage_spec(stage_key).label}: la etapa confirma su propio alcance."
        )
        if stage_key == "seeds":
            self._run_ubs_seed_evaluation()
        elif stage_key == "seed_final_tick":
            self._run_ubs_seed_final_tick()
        elif stage_key == "seed_final_tick_6m":
            self._run_ubs_seed_final_tick_6m()
        elif stage_key == "candidates":
            self.ubs_results_run_id.set(f"#{run_id}")
            self._retry_visible_ubs_run_mismatches()
        elif stage_key == "robustness":
            self._run_ubs_robustness_for_latest_run(pending_only=True, run_id_override=run_id)
        elif stage_key == "final_tick":
            self._run_ubs_final_tick_for_latest_run(
                pending_only=True, final_tick_stage="probe", run_id_override=run_id
            )
        elif stage_key == "final_tick_6m":
            self._run_ubs_final_tick_for_latest_run(
                pending_only=True, final_tick_stage="six_month", run_id_override=run_id
            )
        elif stage_key == "regression":
            self._run_ubs_regression_for_latest_run(pending_only=True, run_id_override=run_id)
        else:
            messagebox.showinfo(
                "Auditor UBS",
                f"La etapa {stage_spec(stage_key).label} no tiene repeticion de pendientes.",
            )
