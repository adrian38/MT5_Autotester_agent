from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from tkinter import messagebox

from run_tests import load_set_files
from ubs.db import connect_memory
from ubs.path_utils import resolve_workspace_path, workspace_path_exists


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSeedsCleanupMixin:
    """Limpieza y borrado de semillas y de su memoria."""

    def _cleanup_seed_db(self, conn, seed_paths: list[str]) -> None:
        """Borra seed_scores y seed_overrides de esas seeds."""
        if not seed_paths:
            return
        keys: set[str] = set()
        normalized_keys: set[str] = set()

        def _add_key(value: object) -> None:
            text = str(value)
            if not text:
                return
            keys.add(text)
            normalized_keys.add(text.replace("\\", "/").lower())

        for raw_path in seed_paths:
            if not raw_path:
                continue
            path = resolve_workspace_path(raw_path)
            _add_key(path)
            if not path.is_absolute():
                _add_key((BASE_DIR / path).resolve())
            try:
                resolved = path.resolve()
                _add_key(resolved)
                try:
                    _add_key(resolved.relative_to(BASE_DIR))
                except ValueError:
                    pass
            except OSError:
                pass
        if not keys and not normalized_keys:
            return
        if keys:
            params = sorted(keys)
            ph = ",".join("?" for _ in params)
            conn.execute(f"delete from seed_scores   where seed_path in ({ph})", params)
            conn.execute(f"delete from seed_overrides where seed_path in ({ph})", params)
        if normalized_keys:
            params = sorted(normalized_keys)
            ph = ",".join("?" for _ in params)
            conn.execute(f"delete from seed_scores   where lower(replace(seed_path, '\\', '/')) in ({ph})", params)
            conn.execute(f"delete from seed_overrides where lower(replace(seed_path, '\\', '/')) in ({ph})", params)
        conn.execute("delete from seed_overrides where seed_path not in (select seed_path from seed_scores)")
        conn.commit()

    def _cleanup_obsolete_seed_db(self, conn) -> int:
        rows = conn.execute("select seed_path from seed_scores where active=0").fetchall()
        obsolete_paths = [str(row[0]) for row in rows]
        if obsolete_paths:
            self._cleanup_seed_db(conn, obsolete_paths)
        return len(obsolete_paths)

    def _cleanup_all_seed_db(self, conn) -> None:
        conn.execute("delete from seed_scores")
        conn.execute("delete from seed_overrides")
        conn.commit()

    def _delete_selected_ubs_seed(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            self._show_error("Sin seleccion", "Selecciona una semilla para eliminar.")
            return
        selected_paths = [info.get("seed_path", "") for info in infos if info.get("seed_path")]
        existing = [resolve_workspace_path(path) for path in selected_paths if workspace_path_exists(path)]
        missing = len(selected_paths) - len(existing)
        if not selected_paths:
            messagebox.showinfo("Eliminar semilla", "No hay rutas asociadas a las seeds marcadas.")
            return
        if not messagebox.askyesno(
            "Eliminar semilla",
            f"Eliminar {len(existing)} seed(s) del disco y {missing} registro(s) obsoleto(s) de memoria?\n"
            "Esta accion no se puede deshacer.",
        ):
            return
        deleted_paths: list[str] = []
        errors: list[str] = []
        for path in existing:
            try:
                path.unlink()
                deleted_paths.append(str(path))
                self.ubs_seed_checked.discard(str(path))
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        memory_path = self._ubs_memory_path()
        if selected_paths and memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                self._cleanup_seed_db(conn, selected_paths)
                conn.close()
            except sqlite3.Error:
                pass
        if errors:
            self._show_error("Errores al eliminar", "\n".join(errors))
        self._refresh_ubs_seeds()
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_universe()

    def _delete_rejected_ubs_seeds(self) -> None:
        if not hasattr(self, "ubs_seeds_tree"):
            return
        selected_infos = self._checked_ubs_seed_infos()
        if selected_infos:
            rejected_infos = [
                info for info in selected_infos
                if str(info.get("status", "")).lower() in {"rejected", "rechazado"}
            ]
        else:
            rejected_infos = []
            for iid in self.ubs_seeds_tree.get_children(""):
                values = self.ubs_seeds_tree.item(iid, "values")
                if len(values) < 9:
                    continue
                status = str(values[1]).strip().lower()
                if status != "rechazado":
                    continue
                info = self.ubs_seed_paths.get(iid, {})
                if info:
                    rejected_infos.append(info)
        existing = [
            resolve_workspace_path(info.get("seed_path", ""))
            for info in rejected_infos
            if workspace_path_exists(info.get("seed_path", ""))
        ]
        if not existing:
            messagebox.showinfo("Eliminar rechazadas", "No hay seeds rechazadas existentes para eliminar.")
            return
        if not messagebox.askyesno(
            "Eliminar rechazadas",
            f"Eliminar {len(existing)} seed(s) rechazada(s) del disco?\nEsta accion no se puede deshacer.",
        ):
            return
        deleted_paths: list[str] = []
        errors: list[str] = []
        for path in existing:
            try:
                path.unlink()
                deleted_paths.append(str(path))
                self.ubs_seed_checked.discard(str(path))
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        memory_path = self._ubs_memory_path()
        if deleted_paths and memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                self._cleanup_seed_db(conn, deleted_paths)
                conn.close()
            except sqlite3.Error:
                pass
        if errors:
            self._show_error("Errores al eliminar", "\n".join(errors))
        self._refresh_ubs_seeds()
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_universe()

    def _delete_all_ubs_seeds(self) -> None:
        try:
            source_dir = self._ubs_generator_source_dir()
            all_paths = load_set_files(source_dir, None, recursive=True)
        except Exception as exc:
            self._show_error("Sin carpeta de seeds", str(exc))
            return
        obsolete_count = 0
        memory_path = self._ubs_memory_path()
        if memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                if self._sqlite_table_exists(conn, "seed_scores"):
                    obsolete_count = int(conn.execute("select count(*) from seed_scores where active=0").fetchone()[0] or 0)
                conn.close()
            except sqlite3.Error:
                obsolete_count = 0
        if not all_paths and not obsolete_count:
            messagebox.showinfo("Eliminar todas", "No hay seeds en la carpeta configurada ni registros obsoletos.")
            return
        if not messagebox.askyesno(
            "Eliminar TODAS las seeds",
            f"Eliminar {len(all_paths)} seed(s) del disco y limpiar toda la memoria de seeds?\n"
            f"Registros obsoletos detectados: {obsolete_count}\n\n"
            f"Carpeta: {source_dir}\n\n"
            "Esta acción no se puede deshacer.",
        ):
            return
        deleted: list[str] = []
        errors: list[str] = []
        for path in all_paths:
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
        if memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                self._cleanup_all_seed_db(conn)
                conn.close()
            except sqlite3.Error:
                pass
        self.ubs_seed_checked.clear()
        self.status_text.set(f"Seeds eliminadas: {len(deleted)}")
        if errors:
            self._show_error("Errores al eliminar", "\n".join(errors))
        self._refresh_ubs_seeds()
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_universe()

    def _reset_ubs_seed_evaluation(self) -> None:
        """Delete all seed reports from disk and reset seed_scores to pending."""
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Resetear evaluación", "Sin memoria UBS. No hay nada que resetear.")
            return
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("select seed_path, report_path from seed_scores where active=1").fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error SQLite", str(exc))
            return
        count = len(rows)
        if not messagebox.askyesno(
            "Resetear evaluación de semillas",
            f"¿Eliminar los reportes y resetear {count} semilla(s) a pendiente?\n\n"
            "Los archivos .set no se borran. Los pesos del Universo quedarán\n"
            "bloqueados hasta que uses 'Calcular pesos' tras la nueva evaluación.",
        ):
            return
        deleted_reports = 0
        for row in rows:
            rp = row["report_path"]
            if rp:
                try:
                    p = resolve_workspace_path(str(rp))
                    if p.exists():
                        p.unlink()
                        deleted_reports += 1
                except OSError:
                    pass
        try:
            conn = connect_memory(memory_path)
            conn.execute("""
                update seed_scores
                set status='pending', score=null, accepted=null,
                    metrics_json=null, report_path=null, evaluated_at=null
                where active=1
            """)
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error al resetear DB", str(exc))
            return
        self.ubs_weights_locked.set(True)
        self._refresh_ubs_seeds()
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_universe()
        messagebox.showinfo(
            "Resetear evaluación",
            f"{count} semilla(s) reseteadas a pendiente.\n{deleted_reports} reporte(s) eliminados del disco.\n\n"
            "Ejecuta 'Evaluar semillas' y luego usa 'Calcular pesos' en el Universo.",
        )
