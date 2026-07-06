from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from ai_copilot.audit import write_audit_bundle
from ai_copilot.features import build_local_report, top_manual_keys
from ai_copilot.manual import default_manual_cache_path, default_manual_pdf_path, load_or_build_manual_index, select_manual_context
from ai_copilot.redaction import build_api_payload
from ai_copilot.schema import evidence_ids
from ai_copilot.snapshot import load_run_snapshot
from ubs.account import account_memory_path, account_types_for_broker
from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSCopilotLogicMixin:
    def _refresh_ubs_copilot_account_values(self) -> None:
        combo = getattr(self, "ubs_copilot_account_combo", None)
        values = tuple(
            self._ubs_account_context_label(self._ubs_broker(), account)
            for account in account_types_for_broker(self._ubs_broker())
        )
        if combo is not None:
            combo.configure(values=values)
        current = self.ubs_copilot_account.get().strip()
        if current not in values:
            self.ubs_copilot_account.set(values[0] if values else "")

    def _refresh_ubs_copilot_run_combo(self) -> None:
        combo = getattr(self, "ubs_copilot_run_combo", None)
        if combo is None:
            return
        self._refresh_ubs_copilot_account_values()
        context = self._parse_ubs_account_context(self.ubs_copilot_account.get())
        if context is None:
            combo.configure(values=())
            self.ubs_copilot_run_id.set("")
            self.ubs_copilot_status.set("Cuenta invalida.")
            return
        broker, account_type = context
        memory_path = account_memory_path(BASE_DIR, account_type, broker)
        if not memory_path.exists():
            combo.configure(values=())
            self.ubs_copilot_run_id.set("")
            self.ubs_copilot_status.set(f"Sin memoria UBS: {broker}/{account_type}")
            return
        try:
            conn = connect_memory(memory_path, enable_wal=False)
            try:
                rows = conn.execute(
                    """
                    select r.id,r.created_at,r.hidden,count(c.id) total,
                           sum(case when c.status='accepted' then 1 else 0 end) accepted,
                           sum(case when c.status='rejected' then 1 else 0 end) rejected,
                           sum(case when c.status='no_trades' then 1 else 0 end) no_trades
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
            self.ubs_copilot_status.set(f"Error leyendo runs: {exc}")
            return
        labels = []
        for row in rows:
            hidden = " [arch]" if row["hidden"] else ""
            labels.append(
                f"#{row['id']} | {str(row['created_at'] or '')[:16]} | cand {int(row['total'] or 0)} "
                f"| OK {int(row['accepted'] or 0)} FAIL {int(row['rejected'] or 0)} 0ops {int(row['no_trades'] or 0)}{hidden}"
            )
        combo.configure(values=labels)
        current_id = self._parse_ubs_copilot_run_id(self.ubs_copilot_run_id.get())
        selected = next((label for label in labels if label.startswith(f"#{current_id} ")), "") if current_id else ""
        if not selected and labels:
            selected = labels[0]
        self.ubs_copilot_run_id.set(selected)
        self.ubs_copilot_status.set("Selecciona run y pulsa Diagnosticar.")

    def _parse_ubs_copilot_run_id(self, value: object) -> int:
        match = re.search(r"#?(\d+)", str(value or "").strip())
        return int(match.group(1)) if match else 0

    def _run_ubs_copilot(self) -> None:
        if getattr(self, "ubs_copilot_running", False):
            messagebox.showwarning("Copiloto IA UBS", "Ya hay un diagnostico en ejecucion.")
            return
        context = self._parse_ubs_account_context(self.ubs_copilot_account.get())
        if context is None:
            self.ubs_copilot_status.set("Cuenta invalida.")
            return
        run_id = self._parse_ubs_copilot_run_id(self.ubs_copilot_run_id.get())
        if run_id <= 0:
            self.ubs_copilot_status.set("Run invalido.")
            return
        provider = self.ubs_copilot_provider.get().strip().lower() or "local"
        if provider not in {"local", "openai"}:
            self.ubs_copilot_status.set("Proveedor invalido.")
            return
        self.ubs_copilot_running = True
        self.ubs_copilot_status.set("Diagnosticando run UBS...")
        self._clear_ubs_copilot_results()
        broker, account_type = context
        threading.Thread(
            target=self._ubs_copilot_worker,
            args=(broker, account_type, run_id, provider),
            daemon=True,
        ).start()

    def _ubs_copilot_worker(self, broker: str, account_type: str, run_id: int, provider: str) -> None:
        try:
            snapshot = load_run_snapshot(BASE_DIR, broker, account_type, run_id)
            manual_context = self._load_ubs_copilot_manual_context(snapshot)
            local_report = build_local_report(snapshot, manual_context=manual_context)
            request_payload = None
            provider_response = None
            report = local_report
            if provider == "openai":
                from ai_copilot.providers.openai_provider import OpenAIProviderError, call_openai

                request_payload = build_api_payload(snapshot, local_report, manual_context)
                try:
                    report, provider_response = call_openai(
                        request_payload,
                        model=self.ubs_copilot_model.get().strip() or "gpt-5.4-mini",
                        reasoning_effort="low",
                        allowed_evidence_ids=evidence_ids(local_report),
                    )
                except OpenAIProviderError as exc:
                    if exc.status_code == 429 or exc.error_code == "insufficient_quota":
                        report = local_report
                        report["summary"] = f"{report['summary']} | OpenAI sin cuota: mostrando diagnostico local."
                        provider_response = {
                            "error": str(exc),
                            "status_code": exc.status_code,
                            "error_code": exc.error_code,
                            "fallback": "local",
                        }
                    else:
                        raise
            out_dir = BASE_DIR / "outputs" / "ai_copilot" / broker / account_type
            paths = write_audit_bundle(
                out_dir,
                report=report,
                request_payload=request_payload,
                provider_response=provider_response,
            )
            self.after(0, lambda: self._ubs_copilot_finished(report, paths, None))
        except Exception as exc:
            self.after(0, lambda error=exc: self._ubs_copilot_finished(None, {}, error))

    def _load_ubs_copilot_manual_context(self, snapshot: dict) -> list[dict]:
        if not self.ubs_copilot_include_manual.get():
            return []
        manual_path = default_manual_pdf_path()
        if not manual_path:
            return []
        try:
            max_keys = max(1, int(str(self.ubs_copilot_max_manual_keys.get() or "20")))
        except ValueError:
            max_keys = 20
        try:
            index = load_or_build_manual_index(manual_path, default_manual_cache_path(BASE_DIR))
        except RuntimeError:
            return []
        keys = top_manual_keys(snapshot, limit=max_keys)
        return select_manual_context(index, keys, max_keys=max_keys)

    def _ubs_copilot_finished(self, report: dict | None, paths: dict, error: Exception | None) -> None:
        self.ubs_copilot_running = False
        if error is not None:
            self.ubs_copilot_status.set(f"Error Copiloto IA: {error}")
            self._show_error("Copiloto IA UBS", str(error))
            return
        if report is None:
            self.ubs_copilot_status.set("Copiloto IA sin resultado.")
            return
        report_path = paths.get("report")
        markdown_path = paths.get("markdown")
        self.ubs_copilot_report_path.set(str(report_path or ""))
        self.ubs_copilot_summary.set(str(report.get("summary") or ""))
        self._populate_ubs_copilot_report(report)
        suffix = f" | informe: {Path(report_path).name}" if report_path else ""
        self.ubs_copilot_status.set(f"{report.get('summary')}{suffix}")

    def _populate_ubs_copilot_report(self, report: dict) -> None:
        if not hasattr(self, "ubs_copilot_tree"):
            return
        self.ubs_copilot_details = {}
        for item in self.ubs_copilot_tree.get_children():
            self.ubs_copilot_tree.delete(item)
        for finding in report.get("findings", []):
            severity = str(finding.get("severity") or "info")
            tag = "rejected" if severity == "critical" else "pending" if severity == "warning" else "accepted"
            item = self.ubs_copilot_tree.insert(
                "",
                "end",
                values=(
                    "Hallazgo",
                    severity,
                    "",
                    finding.get("claim") or "",
                    finding.get("affected_count") or 0,
                    "",
                    ", ".join(finding.get("evidence_ids") or []),
                ),
                tags=(tag,),
            )
            self.ubs_copilot_details[item] = json.dumps(finding, indent=2, ensure_ascii=False)
        for recommendation in report.get("recommendations", []):
            risk = str(recommendation.get("risk") or "low")
            tag = "rejected" if risk == "high" else "pending" if risk == "medium" else "accepted"
            item = self.ubs_copilot_tree.insert(
                "",
                "end",
                values=(
                    "Recomendacion",
                    "",
                    recommendation.get("action_type") or "",
                    recommendation.get("title") or "",
                    "",
                    risk,
                    ", ".join(recommendation.get("evidence_ids") or []),
                ),
                tags=(tag,),
            )
            self.ubs_copilot_details[item] = self._format_ubs_copilot_recommendation(recommendation)
        self._set_ubs_copilot_detail(report.get("summary") or "")

    def _format_ubs_copilot_recommendation(self, recommendation: dict) -> str:
        lines = [
            f"Titulo: {recommendation.get('title') or ''}",
            f"Accion: {recommendation.get('action_type') or ''}",
            f"Riesgo: {recommendation.get('risk') or ''}",
            f"Requiere aprobacion: {recommendation.get('requires_approval')}",
            "",
            f"Rationale: {recommendation.get('rationale') or ''}",
            f"Efecto esperado: {recommendation.get('expected_effect') or ''}",
            "",
            f"Evidencia: {', '.join(recommendation.get('evidence_ids') or [])}",
        ]
        cli_preview = str(recommendation.get("cli_preview") or "").strip()
        if cli_preview:
            lines.extend(["", f"CLI preview: {cli_preview}"])
        return "\n".join(lines)

    def _on_ubs_copilot_select(self) -> None:
        selection = self.ubs_copilot_tree.selection() if hasattr(self, "ubs_copilot_tree") else ()
        if not selection:
            return
        detail = getattr(self, "ubs_copilot_details", {}).get(selection[0], "")
        self._set_ubs_copilot_detail(detail)

    def _set_ubs_copilot_detail(self, text: str) -> None:
        widget = getattr(self, "ubs_copilot_detail_text", None)
        if widget is None:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _show_ubs_copilot_detail_window(self) -> None:
        selection = self.ubs_copilot_tree.selection() if hasattr(self, "ubs_copilot_tree") else ()
        if not selection:
            return
        detail = getattr(self, "ubs_copilot_details", {}).get(selection[0], "")
        if not detail:
            return
        window = tk.Toplevel(getattr(self, "root", self))
        window.title("Detalle Copiloto IA UBS")
        window.geometry("980x520")
        window.configure(bg=self.colors["panel"])
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        text = tk.Text(
            window,
            wrap="word",
            bg=self.colors["tree_bg"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            font=("Segoe UI", 10),
        )
        text.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        text.insert("1.0", detail)
        text.configure(state="disabled")

    def _open_ubs_copilot_report(self) -> None:
        path = Path(str(self.ubs_copilot_report_path.get() or ""))
        if not path.exists():
            messagebox.showinfo("Copiloto IA UBS", "No hay informe generado para abrir.")
            return
        self._open_local_file(path)

    def _open_ubs_copilot_folder(self) -> None:
        path = Path(str(self.ubs_copilot_report_path.get() or ""))
        folder = path.parent if path.exists() else BASE_DIR / "outputs" / "ai_copilot"
        if not folder.exists():
            messagebox.showinfo("Copiloto IA UBS", "No existe la carpeta de informes.")
            return
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except OSError:
            self._open_local_file(folder)

    def _clear_ubs_copilot_results(self) -> None:
        if hasattr(self, "ubs_copilot_tree"):
            for item in self.ubs_copilot_tree.get_children():
                self.ubs_copilot_tree.delete(item)
        self.ubs_copilot_details = {}
        self._set_ubs_copilot_detail("")
