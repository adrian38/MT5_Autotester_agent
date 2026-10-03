from __future__ import annotations

import sqlite3
import sys
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.tester_diagnostics import BROKER_BLOCKED_TRADE_MODES


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseActionsMixin:
    """Activar, desactivar y sondear el historico de los simbolos marcados."""

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
        _, aliases = self._load_ubs_asset_universe()
        symbols = self._canonical_ubs_symbol_set(self._selected_ubs_universe_symbols(), aliases)
        if not symbols:
            messagebox.showinfo("Universo UBS", "Marca uno o mas simbolos primero.")
            return
        disabled, seed_enabled = self._active_ubs_symbol_policy(aliases)
        if enabled:
            disabled.difference_update(symbols)
            action = "habilitados"
        else:
            disabled.update(symbols)
            action = "deshabilitados"
        seed_enabled.difference_update(symbols)
        self._save_disabled_ubs_symbols(disabled, seed_enabled)
        self.ubs_universe_checked.clear()
        self.status_text.set(f"Simbolos {action}: {len(symbols)}")
        self._refresh_ubs_universe()

    def _set_checked_universe_symbols_seed_enabled(self, enabled: bool) -> None:
        _, aliases = self._load_ubs_asset_universe()
        symbols = self._canonical_ubs_symbol_set(self._selected_ubs_universe_symbols(), aliases)
        if not symbols:
            messagebox.showinfo("Universo UBS", "Marca uno o mas simbolos primero.")
            return
        disabled, seed_enabled = self._active_ubs_symbol_policy(aliases)
        if enabled:
            eligible = {symbol for symbol in symbols if symbol in disabled}
            if not eligible:
                messagebox.showinfo("Universo UBS", "SEEDS solo aplica a simbolos con GEN=no.")
                return
            seed_enabled.update(eligible)
            action = "permitidas como seeds"
            changed_count = len(eligible)
        else:
            eligible = {symbol for symbol in symbols if symbol in disabled}
            if not eligible:
                messagebox.showinfo("Universo UBS", "Los simbolos con GEN=si ya permiten seeds por defecto.")
                return
            seed_enabled.difference_update(eligible)
            action = "bloqueadas como seeds"
            changed_count = len(eligible)
        self._save_disabled_ubs_symbols(disabled, seed_enabled)
        self.ubs_universe_checked.clear()
        self.status_text.set(f"Simbolos {action}: {changed_count}")
        self._refresh_ubs_universe()

    def _no_history_universe_symbols(self, aliases: dict[str, str]) -> set[str]:
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return set()
        conn = None
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select target_symbol, status
                from candidates
                where policy='history_probe'
                  and coalesce(target_symbol, '') != ''
                order by id
                """
            ).fetchall()
        except sqlite3.Error as exc:
            messagebox.showerror("Universo UBS", f"No se pudo leer memoria UBS:\n{exc}")
            return set()
        finally:
            if conn is not None:
                conn.close()
        latest: dict[str, str] = {}
        for row in rows:
            symbol = self._canonical_ubs_symbol(str(row["target_symbol"] or ""), aliases).upper()
            if symbol:
                latest[symbol] = str(row["status"] or "")
        return {symbol for symbol, status in latest.items() if status == "no_history"}

    def _disable_no_history_universe_symbols(self) -> None:
        _, aliases = self._load_ubs_asset_universe()
        symbols = self._no_history_universe_symbols(aliases)
        if not symbols:
            messagebox.showinfo("Universo UBS", "No hay simbolos clasificados como sin historico.")
            return
        disabled, seed_enabled = self._active_ubs_symbol_policy(aliases)
        new_symbols = symbols - disabled
        already_disabled = symbols & disabled
        if not new_symbols:
            messagebox.showinfo(
                "Deshabilitar sin historico",
                "No hay simbolos nuevos para deshabilitar.\n\n"
                f"Clasificados no_history por probe: {len(symbols)}\n"
                f"Ya deshabilitados: {len(already_disabled)}",
            )
            return
        detail = ", ".join(sorted(new_symbols)[:20])
        if len(new_symbols) > 20:
            detail += f", ... (+{len(new_symbols) - 20})"
        if not messagebox.askyesno(
            "Deshabilitar sin historico",
            "Se deshabilitaran en GEN solo los simbolos con veredicto no_history del probe historico.\n\n"
            f"Simbolos probe no_history: {len(symbols)}\n"
            f"Nuevos a deshabilitar: {len(new_symbols)}\n"
            f"Ya deshabilitados: {len(already_disabled)}\n\n"
            f"{detail}\n\n"
            "Revisa luego el universo si quieres volver a habilitar alguno.",
        ):
            return
        disabled.update(new_symbols)
        seed_enabled.difference_update(new_symbols)
        self._save_disabled_ubs_symbols(disabled, seed_enabled)
        self.ubs_universe_checked.clear()
        self.status_text.set(
            f"Simbolos sin historico deshabilitados: {len(new_symbols)} nuevos / {len(symbols)} probe no_history"
        )
        self._refresh_ubs_universe()

    def _disable_trade_disabled_universe_symbols(self) -> None:
        extraction = self._query_mt5_trade_modes_from_saved_session(
            "Deshabilitar trading bloqueado"
        )
        if extraction is None:
            return
        _, aliases = self._load_ubs_asset_universe()
        symbols = self._canonical_ubs_symbol_set(
            {
                symbol.name
                for symbol in extraction.symbols
                if symbol.trade_mode in BROKER_BLOCKED_TRADE_MODES
            },
            aliases,
        )
        if not symbols:
            messagebox.showinfo(
                "Deshabilitar trading bloqueado",
                "La consulta actual de MT5 no devolvio simbolos DISABLED o CLOSEONLY.",
            )
            return
        disabled, seed_enabled = self._active_ubs_symbol_policy(aliases)
        new_symbols = symbols - disabled
        already_disabled = symbols & disabled
        if not new_symbols:
            messagebox.showinfo(
                "Deshabilitar trading bloqueado",
                "No hay simbolos nuevos para deshabilitar.\n\n"
                f"Simbolos consultados en MT5: {len(extraction.symbols)}\n"
                f"DISABLED/CLOSEONLY actuales: {len(symbols)}\n"
                f"Ya deshabilitados: {len(already_disabled)}",
            )
            return
        detail = ", ".join(sorted(new_symbols)[:20])
        if len(new_symbols) > 20:
            detail += f", ... (+{len(new_symbols) - 20})"
        if not messagebox.askyesno(
            "Deshabilitar trading bloqueado",
            "Se deshabilitaran en GEN solo los simbolos que la consulta actual de MT5 devuelve "
            "como DISABLED o CLOSEONLY.\n\n"
            f"Simbolos consultados en MT5: {len(extraction.symbols)}\n"
            f"DISABLED/CLOSEONLY actuales: {len(symbols)}\n"
            f"Cuenta: {extraction.account_login or 'sesion guardada'}\n"
            f"Servidor: {extraction.server or 'sesion guardada'}\n"
            f"Nuevos a deshabilitar: {len(new_symbols)}\n"
            f"Ya deshabilitados: {len(already_disabled)}\n\n"
            f"{detail}\n\n"
            "Revisa luego el universo si quieres volver a habilitar alguno.",
        ):
            return
        disabled.update(new_symbols)
        seed_enabled.difference_update(new_symbols)
        self._save_disabled_ubs_symbols(disabled, seed_enabled)
        self.ubs_universe_checked.clear()
        self.status_text.set(
            f"Simbolos con trading bloqueado deshabilitados: {len(new_symbols)} nuevos / "
            f"{len(symbols)} DISABLED/CLOSEONLY en MT5"
        )
        self._refresh_ubs_universe()

    def _count_ubs_history_probe_symbols(self) -> int:
        assets, aliases = self._load_ubs_asset_universe()
        disabled, _seed_enabled = self._active_ubs_symbol_policy(aliases)
        active = [
            symbol
            for _group, symbol, _symbol_aliases in assets
            if self._canonical_ubs_symbol(symbol, aliases).upper() not in disabled
        ]
        memory_path = self._ubs_memory_path()
        if not memory_path.exists():
            return len(active)
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select target_symbol, status
                from candidates
                where policy='history_probe'
                order by id
                """
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return len(active)
        final_statuses = {"history_ok", "no_history"}
        latest = {
            self._canonical_ubs_symbol(str(row["target_symbol"] or ""), aliases).upper(): str(row["status"] or "")
            for row in rows
            if str(row["target_symbol"] or "").strip()
        }
        return sum(
            1
            for symbol in active
            if latest.get(self._canonical_ubs_symbol(symbol, aliases).upper()) not in final_statuses
        )

    def _ubs_history_probe_date_range(self) -> tuple[str, str]:
        start_text = self.ubs_agent_from_date.get().strip() or "2020.01.01"
        try:
            start = datetime.strptime(start_text, "%Y.%m.%d")
        except ValueError as exc:
            raise ValueError("La fecha Desde del Agente UBS debe estar en formato YYYY.MM.DD.") from exc
        try:
            end = start.replace(year=start.year + 1)
        except ValueError:
            end = start + timedelta(days=365)
        return start_text, end.strftime("%Y.%m.%d")

    def _ubs_history_probe_args(self) -> list[str]:
        source_dir = self._ubs_generator_source_dir()
        from_date, to_date = self._ubs_history_probe_date_range()
        args = [
            "--probe-universe-history",
            "--source-dir", str(source_dir),
            "--output-dir", str(self._ubs_generation_output_dir()),
            "--memory", str(self._ubs_memory_path()),
            "--broker", self._ubs_broker(),
            "--account-type", self._ubs_account_type(),
            "--template", self.template_path.get(),
            "--delay", str(self.delay.get()),
            "--probe-history-timeframe", "H1",
            "--execute-backtests",
            "--from-date", from_date,
            "--to-date", to_date,
        ]
        if self.multiterminal_enabled.get():
            args.extend(self._multiterminal_args(require_ubs=True))
        else:
            args.extend(["--expert", self._required_ubs_ex5_file()])
            if self.mt5_path.get().strip():
                args.extend(["--mt5-path", self.mt5_path.get()])
            if self.mt5_data_root.get().strip():
                args.extend(["--data-dir", self.mt5_data_root.get()])
        symbol_map = self._effective_ubs_symbol_map_text()
        if symbol_map:
            args.extend(["--symbol-map", symbol_map])
        args.extend(self._effective_symbol_suffix_args())
        return args

    def _run_ubs_universe_history_probe(self) -> None:
        try:
            total = self._count_ubs_history_probe_symbols()
            if total <= 0:
                messagebox.showinfo("Probe historico", "No hay simbolos GEN=si pendientes de probe historico.")
                return
            from_date, to_date = self._ubs_history_probe_date_range()
            args = self._ubs_history_probe_args()
        except Exception as exc:
            self._show_error("No se pudo iniciar probe historico", str(exc))
            return
        details = [
            "Accion: Probar historico del universo",
            f"Broker/cuenta: {self._ubs_broker()} / {self._ubs_account_type()}",
            f"Simbolos GEN=si pendientes: {total}",
            "TF probe: H1",
            f"Rango: {from_date} -> {to_date}",
            "Resultado: no_history si el log MT5 indica historico insuficiente; history_ok no pesa.",
        ]
        details.extend(self._multiterminal_execution_details())
        if self._confirm_execution_start("Confirmar probe historico", total, details):
            self._run_script("ubs_agent.py", args)
