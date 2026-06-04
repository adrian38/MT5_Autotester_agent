from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from run_tests import apply_symbol_map, infer_tester_fields_from_set, load_set_files, parse_symbol_map


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSSeedsLogicMixin:
    def _refresh_ubs_seeds_panel(self) -> None:
        for label, callback in (
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_seeds", self._refresh_ubs_seeds),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _ubs_seed_reason(self, row: object, status: str) -> str:
        if row is None:
            return ""
        if status == "report_mismatch":
            return "mismatch symbol/TF"
        if status == "parse_error":
            return "error al parsear reporte"
        if status == "no_report":
            return "sin reporte"
        if status == "no_trades":
            return "reporte sin operaciones"
        if status == "disabled_symbol":
            return "symbol deshabilitado"
        metrics_json = None
        try:
            metrics_json = row["metrics_json"]
        except (TypeError, KeyError, IndexError):
            pass
        if not metrics_json:
            return ""
        try:
            data = json.loads(metrics_json)
            reasons = data.get("reasons") or []
            if not reasons:
                return ""
            formats = {
                "net_profit": ("net profit", ".0f", ""),
                "profit_factor": ("PF", ".2f", ""),
                "trades": ("trades", "d", ""),
                "drawdown_pct": ("DD", ".1f", "%"),
                "recovery_factor": ("RF", ".2f", ""),
                "positive_month_ratio": ("meses+", ".0%", ""),
            }
            parts = []
            for reason in reasons:
                label, fmt, suffix = formats.get(reason, (reason, "", ""))
                value = data.get(reason)
                if value is None:
                    parts.append(label)
                    continue
                try:
                    parts.append(f"{label}: {value:{fmt}}{suffix}")
                except (TypeError, ValueError):
                    parts.append(f"{label}: {value}")
            return " | ".join(parts)
        except Exception:
            return ""

    def _count_ubs_seed_files(self) -> tuple[int, str]:
        source_dir = self._ubs_generator_source_dir()
        files = load_set_files(source_dir, None, recursive=True)
        return len(files), str(source_dir)

    def _count_ubs_seed_pending(self, seed_files: list) -> int:
        """Estimate how many seeds will actually run backtests."""
        memory_path = self._ubs_memory_path()
        disabled_symbols = self._load_disabled_ubs_symbols()
        try:
            symbol_map = parse_symbol_map(self.symbol_map.get().strip())
        except Exception:
            symbol_map = {}
        runnable_seed_files = []
        for path in seed_files:
            inferred_symbol, _ = self._inferred_ubs_seed_fields(path)
            canonical = apply_symbol_map(inferred_symbol, symbol_map).strip().upper()
            raw = inferred_symbol.strip().upper()
            if raw in disabled_symbols or canonical in disabled_symbols:
                continue
            runnable_seed_files.append(path)
        seed_files = runnable_seed_files
        if not memory_path.exists():
            return len(seed_files)
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            if not self._sqlite_table_exists(conn, "seed_scores"):
                conn.close()
                return len(seed_files)
            rows = {str(r["seed_path"]): r for r in conn.execute("select * from seed_scores").fetchall()}
            overrides = {
                str(r["seed_path"]): (str(r["symbol"] or "").strip().upper(), str(r["period"] or "").strip().upper())
                for r in conn.execute("select seed_path, symbol, period from seed_overrides").fetchall()
            } if self._sqlite_table_exists(conn, "seed_overrides") else {}
            conn.close()
        except sqlite3.Error:
            return len(seed_files)
        pending = 0
        for path in seed_files:
            path_text = str(path)
            row = rows.get(path_text)
            try:
                stat = path.stat()
            except OSError:
                continue
            inferred_symbol, inferred_period = self._inferred_ubs_seed_fields(path)
            ov_sym, ov_per = overrides.get(path_text, ("", ""))
            symbol = ov_sym or inferred_symbol
            period = ov_per or inferred_period
            canonical = apply_symbol_map(symbol, symbol_map).strip().upper()
            raw = symbol.strip().upper()
            if raw in disabled_symbols or canonical in disabled_symbols:
                continue
            changed = (
                row is None
                or abs(float(row["seed_mtime"] or 0.0) - float(stat.st_mtime)) > 0.001
                or int(row["seed_size"] or -1) != int(stat.st_size)
                or str(row["status"] or "") not in {"accepted", "rejected", "report_mismatch", "disabled_symbol"}
                or str(row["symbol"] or "").strip().upper() != symbol.strip().upper()
                or str(row["period"] or "").strip().upper() != period.strip().upper()
            )
            if changed:
                pending += 1
        return pending

    def _ubs_seed_eval_args(self) -> list[str]:
        source_dir = self._ubs_generator_source_dir()
        output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent"))
        args = [
            "--evaluate-seeds",
            "--source-dir", str(source_dir),
            "--output-dir", str(output_dir),
            "--memory", str(self._ubs_memory_path()),
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
        ]
        if self.ubs_seed_from_date.get().strip():
            args.extend(["--from-date", self.ubs_seed_from_date.get().strip()])
        if self.ubs_seed_to_date.get().strip():
            args.extend(["--to-date", self.ubs_seed_to_date.get().strip()])
        args.extend(self._ubs_seed_score_args())
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
            if self.mt5_path.get().strip():
                args.extend(["--mt5-path", self.mt5_path.get()])
            if self.mt5_data_root.get().strip():
                args.extend(["--data-dir", self.mt5_data_root.get()])
        if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
            args.extend(["--symbol-map", self.symbol_map.get().strip()])
        return args

    def _run_ubs_seed_evaluation(self) -> None:
        try:
            args = self._ubs_seed_eval_args()
            source_dir = self._ubs_generator_source_dir()
            seed_files = load_set_files(source_dir, None, recursive=True)
            total = len(seed_files)
            target = str(source_dir)
            pending = self._count_ubs_seed_pending(seed_files)
            already_ok = total - pending
        except Exception as exc:
            self._show_error("No se pudo preparar evaluacion de semillas", str(exc))
            return
        details = [
            "Accion: Evaluar semillas UBS",
            f"Carpeta seeds: {target}",
            f"Seeds detectadas: {total}",
            f"Backtests a ejecutar: {pending}  (ya evaluadas sin cambios: {already_ok})",
            "Corren: sin reporte, sin score, con symbol/TF cambiado.",
            "Las semillas borradas quedan inactivas para los pesos.",
            f"Pass Seeds: net>{self.ubs_seed_pass_min_net_profit.get().strip()} | PF>={self.ubs_seed_pass_min_profit_factor.get().strip()} | DD<={self.ubs_seed_pass_max_drawdown_pct.get().strip()}%",
            f"Pass Seeds: trades>={self.ubs_seed_pass_min_trades.get()} | recovery>={self.ubs_seed_pass_min_recovery_factor.get().strip()}",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar evaluacion de semillas", pending, details):
            self.ubs_seed_eval_summary.set("Evaluando semillas UBS...")
            self._run_script("ubs_agent.py", args)

    def _refresh_ubs_seed_eval_summary(self) -> None:
        if not hasattr(self, "ubs_seed_eval_summary"):
            return
        try:
            source_dir = self._ubs_generator_source_dir()
            seed_count = len(load_set_files(source_dir, None, recursive=True))
        except Exception:
            self.ubs_seed_eval_summary.set("Semillas: carpeta no valida")
            return

        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_seed_eval_summary.set(f"Semillas: {seed_count} | evaluadas 0 | pendientes {seed_count}")
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            seed_table = conn.execute(
                "select name from sqlite_master where type='table' and name='seed_scores'"
            ).fetchone()
            if not seed_table:
                conn.close()
                self.ubs_seed_eval_summary.set(f"Semillas: {seed_count} | evaluadas 0 | pendientes {seed_count}")
                return
            active_counts = conn.execute(
                """
                select
                    count(*) as total,
                    sum(case when status in ('accepted', 'rejected', 'report_mismatch', 'disabled_symbol') then 1 else 0 end) as ready,
                    sum(case
                        when status in ('accepted', 'rejected') and score is null then 1
                        when status not in ('accepted', 'rejected', 'report_mismatch', 'disabled_symbol') then 1
                        else 0
                    end) as pending
                from seed_scores
                where active=1
                """
            ).fetchone()
            inactive = int(conn.execute("select count(*) from seed_scores where active=0").fetchone()[0] or 0)
            conn.close()
        except sqlite3.Error as exc:
            self.ubs_seed_eval_summary.set(f"Semillas: error SQLite ({exc})")
            return

        ready = int(active_counts["ready"] or 0) if active_counts else 0
        pending = max(seed_count - ready, int(active_counts["pending"] or 0) if active_counts else seed_count)
        self.ubs_seed_eval_summary.set(
            f"Semillas: {seed_count} | listas {ready} | pendientes {pending} | obsoletas {inactive}"
        )

    def _sqlite_table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return bool(conn.execute("select name from sqlite_master where type='table' and name=?", (table,)).fetchone())

    def _ensure_ubs_seed_override_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            create table if not exists seed_overrides (
                seed_path text primary key,
                symbol text not null default '',
                period text not null default '',
                updated_at text not null
            )
            """
        )
        if self._sqlite_table_exists(conn, "seed_scores"):
            conn.execute(
                """
                update seed_scores
                set status='report_mismatch', accepted=null
                where status in ('accepted', 'rejected')
                  and (upper(symbol)='UNKNOWN' or upper(period)='UNKNOWN')
                """
            )
        conn.commit()

    def _current_ubs_seed_files(self) -> list[Path]:
        return sorted(load_set_files(self._ubs_generator_source_dir(), None, recursive=True), key=lambda path: path.name.lower())

    def _inferred_ubs_seed_fields(self, path: Path) -> tuple[str, str]:
        try:
            fields = infer_tester_fields_from_set(path)
        except Exception:
            fields = {}
        symbol = str(fields.get("Symbol") or "UNKNOWN").strip().upper()
        period = str(fields.get("Period") or "UNKNOWN").strip().upper()
        return symbol, period

    def _refresh_ubs_seeds(self) -> None:
        if not hasattr(self, "ubs_seeds_tree"):
            return
        tree = self.ubs_seeds_tree
        tree.delete(*tree.get_children(""))
        self.ubs_seed_paths.clear()
        current_checked = set(self.ubs_seed_checked)

        try:
            seed_files = self._current_ubs_seed_files()
        except Exception as exc:
            self.ubs_seed_detail.set(f"Carpeta de seeds no valida: {exc}")
            return

        score_rows: dict[str, sqlite3.Row] = {}
        overrides: dict[str, tuple[str, str]] = {}
        inactive_rows: list[sqlite3.Row] = []
        memory_path = self._ubs_memory_path()
        if memory_path.exists():
            try:
                conn = sqlite3.connect(memory_path, timeout=1.0)
                conn.row_factory = sqlite3.Row
                self._ensure_ubs_seed_override_schema(conn)
                if self._sqlite_table_exists(conn, "seed_scores"):
                    rows = conn.execute("select * from seed_scores").fetchall()
                    score_rows = {str(row["seed_path"]): row for row in rows}
                    inactive_rows = [row for row in rows if not int(row["active"] or 0)]
                for row in conn.execute("select seed_path, symbol, period from seed_overrides").fetchall():
                    overrides[str(row["seed_path"])] = (
                        str(row["symbol"] or "").strip().upper(),
                        str(row["period"] or "").strip().upper(),
                    )
                conn.close()
            except sqlite3.Error as exc:
                self.ubs_seed_detail.set(f"Error SQLite semillas: {exc}")

        current_paths = {str(path) for path in seed_files}
        first_item = ""
        for path in seed_files:
            path_text = str(path)
            row = score_rows.get(path_text)
            inferred_symbol, inferred_period = self._inferred_ubs_seed_fields(path)
            override_symbol, override_period = overrides.get(path_text, ("", ""))
            symbol = override_symbol or (str(row["symbol"] or "").strip().upper() if row else inferred_symbol)
            period = override_period or (str(row["period"] or "").strip().upper() if row else inferred_period)
            status = str(row["status"] or "pending") if row else "pending"
            accepted = ""
            if row and row["accepted"] is not None:
                accepted = "si" if int(row["accepted"]) else "no"
            reason = self._ubs_seed_reason(row, status)
            item = tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(path_text in current_checked),
                    self._format_ubs_status(status),
                    symbol,
                    period,
                    self._format_ubs_number(row["score"] if row else None),
                    accepted,
                    "si" if override_symbol or override_period else "no",
                    reason,
                    path.name,
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_seed_paths[item] = {"seed_path": path_text, "active": "1", "status": status, "has_row": "1" if row else "0"}
            if not first_item:
                first_item = item

        for row in inactive_rows:
            path_text = str(row["seed_path"] or "")
            if not path_text or path_text in current_paths:
                continue
            status = str(row["status"] or "obsoleta")
            override_symbol, override_period = overrides.get(path_text, ("", ""))
            symbol = override_symbol or str(row["symbol"] or "").strip().upper()
            period = override_period or str(row["period"] or "").strip().upper()
            reason = self._ubs_seed_reason(row, status)
            item = tree.insert(
                "",
                "end",
                values=(
                    self._checkbox_text(path_text in current_checked),
                    "obsoleta",
                    symbol,
                    period,
                    self._format_ubs_number(row["score"]),
                    "",
                    "si" if override_symbol or override_period else "no",
                    reason,
                    Path(path_text).name,
                ),
                tags=("pending",),
            )
            self.ubs_seed_paths[item] = {"seed_path": path_text, "active": "0", "status": status}

        valid_paths = {info["seed_path"] for info in self.ubs_seed_paths.values() if info.get("seed_path")}
        self.ubs_seed_checked.intersection_update(valid_paths)

        if first_item:
            tree.selection_set(first_item)
            tree.focus(first_item)
            self._on_ubs_seed_select()
        else:
            self.ubs_seed_detail.set("No hay semillas .set en la carpeta UBS")
            self.ubs_seed_override_symbol.set("")
            self.ubs_seed_override_period.set("")

    def _selected_ubs_seed_info(self) -> dict[str, str]:
        if not hasattr(self, "ubs_seeds_tree"):
            return {}
        selected = self.ubs_seeds_tree.selection()
        if not selected:
            return {}
        return self.ubs_seed_paths.get(selected[0], {})

    def _checked_ubs_seed_infos(self, *, fallback_selected: bool = True) -> list[dict[str, str]]:
        infos = [
            info for info in self.ubs_seed_paths.values()
            if info.get("seed_path") in self.ubs_seed_checked
        ]
        if infos or not fallback_selected:
            return infos
        selected = self._selected_ubs_seed_info()
        return [selected] if selected else []

    def _on_ubs_seed_tree_click(self, event: tk.Event) -> None:
        item, column = self._tree_item_from_event(self.ubs_seeds_tree, event)
        if not item or column != "#1":
            return
        info = self.ubs_seed_paths.get(item, {})
        seed_path = info.get("seed_path", "")
        if not seed_path:
            return
        if seed_path in self.ubs_seed_checked:
            self.ubs_seed_checked.remove(seed_path)
        else:
            self.ubs_seed_checked.add(seed_path)
        values = list(self.ubs_seeds_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(seed_path in self.ubs_seed_checked)
            self.ubs_seeds_tree.item(item, values=values)
        return "break"

    def _on_ubs_seed_select(self) -> None:
        info = self._selected_ubs_seed_info()
        if not info:
            return
        item = self.ubs_seeds_tree.selection()[0]
        values = self.ubs_seeds_tree.item(item, "values")
        seed_path = info.get("seed_path", "")
        symbol = str(values[2] if len(values) > 2 else "").strip().upper()
        period = str(values[3] if len(values) > 3 else "").strip().upper()
        self.ubs_seed_override_symbol.set("" if symbol == "UNKNOWN" else symbol)
        self.ubs_seed_override_period.set("" if period == "UNKNOWN" else period)
        self.ubs_seed_detail.set(f"{Path(seed_path).name} | estado: {values[1] if len(values) > 1 else '-'}")

    def _open_selected_ubs_seed(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            self._show_error("Sin seleccion", "Selecciona una semilla.")
            return
        for info in infos:
            seed_path = info.get("seed_path", "")
            if seed_path:
                self._open_local_file(Path(seed_path))

    def _open_selected_ubs_seed_report(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            return
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Semillas UBS", "Sin memoria UBS. Evalua las semillas primero.")
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            rows = []
            for info in infos:
                seed_path = info.get("seed_path", "")
                if seed_path:
                    row = conn.execute("select report_path from seed_scores where seed_path=?", (seed_path,)).fetchone()
                    if row and row["report_path"]:
                        rows.append(row)
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error SQLite", str(exc))
            return
        if not rows:
            messagebox.showinfo("Semillas UBS", "Esta semilla no tiene reporte asociado.\nEjecuta 'Evaluar semillas' primero.")
            return
        for row in rows:
            self._open_local_file(Path(str(row["report_path"])))

    def _retry_selected_ubs_seed(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            messagebox.showinfo("Semillas UBS", "Selecciona una semilla primero.")
            return
        active_infos = [info for info in infos if info.get("active") != "0" and Path(info.get("seed_path", "")).expanduser().exists()]
        if not active_infos:
            messagebox.showinfo("Semillas UBS", "No hay seeds activas/existentes entre las marcadas.")
            return
        paths = [Path(info["seed_path"]).expanduser() for info in active_infos]
        try:
            output_dir = Path(self.ubs_generation_output.get().strip() or str(BASE_DIR / "outputs" / "ubs_agent"))
            args = [
                "--memory", str(self._ubs_memory_path()),
                "--output-dir", str(output_dir),
                "--template", self.template_path.get(),
                "--delay", str(self.delay.get()),
            ]
            for path in paths:
                args.extend(["--retry-seed-path", str(path)])
            args.extend(self._ubs_seed_score_args())
            if self.multiterminal_enabled.get():
                args.extend(self._multiterminal_args(require_ubs=True))
            else:
                args.extend(["--expert", self._required_ubs_ex5_file()])
                if self.mt5_path.get().strip():
                    args.extend(["--mt5-path", self.mt5_path.get()])
                if self.mt5_data_root.get().strip():
                    args.extend(["--data-dir", self.mt5_data_root.get()])
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudo preparar retry seed", str(exc))
            return

        selected_items = self.ubs_seeds_tree.selection() if hasattr(self, "ubs_seeds_tree") else ()
        values = self.ubs_seeds_tree.item(selected_items[0], "values") if selected_items else ()
        details = [
            "Accion: Repetir backtest seed UBS",
            f"Seeds: {len(paths)}",
            f"Primera: {paths[0].name}",
            f"Estado actual: {values[1] if len(values) > 1 else active_infos[0].get('status', '-')}",
            f"Backtests previstos: {len(paths)}",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar retry seed", len(paths), details):
            self._run_script("ubs_agent.py", args)

    def _cleanup_seed_db(self, conn, seed_paths: list[str]) -> None:
        """Borra seed_scores y seed_overrides de esas seeds."""
        if not seed_paths:
            return
        ph = ",".join("?" for _ in seed_paths)
        conn.execute(f"delete from seed_scores   where seed_path in ({ph})", seed_paths)
        conn.execute(f"delete from seed_overrides where seed_path in ({ph})", seed_paths)
        conn.commit()

    def _delete_selected_ubs_seed(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            self._show_error("Sin seleccion", "Selecciona una semilla para eliminar.")
            return
        existing = [Path(info.get("seed_path", "")) for info in infos if Path(info.get("seed_path", "")).exists()]
        if not existing:
            messagebox.showinfo("Eliminar semilla", "No existe ningun archivo de las seeds marcadas.")
            return
        if not messagebox.askyesno(
            "Eliminar semilla",
            f"Eliminar {len(existing)} seed(s) del disco?\nEsta accion no se puede deshacer.",
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
                conn = sqlite3.connect(memory_path, timeout=1.0)
                placeholders = ",".join("?" for _ in deleted_paths)
                self._cleanup_seed_db(conn, deleted_paths)
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
            Path(info.get("seed_path", "")).expanduser()
            for info in rejected_infos
            if Path(info.get("seed_path", "")).expanduser().exists()
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
                conn = sqlite3.connect(memory_path, timeout=1.0)
                placeholders = ",".join("?" for _ in deleted_paths)
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
        if not all_paths:
            messagebox.showinfo("Eliminar todas", "No hay seeds en la carpeta configurada.")
            return
        if not messagebox.askyesno(
            "Eliminar TODAS las seeds",
            f"Eliminar {len(all_paths)} seed(s) del disco?\n\n"
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
        memory_path = self._ubs_memory_path()
        if deleted and memory_path.exists():
            try:
                conn = sqlite3.connect(memory_path, timeout=1.0)
                placeholders = ",".join("?" for _ in deleted)
                self._cleanup_seed_db(conn, deleted)
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
            conn = sqlite3.connect(memory_path, timeout=1.0)
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
                    p = Path(str(rp))
                    if p.exists():
                        p.unlink()
                        deleted_reports += 1
                except OSError:
                    pass
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
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

    def _ubs_apply_weights(self) -> None:
        """Check all seeds are evaluated, then unlock and show weights."""
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            messagebox.showinfo("Calcular pesos", "Sin memoria UBS. Evalúa las semillas primero.")
            return
        try:
            conn = sqlite3.connect(memory_path, timeout=1.0)
            conn.row_factory = sqlite3.Row
            seed_files = self._current_ubs_seed_files()
            current_paths = {str(path) for path in seed_files}
            known_paths: set[str] = set()
            pending = conn.execute(
                "select count(*) as n from seed_scores where active=1 and status not in ('accepted','rejected','report_mismatch','disabled_symbol')"
            ).fetchone()
            total = conn.execute(
                "select count(*) as n from seed_scores where active=1"
            ).fetchone()
            if self._sqlite_table_exists(conn, "seed_scores"):
                known_paths = {str(row["seed_path"]) for row in conn.execute("select seed_path from seed_scores where active=1").fetchall()}
            conn.close()
            pending_count = int(pending["n"] if pending else 0)
            total_count = int(total["n"] if total else 0)
            missing_count = len(current_paths - known_paths)
        except sqlite3.Error as exc:
            self._show_error("Error SQLite", str(exc))
            return
        except Exception as exc:
            self._show_error("Error leyendo semillas", str(exc))
            return
        pending_count += missing_count
        total_count += missing_count
        if pending_count > 0:
            messagebox.showwarning(
                "Calcular pesos",
                f"Hay {pending_count} semilla(s) sin evaluar de {total_count} activas.\n\n"
                "Ejecuta 'Evaluar semillas' primero para obtener pesos fiables.",
            )
            return
        self.ubs_weights_locked.set(False)
        self._refresh_ubs_universe()
        messagebox.showinfo("Calcular pesos", "Pesos calculados y aplicados al Universo.")

    def _save_ubs_seed_override(self) -> None:
        infos = self._checked_ubs_seed_infos()
        if not infos:
            self._show_error("Sin seleccion", "Selecciona una o mas semillas.")
            return
        symbol = self.ubs_seed_override_symbol.get().strip().upper()
        period = self.ubs_seed_override_period.get().strip().upper()
        valid_periods = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}
        if not symbol:
            self._show_error("Symbol invalido", "Indica el symbol correcto.")
            return
        if period not in valid_periods:
            self._show_error("Timeframe invalido", f"El timeframe debe ser uno de: {', '.join(sorted(valid_periods))}.")
            return
        seed_paths = [info.get("seed_path", "") for info in infos if info.get("seed_path")]
        memory_path = self._ubs_memory_path()
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(memory_path, timeout=3.0)
            self._ensure_ubs_seed_override_schema(conn)
            now = datetime.now().isoformat(timespec="seconds")
            for seed_path in seed_paths:
                conn.execute(
                    """
                    insert into seed_overrides (seed_path, symbol, period, updated_at)
                    values (?, ?, ?, ?)
                    on conflict(seed_path) do update set
                        symbol=excluded.symbol,
                        period=excluded.period,
                        updated_at=excluded.updated_at
                    """,
                    (seed_path, symbol, period, now),
                )
            if self._sqlite_table_exists(conn, "seed_scores") and seed_paths:
                placeholders = ",".join("?" for _ in seed_paths)
                conn.execute(
                    f"""
                    update seed_scores
                    set symbol=?,
                        period=?,
                        report_path=case when status in ('accepted', 'rejected', 'no_trades') then null else report_path end,
                        score=case when status in ('accepted', 'rejected', 'no_trades') then null else score end,
                        accepted=case when status in ('accepted', 'rejected', 'no_trades') then null else accepted end,
                        metrics_json=case when status in ('accepted', 'rejected', 'no_trades') then null else metrics_json end,
                        status=case when status in ('accepted', 'rejected', 'no_trades') then 'pending' else status end
                    where seed_path in ({placeholders})
                    """,
                    (symbol, period, *seed_paths),
                )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._show_error("Error guardando seed", str(exc))
            return
        self.status_text.set(f"Override aplicado a {len(seed_paths)} seed(s)")
        self._refresh_ubs_seed_eval_summary()
        self._refresh_ubs_seeds()

    def _save_seed_criteria_clicked(self) -> None:
        try:
            self._ubs_seed_score_args()
            self._write_ui_settings()
        except Exception as exc:
            self._show_error("No se pudieron guardar criterios Seeds", str(exc))
            return
        self.status_text.set("Criterios Seeds guardados")
        self._refresh_ubs_seeds_panel()

    def _apply_seed_criteria_clicked(self) -> None:
        try:
            self._write_ui_settings()
            args = [
                "--rescore-seeds-only",
                "--source-dir", str(self._ubs_generator_source_dir()),
                "--memory", str(BASE_DIR / "outputs" / "ubs_memory.sqlite"),
            ]
            args.extend(self._ubs_seed_score_args())
            if self.symbol_map_enabled.get() and self.symbol_map.get().strip():
                args.extend(["--symbol-map", self.symbol_map.get().strip()])
        except Exception as exc:
            self._show_error("No se pudieron aplicar criterios Seeds", str(exc))
            return
        self._run_script("ubs_agent.py", args)


