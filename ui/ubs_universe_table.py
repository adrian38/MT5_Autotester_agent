from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from tkinter import messagebox

from run_tests import KNOWN_TIMEFRAMES, load_symbol_suffix_universe
from ubs.db import connect_memory
from ubs.account import broker_asset_universe_path_with_fallback, load_account_timeframe_universe
from ubs.weights import grouped_shrunk_mean


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseTableMixin:
    """Tabla del universo, simbolos deshabilitados y limpieza de pesos."""

    def _render_ubs_universe_asset_tree(
        self, ranked_assets, checked_symbols, disabled_symbols, seed_enabled_when_disabled,
    ) -> None:
        """Pinta el arbol de activos con los que han pasado la busqueda."""
        if hasattr(self, "ubs_universe_assets_tree"):
            for _, group, symbol, symbol_aliases, stat, weight_value, probability, confidence, final_trials, regression_trials, avg_score in ranked_assets:
                is_disabled = symbol.upper() in disabled_symbols
                seed_enabled = (not is_disabled) or symbol.upper() in seed_enabled_when_disabled
                item = self.ubs_universe_assets_tree.insert(
                    "",
                    "end",
                    values=(
                        self._checkbox_text(symbol.upper() in checked_symbols),
                        "no" if is_disabled else "si",
                        "si" if seed_enabled else "no",
                        group,
                        symbol,
                        ", ".join(symbol_aliases),
                        self._format_ubs_number(weight_value),
                        self._format_ubs_number(probability * 100.0 if probability is not None else None),
                        self._format_ubs_number(confidence * 100.0 if confidence is not None else None),
                        int(final_trials),
                        int(regression_trials),
                        self._format_ubs_number(avg_score),
                        self._format_ubs_number(stat["best"]),
                        int(stat["tests"]),
                        int(stat["accepted"]),
                        int(stat["pending"]),
                    ),
                    tags=("disabled" if is_disabled else self._tag_for_weight(weight_value),),
                )
                self.ubs_universe_paths[item] = {"symbol": symbol.upper()}

    def _rank_ubs_universe_assets(
        self, assets, asset_stats, asset_signals, timeframe_stats,
        disabled_symbols, seed_enabled_when_disabled, checked_symbols,
    ):
        """Ordena los activos, aplica la busqueda y pinta el arbol."""
        ranked_assets = []
        for group, symbol, symbol_aliases in assets:
            stat = asset_stats.get(symbol.upper(), self._empty_ubs_stat())
            scores = stat["scores"]
            signal = asset_signals.get(symbol.upper())
            fallback_weight = None
            groups = stat.get("weight_groups")
            if isinstance(groups, dict):
                fallback_weight = grouped_shrunk_mean(groups)
            weight_value = signal.score if signal is not None else fallback_weight
            probability = signal.probability if signal is not None else None
            confidence = signal.confidence if signal is not None else None
            final_trials = signal.final_trials if signal is not None else 0
            regression_trials = signal.regression_trials if signal is not None else 0
            avg_score = (sum(scores) / len(scores)) if scores else None
            ranked_assets.append((weight_value if weight_value is not None else -999999.0, group, symbol, symbol_aliases, stat, weight_value, probability, confidence, final_trials, regression_trials, avg_score))
        ranked_assets.sort(key=lambda item: (item[0], item[4]["pending"]), reverse=True)
        asset_total_before_filter = len(ranked_assets)
        asset_search_terms = self._ubs_universe_search_terms("ubs_universe_asset_search")
        if asset_search_terms:
            ranked_assets = [
                row for row in ranked_assets
                if self._ubs_universe_asset_matches_search(row[1], row[2], row[3], asset_search_terms)
            ]

        self._render_ubs_universe_asset_tree(
            ranked_assets, checked_symbols, disabled_symbols, seed_enabled_when_disabled,
        )

        valid_symbols = {info["symbol"] for info in self.ubs_universe_paths.values() if info.get("symbol")}
        self.ubs_universe_checked.intersection_update(valid_symbols)
        return ranked_assets, asset_total_before_filter, asset_search_terms

    def _rank_ubs_universe_timeframes(self, timeframe_stats, timeframe_signals):
        """Ordena los timeframes, aplica la busqueda y pinta el arbol."""
        timeframe_order = list(
            load_account_timeframe_universe(
                BASE_DIR,
                self._ubs_account_type(),
                self._ubs_broker(),
                include_experimental_long=True,
            )
        )
        observed_timeframes = sorted(period for period in timeframe_stats if period not in timeframe_order)
        ordered_timeframes = timeframe_order + observed_timeframes
        tf_rows = []
        for period in ordered_timeframes:
            stat = timeframe_stats.get(period, self._empty_ubs_stat())
            scores = stat["scores"]
            signal = timeframe_signals.get(period.upper())
            weight_value = signal.score if signal is not None else None
            probability = signal.probability if signal is not None else None
            confidence = signal.confidence if signal is not None else None
            final_trials = signal.final_trials if signal is not None else 0
            regression_trials = signal.regression_trials if signal is not None else 0
            avg_score = (sum(scores) / len(scores)) if scores else None
            tf_rows.append((weight_value if weight_value is not None else -999999.0, period, stat, weight_value, probability, confidence, final_trials, regression_trials, avg_score))
        tf_rows.sort(key=lambda item: item[0], reverse=True)
        tf_total_before_filter = len(tf_rows)
        tf_search_terms = self._ubs_universe_search_terms("ubs_universe_tf_search")
        if tf_search_terms:
            tf_rows = [row for row in tf_rows if self._ubs_universe_tf_matches_search(row[1], tf_search_terms)]

        if hasattr(self, "ubs_timeframes_tree"):
            valid_tfs: set[str] = set()
            for _, period, stat, weight_value, probability, confidence, final_trials, regression_trials, avg_score in tf_rows:
                valid_tfs.add(period.upper())
                self.ubs_timeframes_tree.insert(
                    "",
                    "end",
                    values=(
                        self._checkbox_text(period.upper() in self.ubs_timeframe_checked),
                        period,
                        self._format_ubs_number(weight_value),
                        self._format_ubs_number(probability * 100.0 if probability is not None else None),
                        self._format_ubs_number(confidence * 100.0 if confidence is not None else None),
                        int(final_trials),
                        int(regression_trials),
                        self._format_ubs_number(avg_score),
                        self._format_ubs_number(stat["best"]),
                        int(stat["tests"]),
                        int(stat["accepted"]),
                        int(stat["pending"]),
                    ),
                    tags=(self._tag_for_weight(weight_value),),
                )
            self.ubs_timeframe_checked.intersection_update(valid_tfs)
        return tf_rows, tf_total_before_filter, tf_search_terms

    def _ubs_universe_context(self):
        """Universo, sufijos, alias y politica de simbolos de la cuenta activa."""
        assets, aliases = self._load_ubs_asset_universe()
        asset_universe_path = broker_asset_universe_path_with_fallback(BASE_DIR, self._ubs_broker())
        universe_symbols_tuple = tuple(symbol for _group, symbol, _symbol_aliases in assets)
        universe_symbols = {symbol.upper() for symbol in universe_symbols_tuple}
        symbol_suffix, futures_suffix, shares_suffix = self._ubs_universe_suffix_config()
        suffix_universe = load_symbol_suffix_universe(
            asset_universe_path,
            symbol_suffix,
            futures_suffix,
            shares_suffix,
        )
        symbol_map = self._ubs_universe_symbol_map()
        signal_aliases = self._ubs_universe_signal_aliases(
            aliases,
            symbol_map,
            suffix_universe,
            symbol_suffix,
            futures_suffix,
            shares_suffix,
        )
        disabled_symbols, seed_enabled_when_disabled = self._active_ubs_symbol_policy(aliases)
        checked_symbols = set(self.ubs_universe_checked)
        memory_path = self._ubs_memory_path()
        return (
            assets, aliases, universe_symbols, universe_symbols_tuple,
            symbol_suffix, futures_suffix, shares_suffix, suffix_universe,
            symbol_map, signal_aliases, disabled_symbols,
            seed_enabled_when_disabled, checked_symbols, memory_path,
        )

    def _set_ubs_universe_summaries(
        self, assets, stats, disabled_symbols, seed_enabled_when_disabled,
        ranked_assets, asset_total_before_filter, asset_search_terms,
        tf_rows, tf_total_before_filter, tf_search_terms,
    ) -> None:
        """Los dos textos de resumen bajo las tablas del universo."""
        asset_filter_text = (
            f" | mostrando activos {len(ranked_assets)}/{asset_total_before_filter}"
            if asset_search_terms else ""
        )
        tf_filter_text = (
            f" | mostrando TF {len(tf_rows)}/{tf_total_before_filter}"
            if tf_search_terms else ""
        )
        self.ubs_universe_summary.set(
            f"Universo: {len(assets)} activos | puntuados validos: {stats['total_scored']} | "
            f"semillas puntuadas: {stats['total_seed_scored']} | pendientes/neutros: {stats['total_pending'] + stats['total_seed_pending']} | "
            f"mismatch ignorados: {stats['total_mismatch'] + stats['total_seed_mismatch']} | robust +/{stats['total_robust_accepted']} -/{stats['total_robust_rejected']} | "
            f"fuera de universo ignorados: {stats['total_outside_universe'] + stats['total_seed_outside_universe']} | "
            f"deshabilitados: {len(disabled_symbols)} | seeds en deshab.: {len(seed_enabled_when_disabled)}{asset_filter_text}{tf_filter_text}"
        )
        self.ubs_timeframe_summary.set(
            "PESO REL = score probabilistico relativo end-to-end; P FINAL = probabilidad estimada hasta regresiva; "
            "pendientes/mismatch/history_probe no aportan; GEN=no bloquea generacion."
        )

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
        context = self._ubs_universe_context()
        (
            assets, aliases, universe_symbols, universe_symbols_tuple,
            symbol_suffix, futures_suffix, shares_suffix, suffix_universe,
            symbol_map, signal_aliases, disabled_symbols,
            seed_enabled_when_disabled, checked_symbols, memory_path,
        ) = context
        stats = self._collect_ubs_universe_stats(
            memory_path, aliases, universe_symbols, universe_symbols_tuple,
            symbol_map, signal_aliases, suffix_universe,
            symbol_suffix, futures_suffix, shares_suffix,
            disabled_symbols, seed_enabled_when_disabled,
        )
        if stats is None:
            # _read_ubs_universe_memory ya dejo el error en los dos resumenes.
            return
        ranked_assets, asset_total_before_filter, asset_search_terms = (
            self._rank_ubs_universe_assets(
                assets, stats["asset_stats"], stats["asset_signals"], stats["timeframe_stats"],
                disabled_symbols, seed_enabled_when_disabled, checked_symbols,
            )
        )
        tf_rows, tf_total_before_filter, tf_search_terms = (
            self._rank_ubs_universe_timeframes(
                stats["timeframe_stats"], stats["timeframe_signals"],
            )
        )
        self._set_ubs_universe_summaries(
            assets, stats, disabled_symbols, seed_enabled_when_disabled,
            ranked_assets, asset_total_before_filter, asset_search_terms,
            tf_rows, tf_total_before_filter, tf_search_terms,
        )

    def _disabled_symbols_path(self):
        from ubs.account import account_disabled_symbols_path
        return account_disabled_symbols_path(BASE_DIR, self._ubs_account_type(), self._ubs_broker())

    def _load_disabled_ubs_symbols(self) -> set:
        from ubs.universe import load_disabled_symbols
        return load_disabled_symbols(self._disabled_symbols_path())

    def _load_seed_enabled_disabled_ubs_symbols(self) -> set:
        from ubs.universe import load_seed_enabled_disabled_symbols
        return load_seed_enabled_disabled_symbols(self._disabled_symbols_path())

    def _save_disabled_ubs_symbols(self, symbols: set, seed_enabled_when_disabled: set | None = None):
        """Escribe la politica de deshabilitados dejando antes una copia.

        ``save_disabled_symbols`` sobreescribe el fichero, y estas acciones
        pueden cambiar miles de entradas de golpe. Devuelve la ruta del backup
        (o None si no habia fichero previo)."""
        from ubs.universe import save_disabled_symbols

        path = self._disabled_symbols_path()
        backup = None
        if path.exists():
            backup = path.with_suffix(path.suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
            try:
                shutil.copy2(path, backup)
            except OSError:
                backup = None
            else:
                self._prune_disabled_symbols_backups(path)
        save_disabled_symbols(path, symbols, seed_enabled_when_disabled)
        return backup

    def _prune_disabled_symbols_backups(self, path: Path, keep: int = 10) -> None:
        """Conserva solo los ``keep`` backups mas recientes de la politica."""
        backups = sorted(
            path.parent.glob(f"{path.name}.bak_*"),
            key=lambda item: item.name,
            reverse=True,
        )
        for stale in backups[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass

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

    def _weight_memory_path(self):
        return self._ubs_memory_path()

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
        conn = connect_memory(mem)
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
        conn = connect_memory(mem)
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
        periods = list(KNOWN_TIMEFRAMES)
        conn = connect_memory(mem)
        n = self._clear_weights_sql(conn, periods=periods)
        conn.close()
        self.ubs_timeframe_checked.clear()
        self.status_text.set(f"Todos los pesos de TF limpiados: {n} candidatos afectados")
        self._refresh_ubs_universe()
