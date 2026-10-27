from __future__ import annotations

import sys
from pathlib import Path
from tkinter import messagebox

from ubs.account import broker_asset_universe_path_with_fallback
from ubs.mt5_symbol_extract import (
    MT5SymbolExtractionError,
    extract_symbols_from_mt5,
    write_asset_universe_from_symbols,
)
from ubs.tester_diagnostics import save_trade_mode_snapshot


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseMT5Mixin:
    """Credenciales, extraccion y sincronizacion del universo desde MT5."""

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

    def _toggle_ubs_universe_account_fields(self) -> None:
        state = "normal" if self.ubs_universe_mt5_use_account.get() else "disabled"
        for entry in getattr(self, "_ubs_universe_account_entries", ()):
            entry.configure(state=state)

    def _save_ubs_universe_mt5_config(self) -> None:
        self._write_ui_settings()
        self.status_text.set("Configuracion MT5 de Universo guardada")

    def _mt5_symbol_extract_credentials(self) -> dict[str, object]:
        use_account = bool(self.ubs_universe_mt5_use_account.get())
        login_text = self.ubs_universe_mt5_login.get().strip()
        if use_account and not login_text:
            raise ValueError("Indica el login o desmarca 'Usar esta cuenta'.")
        try:
            login = int(login_text) if use_account else None
        except ValueError as exc:
            raise ValueError("Login debe ser numerico.") from exc
        return {
            "mt5_path": self.ubs_universe_mt5_path.get().strip(),
            "login": login,
            "server": self.ubs_universe_mt5_server.get().strip() if use_account else "",
            "password": self.ubs_universe_mt5_password.get() if use_account else "",
        }

    def _extract_live_mt5_symbols(self, title: str, confirm_message: str):
        """Consulta el inventario y ``trade_mode`` actuales del terminal MT5."""
        try:
            credentials = self._mt5_symbol_extract_credentials()
        except ValueError as exc:
            messagebox.showerror(title, str(exc))
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

    def _query_mt5_trade_modes_from_saved_session(self, title: str):
        """Consulta MT5 usando el perfil configurado y la sesion ya iniciada."""
        profile = self._default_mt5_symbol_extract_profile()
        raw_path = str(profile.get("mt5_path") or "").strip()
        terminal_path = Path(raw_path).expanduser() if raw_path else None
        if terminal_path is not None and not terminal_path.is_file():
            messagebox.showerror(title, f"No existe el terminal:\n{terminal_path}")
            return None
        self.status_text.set("Consultando trade_mode en la sesion MT5 activa...")
        self.update_idletasks()
        try:
            extraction = extract_symbols_from_mt5(terminal_path=terminal_path)
            if not extraction.symbols:
                messagebox.showerror(title, "MT5 devolvio un inventario vacio.")
                return None
            save_trade_mode_snapshot(
                self._ubs_trade_mode_snapshot_path(),
                extraction.symbols,
                account_login=extraction.account_login,
                server=extraction.server,
                terminal_path=extraction.terminal_path,
            )
            return extraction
        except MT5SymbolExtractionError as exc:
            messagebox.showerror(title, str(exc))
        except Exception as exc:
            messagebox.showerror(title, f"Error inesperado:\n{exc}")
        self.status_text.set("Consulta MT5 fallida")
        return None

    def _report_mt5_universe_sync(
        self, extraction, sync_result, removed, newly_disabled,
        dropped_seed_exceptions, policy_backup, title,
    ) -> None:
        """Resumen de la sincronizacion y refresco del universo."""
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

        self._report_mt5_universe_sync(
            extraction, sync_result, removed, newly_disabled,
            dropped_seed_exceptions, policy_backup, title,
        )

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
