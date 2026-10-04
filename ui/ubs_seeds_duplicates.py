from __future__ import annotations

import queue
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk as _ttk

from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSeedsDuplicatesMixin:
    """Revision, listado y retirada de semillas duplicadas del pool."""

    def _ubs_seed_score_index(self) -> dict[str, float | None]:
        """`seed_path` normalizado -> score, para priorizar que seed se conserva."""
        scores: dict[str, float | None] = {}
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return scores
        try:
            conn = connect_memory(memory_path)
            try:
                rows = conn.execute("SELECT seed_path, score FROM seed_scores").fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return scores
        for seed_path, score in rows:
            if seed_path:
                scores[str(seed_path).strip().lower()] = score
        return scores

    @staticmethod
    def _scan_ubs_seed_duplicates(q, set_files, scores: dict) -> None:
        """Calcula las huellas del pool y agrupa las semillas duplicadas."""
        from run_tests import infer_period_from_set, infer_symbol_from_set, load_set_params
        from ubs.seed_dedup import SeedFingerprint, scan_duplicates
        from ubs.set_utils import force_fixed_lot_text, read_set_with_encoding

        fingerprints = []
        errors: list[str] = []
        total = len(set_files)
        for idx, path in enumerate(set_files):
            q.put(("progress", idx, total, path.name))
            try:
                text, _encoding = read_set_with_encoding(path)
                normalized, _found, _missing = force_fixed_lot_text(text)
                params = load_set_params(path)
                fingerprints.append(
                    SeedFingerprint.from_text(
                        path,
                        normalized,
                        infer_symbol_from_set(path, params) or "UNKNOWN",
                        infer_period_from_set(path, params) or "UNKNOWN",
                    )
                )
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        def _priority(fingerprint) -> tuple:
            # Conserva la seed ya evaluada; luego la del esquema mas
            # completo; luego la ruta mas corta (desempate determinista).
            score = scores.get(str(fingerprint.path).strip().lower())
            return (
                0 if score is not None else 1,
                -len(fingerprint.params),
                len(str(fingerprint.path)),
            )

        try:
            groups = scan_duplicates(fingerprints, priority=_priority)
        except Exception as exc:
            q.put(("failed", str(exc)))
            return
        q.put(("done", groups, errors))

    def _review_ubs_seed_duplicates(self) -> None:
        """Audita el pool de seeds y ofrece retirar las duplicadas."""
        try:
            seeds_dir = self._ubs_generator_source_dir()
        except Exception:
            seeds_dir = self._ubs_default_source_dir()
        if not seeds_dir.exists():
            self._show_error("Revisar duplicados", f"No existe la carpeta de seeds:\n{seeds_dir}")
            return

        set_files = sorted(seeds_dir.rglob("*.set"))
        if not set_files:
            messagebox.showinfo("Revisar duplicados", f"No hay seeds en:\n{seeds_dir}")
            return

        scores = self._ubs_seed_score_index()
        q: queue.Queue = queue.Queue()

        dlg, poll = self._ubs_seed_progress_dialog(
            "Revisando duplicados...",
            "Analizando el pool de seeds",
            str(seeds_dir),
            len(set_files),
            q,
            lambda payload: self._show_ubs_seed_duplicates(seeds_dir, scores, *payload),
        )
        threading.Thread(
            target=lambda: self._scan_ubs_seed_duplicates(q, set_files, scores),
            daemon=True,
        ).start()
        dlg.after(40, poll)

    def _show_ubs_seed_duplicates(self, seeds_dir: Path, scores: dict, groups: list, errors: list[str]) -> None:
        redundant_total = sum(len(group.redundant) for group in groups)
        if not groups:
            messagebox.showinfo(
                "Revisar duplicados",
                f"Sin duplicados.\n\n{len(list(seeds_dir.rglob('*.set')))} seeds revisadas en:\n{seeds_dir}"
                + (f"\n\nErrores de lectura: {len(errors)}" if errors else ""),
            )
            return

        win = tk.Toplevel(self)
        win.title("Duplicados en el pool de seeds")
        win.transient(self)
        win.configure(bg=self.colors["panel"])
        win.geometry("1180x560")
        win.minsize(900, 480)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        header = tk.Frame(win, bg=self.colors["panel"], padx=16, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text=f"{redundant_total} seeds redundantes en {len(groups)} grupos",
            bg=self.colors["panel"], fg=self.colors["text"],
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Una seed es duplicada si tiene el mismo símbolo/TF y valores idénticos en todas "
                 "las claves que comparte con otra (≥100 comunes). Se conserva la evaluada y, a "
                 "igualdad, la del esquema más completo.",
            bg=self.colors["panel"], fg=self.colors["muted"],
            font=("Segoe UI", 9), wraplength=1120, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        tree = self._ubs_duplicate_tree(win)
        to_retire = self._fill_ubs_duplicate_rows(tree, groups, scores, seeds_dir)
        self._ubs_duplicate_footer(win, to_retire, scores, errors)

    def _ubs_duplicate_tree(self, win):
        """Tabla de semillas redundantes y la que se conserva."""
        table_frame = tk.Frame(win, bg=self.colors["panel"])
        table_frame.grid(row=1, column=0, sticky="nsew", padx=16)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("retirar", "motivo", "score", "conservar")
        tree = _ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="none",
            height=14,
        )
        for key, title, width in (
            ("retirar", "Se retira", 430),
            ("motivo", "Motivo", 110),
            ("score", "Score", 80),
            ("conservar", "Se conserva", 430),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=42, anchor="w", stretch=False)
        tree.tag_configure("accepted", foreground=self.colors["accent_soft_text"])
        tree.tag_configure("rejected", foreground=self.colors["danger"])
        tree.tag_configure("pending", foreground=self.colors["muted"])
        self._make_tree_sortable(tree)
        self._attach_tree_scrollbars(
            table_frame,
            tree,
            0,
            vertical=True,
            horizontal=True,
        )
        return tree

    @staticmethod
    def _fill_ubs_duplicate_rows(tree, groups, scores: dict, seeds_dir: Path) -> list[Path]:
        to_retire: list[Path] = []
        for group in sorted(groups, key=lambda g: str(g.keeper.path)):
            keeper_rel = group.keeper.path.relative_to(seeds_dir)
            for fingerprint, reason in group.redundant:
                score = scores.get(str(fingerprint.path).strip().lower())
                tree.insert("", "end", values=(
                    str(fingerprint.path.relative_to(seeds_dir)),
                    "idéntica" if reason == "exact" else "equivalente",
                    "-" if score is None else f"{score:.2f}",
                    str(keeper_rel),
                ))
                to_retire.append(fingerprint.path)
        return to_retire

    def _ubs_duplicate_footer(self, win, to_retire: list[Path], scores: dict, errors: list[str]) -> None:
        """Resumen y botones de la ventana de duplicados."""
        footer = tk.Frame(win, bg=self.colors["panel_alt"], padx=16, pady=8)
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        evaluated = sum(1 for p in to_retire if scores.get(str(p).strip().lower()) is not None)
        tk.Label(
            footer,
            text=f"{evaluated} de las {len(to_retire)} redundantes ya están evaluadas: al "
                 "retirarlas se borran sus filas de seed_scores/seed_overrides y se recalculan "
                 "los pesos del Universo.",
            bg=self.colors["panel_alt"], fg=self.colors["muted"],
            font=("Segoe UI", 9), wraplength=680, justify="left",
        ).grid(row=0, column=0, sticky="w", padx=(10, 16), pady=6)
        tk.Button(
            footer, text="Cerrar", bg=self.colors["panel"], fg=self.colors["muted"],
            relief="solid", borderwidth=1, padx=10, pady=5,
            font=("Segoe UI", 9), cursor="hand2", command=win.destroy,
        ).grid(row=0, column=2, padx=(6, 10), pady=6)
        tk.Button(
            footer, text=f"Retirar redundantes ({len(to_retire)})",
            bg=self.colors["danger"], fg="#ffffff", relief="flat", borderwidth=0,
            padx=12, pady=5, font=("Segoe UI", 9, "bold"), cursor="hand2",
            command=lambda: self._retire_ubs_seed_duplicates(win, seeds_dir, groups),
        ).grid(row=0, column=1, pady=6)

        if errors:
            tk.Label(
                footer, text=f"{len(errors)} ficheros ilegibles omitidos",
                bg=self.colors["panel_alt"], fg=self.colors["danger"],
                font=("Segoe UI", 9),
            ).grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))

    def _move_retired_ubs_seeds(self, pairs, seeds_dir: Path, retired_dir: Path, stamp: str):
        """Mueve las duplicadas fuera del pool y deja el motivo por escrito."""
        import shutil

        moved: list[str] = []
        errors: list[str] = []
        log_lines = [
            f"# Seeds duplicadas retiradas del pool el {stamp}",
            f"# origen: {seeds_dir}",
            "# formato: <retirada>\t<motivo>\t<seed conservada>",
            "",
        ]
        for source, keeper, reason in pairs:
            try:
                relative = source.relative_to(seeds_dir)
                target = retired_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved.append(str(source))
                self.ubs_seed_checked.discard(str(source))
                log_lines.append(f"{relative}\t{reason}\t{keeper.relative_to(seeds_dir)}")
            except Exception as exc:
                errors.append(f"{source.name}: {exc}")

        (retired_dir / "_motivo.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        return moved, errors

    @staticmethod
    def _prune_empty_ubs_seed_dirs(seeds_dir: Path) -> None:
        # Limpiar los directorios que hayan quedado vacios.
        for path in sorted(seeds_dir.rglob("*"), key=lambda p: -len(p.parts)):
            if path.is_dir() and not any(path.iterdir()):
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _retire_ubs_seed_duplicates(self, window: tk.Toplevel, seeds_dir: Path, groups: list) -> None:
        import shutil

        from ubs.account import account_retired_seeds_dir

        pairs = [
            (fingerprint.path, group.keeper.path, reason)
            for group in groups
            for fingerprint, reason in group.redundant
        ]
        if not pairs:
            return
        if not messagebox.askyesno(
            "Retirar duplicadas",
            f"Se moverán {len(pairs)} seeds fuera del pool y se borrarán sus registros\n"
            f"de seed_scores / seed_overrides. Los pesos del Universo se recalcularán.\n\n"
            f"Los ficheros NO se borran: quedan en outputs/seeds_retiradas/.\n\n¿Continuar?",
        ):
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        retired_dir = account_retired_seeds_dir(
            BASE_DIR, self._ubs_account_type(), self._ubs_broker()
        ) / stamp
        retired_dir.mkdir(parents=True, exist_ok=True)

        moved, errors = self._move_retired_ubs_seeds(pairs, seeds_dir, retired_dir, stamp)

        memory_path = self._ubs_memory_path()
        if moved and memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                try:
                    self._cleanup_seed_db(conn, moved)  # ya hace commit
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                errors.append(f"memoria: {exc}")

        self._prune_empty_ubs_seed_dirs(seeds_dir)

        window.destroy()
        self._refresh_ubs_seeds_panel()
        summary = (
            f"Retiradas {len(moved)} seeds duplicadas.\n\n"
            f"  Movidas a: {retired_dir}\n"
            f"  Registros de memoria borrados y pesos del Universo recalculados."
        )
        if errors:
            summary += f"\n\n  Errores: {len(errors)}\n  " + "\n  ".join(errors[:5])
        messagebox.showinfo("Retirar duplicadas", summary)
