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
  Con una etapa marcada repuntua esa, la haya denunciado la auditoria o no,
  que es la unica forma de aplicar una regla ciega: el repuntuado las aplica
  aunque la etapa no las guarde y aqui no se puedan ver.
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
import tkinter as tk
from tkinter import messagebox

from ubs.account import account_memory_path
from ubs.db import connect_memory
from ubs.stage_rules_audit import (
    stage_spec,
    stages_to_repeat,
    stages_to_rescore,
)
from ui.ubs_auditor_queries import UBSAuditorQueriesMixin
from ui.ubs_auditor_rescore import UBSAuditorRescoreMixin


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSAuditorLogicMixin(UBSAuditorQueriesMixin, UBSAuditorRescoreMixin):
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
    # Arreglo de pendientes: lo lanza el runner de cada etapa
    # ------------------------------------------------------------------
    def _ubs_auditor_account_is_active(self) -> bool:
        context = self._parse_ubs_account_context(self.ubs_auditor_account.get())
        if context is None:
            return False
        broker, account_type = context
        return broker == self._ubs_broker() and account_type == self._ubs_account_type()

    def _new_ubs_auditor_menu(self):
        """El menu que se abre sobre la tabla, en un metodo propio.

        Es la unica linea que necesita un Tk de verdad, asi que sale aparte y
        lo demas -que fila se pulso, que etapa es, que dice la entrada- se
        puede probar sin abrir una ventana.
        """
        return tk.Menu(self.ubs_auditor_tree, tearoff=0)

    def _ubs_auditor_row_menu(self, event) -> None:
        """Clic derecho sobre una fila: arreglar los pendientes de esa etapa.

        El boton de la barra ya lo hace, pero sobre lo que este marcado, y
        marcar primero para pulsar despues es un paso de mas: la fila sobre la
        que se pulsa ya dice que etapa es. Se marca al abrir el menu, asi que
        la tabla ensena sobre que se va a actuar antes de elegir nada, y el
        arreglo sigue siendo el mismo de siempre -con sus avisos de cuenta
        activa y de run-, no una segunda copia que se quedaria atras.

        Las hijas cuelgan de su etapa en `ubs_auditor_row_stage`, asi que el
        clic derecho sobre un detalle vale igual que sobre la cabecera. Fuera
        de las filas no se abre nada.
        """
        tree = getattr(self, "ubs_auditor_tree", None)
        if tree is None:
            return
        item = tree.identify_row(event.y)
        stage_key = str(getattr(self, "ubs_auditor_row_stage", {}).get(item, ""))
        if not stage_key:
            return
        tree.selection_set(item)
        tree.focus(item)
        menu = self._new_ubs_auditor_menu()
        menu.add_command(
            label=f"Arreglar pendientes de {stage_spec(stage_key).label}",
            command=self._fix_ubs_pending_states_clicked,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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
