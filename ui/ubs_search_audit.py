from __future__ import annotations

from html import unescape
from pathlib import Path
import re
import sqlite3
import sys
from tkinter import messagebox

from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    account_memory_path,
    account_types_for_broker,
    normalize_account_type,
    normalize_broker,
)
from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


from ui.ubs_search_audit_base import (  # noqa: F401  fachada del modulo
    AUDIT_FINAL_STATUSES,
    audit_nonfinal_count,
)


class UBSSearchAuditMixin:
    """Contexto de cuenta, combos y lanzamiento de la auditoria de run."""

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

    def _set_ubs_audit_run_labels(self, combo, rows):
        """Etiquetas del combo de runs y seleccion por defecto."""
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

        self._set_ubs_audit_run_labels(combo, rows)

    def _parse_ubs_audit_run_id(self, value: object) -> int:
        match = re.search(r"#?(\d+)", str(value or "").strip())
        return int(match.group(1)) if match else 0

    def _detect_ubs_account_from_header(self, cleaned, text, detected_broker, token_map):
        """Encabezado y cuenta detectados para el broker ya identificado."""
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

        return self._detect_ubs_account_from_header(
            cleaned, text, detected_broker, token_map,
        )

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

    def _populate_ubs_audit_trees(self, audit_trees, rows) -> bool:
        """Reparte las filas entre los arboles por etapa; True si los hay."""
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

    def _populate_ubs_audit_summary(self, rows: list[tuple]) -> None:
        self.ubs_audit_details = {}
        audit_trees = getattr(self, "ubs_audit_trees", None)
        if self._populate_ubs_audit_trees(audit_trees, rows):
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
