"""Perfiles de terminal MT4, que solo se usan para leer la cuenta.

La pantalla pide lo mismo que Multiterminales MT5 menos lo que existe por el
Expert Advisor: aqui no se compila ni se prueba nada, asi que no hay carpeta
de Experts ni archivo .ex4 que validar.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from tkinter import messagebox

from ubs.account import DEFAULT_BROKER, normalize_broker


MT4_SECTION_PREFIX = "mt4terminal."


class MT4TerminalLogicMixin:
    """Lectura, edicion y guardado de los perfiles de terminal MT4."""

    def _read_mt4_terminal_profiles(
        self,
        parser: configparser.ConfigParser,
    ) -> list[dict[str, object]]:
        sections = [
            section for section in parser.sections()
            if section.lower().startswith(MT4_SECTION_PREFIX)
        ]

        def section_key(section: str) -> tuple[int, str]:
            suffix = section.split(".", 1)[1] if "." in section else section
            try:
                return (int(suffix), section)
            except ValueError:
                return (9999, section)

        profiles: list[dict[str, object]] = []
        for section in sorted(sections, key=section_key):
            data = parser[section]
            profiles.append({
                "enabled": self._bool_setting(data.get("enabled"), True),
                "broker": normalize_broker(data.get("broker", DEFAULT_BROKER)),
                "name": data.get("name", section).strip() or section,
                "mt4_path": data.get("mt4_path", "").strip(),
                "data_dir": data.get("data_dir", "").strip(),
                "portable": self._bool_setting(data.get("portable"), False),
            })
        return profiles

    def _active_mt4_terminal_broker(self) -> str:
        broker_var = getattr(self, "ubs_broker", None)
        return normalize_broker(broker_var.get() if broker_var is not None else DEFAULT_BROKER)

    def _mt4_profile_broker(self, profile: dict[str, object]) -> str:
        return normalize_broker(profile.get("broker", DEFAULT_BROKER))

    def _broker_mt4_terminal_profile_items(
        self,
        *,
        include_disabled: bool = True,
    ) -> list[tuple[int, dict[str, object]]]:
        broker = self._active_mt4_terminal_broker()
        items = [
            (index, profile)
            for index, profile in enumerate(self.mt4_terminal_profiles)
            if self._mt4_profile_broker(profile) == broker
        ]
        if include_disabled:
            return items
        return [(index, profile) for index, profile in items if bool(profile.get("enabled"))]

    def _broker_mt4_terminal_profiles(
        self,
        *,
        include_disabled: bool = True,
    ) -> list[dict[str, object]]:
        return [
            profile
            for _index, profile in self._broker_mt4_terminal_profile_items(
                include_disabled=include_disabled
            )
        ]

    def _update_mt4_terminal_summary(self) -> None:
        if not hasattr(self, "mt4_terminal_summary"):
            return
        total = len(self._broker_mt4_terminal_profiles())
        active = len(self._broker_mt4_terminal_profiles(include_disabled=False))
        self.mt4_terminal_summary.set(
            f"{self._active_mt4_terminal_broker()}: {total} terminales / "
            f"{active} activas / solo lectura de cuenta"
        )

    def _save_current_mt4_terminal_editor(self) -> None:
        if not hasattr(self, "mt4_profile_name"):
            return
        index = self.mt4_selected_index
        if index is None or index < 0 or index >= len(self.mt4_terminal_profiles):
            return
        self.mt4_terminal_profiles[index] = {
            "enabled": bool(self.mt4_profile_enabled.get()),
            "broker": normalize_broker(self.mt4_profile_broker.get()),
            "name": self.mt4_profile_name.get().strip() or f"MT4 {index + 1}",
            "mt4_path": self.mt4_profile_mt4_path.get().strip(),
            "data_dir": self.mt4_profile_data_dir.get().strip(),
            "portable": bool(self.mt4_profile_portable.get()),
        }

    def _mt4_terminal_tree_values(self, profile: dict[str, object], index: int) -> tuple:
        name = str(profile.get("name") or f"MT4 {index + 1}")
        return (
            self._checkbox_text(name in self.mt4_terminal_checked),
            "si" if bool(profile.get("enabled")) else "no",
            self._mt4_profile_broker(profile),
            name,
            str(profile.get("mt4_path") or ""),
            str(profile.get("data_dir") or ""),
        )

    def _refresh_mt4_terminal_tree(self) -> None:
        if not hasattr(self, "mt4_terminal_tree"):
            self._update_mt4_terminal_summary()
            return
        selected_index = self.mt4_selected_index
        for item in self.mt4_terminal_tree.get_children():
            self.mt4_terminal_tree.delete(item)
        valid_names = set()
        visible_items = self._broker_mt4_terminal_profile_items()
        for index, profile in visible_items:
            name = str(profile.get("name") or f"MT4 {index + 1}")
            valid_names.add(name)
            tag = "odd" if index % 2 else "even"
            self.mt4_terminal_tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._mt4_terminal_tree_values(profile, index),
                tags=(tag,),
            )
        self.mt4_terminal_checked.intersection_update(valid_names)
        self._restore_mt4_terminal_selection(selected_index, visible_items)
        self._update_mt4_terminal_summary()

    def _restore_mt4_terminal_selection(
        self,
        selected_index: int | None,
        visible_items: list[tuple[int, dict[str, object]]],
    ) -> None:
        visible_indexes = {index for index, _profile in visible_items}
        if selected_index is not None and selected_index in visible_indexes:
            self.mt4_terminal_tree.selection_set(str(selected_index))
            self.mt4_terminal_tree.focus(str(selected_index))
        elif visible_items:
            first_index = visible_items[0][0]
            self.mt4_terminal_tree.selection_set(str(first_index))
            self.mt4_terminal_tree.focus(str(first_index))
            self._load_mt4_terminal_profile_editor(first_index)
        else:
            self._load_mt4_terminal_profile_editor(-1)

    def _on_mt4_terminal_tree_click(self, event) -> str | None:
        if not hasattr(self, "mt4_terminal_tree"):
            return None
        item, column = self._tree_item_from_event(self.mt4_terminal_tree, event)
        if not item or column != "#1":
            return None
        try:
            index = int(item)
        except ValueError:
            return None
        if index < 0 or index >= len(self.mt4_terminal_profiles):
            return None
        name = str(self.mt4_terminal_profiles[index].get("name") or f"MT4 {index + 1}")
        was_checked = name in self.mt4_terminal_checked
        self.mt4_terminal_checked.clear()
        for other in self.mt4_terminal_tree.get_children():
            values = list(self.mt4_terminal_tree.item(other, "values"))
            if values:
                values[0] = self._checkbox_text(False)
                self.mt4_terminal_tree.item(other, values=values)
        if not was_checked:
            self.mt4_terminal_checked.add(name)
        values = list(self.mt4_terminal_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(name in self.mt4_terminal_checked)
            self.mt4_terminal_tree.item(item, values=values)
        self._select_mt4_terminal_profile(index)
        return "break"

    def _update_mt4_terminal_tree_item(self, index: int) -> None:
        if not hasattr(self, "mt4_terminal_tree"):
            return
        if index < 0 or index >= len(self.mt4_terminal_profiles):
            return
        iid = str(index)
        if self.mt4_terminal_tree.exists(iid):
            self.mt4_terminal_tree.item(
                iid,
                values=self._mt4_terminal_tree_values(self.mt4_terminal_profiles[index], index),
            )

    def _load_mt4_terminal_profile_editor(self, index: int) -> None:
        if index < 0 or index >= len(self.mt4_terminal_profiles):
            self.mt4_selected_index = None
            self.mt4_profile_enabled.set(False)
            self.mt4_profile_portable.set(False)
            self.mt4_profile_broker.set(self._active_mt4_terminal_broker())
            self.mt4_profile_name.set("")
            self.mt4_profile_mt4_path.set("")
            self.mt4_profile_data_dir.set("")
            return
        profile = self.mt4_terminal_profiles[index]
        self.mt4_selected_index = index
        self.mt4_profile_enabled.set(bool(profile.get("enabled")))
        self.mt4_profile_portable.set(bool(profile.get("portable")))
        self.mt4_profile_broker.set(self._mt4_profile_broker(profile))
        self.mt4_profile_name.set(str(profile.get("name") or f"MT4 {index + 1}"))
        self.mt4_profile_mt4_path.set(str(profile.get("mt4_path") or ""))
        self.mt4_profile_data_dir.set(str(profile.get("data_dir") or ""))

    def _select_mt4_terminal_profile(self, index: int) -> None:
        self._load_mt4_terminal_profile_editor(index)
        if not hasattr(self, "mt4_terminal_tree"):
            return
        if index < 0 or index >= len(self.mt4_terminal_profiles):
            return
        iid = str(index)
        if self.mt4_terminal_tree.exists(iid):
            self.mt4_terminal_tree.selection_set(iid)
            self.mt4_terminal_tree.focus(iid)

    def _on_mt4_terminal_tree_select(self, _event=None) -> None:
        if not hasattr(self, "mt4_terminal_tree"):
            return
        selected = self.mt4_terminal_tree.selection()
        if not selected:
            return
        try:
            index = int(selected[0])
        except (TypeError, ValueError):
            return
        if index == self.mt4_selected_index:
            return
        old_index = self.mt4_selected_index
        self._save_current_mt4_terminal_editor()
        if old_index is not None:
            self._update_mt4_terminal_tree_item(old_index)
        self._load_mt4_terminal_profile_editor(index)
        self._update_mt4_terminal_summary()

    def _apply_mt4_terminal_editor(self) -> None:
        self._save_current_mt4_terminal_editor()
        index = self.mt4_selected_index
        if index is not None:
            self._update_mt4_terminal_tree_item(index)
        self._refresh_mt4_terminal_tree()
        if index is not None:
            self._select_mt4_terminal_profile(index)
        self._update_mt4_terminal_summary()
        self.status_text.set("Fila MT4 aplicada")

    def _new_mt4_terminal_profile(self, name: str | None = None) -> dict[str, object]:
        index = len(self.mt4_terminal_profiles) + 1
        return {
            "enabled": True,
            "broker": self._active_mt4_terminal_broker(),
            "name": name or f"MT4 {index}",
            "mt4_path": "",
            "data_dir": "",
            "portable": False,
        }

    def _add_mt4_terminal_profile(self) -> None:
        self._save_current_mt4_terminal_editor()
        self.mt4_terminal_profiles.append(self._new_mt4_terminal_profile())
        self._refresh_mt4_terminal_tree()
        self._select_mt4_terminal_profile(len(self.mt4_terminal_profiles) - 1)

    def _duplicate_mt4_terminal_profile(self) -> None:
        self._save_current_mt4_terminal_editor()
        index = self.mt4_selected_index if self.mt4_selected_index is not None else 0
        if index < 0 or index >= len(self.mt4_terminal_profiles):
            return
        source = dict(self.mt4_terminal_profiles[index])
        source["name"] = f"{source.get('name') or f'MT4 {index + 1}'} copia"
        self.mt4_terminal_profiles.append(source)
        self._refresh_mt4_terminal_tree()
        self._select_mt4_terminal_profile(len(self.mt4_terminal_profiles) - 1)

    def _delete_mt4_terminal_profile(self) -> None:
        index = self.mt4_selected_index
        if index is None or index < 0 or index >= len(self.mt4_terminal_profiles):
            messagebox.showinfo("Terminales MT4", "Selecciona una terminal para eliminar.")
            return
        name = str(self.mt4_terminal_profiles[index].get("name") or f"MT4 {index + 1}")
        if not messagebox.askyesno("Eliminar terminal MT4", f"Eliminar el perfil '{name}'?"):
            return
        del self.mt4_terminal_profiles[index]
        self.mt4_selected_index = None
        self._refresh_mt4_terminal_tree()
        if self.mt4_terminal_profiles:
            self._select_mt4_terminal_profile(min(index, len(self.mt4_terminal_profiles) - 1))
        else:
            self._load_mt4_terminal_profile_editor(-1)
        self._update_mt4_terminal_summary()

    def _mt4_profile_path(self, profile: dict[str, object], key: str) -> Path | None:
        raw = str(profile.get(key) or "").strip()
        return Path(raw).expanduser() if raw else None

    def _validate_mt4_terminal_errors(self) -> list[str]:
        self._save_current_mt4_terminal_editor()
        errors: list[str] = []
        items = self._broker_mt4_terminal_profile_items(include_disabled=False)
        if not items:
            errors.append(
                f"No hay terminales MT4 activas para {self._active_mt4_terminal_broker()}."
            )
        for index, profile in items:
            name = str(profile.get("name") or f"MT4 {index + 1}")
            mt4_path = self._mt4_profile_path(profile, "mt4_path")
            data_dir = self._mt4_profile_path(profile, "data_dir")
            if not mt4_path:
                errors.append(f"{name}: falta terminal.exe.")
            elif not mt4_path.exists() or not mt4_path.is_file():
                errors.append(f"{name}: no existe terminal.exe: {mt4_path}")
            if data_dir and (not data_dir.exists() or not data_dir.is_dir()):
                errors.append(f"{name}: carpeta datos MT4 invalida: {data_dir}")
        return errors

    def _validate_mt4_terminal_profiles(self) -> bool:
        errors = self._validate_mt4_terminal_errors()
        if errors:
            details = "\n".join(f"- {item}" for item in errors[:20])
            if len(errors) > 20:
                details += f"\n- ... y {len(errors) - 20} mas"
            self._show_error("Terminales MT4 invalidas", details)
            return False
        self.status_text.set("Terminales MT4 validadas")
        messagebox.showinfo("Terminales MT4", "Perfiles MT4 validados correctamente.")
        return True

    def _save_mt4_terminal_clicked(self) -> None:
        try:
            self._save_current_mt4_terminal_editor()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Terminales MT4", str(exc))
            return
        self._refresh_mt4_terminal_tree()
        self.status_text.set("Terminales MT4 guardadas")
        messagebox.showinfo(
            "Terminales MT4",
            "La configuracion de terminales MT4 se guardo correctamente.",
        )
