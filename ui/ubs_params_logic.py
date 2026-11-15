from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ubs.params_catalog import (
    UBS_PARAM_DESCRIPTIONS,
    UBS_SECTION_LABELS,
    fmt_num_str,
    parse_set_parameter_text,
)
from ubs.set_rules import normalize_generated_set_text
from ubs_agent import (
    GLOBAL_PARAMS_FILE,
    RULE_LOCKED_KEYS,
    is_agent_mutable_key,
    load_global_params,
    load_mutation_overrides,
    save_global_params,
    save_mutation_overrides,
)
from ubs.set_utils import read_set_with_encoding
from ubs.v6_parameters import UBS_VERSION, V6_DEFAULT_SET_TEXT


PARAM_DIALOG_BG = "#1f2937"


def _ubs_param_info_parts(param: dict) -> list[str]:
    info_parts = []
    if param["range"]:
        info_parts.append(f"Rango: {param['range']}")
    if param["step"]:
        info_parts.append(f"Paso: {fmt_num_str(param['step'])}")
    if param["default"]:
        info_parts.append(f"Default: {param['default']}")
    if param["optimizable"]:
        info_parts.append(f"Optimizable: {param['optimizable']}")
    return info_parts


class UBSParamsLogicMixin:
    def _ubs_params_auto_load(self) -> None:
        """Load the canonical V6 structure overlaid with editable global values."""
        if self.ubs_params_data:
            return
        normalized, _found, _missing = normalize_generated_set_text(V6_DEFAULT_SET_TEXT)
        data = parse_set_parameter_text(normalized)
        global_vals = load_global_params()
        if global_vals:
            for p in data:
                if p["key"] in global_vals and p["key"] not in RULE_LOCKED_KEYS:
                    p["value"] = global_vals[p["key"]]

        self.ubs_params_data = data
        self.ubs_params_current_path = None
        self.ubs_params_modified = False
        self.ubs_params_file_label.set(
            f"{UBS_VERSION} · Global — {GLOBAL_PARAMS_FILE.name}"
            if GLOBAL_PARAMS_FILE.exists()
            else f"{UBS_VERSION} · Global (sin guardar aun)"
        )
        self.ubs_params_desc_var.set("Selecciona un parámetro para ver su descripción")
        self._ubs_params_apply_filter()

    def _ubs_params_load_from_selection(self) -> None:
        pass  # not used — global tab does not load from individual seeds

    def _ubs_params_browse(self) -> None:
        pass  # not used — global tab does not load from individual seeds

    def _ubs_params_load(self, path: Path) -> None:
        pass  # not used — global tab does not load from individual seeds

    def _parse_set_file(self, path: Path) -> list[dict]:
        text, _enc = read_set_with_encoding(path)
        return parse_set_parameter_text(text)

    def _ubs_params_apply_filter(self) -> None:
        if not hasattr(self, "ubs_params_tree"):
            return
        tree = self.ubs_params_tree
        tree.delete(*tree.get_children(""))
        filt = self._ubs_params_filter.get()
        search = self._ubs_params_search.get().strip().lower()
        last_section = None
        iid_counter = 0
        frozen_ov, mutable_ov = load_mutation_overrides()
        global_vals = load_global_params()
        for p in self.ubs_params_data:
            key = p["key"]
            mutable = is_agent_mutable_key(key)
            if filt == "mutable" and not mutable:
                continue
            if filt == "frozen" and mutable:
                continue
            if search and search not in key.lower() and search not in UBS_PARAM_DESCRIPTIONS.get(key, "").lower():
                continue
            # Section header when section changes
            section = p["section"]
            if section != last_section:
                label = UBS_SECTION_LABELS.get(section, section) if section else ""
                if label:
                    tree.insert("", "end", iid=f"__sec_{iid_counter}__", tags=("section",),
                                values=(f"  ━━  {label}", "", "", "", ""))
                    iid_counter += 1
                last_section = section
            desc = UBS_PARAM_DESCRIPTIONS.get(key, "")
            if key in RULE_LOCKED_KEYS:
                tag = "frozen"
                agent_label = "⛓ regla"
                display_value = p["value"]
            elif key in frozen_ov:
                tag = "overridden_frozen"
                agent_label = "✦ fijo global"
                display_value = global_vals.get(key) or frozen_ov.get(key) or p["value"]
            elif key in mutable_ov:
                tag = "overridden_mutable"
                agent_label = "✦ forzado mutable"
                display_value = p["value"]
            else:
                tag = "mutable" if mutable else "frozen"
                agent_label = "✓ mutable" if mutable else "— fijo"
                display_value = p["value"]
            iid = f"{key}_{iid_counter}"
            iid_counter += 1
            tree.insert("", "end", iid=iid, values=(
                key, desc, display_value, p["range"], agent_label,
            ), tags=(tag,))

    def _ubs_params_toggle_mutability(self) -> None:
        selected = self.ubs_params_tree.selection()
        if not selected:
            messagebox.showinfo("UBS Parámetros", "Selecciona un parámetro para cambiar su mutabilidad.")
            return
        row_values = self.ubs_params_tree.item(selected[0], "values")
        if not row_values or str(row_values[1]) == "":
            return
        key = str(row_values[0])
        if key in RULE_LOCKED_KEYS:
            messagebox.showinfo(
                "UBS Parámetros",
                f"'{key}' lo gobierna una regla de generación (ubs/set_rules.py).\n"
                "Su valor no se negocia por parámetro: cámbialo en la regla.",
            )
            return
        frozen_ov, mutable_ov = load_mutation_overrides()
        frozen_ov, mutable_ov = dict(frozen_ov), set(mutable_ov)
        global_val = load_global_params().get(key, str(row_values[2]))

        if key in frozen_ov:
            del frozen_ov[key]
            msg = f"'{key}' restaurado a su estado por defecto."
        elif key in mutable_ov:
            mutable_ov.discard(key)
            msg = f"'{key}' restaurado a su estado por defecto."
        else:
            default_mutable = is_agent_mutable_key(key)
            if default_mutable:
                frozen_ov[key] = ""
                msg = f"'{key}' = {global_val} fijado globalmente. El agente usará este valor en todas las variantes."
            else:
                mutable_ov.add(key)
                msg = f"'{key}' marcado como MUTABLE. El agente podrá mutarlo."

        try:
            save_mutation_overrides(frozen_ov, mutable_ov)
        except Exception as exc:
            self._show_error("Error al guardar overrides", str(exc))
            return
        self._ubs_params_apply_filter()
        messagebox.showinfo("UBS Parámetros", msg)

    def _ubs_params_on_select(self, _event: object = None) -> None:
        selected = self.ubs_params_tree.selection()
        if not selected:
            return
        values = self.ubs_params_tree.item(selected[0], "values")
        if not values or str(values[1]) == "":  # section header
            return
        key = str(values[0])
        desc = UBS_PARAM_DESCRIPTIONS.get(key, "Sin descripción disponible")
        val = str(values[2])
        rng = str(values[3])
        agent = str(values[4])
        extra = f"  |  Rango: {rng}" if rng else ""
        self.ubs_params_desc_var.set(f"{desc}{extra}  |  Valor actual: {val}  |  {agent}")

    @staticmethod
    def _ubs_params_reject_locked_edit(key: str) -> bool:
        if key not in RULE_LOCKED_KEYS:
            return False
        messagebox.showinfo(
            "UBS Parámetros",
            f"'{key}' esta bloqueado por una regla de generacion y no se puede editar aqui.",
        )
        return True

    def _ubs_params_edit_selected(self) -> None:
        selected = self.ubs_params_tree.selection()
        if not selected:
            return
        row_values = self.ubs_params_tree.item(selected[0], "values")
        if not row_values or str(row_values[1]) == "":
            return
        key = str(row_values[0])
        if self._ubs_params_reject_locked_edit(key):
            return
        current_val = str(row_values[2])
        param = next((p for p in self.ubs_params_data if p["key"] == key), None)
        if not param:
            return

        dlg = tk.Toplevel(self)
        dlg.title(f"Editar: {key}")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.configure(bg=PARAM_DIALOG_BG)
        try:
            dlg.iconbitmap(default="")
        except Exception:
            pass

        pad = dict(padx=16, pady=6)
        ttk.Label(dlg, text=key, style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        desc = UBS_PARAM_DESCRIPTIONS.get(key, "")
        if desc:
            ttk.Label(dlg, text=desc, style="Muted.TLabel", wraplength=380).grid(
                row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 8))

        info_parts = _ubs_param_info_parts(param)
        if info_parts:
            ttk.Label(dlg, text="  ".join(info_parts), style="Muted.TLabel").grid(
                row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 10))

        ttk.Label(dlg, text="Valor:", style="Panel.TLabel").grid(row=3, column=0, sticky="w", **pad)
        val_var = tk.StringVar(value=current_val)
        entry = ttk.Entry(dlg, textvariable=val_var, width=22)
        entry.grid(row=3, column=1, sticky="ew", **pad)
        entry.focus_set()
        entry.select_range(0, "end")

        btn_frame = ttk.Frame(dlg, style="Panel.TFrame")
        btn_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(6, 16))
        btn_frame.columnconfigure(0, weight=1)

        def apply() -> None:
            self._ubs_params_apply_edit(param, key, val_var.get().strip(), dlg)

        ttk.Button(btn_frame, text="Cancelar", style="TButton", command=dlg.destroy).grid(row=0, column=0, sticky="e", padx=(0, 8))
        ttk.Button(btn_frame, text="Aplicar", style="Primary.TButton", command=apply).grid(row=0, column=1, sticky="e")
        dlg.bind("<Return>", lambda _e: apply())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # Center
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

    def _ubs_params_apply_edit(self, param: dict, key: str, new_val: str, dlg) -> None:
        if key in RULE_LOCKED_KEYS:
            messagebox.showinfo(
                "UBS Parámetros",
                f"'{key}' esta bloqueado por una regla de generacion.",
            )
            dlg.destroy()
            return
        param["value"] = new_val
        try:
            gp = load_global_params()
            gp[key] = new_val
            save_global_params(gp)
        except Exception:
            pass
        for iid in self.ubs_params_tree.get_children(""):
            row = self.ubs_params_tree.item(iid, "values")
            if row and str(row[0]) == key:
                self.ubs_params_tree.set(iid, "value", new_val)
                break
        self.ubs_params_modified = True
        name = self.ubs_params_current_path.name if self.ubs_params_current_path else "?"
        self.ubs_params_file_label.set(f"{name}  *")
        dlg.destroy()

    def _ubs_params_save(self) -> None:
        if not self.ubs_params_data:
            messagebox.showinfo("UBS Parámetros", "No hay parámetros cargados.")
            return
        try:
            save_global_params({p["key"]: p["value"] for p in self.ubs_params_data})
        except Exception as exc:
            self._show_error("Error al guardar global params", str(exc))
            return
        self.ubs_params_modified = False
        self.ubs_params_file_label.set(f"Global — {GLOBAL_PARAMS_FILE.name}")
        messagebox.showinfo("UBS Parámetros", f"Guardado en {GLOBAL_PARAMS_FILE.name}")

    def _write_set_file(self, path: Path, params: list[dict]) -> None:
        text, encoding = read_set_with_encoding(path)
        lookup = {p["key"]: p["value"] for p in params}
        result_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or "=" not in stripped:
                result_lines.append(line)
                continue
            key, _, rest = stripped.partition("=")
            key = key.strip()
            if key not in lookup or rest.startswith("---") or rest.startswith("===") or (len(rest) > 6 and rest.count("-") > 4):
                result_lines.append(line)
                continue
            parts = rest.split("||")
            parts[0] = lookup[key]
            result_lines.append(f"{key}={'||'.join(parts)}")
        out = "\n".join(result_lines) + "\n"
        path.write_bytes(out.encode(encoding if encoding != "utf-16" else "utf-16"))

    def _ubs_params_restore_defaults(self) -> None:
        if not self.ubs_params_data:
            messagebox.showinfo("UBS Parámetros", "Carga un archivo .set primero.")
            return
        if not messagebox.askyesno("Restaurar defaults", "¿Restaurar todos los valores al default del .set?"):
            return
        for p in self.ubs_params_data:
            if p["default"] and p["key"] not in RULE_LOCKED_KEYS:
                p["value"] = p["default"]
        self._ubs_params_apply_filter()
        self.ubs_params_modified = True
        name = self.ubs_params_current_path.name if self.ubs_params_current_path else "?"
        self.ubs_params_file_label.set(f"{name}  *")


