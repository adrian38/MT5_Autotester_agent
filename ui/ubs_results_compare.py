from __future__ import annotations

import html
import re
from datetime import datetime
from tkinter import messagebox

from ubs.path_utils import resolve_workspace_path
import sqlite3
import sys
from pathlib import Path

from ubs.db import connect_memory


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSResultsCompareMixin:
    """Comparativa de runs y de sets."""

    def _clear_ubs_comparison(self) -> None:
        if hasattr(self, "ubs_compare_sets_tree"):
            for item in self.ubs_compare_sets_tree.get_children():
                self.ubs_compare_sets_tree.delete(item)
        if hasattr(self, "ubs_compare_diff_tree"):
            for item in self.ubs_compare_diff_tree.get_children():
                self.ubs_compare_diff_tree.delete(item)
        self.ubs_compare_paths.clear()

    def _read_ubs_comparison(self, memory_path: Path):
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            run_options = self._ubs_compare_run_options(conn)
            run_id = self._selected_ubs_compare_run_id(run_options)
            if run_id <= 0:
                return run_options, run_id, None, []
            counts = conn.execute(
                """
                select count(*) as total,
                       sum(case when status = 'accepted' then 1 else 0 end) as accepted,
                       sum(case when status = 'rejected' then 1 else 0 end) as rejected
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                """,
                (run_id,),
            ).fetchone()
            rows = conn.execute(
                """
                select * from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                order by case when status = 'accepted' then 0 else 1 end,
                         score desc, id desc
                """,
                (run_id,),
            ).fetchall()
            return run_options, run_id, counts, rows
        finally:
            conn.close()

    def _insert_ubs_comparison_rows(self, rows) -> None:
        for row in rows:
            metrics = self._parse_ubs_metrics(row["metrics_json"])
            status = str(row["status"] or "")
            cid = str(row["id"] or "")
            item = self.ubs_compare_sets_tree.insert(
                "", "end",
                values=(
                    self._checkbox_text(cid in self.ubs_compare_checked), row["run_id"],
                    row["generation"], self._format_ubs_status(status),
                    row["target_symbol"] or row["symbol"], row["period"],
                    self._format_ubs_number(row["score"]),
                    self._format_ubs_number(metrics.get("net_profit")),
                    self._format_ubs_number(metrics.get("profit_factor")),
                    self._format_ubs_number(metrics.get("drawdown_pct")),
                    self._format_ubs_set_label(row),
                ),
                tags=(self._ubs_result_tag(status),),
            )
            self.ubs_compare_paths[item] = {
                "id": cid, "candidate_id": cid, "set": str(row["set_path"] or ""),
                "seed": str(row["seed_path"] or ""),
                "mutated": str(row["mutated_keys"] or ""),
            }

    def _show_ubs_comparison(self, run_id: int, counts, rows) -> None:
        total = int(counts["total"] or 0) if counts else len(rows)
        accepted = int(counts["accepted"] or 0) if counts else sum(
            1 for row in rows if row["status"] == "accepted"
        )
        rejected = int(counts["rejected"] or 0) if counts else sum(
            1 for row in rows if row["status"] == "rejected"
        )
        self.ubs_compare_summary.set(
            f"Run #{run_id}: resultados {total} | aceptados {accepted} | "
            f"rechazados {rejected} | cargados {len(rows)}"
        )
        if not hasattr(self, "ubs_compare_sets_tree"):
            return
        self._insert_ubs_comparison_rows(rows)
        if rows:
            first = self.ubs_compare_sets_tree.get_children()[0]
            self.ubs_compare_sets_tree.selection_set(first)
            self._refresh_ubs_comparison_diff()
        else:
            self.ubs_compare_detail.set("No hay resultados puntuados para el run visible.")

    def _refresh_ubs_comparison(self) -> None:
        self._clear_ubs_comparison()
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            self.ubs_compare_summary.set("Sin memoria SQLite UBS")
            self.ubs_compare_detail.set(f"No existe: {memory_path}")
            return
        try:
            run_options, selected_run_id, counts, rows = self._read_ubs_comparison(memory_path)
        except sqlite3.Error as exc:
            self.ubs_compare_summary.set("No se pudo leer comparacion UBS")
            self.ubs_compare_detail.set(str(exc))
            return
        if selected_run_id <= 0:
            self.ubs_compare_summary.set("Sin run visible")
            self.ubs_compare_detail.set("No hay runs UBS visibles en memoria.")
            return
        self._update_ubs_compare_run_combo(run_options, selected_run_id)
        self._show_ubs_comparison(selected_run_id, counts, rows)

    def _ubs_compare_run_options(self, conn: sqlite3.Connection) -> list[tuple[int, str]]:
        rows = conn.execute(
            """
            select
                r.id,
                r.created_at,
                count(c.id) as total,
                sum(case when c.status = 'accepted' then 1 else 0 end) as accepted,
                sum(case when c.status = 'rejected' then 1 else 0 end) as rejected
            from runs r
            left join candidates c
                on c.run_id = r.id and c.status in ('accepted', 'rejected')
            where coalesce(r.hidden, 0) = 0
            group by r.id
            order by r.id desc
            """
        ).fetchall()
        options: list[tuple[int, str]] = []
        for row in rows:
            run_id = int(row["id"])
            created = str(row["created_at"] or "")[:16]
            total = int(row["total"] or 0)
            accepted = int(row["accepted"] or 0)
            rejected = int(row["rejected"] or 0)
            options.append((run_id, f"#{run_id} | {created} | {total} ({accepted}/{rejected})"))
        return options

    def _selected_ubs_compare_run_id(self, options: list[tuple[int, str]]) -> int:
        if not options:
            return 0
        newest_run_id = options[0][0]
        latest_seen = int(getattr(self, "_ubs_compare_latest_seen_run_id", 0) or 0)
        if newest_run_id > latest_seen:
            self._ubs_compare_latest_seen_run_id = newest_run_id
            return newest_run_id
        selected = self.ubs_compare_run_id.get().strip()
        match = re.search(r"#?(\d+)", selected)
        if match:
            run_id = int(match.group(1))
            if any(option_id == run_id for option_id, _label in options):
                return run_id
        return newest_run_id

    def _update_ubs_compare_run_combo(self, options: list[tuple[int, str]], selected_run_id: int) -> None:
        if not hasattr(self, "ubs_compare_run_combo"):
            return
        labels = [label for _run_id, label in options]
        self.ubs_compare_run_combo.configure(values=labels)
        selected_label = next((label for run_id, label in options if run_id == selected_run_id), "")
        if selected_label and self.ubs_compare_run_id.get() != selected_label:
            self.ubs_compare_run_id.set(selected_label)

    def _refresh_ubs_comparison_diff(self) -> None:
        if hasattr(self, "ubs_compare_diff_tree"):
            for item in self.ubs_compare_diff_tree.get_children():
                self.ubs_compare_diff_tree.delete(item)
        paths = self._selected_ubs_compare_paths()
        if not paths:
            self.ubs_compare_detail.set("Selecciona un resultado para comparar contra su seed.")
            return
        seed_path = resolve_workspace_path(paths.get("seed", ""))
        set_path = resolve_workspace_path(paths.get("set", ""))
        if not seed_path.exists() or not set_path.exists():
            self.ubs_compare_detail.set("No existe el seed o el set aceptado en disco.")
            return
        seed_values = self._read_set_values_for_compare(seed_path)
        set_values = self._read_set_values_for_compare(set_path)
        changed = []
        for key in sorted(set(seed_values) | set(set_values)):
            seed_value = seed_values.get(key, "(faltante)")
            set_value = set_values.get(key, "(faltante)")
            if seed_value != set_value:
                changed.append((key, seed_value, set_value))
        mutated = [key for key in paths.get("mutated", "").split(";") if key]
        mutated_hint = f" | mutados por agente: {', '.join(mutated[:8])}" if mutated else ""
        self.ubs_compare_detail.set(
            f"{len(changed)} diferencias | Seed: {self._short_filename(seed_path.name)} | "
            f"Resultado: {self._short_filename(set_path.name)}{mutated_hint}"
        )
        if not hasattr(self, "ubs_compare_diff_tree"):
            return
        for key, seed_value, set_value in changed:
            self.ubs_compare_diff_tree.insert("", "end", values=(key, seed_value, set_value))

    def _ubs_compare_rows_for_report(self) -> tuple[int, list[sqlite3.Row]]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return 0, []
        conn = connect_memory(memory_path)
        conn.row_factory = sqlite3.Row
        try:
            run_options = self._ubs_compare_run_options(conn)
            run_id = self._selected_ubs_compare_run_id(run_options)
            if run_id <= 0:
                return 0, []
            rows = conn.execute(
                """
                select *
                from candidates
                where run_id = ? and status in ('accepted', 'rejected')
                order by
                    case when status = 'accepted' then 0 else 1 end,
                    score desc,
                    id desc
                """,
                (run_id,),
            ).fetchall()
            return run_id, rows
        finally:
            conn.close()

    def _set_diff_rows(self, seed_path: Path, set_path: Path) -> list[tuple[str, str, str]]:
        seed_values = self._read_set_values_for_compare(seed_path)
        set_values = self._read_set_values_for_compare(set_path)
        changed: list[tuple[str, str, str]] = []
        for key in sorted(set(seed_values) | set(set_values)):
            seed_value = seed_values.get(key, "(faltante)")
            set_value = set_values.get(key, "(faltante)")
            if seed_value != set_value:
                changed.append((key, seed_value, set_value))
        return changed

    def _ubs_compare_report_row(self, index: int, row) -> tuple[str, str, int]:
        metrics = self._parse_ubs_metrics(row["metrics_json"])
        seed_path = resolve_workspace_path(row["seed_path"])
        set_path = resolve_workspace_path(row["set_path"])
        files_exist = seed_path.exists() and set_path.exists()
        changes = self._set_diff_rows(seed_path, set_path) if files_exist else []
        missing_note = "" if files_exist else "Archivo seed o aceptado no encontrado"
        status = html.escape(self._format_ubs_status(str(row["status"] or "")))
        symbol = html.escape(str(row["target_symbol"] or row["symbol"]))
        score = html.escape(self._format_ubs_number(row["score"]))
        summary = (
            "<tr>" f"<td>{index}</td>" f"<td>{html.escape(str(row['run_id']))}</td>"
            f"<td>{html.escape(str(row['generation']))}</td>" f"<td>{status}</td>"
            f"<td>{symbol}</td>" f"<td>{html.escape(str(row['period']))}</td>"
            f"<td>{score}</td>"
            f"<td>{html.escape(self._format_ubs_number(metrics.get('net_profit')))}</td>"
            f"<td>{html.escape(self._format_ubs_number(metrics.get('profit_factor')))}</td>"
            f"<td>{html.escape(self._format_ubs_number(metrics.get('drawdown_pct')))}</td>"
            f"<td>{len(changes)}</td><td>{html.escape(set_path.name)}</td>"
            f"<td>{html.escape(seed_path.name)}</td></tr>"
        )
        diff_rows = "\n".join(
            "<tr>" f"<td>{html.escape(key)}</td>" f"<td>{html.escape(seed_value)}</td>"
            f"<td>{html.escape(set_value)}</td></tr>"
            for key, seed_value, set_value in changes
        ) or f"<tr><td colspan='3'>{html.escape(missing_note or 'Sin diferencias')}</td></tr>"
        mutated = [key for key in str(row["mutated_keys"] or "").split(";") if key]
        detail = (
            "<details>" f"<summary>#{index} {status} | {symbol} "
            f"{html.escape(str(row['period']))} | score {score} | cambios {len(changes)} | "
            f"{html.escape(set_path.name)}</summary>"
            f"<p><b>Seed:</b> {html.escape(str(seed_path))}<br>"
            f"<b>Set:</b> {html.escape(str(set_path))}<br>"
            f"<b>Mutados por agente:</b> {html.escape(', '.join(mutated) if mutated else '-')}</p>"
            "<table><thead><tr><th>Parametro</th><th>Seed</th><th>Set</th></tr></thead>"
            f"<tbody>{diff_rows}</tbody></table></details>"
        )
        return summary, detail, len(changes)

    @staticmethod
    def _ubs_compare_html(run_id, rows, summary_rows, detail_blocks, total_changes) -> str:
        accepted = sum(1 for row in rows if row["status"] == "accepted")
        rejected = sum(1 for row in rows if row["status"] == "rejected")
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>UBS Seed Compare</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:24px;}"
            "h1{margin:0 0 8px;font-size:24px;} h2{margin-top:28px;}"
            ".meta{color:#a8b3c7;margin-bottom:18px;}"
            "table{border-collapse:collapse;width:100%;margin:12px 0;background:#111827;}"
            "th,td{border:1px solid #334155;padding:6px 8px;font-size:12px;vertical-align:top;}"
            "th{background:#243247;color:#dbeafe;} tr:nth-child(even){background:#172033;}"
            "details{border:1px solid #334155;border-radius:6px;padding:10px;margin:10px 0;background:#111827;}"
            "summary{cursor:pointer;font-weight:600;color:#86efac;} p{color:#cbd5e1;font-size:13px;}"
            "</style></head><body>"
            "<h1>UBS comparacion resultados contra seed</h1>"
            f"<div class='meta'>Generado: {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} | "
            f"run #{run_id} | resultados: {len(rows)} | aceptados: {accepted} | rechazados: {rejected} | "
            f"cambios totales: {total_changes}</div>"
            "<h2>Resumen</h2>"
            "<table><thead><tr>"
            "<th>#</th><th>Run</th><th>Gen</th><th>Estado</th><th>Symbol</th><th>TF</th><th>Score</th>"
            "<th>Net</th><th>PF</th><th>DD %</th><th>Cambios</th><th>Set</th><th>Seed</th>"
            "</tr></thead><tbody>"
            + "\n".join(summary_rows)
            + "</tbody></table><h2>Detalle por set</h2>"
            + "\n".join(detail_blocks)
            + "</body></html>"
        )

    def _generate_ubs_compare_report(self) -> None:
        try:
            run_id, rows = self._ubs_compare_rows_for_report()
        except sqlite3.Error as exc:
            self._show_error("No se pudo generar reporte UBS", str(exc))
            return
        if not rows:
            messagebox.showinfo("Reporte UBS", "No hay resultados puntuados para reportar.")
            return
        summary_rows: list[str] = []
        detail_blocks: list[str] = []
        total_changes = 0
        for index, row in enumerate(rows, start=1):
            summary, detail, changes = self._ubs_compare_report_row(index, row)
            summary_rows.append(summary)
            detail_blocks.append(detail)
            total_changes += changes
        output_dir = BASE_DIR / "outputs" / "ubs_compare"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"ubs_seed_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        html_text = self._ubs_compare_html(
            run_id, rows, summary_rows, detail_blocks, total_changes,
        )
        report_path.write_text(html_text, encoding="utf-8")
        self.status_text.set(f"Reporte UBS generado: {report_path.name}")
        self._open_local_file(report_path)

    def _selected_ubs_compare_paths(self) -> dict[str, str] | None:
        if not hasattr(self, "ubs_compare_sets_tree"):
            return None
        selected = self.ubs_compare_sets_tree.selection()
        if not selected:
            return None
        return self.ubs_compare_paths.get(selected[0])

    def _read_set_values_for_compare(self, path: Path) -> dict[str, str]:
        text = ""
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except UnicodeError:
                continue
        if not text:
            text = path.read_text(errors="replace")
        values: dict[str, str] = {}
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith(";"):
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            values[key] = raw_value.split("||", 1)[0].strip()
        return values

    def _selected_ubs_compare_path(self, kind: str) -> Path | None:
        paths = self._selected_ubs_compare_paths()
        if not paths:
            return None
        raw_path = paths.get(kind, "")
        return resolve_workspace_path(raw_path) if raw_path else None

    def _open_selected_ubs_compare_seed(self) -> None:
        path = self._selected_ubs_compare_path("seed")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)

    def _open_selected_ubs_compare_set(self) -> None:
        path = self._selected_ubs_compare_path("set")
        if path is None:
            messagebox.showinfo("Agente UBS", "Selecciona un resultado primero.")
            return
        self._open_local_file(path)
