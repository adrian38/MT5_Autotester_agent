from __future__ import annotations

import configparser
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from run_tests import looks_like_ubs_expert_file


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"


class MultiterminalLogicMixin:
    def _read_multiterminal_profiles(self, parser: configparser.ConfigParser) -> list[dict[str, object]]:
        sections = [section for section in parser.sections() if section.lower().startswith("terminal.")]

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
                "name": data.get("name", section).strip() or section,
                "mt5_path": data.get("mt5_path", "").strip(),
                "data_dir": data.get("data_dir", "").strip(),
                "experts_root": data.get("experts_root", "").strip(),
                "ubs_ex5_file": data.get("ubs_ex5_file", "").strip(),
                "portable": self._bool_setting(data.get("portable"), False),
            })
        if profiles:
            return profiles
        return [{
            "enabled": bool(self.mt5_path.get().strip()),
            "name": "MT5 principal",
            "mt5_path": self.mt5_path.get().strip(),
            "data_dir": self.mt5_data_root.get().strip(),
            "experts_root": self.experts_root.get().strip(),
            "ubs_ex5_file": self.ubs_ex5_file.get().strip(),
            "portable": False,
        }]


    def _multiterminal_worker_limit(self) -> int:
        try:
            workers = int(self.multiterminal_workers.get())
        except (tk.TclError, ValueError):
            workers = 1
        workers = max(1, workers)
        try:
            if int(self.multiterminal_workers.get()) != workers:
                self.multiterminal_workers.set(workers)
        except (tk.TclError, ValueError):
            self.multiterminal_workers.set(workers)
        return workers

    def _active_multiterminal_profiles(self) -> list[dict[str, object]]:
        return [profile for profile in self.multiterminal_profiles if bool(profile.get("enabled"))]

    def _update_multiterminal_summary(self) -> None:
        if not hasattr(self, "multiterminal_summary"):
            return
        active = len(self._active_multiterminal_profiles())
        workers = min(self._multiterminal_worker_limit(), active) if active else 0
        mode = "on" if self.multiterminal_enabled.get() else "off"
        self.multiterminal_summary.set(f"{active} activas / usando hasta {workers} / {mode}")

    def _save_current_multiterminal_editor(self) -> None:
        if not hasattr(self, "mt_profile_name"):
            return
        index = self.mt_selected_index
        if index is None or index < 0 or index >= len(self.multiterminal_profiles):
            return
        self.multiterminal_profiles[index] = {
            "enabled": bool(self.mt_profile_enabled.get()),
            "name": self.mt_profile_name.get().strip() or f"Terminal {index + 1}",
            "mt5_path": self.mt_profile_mt5_path.get().strip(),
            "data_dir": self.mt_profile_data_dir.get().strip(),
            "experts_root": self.mt_profile_experts_root.get().strip(),
            "ubs_ex5_file": self.mt_profile_ubs_ex5_file.get().strip(),
            "portable": bool(self.mt_profile_portable.get()),
        }

    def _multiterminal_tree_values(self, profile: dict[str, object]) -> tuple[str, str, str, str, str, str, str]:
        return (
            "si" if bool(profile.get("enabled")) else "no",
            str(profile.get("name") or ""),
            str(profile.get("mt5_path") or ""),
            str(profile.get("data_dir") or ""),
            str(profile.get("experts_root") or ""),
            str(profile.get("ubs_ex5_file") or ""),
            "si" if bool(profile.get("portable")) else "no",
        )

    def _refresh_multiterminal_tree(self) -> None:
        if not hasattr(self, "multiterminal_tree"):
            self._update_multiterminal_summary()
            return
        selected_index = self.mt_selected_index
        for item in self.multiterminal_tree.get_children():
            self.multiterminal_tree.delete(item)
        for index, profile in enumerate(self.multiterminal_profiles):
            tag = "odd" if index % 2 else "even"
            self.multiterminal_tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._multiterminal_tree_values(profile),
                tags=(tag,),
            )
        if selected_index is not None and 0 <= selected_index < len(self.multiterminal_profiles):
            self.multiterminal_tree.selection_set(str(selected_index))
            self.multiterminal_tree.focus(str(selected_index))
        self._update_multiterminal_summary()

    def _update_multiterminal_tree_item(self, index: int) -> None:
        if not hasattr(self, "multiterminal_tree"):
            return
        if index < 0 or index >= len(self.multiterminal_profiles):
            return
        iid = str(index)
        if self.multiterminal_tree.exists(iid):
            self.multiterminal_tree.item(iid, values=self._multiterminal_tree_values(self.multiterminal_profiles[index]))

    def _load_multiterminal_profile_editor(self, index: int) -> None:
        if index < 0 or index >= len(self.multiterminal_profiles):
            self.mt_selected_index = None
            self.mt_profile_enabled.set(False)
            self.mt_profile_portable.set(False)
            self.mt_profile_name.set("")
            self.mt_profile_mt5_path.set("")
            self.mt_profile_data_dir.set("")
            self.mt_profile_experts_root.set("")
            self.mt_profile_ubs_ex5_file.set("")
            return
        profile = self.multiterminal_profiles[index]
        self.mt_selected_index = index
        self.mt_profile_enabled.set(bool(profile.get("enabled")))
        self.mt_profile_portable.set(bool(profile.get("portable")))
        self.mt_profile_name.set(str(profile.get("name") or f"Terminal {index + 1}"))
        self.mt_profile_mt5_path.set(str(profile.get("mt5_path") or ""))
        self.mt_profile_data_dir.set(str(profile.get("data_dir") or ""))
        self.mt_profile_experts_root.set(str(profile.get("experts_root") or ""))
        self.mt_profile_ubs_ex5_file.set(str(profile.get("ubs_ex5_file") or ""))

    def _select_multiterminal_profile(self, index: int) -> None:
        self._load_multiterminal_profile_editor(index)
        if hasattr(self, "multiterminal_tree") and 0 <= index < len(self.multiterminal_profiles):
            self.multiterminal_tree.selection_set(str(index))
            self.multiterminal_tree.focus(str(index))

    def _on_multiterminal_tree_select(self, _event=None) -> None:
        if not hasattr(self, "multiterminal_tree"):
            return
        selected = self.multiterminal_tree.selection()
        if not selected:
            return
        try:
            index = int(selected[0])
        except (TypeError, ValueError):
            return
        if index == self.mt_selected_index:
            return
        old_index = self.mt_selected_index
        self._save_current_multiterminal_editor()
        if old_index is not None:
            self._update_multiterminal_tree_item(old_index)
        self._load_multiterminal_profile_editor(index)
        self._update_multiterminal_summary()

    def _apply_multiterminal_editor(self) -> None:
        self._save_current_multiterminal_editor()
        if self.mt_selected_index is not None:
            self._update_multiterminal_tree_item(self.mt_selected_index)
        self._update_multiterminal_summary()
        self.status_text.set("Fila multiterminal aplicada")

    def _new_multiterminal_profile(self, name: str | None = None) -> dict[str, object]:
        index = len(self.multiterminal_profiles) + 1
        return {
            "enabled": True,
            "name": name or f"Terminal {index}",
            "mt5_path": self.mt5_path.get().strip(),
            "data_dir": self.mt5_data_root.get().strip(),
            "experts_root": self.experts_root.get().strip(),
            "ubs_ex5_file": self.ubs_ex5_file.get().strip(),
            "portable": False,
        }

    def _add_multiterminal_profile(self) -> None:
        self._save_current_multiterminal_editor()
        self.multiterminal_profiles.append(self._new_multiterminal_profile())
        self._refresh_multiterminal_tree()
        self._select_multiterminal_profile(len(self.multiterminal_profiles) - 1)

    def _duplicate_multiterminal_profile(self) -> None:
        self._save_current_multiterminal_editor()
        index = self.mt_selected_index if self.mt_selected_index is not None else 0
        if index < 0 or index >= len(self.multiterminal_profiles):
            return
        source = dict(self.multiterminal_profiles[index])
        source["name"] = f"{source.get('name') or f'Terminal {index + 1}'} copia"
        self.multiterminal_profiles.append(source)
        self._refresh_multiterminal_tree()
        self._select_multiterminal_profile(len(self.multiterminal_profiles) - 1)

    def _delete_multiterminal_profile(self) -> None:
        index = self.mt_selected_index
        if index is None or index < 0 or index >= len(self.multiterminal_profiles):
            messagebox.showinfo("Multiterminales", "Selecciona una terminal para eliminar.")
            return
        name = str(self.multiterminal_profiles[index].get("name") or f"Terminal {index + 1}")
        if not messagebox.askyesno("Eliminar terminal", f"Eliminar el perfil '{name}'?"):
            return
        del self.multiterminal_profiles[index]
        self.mt_selected_index = None
        self._refresh_multiterminal_tree()
        if self.multiterminal_profiles:
            self._select_multiterminal_profile(min(index, len(self.multiterminal_profiles) - 1))
        else:
            self._load_multiterminal_profile_editor(-1)
        self._update_multiterminal_summary()

    def _profile_path(self, profile: dict[str, object], key: str, *, base_key: str | None = None) -> Path | None:
        raw = str(profile.get(key) or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.is_absolute() or not base_key:
            return path
        base_raw = str(profile.get(base_key) or "").strip()
        return (Path(base_raw).expanduser() / path) if base_raw else path

    def _validate_multiterminal_errors(self, *, require_ubs: bool = True) -> list[str]:
        self._save_current_multiterminal_editor()
        errors: list[str] = []
        active = self._active_multiterminal_profiles()
        if not active:
            errors.append("No hay terminales habilitadas.")
        for index, profile in enumerate(self.multiterminal_profiles, start=1):
            if not bool(profile.get("enabled")):
                continue
            name = str(profile.get("name") or f"Terminal {index}")
            mt5_path = self._profile_path(profile, "mt5_path")
            data_dir = self._profile_path(profile, "data_dir")
            experts_root = self._profile_path(profile, "experts_root")
            ubs_ex5 = self._profile_path(profile, "ubs_ex5_file", base_key="experts_root")
            if not mt5_path:
                errors.append(f"{name}: falta terminal64.exe.")
            elif not mt5_path.exists() or not mt5_path.is_file():
                errors.append(f"{name}: no existe terminal64.exe: {mt5_path}")
            if data_dir and (not data_dir.exists() or not data_dir.is_dir()):
                errors.append(f"{name}: carpeta datos MT5 invalida: {data_dir}")
            if not experts_root:
                errors.append(f"{name}: falta carpeta MQL5\\Experts.")
            elif not experts_root.exists() or not experts_root.is_dir():
                errors.append(f"{name}: carpeta MQL5\\Experts invalida: {experts_root}")
            if require_ubs:
                if not ubs_ex5:
                    errors.append(f"{name}: falta archivo UBS .ex5.")
                else:
                    if not looks_like_ubs_expert_file(ubs_ex5):
                        errors.append(f"{name}: UBS .ex5 no parece Ultimate Breakout System: {ubs_ex5}")
                    if not ubs_ex5.exists() or not ubs_ex5.is_file():
                        errors.append(f"{name}: no existe UBS .ex5: {ubs_ex5}")
        return errors

    def _validate_multiterminal_profiles(self) -> bool:
        errors = self._validate_multiterminal_errors()
        if errors:
            details = "\n".join(f"- {item}" for item in errors[:20])
            if len(errors) > 20:
                details += f"\n- ... y {len(errors) - 20} mas"
            self._show_error("Multiterminal invalido", details)
            return False
        self.status_text.set("Multiterminal validado")
        messagebox.showinfo("Multiterminales", "Perfiles multiterminal validados correctamente.")
        return True

    def _save_multiterminal_clicked(self) -> None:
        try:
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Multiterminales", str(exc))
            return
        self._refresh_multiterminal_tree()
        self.status_text.set("Multiterminales guardados")
        messagebox.showinfo("Multiterminales", "La configuracion multiterminal se guardo correctamente.")

    def _on_multiterminal_changed(self) -> None:
        self._multiterminal_worker_limit()
        self._save_current_multiterminal_editor()
        self._update_multiterminal_summary()
        try:
            self._write_ui_settings()
        except Exception:
            pass


    def _multiterminal_args(self, *, require_ubs: bool = False) -> list[str]:
        if not self.multiterminal_enabled.get():
            return []
        errors = self._validate_multiterminal_errors(require_ubs=require_ubs)
        if errors:
            details = "\n".join(f"- {item}" for item in errors[:12])
            if len(errors) > 12:
                details += f"\n- ... y {len(errors) - 12} mas"
            raise ValueError(f"Configuracion multiterminal invalida:\n{details}")
        self._write_ui_settings()
        return [
            "--multi-terminal",
            "--terminals-config",
            str(UI_SETTINGS_FILE),
            "--max-workers",
            str(self._multiterminal_worker_limit()),
        ]

    def _multiterminal_execution_details(self) -> list[str]:
        if not self.multiterminal_enabled.get():
            return ["Multiterminal: no"]
        active = len(self._active_multiterminal_profiles())
        workers = min(self._multiterminal_worker_limit(), active) if active else 0
        return [
            "Multiterminal: si",
            f"Terminales activas: {active}",
            f"Workers: {workers}",
        ]


