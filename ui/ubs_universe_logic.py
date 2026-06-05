from __future__ import annotations

import sqlite3
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from ubs.universe import asset_rows_from_groups, canonical_symbol, load_asset_universe


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseLogicMixin:
    def _refresh_ubs_universe_panel(self) -> None:
        for label, callback in (
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _load_ubs_asset_universe(self) -> tuple[list[tuple[str, str, list[str]]], dict[str, str]]:
        path = BASE_DIR / "assets" / "roboforex_assets.ini"
        groups, aliases = load_asset_universe(path, include_disabled=True)
        return asset_rows_from_groups(groups, aliases), aliases

    def _canonical_ubs_symbol(self, symbol: str, aliases: dict[str, str]) -> str:
        return canonical_symbol(symbol, aliases)

    def _empty_ubs_stat(self) -> dict[str, object]:
        return {"scores": [], "weights": [], "tests": 0, "accepted": 0, "pending": 0, "best": None}

    def _tag_for_weight(self, value: float | None) -> str:
        if value is None:
            return "neutral"
        return "positive" if value >= 0 else "negative"

    def _on_ubs_universe_tree_click(self, event: tk.Event) -> None:
        item, column = self._tree_item_from_event(self.ubs_universe_assets_tree, event)
        if not item or column != "#1":
            return
        info = self.ubs_universe_paths.get(item, {})
        symbol = info.get("symbol", "")
        if not symbol:
            return
        if symbol in self.ubs_universe_checked:
            self.ubs_universe_checked.remove(symbol)
        else:
            self.ubs_universe_checked.add(symbol)
        values = list(self.ubs_universe_assets_tree.item(item, "values"))
        if values:
            values[0] = self._checkbox_text(symbol in self.ubs_universe_checked)
            self.ubs_universe_assets_tree.item(item, values=values)
        return "break"

    def _set_checked_universe_symbols_enabled(self, enabled: bool) -> None:
        symbols = set(self.ubs_universe_checked)
        if not symbols and hasattr(self, "ubs_universe_assets_tree"):
            selected = self.ubs_universe_assets_tree.selection()
            symbols = {
                self.ubs_universe_paths.get(item, {}).get("symbol", "")
                for item in selected
            }
            symbols.discard("")
        if not symbols:
            messagebox.showinfo("Universo UBS", "Marca uno o mas simbolos primero.")
            return
        disabled = self._load_disabled_ubs_symbols()
        if enabled:
            disabled.difference_update(symbols)
            action = "habilitados"
        else:
            disabled.update(symbols)
            action = "deshabilitados"
        self._save_disabled_ubs_symbols(disabled)
        self.ubs_universe_checked.clear()
        self.status_text.set(f"Simbolos {action}: {len(symbols)}")
        self._refresh_ubs_universe()

    def _refresh_ubs_universe(self) -> None:
        if hasattr(self, "ubs_universe_assets_tree"):
            for item in self.ubs_universe_assets_tree.get_children():
                self.ubs_universe_assets_tree.delete(item)
        self.ubs_universe_paths.clear()
        if hasattr(self, "ubs_timeframes_tree"):
            for item in self.ubs_timeframes_tree.get_children():
                self.ubs_timeframes_tree.delete(item)
        # Respect locked state — don't show weights until user confirms with "Calcular pesos"
        if getattr(self, "ubs_weights_locked", None) and self.ubs_weights_locked.get():
            if hasattr(self, "ubs_universe_summary"):
                self.ubs_universe_summary.set(
                    "Pesos bloqueados — evalúa todas las semillas y pulsa 'Calcular pesos'"
                )
            if hasattr(self, "ubs_timeframe_summary"):
                self.ubs_timeframe_summary.set("Sin pesos hasta que completes la evaluación")
            return

        assets, aliases = self._load_ubs_asset_universe()
        disabled_symbols = self._load_disabled_ubs_symbols()
        checked_symbols = set(self.ubs_universe_checked)
        memory_path = self._ubs_memory_path()
        asset_stats: dict[str, dict[str, object]] = {}
        timeframe_stats: dict[str, dict[str, object]] = {}
        total_scored = 0
        total_pending = 0
        total_mismatch = 0
        total_seed_scored = 0
        total_seed_pending = 0
        total_seed_mismatch = 0

        if memory_path.exists():
            try:
                conn = sqlite3.connect(memory_path, timeout=1.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    select target_symbol, symbol, period, score, accepted, status
                    from candidates
                    """
                ).fetchall()
                seed_table = conn.execute(
                    "select name from sqlite_master where type='table' and name='seed_scores'"
                ).fetchone()
                seed_rows = []
                if seed_table:
                    seed_rows = conn.execute(
                        """
                        select symbol, period, score, accepted, status, active
                        from seed_scores
                        where active=1
                        """
                    ).fetchall()
                conn.close()
            except sqlite3.Error as exc:
                self.ubs_universe_summary.set(f"No se pudo leer memoria UBS: {exc}")
                self.ubs_timeframe_summary.set("Sin pesos por error SQLite")
                return

            for row in rows:
                status = str(row["status"] or "")
                if status == "report_mismatch":
                    total_mismatch += 1
                    continue
                canonical = self._canonical_ubs_symbol(row["target_symbol"] or row["symbol"], aliases)
                if canonical.upper() in disabled_symbols:
                    continue
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status == "generated":
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_pending += 1
                if row["score"] is None or status not in {"accepted", "rejected"}:
                    continue
                score = float(row["score"])
                accepted = bool(row["accepted"])
                asset_weight = score + (20.0 if accepted else 0.0)
                tf_weight = score + (15.0 if accepted else 0.0)
                for stat, weight in ((asset_stat, asset_weight), (tf_stat, tf_weight)):
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    stat["tests"] = int(stat["tests"]) + 1
                    stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
                    stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
                total_scored += 1

            for row in seed_rows:
                status = str(row["status"] or "")
                if status == "report_mismatch":
                    total_seed_mismatch += 1
                canonical = self._canonical_ubs_symbol(row["symbol"], aliases)
                if canonical.upper() in disabled_symbols:
                    continue
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status in {"pending", "no_report", "parse_error", "no_trades"}:
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_seed_pending += 1
                if row["score"] is None or status not in {"accepted", "rejected"}:
                    continue
                score = float(row["score"])
                accepted = bool(row["accepted"])
                asset_weight = score + (20.0 if accepted else 0.0)
                tf_weight = score + (15.0 if accepted else 0.0)
                for stat, weight in ((asset_stat, asset_weight), (tf_stat, tf_weight)):
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    stat["tests"] = int(stat["tests"]) + 1
                    stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
                    stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
                total_seed_scored += 1

        universe_symbols = {symbol.upper() for _, symbol, _ in assets}
        observed_only = sorted(symbol for symbol in asset_stats if symbol.upper() not in universe_symbols)
        all_assets = assets + [("Memoria", symbol, []) for symbol in observed_only]
        ranked_assets = []
        for group, symbol, symbol_aliases in all_assets:
            stat = asset_stats.get(symbol.upper(), self._empty_ubs_stat())
            weights = stat["weights"]
            scores = stat["scores"]
            weight_value = (sum(weights) / len(weights)) if weights else None
            avg_score = (sum(scores) / len(scores)) if scores else None
            ranked_assets.append((weight_value if weight_value is not None else -999999.0, group, symbol, symbol_aliases, stat, weight_value, avg_score))
        ranked_assets.sort(key=lambda item: (item[0], item[4]["pending"]), reverse=True)

        if hasattr(self, "ubs_universe_assets_tree"):
            for _, group, symbol, symbol_aliases, stat, weight_value, avg_score in ranked_assets:
                is_disabled = symbol.upper() in disabled_symbols
                item = self.ubs_universe_assets_tree.insert(
                    "",
                    "end",
                    values=(
                        self._checkbox_text(symbol.upper() in checked_symbols),
                        "no" if is_disabled else "si",
                        group,
                        symbol,
                        ", ".join(symbol_aliases),
                        self._format_ubs_number(weight_value),
                        self._format_ubs_number(avg_score),
                        self._format_ubs_number(stat["best"]),
                        int(stat["tests"]),
                        int(stat["accepted"]),
                        int(stat["pending"]),
                    ),
                    tags=("disabled" if is_disabled else self._tag_for_weight(weight_value),),
                )
                self.ubs_universe_paths[item] = {"symbol": symbol.upper()}

        valid_symbols = {info["symbol"] for info in self.ubs_universe_paths.values() if info.get("symbol")}
        self.ubs_universe_checked.intersection_update(valid_symbols)

        timeframe_order = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
        observed_timeframes = sorted(period for period in timeframe_stats if period not in timeframe_order)
        ordered_timeframes = timeframe_order + observed_timeframes
        tf_rows = []
        for period in ordered_timeframes:
            stat = timeframe_stats.get(period, self._empty_ubs_stat())
            weights = stat["weights"]
            scores = stat["scores"]
            weight_value = (sum(weights) / len(weights)) if weights else None
            avg_score = (sum(scores) / len(scores)) if scores else None
            tf_rows.append((weight_value if weight_value is not None else -999999.0, period, stat, weight_value, avg_score))
        tf_rows.sort(key=lambda item: item[0], reverse=True)

        if hasattr(self, "ubs_timeframes_tree"):
            valid_tfs: set[str] = set()
            for _, period, stat, weight_value, avg_score in tf_rows:
                valid_tfs.add(period.upper())
                self.ubs_timeframes_tree.insert(
                    "",
                    "end",
                    values=(
                        self._checkbox_text(period.upper() in self.ubs_timeframe_checked),
                        period,
                        self._format_ubs_number(weight_value),
                        self._format_ubs_number(avg_score),
                        self._format_ubs_number(stat["best"]),
                        int(stat["tests"]),
                        int(stat["accepted"]),
                        int(stat["pending"]),
                    ),
                    tags=(self._tag_for_weight(weight_value),),
                )
            self.ubs_timeframe_checked.intersection_update(valid_tfs)

        self.ubs_universe_summary.set(
            f"Universo: {len(assets)} activos | puntuados validos: {total_scored} | "
            f"semillas puntuadas: {total_seed_scored} | pendientes sin backtest: {total_pending + total_seed_pending} | "
            f"mismatch ignorados: {total_mismatch + total_seed_mismatch} | deshabilitados: {len(disabled_symbols)}"
        )
        self.ubs_timeframe_summary.set(
            "PESO activos = promedio(score +20 si accepted); PESO TF = promedio(score +15 si accepted). El agente usa esos pesos para priorizar."
        )

    def _disabled_symbols_path(self):
        from ubs.universe import disabled_symbols_path
        return disabled_symbols_path(BASE_DIR)

    def _load_disabled_ubs_symbols(self) -> set:
        from ubs.universe import load_disabled_symbols
        return load_disabled_symbols(self._disabled_symbols_path())

    def _save_disabled_ubs_symbols(self, symbols: set) -> None:
        from ubs.universe import save_disabled_symbols
        save_disabled_symbols(self._disabled_symbols_path(), symbols)

    # ── SEL en Timeframes ────────────────────────────────────────────────────

    def _on_ubs_timeframe_tree_click(self, event) -> None:
        if not hasattr(self, "ubs_timeframes_tree"):
            return
        item, column = self._tree_item_from_event(self.ubs_timeframes_tree, event)
        if not item or column != "#1":
            return
        values = list(self.ubs_timeframes_tree.item(item, "values"))
        if not values:
            return
        period = str(values[1]).upper()
        if period in self.ubs_timeframe_checked:
            self.ubs_timeframe_checked.remove(period)
        else:
            self.ubs_timeframe_checked.add(period)
        values[0] = self._checkbox_text(period in self.ubs_timeframe_checked)
        self.ubs_timeframes_tree.item(item, values=values)
        return "break"

    # ── Limpiar pesos (score=NULL en candidates/seed_scores) ─────────────────

    def _weight_memory_path(self):
        return BASE_DIR / "outputs" / "ubs_memory.sqlite"

    def _clear_weights_sql(self, conn, *, symbols=None, periods=None) -> int:
        """Set score=NULL for candidates matching symbols and/or periods.
        Returns number of rows affected."""
        affected = 0
        if symbols:
            for sym in symbols:
                r = conn.execute(
                    "update candidates set score=null, accepted=null "
                    "where upper(target_symbol)=upper(?) and score is not null",
                    (sym,),
                )
                affected += r.rowcount
                r2 = conn.execute(
                    "update seed_scores set score=null, accepted=null "
                    "where upper(symbol)=upper(?) and score is not null",
                    (sym,),
                )
                affected += r2.rowcount
        if periods:
            for per in periods:
                r = conn.execute(
                    "update candidates set score=null, accepted=null "
                    "where upper(period)=upper(?) and score is not null",
                    (per,),
                )
                affected += r.rowcount
                r2 = conn.execute(
                    "update seed_scores set score=null, accepted=null "
                    "where upper(period)=upper(?) and score is not null",
                    (per,),
                )
                affected += r2.rowcount
        conn.commit()
        return affected

    def _clear_selected_weights(self) -> None:
        symbols = set(self.ubs_universe_checked)
        periods = set(self.ubs_timeframe_checked)
        if not symbols and not periods:
            messagebox.showinfo("Limpiar pesos", "Marca activos o TFs primero (columna SEL).")
            return
        mem = self._weight_memory_path()
        if not mem.exists():
            messagebox.showinfo("Limpiar pesos", "No existe memoria UBS.")
            return
        desc = []
        if symbols:
            desc.append(f"activos: {', '.join(sorted(symbols))}")
        if periods:
            desc.append(f"TF: {', '.join(sorted(periods))}")
        if not messagebox.askyesno("Limpiar pesos seleccionados",
                                   f"Esto pondrá score=NULL en todos los candidatos para:\n{chr(10).join(desc)}\n\nSus pesos volverán a 0. ¿Continuar?"):
            return
        import sqlite3
        conn = sqlite3.connect(mem, timeout=2.0)
        n = self._clear_weights_sql(conn, symbols=symbols, periods=periods)
        conn.close()
        self.ubs_universe_checked.clear()
        self.ubs_timeframe_checked.clear()
        self.status_text.set(f"Pesos limpiados: {n} candidatos afectados")
        self._refresh_ubs_universe()

    def _clear_all_asset_weights(self) -> None:
        mem = self._weight_memory_path()
        if not mem.exists():
            messagebox.showinfo("Limpiar pesos activos", "No existe memoria UBS.")
            return
        if not messagebox.askyesno("Limpiar todos los pesos de activos",
                                   "Esto pondrá score=NULL en TODOS los candidatos de todos los activos.\n"
                                   "Los pesos volverán a 0. ¿Continuar?"):
            return
        import sqlite3
        conn = sqlite3.connect(mem, timeout=2.0)
        conn.execute("update candidates set score=null, accepted=null where score is not null")
        conn.execute("update seed_scores  set score=null, accepted=null where score is not null")
        n = conn.execute("select changes()").fetchone()[0]
        conn.commit()
        conn.close()
        self.status_text.set(f"Todos los pesos de activos limpiados")
        self._refresh_ubs_universe()

    def _clear_all_tf_weights(self) -> None:
        mem = self._weight_memory_path()
        if not mem.exists():
            messagebox.showinfo("Limpiar pesos TF", "No existe memoria UBS.")
            return
        if not messagebox.askyesno("Limpiar todos los pesos de Timeframes",
                                   "Esto pondrá score=NULL para todos los TFs en candidates y seed_scores.\n"
                                   "Los pesos de TF volverán a 0. ¿Continuar?"):
            return
        import sqlite3
        periods = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
        conn = sqlite3.connect(mem, timeout=2.0)
        n = self._clear_weights_sql(conn, periods=periods)
        conn.close()
        self.ubs_timeframe_checked.clear()
        self.status_text.set(f"Todos los pesos de TF limpiados: {n} candidatos afectados")
        self._refresh_ubs_universe()


