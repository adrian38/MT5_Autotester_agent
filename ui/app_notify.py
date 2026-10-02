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

    _UBS_NOTIFICATION_MODES = (
        ("--probe-universe-history", "UBS Probe historico"),
        ("--evaluate-robustness", "UBS Robustez OOS"),
        ("--evaluate-final-tick", None),
        ("--evaluate-regression", "UBS Regresiva OHLC"),
        ("--rescore-regression-only", "UBS Regresiva rescore"),
        ("--evaluate-seeds", "UBS Seeds"),
        ("--rescore-seeds-only", "UBS Seeds rescore"),
        ("--retry-candidate-id", "UBS retry candidato"),
        ("--retry-mismatch-run", "UBS retry run"),
        ("--continue-last-run", "UBS continuar run"),
    )

    def _ubs_notification_is_six_month(self, args: list[str]) -> bool:
        """Indica si la etapa de final tick pedida es la de seis meses."""
        stage = (self._arg_value(args, "--final-tick-stage") or "").strip().lower().replace("-", "_")
        return stage in {"six_month", "6m", "sixmonth"}

    def _ubs_notification_mode(self, args: list[str]) -> str:
        """Nombre del modo UBS deducido de los argumentos de la ejecucion."""
        for flag, label in self._UBS_NOTIFICATION_MODES:
            if flag not in args:
                continue
            if label is not None:
                return label
            return "UBS Final Tick 6M" if self._ubs_notification_is_six_month(args) else "UBS Final Tick corto"
        return "UBS Agente"

    def _ubs_notification_run_id(self, conn: sqlite3.Connection, args: list[str], flag: str) -> int:
        """Run pedido por argumento o, si no hay, el ultimo registrado."""
        run_id = int(self._arg_value(args, flag) or 0)
        if run_id > 0:
            return run_id
        row = conn.execute("select id from runs order by id desc limit 1").fetchone()
        return int(row["id"]) if row else 0

    @staticmethod
    def _ubs_notification_neutral(counts) -> int:
        """Evaluados que no acabaron ni en aceptado ni en rechazado."""
        return int(counts["evaluated"] or 0) - int(counts["ok"] or 0) - int(counts["fail"] or 0)

    def _ubs_notification_robustness(self, conn: sqlite3.Connection, args: list[str], header: str) -> str:
        """Resumen de la evaluacion de robustez OOS del run."""
        run_id = self._ubs_notification_run_id(conn, args, "--robust-run-id")
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
        return (
            f"{header}\n"
            f"Run #{run_id} | accepted base: {int(counts['total'] or 0)} | "
            f"OOS evaluados: {int(counts['evaluated'] or 0)} | "
            f"OK: {int(counts['ok'] or 0)} | FAIL: {int(counts['fail'] or 0)} | "
            f"neutros: {self._ubs_notification_neutral(counts)}"
        )

    @staticmethod
    def _ubs_notification_final_tick_counts(conn: sqlite3.Connection, run_id: int, is_6m: bool):
        """Recuento de final tick y etiquetas segun la etapa evaluada."""
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
            return counts, "elegibles 6M", "6M evaluados"
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
        return counts, "robust accepted", "Final corto evaluados"

    def _ubs_notification_final_tick(self, conn: sqlite3.Connection, args: list[str], header: str) -> str:
        """Resumen de la etapa de final tick corta o de seis meses."""
        run_id = self._ubs_notification_run_id(conn, args, "--final-tick-run-id")
        counts, total_label, evaluated_label = self._ubs_notification_final_tick_counts(
            conn, run_id, self._ubs_notification_is_six_month(args)
        )
        return (
            f"{header}\n"
            f"Run #{run_id} | {total_label}: {int(counts['total'] or 0)} | "
            f"{evaluated_label}: {int(counts['evaluated'] or 0)} | "
            f"OK: {int(counts['ok'] or 0)} | FAIL: {int(counts['fail'] or 0)} | "
            f"neutros: {self._ubs_notification_neutral(counts)}"
        )

    def _ubs_notification_regression(self, conn: sqlite3.Connection, args: list[str], header: str) -> str:
        """Resumen de la regresiva OHLC sobre los aceptados de seis meses."""
        run_id = self._ubs_notification_run_id(conn, args, "--regression-run-id")
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
        return (
            f"{header}\n"
            f"Run #{run_id} | 6M accepted: {int(counts['total'] or 0)} | "
            f"evaluados: {int(counts['evaluated'] or 0)} | OK: {int(counts['ok'] or 0)} | "
            f"FAIL: {int(counts['fail'] or 0)} | tecnicos: {self._ubs_notification_neutral(counts)} | "
            f"puntos: {float(counts['points'] or 0):+.0f}"
        )

    def _ubs_notification_seeds(self, conn: sqlite3.Connection, header: str) -> str:
        """Resumen del estado de las seeds activas."""
        counts = self._ubs_status_counts(conn, "seed_scores", "active=1")
        total = sum(counts.values())
        return (
            f"{header}\n"
            f"Seeds activas: {total} | accepted: {counts.get('accepted', 0)} | "
            f"rejected: {counts.get('rejected', 0)} | no_trades: {counts.get('no_trades', 0)} | "
            f"mismatch: {counts.get('report_mismatch', 0)} | pending: {counts.get('pending', 0)} | "
            f"no_report: {counts.get('no_report', 0)}"
        )

    def _ubs_notification_probe(self, conn: sqlite3.Connection, header: str) -> str:
        """Resumen del probe de historico del universo."""
        counts = self._ubs_status_counts(conn, "candidates", "policy='history_probe'")
        total = sum(counts.values())
        pending = total - counts.get("history_ok", 0) - counts.get("no_history", 0)
        return (
            f"{header}\n"
            f"Probe historico | simbolos: {total} | history_ok: {counts.get('history_ok', 0)} | "
            f"no_history: {counts.get('no_history', 0)} | pendientes: {pending} | "
            f"mismatch: {counts.get('report_mismatch', 0)} | no_report: {counts.get('no_report', 0)}"
        )

    def _ubs_notification_candidate(self, conn: sqlite3.Connection, args: list[str], header: str) -> str | None:
        """Ficha del candidato reintentado, o None si ya no esta en memoria."""
        candidate_id = int(self._arg_value(args, "--retry-candidate-id") or 0)
        row = conn.execute("select * from candidates where id=?", (candidate_id,)).fetchone()
        if not row:
            return None
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metrics = {}
        reasons = ", ".join(metrics.get("reasons") or []) or "-"
        return (
            f"{header}\n"
            f"Candidate #{candidate_id} | run #{row['run_id']} | {row['target_symbol']} {row['period']} | "
            f"estado: {row['status']} | score: {self._format_ubs_number(row['score'])} | motivo: {reasons}"
        )

    def _ubs_notification_run_summary(self, conn: sqlite3.Connection, args: list[str], header: str) -> str:
        """Resumen general del run: candidatos por estado y robustez."""
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
            f"{header}\n"
            f"Run #{run_id} | candidatos: {total} | accepted: {counts.get('accepted', 0)} | "
            f"rejected: {counts.get('rejected', 0)} | no_trades: {counts.get('no_trades', 0)} | "
            f"history_ok: {counts.get('history_ok', 0)} | no_history: {counts.get('no_history', 0)} | "
            f"mismatch: {counts.get('report_mismatch', 0)} | no_report: {counts.get('no_report', 0)} | "
            f"robust OK/FAIL: {int(robust['ok'] or 0)}/{int(robust['fail'] or 0)}"
        )

    def _ubs_notification_summary(self, conn: sqlite3.Connection, args: list[str], header: str) -> str:
        """Elige el resumen que corresponde al modo UBS ejecutado."""
        if "--evaluate-robustness" in args:
            return self._ubs_notification_robustness(conn, args, header)
        if "--evaluate-final-tick" in args:
            return self._ubs_notification_final_tick(conn, args, header)
        if "--evaluate-regression" in args or "--rescore-regression-only" in args:
            return self._ubs_notification_regression(conn, args, header)
        if "--evaluate-seeds" in args or "--rescore-seeds-only" in args:
            return self._ubs_notification_seeds(conn, header)
        if "--probe-universe-history" in args:
            return self._ubs_notification_probe(conn, header)
        if "--retry-candidate-id" in args:
            message = self._ubs_notification_candidate(conn, args, header)
            if message is not None:
                return message
        return self._ubs_notification_run_summary(conn, args, header)

    def _ubs_agent_notification_message(self, code: int, args: list[str]) -> str:
        prefix = "OK" if code == 0 else f"ERROR codigo {code}"
        memory_path = self._ubs_notification_memory_path(args)
        account_label = self._ubs_notification_account_label(args)
        header = self._ubs_notification_header(self._ubs_notification_mode(args), prefix, account_label)
        if not memory_path.exists():
            return f"{header}\nMemoria UBS no encontrada: {memory_path}"
        conn = None
        try:
            conn = connect_memory(memory_path)
            return self._ubs_notification_summary(conn, args, header)
        except Exception as exc:
            return f"{header}\nNo se pudo leer resumen UBS: {exc}"
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
