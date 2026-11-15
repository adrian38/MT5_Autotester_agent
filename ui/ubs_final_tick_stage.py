"""Estado, seleccion de run y marcado de filas por etapa de Final Tick."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from tkinter import messagebox

from ui.ubs_final_tick_queries import (
    FINAL_TICK_RUN_OPTION_SPECS,
    final_tick_run_options_sql,
)
from ui.ubs_final_tick_rows import UBSFinalTickRowsMixin
from ui.ubs_final_tick_shared import stage_label, stage_suffix


class UBSFinalTickStageMixin(UBSFinalTickRowsMixin):
    """Resuelve por sufijo el estado que cada pantalla guarda aparte."""

    # ------------------------------------------------------------------
    # Estado por etapa
    # ------------------------------------------------------------------
    def _final_tick_attr(self, stage: str, name: str):
        return getattr(self, f"ubs_final_tick{stage_suffix(stage)}_{name}")

    def _final_tick_tree_for(self, stage: str):
        return getattr(self, f"ubs_final_tick{stage_suffix(stage)}_tree", None)

    def _final_tick_checked(self, stage: str) -> set[str]:
        return self._final_tick_attr(stage, "checked")

    def _final_tick_section(self, stage: str) -> str:
        return f"ubs_final_tick{stage_suffix(stage)}"

    # ------------------------------------------------------------------
    # Seleccion de run
    # ------------------------------------------------------------------
    def _final_tick_run_options(self, conn: sqlite3.Connection, stage: str) -> list[tuple[int, str]]:
        suffix = stage_suffix(stage)
        template = FINAL_TICK_RUN_OPTION_SPECS[suffix]["label"]
        options: list[tuple[int, str]] = []
        for row in conn.execute(final_tick_run_options_sql(suffix)).fetchall():
            run_id = int(row["id"])
            options.append((
                run_id,
                template.format(
                    run_id=run_id,
                    created=str(row["created_at"] or "")[:16],
                    total=int(row["total"] or 0),
                    eligible=int(row["eligible"] or 0),
                    done=int(row["done"] or 0),
                    ok=int(row["ok"] or 0),
                    fail=int(row["fail"] or 0),
                ) + (" [arch]" if row["hidden"] else ""),
            ))
        return options

    def _selected_final_tick_run_id(self, options: list[tuple[int, str]], stage: str) -> int:
        """El run mas nuevo gana una sola vez; despues manda la eleccion del usuario."""
        if not options:
            return 0
        suffix = stage_suffix(stage)
        newest_run_id = options[0][0]
        seen_attr = f"_ubs_final_tick{suffix}_latest_seen_run_id"
        if newest_run_id > int(getattr(self, seen_attr, 0) or 0):
            setattr(self, seen_attr, newest_run_id)
            return newest_run_id
        run_id = self._final_tick_run_id_from_label(self._final_tick_attr(stage, "run_id").get())
        if run_id and any(option_id == run_id for option_id, _ in options):
            return run_id
        return newest_run_id

    def _update_final_tick_run_combo(
        self, options: list[tuple[int, str]], selected_run_id: int, stage: str,
    ) -> None:
        combo = getattr(self, f"ubs_final_tick{stage_suffix(stage)}_run_combo", None)
        if combo is None:
            return
        combo.configure(values=[label for _run_id, label in options])
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        run_var = self._final_tick_attr(stage, "run_id")
        if selected_label and run_var.get() != selected_label:
            run_var.set(selected_label)

    # ------------------------------------------------------------------
    # Marcado de filas
    # ------------------------------------------------------------------
    def _on_final_tick_tree_click(self, event, stage: str) -> str | None:
        tree = self._final_tick_tree_for(stage)
        if tree is None:
            return None
        item, column = self._tree_item_from_event(tree, event)
        if not item or column != "#1":
            return None
        info = self._final_tick_attr(stage, "paths").get(item, {})
        cid = info.get("id", item)
        checked = self._final_tick_checked(stage)
        if cid in checked:
            checked.remove(cid)
        else:
            checked.add(cid)
        values = list(tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(cid in checked)
            tree.item(item, values=values)
        return "break"

    def _checked_final_tick_infos(
        self, stage: str, *, fallback_selected: bool = True,
    ) -> list[dict[str, str]]:
        checked = self._final_tick_checked(stage)
        marked = [
            info for info in self._final_tick_attr(stage, "paths").values()
            if info.get("id") in checked
        ]
        if marked or not fallback_selected:
            return marked
        return self._selected_final_tick_infos(stage)

    def _selected_final_tick_infos(self, stage: str) -> list[dict[str, str]]:
        tree = self._final_tick_tree_for(stage)
        if tree is None:
            return []
        paths = self._final_tick_attr(stage, "paths")
        return [paths[item] for item in tree.selection() if item in paths]

    def _selected_final_tick_info(self, stage: str) -> dict[str, str]:
        infos = self._selected_final_tick_infos(stage)
        return infos[0] if infos else {}

    def _open_final_tick_path(self, stage: str, kind: str) -> None:
        info = self._selected_final_tick_info(stage)
        raw_path = info.get(kind, "") if info else ""
        if not raw_path:
            messagebox.showinfo(
                f"{stage_label(stage)} UBS",
                "Selecciona una fila con archivo disponible.",
            )
            return
        self._open_local_file(Path(raw_path).expanduser())
