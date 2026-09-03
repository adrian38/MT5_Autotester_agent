from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox

from run_tests import KNOWN_TIMEFRAMES, apply_symbol_map, apply_symbol_suffix, load_symbol_suffix_universe, parse_symbol_map
from ubs.db import connect_memory
from ubs.memory import AgentMemory
from ubs.account import (
    axi_cash_future_family_targets,
    broker_asset_universe_path_with_fallback,
    default_symbol_map_for_broker,
    load_account_timeframe_universe,
)
from ubs.mt5_symbol_extract import (
    MT5SymbolExtractionError,
    extract_symbols_from_mt5,
    write_asset_universe_from_symbols,
)
from ubs.tester_diagnostics import (
    BROKER_BLOCKED_TRADE_MODES,
    save_trade_mode_snapshot,
    trade_mode_snapshot_path,
)
from ubs.universe import asset_rows_from_groups, canonical_symbol, load_asset_universe
from ubs.weights import (
    ASSET_ACCEPTED_BONUS,
    SEED_WEIGHT_SCALE,
    TIMEFRAME_ACCEPTED_BONUS,
    candidate_group_key,
    feedback_weight,
    grouped_shrunk_mean,
    seed_group_key,
)


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseLogicMixin:
    def _ubs_trade_mode_snapshot_path(self) -> Path:
        return trade_mode_snapshot_path(BASE_DIR, self._ubs_broker(), self._ubs_account_type())

    def _refresh_ubs_universe_panel(self) -> None:
        for label, callback in (
            ("ubs_seed_summary", self._refresh_ubs_seed_eval_summary),
            ("ubs_universe", self._refresh_ubs_universe),
        ):
            self._safe_refresh(label, callback)

    def _load_ubs_asset_universe(self) -> tuple[list[tuple[str, str, list[str]]], dict[str, str]]:
        path = broker_asset_universe_path_with_fallback(BASE_DIR, self._ubs_broker())
        groups, aliases = load_asset_universe(path, include_disabled=True)
        return asset_rows_from_groups(groups, aliases), aliases

    def _canonical_ubs_symbol(
        self,
        symbol: str,
        aliases: dict[str, str],
        *,
        symbol_map: dict[str, str] | None = None,
        suffix_universe: dict[str, str] | None = None,
        symbol_suffix: str = "",
        futures_suffix: str = "",
        shares_suffix: str = "",
    ) -> str:
        mapped = apply_symbol_map(str(symbol or ""), symbol_map or {})
        suffixed = apply_symbol_suffix(
            mapped,
            symbol_suffix,
            futures_suffix,
            shares_suffix,
            suffix_universe,
        )
        return canonical_symbol(suffixed, aliases)

    def _ubs_seed_row_canonical_symbols(
        self,
        row: object,
        universe_symbols: tuple[str, ...],
        aliases: dict[str, str],
        symbol_map: dict[str, str],
        suffix_universe: dict[str, str],
        symbol_suffix: str,
        futures_suffix: str,
        shares_suffix: str,
    ) -> tuple[str, ...]:
        try:
            raw_symbol = str(row["symbol"] or "")  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            raw_symbol = ""
        metric_symbol = self._ubs_row_metric_symbol(row)
        primary = metric_symbol or raw_symbol
        targets = []
        if primary:
            targets.append(
                self._canonical_ubs_symbol(
                    primary,
                    aliases,
                    symbol_map=symbol_map,
                    suffix_universe=suffix_universe,
                    symbol_suffix=symbol_suffix,
                    futures_suffix=futures_suffix,
                    shares_suffix=shares_suffix,
                )
            )
        for source in (metric_symbol, raw_symbol, apply_symbol_map(raw_symbol, symbol_map)):
            for target in axi_cash_future_family_targets(source, universe_symbols):
                targets.append(
                    self._canonical_ubs_symbol(
                        target,
                        aliases,
                        symbol_map=symbol_map,
                        suffix_universe=suffix_universe,
                        symbol_suffix=symbol_suffix,
                        futures_suffix=futures_suffix,
                        shares_suffix=shares_suffix,
                    )
                )
        return tuple(dict.fromkeys(target for target in targets if target))

    def _ubs_universe_suffix_config(self) -> tuple[str, str, str]:
        enabled = getattr(self, "symbol_suffix_enabled", None)
        if enabled is not None and not bool(enabled.get()):
            return "", "", ""
        suffix_var = getattr(self, "symbol_suffix", None)
        futures_var = getattr(self, "symbol_futures_suffix", None)
        shares_var = getattr(self, "symbol_shares_suffix", None)
        suffix = suffix_var.get().strip() if suffix_var is not None else ""
        futures_suffix = futures_var.get().strip() if futures_var is not None else ""
        shares_suffix = shares_var.get().strip() if shares_var is not None else ""
        return suffix, futures_suffix, shares_suffix

    def _ubs_universe_symbol_map(self) -> dict[str, str]:
        parts = [default_symbol_map_for_broker(self._ubs_broker())]
        enabled = getattr(self, "symbol_map_enabled", None)
        custom = getattr(self, "symbol_map", None)
        if enabled is not None and bool(enabled.get()) and custom is not None and custom.get().strip():
            parts.append(custom.get().strip())
        try:
            return parse_symbol_map(",".join(part for part in parts if part.strip()))
        except ValueError:
            return parse_symbol_map(parts[0])

    def _ubs_universe_signal_aliases(
        self,
        aliases: dict[str, str],
        symbol_map: dict[str, str],
        suffix_universe: dict[str, str],
        symbol_suffix: str,
        futures_suffix: str,
        shares_suffix: str,
    ) -> dict[str, str]:
        signal_aliases = {str(key).upper(): str(value).upper() for key, value in aliases.items()}
        sources = set(symbol_map) | set(suffix_universe)
        for source in sources:
            canonical = self._canonical_ubs_symbol(
                source,
                aliases,
                symbol_map=symbol_map,
                suffix_universe=suffix_universe,
                symbol_suffix=symbol_suffix,
                futures_suffix=futures_suffix,
                shares_suffix=shares_suffix,
            )
            if canonical:
                signal_aliases[str(source).upper()] = canonical.upper()
        return signal_aliases

    def _ubs_row_metric_symbol(self, row: object) -> str:
        try:
            raw = row["metrics_json"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return ""
        if not raw:
            return ""
        try:
            metrics = json.loads(str(raw))
        except json.JSONDecodeError:
            return ""
        if not isinstance(metrics, dict):
            return ""
        return str(metrics.get("symbol") or "").strip()

    def _canonical_ubs_symbol_set(self, symbols: set[str], aliases: dict[str, str]) -> set[str]:
        return {
            self._canonical_ubs_symbol(symbol, aliases).upper()
            for symbol in symbols
            if str(symbol or "").strip()
        }

    def _selected_ubs_universe_symbols(self) -> set[str]:
        symbols = set(self.ubs_universe_checked)
        if not symbols and hasattr(self, "ubs_universe_assets_tree"):
            selected = self.ubs_universe_assets_tree.selection()
            symbols = {
                self.ubs_universe_paths.get(item, {}).get("symbol", "")
                for item in selected
            }
            symbols.discard("")
        return symbols

    def _active_ubs_symbol_policy(self, aliases: dict[str, str]) -> tuple[set[str], set[str]]:
        disabled = self._canonical_ubs_symbol_set(self._load_disabled_ubs_symbols(), aliases)
        seed_enabled = self._canonical_ubs_symbol_set(self._load_seed_enabled_disabled_ubs_symbols(), aliases)
        return disabled, seed_enabled & disabled

    def _empty_ubs_stat(self) -> dict[str, object]:
        return {
            "scores": [],
            "weights": [],
            "weight_groups": {},
            "tests": 0,
            "accepted": 0,
            "pending": 0,
            "best": None,
        }

    def _tag_for_weight(self, value: float | None) -> str:
        if value is None:
            return "neutral"
        return "positive" if value >= 0 else "negative"

    def _ubs_universe_search_terms(self, var_name: str) -> list[str]:
        variable = getattr(self, var_name, None)
        if variable is None:
            return []
        return [term for term in variable.get().strip().lower().split() if term]

    def _ubs_universe_asset_matches_search(self, group: str, symbol: str, aliases: list[str], terms: list[str]) -> bool:
        if not terms:
            return True
        haystack = " ".join([group, symbol, *aliases]).lower()
        return all(term in haystack for term in terms)

    def _ubs_universe_tf_matches_search(self, period: str, terms: list[str]) -> bool:
        if not terms:
            return True
        haystack = period.lower()
        return all(term in haystack for term in terms)

    def _clear_ubs_universe_search(self) -> None:
        changed = False
        for variable in (getattr(self, "ubs_universe_asset_search", None), getattr(self, "ubs_universe_tf_search", None)):
            if variable is not None and variable.get():
                variable.set("")
                changed = True
        if not changed:
            self._refresh_ubs_universe()

    def _default_mt5_symbol_extract_profile(self) -> dict[str, str]:
        profile: dict[str, str] = {
            "mt5_path": self.mt5_path.get().strip() if hasattr(self, "mt5_path") else "",
            "name": "MT5 principal",
        }
        try:
            if hasattr(self, "_save_current_multiterminal_editor"):
                self._save_current_multiterminal_editor()
            profiles = self._broker_multiterminal_profiles(include_disabled=False)
        except Exception:
            profiles = []
        if profiles:
            selected = profiles[0]
            return {
                "mt5_path": str(selected.get("mt5_path") or profile["mt5_path"]).strip(),
                "name": str(selected.get("name") or profile["name"]).strip(),
            }
        return profile

    def _ask_mt5_symbol_extract_credentials(self) -> dict[str, object] | None:
        profile = self._default_mt5_symbol_extract_profile()
        dialog = tk.Toplevel(self)
        dialog.title("Extraer simbolos MT5")
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(bg=self.colors["panel"])
        dialog.columnconfigure(1, weight=1)

        mt5_path_var = tk.StringVar(value=profile["mt5_path"])
        login_var = tk.StringVar(value="")
        server_var = tk.StringVar(value="")
        password_var = tk.StringVar(value="")
        result: dict[str, object] | None = None

        fields = (
            ("Terminal", mt5_path_var, False),
            ("Login", login_var, False),
            ("Servidor", server_var, False),
            ("Password", password_var, True),
        )
        for row, (label, variable, secret) in enumerate(fields):
            tk.Label(
                dialog,
                text=label,
                bg=self.colors["panel"],
                fg=self.colors["text"],
                font=("Segoe UI", 10),
            ).grid(row=row, column=0, sticky="w", padx=(16, 8), pady=7)
            entry = tk.Entry(
                dialog,
                textvariable=variable,
                show="*" if secret else "",
                bg=self.colors["panel_alt"],
                fg=self.colors["text"],
                insertbackground=self.colors["text"],
                relief="solid",
                borderwidth=1,
                width=62,
            )
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 16), pady=7)

        hint = (
            f"Perfil: {profile['name']}. Login/servidor/password son opcionales si el terminal ya esta conectado."
        )
        tk.Label(
            dialog,
            text=hint,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            wraplength=560,
        ).grid(row=len(fields), column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 12))

        button_bar = tk.Frame(dialog, bg=self.colors["panel_alt"])
        button_bar.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 16))
        button_bar.columnconfigure(0, weight=1)

        def accept() -> None:
            nonlocal result
            login_text = login_var.get().strip()
            if login_text:
                try:
                    login_value = int(login_text)
                except ValueError:
                    messagebox.showerror("Extraer simbolos MT5", "Login debe ser numerico.", parent=dialog)
                    return
            else:
                login_value = None
            result = {
                "mt5_path": mt5_path_var.get().strip(),
                "login": login_value,
                "server": server_var.get().strip(),
                "password": password_var.get(),
            }
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        tk.Button(
            button_bar,
            text="Cancelar",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=cancel,
        ).grid(row=0, column=1, sticky="e", padx=(0, 6), pady=5)
        tk.Button(
            button_bar,
            text="Extraer",
            bg=self.colors["accent"],
            fg="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=5,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=accept,
        ).grid(row=0, column=2, sticky="e", padx=(0, 10), pady=5)

        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return result

    def _extract_live_mt5_symbols(self, title: str, confirm_message: str):
        """Consulta el inventario y ``trade_mode`` actuales del terminal MT5."""
        credentials = self._ask_mt5_symbol_extract_credentials()
        if credentials is None:
            return None
        raw_path = str(credentials.get("mt5_path") or "").strip()
        terminal_path = Path(raw_path).expanduser() if raw_path else None
        if terminal_path is not None and not terminal_path.exists():
            messagebox.showerror(title, f"No existe el terminal:\n{terminal_path}")
            return None
        if not messagebox.askyesno(title, confirm_message):
            return None
        self.status_text.set("Consultando simbolos y trade_mode en MT5...")
        self.update_idletasks()
        try:
            extraction = extract_symbols_from_mt5(
                terminal_path=terminal_path,
                login=credentials.get("login"),
                password=str(credentials.get("password") or ""),
                server=str(credentials.get("server") or ""),
            )
            if not extraction.symbols:
                messagebox.showerror(title, "MT5 devolvio un inventario vacio.")
                self.status_text.set("Consulta MT5 fallida")
                return None
            save_trade_mode_snapshot(
                self._ubs_trade_mode_snapshot_path(),
                extraction.symbols,
                account_login=extraction.account_login,
                server=extraction.server,
                terminal_path=extraction.terminal_path,
            )
        except MT5SymbolExtractionError as exc:
            messagebox.showerror(title, str(exc))
            self.status_text.set("Consulta MT5 fallida")
            return None
        except Exception as exc:
            messagebox.showerror(title, f"Error inesperado:\n{exc}")
            self.status_text.set("Consulta MT5 fallida")
            return None
        return extraction

    def _extract_mt5_universe_into_asset_file(self, title: str, confirm_message: str):
        """Lee los simbolos del servidor y reescribe el universo del broker activo.

        Nucleo compartido por "Extraer MT5" y "Sincronizacion de simbolos": el
        segundo solo añade la parte de politica, para no duplicar el dialogo de
        credenciales ni la escritura del assets.ini. Devuelve
        ``(extraction, sync_result)`` o None si el usuario cancela o algo falla
        (en ese caso ya se ha informado)."""
        extraction = self._extract_live_mt5_symbols(title, confirm_message)
        if extraction is None:
            return None
        try:
            universe_path = broker_asset_universe_path_with_fallback(BASE_DIR, self._ubs_broker())
            sync_result = write_asset_universe_from_symbols(
                universe_path,
                extraction.symbols,
                preserve_existing_groups=False,
            )
        except Exception as exc:
            messagebox.showerror(title, f"No se pudo escribir el universo:\n{exc}")
            self.status_text.set("Sincronizacion MT5 fallida")
            return None
        return extraction, sync_result

    def _sync_mt5_universe_symbols(self) -> None:
        """Extrae del servidor y deja el universo listo para el probe historico.

        Es la parte previa al probe del proceso completo: sincronizar el
        inventario y deshabilitar en GEN lo que el broker retiro. Los retirados
        se deshabilitan porque ya no pueden generar seeds; deshabilitarlos NO los
        marca como terminales para los candidatos existentes (eso lo decide su
        ausencia del universo)."""
        title = "Sincronizacion de simbolos"
        result = self._extract_mt5_universe_into_asset_file(
            title,
            "Paso 1 del proceso de sincronizacion (previo al probe historico).\n\n"
            "1) Lee la lista de simbolos del servidor MT5.\n"
            "2) Reescribe el universo del broker activo: agrega los nuevos y "
            "elimina los que el broker ya no ofrece (con backup).\n"
            "3) Deshabilita en GEN los eliminados, para que no generen seeds.\n\n"
            "No lanza backtests. Despues usa 'Probar history GEN'.",
        )
        if result is None:
            return
        extraction, sync_result = result

        removed = tuple(sync_result.removed_symbols)
        newly_disabled: set[str] = set()
        dropped_seed_exceptions: set[str] = set()
        policy_backup = None
        if removed:
            _assets, aliases = self._load_ubs_asset_universe()
            disabled, seed_enabled = self._active_ubs_symbol_policy(aliases)
            retired = self._canonical_ubs_symbol_set(set(removed), aliases)
            newly_disabled = retired - disabled
            # La excepcion seed_enabled se retira de TODOS los retirados, no solo
            # de los recien deshabilitados: si el broker ya no ofrece el simbolo,
            # sus seeds no pueden ejecutarse aunque la excepcion sea antigua.
            dropped_seed_exceptions = seed_enabled & retired
            if newly_disabled or dropped_seed_exceptions:
                disabled.update(newly_disabled)
                seed_enabled.difference_update(retired)
                policy_backup = self._save_disabled_ubs_symbols(disabled, seed_enabled)

        total = sum(sync_result.counts.values())
        removed_preview = ", ".join(removed[:12])
        if len(removed) > 12:
            removed_preview += f", ... (+{len(removed) - 12})"
        universe_backup = f"\nBackup universo: {sync_result.backup_path}" if sync_result.backup_path else ""
        policy_backup_text = f"\nBackup politica: {policy_backup}" if policy_backup else ""
        messagebox.showinfo(
            title,
            f"Universo sincronizado: {total} simbolos\n"
            f"Agregados: {len(sync_result.added_symbols)}\n"
            f"Retirados por el broker: {len(removed)}"
            + (f" ({removed_preview})" if removed_preview else "")
            + f"\nDeshabilitados en GEN ahora: {len(newly_disabled)}\n"
            + (
                f"Excepciones de seeds retiradas: {len(dropped_seed_exceptions)}\n"
                if dropped_seed_exceptions
                else ""
            )
            + f"Cuenta: {extraction.account_login or '(sesion actual)'}\n"
            f"Servidor: {extraction.server or '(sin dato)'}"
            f"{universe_backup}{policy_backup_text}\n\n"
            "Siguiente paso: 'Probar history GEN' y, cuando termine, "
            "'Deshabilitar simbolos sin history'.",
        )
        self.ubs_universe_checked.clear()
        self.status_text.set(
            f"Sincronizacion de simbolos: {total} en universo, "
            f"+{len(sync_result.added_symbols)} / -{len(removed)}, "
            f"{len(newly_disabled)} deshabilitados en GEN"
        )
        self._refresh_ubs_universe()

    def _extract_mt5_universe_symbols(self) -> None:
        result = self._extract_mt5_universe_into_asset_file(
            "Extraer simbolos MT5",
            "Se leera la lista de simbolos del servidor MT5 y se sincronizara el universo del broker activo.\n\n"
            "Se eliminaran los simbolos que ya no existan, se agregaran los nuevos y se creara un backup antes de escribir.\n\n"
            "No toca la politica de deshabilitados: para eso usa 'Sincronizacion de simbolos'.",
        )
        if result is None:
            return
        extraction, sync_result = result

        total = sum(sync_result.counts.values())
        details = ", ".join(f"{group}: {count}" for group, count in sync_result.counts.items())
        backup_text = f"\nBackup: {sync_result.backup_path}" if sync_result.backup_path else ""
        added_preview = ", ".join(sync_result.added_symbols[:12])
        removed_preview = ", ".join(sync_result.removed_symbols[:12])
        if len(sync_result.added_symbols) > 12:
            added_preview += f", ... (+{len(sync_result.added_symbols) - 12})"
        if len(sync_result.removed_symbols) > 12:
            removed_preview += f", ... (+{len(sync_result.removed_symbols) - 12})"
        messagebox.showinfo(
            "Extraer simbolos MT5",
            f"Simbolos en universo: {total}\n"
            f"Agregados: {len(sync_result.added_symbols)}"
            + (f" ({added_preview})" if added_preview else "")
            + "\n"
            f"Eliminados: {len(sync_result.removed_symbols)}"
            + (f" ({removed_preview})" if removed_preview else "")
            + "\n"
            f"Cuenta: {extraction.account_login or '(sesion actual)'}\n"
            f"Servidor: {extraction.server or '(sin dato)'}\n"
            f"{details}{backup_text}",
        )
        self.ubs_universe_checked.clear()
        self.status_text.set(
            f"Universo MT5 sincronizado: {total} simbolos, "
            f"+{len(sync_result.added_symbols)} / -{len(sync_result.removed_symbols)}"
        )
        self._refresh_ubs_universe()

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
        extraction = self._extract_live_mt5_symbols(
            "Deshabilitar trading bloqueado",
            "Se consultara directamente en el terminal MT5 el trade_mode actual de todos los simbolos.\n\n"
            "Esta consulta no usa journals ni deshabilita nada todavia. Despues podras revisar y confirmar.",
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
        asset_stats: dict[str, dict[str, object]] = {}
        timeframe_stats: dict[str, dict[str, object]] = {}
        total_scored = 0
        total_pending = 0
        total_mismatch = 0
        total_seed_scored = 0
        total_seed_pending = 0
        total_seed_mismatch = 0
        total_outside_universe = 0
        total_seed_outside_universe = 0
        total_robust_accepted = 0
        total_robust_rejected = 0
        asset_signals = {}
        timeframe_signals = {}

        if memory_path.exists():
            try:
                conn = connect_memory(memory_path)
                conn.row_factory = sqlite3.Row
                if hasattr(self, "_ensure_ubs_memory_schema"):
                    self._ensure_ubs_memory_schema(conn)
                rows = conn.execute(
                    """
                    select
                        c.run_id, c.seed_path, c.target_symbol, c.symbol, c.period, c.family,
                        c.policy, c.score, c.accepted, c.metrics_json, c.status, c.report_path,
                        cr.status as robust_status,
                        cr.positive_bonus as robust_positive_bonus,
                        cr.negative_bonus as robust_negative_bonus,
                        cr.metrics_json as robust_metrics_json,
                        ft.status as final_tick_status,
                        ft.similarity_json as final_tick_similarity_json,
                        ft6.status as final_tick_6m_status,
                        ft6.similarity_json as final_tick_6m_similarity_json,
                        rg.status as regression_status,
                        rg.points_applied as regression_points_applied
                    from candidates c
                    left join candidate_robustness cr on cr.candidate_id = c.id
                    left join candidate_final_tick ft on ft.candidate_id = c.id
                    left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
                    left join candidate_regression rg on rg.candidate_id = c.id
                    """
                ).fetchall()
                seed_table = conn.execute(
                    "select name from sqlite_master where type='table' and name='seed_scores'"
                ).fetchone()
                seed_rows = []
                if seed_table:
                    seed_rows = conn.execute(
                        """
                        select seed_path, symbol, period, score, accepted, metrics_json, status, active, report_path
                        from seed_scores
                        where active=1
                        """
                    ).fetchall()
                conn.close()
                memory = AgentMemory(memory_path)
                try:
                    asset_signals = memory.asset_feedback_signals(
                        signal_aliases,
                        allowed_symbols=universe_symbols,
                    )
                    timeframe_signals = memory.timeframe_feedback_signals()
                finally:
                    memory.close()
            except sqlite3.Error as exc:
                self.ubs_universe_summary.set(f"No se pudo leer memoria UBS: {exc}")
                self.ubs_timeframe_summary.set("Sin pesos por error SQLite")
                return

            for row in rows:
                status = str(row["status"] or "")
                if str(row["policy"] or "") == "history_probe":
                    continue
                if status == "report_mismatch":
                    total_mismatch += 1
                    continue
                canonical = self._canonical_ubs_symbol(
                    self._ubs_row_metric_symbol(row) or row["target_symbol"] or row["symbol"],
                    aliases,
                    symbol_map=symbol_map,
                    suffix_universe=suffix_universe,
                    symbol_suffix=symbol_suffix,
                    futures_suffix=futures_suffix,
                    shares_suffix=shares_suffix,
                )
                # Historical rows stay in SQLite for audit, but only the live
                # broker universe may create selectable rows or asset weights.
                if canonical.upper() not in universe_symbols:
                    total_outside_universe += 1
                    continue
                if canonical.upper() in disabled_symbols:
                    continue
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status == "trade_disabled":
                    continue
                if status not in {"accepted", "rejected", "no_trades"}:
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_pending += 1
                    continue
                score = float(row["score"] or 0.0)
                accepted = bool(row["accepted"])
                robust_status = str(row["robust_status"] or "")
                if robust_status == "accepted":
                    total_robust_accepted += 1
                elif robust_status == "rejected":
                    total_robust_rejected += 1
                asset_weight = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
                tf_weight = feedback_weight(row, accepted_bonus=TIMEFRAME_ACCEPTED_BONUS)
                if asset_weight is None and tf_weight is None:
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_pending += 1
                    continue
                for stat, weight in ((asset_stat, asset_weight), (tf_stat, tf_weight)):
                    if weight is None:
                        continue
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    groups = stat["weight_groups"]
                    if isinstance(groups, dict):
                        groups.setdefault(candidate_group_key(row), []).append(weight)
                    stat["tests"] = int(stat["tests"]) + 1
                    stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
                    stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
                total_scored += 1

            for row in seed_rows:
                status = str(row["status"] or "")
                if status == "report_mismatch":
                    total_seed_mismatch += 1
                    continue
                canonicals = self._ubs_seed_row_canonical_symbols(
                    row,
                    universe_symbols_tuple,
                    aliases,
                    symbol_map,
                    suffix_universe,
                    symbol_suffix,
                    futures_suffix,
                    shares_suffix,
                )
                current_canonicals = tuple(
                    canonical
                    for canonical in canonicals
                    if canonical.upper() in universe_symbols
                )
                if not current_canonicals:
                    total_seed_outside_universe += 1
                    continue
                eligible_canonicals = tuple(
                    canonical
                    for canonical in current_canonicals
                    if (
                        canonical.upper() not in disabled_symbols
                        or canonical.upper() in seed_enabled_when_disabled
                    )
                )
                if not eligible_canonicals:
                    continue
                period = str(row["period"] or "UNKNOWN").upper()
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status == "trade_disabled":
                    continue
                if status not in {"accepted", "rejected", "no_trades"}:
                    for canonical in eligible_canonicals:
                        asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                        asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_seed_pending += 1
                    continue
                score = float(row["score"] or 0.0)
                accepted = bool(row["accepted"])
                asset_weight = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
                tf_weight = feedback_weight(row, accepted_bonus=TIMEFRAME_ACCEPTED_BONUS)
                if asset_weight is None and tf_weight is None:
                    asset_stat["pending"] = int(asset_stat["pending"]) + 1
                    tf_stat["pending"] = int(tf_stat["pending"]) + 1
                    total_seed_pending += 1
                    continue
                if asset_weight is not None:
                    asset_weight *= SEED_WEIGHT_SCALE
                if tf_weight is not None:
                    tf_weight *= SEED_WEIGHT_SCALE
                if asset_weight is not None:
                    for canonical in eligible_canonicals:
                        asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                        asset_stat["scores"].append(score)
                        asset_stat["weights"].append(asset_weight)
                        groups = asset_stat["weight_groups"]
                        if isinstance(groups, dict):
                            groups.setdefault(seed_group_key(row), []).append(asset_weight)
                        asset_stat["tests"] = int(asset_stat["tests"]) + 1
                        asset_stat["accepted"] = int(asset_stat["accepted"]) + (1 if accepted else 0)
                        asset_stat["best"] = score if asset_stat["best"] is None else max(float(asset_stat["best"]), score)
                if tf_weight is not None:
                    stat = tf_stat
                    weight = tf_weight
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    groups = stat["weight_groups"]
                    if isinstance(groups, dict):
                        groups.setdefault(seed_group_key(row), []).append(weight)
                    stat["tests"] = int(stat["tests"]) + 1
                    stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
                    stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
                total_seed_scored += 1

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

        valid_symbols = {info["symbol"] for info in self.ubs_universe_paths.values() if info.get("symbol")}
        self.ubs_universe_checked.intersection_update(valid_symbols)

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

        asset_filter_text = (
            f" | mostrando activos {len(ranked_assets)}/{asset_total_before_filter}"
            if asset_search_terms else ""
        )
        tf_filter_text = (
            f" | mostrando TF {len(tf_rows)}/{tf_total_before_filter}"
            if tf_search_terms else ""
        )
        self.ubs_universe_summary.set(
            f"Universo: {len(assets)} activos | puntuados validos: {total_scored} | "
            f"semillas puntuadas: {total_seed_scored} | pendientes/neutros: {total_pending + total_seed_pending} | "
            f"mismatch ignorados: {total_mismatch + total_seed_mismatch} | robust +/{total_robust_accepted} -/{total_robust_rejected} | "
            f"fuera de universo ignorados: {total_outside_universe + total_seed_outside_universe} | "
            f"deshabilitados: {len(disabled_symbols)} | seeds en deshab.: {len(seed_enabled_when_disabled)}{asset_filter_text}{tf_filter_text}"
        )
        self.ubs_timeframe_summary.set(
            "PESO REL = score probabilistico relativo end-to-end; P FINAL = probabilidad estimada hasta regresiva; "
            "pendientes/mismatch/history_probe no aportan; GEN=no bloquea generacion."
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
