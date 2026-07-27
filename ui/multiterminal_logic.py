from __future__ import annotations

import configparser
from dataclasses import dataclass
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from run_tests import find_matching_running_terminals, looks_like_ubs_expert_file
from ubs.account import DEFAULT_BROKER, normalize_broker


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
UI_SETTINGS_FILE = BASE_DIR / "ui_settings.ini"
TESTER_ROOT_TEMP_SUFFIXES = {".gif", ".htm", ".html", ".png", ".set", ".xml"}


@dataclass(frozen=True)
class TesterCleanupPlan:
    data_dirs: tuple[Path, ...]
    files: tuple[Path, ...]
    removable_dirs: tuple[Path, ...]
    total_bytes: int


def _normalized_path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def build_tester_cleanup_plan(data_dirs: list[Path]) -> TesterCleanupPlan:
    """Collect only disposable artifacts created by MT5 or this runner."""
    unique_data_dirs: list[Path] = []
    seen_data_dirs: set[str] = set()
    for raw_data_dir in data_dirs:
        data_dir = Path(raw_data_dir).expanduser().resolve(strict=False)
        key = _normalized_path_key(data_dir)
        if key in seen_data_dirs:
            continue
        seen_data_dirs.add(key)
        unique_data_dirs.append(data_dir)

    files: dict[str, Path] = {}
    removable_dirs: dict[str, Path] = {}

    def add_file(path: Path, allowed_root: Path) -> None:
        if not _path_within(path, allowed_root) or not path.is_file():
            return
        files.setdefault(_normalized_path_key(path), path)

    for data_dir in unique_data_dirs:
        tester_dir = data_dir / "Tester"
        if not tester_dir.is_dir():
            continue

        root_set_names: set[str] = set()
        try:
            root_entries = list(tester_dir.iterdir())
        except OSError:
            root_entries = []
        for path in root_entries:
            if not path.is_file() or path.suffix.lower() not in TESTER_ROOT_TEMP_SUFFIXES:
                continue
            add_file(path, tester_dir)
            if path.suffix.lower() == ".set":
                root_set_names.add(path.name.casefold())

        for folder_name in ("cache", "logs"):
            disposable_dir = tester_dir / folder_name
            if not disposable_dir.is_dir() or not _path_within(disposable_dir, tester_dir):
                continue
            for walk_root, dir_names, file_names in os.walk(disposable_dir, followlinks=False):
                walk_path = Path(walk_root)
                for file_name in file_names:
                    add_file(walk_path / file_name, disposable_dir)
                for dir_name in dir_names:
                    candidate = walk_path / dir_name
                    if not candidate.is_symlink() and _path_within(candidate, disposable_dir):
                        removable_dirs.setdefault(_normalized_path_key(candidate), candidate)
            removable_dirs.setdefault(_normalized_path_key(disposable_dir), disposable_dir)

        profiles_dir = data_dir / "MQL5" / "Profiles" / "Tester"
        if root_set_names and profiles_dir.is_dir():
            try:
                profile_entries = list(profiles_dir.iterdir())
            except OSError:
                profile_entries = []
            for path in profile_entries:
                if path.is_file() and path.suffix.lower() == ".set" and path.name.casefold() in root_set_names:
                    add_file(path, profiles_dir)

    total_bytes = 0
    for path in files.values():
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
    dirs_deepest_first = sorted(
        removable_dirs.values(),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    return TesterCleanupPlan(
        data_dirs=tuple(unique_data_dirs),
        files=tuple(files.values()),
        removable_dirs=tuple(dirs_deepest_first),
        total_bytes=total_bytes,
    )


def execute_tester_cleanup(plan: TesterCleanupPlan) -> tuple[int, int, list[str]]:
    deleted_files = 0
    freed_bytes = 0
    failures: list[str] = []
    for path in plan.files:
        try:
            size = path.stat().st_size
            path.unlink()
            deleted_files += 1
            freed_bytes += size
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    for path in plan.removable_dirs:
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            # It may contain a protected junction/symlink or a file that could
            # not be removed. Leaving the directory is the safe outcome.
            continue
    return deleted_files, freed_bytes, failures


def _format_cleanup_bytes(value: int) -> str:
    amount = float(max(value, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TB"


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
                "broker": normalize_broker(data.get("broker", DEFAULT_BROKER)),
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
            "broker": self._active_multiterminal_broker(),
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
        worker_limit = self._multiterminal_worker_limit()
        if worker_limit > 1:
            return self._broker_multiterminal_profiles()
        return self._broker_multiterminal_profiles(include_disabled=False)

    def _selected_multiterminal_profile_items(self) -> list[tuple[int, dict[str, object]]]:
        worker_limit = self._multiterminal_worker_limit()
        if worker_limit > 1:
            items = self._broker_multiterminal_profile_items()
        else:
            items = self._broker_multiterminal_profile_items(include_disabled=False)
        limit = max(1, min(worker_limit, len(items))) if items else 0
        return items[:limit]

    def _active_multiterminal_broker(self) -> str:
        broker_var = getattr(self, "ubs_broker", None)
        return normalize_broker(broker_var.get() if broker_var is not None else DEFAULT_BROKER)

    def _profile_broker(self, profile: dict[str, object]) -> str:
        return normalize_broker(profile.get("broker", DEFAULT_BROKER))

    def _broker_multiterminal_profiles(self, *, include_disabled: bool = True) -> list[dict[str, object]]:
        broker = self._active_multiterminal_broker()
        profiles = [
            profile
            for profile in self.multiterminal_profiles
            if self._profile_broker(profile) == broker
        ]
        if include_disabled:
            return profiles
        return [profile for profile in profiles if bool(profile.get("enabled"))]

    def _broker_multiterminal_profile_items(self, *, include_disabled: bool = True) -> list[tuple[int, dict[str, object]]]:
        broker = self._active_multiterminal_broker()
        items = [
            (index, profile)
            for index, profile in enumerate(self.multiterminal_profiles)
            if self._profile_broker(profile) == broker
        ]
        if include_disabled:
            return items
        return [(index, profile) for index, profile in items if bool(profile.get("enabled"))]

    def _update_multiterminal_summary(self) -> None:
        if not hasattr(self, "multiterminal_summary"):
            return
        worker_limit = self._multiterminal_worker_limit()
        available = len(self._active_multiterminal_profiles())
        workers = min(worker_limit, available) if available else 0
        mode = "on" if self.multiterminal_enabled.get() else "off"
        self.multiterminal_summary.set(
            f"{self._active_multiterminal_broker()}: {available} perfiles / usando hasta {workers} / {mode}"
        )

    def _save_current_multiterminal_editor(self) -> None:
        if not hasattr(self, "mt_profile_name"):
            return
        index = self.mt_selected_index
        if index is None or index < 0 or index >= len(self.multiterminal_profiles):
            return
        self.multiterminal_profiles[index] = {
            "enabled": bool(self.mt_profile_enabled.get()),
            "broker": normalize_broker(
                self.mt_profile_broker.get() if hasattr(self, "mt_profile_broker") else self._active_multiterminal_broker()
            ),
            "name": self.mt_profile_name.get().strip() or f"Terminal {index + 1}",
            "mt5_path": self.mt_profile_mt5_path.get().strip(),
            "data_dir": self.mt_profile_data_dir.get().strip(),
            "experts_root": self.mt_profile_experts_root.get().strip(),
            "ubs_ex5_file": self.mt_profile_ubs_ex5_file.get().strip(),
            "portable": bool(self.mt_profile_portable.get()),
        }

    def _enforce_single_primary_multiterminal_profile(self) -> None:
        idx = self.mt_selected_index
        if idx is None or idx < 0 or idx >= len(self.multiterminal_profiles):
            return
        if not bool(self.multiterminal_profiles[idx].get("enabled")):
            return
        selected_broker = self._profile_broker(self.multiterminal_profiles[idx])
        for i, profile in enumerate(self.multiterminal_profiles):
            if i != idx and self._profile_broker(profile) == selected_broker:
                profile["enabled"] = False

    def _multiterminal_tree_values(self, profile: dict[str, object], index: int) -> tuple:
        name = str(profile.get("name") or f"Terminal {index + 1}")
        return (
            self._checkbox_text(name in self.multiterminal_checked),
            "si" if bool(profile.get("enabled")) else "no",
            self._profile_broker(profile),
            name,
            str(profile.get("mt5_path") or ""),
            str(profile.get("data_dir") or ""),
            str(profile.get("experts_root") or ""),
            str(profile.get("ubs_ex5_file") or ""),
        )

    def _refresh_multiterminal_tree(self) -> None:
        if not hasattr(self, "multiterminal_tree"):
            self._update_multiterminal_summary()
            return
        selected_index = self.mt_selected_index
        for item in self.multiterminal_tree.get_children():
            self.multiterminal_tree.delete(item)
        valid_names = set()
        visible_items = self._broker_multiterminal_profile_items()
        for index, profile in visible_items:
            name = str(profile.get("name") or f"Terminal {index + 1}")
            valid_names.add(name)
            tag = "odd" if index % 2 else "even"
            self.multiterminal_tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._multiterminal_tree_values(profile, index),
                tags=(tag,),
            )
        self.multiterminal_checked.intersection_update(valid_names)
        visible_indexes = {index for index, _profile in visible_items}
        if selected_index is not None and selected_index in visible_indexes:
            self.multiterminal_tree.selection_set(str(selected_index))
            self.multiterminal_tree.focus(str(selected_index))
        elif visible_items:
            first_index = visible_items[0][0]
            self.multiterminal_tree.selection_set(str(first_index))
            self.multiterminal_tree.focus(str(first_index))
            self._load_multiterminal_profile_editor(first_index)
        else:
            self._load_multiterminal_profile_editor(-1)
        self._update_multiterminal_summary()

    def _on_multiterminal_tree_click(self, event) -> None:
        if not hasattr(self, "multiterminal_tree"):
            return
        item, column = self._tree_item_from_event(self.multiterminal_tree, event)
        if not item or column != "#1":
            return
        try:
            index = int(item)
        except ValueError:
            return
        if index < 0 or index >= len(self.multiterminal_profiles):
            return
        name = str(self.multiterminal_profiles[index].get("name") or f"Terminal {index + 1}")

        # Radio visual en SEL: desmarcar todos, marcar este
        was_checked = name in self.multiterminal_checked
        self.multiterminal_checked.clear()
        for other in self.multiterminal_tree.get_children():
            v = list(self.multiterminal_tree.item(other, "values"))
            if v:
                v[0] = self._checkbox_text(False)
                self.multiterminal_tree.item(other, values=v)

        if not was_checked:
            self.multiterminal_checked.add(name)
        v = list(self.multiterminal_tree.item(item, "values"))
        if v:
            v[0] = self._checkbox_text(name in self.multiterminal_checked)
            self.multiterminal_tree.item(item, values=v)

        # Cargar datos en el editor
        self._select_multiterminal_profile(index)
        return "break"

    def _update_multiterminal_tree_item(self, index: int) -> None:
        if not hasattr(self, "multiterminal_tree"):
            return
        if index < 0 or index >= len(self.multiterminal_profiles):
            return
        iid = str(index)
        if self.multiterminal_tree.exists(iid):
            self.multiterminal_tree.item(iid, values=self._multiterminal_tree_values(self.multiterminal_profiles[index], index))

    def _load_multiterminal_profile_editor(self, index: int) -> None:
        if index < 0 or index >= len(self.multiterminal_profiles):
            self.mt_selected_index = None
            self.mt_profile_enabled.set(False)
            self.mt_profile_portable.set(False)
            if hasattr(self, "mt_profile_broker"):
                self.mt_profile_broker.set(self._active_multiterminal_broker())
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
        if hasattr(self, "mt_profile_broker"):
            self.mt_profile_broker.set(self._profile_broker(profile))
        self.mt_profile_name.set(str(profile.get("name") or f"Terminal {index + 1}"))
        self.mt_profile_mt5_path.set(str(profile.get("mt5_path") or ""))
        self.mt_profile_data_dir.set(str(profile.get("data_dir") or ""))
        self.mt_profile_experts_root.set(str(profile.get("experts_root") or ""))
        self.mt_profile_ubs_ex5_file.set(str(profile.get("ubs_ex5_file") or ""))

    def _select_multiterminal_profile(self, index: int) -> None:
        self._load_multiterminal_profile_editor(index)
        if not hasattr(self, "multiterminal_tree") or index < 0 or index >= len(self.multiterminal_profiles):
            return
        iid = str(index)
        if self.multiterminal_tree.exists(iid):
            self.multiterminal_tree.selection_set(iid)
            self.multiterminal_tree.focus(iid)

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
        idx = self.mt_selected_index
        self._enforce_single_primary_multiterminal_profile()
        if idx is not None:
            self._update_multiterminal_tree_item(idx)
        self._refresh_multiterminal_tree()
        if idx is not None:
            self._select_multiterminal_profile(idx)
        self._update_multiterminal_summary()
        self.status_text.set("Fila multiterminal aplicada")

    def _new_multiterminal_profile(self, name: str | None = None) -> dict[str, object]:
        index = len(self.multiterminal_profiles) + 1
        return {
            "enabled": True,
            "broker": self._active_multiterminal_broker(),
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
        self._enforce_single_primary_multiterminal_profile()
        errors: list[str] = []
        selected = self._selected_multiterminal_profile_items()
        if not selected:
            errors.append(f"No hay terminales habilitadas para {self._active_multiterminal_broker()}.")
        for index, profile in selected:
            name = str(profile.get("name") or f"Terminal {index + 1}")
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
            self._save_current_multiterminal_editor()
            self._enforce_single_primary_multiterminal_profile()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudo guardar Multiterminales", str(exc))
            return
        self._refresh_multiterminal_tree()
        self.status_text.set("Multiterminales guardados")
        messagebox.showinfo("Multiterminales", "La configuracion multiterminal se guardo correctamente.")

    def _clean_multiterminal_tester_files(self) -> None:
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            messagebox.showwarning("Proceso activo", "Deten el proceso activo antes de limpiar las carpetas Tester.")
            return
        self._save_current_multiterminal_editor()

        running_lines: list[str] = []
        data_dirs: list[Path] = []
        invalid_profiles: list[str] = []
        seen_running: set[tuple[int, str]] = set()
        for index, profile in enumerate(self._broker_multiterminal_profiles(), start=1):
            name = str(profile.get("name") or f"Terminal {index}")
            data_dir = self._profile_path(profile, "data_dir")
            if data_dir is None or not data_dir.is_dir():
                invalid_profiles.append(name)
                continue
            data_dirs.append(data_dir)
            mt5_path = self._profile_path(profile, "mt5_path")
            if mt5_path is None:
                continue
            for running in find_matching_running_terminals(mt5_path):
                running_key = (int(running["pid"]), str(running["path"]))
                if running_key in seen_running:
                    continue
                seen_running.add(running_key)
                running_lines.append(f"{name} | PID {running['pid']}: {running['path']}")
        if running_lines:
            messagebox.showerror(
                "MT5 abierto",
                "Cierra las terminales MT5 antes de limpiar Tester.\n\n" + "\n".join(running_lines),
            )
            return
        if not data_dirs:
            messagebox.showinfo("Limpiar Tester", "No hay carpetas de datos MT5 validas en los perfiles guardados.")
            return

        self.status_text.set("Analizando basura de Tester...")
        button = getattr(self, "multiterminal_cleanup_button", None)
        if button is not None:
            button.configure(state="disabled")

        def scan_worker() -> None:
            try:
                plan = build_tester_cleanup_plan(data_dirs)
            except OSError as exc:
                self.after(0, lambda: self._fail_multiterminal_tester_scan(str(exc)))
                return
            self.after(
                0,
                lambda: self._confirm_multiterminal_tester_cleanup(plan, invalid_profiles),
            )

        threading.Thread(target=scan_worker, daemon=True).start()

    def _fail_multiterminal_tester_scan(self, error: str) -> None:
        button = getattr(self, "multiterminal_cleanup_button", None)
        if button is not None:
            button.configure(state="normal")
        self.status_text.set("No se pudo analizar Tester")
        self._show_error("No se pudo analizar Tester", error)

    def _confirm_multiterminal_tester_cleanup(
        self,
        plan: TesterCleanupPlan,
        invalid_profiles: list[str],
    ) -> None:
        button = getattr(self, "multiterminal_cleanup_button", None)
        invalid_note = (
            "\n\nPerfiles omitidos por carpeta de datos invalida: " + ", ".join(invalid_profiles)
            if invalid_profiles else ""
        )
        if not plan.files:
            if button is not None:
                button.configure(state="normal")
            self.status_text.set("Tester limpio: no hay archivos temporales")
            messagebox.showinfo(
                "Limpiar Tester",
                f"No se encontraron archivos temporales en {len(plan.data_dirs)} terminal(es)." + invalid_note,
            )
            return
        if not messagebox.askyesno(
            "Limpiar carpetas Tester",
            f"Se borraran {len(plan.files):,} archivos temporales "
            f"({_format_cleanup_bytes(plan.total_bytes)}) de {len(plan.data_dirs)} terminal(es).\n\n"
            "Incluye:\n"
            "- .set/reportes temporales en Tester\n"
            "- contenido de Tester\\cache y Tester\\logs\n"
            "- copias .set coincidentes en MQL5\\Profiles\\Tester\n\n"
            "No se borraran bases, historial ni presets sin copia temporal." + invalid_note + "\n\nContinuar?",
        ):
            if button is not None:
                button.configure(state="normal")
            self.status_text.set("Limpieza de Tester cancelada")
            return

        self.status_text.set("Limpiando carpetas Tester...")

        def worker() -> None:
            result = execute_tester_cleanup(plan)
            self.after(0, lambda: self._finish_multiterminal_tester_cleanup(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_multiterminal_tester_cleanup(self, result: tuple[int, int, list[str]]) -> None:
        deleted_files, freed_bytes, failures = result
        button = getattr(self, "multiterminal_cleanup_button", None)
        if button is not None:
            button.configure(state="normal")
        summary = f"Tester limpiado: {deleted_files:,} archivos | {_format_cleanup_bytes(freed_bytes)} liberados"
        self.status_text.set(summary)
        if hasattr(self, "_append_console"):
            self._append_console(f"\n[Limpiar Tester] {summary}\n", tag="warn" if failures else "info")
            for failure in failures[:20]:
                self._append_console(f"  No borrado: {failure}\n", tag="warn")
        if failures:
            messagebox.showwarning(
                "Limpieza Tester incompleta",
                summary + f"\n\nNo se pudieron borrar {len(failures)} archivo(s). Revisa el log.",
            )
        else:
            messagebox.showinfo("Limpieza Tester completada", summary)

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
        worker_limit = self._multiterminal_worker_limit()
        available = len(self._active_multiterminal_profiles())
        workers = min(worker_limit, available) if available else 0
        return [
            "Multiterminal: si",
            f"Broker terminales: {self._active_multiterminal_broker()}",
            f"Terminales disponibles: {available}",
            f"Workers: {workers}",
        ]
