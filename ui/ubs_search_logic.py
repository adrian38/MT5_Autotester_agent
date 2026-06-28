from __future__ import annotations

from datetime import datetime
from html import unescape
import json
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    account_memory_path,
    account_types_for_broker,
    normalize_account_type,
    normalize_broker,
)
from ubs.db import connect_memory
from ubs.weights import (
    ASSET_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_ACCEPTED_BONUS,
    DEFAULT_FINAL_TICK_REJECTED_PENALTY,
    DEFAULT_ROBUST_NEGATIVE_BONUS,
    DEFAULT_ROBUST_POSITIVE_BONUS,
    FINAL_TICK_REASON_PENALTIES,
    NO_TRADES_WEIGHT,
    REJECTED_BASE_PENALTY,
    REJECTED_REASON_PENALTIES,
    ROBUST_REASON_PENALTIES,
    feedback_weight,
    metric_reasons,
    reason_penalty,
    robust_bonus,
)


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


AUDIT_FINAL_STATUSES = {"accepted", "rejected"}


def audit_nonfinal_count(
    status_counts: dict[str, int],
    *,
    additional_final_statuses: set[str] | frozenset[str] = frozenset(),
) -> int:
    """Count stored stage rows that have not reached a final pass/fail state."""
    final_statuses = AUDIT_FINAL_STATUSES | {
        str(status or "").strip().lower() for status in additional_final_statuses
    }
    return sum(
        int(count or 0)
        for status, count in status_counts.items()
        if str(status or "").strip().lower() not in final_statuses
    )


class UBSSearchLogicMixin:
    def _ubs_active_broker_account_contexts(self) -> tuple[tuple[str, str], ...]:
        broker = self._ubs_broker()
        return tuple((broker, account) for account in account_types_for_broker(broker))

    def _ubs_account_context_label(self, broker: object, account_type: object) -> str:
        broker_key = normalize_broker(broker)
        account = normalize_account_type(account_type, broker_key)
        return f"{broker_key}/{account}"

    def _ubs_account_context_file_label(self, value: object) -> str:
        return str(value or "").strip().replace("\\", "_").replace("/", "_") or "UBS"

    def _parse_ubs_account_context(self, value: object) -> tuple[str, str] | None:
        text = str(value or "").strip().upper().replace("\\", "/")
        if not text:
            return self._ubs_broker(), self._ubs_account_type()
        if "/" in text or ":" in text:
            separator = "/" if "/" in text else ":"
            broker_raw, account_raw = text.split(separator, 1)
            broker = normalize_broker(broker_raw)
            account = normalize_account_type(account_raw, broker)
            if (broker, account) in self._ubs_active_broker_account_contexts():
                return broker, account
            return None
        if text in ACCOUNT_TYPES:
            broker = self._ubs_broker()
            account = normalize_account_type(text, broker)
            if (broker, account) not in self._ubs_active_broker_account_contexts():
                return None
            return broker, account
        return None

    def _refresh_ubs_audit_account_values(self) -> None:
        combo = getattr(self, "ubs_audit_account_combo", None)
        values = tuple(
            self._ubs_account_context_label(broker, account)
            for broker, account in self._ubs_active_broker_account_contexts()
        )
        if combo is not None:
            combo.configure(values=values)
        context = self._parse_ubs_account_context(self.ubs_audit_account.get())
        current = self._ubs_account_context_label(*context) if context else (values[0] if values else "")
        if self.ubs_audit_account.get() != current:
            self.ubs_audit_account.set(current)

    def _refresh_ubs_audit_run_combo(self) -> None:
        combo = getattr(self, "ubs_audit_run_combo", None)
        if combo is None:
            return
        context = self._parse_ubs_account_context(self.ubs_audit_account.get())
        if context is None:
            combo.configure(values=())
            self.ubs_audit_run_id.set("")
            return
        broker, account_type = context
        memory_path = account_memory_path(BASE_DIR, account_type, broker)
        account_label = self._ubs_account_context_label(broker, account_type)
        if not memory_path.exists():
            combo.configure(values=())
            self.ubs_audit_run_id.set("")
            self.ubs_audit_status.set(f"Sin memoria {account_label}.")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    select
                        r.id,
                        r.created_at,
                        r.hidden,
                        count(c.id) as total,
                        sum(case when c.status='accepted' then 1 else 0 end) as accepted,
                        sum(case when c.status='rejected' then 1 else 0 end) as rejected,
                        sum(case when c.status='no_trades' then 1 else 0 end) as no_trades
                    from runs r
                    left join candidates c on c.run_id=r.id
                    group by r.id
                    order by r.id desc
                    """
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            combo.configure(values=())
            self.ubs_audit_status.set(f"Error runs {account_label}: {exc}")
            return

        labels = []
        for row in rows:
            hidden = " [arch]" if row["hidden"] else ""
            labels.append(
                f"#{row['id']} | {str(row['created_at'] or '')[:16]} | cand {int(row['total'] or 0)} "
                f"| OK {int(row['accepted'] or 0)} FAIL {int(row['rejected'] or 0)} 0ops {int(row['no_trades'] or 0)}{hidden}"
            )
        combo.configure(values=labels)
        current = str(self.ubs_audit_run_id.get() or "").strip()
        current_id = self._parse_ubs_audit_run_id(current)
        selected = ""
        if current_id:
            selected = next((label for label in labels if label.startswith(f"#{current_id} ")), "")
        if not selected and labels:
            selected = labels[0]
        self.ubs_audit_run_id.set(selected)
        self.ubs_audit_status.set("Selecciona run y audita.")

    def _parse_ubs_audit_run_id(self, value: object) -> int:
        match = re.search(r"#?(\d+)", str(value or "").strip())
        return int(match.group(1)) if match else 0

    def _detect_ubs_report_account_header(self, path: Path, expected_broker: object) -> tuple[str, str, str]:
        try:
            raw = path.read_bytes()[:12000]
        except OSError:
            return "", "", ""
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="ignore")

        cleaned = re.sub(r"<[^>]+>", " ", text)
        cleaned = unescape(re.sub(r"\s+", " ", cleaned)).strip()
        token_map = {
            "ROBOFOREX": ("RoboForex",),
            "ICTRADING": ("ICTrading", "IC Trading", "ICMarkets", "IC Markets"),
            "AXI": ("AXI",),
        }
        detected_broker = ""
        for broker in BROKERS:
            if any(re.search(rf"\b{re.escape(token)}\b", cleaned, flags=re.IGNORECASE) for token in token_map.get(broker, ())):
                detected_broker = broker
                break
        if not detected_broker:
            expected = normalize_broker(expected_broker)
            if any(re.search(rf"\b{re.escape(token)}\b", cleaned, flags=re.IGNORECASE) for token in token_map.get(expected, ())):
                detected_broker = expected
        if not detected_broker:
            return "", "", ""

        header = ""
        broker_tokens = token_map.get(detected_broker, ())
        if broker_tokens:
            token_pattern = "|".join(re.escape(token) for token in broker_tokens)
            match = re.search(
                rf"([^<\r\n]*?(?:{token_pattern})[^<\r\n(]*\s*\(Build\s+\d+\))",
                text,
                flags=re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    rf"([A-Za-z0-9 ._-]*(?:{token_pattern})[A-Za-z0-9 ._-]*\s*\(Build\s+\d+\))",
                    cleaned,
                    flags=re.IGNORECASE,
                )
            header = unescape(match.group(1)).strip() if match else ""
        if not header:
            header = cleaned[:160]

        accounts = account_types_for_broker(detected_broker)
        detected_account = ""
        account_probe = re.sub(r"[^A-Z0-9]+", " ", header.upper())
        for account in accounts:
            if account in account_probe:
                detected_account = account
                break
        if not detected_account and len(accounts) == 1:
            detected_account = accounts[0]
        return header, detected_broker, detected_account

    def _run_ubs_audit_from_search(self) -> None:
        context = self._parse_ubs_account_context(self.ubs_audit_account.get())
        if context is None:
            self.ubs_audit_status.set("Cuenta invalida.")
            return
        broker, account_type = context
        account_label = self._ubs_account_context_label(broker, account_type)
        run_id = self._parse_ubs_audit_run_id(self.ubs_audit_run_id.get())
        if run_id <= 0:
            self.ubs_audit_status.set("Run invalido.")
            return
        memory_path = account_memory_path(BASE_DIR, account_type, broker)
        if not memory_path.exists():
            self.ubs_audit_status.set(f"No existe memoria {account_label}.")
            return
        try:
            summary, report_path = self._build_ubs_run_audit(memory_path, account_label, run_id)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._show_error("Auditoria run UBS", str(exc))
            return
        self.ubs_audit_report_path.set(str(report_path))
        self._populate_ubs_audit_summary(summary)
        self.ubs_audit_status.set(f"Guardada: {report_path.name}")

    def _open_ubs_audit_report(self) -> None:
        path = Path(str(self.ubs_audit_report_path.get() or ""))
        if not path.exists():
            messagebox.showinfo("Auditoria run UBS", "No hay auditoria generada para abrir.")
            return
        self._open_local_file(path)

    def _populate_ubs_audit_summary(self, rows: list[tuple]) -> None:
        self.ubs_audit_details = {}
        audit_trees = getattr(self, "ubs_audit_trees", None)
        if isinstance(audit_trees, dict) and audit_trees:
            for tree in audit_trees.values():
                for item in tree.get_children():
                    tree.delete(item)
            groups = {
                "Generacion": {"Run", "Base", "Sets/reportes", "Cuenta MT5 base"},
                "Robustez": {"Robustez", "Cuenta MT5 robustez"},
                "Final Tick": {"Final Tick corto", "Cuenta MT5 FT corto"},
                "Final Tick 6M": {"Final Tick 6M", "Cuenta MT5 FT 6M"},
                "Pesos": {"Formula pesos", "Peso run", "Detalle pesos", "Hallazgo"},
            }
            status_by_tag = {
                "accepted": "OK",
                "rejected": "REVISAR",
                "pending": "INFO",
            }
            for row in rows:
                metric, value, tag, *details = row
                target = "Pesos"
                for group_name, metrics in groups.items():
                    if metric in metrics:
                        target = group_name
                        break
                tree = audit_trees.get(target)
                if tree is None:
                    continue
                row_tag = tag or "pending"
                item = tree.insert(
                    "",
                    "end",
                    values=(metric, status_by_tag.get(row_tag, "INFO"), value),
                    tags=(row_tag,),
                )
                if details:
                    detail = details[0]
                    detail_key = (id(tree), item)
                    if isinstance(detail, list) and detail:
                        self.ubs_audit_details[detail_key] = "\n".join(str(part) for part in detail)
                    elif detail:
                        self.ubs_audit_details[detail_key] = str(detail)
            return
        if not hasattr(self, "ubs_audit_tree"):
            return
        for item in self.ubs_audit_tree.get_children():
            self.ubs_audit_tree.delete(item)
        status_by_tag = {
            "accepted": "OK",
            "rejected": "REVISAR",
            "pending": "INFO",
        }
        for index, row in enumerate(rows):
            metric, value, tag, *details = row
            row_tag = tag or "pending"
            item = self.ubs_audit_tree.insert(
                "",
                "end",
                values=(metric, status_by_tag.get(row_tag, "INFO"), value),
                tags=(row_tag,),
            )
            if details:
                detail = details[0]
                detail_key = (id(self.ubs_audit_tree), item)
                if isinstance(detail, list) and detail:
                    self.ubs_audit_details[detail_key] = "\n".join(str(part) for part in detail)
                elif detail:
                    self.ubs_audit_details[detail_key] = str(detail)
            if index < len(rows) - 1:
                self.ubs_audit_tree.insert("", "end", values=("", "", ""), tags=("separator",))

    def _on_ubs_audit_detail_double_click(self, event) -> None:
        tree = event.widget
        item = tree.focus()
        if not item:
            return
        values = tree.item(item, "values")
        title = str(values[0]) if values else "Auditoria run UBS"
        details = getattr(self, "ubs_audit_details", {})
        detail = details.get((id(tree), item), details.get(item, ""))
        if not detail:
            messagebox.showinfo("Auditoria run UBS", "Esta fila no tiene detalle adicional.")
            return
        self._show_ubs_audit_detail_window(title, detail)

    def _show_ubs_audit_detail_window(self, title: str, detail: str) -> None:
        parent = getattr(self, "root", self)
        window = tk.Toplevel(parent)
        window.title(title)
        window.geometry("1180x520")
        window.configure(bg=self.colors["panel"])
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        tk.Label(
            window,
            text=title,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        lines = [line for line in detail.splitlines() if line.strip()]
        if lines and "\t" in lines[0]:
            header_labels = [part.strip() or f"COL {idx + 1}" for idx, part in enumerate(lines[0].split("\t"))]
            data_lines = lines[1:]
        else:
            header_labels = ["GEN", "ID", "SET", "TEST", "ESPERADO", "REPORTE", "HEADER", "ARCHIVO"]
            data_lines = lines
        columns = tuple(f"c{index}" for index in range(len(header_labels)))
        detail_tree = ttk.Treeview(frame, columns=columns, show="headings", height=16, selectmode="extended")
        width_by_label = {
            "GEN": 55,
            "ID": 80,
            "SET": 360,
            "TEST": 120,
            "ESPERADO": 90,
            "REPORTE": 90,
            "HEADER": 210,
            "ARCHIVO": 390,
            "STATUS": 110,
            "BASE": 86,
            "ROBUST": 86,
            "FT CORTO": 86,
            "FT 6M": 86,
            "PESO": 86,
            "CHECK": 90,
            "RAZONES": 300,
        }
        for column in columns:
            label = header_labels[int(column[1:])]
            detail_tree.heading(column, text=label)
            detail_tree.column(column, width=width_by_label.get(label.upper(), 130), minwidth=42, anchor="w", stretch=False)
        detail_tree.tag_configure("rejected", foreground=self.colors["danger"])
        detail_tree.tag_configure("pending", foreground=self.colors["muted"])
        detail_tree.tag_configure("accepted", foreground=self.colors["accent"])

        inserted_items: list[str] = []
        header_index = {label.strip().upper(): idx for idx, label in enumerate(header_labels)}
        check_index = header_index.get("CHECK")
        status_index = header_index.get("STATUS") if "STATUS" in header_index else header_index.get("ESTADO")
        for line in data_lines:
            parts = line.split("\t")
            if len(parts) < len(columns):
                parts = [*parts, *([""] * (len(columns) - len(parts)))]
            check_value = str(parts[check_index]).strip().upper() if check_index is not None and len(parts) > check_index else ""
            status_value = str(parts[status_index]).strip().casefold() if status_index is not None and len(parts) > status_index else ""
            text = "\t".join(parts).casefold()
            if check_value == "OK":
                tag = "accepted"
            elif check_value:
                tag = "rejected"
            elif "sin_encabezado" in text or "pend" in text:
                tag = "pending"
            elif status_value in {"accepted", "aceptado", "ok"}:
                tag = "accepted"
            elif status_value in {"rejected", "rechazado", "revisar"}:
                tag = "rejected"
            else:
                tag = "pending"
            inserted_items.append(detail_tree.insert("", "end", values=parts[: len(columns)], tags=(tag,)))

        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=detail_tree.xview)
        detail_tree.configure(xscrollcommand=xscroll.set)
        detail_tree.grid(row=0, column=0, sticky="nsew")
        xscroll.grid(row=1, column=0, sticky="ew")

        actions = tk.Frame(window, bg=self.colors["panel_alt"])
        actions.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        actions.columnconfigure(0, weight=1)
        can_mark_pending = {"ID", "TEST"}.issubset({label.strip().upper() for label in header_labels})
        id_index = header_index.get("ID")
        unique_candidate_ids = set()
        if id_index is not None:
            unique_candidate_ids = {
                str(detail_tree.item(item, "values")[id_index]).strip()
                for item in inserted_items
                if len(detail_tree.item(item, "values")) > id_index
                and str(detail_tree.item(item, "values")[id_index]).strip()
            }
        count_label = "Filas reporte" if can_mark_pending else "Filas"
        tk.Label(
            actions,
            text=f"{count_label}: {len(inserted_items)} | candidatos unicos: {len(unique_candidate_ids)}",
            bg=self.colors["panel_alt"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        next_column = 1
        if can_mark_pending:
            tk.Button(
                actions,
                text="Poner pendientes",
                bg=self.colors["accent"],
                fg="#ffffff",
                relief="flat",
                borderwidth=0,
                padx=10,
                pady=5,
                font=("Segoe UI", 9, "bold"),
                cursor="hand2",
                command=lambda: self._reset_ubs_audit_detail_rows_pending(title, detail_tree, window),
            ).grid(row=0, column=next_column, sticky="e", padx=(0, 6), pady=5)
            next_column += 1
        tk.Button(
            actions,
            text="Cerrar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=window.destroy,
        ).grid(row=0, column=next_column, sticky="e", padx=(0, 10), pady=5)

    def _reset_ubs_audit_detail_rows_pending(
        self,
        title: str,
        detail_tree: ttk.Treeview,
        window: tk.Toplevel,
    ) -> None:
        selected_items = list(detail_tree.selection()) or list(detail_tree.get_children())
        if not selected_items:
            messagebox.showinfo("Auditoria run UBS", "No hay filas para poner pendientes.")
            return

        stage_ids: dict[str, set[int]] = {"base": set(), "robust": set(), "final_tick": set(), "final_tick_6m": set()}
        for item in selected_items:
            values = detail_tree.item(item, "values")
            if len(values) < 4:
                continue
            try:
                candidate_id = int(str(values[1]).strip())
            except (TypeError, ValueError):
                continue
            test_name = f"{values[3]} {title}".casefold()
            if "6m" in test_name:
                stage_ids["final_tick_6m"].add(candidate_id)
            elif "ft corto" in test_name or "final tick" in test_name:
                stage_ids["final_tick"].add(candidate_id)
            elif "robust" in test_name:
                stage_ids["robust"].add(candidate_id)
            elif "base" in test_name or "generacion" in test_name:
                stage_ids["base"].add(candidate_id)

        total_ids = sum(len(ids) for ids in stage_ids.values())
        if total_ids == 0:
            messagebox.showinfo("Auditoria run UBS", "No pude identificar la prueba de esas filas.")
            return

        context = self._parse_ubs_account_context(self.ubs_audit_account.get())
        if context is None:
            messagebox.showerror("Auditoria run UBS", "Cuenta invalida.")
            return
        broker, account_type = context
        run_id = self._parse_ubs_audit_run_id(self.ubs_audit_run_id.get())
        if run_id <= 0:
            messagebox.showerror("Auditoria run UBS", "Run invalido.")
            return

        selected_report_rows = len(selected_items)
        summary = []
        if stage_ids["base"]:
            summary.append(f"Base/generacion: {len(stage_ids['base'])} candidato(s)")
        if stage_ids["robust"]:
            summary.append(f"Robustez: {len(stage_ids['robust'])} candidato(s)")
        if stage_ids["final_tick"]:
            summary.append(f"Final Tick corto: {len(stage_ids['final_tick'])} candidato(s)")
        if stage_ids["final_tick_6m"]:
            summary.append(f"Final Tick 6M: {len(stage_ids['final_tick_6m'])} candidato(s)")
        if not messagebox.askyesno(
            "Poner pendientes",
            "Se marcaran como pendientes para que se vuelvan a ejecutar:\n\n"
            + "\n".join(summary)
            + f"\n\nFilas de reporte seleccionadas: {selected_report_rows}"
            + "\n\nNo se borraran candidatos, archivos .set ni reportes del disco.",
        ):
            return

        memory_path = account_memory_path(BASE_DIR, account_type, broker)
        try:
            conn = connect_memory(memory_path)
            try:
                self._ensure_ubs_memory_schema(conn)
                changed = self._reset_ubs_candidate_stage_rows(conn, run_id, stage_ids)
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            messagebox.showerror("Auditoria run UBS", str(exc))
            return

        self.ubs_audit_status.set(
            "Pendientes actualizados: "
            + ", ".join(f"{stage}={count}" for stage, count in changed.items() if count)
        )
        window.destroy()
        self._run_ubs_audit_from_search()
        for label, callback_name in (
            ("ubs_results", "_refresh_ubs_results"),
            ("ubs_robustness", "_refresh_ubs_robustness"),
            ("ubs_final_tick", "_refresh_ubs_final_tick"),
            ("ubs_final_tick_6m", "_refresh_ubs_final_tick_6m"),
            ("ubs_universe", "_refresh_ubs_universe"),
        ):
            callback = getattr(self, callback_name, None)
            if callback is not None:
                self._safe_refresh(label, callback)

    def _reset_ubs_candidate_stage_rows(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        stage_ids: dict[str, set[int]],
    ) -> dict[str, int]:
        def scoped(ids: set[int]) -> list[int]:
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"select id from candidates where run_id=? and id in ({placeholders})",
                (int(run_id), *sorted(ids)),
            ).fetchall()
            return [int(row[0]) for row in rows]

        def delete_stage(table: str, ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            placeholders = ",".join("?" for _ in valid_ids)
            cur = conn.execute(
                f"delete from {table} where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *valid_ids),
            )
            return int(cur.rowcount or 0)

        def mark_stage_pending(table: str, ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            now = datetime.now().isoformat(timespec="seconds")
            for candidate_id in valid_ids:
                conn.execute(
                    f"""
                    insert into {table} (
                        candidate_id, run_id, status, accepted,
                        ohlc_report_path, real_tick_report_path,
                        ohlc_score, real_tick_score,
                        ohlc_metrics_json, real_tick_metrics_json, similarity_json,
                        history_quality, min_history_quality, from_date, to_date,
                        max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
                        evaluated_at
                    ) values (?, ?, 'pending', null, null, null, null, null, null, null, null, null, 80.0, '', '', 35.0, 35.0, 35.0, 35.0, ?)
                    on conflict(candidate_id) do update set
                        run_id=excluded.run_id,
                        status='pending',
                        accepted=null,
                        ohlc_report_path=null,
                        real_tick_report_path=null,
                        ohlc_score=null,
                        real_tick_score=null,
                        ohlc_metrics_json=null,
                        real_tick_metrics_json=null,
                        similarity_json=null,
                        history_quality=null,
                        from_date='',
                        to_date='',
                        evaluated_at=excluded.evaluated_at
                    """,
                    (candidate_id, int(run_id), now),
                )
            return len(valid_ids)

        def mark_robust_pending(ids: set[int]) -> int:
            valid_ids = scoped(ids)
            if not valid_ids:
                return 0
            now = datetime.now().isoformat(timespec="seconds")
            for candidate_id in valid_ids:
                conn.execute(
                    """
                    insert into candidate_robustness (
                        candidate_id, run_id, status, report_path, score, accepted, metrics_json,
                        from_date, to_date, positive_bonus, negative_bonus, evaluated_at
                    ) values (?, ?, 'pending', null, null, null, null, '', '', 70.0, -70.0, ?)
                    on conflict(candidate_id) do update set
                        run_id=excluded.run_id,
                        status='pending',
                        report_path=null,
                        score=null,
                        accepted=null,
                        metrics_json=null,
                        from_date='',
                        to_date='',
                        evaluated_at=excluded.evaluated_at
                    """,
                    (candidate_id, int(run_id), now),
                )
            return len(valid_ids)

        changed = {"base": 0, "robust": 0, "final_tick": 0, "final_tick_6m": 0}

        base_ids = set(scoped(stage_ids.get("base", set())))
        if base_ids:
            placeholders = ",".join("?" for _ in base_ids)
            conn.execute(
                f"delete from candidate_final_tick_6m where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            conn.execute(
                f"delete from candidate_final_tick where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            conn.execute(
                f"delete from candidate_robustness where run_id=? and candidate_id in ({placeholders})",
                (int(run_id), *sorted(base_ids)),
            )
            cur = conn.execute(
                f"""
                update candidates
                set status='generated',
                    report_path=null,
                    score=null,
                    accepted=null,
                    metrics_json=null
                where run_id=? and id in ({placeholders})
                """,
                (int(run_id), *sorted(base_ids)),
            )
            changed["base"] = int(cur.rowcount or 0)

        robust_ids = set(scoped(stage_ids.get("robust", set())))
        if robust_ids:
            delete_stage("candidate_final_tick_6m", robust_ids)
            delete_stage("candidate_final_tick", robust_ids)
            changed["robust"] = mark_robust_pending(robust_ids)

        final_tick_ids = set(scoped(stage_ids.get("final_tick", set())))
        if final_tick_ids:
            delete_stage("candidate_final_tick_6m", final_tick_ids)
            changed["final_tick"] = mark_stage_pending("candidate_final_tick", final_tick_ids)

        final_tick_6m_ids = set(scoped(stage_ids.get("final_tick_6m", set())))
        if final_tick_6m_ids:
            changed["final_tick_6m"] = mark_stage_pending("candidate_final_tick_6m", final_tick_6m_ids)

        return changed

    def _build_ubs_run_audit(
        self,
        memory_path: Path,
        account_type: str,
        run_id: int,
    ) -> tuple[list[tuple[str, str, str]], Path]:
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_ubs_memory_schema(conn)
            run = conn.execute("select * from runs where id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"Run #{run_id} no existe en {account_type}.")
            out, summary = self._compose_ubs_run_audit(conn, account_type, run)
        finally:
            conn.close()
        report_path = BASE_DIR / "outputs" / f"run{run_id}_{self._ubs_account_context_file_label(account_type)}_audit.txt"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return summary, report_path

    def _compose_ubs_run_audit(
        self,
        conn: sqlite3.Connection,
        account_type: str,
        run: sqlite3.Row,
    ) -> tuple[list[str], list[tuple[str, str, str]]]:
        run_id = int(run["id"])
        run_dir = Path(str(run["output_dir"] or ""))

        def rows(sql: str, params: tuple[object, ...] = (run_id,)) -> list[sqlite3.Row]:
            return conn.execute(sql, params).fetchall()

        def one(sql: str, params: tuple[object, ...] = (run_id,)) -> int:
            value = conn.execute(sql, params).fetchone()[0]
            return int(value or 0)

        def counts(sql: str, params: tuple[object, ...] = (run_id,)) -> dict[str, int]:
            return {str(row[0]): int(row[1] or 0) for row in conn.execute(sql, params)}

        def fmt_counts(data: dict[str, int]) -> str:
            return ", ".join(f"{key}={value}" for key, value in sorted(data.items())) or "sin filas"

        def stat(values: list[object]) -> str:
            nums: list[float] = []
            for value in values:
                try:
                    if value is not None:
                        nums.append(float(value))
                except (TypeError, ValueError):
                    pass
            if not nums:
                return "n=0"
            return (
                f"n={len(nums)} min={min(nums):.2f} avg={sum(nums)/len(nums):.2f} "
                f"med={statistics.median(nums):.2f} max={max(nums):.2f}"
            )

        def parse_json(raw: object) -> dict[str, object]:
            try:
                data = json.loads(str(raw or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        expected_context = self._parse_ubs_account_context(account_type)
        if expected_context is None:
            if "/" in str(account_type):
                broker_raw, account_raw = str(account_type).split("/", 1)
                expected_context = (
                    normalize_broker(broker_raw),
                    normalize_account_type(account_raw, normalize_broker(broker_raw)),
                )
            else:
                expected_broker = self._ubs_broker()
                expected_context = (expected_broker, normalize_account_type(account_type, expected_broker))
        expected_broker, expected_account = expected_context
        expected_label = self._ubs_account_context_label(expected_broker, expected_account)

        out: list[str] = []

        def line(text: str = "") -> None:
            out.append(str(text))

        config = parse_json(run["config_json"] if "config_json" in run.keys() else "")
        args = config.get("args") if isinstance(config.get("args"), dict) else {}
        generation = config.get("generation") if isinstance(config.get("generation"), dict) else {}
        score_cfg = config.get("score") if isinstance(config.get("score"), dict) else {}
        caps = generation.get("target_diversity_caps") if isinstance(generation.get("target_diversity_caps"), dict) else {}

        line(f"AUDITORIA RUN #{run_id} - {expected_label}")
        line("=" * 96)
        line(f"created_at: {run['created_at']}")
        line(f"run_dir: {run['output_dir']}")
        line(
            f"gens={run['generations']} variants/set={run['variants_per_seed']} "
            f"max_seeds={run['max_seeds']} execute_backtests={run['execute_backtests']} "
            f"dry_run={run['dry_run']} hidden={run['hidden']}"
        )
        line(f"fechas base: {args.get('from_date')} -> {args.get('to_date')}")
        line(
            f"force_unseeded={generation.get('force_unseeded_universe')} "
            f"long_tf={generation.get('experimental_long_timeframes')} "
            f"TFs={','.join(generation.get('timeframe_universe') or [])}"
        )
        line(
            f"score base: net>{score_cfg.get('min_net_profit')} pf>={score_cfg.get('min_profit_factor')} "
            f"trades>={score_cfg.get('min_trades')} DD<={score_cfg.get('max_drawdown_pct')} "
            f"RF>={score_cfg.get('min_recovery_factor')}"
        )
        line(
            f"caps: group={caps.get('group_ratios')} symbol={caps.get('symbol_ratio')} "
            f"tf={caps.get('timeframe_ratio')} pair={caps.get('symbol_timeframe_ratio')}"
        )

        base = counts("select status,count(*) from candidates where run_id=? group by status")
        theoretical = int(run["generations"]) * int(run["variants_per_seed"]) * int(run["max_seeds"])
        line("\nBASE / GENERACION")
        line("-" * 96)
        line(f"candidatos DB={sum(base.values())} teorico={theoretical} | {fmt_counts(base)}")
        for row in rows("select generation,status,count(*) n from candidates where run_id=? group by generation,status order by generation,status"):
            line(f"  gen {row['generation']}: {row['status']}={row['n']}")
        for status in ("accepted", "rejected", "no_trades"):
            values = [row["score"] for row in rows("select score from candidates where run_id=? and status=?", (run_id, status))]
            if values:
                line(f"  score {status}: {stat(values)}")

        missing_set_path = one("select count(*) from candidates where run_id=? and (set_path is null or set_path='')")
        missing_set_files: list[int] = []
        missing_report_path: list[int] = []
        missing_report_files: list[int] = []
        invalid_metrics: list[int] = []
        artifact_detail = ["GEN\tID\tSTATUS\tPROBLEMA\tSET_PATH\tREPORT_PATH\tSET\tREPORTE"]
        for row in rows("select id,generation,status,set_path,report_path,metrics_json from candidates where run_id=?"):
            set_path = str(row["set_path"] or "")
            report_path = str(row["report_path"] or "")
            problems: list[str] = []
            if not set_path:
                problems.append("set_path vacio")
            if set_path and not Path(set_path).exists():
                missing_set_files.append(int(row["id"]))
                problems.append("set file no existe")
            if str(row["status"]) in {"accepted", "rejected", "no_trades", "report_mismatch", "parse_error"}:
                if not report_path:
                    missing_report_path.append(int(row["id"]))
                    problems.append("report_path vacio")
                elif not Path(report_path).exists():
                    missing_report_files.append(int(row["id"]))
                    problems.append("report file no existe")
            if row["metrics_json"] and not parse_json(row["metrics_json"]):
                invalid_metrics.append(int(row["id"]))
                problems.append("metrics_json invalido")
            if problems:
                artifact_detail.append(
                    "\t".join(
                        [
                            str(row["generation"] or ""),
                            str(row["id"] or ""),
                            str(row["status"] or ""),
                            ", ".join(problems),
                            set_path,
                            report_path,
                            Path(set_path).name if set_path else "",
                            Path(report_path).name if report_path else "",
                        ]
                    )
                )
        line(
            f"set_path faltantes={missing_set_path} | set files faltantes={len(missing_set_files)} | "
            f"report_path faltantes={len(missing_report_path)} | report files faltantes={len(missing_report_files)} | "
            f"metrics_json invalidos={len(invalid_metrics)}"
        )

        line("\nARTEFACTOS EN DISCO")
        line("-" * 96)
        line(f"run_dir existe: {run_dir.exists()}")
        for gen_no in range(1, int(run["generations"]) + 1):
            gen_dir = run_dir / f"gen_{gen_no:03d}"
            accepted_dir = run_dir / f"accepted_gen_{gen_no:03d}"
            db_gen = one("select count(*) from candidates where run_id=? and generation=?", (run_id, gen_no))
            db_acc = one("select count(*) from candidates where run_id=? and generation=? and status='accepted'", (run_id, gen_no))
            set_files = len(list(gen_dir.rglob("*.set"))) if gen_dir.exists() else 0
            accepted_copies = len(list(accepted_dir.glob("*.set"))) if accepted_dir.exists() else 0
            line(f"gen_{gen_no:03d}: db={db_gen} set_files={set_files} accepted_db={db_acc} accepted_copies={accepted_copies}")
        for name in ("robustness", "final_tick", "final_tick_6m", "retry_mismatch", "retry_full"):
            folder = run_dir / name
            dirs = len([path for path in folder.iterdir() if path.is_dir()]) if folder.exists() else 0
            set_files = len(list(folder.rglob("*.set"))) if folder.exists() else 0
            line(f"{name}: exists={folder.exists()} dirs={dirs} sets={set_files}")

        report_account_rows = rows(
            """
            select c.generation,c.id,c.set_path,
                   c.report_path as base_report,
                   cr.report_path as robust_report,
                   ft.ohlc_report_path as ft_ohlc_report,
                   ft.real_tick_report_path as ft_tick_report,
                   ft6.ohlc_report_path as ft6_ohlc_report,
                   ft6.real_tick_report_path as ft6_tick_report
            from candidates c
            left join candidate_robustness cr on cr.candidate_id=c.id
            left join candidate_final_tick ft on ft.candidate_id=c.id
            left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            where c.run_id=?
            order by c.generation,c.id
            """
        )
        process_by_column = [
            ("Base", "base_report"),
            ("Robustez", "robust_report"),
            ("FT corto OHLC", "ft_ohlc_report"),
            ("FT corto Tick", "ft_tick_report"),
            ("FT 6M OHLC", "ft6_ohlc_report"),
            ("FT 6M Tick", "ft6_tick_report"),
        ]
        account_cache: dict[str, tuple[str, str, str]] = {}
        account_by_gen: dict[int, dict[str, dict[str, int]]] = {}
        account_ids_by_gen: dict[int, dict[str, dict[str, set[int]]]] = {}
        account_bad_types_by_process: dict[str, dict[str, int]] = {}
        account_bad_ids_by_process: dict[str, dict[str, set[int]]] = {}
        account_details_by_process: dict[str, list[str]] = {}
        account_mismatches: list[str] = []
        account_unknowns: list[str] = []
        checked_report_files: set[str] = set()
        for row in report_account_rows:
            gen_no = int(row["generation"] or 0)
            for process, column in process_by_column:
                raw_path = str(row[column] or "").strip()
                if not raw_path:
                    continue
                report_path = Path(raw_path)
                if not report_path.exists():
                    continue
                cache_key = str(report_path.resolve()).casefold()
                checked_report_files.add(cache_key)
                header, detected_broker, detected_account = account_cache.get(cache_key, ("", "", ""))
                if not header and cache_key not in account_cache:
                    header, detected_broker, detected_account = self._detect_ubs_report_account_header(report_path, expected_broker)
                    account_cache[cache_key] = (header, detected_broker, detected_account)
                detected_label = (
                    self._ubs_account_context_label(detected_broker, detected_account)
                    if detected_broker and detected_account
                    else ""
                )
                bucket = account_by_gen.setdefault(gen_no, {}).setdefault(
                    process,
                    {"ok": 0, "mismatch": 0, "unknown": 0},
                )
                id_bucket = account_ids_by_gen.setdefault(gen_no, {}).setdefault(
                    process,
                    {"ok": set(), "mismatch": set(), "unknown": set()},
                )
                candidate_id = int(row["id"] or 0)
                if not detected_account:
                    bucket["unknown"] += 1
                    id_bucket["unknown"].add(candidate_id)
                    detail = (
                        f"{gen_no}\t{row['id']}\t{Path(str(row['set_path'] or '')).name}\t{process}\t"
                        f"{expected_label}\tSIN_ENCABEZADO\t\t{report_path.name}"
                    )
                    account_details_by_process.setdefault(process, []).append(detail)
                    account_unknowns.append(detail)
                elif (detected_broker, detected_account) != expected_context:
                    bucket["mismatch"] += 1
                    id_bucket["mismatch"].add(candidate_id)
                    account_bad_types_by_process.setdefault(process, {})[detected_label] = (
                        account_bad_types_by_process.setdefault(process, {}).get(detected_label, 0) + 1
                    )
                    account_bad_ids_by_process.setdefault(process, {}).setdefault(detected_label, set()).add(candidate_id)
                    detail = (
                        f"{gen_no}\t{row['id']}\t{Path(str(row['set_path'] or '')).name}\t{process}\t"
                        f"{expected_label}\t{detected_label}\t{header}\t{report_path.name}"
                    )
                    account_details_by_process.setdefault(process, []).append(detail)
                    account_mismatches.append(detail)
                else:
                    bucket["ok"] += 1
                    id_bucket["ok"].add(candidate_id)

        line("\nCUENTA REAL EN REPORTES POR GENERACION")
        line("-" * 96)
        line(
            f"reportes inspeccionados={len(checked_report_files)} | "
            f"mismatch cuenta={len(account_mismatches)} | sin encabezado={len(account_unknowns)}"
        )
        for gen_no in sorted(account_by_gen):
            parts: list[str] = []
            for process in [item[0] for item in process_by_column]:
                data = account_by_gen[gen_no].get(process)
                if not data:
                    continue
                parts.append(f"{process}: ok={data['ok']} mismatch={data['mismatch']} unknown={data['unknown']}")
            line(f"  gen {gen_no}: " + " | ".join(parts))
        if account_mismatches:
            line("  mismatches:")
            for item in account_mismatches[:80]:
                line(f"    - {item}")
            if len(account_mismatches) > 80:
                line(f"    ... {len(account_mismatches) - 80} mas")
        if account_unknowns:
            line("  sin encabezado legible:")
            for item in account_unknowns[:40]:
                line(f"    - {item}")
            if len(account_unknowns) > 40:
                line(f"    ... {len(account_unknowns) - 40} mas")

        def account_process_summary(process_names: tuple[str, ...]) -> tuple[str, str, list[str]]:
            ok_report_total = 0
            mismatch_report_total = 0
            unknown_report_total = 0
            ok_ids: set[int] = set()
            mismatch_ids: set[int] = set()
            unknown_ids: set[int] = set()
            bad_accounts: dict[str, int] = {}
            bad_account_ids: dict[str, set[int]] = {}
            detail_lines: list[str] = []
            gen_parts: list[str] = []
            for gen_no in sorted(account_by_gen):
                gen_ok_reports = 0
                gen_mismatch_reports = 0
                gen_unknown_reports = 0
                gen_ok_ids: set[int] = set()
                gen_mismatch_ids: set[int] = set()
                gen_unknown_ids: set[int] = set()
                for process_name in process_names:
                    data = account_by_gen[gen_no].get(process_name)
                    if not data:
                        continue
                    gen_ok_reports += int(data["ok"])
                    gen_mismatch_reports += int(data["mismatch"])
                    gen_unknown_reports += int(data["unknown"])
                    id_data = account_ids_by_gen.get(gen_no, {}).get(process_name, {})
                    gen_ok_ids.update(id_data.get("ok", set()))
                    gen_mismatch_ids.update(id_data.get("mismatch", set()))
                    gen_unknown_ids.update(id_data.get("unknown", set()))
                if gen_ok_reports or gen_mismatch_reports or gen_unknown_reports:
                    gen_parts.append(
                        f"g{gen_no}: ok_rep={gen_ok_reports} "
                        f"m_cand={len(gen_mismatch_ids)}/rep={gen_mismatch_reports} "
                        f"u_cand={len(gen_unknown_ids)}/rep={gen_unknown_reports}"
                    )
                    ok_report_total += gen_ok_reports
                    mismatch_report_total += gen_mismatch_reports
                    unknown_report_total += gen_unknown_reports
                    ok_ids.update(gen_ok_ids)
                    mismatch_ids.update(gen_mismatch_ids)
                    unknown_ids.update(gen_unknown_ids)
            for process_name in process_names:
                for account_name, count in account_bad_types_by_process.get(process_name, {}).items():
                    bad_accounts[account_name] = bad_accounts.get(account_name, 0) + count
                for account_name, ids in account_bad_ids_by_process.get(process_name, {}).items():
                    bad_account_ids.setdefault(account_name, set()).update(ids)
                detail_lines.extend(account_details_by_process.get(process_name, []))
            bad_text = ""
            if bad_accounts:
                bad_text = " | cuenta mal: " + ", ".join(
                    f"{name}={len(bad_account_ids.get(name, set()))} cand/{count} rep"
                    for name, count in sorted(bad_accounts.items())
                )
            detail = (
                f"ok reportes={ok_report_total} | "
                f"mismatch candidatos={len(mismatch_ids)} reportes={mismatch_report_total} | "
                f"sin encabezado candidatos={len(unknown_ids)} reportes={unknown_report_total}"
                + bad_text
                + (f" | {'; '.join(gen_parts)}" if gen_parts else "")
            )
            tag = "rejected" if mismatch_ids else "accepted"
            if detail_lines:
                detail_lines = ["GEN\tID\tSET\tTEST\tESPERADO\tREPORTE\tHEADER\tARCHIVO", *detail_lines]
            return detail, tag, detail_lines

        robust = counts("select cr.status,count(*) from candidate_robustness cr join candidates c on c.id=cr.candidate_id where c.run_id=? group by cr.status")
        nonfinal_robust = audit_nonfinal_count(robust)
        missing_robust = one(
            "select count(*) from candidates c left join candidate_robustness cr on cr.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.candidate_id is null"
        )
        stale_robust = one(
            "select count(*) from candidate_robustness cr join candidates c on c.id=cr.candidate_id "
            "where c.run_id=? and c.status<>'accepted'"
        )
        line("\nROBUSTEZ")
        line("-" * 96)
        line(fmt_counts(robust))
        line(
            f"base accepted sin robustez={missing_robust} | "
            f"estados no finales={nonfinal_robust} | stale={stale_robust}"
        )
        for row in rows(
            "select c.generation,cr.status,count(*) n from candidate_robustness cr "
            "join candidates c on c.id=cr.candidate_id where c.run_id=? "
            "group by c.generation,cr.status order by c.generation,cr.status"
        ):
            line(f"  gen {row['generation']}: {row['status']}={row['n']}")

        ft = counts("select ft.status,count(*) from candidate_final_tick ft join candidates c on c.id=ft.candidate_id where c.run_id=? group by ft.status")
        # pending_ohlc_trades is an intentional hand-off from the short window
        # to 6M. Missing/unresolved 6M rows are audited separately below.
        short_handoff_ft = int(ft.get("pending_ohlc_trades", 0) or 0)
        nonfinal_ft = audit_nonfinal_count(
            ft,
            additional_final_statuses={"pending_ohlc_trades"},
        )
        eligible_ft = one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted'"
        )
        missing_ft = one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "left join candidate_final_tick ft on ft.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft.candidate_id is null"
        )
        stale_ft = one(
            "select count(*) from candidate_final_tick ft join candidates c on c.id=ft.candidate_id "
            "left join candidate_robustness cr on cr.candidate_id=c.id "
            "where c.run_id=? and not (c.status='accepted' and cr.status='accepted')"
        )
        line("\nFINAL TICK CORTO")
        line("-" * 96)
        line(fmt_counts(ft))
        line(
            f"elegibles base+robust={eligible_ft} | sin FT={missing_ft} | "
            f"bloqueantes={nonfinal_ft} | derivados a 6M={short_handoff_ft} | stale={stale_ft}"
        )
        for row in rows(
            "select c.generation,ft.status,count(*) n from candidate_final_tick ft "
            "join candidates c on c.id=ft.candidate_id where c.run_id=? "
            "group by c.generation,ft.status order by c.generation,ft.status"
        ):
            line(f"  gen {row['generation']}: {row['status']}={row['n']}")

        ft6 = counts("select ft6.status,count(*) from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id where c.run_id=? group by ft6.status")
        nonfinal_ft6 = audit_nonfinal_count(ft6)
        eligible_ft6 = one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades') "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted'"
        )
        missing_ft6 = one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades') "
            "left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.candidate_id is null"
        )
        stale_ft6 = one(
            "select count(*) from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id "
            "left join candidate_robustness cr on cr.candidate_id=c.id "
            "left join candidate_final_tick ft on ft.candidate_id=c.id "
            "where c.run_id=? and not (c.status='accepted' and cr.status='accepted' and ft.status in ('accepted','pending_ohlc_trades'))"
        )
        usable = one(
            "select count(*) from candidates c join candidate_robustness cr on cr.candidate_id=c.id "
            "join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades') "
            "join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id "
            "where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.status='accepted'"
        )
        reason_counts: dict[str, int] = {}
        for row in rows(
            "select ft6.similarity_json from candidate_final_tick_6m ft6 join candidates c on c.id=ft6.candidate_id "
            "where c.run_id=? and ft6.status='rejected'"
        ):
            sim = parse_json(row["similarity_json"])
            for reason in sim.get("reasons") or ("sin_reason",):
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
        line("\nFINAL TICK 6M")
        line("-" * 96)
        line(fmt_counts(ft6))
        line(
            f"elegibles 6M={eligible_ft6} | sin fila 6M={missing_ft6} | "
            f"pendientes/problema={nonfinal_ft6} | stale={stale_ft6} | "
            f"usable portfolio/live={usable}"
        )
        line("causas rejected 6M: " + (", ".join(f"{key}={value}" for key, value in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))) or "sin rechazos"))
        for row in rows(
            "select c.generation,ft6.status,count(*) n from candidate_final_tick_6m ft6 "
            "join candidates c on c.id=ft6.candidate_id where c.run_id=? "
            "group by c.generation,ft6.status order by c.generation,ft6.status"
        ):
            line(f"  gen {row['generation']}: {row['status']}={row['n']}")

        feedback_rows = rows(
            """
            select c.id,c.run_id,c.generation,c.set_path,c.seed_path,c.target_symbol,c.symbol,c.period,c.family,c.mutated_keys,
                   c.score,c.accepted,c.metrics_json,c.status,c.report_path,
                   cr.status as robust_status,cr.positive_bonus as robust_positive_bonus,
                   cr.negative_bonus as robust_negative_bonus,cr.metrics_json as robust_metrics_json,
                   ft.status as final_tick_status,ft.similarity_json as final_tick_similarity_json,
                   ft6.status as final_tick_6m_status,ft6.similarity_json as final_tick_6m_similarity_json
            from candidates c
            left join candidate_robustness cr on cr.candidate_id=c.id and c.status='accepted'
            left join candidate_final_tick ft on ft.candidate_id=c.id and c.status='accepted' and cr.status='accepted'
            left join candidate_final_tick_6m ft6
              on ft6.candidate_id=c.id
             and c.status='accepted'
             and cr.status='accepted'
             and ft.status in ('accepted','pending_ohlc_trades')
            where c.run_id=? and c.status in ('accepted','rejected','no_trades')
              and (c.score is not null or c.status='no_trades')
            """
        )
        formula_detail = [
            "CONCEPTO\tVALOR",
            f"Base accepted\tscore + accepted_bonus ({ASSET_ACCEPTED_BONUS:.2f})",
            f"Base rejected\tscore - base_penalty ({REJECTED_BASE_PENALTY:.2f}) - penalizaciones por razones; capado para que no aporte positivo",
            f"Base no_trades\t{NO_TRADES_WEIGHT:.2f} solo si tiene report_path real; si no tiene reporte no aporta peso",
            f"Robustez accepted\t+positive_bonus del row, default {DEFAULT_ROBUST_POSITIVE_BONUS:.2f}",
            f"Robustez rejected\t+negative_bonus del row, default {DEFAULT_ROBUST_NEGATIVE_BONUS:.2f}, menos penalizaciones por razones",
            "Final Tick corto accepted\t0.00; queda esperando Final Tick 6M",
            f"Final Tick corto rejected\t{DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f} menos penalizaciones por razones",
            f"Final Tick 6M accepted\t+{DEFAULT_FINAL_TICK_ACCEPTED_BONUS:.2f}",
            f"Final Tick 6M rejected\t{DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f} menos penalizaciones por razones; gate duro para portafolio",
            "Final Tick pending / sin fila\t0.00; neutral",
        ]

        def fnum(value: object) -> str:
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return ""

        def weight_breakdown(row: sqlite3.Row) -> tuple[float | None, dict[str, float], str]:
            status = str(row["status"] or "").lower()
            parts = {"base": 0.0, "robust": 0.0, "ft": 0.0, "ft6": 0.0}
            reason_parts: list[str] = []
            if status == "no_trades":
                if not str(row["report_path"] or "").strip():
                    return None, parts, "no_trades sin report_path no aporta"
                parts["base"] = NO_TRADES_WEIGHT
                return parts["base"], parts, "no_trades con reporte"
            if status not in {"accepted", "rejected"} or row["score"] in (None, ""):
                return None, parts, "status/score no ponderable"

            score = float(row["score"] or 0.0)
            if status == "accepted":
                parts["base"] = score + ASSET_ACCEPTED_BONUS
                reason_parts.append(f"base accepted score {score:.2f}+{ASSET_ACCEPTED_BONUS:.2f}")
            else:
                base_reasons = metric_reasons(row["metrics_json"])
                penalty = reason_penalty(base_reasons, REJECTED_REASON_PENALTIES)
                value = score - REJECTED_BASE_PENALTY - penalty
                ceiling = -penalty if base_reasons else -REJECTED_BASE_PENALTY
                parts["base"] = min(value, ceiling)
                reason_parts.append(
                    f"base rejected score {score:.2f}-{REJECTED_BASE_PENALTY:.2f}-{penalty:.2f} cap {ceiling:.2f}"
                )
                if base_reasons:
                    reason_parts.append("base razones=" + ",".join(base_reasons))

            robust_status = str(row["robust_status"] or "").lower()
            if robust_status == "accepted":
                parts["robust"] = robust_bonus(row)
                reason_parts.append(f"robust accepted {parts['robust']:.2f}")
            elif robust_status == "rejected":
                robust_reasons = metric_reasons(row["robust_metrics_json"])
                penalty = reason_penalty(robust_reasons, ROBUST_REASON_PENALTIES)
                parts["robust"] = robust_bonus(row) - penalty
                reason_parts.append(f"robust rejected {robust_bonus(row):.2f}-{penalty:.2f}")
                if robust_reasons:
                    reason_parts.append("robust razones=" + ",".join(robust_reasons))

            rejected_ceilings: list[float] = []
            ft_status = str(row["final_tick_status"] or "").lower()
            if ft_status == "rejected":
                ft_reasons = metric_reasons(row["final_tick_similarity_json"])
                penalty = reason_penalty(ft_reasons, FINAL_TICK_REASON_PENALTIES)
                parts["ft"] = DEFAULT_FINAL_TICK_REJECTED_PENALTY - penalty
                rejected_ceilings.append(parts["ft"])
                reason_parts.append(f"ft corto rejected {DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f}-{penalty:.2f}")
                if ft_reasons:
                    reason_parts.append("ft razones=" + ",".join(ft_reasons))
            elif ft_status == "accepted":
                reason_parts.append("ft corto accepted 0.00")

            ft6_status = str(row["final_tick_6m_status"] or "").lower()
            if ft6_status == "accepted":
                parts["ft6"] = DEFAULT_FINAL_TICK_ACCEPTED_BONUS
                reason_parts.append(f"ft6 accepted +{DEFAULT_FINAL_TICK_ACCEPTED_BONUS:.2f}")
            elif ft6_status == "rejected":
                ft6_reasons = metric_reasons(row["final_tick_6m_similarity_json"])
                penalty = reason_penalty(ft6_reasons, FINAL_TICK_REASON_PENALTIES)
                parts["ft6"] = DEFAULT_FINAL_TICK_REJECTED_PENALTY - penalty
                rejected_ceilings.append(parts["ft6"])
                reason_parts.append(f"ft6 rejected {DEFAULT_FINAL_TICK_REJECTED_PENALTY:.2f}-{penalty:.2f}")
                if ft6_reasons:
                    reason_parts.append("ft6 razones=" + ",".join(ft6_reasons))

            total = parts["base"] + parts["robust"] + parts["ft"] + parts["ft6"]
            if rejected_ceilings:
                total = min(total, min(rejected_ceilings))
                reason_parts.append(f"cap final tick {min(rejected_ceilings):.2f}")
            return total, parts, " | ".join(reason_parts)

        weights: list[float] = []
        weights_by_ft6: dict[str, list[float]] = {}
        weights_by_asset: dict[str, list[float]] = {}
        weights_by_tf: dict[str, list[float]] = {}
        weight_detail = [
            "GEN\tID\tSTATUS\tROBUST\tFT CORTO\tFT 6M\tSIMBOLO\tTF\tSCORE\tBASE\tROBUST ADJ\tFT ADJ\tFT6 ADJ\tPESO FORMULA\tPESO FUNC\tCHECK\tRAZONES\tSET"
        ]
        weight_mismatches = 0
        for row in feedback_rows:
            value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
            formula_value, parts, reason_text = weight_breakdown(row)
            check = "OK"
            if value is None and formula_value is not None:
                check = "REVISAR"
                weight_mismatches += 1
            elif value is not None and formula_value is None:
                check = "REVISAR"
                weight_mismatches += 1
            elif value is not None and formula_value is not None and abs(float(value) - float(formula_value)) > 0.01:
                check = "REVISAR"
                weight_mismatches += 1
            weight_detail.append(
                "\t".join(
                    [
                        str(row["generation"] or ""),
                        str(row["id"] or ""),
                        str(row["status"] or ""),
                        str(row["robust_status"] or ""),
                        str(row["final_tick_status"] or ""),
                        str(row["final_tick_6m_status"] or ""),
                        str(row["target_symbol"] or row["symbol"] or ""),
                        str(row["period"] or ""),
                        fnum(row["score"]),
                        fnum(parts["base"]),
                        fnum(parts["robust"]),
                        fnum(parts["ft"]),
                        fnum(parts["ft6"]),
                        fnum(formula_value),
                        fnum(value),
                        check,
                        reason_text,
                        Path(str(row["set_path"] or "")).name,
                    ]
                )
            )
            if value is None:
                continue
            weights.append(value)
            weights_by_ft6.setdefault(str(row["final_tick_6m_status"] or "sin_6m"), []).append(value)
            weights_by_asset.setdefault(str(row["target_symbol"] or row["symbol"]).upper(), []).append(value)
            weights_by_tf.setdefault(str(row["period"]).upper(), []).append(value)
        line("\nUTILIDAD LEGACY POR FILA (DIAGNOSTICO; NO USADA PARA SELECCION)")
        line("-" * 96)
        for item in formula_detail[1:]:
            line(item.replace("\t", ": "))
        line(f"utilidad legacy run: {stat(weights)}")
        line(f"detalle pesos verificable: filas={max(len(weight_detail) - 1, 0)} mismatch_formula_vs_funcion={weight_mismatches}")
        for status, values in sorted(weights_by_ft6.items()):
            line(f"  ft6 {status}: {stat(values)}")
        line("asset top run:")
        for key, values in sorted(weights_by_asset.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True)[:12]:
            line(f"  {key}: avg={sum(values)/len(values):.2f} n={len(values)}")
        line("asset bottom run:")
        for key, values in sorted(weights_by_asset.items(), key=lambda item: sum(item[1]) / len(item[1]))[:12]:
            line(f"  {key}: avg={sum(values)/len(values):.2f} n={len(values)}")
        line("TF run:")
        for key, values in sorted(weights_by_tf.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True):
            line(f"  {key}: avg={sum(values)/len(values):.2f} n={len(values)}")

        accepted_6m_detail = rows(
            """
            select c.id,c.set_path,c.target_symbol,c.period,c.score base_score,cr.score robust_score,
                   ft6.ohlc_score,ft6.real_tick_score,ft6.similarity_json,
                   c.run_id,c.seed_path,c.symbol,c.family,c.mutated_keys,c.accepted,c.metrics_json,c.status,c.report_path,
                   cr.status robust_status,cr.positive_bonus robust_positive_bonus,
                   cr.negative_bonus robust_negative_bonus,cr.metrics_json robust_metrics_json,
                   ft.status final_tick_status,ft.similarity_json final_tick_similarity_json,
                   ft6.status final_tick_6m_status,ft6.similarity_json final_tick_6m_similarity_json
            from candidates c
            join candidate_robustness cr on cr.candidate_id=c.id
            join candidate_final_tick ft on ft.candidate_id=c.id and ft.status in ('accepted','pending_ohlc_trades')
            join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted' and ft6.status='accepted'
            order by c.id
            """
        )
        line("\n6M ACCEPTED DETALLE")
        line("-" * 96)
        if not accepted_6m_detail:
            line("sin accepted 6M")
        for row in accepted_6m_detail:
            value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
            sim = parse_json(row["similarity_json"])
            checks = sim.get("checks") if isinstance(sim.get("checks"), dict) else {}
            pf = checks.get("profit_factor", {}) if isinstance(checks, dict) else {}
            floor = checks.get("profit_factor_floor", {}) if isinstance(checks, dict) else {}

            def num(value_obj: object) -> str:
                try:
                    return f"{float(value_obj):.2f}"
                except (TypeError, ValueError):
                    return ""

            line(
                f"  id={row['id']} {row['target_symbol']} {row['period']} "
                f"base={num(row['base_score'])} robust={num(row['robust_score'])} "
                f"ohlc={num(row['ohlc_score'])} tick={num(row['real_tick_score'])} "
                f"weight={num(value)} pf_delta={pf.get('delta_pct')} floor_ok={floor.get('accepted')} "
                f"set={Path(str(row['set_path'] or '')).name}"
            )

        issues: list[str] = []
        if sum(base.values()) != theoretical:
            issues.append(f"candidatos DB {sum(base.values())} != teorico {theoretical}")
        if missing_set_path or missing_set_files:
            issues.append(f"sets faltantes path={missing_set_path} files={len(missing_set_files)}")
        if missing_report_path or missing_report_files:
            issues.append(f"reportes base faltantes path={len(missing_report_path)} files={len(missing_report_files)}")
        if account_mismatches:
            issues.append(f"mismatch cuenta MT5 en reportes={len(account_mismatches)}")
        if stale_robust or stale_ft or stale_ft6:
            issues.append(f"stale rows robust={stale_robust} ft={stale_ft} ft6={stale_ft6}")
        if missing_robust or missing_ft or missing_ft6:
            issues.append(f"pendientes missing robust={missing_robust} ft={missing_ft} ft6={missing_ft6}")
        if nonfinal_robust or nonfinal_ft or nonfinal_ft6:
            issues.append(
                "estados no finales "
                f"robust={nonfinal_robust} ft={nonfinal_ft} ft6={nonfinal_ft6}"
            )
        if weight_mismatches:
            issues.append(f"mismatch diagnostico legacy vs feedback_weight={weight_mismatches}")
        if not issues:
            issues.append(f"sin inconsistencias estructurales detectadas en run {run_id}")
        line("\nHALLAZGOS")
        line("-" * 96)
        for issue in issues:
            line(f"- {issue}")

        issue_tag = "accepted" if issues and issues[0].startswith("sin inconsistencias") else "rejected"
        base_account_detail, base_account_tag, base_account_lines = account_process_summary(("Base",))
        robust_account_detail, robust_account_tag, robust_account_lines = account_process_summary(("Robustez",))
        ft_account_detail, ft_account_tag, ft_account_lines = account_process_summary(("FT corto OHLC", "FT corto Tick"))
        ft6_account_detail, ft6_account_tag, ft6_account_lines = account_process_summary(("FT 6M OHLC", "FT 6M Tick"))
        summary = [
            ("Run", f"#{run_id} {account_type} | {run['created_at']}", "pending"),
            ("Base", f"{fmt_counts(base)} | teorico={theoretical}", "accepted" if sum(base.values()) == theoretical else "rejected"),
            (
                "Sets/reportes",
                f"set files faltantes={len(missing_set_files)} | reportes base faltantes={len(missing_report_path)+len(missing_report_files)}",
                "accepted" if len(artifact_detail) == 1 else "rejected",
                artifact_detail if len(artifact_detail) > 1 else [],
            ),
            ("Cuenta MT5 base", base_account_detail, base_account_tag, base_account_lines),
            (
                "Robustez",
                f"{fmt_counts(robust)} | sin robust={missing_robust} | no finales={nonfinal_robust} | stale={stale_robust}",
                "accepted" if not (missing_robust or nonfinal_robust or stale_robust) else "rejected",
            ),
            ("Cuenta MT5 robustez", robust_account_detail, robust_account_tag, robust_account_lines),
            (
                "Final Tick corto",
                f"{fmt_counts(ft)} | sin FT={missing_ft} | bloqueantes={nonfinal_ft} | derivados 6M={short_handoff_ft} | stale={stale_ft}",
                "accepted" if not (missing_ft or nonfinal_ft or stale_ft) else "rejected",
            ),
            ("Cuenta MT5 FT corto", ft_account_detail, ft_account_tag, ft_account_lines),
            (
                "Final Tick 6M",
                f"{fmt_counts(ft6)} | usable={usable} | sin fila={missing_ft6} | pendientes/problema={nonfinal_ft6} | stale={stale_ft6}",
                "accepted" if usable > 0 and not (missing_ft6 or nonfinal_ft6 or stale_ft6) else "rejected",
            ),
            ("Cuenta MT5 FT 6M", ft6_account_detail, ft6_account_tag, ft6_account_lines),
            (
                "Formula pesos",
                "base + robustez + final corto + final 6M; corto accepted=0, 6M accepted=+120, rejected penaliza",
                "pending",
                formula_detail,
            ),
            ("Peso run", stat(weights), "pending"),
            (
                "Detalle pesos",
                f"filas={max(len(weight_detail)-1, 0)} | check formula_vs_funcion mismatch={weight_mismatches}",
                "accepted" if weight_mismatches == 0 else "rejected",
                weight_detail,
            ),
            ("Hallazgo", "; ".join(issues), issue_tag),
        ]
        return out, summary

    def _run_ubs_search(self) -> None:
        if not hasattr(self, "ubs_search_tree"):
            return
        query = self.ubs_search_query.get().strip()
        if not query:
            self.ubs_search_status.set("Escribe parte del nombre del set.")
            return

        for item in self.ubs_search_tree.get_children():
            self.ubs_search_tree.delete(item)
        self.ubs_search_paths = {}

        rows: list[dict[str, object]] = []
        errors: list[str] = []
        for broker, account_type in self._ubs_active_broker_account_contexts():
            memory_path = account_memory_path(BASE_DIR, account_type, broker)
            account_label = self._ubs_account_context_label(broker, account_type)
            if not memory_path.exists():
                continue
            try:
                rows.extend(self._ubs_search_rows(memory_path, account_label, query))
            except sqlite3.Error as exc:
                errors.append(f"{account_label}: {exc}")

        rows.sort(
            key=lambda row: (
                str(row.get("set_name") or "").casefold(),
                str(row.get("account_type") or ""),
                int(row.get("candidate_id") or 0),
            )
        )

        for index, row in enumerate(rows):
            tag = self._ubs_search_row_tag(row)
            item = self.ubs_search_tree.insert(
                "",
                "end",
                iid=f"{row['account_type']}:{row['candidate_id']}:{index}",
                values=(
                    row.get("account_type", ""),
                    row.get("candidate_id", ""),
                    row.get("status", ""),
                    row.get("robust_status", ""),
                    row.get("final_tick_status", ""),
                    row.get("target_symbol", "") or row.get("symbol", ""),
                    row.get("period", ""),
                    self._ubs_search_score(row.get("score")),
                    row.get("set_name", ""),
                    row.get("run_id", ""),
                ),
                tags=(tag,),
            )
            self.ubs_search_paths[item] = {
                "set": str(row.get("set_path") or ""),
                "base_report": str(row.get("report_path") or ""),
                "robust_report": str(row.get("robust_report_path") or ""),
                "ohlc_report": str(row.get("ohlc_report_path") or ""),
                "tick_report": str(row.get("real_tick_report_path") or ""),
            }

        message = f"{len(rows)} resultado(s) para: {query}"
        if errors:
            message += " | Errores: " + " ; ".join(errors)
        self.ubs_search_status.set(message)

    def _ubs_search_rows(self, memory_path: Path, account_type: str, query: str) -> list[dict[str, object]]:
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            pattern = f"%{query.casefold()}%"
            rows = conn.execute(
                """
                select ? as account_type,
                       c.id as candidate_id,
                       c.run_id,
                       c.status,
                       c.score,
                       c.set_path,
                       c.report_path,
                       c.symbol,
                       c.target_symbol,
                       c.period,
                       cr.status as robust_status,
                       cr.report_path as robust_report_path,
                       ft.status as final_tick_status,
                       ft.ohlc_report_path,
                       ft.real_tick_report_path
                from candidates c
                left join candidate_robustness cr on cr.candidate_id = c.id
                left join candidate_final_tick ft on ft.candidate_id = c.id
                where lower(c.set_path) like ?
                   or lower(coalesce(c.seed_path, '')) like ?
                   or lower(coalesce(c.report_path, '')) like ?
                order by c.id desc
                limit 1000
                """,
                (account_type, pattern, pattern, pattern),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["set_name"] = Path(str(item.get("set_path") or "")).name
            out.append(item)
        return out

    def _ubs_search_score(self, value: object) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return ""

    def _ubs_search_row_tag(self, row: dict[str, object]) -> str:
        final_status = str(row.get("final_tick_status") or "").strip().lower()
        robust_status = str(row.get("robust_status") or "").strip().lower()
        base_status = str(row.get("status") or "").strip().lower()
        if final_status == "accepted" or (not final_status and robust_status == "accepted") or (
            not final_status and not robust_status and base_status == "accepted"
        ):
            return "accepted"
        if final_status == "rejected" or robust_status == "rejected" or base_status == "rejected":
            return "rejected"
        return "pending"

    def _selected_ubs_search_items(self) -> list[str]:
        if not hasattr(self, "ubs_search_tree"):
            return []
        return list(self.ubs_search_tree.selection())

    def _selected_ubs_search_paths(self) -> dict[str, str] | None:
        selection = self._selected_ubs_search_items()
        if not selection:
            messagebox.showinfo("Buscador UBS", "Selecciona un set primero.")
            return None
        return getattr(self, "ubs_search_paths", {}).get(selection[0], {})

    def _open_selected_ubs_search_set(self) -> None:
        paths = self._selected_ubs_search_paths()
        if not paths:
            return
        set_path = paths.get("set")
        if not set_path:
            messagebox.showinfo("Buscador UBS", "La fila seleccionada no tiene set asociado.")
            return
        self._open_local_file(Path(set_path))

    def _open_selected_ubs_search_report(self) -> None:
        paths = self._selected_ubs_search_paths()
        if not paths:
            return
        for key in ("tick_report", "ohlc_report", "robust_report", "base_report"):
            value = paths.get(key)
            if value and Path(value).exists():
                self._open_local_file(Path(value))
                return
        messagebox.showinfo("Buscador UBS", "La fila seleccionada no tiene reporte disponible.")

    def _export_selected_ubs_search_set(self) -> None:
        items = self._selected_ubs_search_items()
        if not items:
            messagebox.showinfo("Exportar set", "Selecciona uno o mas sets primero.")
            return
        folder = filedialog.askdirectory(title="Carpeta destino para exportar sets")
        if not folder:
            return
        dest = Path(folder)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Exportar set", f"No pude crear la carpeta:\n{exc}")
            return

        exported = 0
        missing: list[str] = []
        paths_by_item = getattr(self, "ubs_search_paths", {})
        for item in items:
            set_path = Path(str(paths_by_item.get(item, {}).get("set") or ""))
            if not set_path.is_file():
                missing.append(set_path.name or item)
                continue
            out_path = dest / set_path.name
            try:
                if set_path.resolve() != out_path.resolve():
                    shutil.copy2(set_path, out_path)
                exported += 1
            except OSError:
                missing.append(set_path.name)

        status = f"Exportados {exported} set(s) a {dest}"
        if missing:
            status += f" | omitidos: {len(missing)}"
        self.ubs_search_status.set(status)
        message = status
        if missing:
            message += "\n\nOmitidos:\n" + "\n".join(missing[:20])
        messagebox.showinfo("Exportar set", message)
        if exported:
            self._open_local_file(dest)

    def _clear_ubs_search(self) -> None:
        if hasattr(self, "ubs_search_tree"):
            for item in self.ubs_search_tree.get_children():
                self.ubs_search_tree.delete(item)
        self.ubs_search_paths = {}
        self.ubs_search_query.set("")
        self.ubs_search_status.set("Sin busqueda.")
