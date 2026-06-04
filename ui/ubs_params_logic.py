from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ubs.params_catalog import (
    UBS_PARAM_DESCRIPTIONS,
    UBS_SECTION_LABELS,
    fmt_num,
    fmt_num_str,
)
from ubs_agent import (
    GLOBAL_PARAMS_FILE,
    is_agent_mutable_key,
    load_global_params,
    load_mutation_overrides,
    save_global_params,
    save_mutation_overrides,
)
from ubs.set_utils import read_set_with_encoding


PARAM_DIALOG_BG = "#1f2937"


class UBSParamsLogicMixin:
    def _ubs_params_auto_load(self) -> None:
        """Load global params from ubs_global_params.json, bootstrapping from the first seed if needed."""
        if self.ubs_params_data:
            return
        # Try to bootstrap structure from first seed (for key/range info) but values from global file
        try:
            source_dir = self._ubs_generator_source_dir()
            files = sorted(source_dir.rglob("*.set"))
            seed_path = files[0] if files else None
        except Exception:
            seed_path = None

        if seed_path:
            try:
                data = self._parse_set_file(seed_path)
            except Exception:
                data = []
        else:
            data = []

        # Override values with what's stored in the global params file
        global_vals = load_global_params()
        if global_vals:
            for p in data:
                if p["key"] in global_vals:
                    p["value"] = global_vals[p["key"]]
        elif data:
            # First run: persist the seed values as global baseline
            save_global_params({p["key"]: p["value"] for p in data})

        self.ubs_params_data = data
        self.ubs_params_current_path = None
        self.ubs_params_modified = False
        self.ubs_params_file_label.set(
            f"Global — {GLOBAL_PARAMS_FILE.name}" if GLOBAL_PARAMS_FILE.exists() else "Global (sin guardar aún)"
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
        params: list[dict] = []
        current_section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            if "=" not in stripped:
                continue
            key, _, rest = stripped.partition("=")
            key = key.strip()
            # Section header: value is a separator string
            if rest.startswith("---") or rest.startswith("===") or (len(rest) > 6 and rest.count("-") > 4):
                current_section = key
                continue
            parts = rest.split("||")
            value = parts[0] if parts else rest
            default = parts[1].strip() if len(parts) > 1 else ""
            step = parts[2].strip() if len(parts) > 2 else ""
            max_val = parts[3].strip() if len(parts) > 3 else ""
            optimizable = parts[4].strip() if len(parts) > 4 else ""
            # Determine range string
            if step and max_val and step != "0.000000" and max_val != "0.000000":
                try:
                    mn = float(default) if default else 0.0
                    mx = float(max_val)
                    rng = f"{fmt_num(mn)} - {fmt_num(mx)}"
                except ValueError:
                    rng = f"{default} - {max_val}"
            else:
                rng = ""
            params.append({
                "section": current_section,
                "key": key,
                "value": value,
                "default": default,
                "step": step,
                "max": max_val,
                "optimizable": optimizable,
                "range": rng,
            })
        return params

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
            if key in frozen_ov:
                tag = "overridden_frozen"
                agent_label = "✦ fijo global"
                display_value = frozen_ov[key] if frozen_ov[key] else p["value"]
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
                frozen_ov[key] = global_val
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

    def _ubs_params_edit_selected(self) -> None:
        selected = self.ubs_params_tree.selection()
        if not selected:
            return
        row_values = self.ubs_params_tree.item(selected[0], "values")
        if not row_values or str(row_values[1]) == "":
            return
        key = str(row_values[0])
        current_val = str(row_values[2])
        param = next((p for p in self.ubs_params_data if p["key"] == key), None)
        if not param:
            return

        # Edit dialog
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

        info_parts = []
        if param["range"]:
            info_parts.append(f"Rango: {param['range']}")
        if param["step"]:
            info_parts.append(f"Paso: {fmt_num_str(param['step'])}")
        if param["default"]:
            info_parts.append(f"Default: {param['default']}")
        if param["optimizable"]:
            info_parts.append(f"Optimizable: {param['optimizable']}")
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
            new_val = val_var.get().strip()
            param["value"] = new_val
            # Always persist to global params file
            try:
                gp = load_global_params()
                gp[key] = new_val
                save_global_params(gp)
            except Exception:
                pass
            # Update the value column in the tree
            for iid in self.ubs_params_tree.get_children(""):
                row = self.ubs_params_tree.item(iid, "values")
                if row and str(row[0]) == key:
                    self.ubs_params_tree.set(iid, "value", new_val)
                    break
            self.ubs_params_modified = True
            name = self.ubs_params_current_path.name if self.ubs_params_current_path else "?"
            self.ubs_params_file_label.set(f"{name}  *")
            dlg.destroy()

        ttk.Button(btn_frame, text="Cancelar", style="TButton", command=dlg.destroy).grid(row=0, column=0, sticky="e", padx=(0, 8))
        ttk.Button(btn_frame, text="Aplicar", style="Primary.TButton", command=apply).grid(row=0, column=1, sticky="e")
        dlg.bind("<Return>", lambda _e: apply())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # Center
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

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
            if p["default"]:
                p["value"] = p["default"]
        self._ubs_params_apply_filter()
        self.ubs_params_modified = True
        name = self.ubs_params_current_path.name if self.ubs_params_current_path else "?"
        self.ubs_params_file_label.set(f"{name}  *")


