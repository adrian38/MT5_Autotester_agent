"""Logica de Final Tick aplicada directamente a las seeds UBS."""
from __future__ import annotations

from pathlib import Path
from tkinter import messagebox

from ubs.memory import AgentMemory
from ubs.path_utils import resolve_workspace_path
from ubs.agent.seeds.final_tick import eligible_seed_final_tick_rows
from ui.ubs_final_tick_shared import stage_label, stage_suffix


class UBSSeedFinalTickLogicMixin:
    def _seed_final_tick_attr(self, stage: str, name: str):
        return getattr(self, f"ubs_seed_final_tick{stage_suffix(stage)}_{name}")

    def _seed_final_tick_state(self, stage: str):
        return tuple(
            self._seed_final_tick_attr(stage, name)
            for name in ("summary", "status", "tree", "paths")
        )

    def _load_seed_final_tick_rows(self, stage: str) -> list:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return []
        memory = AgentMemory(memory_path)
        try:
            return eligible_seed_final_tick_rows(memory, stage)
        finally:
            memory.close()

    def _seed_final_tick_reason(self, row, status: str) -> str:
        """La misma causa que pintan las pantallas de candidatos."""
        return self._ubs_final_tick_reason(
            status,
            self._parse_ubs_final_tick_similarity(row["ft_similarity_json"]),
            history_quality=row["ft_history_quality"],
            min_history_quality=row["ft_min_history_quality"],
        )

    def _seed_final_tick_values(self, row, status: str, checked: bool = False) -> tuple[str, ...]:
        return (
            self._checkbox_text(checked),
            Path(str(row["seed_path"])).name,
            str(row["symbol"]),
            str(row["period"]),
            self._format_ubs_status(status),
            self._seed_final_tick_reason(row, status),
            "" if row["ft_history_quality"] is None else f"{self._format_ubs_number(row['ft_history_quality'])}%",
            self._format_ubs_number(row["ft_ohlc_score"]),
            self._format_ubs_number(row["ft_real_tick_score"]),
            str(row["final_tick_from_date"] or ""),
            str(row["final_tick_to_date"] or ""),
        )

    def _refresh_seed_final_tick_stage(self, stage: str) -> None:
        summary, status_var, tree, paths = self._seed_final_tick_state(stage)
        rows = self._load_seed_final_tick_rows(stage)
        checked = self._seed_final_tick_attr(stage, "checked")
        active_paths = {str(row["seed_path"]) for row in rows}
        checked.intersection_update(active_paths)
        selected_path = self._selected_seed_final_tick_path(stage, "set", quiet=True)
        tree.delete(*tree.get_children())
        paths.clear()
        counts: dict[str, int] = {}
        selected_item = None
        for index, row in enumerate(rows):
            status = str(row["final_tick_status"] or "pending")
            counts[status] = counts.get(status, 0) + 1
            item = tree.insert(
                "", "end",
                values=self._seed_final_tick_values(row, status, str(row["seed_path"]) in checked),
                tags=(self._ubs_result_tag(status), "odd" if index % 2 else "even"),
            )
            paths[item] = {
                "set": str(row["seed_path"] or ""),
                "ohlc": str(row["ft_ohlc_report_path"] or ""),
                "tick": str(row["ft_real_tick_report_path"] or ""),
            }
            if selected_path and resolve_workspace_path(paths[item]["set"]) == selected_path:
                selected_item = item
        label = stage_label(stage, "Seeds Final Tick")
        detail = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        summary.set(f"{label}: {len(rows)} elegibles" + (f" · {detail}" if detail else ""))
        status_var.set("Datos actualizados")
        if selected_item:
            tree.selection_set(selected_item)
            tree.see(selected_item)

    def _refresh_ubs_seed_final_tick(self) -> None:
        self._refresh_seed_final_tick_stage("probe")

    def _on_seed_final_tick_click(self, event, stage: str) -> None:
        _, _, tree, paths = self._seed_final_tick_state(stage)
        if tree.identify_region(event.x, event.y) != "cell" or tree.identify_column(event.x) != "#1":
            return
        item = tree.identify_row(event.y)
        seed_path = paths.get(item, {}).get("set", "")
        if not seed_path:
            return
        checked = self._seed_final_tick_attr(stage, "checked")
        checked.discard(seed_path) if seed_path in checked else checked.add(seed_path)
        values = list(tree.item(item, "values"))
        values[0] = self._checkbox_text(seed_path in checked)
        tree.item(item, values=values)

    def _seed_final_tick_cli_args(
        self, stage: str, *, pending_only: bool, retry_quality: bool = False,
        reconcile_only: bool = False,
    ) -> list[str]:
        args = self._ubs_final_tick_args(
            0, pending_only=pending_only, retry_pending_quality=retry_quality,
            final_tick_stage=stage,
        )
        args[args.index("--evaluate-final-tick")] = "--evaluate-seed-final-tick"
        run_index = args.index("--final-tick-run-id")
        del args[run_index:run_index + 2]
        if reconcile_only:
            args.append("--final-tick-reconcile-only")
        return args

    def _seed_final_tick_scope_rows(
        self, stage: str, *, pending_only: bool, retry_quality: bool,
    ) -> list:
        rows = self._load_seed_final_tick_rows(stage)
        rows = [row for row in rows if resolve_workspace_path(row["seed_path"]).exists()]
        if retry_quality:
            return [row for row in rows if str(row["final_tick_status"] or "") == "pending_history_quality"]
        if pending_only:
            return self._pending_final_tick_rows(rows, stage)
        return rows

    def _run_seed_final_tick_stage(
        self, stage: str, *, pending_only: bool = True,
        retry_quality: bool = False, reconcile_only: bool = False,
    ) -> None:
        label = f"UBS {stage_label(stage, 'Seeds Final Tick')}"
        try:
            rows = self._seed_final_tick_scope_rows(
                stage, pending_only=pending_only, retry_quality=retry_quality,
            )
            if not rows:
                messagebox.showinfo(label, "No hay seeds elegibles pendientes para esta accion.")
                return
            args = self._seed_final_tick_cli_args(
                stage, pending_only=pending_only, retry_quality=retry_quality,
                reconcile_only=reconcile_only,
            )
        except Exception as exc:
            self._show_error(f"No se pudo preparar {label}", str(exc))
            return
        mode = "reconciliar reportes" if reconcile_only else ("continuar pendientes" if pending_only else "reprobar todas")
        details = [f"Etapa: {label}", f"Accion: {mode}", f"Seeds: {len(rows)}"]
        if not self._confirm_execution_start(f"Confirmar {label}", len(rows), details):
            return
        self._show_section(f"ubs_seed_final_tick{stage_suffix(stage)}")
        self.status_text.set(f"Preparando {label}: {len(rows)} seed(s)")
        self.after(10, lambda: self._run_script("ubs_agent.py", args))

    def _selected_seed_final_tick_path(self, stage: str, kind: str, *, quiet: bool = False):
        _, _, tree, paths = self._seed_final_tick_state(stage)
        selected = tree.selection()
        raw = paths.get(selected[0], {}).get(kind, "") if selected else ""
        if raw:
            return resolve_workspace_path(raw)
        if not quiet:
            messagebox.showinfo("UBS Seeds Final Tick", "Selecciona una fila con archivo disponible.")
        return None

    def _open_seed_final_tick_path(self, stage: str, kind: str) -> None:
        path = self._selected_seed_final_tick_path(stage, kind)
        if path is not None:
            self._open_local_file(path)

    def _run_ubs_seed_final_tick(self) -> None:
        self._run_seed_final_tick_stage("probe")

    def _rerun_ubs_seed_final_tick(self) -> None:
        self._run_seed_final_tick_stage("probe", pending_only=False)

    def _retry_ubs_seed_final_tick_quality(self) -> None:
        self._run_seed_final_tick_stage("probe", retry_quality=True)

    def _reconcile_ubs_seed_final_tick(self) -> None:
        self._run_seed_final_tick_stage("probe", reconcile_only=True)
