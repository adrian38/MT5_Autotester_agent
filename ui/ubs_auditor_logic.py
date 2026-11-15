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
  Sin etapa marcada arregla todas, como la tabla cuenta toda la memoria
  mientras no se marque nada; con una marcada, solo esa.
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
from ubs.stage_rules_audit import audit_pending_states, pending_runs, stage_spec
from ui.ubs_auditor_queries import UBSAuditorQueriesMixin
from ui.ubs_auditor_rescore import UBSAuditorRescoreMixin
from ui.ubs_search_audit_base import AUDIT_FINAL_STATUSES


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

    def _ubs_auditor_pending_runs(self, stage_key: str, *, quiet: bool = False):
        """Los runs de la etapa a los que les queda algo, o `None` si fallo.

        Las etapas de semilla no cuelgan de ningun run y devuelven la lista
        vacia: las arregla una sola corrida. Con un run marcado en la barra se
        mira solo ese, que es lo que la tabla esta ensenando; sin marcar, la
        etapa entera, porque arreglar «Resultados» es arreglar los resultados,
        no los de un run que el auditor no ha elegido.

        `quiet` calla el aviso de «no tiene pendientes»: en el barrido de todas
        las etapas, una vacia es lo normal y no un dialogo que cerrar.
        """
        spec = stage_spec(stage_key)
        if not spec.run_column:
            return ()
        conn = self._ubs_auditor_open_memory()
        if conn is None:
            return None
        try:
            gaps = pending_runs(conn, stage_key, final_statuses=AUDIT_FINAL_STATUSES)
        except sqlite3.Error as exc:
            self._show_error("Auditor UBS", f"Error buscando pendientes: {exc}")
            return None
        finally:
            conn.close()
        selected = self._ubs_auditor_selected_run_id()
        if selected:
            gaps = tuple(gap for gap in gaps if gap.run_id == selected)
        if not gaps:
            if not quiet:
                messagebox.showinfo(
                    "Auditor UBS",
                    f"{spec.label} no tiene pendientes"
                    + (f" en el run #{selected}." if selected else " en esta memoria."),
                )
            return None
        return gaps

    def _ubs_auditor_stage_queue(
        self, stage_key: str, *, quiet: bool = False, rows: tuple[int, int] = (0, 0)
    ) -> list[tuple]:
        """La cola de una etapa: una corrida por run con hueco.

        Las de semilla no cuelgan de ningun run y salen como una sola corrida
        con el run cero, que su runner ignora, para que el camino sea uno solo.
        Sus cuentas no vienen por run, asi que las pone quien las sepa (`rows`)
        o el dialogo prometeria cero semillas.
        """
        gaps = self._ubs_auditor_pending_runs(stage_key, quiet=quiet)
        if gaps is None:
            return []
        return [
            (stage_key, gap.run_id, gap.pending, gap.missing) for gap in gaps
        ] or [(stage_key, 0, rows[0], rows[1])]

    def _ubs_auditor_every_stage_queue(self) -> list[tuple]:
        """La cola de todas las etapas, que es lo que el boton hace sin marca.

        Sin fila marcada el boton arregla lo que la tabla cuenta: la memoria
        entera. Las etapas van en el orden de `STAGE_SPECS`, que es el del
        pipeline, porque arreglar Resultados da de comer a Robustez y esta a
        Final Tick; al reves, la de abajo correria antes de que existiera su
        entrada y habria que volver a pasar.

        Se pregunta primero quien tiene algo -la misma cuenta que Consultar
        pendientes- en vez de encolar las ocho: las tres de semilla recorren el
        disco entero y abrir MT5 para nada es media tarde.
        """
        conn = self._ubs_auditor_open_memory()
        if conn is None:
            return []
        try:
            summaries = audit_pending_states(
                conn,
                final_statuses=AUDIT_FINAL_STATUSES,
                run_id=self._ubs_auditor_selected_run_id(),
            )
        except sqlite3.Error as exc:
            self._show_error("Auditor UBS", f"Error buscando pendientes: {exc}")
            return []
        finally:
            conn.close()
        queue: list[tuple] = []
        for summary in summaries:
            if not (summary.pending + summary.missing):
                continue
            queue.extend(
                self._ubs_auditor_stage_queue(
                    summary.stage, quiet=True, rows=(summary.pending, summary.missing)
                )
            )
        if not queue:
            selected = self._ubs_auditor_selected_run_id()
            messagebox.showinfo(
                "Auditor UBS",
                "Ninguna etapa tiene pendientes"
                + (f" en el run #{selected}." if selected else " en esta memoria."),
            )
        return queue

    def _confirm_ubs_auditor_pending(self, queue) -> bool:
        """Pregunta una vez por toda la cola, con el alcance a la vista.

        Lo que hay que ver antes de decir que si son las corridas: cada una
        abre MT5 por su cuenta y van una detras de otra, asi que trescientos
        runs no son un boton, son una tarde.
        """
        stages: list[str] = []
        for stage_key, _run_id, _pending, _missing in queue:
            if stage_key not in stages:
                stages.append(stage_key)
        pending = sum(item[2] for item in queue)
        missing = sum(item[3] for item in queue)
        details = [
            "Etapas: " + ", ".join(stage_spec(key).label for key in stages),
            f"Corridas en cola: {len(queue)}",
            f"Se quedaron a medias: {pending}",
            f"Sin fila todavia: {missing}",
            "Cada corrida abre MT5 por su cuenta, una detras de otra.",
            "Si una termina mal, la cola se para.",
        ]
        return self._confirm_execution_start(
            "Confirmar arreglo de pendientes", pending + missing, details
        )

    def _maybe_auto_run_ubs_auditor_pending(
        self, script_name: str, args: list[str], code: int
    ) -> bool:
        """Manda el siguiente run de la cola de pendientes del auditor.

        Se lanza con `after` y no aqui mismo, como la cola del repuntuado: el
        proceso anterior todavia se esta cerrando.
        """
        if not getattr(self, "_ubs_auditor_pending_queue", []):
            return False
        if script_name != "ubs_agent.py" or code != 0 or not getattr(
            self, "_ubs_auditor_pending_stage", ""
        ):
            self._ubs_auditor_pending_queue = []
            self._append_console(
                "\n[Auditor UBS] Cola de pendientes cancelada: el proceso anterior "
                "no termino bien.\n",
                tag="error",
            )
            return False
        self.after(10, self._start_next_ubs_auditor_pending)
        return True

    def _start_next_ubs_auditor_pending(self) -> bool:
        """Arranca el primer run de la cola que de verdad tenga que correr.

        La auditoria y el runner no cuentan igual: la primera mira la memoria
        y el segundo, ademas, que el .set siga en disco y que el estado sea de
        los suyos. Un run que el runner deja pasar no deja proceso, y sin
        proceso no hay final al que colgar el siguiente, asi que la cola se
        quedaria parada en el primer desacuerdo. Por eso se salta en el sitio
        hasta dar con uno que arranque.
        """
        queue = list(getattr(self, "_ubs_auditor_pending_queue", []))
        skipped = 0
        while queue:
            stage_key, run_id = queue[0][0], int(queue[0][1])
            queue.pop(0)
            self._ubs_auditor_pending_queue = queue
            # La etapa es de la corrida, no de la cola: sin marca van varias, y
            # de ella cuelga el permiso para encadenar la siguiente.
            self._ubs_auditor_pending_stage = stage_key
            label = stage_spec(stage_key).label
            if self._run_ubs_auditor_pending(stage_key, run_id):
                self.ubs_auditor_status.set(
                    f"Pendientes de {label}"
                    + (f" en el run #{run_id}" if run_id else "")
                    + f"; quedan {len(queue)} corrida(s)."
                )
                self._append_console(
                    f"\n[Auditor UBS] Pendientes de {label}"
                    + (f" en el run #{run_id}" if run_id else "")
                    + f"; quedan {len(queue)} corrida(s).\n",
                    tag="info",
                )
                return True
            skipped += 1
        self._ubs_auditor_pending_queue = []
        if skipped:
            self.ubs_auditor_status.set(
                f"Arreglar pendientes: {skipped} corrida(s) sin nada que su runner "
                "reconozca. Vuelve a consultar pendientes."
            )
        return False

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

        Solo sobre la fila de la etapa. Las hijas son el detalle de lo que se
        encontro -un estado, un hueco-, no algo que se pueda arreglar aparte:
        el arreglo es siempre de la etapa entera, asi que ofrecerlo colgando
        de una hija prometia una precision que no existe.
        """
        tree = getattr(self, "ubs_auditor_tree", None)
        if tree is None:
            return
        item = tree.identify_row(event.y)
        if not item or tree.parent(item):
            return
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
        """Arregla los pendientes de la etapa marcada, o de todas si no hay.

        La marca es un filtro, no un requisito: la tabla ya cuenta la memoria
        entera mientras no se marque nada, y pedir una fila para arreglar lo
        que se acaba de contar era un paso de mas. El menu del clic derecho
        marca la fila antes de llamar aqui, asi que sigue arreglando una.
        """
        if not self._ubs_auditor_account_is_active():
            messagebox.showinfo(
                "Auditor UBS",
                "Repetir una etapa usa la cuenta UBS activa. Cambia la cuenta activa en "
                "Configuracion a "
                f"{self.ubs_auditor_account.get()} y vuelve a intentarlo.",
            )
            return
        stage_key = self._selected_ubs_auditor_stage()
        queue = (
            self._ubs_auditor_stage_queue(stage_key) if stage_key
            else self._ubs_auditor_every_stage_queue()
        )
        if not queue:
            return
        if not self._confirm_ubs_auditor_pending(queue):
            return
        self._ubs_auditor_pending_queue = queue
        self._ubs_auditor_pending_stage = queue[0][0]
        scope = (
            stage_spec(stage_key).label if stage_key
            else f"{len({item[0] for item in queue})} etapa(s)"
        )
        self.ubs_auditor_status.set(
            f"Pendientes de {scope}: {len(queue)} corrida(s) en cola."
        )
        self._start_next_ubs_auditor_pending()

    def _run_ubs_auditor_pending(self, stage_key: str, run_id: int) -> bool:
        """Manda un run de una etapa a su propio runner, sin volver a preguntar.

        Preguntar lo hace el auditor una vez, con el total de los runs; el
        runner pregunta por run, y encadenar trescientos dialogos no es
        confirmar nada. Devuelve si el runner llego a lanzar algo, que es de
        lo que cuelga el siguiente de la cola.
        """
        if stage_key == "seeds":
            # Las tres de semilla recorren el disco entero y no devuelven
            # nada: una corrida y ya esta, no hay cola detras.
            self._run_ubs_seed_evaluation()
            return True
        if stage_key == "seed_final_tick":
            self._run_ubs_seed_final_tick()
            return True
        if stage_key == "seed_final_tick_6m":
            self._run_ubs_seed_final_tick_6m()
            return True
        if stage_key == "candidates":
            self.ubs_results_run_id.set(f"#{run_id}")
            return self._retry_visible_ubs_run_mismatches(run_id=run_id, confirm=False)
        if stage_key == "robustness":
            return self._run_ubs_robustness_for_latest_run(
                confirm=False, pending_only=True, run_id_override=run_id
            )
        if stage_key == "final_tick":
            return self._run_ubs_final_tick_for_latest_run(
                confirm=False, pending_only=True, final_tick_stage="probe", run_id_override=run_id
            )
        if stage_key == "final_tick_6m":
            return self._run_ubs_final_tick_for_latest_run(
                confirm=False, pending_only=True, final_tick_stage="six_month",
                run_id_override=run_id,
            )
        if stage_key == "regression":
            return self._run_ubs_regression_for_latest_run(
                confirm=False, pending_only=True, run_id_override=run_id
            )
        messagebox.showinfo(
            "Auditor UBS",
            f"La etapa {stage_spec(stage_key).label} no tiene repeticion de pendientes.",
        )
        return False
