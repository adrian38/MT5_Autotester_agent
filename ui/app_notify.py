"""Avisos de Telegram con el resumen de lo que acaba de terminar."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import telegram_notify
from ubs.db import connect_memory
from mt5_env import env_value
from run_tests import RUNNING_TERMINAL_EXIT_CODE
from ubs.account import (
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_memory_path,
    normalize_account_type,
    normalize_broker,
)
from ui.app_base import BASE_DIR


class AppNotifyMixin:
    """Avisos de Telegram con el resumen de lo que acaba de terminar."""

    def _notify_telegram(self, message: str) -> None:
        if not self.telegram_enabled.get():
            return
        token_set = bool(env_value("TELEGRAM_BOT_TOKEN"))
        chat_set = bool(env_value("TELEGRAM_CHAT_ID"))
        if not token_set or not chat_set:
            self.output_queue.put(
                "[Telegram] No se envia: falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env\n"
            )
            return
        self.output_queue.put(f"[Telegram] Enviando: {message}\n")

        def on_result(error: str | None) -> None:
            if error:
                self.output_queue.put(f"[Telegram] ERROR: {error}\n")
            else:
                self.output_queue.put("[Telegram] Mensaje enviado correctamente.\n")

        telegram_notify.send_async(message, on_result=on_result)

    def _arg_value(self, args: list[str], flag: str) -> str:
        try:
            index = args.index(flag)
        except ValueError:
            return ""
        next_index = index + 1
        return args[next_index] if next_index < len(args) else ""

    def _ubs_notification_memory_path(self, args: list[str]) -> Path:
        raw = self._arg_value(args, "--memory")
        return Path(raw).expanduser() if raw else account_memory_path(BASE_DIR, self.ubs_account_type.get(), self.ubs_broker.get())

    def _ubs_notification_account_label(self, args: list[str]) -> str:
        broker_raw = self._arg_value(args, "--broker") or self.ubs_broker.get()
        broker = normalize_broker(broker_raw or DEFAULT_BROKER)
        account_raw = self._arg_value(args, "--account-type") or self.ubs_account_type.get()
        account = normalize_account_type(account_raw or DEFAULT_ACCOUNT_TYPE, broker)
        return f"{broker}/{account}"

    def _ubs_notification_header(self, mode: str, prefix: str, account_label: str) -> str:
        return f"MT5 Autotester: {mode} terminado ({prefix}).\nBroker/Cuenta: {account_label}"

    def _ubs_status_counts(self, conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> dict[str, int]:
        query = f"select status, count(*) as total from {table}"
        if where:
            query += f" where {where}"
        query += " group by status"
        return {str(row["status"] or "unknown"): int(row["total"] or 0) for row in conn.execute(query, params)}

    def _ubs_agent_notification_message(self, code: int, args: list[str]) -> str:
        prefix = "OK" if code == 0 else f"ERROR codigo {code}"
        memory_path = self._ubs_notification_memory_path(args)
        account_label = self._ubs_notification_account_label(args)
        mode = "UBS Agente"
        if "--probe-universe-history" in args:
            mode = "UBS Probe historico"
        elif "--evaluate-robustness" in args:
            mode = "UBS Robustez OOS"
        elif "--evaluate-final-tick" in args:
            stage = (self._arg_value(args, "--final-tick-stage") or "").strip().lower().replace("-", "_")
            mode = "UBS Final Tick 6M" if stage in {"six_month", "6m", "sixmonth"} else "UBS Final Tick corto"
        elif "--evaluate-regression" in args:
            mode = "UBS Regresiva OHLC"
        elif "--rescore-regression-only" in args:
            mode = "UBS Regresiva rescore"
        elif "--evaluate-seeds" in args:
            mode = "UBS Seeds"
        elif "--rescore-seeds-only" in args:
            mode = "UBS Seeds rescore"
        elif "--retry-candidate-id" in args:
            mode = "UBS retry candidato"
        elif "--retry-mismatch-run" in args:
            mode = "UBS retry run"
        elif "--continue-last-run" in args:
            mode = "UBS continuar run"

        if not memory_path.exists():
            return f"{self._ubs_notification_header(mode, prefix, account_label)}\nMemoria UBS no encontrada: {memory_path}"

        conn = None
        try:
            conn = connect_memory(memory_path)
            if "--evaluate-robustness" in args:
                run_id = int(self._arg_value(args, "--robust-run-id") or 0)
                if run_id <= 0:
                    row = conn.execute("select id from runs order by id desc limit 1").fetchone()
                    run_id = int(row["id"]) if row else 0
                counts = conn.execute(
                    """
                    select
                        count(*) as total,
                        sum(case when cr.status is not null then 1 else 0 end) as evaluated,
                        sum(case when cr.status='accepted' then 1 else 0 end) as ok,
                        sum(case when cr.status='rejected' then 1 else 0 end) as fail
                    from candidates c
                    left join candidate_robustness cr on cr.candidate_id=c.id
                    where c.run_id=? and c.status='accepted'
                    """,
                    (run_id,),
                ).fetchone()
                neutral = int(counts["evaluated"] or 0) - int(counts["ok"] or 0) - int(counts["fail"] or 0)
                return (
                    f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                    f"Run #{run_id} | accepted base: {int(counts['total'] or 0)} | "
                    f"OOS evaluados: {int(counts['evaluated'] or 0)} | "
                    f"OK: {int(counts['ok'] or 0)} | FAIL: {int(counts['fail'] or 0)} | neutros: {neutral}"
                )

            if "--evaluate-final-tick" in args:
                stage = (self._arg_value(args, "--final-tick-stage") or "").strip().lower().replace("-", "_")
                is_6m = stage in {"six_month", "6m", "sixmonth"}
                run_id = int(self._arg_value(args, "--final-tick-run-id") or 0)
                if run_id <= 0:
                    row = conn.execute("select id from runs order by id desc limit 1").fetchone()
                    run_id = int(row["id"]) if row else 0
                if is_6m:
                    counts = conn.execute(
                        """
                        select
                            count(*) as total,
                            sum(case when ft6.status is not null then 1 else 0 end) as evaluated,
                            sum(case when ft6.status='accepted' then 1 else 0 end) as ok,
                            sum(case when ft6.status='rejected' then 1 else 0 end) as fail
                        from candidates c
                        join candidate_robustness cr on cr.candidate_id=c.id
                        join candidate_final_tick ft on ft.candidate_id=c.id
                        left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
                        where c.run_id=?
                          and c.status='accepted'
                          and cr.status='accepted'
                          and ft.status in ('accepted', 'pending_ohlc_trades')
                        """,
                        (run_id,),
                    ).fetchone()
                    total_label = "elegibles 6M"
                    evaluated_label = "6M evaluados"
                else:
                    counts = conn.execute(
                        """
                        select
                            count(*) as total,
                            sum(case when ft.status is not null then 1 else 0 end) as evaluated,
                            sum(case when ft.status='accepted' then 1 else 0 end) as ok,
                            sum(case when ft.status='rejected' then 1 else 0 end) as fail
                        from candidates c
                        join candidate_robustness cr on cr.candidate_id=c.id
                        left join candidate_final_tick ft on ft.candidate_id=c.id
                        where c.run_id=? and c.status='accepted' and cr.status='accepted'
                        """,
                        (run_id,),
                    ).fetchone()
                    total_label = "robust accepted"
                    evaluated_label = "Final corto evaluados"
                neutral = int(counts["evaluated"] or 0) - int(counts["ok"] or 0) - int(counts["fail"] or 0)
                return (
                    f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                    f"Run #{run_id} | {total_label}: {int(counts['total'] or 0)} | "
                    f"{evaluated_label}: {int(counts['evaluated'] or 0)} | "
                    f"OK: {int(counts['ok'] or 0)} | FAIL: {int(counts['fail'] or 0)} | neutros: {neutral}"
                )

            if "--evaluate-regression" in args or "--rescore-regression-only" in args:
                run_id = int(self._arg_value(args, "--regression-run-id") or 0)
                if run_id <= 0:
                    row = conn.execute("select id from runs order by id desc limit 1").fetchone()
                    run_id = int(row["id"]) if row else 0
                counts = conn.execute(
                    """
                    select count(*) as total,
                           sum(case when rg.status is not null then 1 else 0 end) as evaluated,
                           sum(case when rg.status='accepted' then 1 else 0 end) as ok,
                           sum(case when rg.status in ('rejected','no_trades') then 1 else 0 end) as fail,
                           coalesce(sum(rg.points_applied),0) as points
                    from candidates c
                    join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id and ft6.status='accepted'
                    left join candidate_regression rg on rg.candidate_id=c.id
                    where c.run_id=? and c.status='accepted'
                    """,
                    (run_id,),
                ).fetchone()
                neutral = int(counts["evaluated"] or 0) - int(counts["ok"] or 0) - int(counts["fail"] or 0)
                return (
                    f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                    f"Run #{run_id} | 6M accepted: {int(counts['total'] or 0)} | "
                    f"evaluados: {int(counts['evaluated'] or 0)} | OK: {int(counts['ok'] or 0)} | "
                    f"FAIL: {int(counts['fail'] or 0)} | tecnicos: {neutral} | "
                    f"puntos: {float(counts['points'] or 0):+.0f}"
                )

            if "--evaluate-seeds" in args or "--rescore-seeds-only" in args:
                counts = self._ubs_status_counts(conn, "seed_scores", "active=1")
                total = sum(counts.values())
                return (
                    f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                    f"Seeds activas: {total} | accepted: {counts.get('accepted', 0)} | "
                    f"rejected: {counts.get('rejected', 0)} | no_trades: {counts.get('no_trades', 0)} | "
                    f"mismatch: {counts.get('report_mismatch', 0)} | pending: {counts.get('pending', 0)} | "
                    f"no_report: {counts.get('no_report', 0)}"
                )

            if "--probe-universe-history" in args:
                counts = self._ubs_status_counts(conn, "candidates", "policy='history_probe'")
                total = sum(counts.values())
                pending = total - counts.get("history_ok", 0) - counts.get("no_history", 0)
                return (
                    f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                    f"Probe historico | simbolos: {total} | history_ok: {counts.get('history_ok', 0)} | "
                    f"no_history: {counts.get('no_history', 0)} | pendientes: {pending} | "
                    f"mismatch: {counts.get('report_mismatch', 0)} | no_report: {counts.get('no_report', 0)}"
                )

            if "--retry-candidate-id" in args:
                candidate_id = int(self._arg_value(args, "--retry-candidate-id") or 0)
                row = conn.execute("select * from candidates where id=?", (candidate_id,)).fetchone()
                if row:
                    metrics = {}
                    try:
                        metrics = json.loads(row["metrics_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        metrics = {}
                    reasons = ", ".join(metrics.get("reasons") or []) or "-"
                    return (
                        f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                        f"Candidate #{candidate_id} | run #{row['run_id']} | {row['target_symbol']} {row['period']} | "
                        f"estado: {row['status']} | score: {self._format_ubs_number(row['score'])} | motivo: {reasons}"
                    )

            run_id = int(self._arg_value(args, "--retry-run-id") or 0)
            if run_id <= 0:
                row = conn.execute("select id from runs where hidden=0 order by id desc limit 1").fetchone()
                if row is None:
                    row = conn.execute("select id from runs order by id desc limit 1").fetchone()
                run_id = int(row["id"]) if row else 0
            counts = self._ubs_status_counts(conn, "candidates", "run_id=?", (run_id,))
            robust = conn.execute(
                """
                select
                    sum(case when cr.status='accepted' then 1 else 0 end) as ok,
                    sum(case when cr.status='rejected' then 1 else 0 end) as fail
                from candidates c
                left join candidate_robustness cr on cr.candidate_id=c.id
                where c.run_id=? and c.status='accepted'
                """,
                (run_id,),
            ).fetchone()
            total = sum(counts.values())
            return (
                f"{self._ubs_notification_header(mode, prefix, account_label)}\n"
                f"Run #{run_id} | candidatos: {total} | accepted: {counts.get('accepted', 0)} | "
                f"rejected: {counts.get('rejected', 0)} | no_trades: {counts.get('no_trades', 0)} | "
                f"history_ok: {counts.get('history_ok', 0)} | no_history: {counts.get('no_history', 0)} | "
                f"mismatch: {counts.get('report_mismatch', 0)} | no_report: {counts.get('no_report', 0)} | "
                f"robust OK/FAIL: {int(robust['ok'] or 0)}/{int(robust['fail'] or 0)}"
            )
        except Exception as exc:
            return f"{self._ubs_notification_header(mode, prefix, account_label)}\nNo se pudo leer resumen UBS: {exc}"
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    def _completion_notification_message(self, script_name: str, args: list[str], code: int) -> str:
        if script_name == "ubs_agent.py":
            return self._ubs_agent_notification_message(code, args)
        if code == 0:
            return "MT5 Autotester: proceso finalizado correctamente."
        if code == RUNNING_TERMINAL_EXIT_CODE:
            return "MT5 Autotester: proceso cancelado porque MT5 ya estaba abierto."
        return f"MT5 Autotester: proceso terminado con error (codigo {code})."
