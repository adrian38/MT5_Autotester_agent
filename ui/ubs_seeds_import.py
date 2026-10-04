from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk as _ttk



BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSeedsImportMixin:
    """Importacion de semillas y revision de duplicados."""

    @staticmethod
    def _import_one_ubs_seed(index, source, source_dir, output_dir, without_symbol, errors) -> str:
        """Copia una semilla normalizada o la descarta por duplicada."""
        from run_tests import infer_period_from_set, infer_symbol_from_set, load_set_params
        from ubs.seed_dedup import DUPLICATE_EXACT, SeedFingerprint
        from ubs.set_utils import force_fixed_lot_text, read_set_with_encoding, write_set_text
        from ubs_prepare_sets import unique_target

        try:
            text, encoding = read_set_with_encoding(source)
            normalized, _found, _missing = force_fixed_lot_text(text)

            params = load_set_params(source)
            symbol = infer_symbol_from_set(source, params) or "UNKNOWN"
            period = infer_period_from_set(source, params) or "UNKNOWN"

            fingerprint = SeedFingerprint.from_text(
                source, normalized, symbol, period
            )
            match = index.find_duplicate(fingerprint)
            if match is not None:
                _existing, reason = match
                if reason == DUPLICATE_EXACT:
                    return "exact"
                return "equivalent"

            if symbol == "UNKNOWN" or period == "UNKNOWN":
                # Se importa igualmente: el usuario puede rescatarla
                # con "Guardar Symbol/TF". Hasta entonces la pestana
                # Seeds la marca `invalid_seed` y no se evalua.
                without_symbol.append(source.name)

            relative = source.relative_to(source_dir)
            target = unique_target(output_dir, relative, symbol, period)
            write_set_text(target, normalized, encoding)
            # Reindexar con la ruta final para que el resto del lote
            # compare tambien contra lo recien copiado.
            index.add(
                SeedFingerprint.from_text(target, normalized, symbol, period)
            )
            return "copied"
        except Exception as exc:
            errors.append(f"{source.name}: {exc}")
        return ""

    @staticmethod
    def _run_ubs_seed_import(q, set_files, source_dir: Path, output_dir: Path) -> None:
        """Indexa el destino, copia el lote normalizado y publica el progreso."""
        from run_tests import infer_period_from_set, infer_symbol_from_set, load_set_params
        from ubs.seed_dedup import DUPLICATE_EXACT, SeedDuplicateIndex, SeedFingerprint
        from ubs.set_utils import force_fixed_lot_text, read_set_with_encoding, write_set_text
        from ubs_prepare_sets import unique_target

        index = SeedDuplicateIndex()
        copied = 0
        dup_exact = 0
        dup_equivalent = 0
        without_symbol: list[str] = []
        errors: list[str] = []

        output_dir.mkdir(parents=True, exist_ok=True)

        # Indexar las seeds ya presentes: sin esto el dedup solo compara los
        # ficheros del propio lote y reimporta todo el pool existente.
        existing = sorted(output_dir.rglob("*.set"))
        for idx, current in enumerate(existing):
            q.put(("index", idx, len(existing), current.name))
            try:
                text, _encoding = read_set_with_encoding(current)
                normalized, _found, _missing = force_fixed_lot_text(text)
                params = load_set_params(current)
                index.add(
                    SeedFingerprint.from_text(
                        current,
                        normalized,
                        infer_symbol_from_set(current, params) or "UNKNOWN",
                        infer_period_from_set(current, params) or "UNKNOWN",
                    )
                )
            except Exception as exc:
                errors.append(f"[indexando] {current.name}: {exc}")

        for idx, source in enumerate(set_files):
            q.put(("progress", idx, total, source.name))
            outcome = UBSSeedsImportMixin._import_one_ubs_seed(
                index, source, source_dir, output_dir, without_symbol, errors,
            )
            if outcome == "copied":
                copied += 1
            elif outcome == "exact":
                dup_exact += 1
            elif outcome == "equivalent":
                dup_equivalent += 1
        q.put(("done", copied, dup_exact, dup_equivalent, without_symbol, errors))

    def _report_ubs_seed_import(
        self,
        output_dir: Path,
        copied: int,
        dup_exact: int,
        dup_equivalent: int,
        without_symbol: list[str],
        errors: list[str],
    ) -> None:
        """Resumen de la importacion y refresco del panel."""
        summary = (
            f"Importación completada\n\n"
            f"  Seeds copiadas:    {copied}\n"
            f"  Duplicados idénticos omitidos:   {dup_exact}\n"
            f"  Duplicados equivalentes omitidos: {dup_equivalent}\n"
            f"    (mismo símbolo/TF e idénticos en todas las claves\n"
            f"     comunes; solo difieren en parámetros nuevos del EA)\n"
            f"  Destino: {output_dir}"
        )
        if without_symbol:
            shown = "\n    ".join(without_symbol[:5])
            summary += (
                f"\n\n  ⚠ {len(without_symbol)} sin símbolo/TF resoluble → carpeta UNKNOWN\\.\n"
                f"    Quedan como 'invalid_seed' y no se evalúan hasta que les\n"
                f"    asignes símbolo con «Guardar Symbol/TF».\n"
                f"    {shown}"
            )
            if len(without_symbol) > 5:
                summary += f"\n    ... y {len(without_symbol) - 5} más"
        if errors:
            summary += f"\n\n  Errores: {len(errors)}\n  " + "\n  ".join(errors[:5])
        messagebox.showinfo("Importar seeds — completado", summary)
        self._refresh_ubs_seeds_panel()

    def _import_ubs_seeds(self) -> None:
        """Importa una carpeta de .set, normaliza lote fijo, detecta duplicados y muestra progreso."""
        source_str = filedialog.askdirectory(title="Carpeta origen con los .set a importar")
        if not source_str:
            return

        source_dir = Path(source_str)
        try:
            output_dir = self._ubs_generator_source_dir()
        except Exception:
            output_dir = self._ubs_default_source_dir()

        set_files = sorted(source_dir.rglob("*.set"))
        total = len(set_files)
        if total == 0:
            messagebox.showinfo("Importar seeds", f"No se encontraron archivos .set en:\n{source_dir}")
            return

        if not messagebox.askyesno(
            "Importar seeds",
            f"Importar {total} .set desde:\n{source_dir}\n\n"
            f"Destino: {output_dir}\n\n"
            "Se normalizará el lotaje (lote fijo 0.01) y se omitirán las seeds\n"
            "que ya existan en el destino, tanto idénticas como equivalentes\n"
            "(mismo símbolo/TF que solo difieren en parámetros nuevos del EA).\n\n"
            "¿Continuar?",
        ):
            return

        q: queue.Queue = queue.Queue()

        def _finish(payload) -> None:
            self._report_ubs_seed_import(output_dir, *payload)

        dlg, poll = self._ubs_seed_progress_dialog(
            "Importando seeds...",
            "Importando y normalizando seeds",
            str(source_dir),
            total,
            q,
            _finish,
        )
        threading.Thread(
            target=lambda: self._run_ubs_seed_import(q, set_files, source_dir, output_dir),
            daemon=True,
        ).start()
        dlg.after(40, poll)

    def _ubs_seed_progress_widgets(self, title: str, heading: str, subtitle: str, total: int):
        """Construye el popup modal y devuelve sus controles de progreso."""
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        dlg.configure(bg=self.colors["panel"])
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)

        body = tk.Frame(dlg, bg=self.colors["panel"], padx=28, pady=22)
        body.pack()
        tk.Label(body, text=heading,
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(body, text=subtitle,
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=440).pack(anchor="w", pady=(2, 16))

        bar = _ttk.Progressbar(body, mode="determinate", maximum=100,
                               style="Horizontal.TProgressbar", length=440)
        bar.pack(fill="x")
        count_var = tk.StringVar(value=f"0 / {total}")
        status_var = tk.StringVar(value="Iniciando...")
        tk.Label(body, textvariable=count_var,
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(anchor="e", pady=(4, 0))
        tk.Label(body, textvariable=status_var,
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=440, anchor="w").pack(fill="x", pady=(3, 0))

        dlg.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dlg.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dlg.winfo_height()) // 2)
        dlg.geometry(f"+{x}+{y}")
        return dlg, bar, count_var, status_var

    def _ubs_seed_progress_dialog(
        self,
        title: str,
        heading: str,
        subtitle: str,
        total: int,
        q: "queue.Queue",
        on_done,
    ):
        """Popup modal de progreso + su `poll`. Devuelve `(dialogo, poll)`.

        El worker publica en `q`: `("progress"|"index", idx, total, nombre)`,
        `("done", *payload)` o `("failed", mensaje)`. Al terminar cierra el
        dialogo y llama a `on_done(payload)`.
        """
        dlg, bar, count_var, status_var = self._ubs_seed_progress_widgets(
            title, heading, subtitle, total,
        )

        def _close() -> None:
            dlg.grab_release()
            dlg.destroy()

        def _poll() -> None:
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] in ("index", "progress"):
                        _, idx, tot, name = msg
                        bar["value"] = int((idx + 1) / max(tot, 1) * 100)
                        count_var.set(f"{idx + 1} / {tot}")
                        label = name[:55] + "..." if len(name) > 55 else name
                        prefix = "Indexando seeds existentes" if msg[0] == "index" else "Procesando"
                        status_var.set(f"{prefix}: {label}")
                    elif msg[0] == "failed":
                        _close()
                        self._show_error(title, msg[1])
                        return
                    elif msg[0] == "done":
                        payload = tuple(msg[1:])
                        bar["value"] = 100
                        status_var.set("Completado.")

                        def _finish_now() -> None:
                            _close()
                            on_done(payload)

                        dlg.after(400, _finish_now)
                        return
            except queue.Empty:
                pass
            dlg.after(40, _poll)

        return dlg, _poll
