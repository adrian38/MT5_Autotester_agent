from __future__ import annotations

import sqlite3
import sys
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox

from ubs.db import connect_memory
from ubs.memory import AgentMemory
from ubs.account import broker_asset_universe_path_with_fallback, load_account_timeframe_universe
from ubs.mt5_symbol_extract import (
    MT5SymbolExtractionError,
    extract_symbols_from_mt5,
    write_asset_universe_from_symbols,
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

    def _canonical_ubs_symbol(self, symbol: str, aliases: dict[str, str]) -> str:
        return canonical_symbol(symbol, aliases)

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

    def _extract_mt5_universe_symbols(self) -> None:
        credentials = self._ask_mt5_symbol_extract_credentials()
        if credentials is None:
            return
        raw_path = str(credentials.get("mt5_path") or "").strip()
        terminal_path = Path(raw_path).expanduser() if raw_path else None
        if terminal_path is not None and not terminal_path.exists():
            messagebox.showerror("Extraer simbolos MT5", f"No existe el terminal:\n{terminal_path}")
            return
        if not messagebox.askyesno(
            "Extraer simbolos MT5",
            "Se leera la lista de simbolos del servidor MT5 y se sincronizara el universo del broker activo.\n\n"
            "Se eliminaran los simbolos que ya no existan, se agregaran los nuevos y se creara un backup antes de escribir.",
        ):
            return
        self.status_text.set("Extrayendo simbolos MT5...")
        self.update_idletasks()
        try:
            extraction = extract_symbols_from_mt5(
                terminal_path=terminal_path,
                login=credentials.get("login"),
                password=str(credentials.get("password") or ""),
                server=str(credentials.get("server") or ""),
            )
            universe_path = broker_asset_universe_path_with_fallback(BASE_DIR, self._ubs_broker())
            sync_result = write_asset_universe_from_symbols(universe_path, extraction.symbols)
        except MT5SymbolExtractionError as exc:
            messagebox.showerror("Extraer simbolos MT5", str(exc))
            self.status_text.set("Extraccion MT5 fallida")
            return
        except Exception as exc:
            messagebox.showerror("Extraer simbolos MT5", f"Error inesperado:\n{exc}")
            self.status_text.set("Extraccion MT5 fallida")
            return

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
                        ft6.similarity_json as final_tick_6m_similarity_json
                    from candidates c
                    left join candidate_robustness cr on cr.candidate_id = c.id
                    left join candidate_final_tick ft on ft.candidate_id = c.id
                    left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
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
                    asset_signals = memory.asset_feedback_signals(aliases)
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
                canonical = self._canonical_ubs_symbol(row["target_symbol"] or row["symbol"], aliases)
                if canonical.upper() in disabled_symbols:
                    continue
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
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
                canonical = self._canonical_ubs_symbol(row["symbol"], aliases)
                is_disabled = canonical.upper() in disabled_symbols
                if is_disabled and canonical.upper() not in seed_enabled_when_disabled:
                    continue
                period = str(row["period"] or "UNKNOWN").upper()
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
                if status not in {"accepted", "rejected", "no_trades"}:
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
                for stat, weight in ((asset_stat, asset_weight), (tf_stat, tf_weight)):
                    if weight is None:
                        continue
                    stat["scores"].append(score)
                    stat["weights"].append(weight)
                    groups = stat["weight_groups"]
                    if isinstance(groups, dict):
                        groups.setdefault(seed_group_key(row), []).append(weight)
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
            scores = stat["scores"]
            signal = asset_signals.get(symbol.upper())
            weight_value = signal.score if signal is not None else None
            probability = signal.probability if signal is not None else None
            confidence = signal.confidence if signal is not None else None
            final_trials = signal.final_trials if signal is not None else 0
            avg_score = (sum(scores) / len(scores)) if scores else None
            ranked_assets.append((weight_value if weight_value is not None else -999999.0, group, symbol, symbol_aliases, stat, weight_value, probability, confidence, final_trials, avg_score))
        ranked_assets.sort(key=lambda item: (item[0], item[4]["pending"]), reverse=True)
        asset_total_before_filter = len(ranked_assets)
        asset_search_terms = self._ubs_universe_search_terms("ubs_universe_asset_search")
        if asset_search_terms:
            ranked_assets = [
                row for row in ranked_assets
                if self._ubs_universe_asset_matches_search(row[1], row[2], row[3], asset_search_terms)
            ]

        if hasattr(self, "ubs_universe_assets_tree"):
            for _, group, symbol, symbol_aliases, stat, weight_value, probability, confidence, final_trials, avg_score in ranked_assets:
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
            avg_score = (sum(scores) / len(scores)) if scores else None
            tf_rows.append((weight_value if weight_value is not None else -999999.0, period, stat, weight_value, probability, confidence, final_trials, avg_score))
        tf_rows.sort(key=lambda item: item[0], reverse=True)
        tf_total_before_filter = len(tf_rows)
        tf_search_terms = self._ubs_universe_search_terms("ubs_universe_tf_search")
        if tf_search_terms:
            tf_rows = [row for row in tf_rows if self._ubs_universe_tf_matches_search(row[1], tf_search_terms)]

        if hasattr(self, "ubs_timeframes_tree"):
            valid_tfs: set[str] = set()
            for _, period, stat, weight_value, probability, confidence, final_trials, avg_score in tf_rows:
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
            f"deshabilitados: {len(disabled_symbols)} | seeds en deshab.: {len(seed_enabled_when_disabled)}{asset_filter_text}{tf_filter_text}"
        )
        self.ubs_timeframe_summary.set(
            "PESO REL = score probabilistico relativo end-to-end; P 6M = probabilidad estimada hasta Final Tick 6M; "
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

    def _save_disabled_ubs_symbols(self, symbols: set, seed_enabled_when_disabled: set | None = None) -> None:
        from ubs.universe import save_disabled_symbols
        save_disabled_symbols(self._disabled_symbols_path(), symbols, seed_enabled_when_disabled)

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
        periods = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"]
        conn = connect_memory(mem)
        n = self._clear_weights_sql(conn, periods=periods)
        conn.close()
        self.ubs_timeframe_checked.clear()
        self.status_text.set(f"Todos los pesos de TF limpiados: {n} candidatos afectados")
        self._refresh_ubs_universe()
