from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ui.ubs_results_base import ubs_run_base_dates
from ubs.path_utils import resolve_workspace_path, workspace_path_exists


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSResultsRetryMixin:
    """Reintentos de candidatos y de runs completos."""

    def _ubs_retry_args(self, specific: list[str]) -> list[str]:
        args = [
            "--memory", str(self._ubs_memory_path()),
            "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            *specific,
            "--delay", str(self.delay.get()),
        ]
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
        args.extend(self._ubs_score_args())
        if not self.multiterminal_enabled.get():
            if self.mt5_path.get().strip():
                args.extend(["--mt5-path", self.mt5_path.get()])
            if self.mt5_data_root.get().strip():
                args.extend(["--data-dir", self.mt5_data_root.get()])
        symbol_map = self._effective_ubs_symbol_map_text()
        if symbol_map:
            args.extend(["--symbol-map", symbol_map])
        args.extend(self._effective_symbol_suffix_args())
        return args

    def _selected_ubs_result_path(self, kind: str):
        info = self._selected_ubs_result_info()
        if not info:
            return None
        raw_path = info.get(kind, "")
        return resolve_workspace_path(raw_path) if raw_path else None

    def _selected_ubs_result_info(self) -> dict:
        if not hasattr(self, "ubs_results_tree"):
            return {}
        selected = self.ubs_results_tree.selection()
        if not selected:
            return {}
        return self.ubs_result_paths.get(selected[0], {})

    def _open_ubs_output_dir(self) -> None:
        output_dir = self._ubs_generation_output_dir().expanduser()
        if not output_dir.exists():
            messagebox.showinfo("Agente UBS", f"No existe la carpeta:\n{output_dir}")
            return
        subprocess.Popen(["explorer", str(output_dir)])

    def _open_selected_ubs_set(self) -> None:
        path = self._selected_ubs_result_path("set")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_report(self) -> None:
        path = self._selected_ubs_result_path("report")
        if path is None:
            messagebox.showinfo("Agente UBS", "Ese resultado no tiene reporte asociado.")
            return
        self._open_local_file(path)

    def _retry_ubs_result_candidates(
        self,
        *,
        eligible_statuses: set[str],
        dialog_title: str,
        action_label: str,
        not_eligible_message: str,
        error_label: str,
    ) -> None:
        infos = [info for info in self._checked_ubs_result_infos() if info]
        if not infos:
            messagebox.showinfo(dialog_title, "Selecciona o marca un resultado primero.")
            return
        rows = [info for info in infos if info.get("status") in eligible_statuses]
        if not rows:
            messagebox.showinfo(dialog_title, not_eligible_message)
            return
        for info in rows:
            candidate_id = str(info.get("id", "")).strip()
            set_path = resolve_workspace_path(str(info.get("set", "")))
            if not candidate_id:
                messagebox.showinfo(dialog_title, "Una fila marcada no tiene candidate id.")
                return
            if not set_path.exists():
                messagebox.showinfo(dialog_title, f"No existe el set:\n{set_path}")
                return
        try:
            specific = []
            for info in rows:
                specific.extend(["--retry-candidate-id", str(info.get("id", "")).strip()])
            args = self._ubs_retry_args(specific)
        except Exception as exc:
            self._show_error(error_label, str(exc))
            return

        first = rows[0]
        details = [
            f"Accion: {action_label}",
            f"Candidatos: {', '.join('#' + str(info.get('id', '')).strip() for info in rows)}",
            f"Primero: {first.get('symbol', '')} {first.get('period', '')} ({Path(str(first.get('set', ''))).name})",
            f"Backtests previstos: {len(rows)}",
        ]
        if len(infos) > len(rows):
            details.append(f"Filas marcadas ignoradas por estado no aplicable: {len(infos) - len(rows)}")
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start(f"Confirmar {dialog_title.lower()}", len(rows), details):
            self.ubs_result_checked.clear()
            self._run_script("ubs_agent.py", args)

    def _retry_selected_ubs_mismatch(self) -> None:
        self._retry_ubs_result_candidates(
            eligible_statuses={"report_mismatch", "no_report"},
            dialog_title="Reprobar fila",
            action_label="Reprobar fila UBS (mismatch reporte / sin reporte)",
            not_eligible_message="Esta accion solo aplica a filas con estado mismatch reporte o sin reporte.",
            error_label="No se pudo preparar retry de fila",
        )

    def _retry_visible_ubs_run_mismatches(self) -> None:
        try:
            run_id = self._visible_ubs_run_id()
            if run_id <= 0:
                messagebox.showinfo("Agente UBS", "No hay run visible para continuar.")
                return
            problem_count = self._count_ubs_run_retryable_problems(run_id)
            if problem_count <= 0:
                messagebox.showinfo("Agente UBS", f"Run #{run_id} no tiene mismatch/sin reporte pendientes.")
                return
            conn = connect_memory(self._ubs_memory_path())
            try:
                run = conn.execute("select config_json from runs where id=?", (run_id,)).fetchone()
            finally:
                conn.close()
            if run is None:
                raise ValueError(f"No existe el run #{run_id} en memoria.")
            from_date, to_date = ubs_run_base_dates(run["config_json"])
            if not from_date or not to_date:
                raise ValueError(
                    f"El run #{run_id} no guarda sus fechas base; se cancela el retry para no usar la plantilla actual."
                )
            args = self._ubs_retry_args([
                "--retry-run-id", str(run_id),
                "--retry-mismatch-run",
                "--from-date", from_date,
                "--to-date", to_date,
            ])
        except Exception as exc:
            self._show_error("No se pudo preparar continuar run", str(exc))
            return

        details = [
            "Accion: Continuar run UBS (recuperar mismatch/sin reporte)",
            f"Run: #{run_id}",
            f"Fechas base originales: {from_date} -> {to_date}",
            f"Backtests previstos: {problem_count}",
            "Al terminar actualiza esas mismas filas SQLite.",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar continuar run", problem_count, details):
            self._run_script("ubs_agent.py", args)

    def _retry_visible_ubs_full_run(self) -> None:
        try:
            run_id = self._visible_ubs_run_id()
            if run_id <= 0:
                messagebox.showinfo("Agente UBS", "No hay run visible para reprobar.")
                return
            checked_ids = self._checked_ubs_retry_ids(run_id)
            if checked_ids is None:
                return
            candidate_count = len(checked_ids) or self._count_ubs_run_existing_sets(run_id)
            if candidate_count <= 0:
                messagebox.showinfo("Agente UBS", f"Run #{run_id} no tiene candidatos con .set existente.")
                return
            specific = [
                "--retry-run-id", str(run_id),
                "--retry-full-run",
            ]
            for candidate_id in checked_ids:
                specific.extend(["--retry-candidate-id", candidate_id])
            args = self._ubs_retry_args(specific)
        except Exception as exc:
            self._show_error("No se pudo preparar reprobar run completo", str(exc))
            return

        scoped = bool(checked_ids)
        details = [
            "Accion: " + ("Reprobar candidatos marcados" if scoped else "Reprobar run UBS completo"),
            f"Run: #{run_id}",
            f"Backtests previstos: {candidate_count}",
            (
                "Relanza solo las filas marcadas con [x]."
                if scoped
                else "Relanza todos los candidatos con .set existente."
            ),
            "Limpia reportes/copias accepted previas de esos candidatos antes de re-evaluar.",
        ]
        if scoped:
            details.append(f"Candidatos: {', '.join('#' + candidate_id for candidate_id in checked_ids[:20])}")
            if len(checked_ids) > 20:
                details.append(f"... y {len(checked_ids) - 20} mas")
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start(
            "Confirmar reprobar marcados" if scoped else "Confirmar reprobar run completo",
            candidate_count,
            details,
        ):
            self.ubs_result_checked.clear()
            self._run_script("ubs_agent.py", args)

    def _checked_ubs_retry_ids(self, run_id: int) -> list[str] | None:
        infos = [
            info for info in self._checked_ubs_result_infos(fallback_selected=False)
            if str(info.get("run") or "").strip() == str(run_id)
        ]
        checked_ids: list[str] = []
        missing_sets = []
        for info in infos:
            candidate_id = str(info.get("id") or "").strip()
            set_path = resolve_workspace_path(str(info.get("set") or ""))
            if not candidate_id:
                messagebox.showinfo("Agente UBS", "Una fila marcada no tiene candidate id.")
                return None
            if not set_path.exists():
                missing_sets.append(str(set_path))
            checked_ids.append(candidate_id)
        if missing_sets:
            messagebox.showinfo(
                "Agente UBS",
                "Hay filas marcadas con .set faltante:\n" + "\n".join(missing_sets[:8]),
            )
            return None
        return checked_ids

    def _visible_ubs_run_id(self) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        selected = self.ubs_results_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            return int(match.group(1))
        conn = connect_memory(memory_path)
        try:
            row = conn.execute("select id from runs where hidden=0 order by id desc limit 1").fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _count_ubs_run_mismatches(self, run_id: int) -> int:
        return self._count_ubs_run_retryable_problems(run_id)

    def _count_ubs_run_retryable_problems(self, run_id: int) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        conn = connect_memory(memory_path)
        try:
            row = conn.execute(
                """
                select count(*) as total
                from candidates
                where run_id=? and status in ('report_mismatch', 'no_report')
                """,
                (run_id,),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()

    def _count_ubs_run_existing_sets(self, run_id: int) -> int:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "select set_path from candidates where run_id=? order by generation, id",
                (run_id,),
            ).fetchall()
            return sum(1 for row in rows if workspace_path_exists(str(row["set_path"] or "")))
        finally:
            conn.close()

    def _retry_no_trades_result(self) -> None:
        self._retry_ubs_result_candidates(
            eligible_statuses={"no_trades"},
            dialog_title="Repetir sin ops",
            action_label="Repetir candidato sin operaciones",
            not_eligible_message="Esta acción solo aplica a filas con estado 'sin operaciones'.",
            error_label="No se pudo preparar retry sin ops",
        )
